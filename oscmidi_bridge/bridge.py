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

from . import midi, osc
from .mapping import ANY, Mapping
from .projection import load as load_projection

# Default MIDI port: the one the controller ALREADY uses.
#
# No virtual port is created: the controller already emits to an existing port
# that its MIDI host provides permanently. One more port would be one more thing
# to configure on every key, and one more to lose when the host goes down. The
# bridge attaches to what is there and filters on the channel.
# The port name is a property of an INSTALLATION, not of this bridge: it is what the
# person called their MIDI bus. The default therefore describes the port's ROLE rather
# than naming one rig's — "Rig Bus" was one person's name and had no business being the
# value everyone else inherits. `ports.py` prefers the first real port anyway; this is
# only the label of last resort, and the sentinel that means "the config said nothing".
DEFAULT_PORT = "OSC Bridge"


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
        self._sends = {(s.port, s.kind, s.channel, s.number): s for s in m.sends}
        self._verbs = {(v.port, v.kind, v.channel, v.number): v for v in m.verbs}
        self._watches: dict[str, list] = {}
        for w in m.watches:
            self._watches.setdefault(w.address, []).append(w)
        self._last_emit: dict[int, float] = {}   # rate-limited watches
        self._dirty = False                     # etat modifie depuis la derniere ecriture
        self._written = 0.0                      # date de la derniere ecriture
        self._texts: dict[str, list] = {}
        for t in m.texts:
            self._texts.setdefault(t.address, []).append(t)
        # Phases. `boot` replays whenever Live comes back: a set reloaded or Live
        # restarted leaves the master routing wherever the new set put it, and a
        # monitoring path that silently reverts is discovered on stage.
        self._live_seen = False
        self._last_reply = 0.0
        self._tracks_seen = None
        self.proj = load_projection(m.projection)
        # An unknown gesture would match nothing and stay silent for a whole show.
        # It cannot be caught while parsing -- the projection is only known here --
        # so it is caught here, before a single message goes out.
        unknown = sorted({v.name for v in m.verbs} - set(self.proj.gestures))
        if unknown:
            known = ", ".join(sorted(self.proj.gestures)) or "none"
            raise ValueError(f"gestures unknown to projection {self.proj.name!r}: "
                             f"{', '.join(unknown)} — known: {known}")
        self._triggers = {(t.port, t.kind, t.channel, t.number): t for t in m.triggers}

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
        self._sends = {(x.port, x.kind, x.channel, x.number): x for x in self.m.sends}
        self._verbs = {(v.port, v.kind, v.channel, v.number): v for v in self.m.verbs}
        self._watches, self._texts = {}, {}
        for w in self.m.watches:
            self._watches.setdefault(w.address, []).append(w)
        for t in self.m.texts:
            self._texts.setdefault(t.address, []).append(t)
        self._triggers = {(t.port, t.kind, t.channel, t.number): t for t in self.m.triggers}

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
        """Open every port the configuration names, not just one.

        A rig separates its gear by PORT as much as by channel: the pedalboard,
        the amp modeller and the DAW loopback are three cables, and all three may
        speak on channel 1 without meaning the same thing. Opening one port per
        distinct name keeps them apart, and a line's port is part of its address.
        """
        self._ins: dict[str, object] = {}
        self._outs: dict[str, object] = {}
        for name in self._ports_used():
            self._open_one(name)
        self.midi_in = self._ins.get(self.port_name)      # kept for compatibility
        self.midi_out = self._outs.get(self.port_name)

    def _ports_used(self) -> list[str]:
        used = {self.port_name}
        for group in (self.m.sends, self.m.verbs, self.m.watches, self.m.triggers):
            used |= {x.port for x in group if x.port}
        return sorted(used)

    def _open_one(self, name: str) -> None:
        mi, mo = rtmidi.MidiIn(), rtmidi.MidiOut()
        i_in = self._find_port(mi.get_ports(), name)
        i_out = self._find_port(mo.get_ports(), name)
        if i_in is None or i_out is None:
            # Port absent: open a virtual one so the bridge starts anyway. It is
            # useless in that state, but it SAYS so and it does not die — there
            # would be nothing for a watchdog to restart.
            self.log(f'port "{name}" not found — is the MIDI host running? falling back to a virtual port')
            mi.open_virtual_port(f"OSC Bridge In ({name})")
            mo.open_virtual_port(f"OSC Bridge Out ({name})")
        else:
            mi.open_port(i_in)
            mo.open_port(i_out)
            self.log(f'MIDI on "{mi.get_ports()[i_in]}"')
        # The callback is told WHICH port fired it: the same channel and number
        # on two ports are two different addresses.
        mi.set_callback(self._on_midi, name)
        self._ins[name], self._outs[name] = mi, mo

    def _out(self, name: str | None):
        return self._outs.get(name or self.port_name) or self._outs[self.port_name]

    # ── phases ──────────────────────────────────────────────────────────────
    def _resolve(self, args: list, val: int | None = None) -> list:
        """Substitute $a / $track / $scene in an argument list.

        $track and $scene are what makes "act on the current selection"
        expressible: the OSC server only wants an index, and the bridge is the one
        watching which index that is.
        """
        subs = {"$track": int(self.state.get("_track", 0)),
                "$scene": int(self.state.get("_scene", 0))}
        if val is not None:
            subs["$a"] = val
            subs["$a.0"] = float(val)
        return [subs.get(a, a) if isinstance(a, str) else a for a in args]

    def _run_phase(self, phase: str, value: int | None = None) -> None:
        """Play a phase. `value` is the number that fired it, if any.

        A Program Change fires a phase AND says which one of something: with
        `trigger song pc *`, $a inside the phase is the program number, so one
        phase can serve every song instead of one phase per song.
        """
        steps = [st for st in self.m.steps if st.phase == phase]
        if not steps:
            return
        for st in steps:
            self._send(st.address, self._resolve(st.args, value))
        self.log(f"phase '{phase}': {len(steps)} messages")

    def _on_midi(self, event, port=None):
        message, _dt = event
        decoded = midi.decode(message)
        if decoded is None:
            return
        kind, channel, d1, d2 = decoded
        port = port or self.port_name
        key = (port, kind, channel, d1)
        # A `*` number matches whatever arrived, and the number becomes the value.
        # Messages that have no number at all (channel pressure, pitch bend) only
        # ever match this way.
        wild = (port, kind, channel, ANY)
        t = self._triggers.get(key) or self._triggers.get(wild)
        if t is not None:
            self._run_phase(t.phase, value=d2)
            return
        v = self._verbs.get(key) or self._verbs.get(wild)
        if v is not None:
            # A gesture: computes from the observed state, returns several messages.
            for address, args in self.proj.gestures[v.name](self._state_snapshot(), d2):
                self._send(address, args)
            return
        s = self._sends.get(key) or self._sends.get(wild)
        if s is None:
            return
        val = s.transform(d2) if s.transform else d2
        self._send(s.address, self._resolve(s.args, val))

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

    def _emit_cc(self, w, value: float):
        if w.every_ms:
            # Rate limiting: a VU meter sends dozens of messages per second and a
            # controller cannot redraw that fast. Drop the intermediate values
            # rather than flooding the MIDI link.
            now = time.time() * 1000
            previous = self._last_emit.get(id(w), 0)
            if now - previous < w.every_ms:
                return
            self._last_emit[id(w)] = now
        v = int(round(value))
        ceiling = midi.KINDS[w.kind].maximum
        if not 0 <= v <= ceiling:
            # MIDI values are 7-bit, except pitch bend at 14: SAY it rather than
            # silently truncating, and name the ceiling that applied.
            self.log(f"{w.source}: {w.address} = {value} outside 0-{ceiling}, clamped")
            v = max(0, min(ceiling, v))
        num = 0 if w.number == ANY else w.number
        self._out(w.port).send_message(midi.encode(w.kind, w.channel, num, v))

    # ── OSC ─────────────────────────────────────────────────────────────────
    def _open_osc(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.m.recv_port))
        self.sock.settimeout(0.5)
        self.log(f"OSC → {self.m.host}:{self.m.send_port}, replies on {self.m.recv_port}")

    def _subscribe(self):
        """The application broadcasts nothing until asked.

        Hence a `start_listen` AND an initial `get`: without the `get`, the state
        stays empty until the first change — and a key lit by mistake is worse
        than a key left dark.
        """
        # The projection names what must be watched NO MATTER WHAT: the gestures
        # compute from it, even when the configuration never sends it back as MIDI.
        essential = list(self.proj.essential)
        for address in list(dict.fromkeys(list(self._watches) + list(self._texts) + essential)):
            # Some addresses have NO start_listen and the OSC server answers "Unknown
            # OSC address" — every 30 s, straight into Live's log. Asking once is a
            # mistake; asking forever is noise that hides real errors. They are
            # re-read by the `get` below, which is plenty for values that barely move.
            if address not in self._texts and address not in self.proj.no_listen:
                self.sock.sendto(osc.encode(address.replace("/get/", "/start_listen/")),
                                 (self.m.host, self.m.send_port))
            self.sock.sendto(osc.encode(address), (self.m.host, self.m.send_port))
        self.log(f"{len(self._watches)} subscriptions, {len(self._texts)} texts")

    def _on_osc(self, address: str, args: list):
        # A reply means the application is there. The FIRST one after a silence replays the
        # `boot` phase: the application restarted, or loaded another document, and either way
        # the master routing is now whatever that set decided. Re-asserting it is
        # idempotent, so doing it once too often costs nothing — missing it once
        # is discovered on stage.
        self._last_reply = time.time()
        if not self._live_seen:
            self._live_seen = True
            self._run_phase("boot")
        # A DOCUMENT loaded rather than a process restarted: nothing ever went
        # quiet, so the silence check above never fires -- yet the new document
        # brought its own settings, which is exactly what `boot` exists to assert.
        # The projection names the address that witnesses it; the bridge already
        # reads it. A false positive replays boot, which is idempotent.
        if self.proj.witness and address == self.proj.witness and args:
            names = tuple(args)
            if self._tracks_seen is not None and names != self._tracks_seen:
                self.log("the document changed — replaying the boot phase")
                self._run_phase("boot")
            self._tracks_seen = names
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
                # Fan-out: the OSC server forces its reply port and does not answer the
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
            # re-issue them periodically. Re-subscribing is expected to be idempotent.
            # Live gone quiet for two refresh cycles: consider it away, so that
            # its return replays `boot`. Two cycles rather than one, because a
            # single missed answer is a hiccup, not a restart.
            if self._live_seen and time.time() - self._last_reply > 70:
                self._live_seen = False
                self.log("Live is silent — the boot phase will replay when it answers again")
            if time.time() - last_refresh > 30:
                last_refresh = time.time()
                self._subscribe()

    def stop(self):
        self._stop.set()
