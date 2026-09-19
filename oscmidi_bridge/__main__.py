"""Point d'entrée : `python3 -m oscmidi_bridge [fichier.map] [--verify]`.

`--verify` charge la configuration, affiche ce qu'elle déclare et sort — à passer
AVANT le concert plutôt que de découvrir une faute de frappe sur scène.
"""
from __future__ import annotations

import signal
import sys
from pathlib import Path

from .bridge import Bridge, PORT_DEFAUT
from .mapping import load

RACINE = Path(__file__).resolve().parent.parent


def journal(*a):
    print(*a, flush=True)   # launchd redirige stdout vers le fichier de log


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    verify = "--verify" in argv
    chemin = Path(args[0]) if args else RACINE / "config" / "common.map"
    if not chemin.exists():
        journal(f"configuration introuvable : {chemin}")
        return 2
    try:
        m = load(chemin)
    except ValueError as e:
        journal(f"configuration refusée —\n{e}")
        return 2

    journal(f"groupe « {m.group} » · {len(m.sends)} commandes · {len(m.verbs)} gestes · "
            f"{len(m.watches)} retours · {len(m.texts)} textes · {chemin.name}")
    if verify:
        for s in m.sends:
            journal(f"  {s.kind} {s.channel:>2} {s.number:>3}  ->  {s.address} {s.args}")
        for v in m.verbs:
            journal(f"  {v.kind} {v.channel:>2} {v.number:>3}  ~>  {v.name}")
        for w in m.watches:
            journal(f"  {w.address}[{w.arg}]  ->  {w.kind} {w.channel} {w.number}")
        return 0

    b = Bridge(m, RACINE / "state.json", journal, PORT_DEFAUT)
    signal.signal(signal.SIGTERM, lambda *_: b.stop())
    signal.signal(signal.SIGINT, lambda *_: b.stop())
    b.run()
    journal("arrêt")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
