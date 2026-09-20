"""Write the list of available MIDI ports INTO the configuration file.

Nobody remembers the exact name of a MIDI port — "Rig Bus", "Bome MIDI
Translator 1", "RME Fireface UCX Port 1"… — and retyping one by hand produces
mistakes you only discover when nothing answers. So the tool goes and finds
them, and writes them as comments next to the active port: you just move the "#".

The block is delimited, so it can be regenerated without touching the rest.
"""

from __future__ import annotations

from .mapping import DEFAULT_PORT

import datetime
from pathlib import Path

DEBUT = "# ┌── MIDI PORTS ─ written by --ports"
FIN = "# └── end of ports ────────────────────────────────────────────────────────"


def ports_disponibles() -> list[str]:
    import rtmidi
    entrees = set(rtmidi.MidiIn().get_ports())
    sorties = set(rtmidi.MidiOut().get_ports())
    # Only ports usable in BOTH directions are offered: the bridge receives
    # commands and sends state back on the same port.
    return sorted(_reparer(p) for p in entrees & sorties)


def _reparer(nom: str) -> str:
    """rtmidi returns CoreMIDI names as UTF-8 bytes decoded through MacRoman.

    "Périphérique" therefore arrives as "P√©riph√©rique". Round-trip it back when
    that works, and leave it alone when it doesn't.
    """
    try:
        return nom.encode("mac_roman").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return nom


def write_ports(path_: Path) -> list[str]:
    lines = path_.read_text(encoding="utf-8").splitlines()
    available = ports_disponibles()

    # whatever is active today, so it survives the rewrite
    active_port = next((l.split(None, 1)[1].strip().strip('"')
                       for l in lines if l.strip().startswith("port ")), None)
    active_channel = next((l.split()[1] for l in lines if l.strip().startswith("channel ")), "16")
    if active_port is None:
        active_port = next((p for p in available if "ableton loopback" in p.lower()),
                          available[0] if available else DEFAULT_PORT)

    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    block = [f"{DEBUT} on {when} ──────────────────────",
            "#   The active port is the uncommented `port` line below.",
            "#   To change it: comment that one out, uncomment another.",
            "#   Only ports available for BOTH input and output are listed."]
    for p in available:
        block.append(f"port    {p}" if p == active_port else f"# port    {p}")
    if active_port not in available:
        block.append(f"port    {active_port}    # ⚠ not present on this machine right now")
    block += ["#", "#   Default channel: send/verb/watch lines may then omit the",
             "#   channel (`send cc 20 /live/song/start_playing`).",
             f"channel {active_channel}", FIN]

    # replace the existing block, or insert it after the `group` line
    try:
        i = next(k for k, l in enumerate(lines) if l.startswith(DEBUT))
        j = next(k for k, l in enumerate(lines) if l.startswith(FIN))
        fresh = lines[:i] + block + lines[j + 1:]
    except StopIteration:
        # drop any stray port/channel lines so they are not duplicated
        rest = [l for l in lines if not l.strip().startswith(("port ", "channel "))]
        k = next((n for n, l in enumerate(rest) if l.startswith("group")), 0) + 1
        fresh = rest[:k] + [""] + block + rest[k:]
    path_.write_text("\n".join(fresh) + "\n", encoding="utf-8")

    return [f"{len(available)} ports available, written into {path_.name}",
            *(f"   {'▸' if p == active_port else ' '} {p}" for p in available),
            f"   default channel: {active_channel}"]
