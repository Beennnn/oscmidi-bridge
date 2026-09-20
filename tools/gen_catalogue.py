#!/usr/bin/env python3
"""Generate `catalogue.txt`: EVERY AbletonOSC address, commented out.

The idea: you no longer write a configuration line, you **uncomment** one. Every
address the installed AbletonOSC exposes appears with a CC number already assigned,
unique, on a free channel — just remove the `#`.

Numbers are assigned in ORDER OF USEFULNESS (song, view, scene, track, then the
rest) so that what gets used daily lands on the default channel, the one verified
to be entirely free. When it fills up, the assignment overflows onto the next two —
and the header says so, because an overflow channel has NOT been verified free:
check it before uncommenting.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ABLETONOSC = Path.home() / "Music/Ableton/User Library/Remote Scripts/AbletonOSC/abletonosc"
BASES = {"song.py": "song", "track.py": "track", "clip.py": "clip", "device.py": "device",
         "clip_slot.py": "clip_slot", "scene.py": "scene", "view.py": "view",
         "application.py": "application"}
# Default channel, and the overflow channels used once it is full. They descend
# from the top on purpose: a rig fills its LOW channels with playing material, so
# the catalogue grows away from them and the two never meet in the middle.
CHANNEL, OVERFLOW = 16, (15, 14, 13, 12)
# Numbers already taken by the hand-written configuration — never reassigned.
TAKEN = set(range(20, 50)) | set(range(60, 96)) | set(range(100, 110))
ORDER = ["song", "view", "scene", "track", "clip", "clip_slot", "device", "application"]


def addresses() -> set[str]:
    out: set[str] = set()
    for f in ABLETONOSC.glob("*.py"):
        s = f.read_text(encoding="utf-8", errors="replace")
        base = BASES.get(f.name, f.stem)
        out |= set(re.findall(r'add_handler\(\s*"(/live/[^"%]+)"', s))
        for m in re.finditer(r'(?:for method in|methods\s*=)\s*\[(.*?)\]', s, re.S):
            out |= {f"/live/{base}/{n}" for n in re.findall(r'"(\w+)"', m.group(1))}
        for key in ("properties_rw", "properties_r"):
            for m in re.finditer(key + r"\s*=\s*\[(.*?)\]", s, re.S):
                for p in re.findall(r'"(\w+)"', m.group(1)):
                    out.add(f"/live/{base}/get/{p}")
                    if key == "properties_rw":
                        out.add(f"/live/{base}/set/{p}")
    return out


def _output() -> Path:
    """Where to write: a real rig's configuration lives in its own repository."""
    import os
    base = Path(os.environ.get("OSCMIDI_CONFIG_DIR",
                               Path.home() / ".config/oscmidi"))
    return base / "catalogue.txt" if base.exists() else Path("examples") / "catalogue.txt"


def main() -> int:
    if not ABLETONOSC.exists():
        print(f"AbletonOSC not found: {ABLETONOSC}"); return 2
    adr = sorted(addresses())
    # assignment: default channel first (minus the taken numbers), then the overflows
    free = [(CHANNEL, n) for n in range(128) if n not in TAKEN]
    for c in OVERFLOW:
        free += [(c, n) for n in range(128)]
    it = iter(free)

    by_base: dict[str, list[str]] = {}
    for a in adr:
        by_base.setdefault(a.split("/")[2], []).append(a)

    lines = [f"""# CATALOGUE — every address of the installed AbletonOSC. GENERATED, do not edit.
#   regenerate:  python3 tools/gen_catalogue.py
#
# {len(adr)} addresses. Each already has its number: just remove the leading `#`.
#
# WARNING: check that the default channel really is free on your setup, and above
#   all the overflow channels — nothing guarantees they are.
#
# Conventions:  set/…  -> send  (the CC value becomes the $a argument)
#               get/…  -> watch (Live's reply comes back as a CC)
#               anything else -> send with no argument (a method: play, undo, fire…)
#   Addresses that require an INDEX (track, scene, device) take it as the first
#   argument: $track and $scene are the current selection, observed by the bridge.

group   catalogue
channel {CHANNEL}         # default channel: lines on this channel do not repeat it
target  127.0.0.1:11000 -> 11001
"""]

    for base in ORDER + [b for b in by_base if b not in ORDER]:
        if base not in by_base:
            continue
        lines.append(f"\n# ══ {base} ══════════════════════════════════════════════")
        for a in by_base[base]:
            chan, num = next(it)
            # The channel is written ONLY when it differs from the default: a line
            # on the default channel reads `send cc 42 /…`, without repeating it.
            ch = "" if chan == CHANNEL else f"{chan} "
            index = "$track " if base == "track" else "$scene " if base == "scene" else ""
            if "/set/" in a:
                lines.append(f"# send  cc {ch}{num:<3} {a} {index}$a")
            elif "/get/" in a:
                lines.append(f"# watch {a} 0  ->  cc {ch}{num}")
            elif "/start_listen/" in a or "/stop_listen/" in a:
                continue                      # handled automatically by the `watch` lines
            else:
                lines.append(f"# send  cc {ch}{num:<3} {a} {index}".rstrip())
    _output().write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{_output()}: {len(adr)} addresses, "
          f"{sum(1 for l in lines if l.startswith('# send') or l.startswith('# watch'))} lines ready to uncomment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
