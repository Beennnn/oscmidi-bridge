"""Lecture des fichiers de correspondance.

Un fichier par groupe (funk, bs, evy) plus un common, comme décidé pour le rig :
les index de piste et les noms de scène ne veulent rien dire d'un répertoire à
l'autre, et un fichier unique obligerait à préfixer chaque nom.

Quatre words-clés, une instruction par line, aucune imbrication. L'indentation est
cosmétique. `#` commente jusqu'à la end de la line — et pas la line entière,
contrairement au `-` du langage de trevligaspel, qui mangeait des commandes
entières when on posait un commentaire au mauvais endroit.

    group    bs
    include  common.txt
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
#   $a  la valeur MIDI après transformation   $v  la valeur MIDI raw_line


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
# Volontairement UNE only_selected opération binaire : $v, $v+50, $v*2, $v/2, $v-64.
# Assez pour un tempo ou un décalage signé ; trop peu pour devenir un langage.
_TRANSFORM = re.compile(r"^\$v(?:\s*([+\-*/])\s*(-?\d+(?:\.\d+)?))?$")


def parse_transform(tok: str):
    m = _TRANSFORM.match(tok)
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
    transform: Any = field(default=None)
    source: str = ""   # fichier:line, pour les messages d'erreur


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
    transform: Any = field(default=None)
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
    # Port MIDI et channel_ par défaut : écrits une fois en tête de fichier plutôt que
    # répétés sur chaque line. `python3 -m oscmidi_bridge --ports` inscrit dans le
    # fichier la liste des ports réellement présents, il n'y a qu'à décommenter.
    port: str = "Ableton Loopback"
    channel: int = 16
    host: str = "127.0.0.1"
    send_port: int = 11000
    recv_port: int = 11001
    # Ports a qui REDIFFUSER les reponses de Live. AbletonOSC force le port de
    # reponse a 11001 (il ne repond PAS au port source), donc un seul processus
    # peut les recevoir. La passerelle le detient et relaie aux autres clients.
    fanout: list = field(default_factory=list)
    sends: list[Send] = field(default_factory=list)
    verbs: list[Verb] = field(default_factory=list)
    watches: list[Watch] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)


_TARGET_RE = re.compile(r"^([\d.]+):(\d+)\s*->\s*(\d+)$")


def load(path: Path, _vus: set[Path] | None = None) -> Mapping:
    """Charge un fichier et ses `include`, dans l'ordre d'apparition."""
    seen = _vus if _vus is not None else set()
    p = path.resolve()
    m = Mapping()
    if p in seen:
        return m            # include circulaire : on s'arrête sans rien dire de plus
    seen.add(p)

    for n, raw_line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        words = line.split()
        ou = f"{p.name}:{n}"
        try:
            match words[0]:
                case "group":
                    m.group = words[1]
                case "include":
                    sub = load(p.parent / words[1], seen)
                    m.sends += sub.sends
                    m.verbs += sub.verbs
                    m.watches += sub.watches
                    m.texts += sub.texts
                    m.fanout += sub.fanout
                case "port":
                    # le nom peut contenir des espaces : tout ce qui suit le mot-clé
                    m.port = " ".join(words[1:]).strip('"')
                case "channel":
                    c = int(words[1])
                    if not 1 <= c <= 16:
                        raise ValueError("channel_ MIDI hors 1-16")
                    m.channel = c
                case "fanout":
                    h, _, po = " ".join(words[1:]).partition(":")
                    m.fanout.append((h.strip(), int(po)))
                case "target":
                    c = _TARGET_RE.match(" ".join(words[1:]))
                    if not c:
                        raise ValueError("attendu  target 127.0.0.1:11000 -> 11001")
                    m.host, m.send_port, m.recv_port = c.group(1), int(c.group(2)), int(c.group(3))
                case "send":
                    # send <cc|note> [channel_] <numero> [transform] <address> [args…]
                    # Le channel_ est FACULTATIF : sans lui, celui de `channel`. On le
                    # reconnaît sans ambiguïté — une address commence toujours par /.
                    implicite = words[3].startswith("/") or words[3].startswith("$v")
                    kind = words[1]
                    channel_ = m.channel if implicite else int(words[2])
                    num = int(words[2]) if implicite else int(words[3])
                    rest = words[3:] if implicite else words[4:]
                    tr = None
                    if rest and rest[0].startswith("$v"):
                        tr = parse_transform(rest[0])
                        rest = rest[1:]
                    m.sends.append(Send(kind, channel_, num, rest[0],
                                        [literal(t) for t in rest[1:]], tr, ou))
                case "verb":
                    # verb <cc|note> [channel_] <numero> <nom du geste>
                    from .verbs import GESTURES
                    implicite = len(words) == 4
                    canal_v = m.channel if implicite else int(words[2])
                    num_v = int(words[2]) if implicite else int(words[3])
                    nom = words[3] if implicite else words[4]
                    if nom not in GESTURES:
                        raise ValueError(f"geste inconnu : {nom!r} — connus : {', '.join(sorted(GESTURES))}")
                    m.verbs.append(Verb(words[1], canal_v, num_v, nom, ou))
                case "watch":
                    # watch <address> <rang arg> -> <cc|note> <channel_> <numero> [transform]
                    i = words.index("->")
                    address, rang = words[1], int(words[2])
                    # watch <address> <rang> -> <cc|note> [channel_] <numero> [transform]
                    suite = words[i + 1:]
                    impl = len(suite) < 3 or not suite[2].lstrip("-").isdigit()
                    kind = suite[0]
                    channel_ = m.channel if impl else int(suite[1])
                    num = int(suite[1]) if impl else int(suite[2])
                    reste_w = suite[2:] if impl else suite[3:]
                    tr = None; every = 0
                    for tok in reste_w:
                        if tok.startswith("$v"):
                            tr = parse_transform(tok)
                        elif tok == "every":
                            continue
                        elif tok.rstrip("ms").isdigit():
                            every = int(tok.rstrip("ms"))
                        else:
                            raise ValueError(f"jeton inattendu apres le watch : {tok!r}")
                    m.watches.append(Watch(address, rang, kind, channel_, num, tr, every, ou))
                case "text":
                    i = words.index("->")
                    m.texts.append(Text(words[1], words[i + 1], ou))
                case _:
                    raise ValueError(f"mot-clé inconnu : {words[0]!r}")
        except Exception as e:
            # Un nom ou une forme inconnus doivent être DITS, jamais avalés : c'est
            # le silence des commandes rejetées qui coûte des heures de débogage.
            raise ValueError(f"{ou} : {e}\n    {raw_line.strip()}") from None
    return m
