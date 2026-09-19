"""The bridge: MIDI ↔ OSC, plus a state file for what MIDI cannot carry.

DESIGN RULE, not to be broken: **the bridge is never required to play.** No note,
no sound, no keyboard zone goes through it. It carries only Live control and
display. If it dies mid-show the rig keeps playing and only the feedback freezes.
That is the difference between one more link and one more point of failure.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import rtmidi

from . import osc
from .mapping import Mapping
from .verbs import GESTURES

# Default MIDI port: the one the controller ALREADY uses.
#
# No virtual port is created: the controller already emits to an existing port
# that its MIDI host provides permanently. One more port would be one more thing
# to configure on every key, and one more to lose when the host goes down. The
# bridge attaches to what is there and filters on the channel.
DEFAULT_PORT = "Ableton Loopback"

# Addresses AbletonOSC exposes as a `get` but NOT as a subscription. Measured
# against the installed version, not guessed: each one answered "Unknown OSC
# address". Lists (track_names, scenes/name) behave the same way and are already
# excluded, being declared as `text`.
NO_LISTEN = frozenset({
    "/live/song/get/num_tracks",
    "/live/song/get/num_scenes",
})


class Bridge:
    def __init__(self, m: Mapping, state_file: Path, log=print, port_name: str = DEFAULT_PORT,
                 config: Path | None = None):
        self.m = m
        self.config = config          # loaded file, for hot reload
        self._mtime = self._fingerprint()
        self.state_file = state_file
        self.log = log
        self.port_name = port_name
        self.state: dict[str, object] = {}
        self._stop = threading.Event()
        # indexed mappings, so we do not scan lists on every message
        self._sends = {(s.kind, s.channel, s.number): s for s in m.sends}
        self._verbs = {(v.kind, v.channel, v.number): v for v in m.verbs}
        self._watches: dict[str, list] = {}
        for w in m.watches:
            self._watches.setdefault(w.address, []).append(w)
        self._last_emit: dict[int, float] = {}   # rate-limited watches
        self._dirty = False                     # etat modifie depuis la derniere ecriture
        self._written = 0.0                      # date de la derniere ecriture
        self._texts: dict[str, list] = {}
        for t in m.texts:
            self._texts.setdefault(t.address, []).append(t)

    # ── hot reload ──────────────────────────────────────────────────────────
    def _fingerprint(self) -> float:
        """Most recent modification time in the configuration folder.

        We look at the whole folder, not just the loaded file: an edited `include`
        would not change the main file's timestamp, and the reload would miss it
        without saying a word.
        """
        if not self.config:
            return 0.0
        try:
            return max(f.stat().st_mtime for f in self.config.parent.glob("*.txt"))
        except ValueError:
            return 0.0

    def _reindex(self) -> None:
        self._sends = {(x.kind, x.channel, x.number): x for x in self.m.sends}
        self._verbs = {(v.kind, v.channel, v.number): v for v in self.m.verbs}
        self._watches, self._texts = {}, {}
        for w in self.m.watches:
            self._watches.setdefault(w.address, []).append(w)
        for t in self.m.texts:
            self._texts.setdefault(t.address, []).append(t)

    def _reload(self) -> None:
        """Re-read the configuration without restarting.

        A syntax error MUST NOT stop the bridge: we keep the previous configuration
        and say so. Editing a file during a rehearsal therefore cannot cut the
        feedback in the middle of a song.
        """
        from .mapping import load
        try:
            fresh = load(self.config)
        except Exception as e:
            self.log(f"configuration REFUSEE, l'ancienne rest en place —\n{e}")
            return
        self.m = fresh
        self._reindex()
        self._subscribe()
        self.log(f"reloaded: {len(fresh.sends)} commands · {len(fresh.verbs)} gestures · "
                 f"{len(fresh.watches)} feedbacks · {len(fresh.texts)} texts")

    # ── MIDI ────────────────────────────────────────────────────────────────
    @staticmethod
    def _find_port(ports: list[str], nom: str) -> int | None:
        for i, p in enumerate(ports):
            if nom.lower() in p.lower():
                return i
        return None

    def _open_midi(self):
        self.midi_in = rtmidi.MidiIn()
        self.midi_out = rtmidi.MidiOut()
        i_in = self._find_port(self.midi_in.get_ports(), self.port_name)
        i_out = self._find_port(self.midi_out.get_ports(), self.port_name)
        if i_in is None or i_out is None:
            # MIDI host absent: open a virtual port so the bridge starts anyway.
            # It is useless in that state, but it SAYS so and it does not die —
            # there would be nothing for a watchdog to restart.
            self.log(f"port \"{self.port_name}\" not found — is the MIDI host running? falling back to a virtual port")
            self.midi_in.open_virtual_port("OSC Bridge In")
            self.midi_out.open_virtual_port("OSC Bridge Out")
        else:
            self.midi_in.open_port(i_in)
            self.midi_out.open_port(i_out)
            self.log(f"MIDI on \"{self.midi_in.get_ports()[i_in]}\"")
        self.midi_in.set_callback(self._on_midi)

    def _on_midi(self, evenement, _data=None):
        message, _dt = evenement
        if len(message) < 3:
            return
        statut, d1, d2 = message[0], message[1], message[2]
        kind = "cc" if 0xB0 <= statut <= 0xBF else "note" if 0x90 <= statut <= 0x9F else None
        if kind is None:
            return
        channel_ = (statut & 0x0F) + 1
        cle = (kind, channel_, d1)
        v = self._verbs.get(cle)
        if v is not None:
            # A gesture: computes from the observed state, returns several messages.
            for address, args in GESTURES[v.name](self._state_snapshot(), d2):
                self._send(address, args)
            return
        s = self._sends.get(cle)
        if s is None:
            return
        val = s.transform(d2) if s.transform else d2
        # Substitutions available in arguments:
        #   $a     the CC value after transform      $a.0  the same, forced to float
        #   $track the SELECTED track, $scene the selected scene
        # The last two are what makes "act on the current track" expressible at all:
        # AbletonOSC has no such notion, it wants an index. The bridge watches it.
        subs = {"$a": val, "$a.0": float(val),
                "$track": int(self.state.get("_track", 0)),
                "$scene": int(self.state.get("_scene", 0))}
        args = [subs.get(a, a) if isinstance(a, str) else a for a in s.args]
        self._send(s.address, args)

    def _send(self, address: str, args: list):
        try:
            self.sock.sendto(osc.encode(address, args), (self.m.host, self.m.send_port))
        except Exception as e:
            self.log(f"OSC send {address}: {e}")

    def _state_snapshot(self) -> dict:
        """The state gestures read — never consulted anywhere else."""
        return {"scene": self.state.get("_scene", 0), "track": self.state.get("_track", 0),
                "scenes": self.state.get("_scenes"), "tracks": self.state.get("_tracks"),
                "playing": self.state.get("_playing"), "tempo": self.state.get("_tempo")}

    def _emit_cc(self, w, valeur: float):
        if w.every_ms:
            # Rate limiting: a VU meter sends dozens of messages per second and a
            # controller cannot redraw that fast. Drop the intermediate values
            # rather than flooding the MIDI link.
            maintenant = time.time() * 1000
            precedent = self._last_emit.get(id(w), 0)
            if maintenant - precedent < w.every_ms:
                return
            self._last_emit[id(w)] = maintenant
        v = int(round(valeur))
        if not 0 <= v <= 127:
            # MIDI is 7-bit: SAY it rather than silently truncating.
            self.log(f"{w.source}: {w.address} = {valeur} outside 0-127, clamped")
            v = max(0, min(127, v))
        statut = (0xB0 if w.kind == "cc" else 0x90) | (w.channel - 1)
        self.midi_out.send_message([statut, w.number, v])

    # ── OSC ─────────────────────────────────────────────────────────────────
    def _open_osc(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.m.recv_port))
        self.sock.settimeout(0.5)
        self.log(f"OSC → {self.m.host}:{self.m.send_port}, replies on {self.m.recv_port}")

    def _subscribe(self):
        """Live broadcasts nothing until asked.

        Hence a `start_listen` AND an initial `get`: without the `get`, the state
        stays empty until the first change — and a key lit by mistake is worse
        than a key left dark.
        """
        # These six are watched NO MATTER WHAT: they are what makes gestures
        # computable, even when the configuration does not send them back as MIDI.
        essential = ["/live/view/get/selected_track", "/live/view/get/selected_scene",
                      "/live/song/get/num_scenes", "/live/song/get/num_tracks",
                      "/live/song/get/is_playing", "/live/song/get/tempo"]
        for address in list(dict.fromkeys(list(self._watches) + list(self._texts) + essential)):
            # Some addresses have NO start_listen and AbletonOSC answers "Unknown
            # OSC address" — every 30 s, straight into Live's log. Asking once is a
            # mistake; asking forever is noise that hides real errors. They are
            # re-read by the `get` below, which is plenty for values that barely move.
            if address not in self._texts and address not in NO_LISTEN:
                self.sock.sendto(osc.encode(address.replace("/get/", "/start_listen/")),
                                 (self.m.host, self.m.send_port))
            self.sock.sendto(osc.encode(address), (self.m.host, self.m.send_port))
        self.log(f"{len(self._watches)} subscriptions, {len(self._texts)} texts")

    def _on_osc(self, address: str, args: list):
        # The current selection is remembered no matter what: it is what makes
        # "the current track" expressible, even if no watch sends it back as MIDI.
        if address == "/live/view/get/selected_track" and args:
            self.state["_track"] = args[0]
        elif address == "/live/view/get/selected_scene" and args:
            self.state["_scene"] = args[0]
        elif address == "/live/song/get/num_scenes" and args:
            self.state["_scenes"] = args[0]
        elif address == "/live/song/get/num_tracks" and args:
            self.state["_tracks"] = args[0]
        elif address == "/live/song/get/is_playing" and args:
            self.state["_playing"] = args[0]
        elif address == "/live/song/get/tempo" and args:
            self.state["_tempo"] = args[0]
        if address.startswith("/live/") and args:
            self._dirty = True
        for w in self._watches.get(address, []):
            if w.arg < len(args):
                v = args[w.arg]
                v = 1 if v is True else 0 if v is False else v
                if isinstance(v, (int, float)):
                    self._emit_cc(w, w.transform(v) if w.transform else v)
        for t in self._texts.get(address, []):
            # Text cannot travel over MIDI: it goes to the file the client re-reads.
            self.state[t.key] = args if len(args) != 1 else args[0]
            self._dirty = True

    def _write_state(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)   # atomic replace: never a half-written file

    # ── boucle ──────────────────────────────────────────────────────────────
    def run(self):
        self._open_osc()
        self._open_midi()
        self._subscribe()
        last_refresh = 0.0
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                data = None
            except OSError as e:
                self.log(f"socket: {e}")
                time.sleep(1)
                continue
            if data:
                # Fan-out: AbletonOSC forces its reply port and does not answer the
                # source port, so only one process can read them. The bridge holds it
                # — it serves the show — and relays to the other clients.
                for target in self.m.fanout:
                    try:
                        self.sock.sendto(data, target)
                    except OSError:
                        pass          # a client that is away must never interrupt anything
                msg = osc.decode(data)
                if msg:
                    self._on_osc(*msg)
            # Batched state write: four times per second at most. Without it the
            # file would be rewritten on every incoming message — the same reflex
            # that rate limiting exists to tame on VU meters.
            if self._dirty and time.time() - self._written > 0.25:
                self._written = time.time(); self._dirty = False
                self._write_state()
            # Hot reload: edit the file, save, the bridge follows.
            fp = self._fingerprint()
            if fp > self._mtime:
                self._mtime = fp
                self._reload()
            # If Live restarts, our subscriptions die with it without warning:
            # re-issue them periodically. This is idempotent on the AbletonOSC side.
            if time.time() - last_refresh > 30:
                last_refresh = time.time()
                self._subscribe()

    def stop(self):
        self._stop.set()
