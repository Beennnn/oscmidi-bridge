"""Lecture des fichiers de correspondance.

Un fichier par groupe (funk, bs, evy) plus un common, comme décidé pour le rig :
les index de piste et les noms de scène ne veulent rien dire d'un répertoire à
l'autre, et un fichier unique obligerait à préfixer chaque nom.

Quatre mots-clés, une instruction par ligne, aucune imbrication. L'indentation est
cosmétique. `#` commente jusqu'à la fin de la ligne — et pas la ligne entière,
contrairement au `-` du langage de trevligaspel, qui mangeait des commandes
entières quand on posait un commentaire au mauvais endroit.

    group    bs
    include  common.map
    target   127.0.0.1:11000 -> 11001

    send  cc 13 20            /live/song/start_playing
    send  cc 13 24  $v+50     /live/song/set/tempo $a.0
    watch /live/song/get/is_playing   0  ->  cc 13 100
    watch /live/track/get/output_meter_level 0 -> cc 110  every 120ms
    text  /live/song/get/track_names       ->  tracks
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── valeurs littérales ───────────────────────────────────────────────────────
# Le typage OSC est significatif : AbletonOSC refuse un index de piste envoyé en
# float. On déduit donc le type de l'écriture, comme côté plugin Stream Deck.
#   12  entier   12.0  float   "x"  chaîne   true/false  T/F   ~  nil
#   $a  la valeur MIDI après transformation   $v  la valeur MIDI brute


def literal(tok: str) -> Any:
    if tok.startswith('"') and tok.endswith('"'):
        return tok[1:-1]
    if tok == "true":
        return True
    if tok == "false":
        return False
    if tok == "~":
        return None
    if re.fullmatch(r"-?\d+", tok):
        return int(tok)
    if re.fullmatch(r"-?\d*\.\d+", tok):
        return float(tok)
    return tok


# ── transformation de la valeur MIDI ─────────────────────────────────────────
# Volontairement UNE seule opération binaire : $v, $v+50, $v*2, $v/2, $v-64.
# Assez pour un tempo ou un décalage signé ; trop peu pour devenir un langage.
_TRANSFO = re.compile(r"^\$v(?:\s*([+\-*/])\s*(-?\d+(?:\.\d+)?))?$")


def parse_transfo(tok: str):
    m = _TRANSFO.match(tok)
    if not m:
        raise ValueError(f"transformation illisible : {tok!r} (attendu $v, $v+50, $v*2 …)")
    op, n = m.group(1), m.group(2)
    if op is None:
        return lambda v: v
    k = float(n) if "." in n else int(n)
    return {
        "+": lambda v: v + k,
        "-": lambda v: v - k,
        "*": lambda v: v * k,
        "/": lambda v: v / k,
    }[op]


@dataclass
class Send:
    """MIDI entrant → message OSC."""
    kind: str          # "cc" | "note"
    channel: int       # 1-16, comme l'écrit l'utilisateur
    number: int
    address: str
    args: list[Any]
    transfo: Any = field(default=None)
    source: str = ""   # fichier:ligne, pour les messages d'erreur


@dataclass
class Verb:
    """MIDI entrant → un GESTE, c'est-à-dire plusieurs messages calculés sur l'état."""
    kind: str
    channel: int
    number: int
    name: str
    source: str = ""


@dataclass
class Watch:
    """Réponse OSC → CC sortant."""
    address: str
    arg: int
    kind: str
    channel: int
    number: int
    transfo: Any = field(default=None)
    # Débit minimal entre deux émissions, en millisecondes. Indispensable pour les
    # VU-mètres : c'est exactement ce qui a saturé le CPU du plugin MIDI le 13/09
    # (redessin à chaque message reçu) et qu'un intervalle de 120 ms avait réglé.
    every_ms: int = 0
    source: str = ""


@dataclass
class Text:
    """Réponse OSC → clé du fichier d'état JSON (ce que le MIDI ne peut pas porter)."""
    address: str
    key: str
    source: str = ""


@dataclass
class Mapping:
    group: str = "common"
    # Port MIDI et canal par défaut : écrits une fois en tête de fichier plutôt que
    # répétés sur chaque ligne. `python3 -m oscmidi_bridge --ports` inscrit dans le
    # fichier la liste des ports réellement présents, il n'y a qu'à décommenter.
    port: str = "Ableton Loopback"
    channel: int = 16
    host: str = "127.0.0.1"
    send_port: int = 11000
    recv_port: int = 11001
    sends: list[Send] = field(default_factory=list)
    verbs: list[Verb] = field(default_factory=list)
    watches: list[Watch] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)


_CIBLE = re.compile(r"^([\d.]+):(\d+)\s*->\s*(\d+)$")


def load(path: Path, _vus: set[Path] | None = None) -> Mapping:
    """Charge un fichier et ses `include`, dans l'ordre d'apparition."""
    vus = _vus if _vus is not None else set()
    p = path.resolve()
    m = Mapping()
    if p in vus:
        return m            # include circulaire : on s'arrête sans rien dire de plus
    vus.add(p)

    for n, brute in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        ligne = brute.split("#", 1)[0].strip()
        if not ligne:
            continue
        mots = ligne.split()
        ou = f"{p.name}:{n}"
        try:
            match mots[0]:
                case "group":
                    m.group = mots[1]
                case "include":
                    sous = load(p.parent / mots[1], vus)
                    m.sends += sous.sends
                    m.verbs += sous.verbs
                    m.watches += sous.watches
                    m.texts += sous.texts
                case "port":
                    # le nom peut contenir des espaces : tout ce qui suit le mot-clé
                    m.port = " ".join(mots[1:]).strip('"')
                case "channel":
                    c = int(mots[1])
                    if not 1 <= c <= 16:
                        raise ValueError("canal MIDI hors 1-16")
                    m.channel = c
                case "target":
                    c = _CIBLE.match(" ".join(mots[1:]))
                    if not c:
                        raise ValueError("attendu  target 127.0.0.1:11000 -> 11001")
                    m.host, m.send_port, m.recv_port = c.group(1), int(c.group(2)), int(c.group(3))
                case "send":
                    # send <cc|note> [canal] <numero> [transfo] <adresse> [args…]
                    # Le canal est FACULTATIF : sans lui, celui de `channel`. On le
                    # reconnaît sans ambiguïté — une adresse commence toujours par /.
                    implicite = mots[3].startswith("/") or mots[3].startswith("$v")
                    kind = mots[1]
                    canal = m.channel if implicite else int(mots[2])
                    num = int(mots[2]) if implicite else int(mots[3])
                    reste = mots[3:] if implicite else mots[4:]
                    tr = None
                    if reste and reste[0].startswith("$v"):
                        tr = parse_transfo(reste[0])
                        reste = reste[1:]
                    m.sends.append(Send(kind, canal, num, reste[0],
                                        [literal(t) for t in reste[1:]], tr, ou))
                case "verb":
                    # verb <cc|note> [canal] <numero> <nom du geste>
                    from .verbs import VERBES
                    implicite = len(mots) == 4
                    canal_v = m.channel if implicite else int(mots[2])
                    num_v = int(mots[2]) if implicite else int(mots[3])
                    nom = mots[3] if implicite else mots[4]
                    if nom not in VERBES:
                        raise ValueError(f"geste inconnu : {nom!r} — connus : {', '.join(sorted(VERBES))}")
                    m.verbs.append(Verb(mots[1], canal_v, num_v, nom, ou))
                case "watch":
                    # watch <adresse> <rang arg> -> <cc|note> <canal> <numero> [transfo]
                    i = mots.index("->")
                    adresse, rang = mots[1], int(mots[2])
                    # watch <adresse> <rang> -> <cc|note> [canal] <numero> [transfo]
                    suite = mots[i + 1:]
                    impl = len(suite) < 3 or not suite[2].lstrip("-").isdigit()
                    kind = suite[0]
                    canal = m.channel if impl else int(suite[1])
                    num = int(suite[1]) if impl else int(suite[2])
                    reste_w = suite[2:] if impl else suite[3:]
                    tr = None; every = 0
                    for tok in reste_w:
                        if tok.startswith("$v"):
                            tr = parse_transfo(tok)
                        elif tok == "every":
                            continue
                        elif tok.rstrip("ms").isdigit():
                            every = int(tok.rstrip("ms"))
                        else:
                            raise ValueError(f"jeton inattendu apres le watch : {tok!r}")
                    m.watches.append(Watch(adresse, rang, kind, canal, num, tr, every, ou))
                case "text":
                    i = mots.index("->")
                    m.texts.append(Text(mots[1], mots[i + 1], ou))
                case _:
                    raise ValueError(f"mot-clé inconnu : {mots[0]!r}")
        except Exception as e:
            # Un nom ou une forme inconnus doivent être DITS, jamais avalés : c'est
            # le silence des commandes rejetées qui coûte des heures de débogage.
            raise ValueError(f"{ou} : {e}\n    {brute.strip()}") from None
    return m
