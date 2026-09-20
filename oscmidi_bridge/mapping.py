"""Reading the mapping files.

One file per group, plus a shared `common`. Track indices and scene names mean
nothing from one repertoire to the next, and a single file would force every name
to be prefixed.

A handful of keywords, one statement per line, no nesting. Indentation is
cosmetic. `#` comments to the END OF THE LINE — not the whole line, unlike the
`-` of the Stream Deck plugin dialect, which swallowed entire commands whenever a
comment landed in the wrong place.

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

from .midi import KINDS

# ── literal values ───────────────────────────────────────────────────────────
# OSC typing is significant: AbletonOSC refuses a track index sent as a float, so
# the type is inferred from how the value is written.
#   12  int   12.0  float   "x"  string   true/false  T/F   ~  nil
#   $a  the MIDI value after transformation   $v  the raw MIDI value


# A number slot written `*` means "any", and the number that arrives becomes the
# value. It exists for Program Change, where the interesting information IS the
# program number: one line then covers all 128 of them instead of 128 lines.
ANY = -1


def number(tok: str) -> int:
    return ANY if tok == "*" else int(tok)


def kind_of(tok: str) -> str:
    """Validate a message type AT PARSE TIME, so --verify catches a typo.

    A wrong type would otherwise match nothing at all and stay silent for the
    whole show -- the failure mode this configuration language exists to avoid.
    """
    if tok not in KINDS:
        raise ValueError(f"unknown message type: {tok!r} — known: {', '.join(KINDS)}")
    return tok


def tokenise(line: str) -> list[str]:
    """Split on whitespace, EXCEPT inside double quotes.

    Live names spaces freely -- an output routing is called "Ext. Out", a track
    "C-Cue Left". A plain split() cuts those in two and hands OSC a truncated
    name, which Live then fails to match without saying why. The quotes are KEPT,
    because they are what tells `literal` a token is a string rather than a number:
    dropping them here would turn "12" into the integer 12.
    """
    out: list[str] = []
    cur = ""
    quoted = False
    for ch in line:
        if ch == '"':
            quoted = not quoted
            cur += ch
        elif ch.isspace() and not quoted:
            if cur:
                out.append(cur); cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    if quoted:
        raise ValueError("unclosed double quote")
    return out


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


# ── transforming the MIDI value ──────────────────────────────────────────────
# Deliberately ONE binary operation and no more: $v, $v+50, $v*2, $v/2, $v-64, $v%4.
# Enough for a tempo, a signed offset or a cycle; too little to grow into a language.
# The modulo earns its place on one case the others cannot express: a counter that
# wraps. Live's beat number grows without bound — `$v%4` is what turns it into a
# position in the bar.
_TRANSFORM = re.compile(r"^\$v(?:\s*([+\-*/%])\s*(-?\d+(?:\.\d+)?))?$")


def parse_transform(tok: str):
    m = _TRANSFORM.match(tok)
    if not m:
        raise ValueError(f"unreadable transform: {tok!r} (expected $v, $v+50, $v*2 …)")
    op, n = m.group(1), m.group(2)
    if op is None:
        return lambda v: v
    k = float(n) if "." in n else int(n)
    return {
        "+": lambda v: v + k,
        "-": lambda v: v - k,
        "*": lambda v: v * k,
        "/": lambda v: v / k,
        "%": lambda v: v % k,
    }[op]


@dataclass
class Send:
    """Incoming MIDI → an OSC message."""
    kind: str          # "cc" | "note"
    channel: int       # 1-16, as written in the configuration
    number: int
    address: str
    args: list[Any]
    transform: Any = field(default=None)
    port: str = ""     # empty means the current default port
    source: str = ""   # file:line, for error messages


@dataclass
class Verb:
    """Incoming MIDI → a GESTURE: several messages computed from the observed state."""
    kind: str
    channel: int
    number: int
    name: str
    port: str = ""
    source: str = ""


@dataclass
class Watch:
    """An OSC reply → an outgoing CC."""
    address: str
    arg: int
    kind: str
    channel: int
    number: int
    transform: Any = field(default=None)
    # Minimum interval between two emissions, in milliseconds. Indispensable for
    # VU meters: without it the controller redraws on every single message, which
    # is enough to saturate the plugin's CPU. 120 ms is comfortable.
    every_ms: int = 0
    port: str = ""
    source: str = ""


@dataclass
class Step:
    """One OSC message belonging to a named PHASE, not to a MIDI key.

    Two phases matter in practice, and they differ by what they touch:
    `boot` sets what never moves during the show (master routing, output
    levels), `song` re-initialises what the playing DOES move, at the start of
    each piece. Steps run in the order they are written — that is the whole
    control flow, and it is enough.
    """
    phase: str
    address: str
    args: list[Any]
    source: str = ""


@dataclass
class Trigger:
    """A MIDI key that replays a phase."""
    phase: str
    kind: str
    channel: int
    number: int
    port: str = ""
    source: str = ""


@dataclass
class Text:
    """An OSC reply → a key in the JSON state file (what MIDI cannot carry)."""
    address: str
    key: str
    source: str = ""


@dataclass
class Mapping:
    group: str = "common"
    # Default MIDI port and channel: written once at the top of the file rather
    # than repeated on every line. `python3 -m oscmidi_bridge --ports` writes the
    # ports actually present into the file — uncomment the one you want.
    port: str = "Ableton Loopback"
    channel: int = 16
    host: str = "127.0.0.1"
    send_port: int = 11000
    recv_port: int = 11001
    # Ports to RELAY Live's replies to. AbletonOSC forces its reply port to 11001
    # (it does NOT answer the source port), so only one process can receive them.
    # The bridge holds that port and fans out to any other client.
    fanout: list = field(default_factory=list)
    sends: list[Send] = field(default_factory=list)
    verbs: list[Verb] = field(default_factory=list)
    watches: list[Watch] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)


_TARGET_RE = re.compile(r"^([\d.]+):(\d+)\s*->\s*(\d+)$")


def load(path: Path, _seen: set[Path] | None = None) -> Mapping:
    """Load a file and its `include`s, in order of appearance."""
    seen = _seen if _seen is not None else set()
    p = path.resolve()
    m = Mapping()
    if p in seen:
        return m            # circular include: stop here, quietly
    seen.add(p)
    cur_port, seen_port = m.port, False

    for n, raw_line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        words = tokenise(line)
        where = f"{p.name}:{n}"
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
                    m.steps += sub.steps
                    m.triggers += sub.triggers
                case "port":
                    # The name may contain spaces: everything after the keyword.
                    # `port` and `channel` apply to the lines that FOLLOW them, so
                    # a file can switch gear mid-way without repeating either on
                    # every line. Stated once at the top, nothing changes.
                    cur_port = " ".join(words[1:]).strip('"')
                    if m.port == "Ableton Loopback" and not seen_port:
                        m.port = cur_port      # the first one is also the default
                    seen_port = True
                case "channel":
                    c = int(words[1])
                    if not 1 <= c <= 16:
                        raise ValueError("MIDI channel outside 1-16")
                    m.channel = c          # positional, like `port` above
                case "fanout":
                    h, _, po = " ".join(words[1:]).partition(":")
                    m.fanout.append((h.strip(), int(po)))
                case "target":
                    c = _TARGET_RE.match(" ".join(words[1:]))
                    if not c:
                        raise ValueError("expected  target 127.0.0.1:11000 -> 11001")
                    m.host, m.send_port, m.recv_port = c.group(1), int(c.group(2)), int(c.group(3))
                case "send":
                    # send <cc|note> [channel] <number> [transform] <address> [args…]
                    # The channel is OPTIONAL: without it, the file's `channel`.
                    # It is unambiguous — an address always starts with a slash.
                    implicit = words[3].startswith("/") or words[3].startswith("$v")
                    kind = kind_of(words[1])
                    chan = m.channel if implicit else int(words[2])
                    num = number(words[2]) if implicit else number(words[3])
                    rest = words[3:] if implicit else words[4:]
                    tr = None
                    if rest and rest[0].startswith("$v"):
                        tr = parse_transform(rest[0])
                        rest = rest[1:]
                    m.sends.append(Send(kind, chan, num, rest[0],
                                        [literal(t) for t in rest[1:]], tr,
                                        port=cur_port, source=where))
                case "verb":
                    # verb <cc|note> [channel] <number> <gesture name>
                    from .verbs import GESTURES
                    implicit = len(words) == 4
                    chan_v = m.channel if implicit else int(words[2])
                    num_v = number(words[2]) if implicit else number(words[3])
                    name = words[3] if implicit else words[4]
                    if name not in GESTURES:
                        raise ValueError(f"unknown gesture: {name!r} — known: {', '.join(sorted(GESTURES))}")
                    m.verbs.append(Verb(kind_of(words[1]), chan_v, num_v, name, port=cur_port, source=where))
                case "watch":
                    # watch <address> <arg index> -> <cc|note> [channel] <number> [transform]
                    i = words.index("->")
                    address, rank = words[1], int(words[2])
                    tail = words[i + 1:]
                    impl = len(tail) < 3 or not tail[2].lstrip("-").isdigit()
                    kind = kind_of(tail[0])
                    chan = m.channel if impl else int(tail[1])
                    num = number(tail[1]) if impl else number(tail[2])
                    extras = tail[2:] if impl else tail[3:]
                    tr = None; every = 0
                    for tok in extras:
                        if tok.startswith("$v"):
                            tr = parse_transform(tok)
                        elif tok == "every":
                            continue
                        elif tok.rstrip("ms").isdigit():
                            every = int(tok.rstrip("ms"))
                        else:
                            raise ValueError(f"unexpected token after watch: {tok!r}")
                    m.watches.append(Watch(address, rank, kind, chan, num, tr, every,
                                           port=cur_port, source=where))
                case "on":
                    # on <phase> <address> [args…]
                    # No block, no indentation, no nesting: the phase name is
                    # repeated on every line. Each line stays readable on its own,
                    # and the grammar gains a feature without gaining a shape.
                    m.steps.append(Step(words[1], words[2],
                                        [literal(t) for t in words[3:]], source=where))
                case "trigger":
                    # trigger <phase> <cc|note> [channel] <number>
                    implicit = len(words) == 4
                    chan_t = m.channel if implicit else int(words[3])
                    num_t = number(words[3]) if implicit else number(words[4])
                    m.triggers.append(Trigger(words[1], kind_of(words[2]), chan_t, num_t, port=cur_port, source=where))
                case "text":
                    i = words.index("->")
                    m.texts.append(Text(words[1], words[i + 1], source=where))
                case _:
                    raise ValueError(f"unknown keyword: {words[0]!r}")
        except Exception as e:
            # An unknown name or shape must be SAID, never swallowed: silently
            # rejected commands are what cost hours of debugging.
            raise ValueError(f"{where}: {e}\n    {raw_line.strip()}") from None
    return m
