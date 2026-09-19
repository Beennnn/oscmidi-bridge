#!/usr/bin/env python3
"""Génère `config/catalogue.map` : TOUTES les adresses d'AbletonOSC, commentées.

Le principe : on n'écrit plus une ligne de configuration, on en **décommente** une.
Chaque adresse exposée par l'AbletonOSC installé apparaît avec un numéro de CC déjà
attribué, unique, dans un canal libre — il n'y a qu'à retirer le « # ».

Les numéros sont attribués dans un ORDRE D'UTILITÉ (song, view, scene, track, puis
le reste) pour que ce qui sert tous les jours tombe sur le canal 16, celui dont on a
vérifié qu'il est entièrement libre. Quand il est plein, on déborde sur 15 puis 14 —
et l'en-tête le dit, parce que ces deux-là portent des presets Evy inactifs et un
CC 100 du profil Live : à vérifier avant de décommenter.
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
# Numéros déjà pris par common.map sur le canal 16 — on ne les réattribue pas.
PRIS_16 = set(range(20, 50)) | set(range(60, 96)) | set(range(100, 110))
ORDRE = ["song", "view", "scene", "track", "clip", "clip_slot", "device", "application"]


def adresses() -> set[str]:
    out: set[str] = set()
    for f in ABLETONOSC.glob("*.py"):
        s = f.read_text(encoding="utf-8", errors="replace")
        base = BASES.get(f.name, f.stem)
        out |= set(re.findall(r'add_handler\(\s*"(/live/[^"%]+)"', s))
        for m in re.finditer(r'(?:for method in|methods\s*=)\s*\[(.*?)\]', s, re.S):
            out |= {f"/live/{base}/{n}" for n in re.findall(r'"(\w+)"', m.group(1))}
        for cle in ("properties_rw", "properties_r"):
            for m in re.finditer(cle + r"\s*=\s*\[(.*?)\]", s, re.S):
                for p in re.findall(r'"(\w+)"', m.group(1)):
                    out.add(f"/live/{base}/get/{p}")
                    if cle == "properties_rw":
                        out.add(f"/live/{base}/set/{p}")
    return out


def main() -> int:
    if not ABLETONOSC.exists():
        print(f"AbletonOSC introuvable : {ABLETONOSC}"); return 2
    adr = sorted(adresses())
    # attribution : canal 16 d'abord (hors numéros déjà pris), puis 15, puis 14
    libres = [(16, n) for n in range(128) if n not in PRIS_16] \
           + [(15, n) for n in range(128)] + [(14, n) for n in range(128)]
    it = iter(libres)

    par_base: dict[str, list[str]] = {}
    for a in adr:
        par_base.setdefault(a.split("/")[2], []).append(a)

    lignes = [f"""# CATALOGUE — toutes les adresses de l'AbletonOSC installé. GÉNÉRÉ, ne pas éditer.
#   régénérer :  python3 tools/gen_catalogue.py
#
# {len(adr)} adresses. Chacune a déjà son numéro : il n'y a qu'à retirer le « # ».
#
# ⚠ Le canal 16 est vérifié libre (0 CC utilisé par le rig, 0 mappage Cmd+M dans le
#   set Funk). Les canaux 15 et 14, où déborde la fin du catalogue, portent des
#   presets Evy inactifs et un CC 100 du profil Live : VÉRIFIER avant de décommenter.
#
# Conventions :  set/…  → send (la valeur du CC devient l'argument $a)
#                get/…  → watch (la réponse de Live revient en CC)
#                le reste → send sans argument (une méthode : play, undo, fire…)
#   Les adresses qui exigent un INDEX (piste, scène, device) le prennent en premier
#   argument : $track et $scene valent la sélection courante, observée par la passerelle.

group   catalogue
channel 16         # canal par defaut : les lignes du canal 16 ne le repetent pas
target  127.0.0.1:11000 -> 11001
"""]

    for base in ORDRE + [b for b in par_base if b not in ORDRE]:
        if base not in par_base:
            continue
        lignes.append(f"\n# ══ {base} ══════════════════════════════════════════════")
        for a in par_base[base]:
            canal, num = next(it)
            # Le canal n'est écrit QUE s'il diffère du canal par défaut : une ligne
            # sur le canal 16 s'écrit « send cc 42 /… », sans le répéter.
            ch = "" if canal == 16 else f"{canal} "
            index = "$track " if base == "track" else "$scene " if base == "scene" else ""
            if "/set/" in a:
                lignes.append(f"# send  cc {ch}{num:<3} {a} {index}$a")
            elif "/get/" in a:
                lignes.append(f"# watch {a} 0  ->  cc {ch}{num}")
            elif "/start_listen/" in a or "/stop_listen/" in a:
                continue                      # géré automatiquement par les `watch`
            else:
                lignes.append(f"# send  cc {ch}{num:<3} {a} {index}".rstrip())
    Path("config/catalogue.map").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print(f"config/catalogue.map : {len(adr)} adresses, "
          f"{sum(1 for l in lignes if l.startswith('# send') or l.startswith('# watch'))} lignes prêtes à décommenter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
