"""The MIDI channel-voice messages, described once instead of everywhere.

Every message type differs on three points, and getting any of them wrong fails
quietly: how many bytes it carries, whether it has a NUMBER as well as a value,
and how wide that value is. Program Change has no value byte at all, pitch bend
has fourteen bits rather than seven, and channel pressure has no number. Spelling
those differences out in one table is what keeps `if status == 0xC0` from being
scattered through the code.

`has_number` is the one that shapes the configuration language. A message WITH a
number is addressed by it -- `cc 20` is a different control from `cc 21`. A
message without one has nothing to address, so it is always written `*`, and its
single payload becomes the value.
"""

from __future__ import annotations

from typing import NamedTuple


class Kind(NamedTuple):
    status: int         # high nibble, channel goes in the low one
    length: int         # bytes on the wire, channel byte included
    has_number: bool    # is there something to address, beyond the channel?
    maximum: int        # widest value this message can carry


KINDS: dict[str, Kind] = {
    #        status  len  number  max
    "note":    Kind(0x90, 3, True, 127),    # number = the note, value = velocity
    "noteoff": Kind(0x80, 3, True, 127),
    "poly":    Kind(0xA0, 3, True, 127),    # polyphonic aftertouch, per note
    "cc":      Kind(0xB0, 3, True, 127),
    "pc":      Kind(0xC0, 2, True, 127),    # the number IS the payload -- see below
    "touch":   Kind(0xD0, 2, False, 127),   # channel pressure: one byte, no number
    "bend":    Kind(0xE0, 3, False, 16383),  # 14 bits, centre 8192
}

# Program Change is the odd one: it has a number and nothing else. It is declared
# `has_number` because a configuration may well want one specific program -- and
# when it says `*` instead, that same number arrives as the value. That is what
# lets a single line answer all 128 programs.

BY_STATUS = {k.status: name for name, k in KINDS.items()}


def decode(message: list[int]) -> tuple[str, int, int, int] | None:
    """Turn raw bytes into (kind, channel, number, value), or None.

    Returns None rather than raising: a truncated or exotic message during a
    show must not take the bridge down, and there is nothing useful to say
    about a running-status byte we did not ask for.
    """
    if len(message) < 2:
        return None
    status = message[0] & 0xF0
    name = BY_STATUS.get(status)
    if name is None:
        return None
    kind = KINDS[name]
    if len(message) < kind.length:
        return None
    channel = (message[0] & 0x0F) + 1
    if name == "pc":
        # No value byte: the program number is both the address and the payload.
        return name, channel, message[1], message[1]
    if name == "touch":
        return name, channel, 0, message[1]
    if name == "bend":
        # Little end first, seven bits each. Read as a single byte it looks like
        # the wheel jumps around the middle of its travel.
        return name, channel, 0, message[1] | (message[2] << 7)
    return name, channel, message[1], message[2]


def encode(name: str, channel: int, num: int, value: int) -> list[int]:
    """Turn (kind, channel, number, value) back into bytes."""
    kind = KINDS[name]
    head = kind.status | ((channel - 1) & 0x0F)
    if name == "pc":
        # A watch landing on `pc` sends the WATCHED VALUE as the program, which
        # is how a scene index reaches an amp modeller as a preset change.
        return [head, value & 0x7F]
    if name == "touch":
        return [head, value & 0x7F]
    if name == "bend":
        return [head, value & 0x7F, (value >> 7) & 0x7F]
    return [head, num & 0x7F, value & 0x7F]
