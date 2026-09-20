"""OSC 1.0 codec, no dependencies.

Why not python-osc: this bridge runs permanently under a service manager, next
to a live rig. Every installed package is one more thing that can go missing
after a Python upgrade or a machine rebuild. OSC fits in eighty lines; write
them once.

Types covered: i f s T F N b.
"""

from __future__ import annotations

import struct
from typing import Any


def _pad4(n: int) -> int:
    """Round up to the next multiple of 4 (OSC padding rule)."""
    return (n + 3) & ~3


def _enc_str(s: str) -> bytes:
    raw = s.encode("utf-8")
    # An OSC string ends with AT LEAST one null byte, then is padded to a multiple
    # of 4: "abc" takes 4 bytes, "abcd" takes 8.
    return raw + b"\0" * (_pad4(len(raw) + 1) - len(raw))


def encode(address: str, args: list[Any] | tuple[Any, ...] = ()) -> bytes:
    tags = ","
    body = b""
    for a in args:
        if isinstance(a, bool):
            # T and F carry NO payload: the type tag IS the value.
            tags += "T" if a else "F"
        elif isinstance(a, int):
            tags += "i"
            body += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            body += struct.pack(">f", a)
        elif isinstance(a, str):
            tags += "s"
            body += _enc_str(a)
        elif a is None:
            tags += "N"
        elif isinstance(a, (bytes, bytearray)):
            tags += "b"
            body += struct.pack(">i", len(a)) + bytes(a) + b"\0" * (_pad4(len(a)) - len(a))
        else:
            raise TypeError(f"unsupported OSC type: {type(a).__name__}")
    return _enc_str(address) + _enc_str(tags) + body


def _dec_str(buf: bytes, off: int) -> tuple[str, int]:
    fin = buf.index(b"\0", off)
    return buf[off:fin].decode("utf-8", "replace"), off + _pad4(fin - off + 1)


def decode(buf: bytes) -> tuple[str, list[Any]] | None:
    """Return (address, args), or None if the packet is unreadable.

    Returning None rather than raising is deliberate: a malformed datagram during
    a show must not take the bridge down.
    """
    try:
        if not buf or buf[0:1] != b"/":
            return None
        address, off = _dec_str(buf, 0)
        tags, off = _dec_str(buf, off)
        if not tags.startswith(","):
            return None
        args: list[Any] = []
        for t in tags[1:]:
            if t == "i":
                args.append(struct.unpack_from(">i", buf, off)[0]); off += 4
            elif t == "f":
                args.append(struct.unpack_from(">f", buf, off)[0]); off += 4
            elif t == "s":
                s, off = _dec_str(buf, off); args.append(s)
            elif t == "b":
                (n,) = struct.unpack_from(">i", buf, off); off += 4
                args.append(buf[off:off + n]); off += _pad4(n)
            elif t == "T":
                args.append(True)
            elif t == "F":
                args.append(False)
            elif t == "N":
                args.append(None)
            else:
                # Unknown type: we don't know how far to advance, so return what we have.
                return address, args
        return address, args
    except Exception:
        return None
