"""Point d'entrée : `python3 -m oscmidi_bridge [fichier.txt] [--verify]`.

`--verify` charge la configuration, affiche ce qu'elle déclare et sort — à passer
AVANT le concert plutôt que de découvrir une faute de frappe sur scène.
"""
from __future__ import annotations

import signal
import sys
from pathlib import Path

from .bridge import Bridge
from .mapping import load
from .ports import inscrire_ports

RACINE = Path(__file__).resolve().parent.parent


def _config_par_defaut() -> Path:
    """Where to look for the configuration, in order.

    It does NOT live in this repository: it describes one specific rig — CC
    numbers, track indices, ports — whereas the engine is generic.
    """
    import os
    if os.environ.get("OSCMIDI_CONFIG"):
        return Path(os.environ["OSCMIDI_CONFIG"]).expanduser()
    rig = Path.home() / "dev/music/rig-config/oscmidi/common.txt"
    return rig if rig.exists() else RACINE / "examples" / "common.txt"


def journal(*a):
    print(*a, flush=True)   # the service manager redirects stdout to the log file


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    verify = "--verify" in argv
    ports = "--ports" in argv
    path_ = Path(args[0]) if args else _config_par_defaut()
    if not path_.exists():
        journal(f"configuration not found: {path_}")
        return 2
    if ports:
        # We write the list INTO the file rather than just printing it: nobody
        # remembers the exact name of a MIDI port, and retyping one is a source of
        # mistakes. Just uncomment the one you want.
        for l in inscrire_ports(path_):
            journal(l)
        return 0
    try:
        m = load(path_)
    except ValueError as e:
        journal(f"configuration rejected —\n{e}")
        return 2

    journal(f"group '{m.group}' · {len(m.sends)} commands · {len(m.verbs)} gestures · "
            f"{len(m.watches)} feedbacks · {len(m.texts)} texts · {path_.name}")
    if verify:
        for s in m.sends:
            journal(f"  {s.kind} {s.channel:>2} {s.number:>3}  ->  {s.address} {s.args}")
        for v in m.verbs:
            journal(f"  {v.kind} {v.channel:>2} {v.number:>3}  ~>  {v.name}")
        for w in m.watches:
            journal(f"  {w.address}[{w.arg}]  ->  {w.kind} {w.channel} {w.number}")
        return 0

    b = Bridge(m, RACINE / "state.json", journal, m.port, path_)
    signal.signal(signal.SIGTERM, lambda *_: b.stop())
    signal.signal(signal.SIGINT, lambda *_: b.stop())
    b.run()
    journal("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
