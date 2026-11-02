"""Decode raw Modbus register words into Python values.

Kept independent of any Modbus client library: the words come from a
``modbus_connection`` unit read (``list[int]``) and are turned into signed /
unsigned integers or the Conext packed-ASCII strings.
"""

from __future__ import annotations


def decode(words: list[int], data_type: str, word_order: str) -> int:
    """Decode raw 16-bit words into a signed/unsigned integer."""
    if data_type == "uint16":
        return words[0]
    if data_type == "int16":
        v = words[0]
        return v - 0x10000 if v >= 0x8000 else v

    hi, lo = words[0], words[1]
    if word_order == "little":
        hi, lo = lo, hi
    raw = (hi << 16) | lo
    if data_type == "int32" and raw >= 0x80000000:
        raw -= 0x100000000
    return raw


def decode_string(words: list[int]) -> str:
    """Decode a Conext packed string: two 8-bit chars per register, hi byte first."""
    chars = bytearray()
    for word in words:
        chars.append((word >> 8) & 0xFF)
        chars.append(word & 0xFF)
    # Strings are NUL-padded; cut at the first NUL and drop non-printables.
    text = bytes(chars).split(b"\x00", 1)[0]
    return text.decode("ascii", "ignore").strip()
