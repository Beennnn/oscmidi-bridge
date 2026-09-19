"""Codec OSC 1.0, sans dépendance.

Pourquoi ne pas prendre python-osc : la passerelle tourne en permanence sous
launchd, à côté d'un rig de concert. Chaque paquet installé est une chose qui
peut manquer après une mise à jour de Python ou une réinstallation de la machine.
OSC tient en quatre-vingts lignes ; on les écrit une fois.

Types couverts : i f s T F N b — ceux qu'AbletonOSC utilise réellement.
"""

from __future__ import annotations

import struct
from typing import Any


def _pad4(n: int) -> int:
    """Longueur alignée sur le multiple de 4 supérieur (règle de bourrage OSC)."""
    return (n + 3) & ~3


def _enc_str(s: str) -> bytes:
    raw = s.encode("utf-8")
    # une chaîne OSC se termine par AU MOINS un octet nul puis est complétée à un
    # multiple de 4 : "abc" fait 4 octets, "abcd" en fait 8.
    return raw + b"\0" * (_pad4(len(raw) + 1) - len(raw))


def encode(address: str, args: list[Any] | tuple[Any, ...] = ()) -> bytes:
    tags = ","
    corps = b""
    for a in args:
        if isinstance(a, bool):
            # T et F ne portent PAS de charge utile : le type EST la valeur.
            tags += "T" if a else "F"
        elif isinstance(a, int):
            tags += "i"
            corps += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            corps += struct.pack(">f", a)
        elif isinstance(a, str):
            tags += "s"
            corps += _enc_str(a)
        elif a is None:
            tags += "N"
        elif isinstance(a, (bytes, bytearray)):
            tags += "b"
            corps += struct.pack(">i", len(a)) + bytes(a) + b"\0" * (_pad4(len(a)) - len(a))
        else:
            raise TypeError(f"type OSC non pris en charge : {type(a).__name__}")
    return _enc_str(address) + _enc_str(tags) + corps


def _dec_str(buf: bytes, off: int) -> tuple[str, int]:
    fin = buf.index(b"\0", off)
    return buf[off:fin].decode("utf-8", "replace"), off + _pad4(fin - off + 1)


def decode(buf: bytes) -> tuple[str, list[Any]] | None:
    """Rend (adresse, arguments), ou None si le paquet est illisible.

    On rend None plutôt que de lever : un datagramme mal formé pendant un concert
    ne doit pas arrêter la passerelle.
    """
    try:
        if not buf or buf[0:1] != b"/":
            return None
        adresse, off = _dec_str(buf, 0)
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
                # type inconnu : on ignore de combien avancer, on rend ce qu'on a.
                return adresse, args
        return adresse, args
    except Exception:
        return None
