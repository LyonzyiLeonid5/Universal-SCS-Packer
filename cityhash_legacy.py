"""CityHash64 variant used by SCS HashFS v1 (the 2011 implementation).

Ported from the MIT-licensed scs_tools/src/cityhash.rs implementation.
"""

from __future__ import annotations

from struct import unpack_from

MASK = (1 << 64) - 1
K0 = 0xC3A5C85C97CB3127
K1 = 0xB492B66FBE98F273
K2 = 0x9AE16A3B2F90404F
K3 = 0xC949D7C7509E6557
KM = 0x9DDFEA08EB382D69


def _u(value: int) -> int:
    return value & MASK


def _r(value: int, bits: int) -> int:
    return _u((value >> bits) | (value << (64 - bits))) if bits else _u(value)


def _f64(data: bytes, at: int) -> int:
    return unpack_from("<Q", data, at)[0]


def _f32(data: bytes, at: int) -> int:
    return unpack_from("<I", data, at)[0]


def _mix(value: int) -> int:
    return value ^ (value >> 47)


def _h16(low: int, high: int) -> int:
    a = _u((low ^ high) * KM)
    a ^= a >> 47
    b = _u((high ^ a) * KM)
    b ^= b >> 47
    return _u(b * KM)


def _weak(data: bytes, at: int, a: int, b: int) -> tuple[int, int]:
    w, x, y, z = unpack_from("<QQQQ", data, at)
    a = _u(a + w)
    b = _r(_u(b + a + z), 21)
    c = a
    a = _u(a + x + y)
    b = _u(b + _r(a, 44))
    return _u(a + z), _u(b + c)


def cityhash64(data: bytes) -> int:
    """Return the SCS compatible 64 bit hash of raw path bytes."""
    length = len(data)
    if length <= 16:
        if length > 8:
            a, b = _f64(data, 0), _f64(data, length - 8)
            return _h16(a, _r(_u(b + length), length)) ^ b
        if length >= 4:
            a = _f32(data, 0)
            return _h16(length + (a << 3), _f32(data, length - 4))
        if length:
            a, b, c = data[0], data[length >> 1], data[-1]
            return _u(_mix(_u((a + (b << 8)) * K2) ^ _u((length + (c << 2)) * K3)) * K2)
        return K2
    if length <= 32:
        a = _u(_f64(data, 0) * K1)
        b = _f64(data, 8)
        c = _u(_f64(data, length - 8) * K2)
        d = _u(_f64(data, length - 16) * K0)
        return _h16(_u(_r(_u(a - b), 43) + _r(c, 30) + d),
                    _u(a + _r(b ^ K3, 20) - c + length))
    if length <= 64:
        z = _f64(data, 24)
        a = _u(_f64(data, 0) + _u((length + _f64(data, length - 16)) * K0))
        b, c = _r(_u(a + z), 52), _r(a, 37)
        a = _u(a + _f64(data, 8))
        c = _u(c + _r(a, 7))
        a = _u(a + _f64(data, 16))
        vf, vs = _u(a + z), _u(b + _r(a, 31) + c)
        a = _u(_f64(data, 16) + _f64(data, length - 32))
        z = _f64(data, length - 8)
        b, c = _r(_u(a + z), 52), _r(a, 37)
        a = _u(a + _f64(data, length - 24))
        c = _u(c + _r(a, 7))
        a = _u(a + _f64(data, length - 16))
        wf, ws = _u(a + z), _u(b + _r(a, 31) + c)
        r = _mix(_u(_u(vf + ws) * K2 + _u(wf + vs) * K0))
        return _u(_mix(_u(r * K0 + vs)) * K2)

    x = _f64(data, length - 40)
    y = _u(_f64(data, length - 16) + _f64(data, length - 56))
    z = _h16(_u(_f64(data, length - 48) + length), _f64(data, length - 24))
    v = _weak(data, length - 64, length, z)
    w = _weak(data, length - 32, _u(y + K1), x)
    x = _u(x * K1 + _f64(data, 0))
    remaining = (length - 1) & ~63
    at = 0
    while remaining:
        x = _u(_r(_u(x + y + v[0] + _f64(data, at + 8)), 37) * K1)
        y = _u(_r(_u(y + v[1] + _f64(data, at + 48)), 42) * K1)
        x ^= w[1]
        y = _u(y + v[0] + _f64(data, at + 40))
        z = _u(_r(_u(z + w[0]), 33) * K1)
        v = _weak(data, at, _u(v[1] * K1), _u(x + w[0]))
        w = _weak(data, at + 32, _u(z + w[1]), _u(y + _f64(data, at + 16)))
        x, z = z, x
        at += 64
        remaining -= 64
    return _h16(_u(_h16(v[0], w[0]) + _u(_mix(y) * K1) + z),
                _u(_h16(v[1], w[1]) + x))
