"""Inscrit la liste des ports MIDI disponibles DANS le fichier de configuration.

Le nom exact d'un port ne se retient pas — « Ableton Loopback », « Bome MIDI
Translator 1 », « RME Fireface UCX Port 1 »… — et le recopier à la main produit
des fautes qu'on ne découvre qu'au moment où rien ne répond. L'outil va donc les
chercher et les écrit en commentaire, à côté du port actif : il n'y a qu'à
déplacer le « # ».

Le bloc est délimité, donc régénérable sans toucher au reste du fichier.
"""

from __future__ import annotations

import datetime
from pathlib import Path

DEBUT = "# ┌── PORTS MIDI ─ inscrit par --ports"
FIN = "# └── fin des ports ────────────────────────────────────────────────────────"


def ports_disponibles() -> list[str]:
    import rtmidi
    entrees = set(rtmidi.MidiIn().get_ports())
    sorties = set(rtmidi.MidiOut().get_ports())
    # On ne propose que les ports utilisables DANS LES DEUX SENS : la passerelle
    # reçoit les commandes et renvoie l'état sur le même port.
    return sorted(_reparer(p) for p in entrees & sorties)


def _reparer(nom: str) -> str:
    """rtmidi rend les noms CoreMIDI en octets UTF-8 décodés en MacRoman.

    « Périphérique » arrive donc en « P√©riph√©rique ». On refait le tour dans
    l'autre sens quand ça marche, et on laisse tel quel sinon.
    """
    try:
        return nom.encode("mac_roman").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return nom


def inscrire_ports(chemin: Path) -> list[str]:
    lignes = chemin.read_text(encoding="utf-8").splitlines()
    dispo = ports_disponibles()

    # ce qui est actif aujourd'hui, pour le conserver
    actif_port = next((l.split(None, 1)[1].strip().strip('"')
                       for l in lignes if l.strip().startswith("port ")), None)
    actif_canal = next((l.split()[1] for l in lignes if l.strip().startswith("channel ")), "16")
    if actif_port is None:
        actif_port = next((p for p in dispo if "ableton loopback" in p.lower()),
                          dispo[0] if dispo else "Ableton Loopback")

    quand = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    bloc = [f"{DEBUT} le {quand} ──────────────────────",
            "#   Le port actif est la ligne « port » non commentée ci-dessous.",
            "#   Pour en changer : commenter celle-là, décommenter une autre.",
            "#   Seuls les ports disponibles EN ENTRÉE ET EN SORTIE sont listés."]
    for p in dispo:
        bloc.append(f"port    {p}" if p == actif_port else f"# port    {p}")
    if actif_port not in dispo:
        bloc.append(f"port    {actif_port}    # ⚠ absent de la machine en ce moment")
    bloc += ["#", "#   Canal par défaut : les lignes send/verb/watch peuvent alors",
             "#   omettre le canal (« send cc 20 /live/song/start_playing »).",
             f"channel {actif_canal}", FIN]

    # remplacement du bloc existant, ou insertion après la ligne « group »
    try:
        i = next(k for k, l in enumerate(lignes) if l.startswith(DEBUT))
        j = next(k for k, l in enumerate(lignes) if l.startswith(FIN))
        neuf = lignes[:i] + bloc + lignes[j + 1:]
    except StopIteration:
        # on retire les anciennes lignes port/channel éparses pour ne pas les dupliquer
        reste = [l for l in lignes if not l.strip().startswith(("port ", "channel "))]
        k = next((n for n, l in enumerate(reste) if l.startswith("group")), 0) + 1
        neuf = reste[:k] + [""] + bloc + reste[k:]
    chemin.write_text("\n".join(neuf) + "\n", encoding="utf-8")

    return [f"{len(dispo)} ports disponibles, inscrits dans {chemin.name}",
            *(f"   {'▸' if p == actif_port else ' '} {p}" for p in dispo),
            f"   canal par défaut : {actif_canal}"]
