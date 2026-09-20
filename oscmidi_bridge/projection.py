"""Projections: what the bridge needs to know about the application it drives.

The bridge itself knows nothing about any application. It moves MIDI to OSC and
back, runs phases, rate-limits feedback, reloads on save — none of which mentions
a product. What *is* application-specific fits in three things, and they are what
a projection supplies:

- **gestures**: moves that no single address can express. "Next scene" is a loop
  over a count, "exclusive solo" is one message per track. They are arithmetic
  over observed state, and the state a given application exposes is its own.
- **essentials**: the addresses to watch NO MATTER WHAT, because the gestures
  compute from them even when the configuration never sends them back as MIDI.
- **witness**: the address whose value changing means the application loaded a
  different document. Nothing goes quiet in that case, so no silence check can
  see it -- yet it is exactly when `boot` must replay.
- **listen-only quirks**: addresses an application exposes as a read but not as
  a subscription, and which must therefore not be subscribed to. Asking once is
  a mistake; asking every refresh is noise in someone else's log.

A projection is a Python module exposing `GESTURES`, `ESSENTIAL`, `NO_LISTEN` and `WITNESS`.
It is named in the configuration:

    projection  oscmidi_ableton

Without one, the bridge still carries `send`, `watch`, `text` and `phase` — every
address written literally works. Only gestures need a projection, because only
gestures need to know what an application calls things.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, NamedTuple

Msg = tuple[str, list[Any]]
State = dict[str, Any]


class Projection(NamedTuple):
    name: str
    gestures: dict[str, Callable[[State, int], list[Msg]]]
    essential: tuple[str, ...]
    no_listen: frozenset[str]
    witness: str = ""


EMPTY = Projection("none", {}, (), frozenset(), "")


def load(name: str) -> Projection:
    """Import a projection by module name.

    A missing projection raises with its name in the message rather than leaving
    gestures silently unresolved -- a gesture that matches nothing would simply
    do nothing, for an entire show, without a word.
    """
    if not name:
        return EMPTY
    try:
        mod = importlib.import_module(name)
    except ImportError as e:
        raise ImportError(
            f"projection {name!r} not found -- install it, or drop the "
            f"`projection` line to run without gestures ({e})"
        ) from None
    return Projection(
        name=name,
        gestures=dict(getattr(mod, "GESTURES", {})),
        essential=tuple(getattr(mod, "ESSENTIAL", ())),
        no_listen=frozenset(getattr(mod, "NO_LISTEN", ())),
        witness=getattr(mod, "WITNESS", ""),
    )
