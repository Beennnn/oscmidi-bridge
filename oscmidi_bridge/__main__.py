"""Entry point: `python3 -m oscmidi_bridge [file.txt] [--verify]`.

`--verify` loads the configuration, prints what it declares and exits — to be run
BEFORE the show rather than discovering a typo on stage.
"""
from __future__ import annotations

import signal
import sys
from pathlib import Path

from .bridge import Bridge
from .mapping import load
from .ports import write_ports

ROOT = Path(__file__).resolve().parent.parent


def _default_config() -> Path:
    """Where to look for the configuration, in order.

    It does NOT live in this repository: it describes one specific rig — CC
    numbers, track indices, ports — whereas the engine is generic.
    """
    import os
    if os.environ.get("OSCMIDI_CONFIG"):
        return Path(os.environ["OSCMIDI_CONFIG"]).expanduser()
    rig = Path.home() / ".config/oscmidi/common.txt"
    return rig if rig.exists() else ROOT / "examples" / "common.txt"


def log(*a):
    print(*a, flush=True)   # the service manager redirects stdout to the log file


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    verify = "--verify" in argv
    ports = "--ports" in argv
    path_ = Path(args[0]) if args else _default_config()
    if not path_.exists():
        log(f"configuration not found: {path_}")
        return 2
    if ports:
        # We write the list INTO the file rather than just printing it: nobody
        # remembers the exact name of a MIDI port, and retyping one is a source of
        # mistakes. Just uncomment the one you want.
        for l in write_ports(path_):
            log(l)
        return 0
    try:
        m = load(path_)
    except ValueError as e:
        log(f"configuration rejected —\n{e}")
        return 2

    phases = sorted({st.phase for st in m.steps})
    log(f"group '{m.group}' · {len(m.sends)} commands · {len(m.verbs)} gestures · "
        f"{len(m.watches)} feedbacks · {len(m.texts)} texts · "
        f"{len(m.steps)} steps in {len(phases)} phases · {path_.name}")
    if verify:
        for s in m.sends:
            log(f"  {s.kind} {s.channel:>2} {s.number:>3}  ->  {s.address} {s.args}")
        for v in m.verbs:
            log(f"  {v.kind} {v.channel:>2} {v.number:>3}  ~>  {v.name}")
        for w in m.watches:
            log(f"  {w.address}[{w.arg}]  ->  {w.kind} {w.channel} {w.number}")
        for ph in phases:
            fired = [t for t in m.triggers if t.phase == ph]
            how = (f"on {fired[0].kind} {fired[0].channel} {fired[0].number}" if fired
                   else "when Live answers" if ph == "boot" else "NEVER FIRED — no trigger")
            log(f"  phase '{ph}' ({how})")
            for st in [x for x in m.steps if x.phase == ph]:
                log(f"      {st.address} {st.args}")
        return 0

    b = Bridge(m, ROOT / "state.json", log, m.port, path_)
    signal.signal(signal.SIGTERM, lambda *_: b.stop())
    signal.signal(signal.SIGINT, lambda *_: b.stop())
    b.run()
    log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
