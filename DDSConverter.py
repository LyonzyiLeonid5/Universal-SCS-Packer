"""Convert legacy DDS files to DDS_HEADER_DXT10.

The conversion is lossless for supported formats. Most formats need only a
header rewrite. Legacy 24-bit RGB/BGR, for which DXGI has no direct format, is
expanded to 32-bit RGBA/BGRA with an opaque alpha channel. Formats which cannot
be mapped unambiguously are rejected instead of being silently mislabeled.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


DDS_MAGIC = b"DDS "
DDS_HEADER_SIZE = 124
DDS_PIXELFORMAT_SIZE = 32
DDPF_ALPHAPIXELS = 0x1
DDPF_ALPHA = 0x2
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDPF_LUMINANCE = 0x20000
DDPF_BUMPDUDV = 0x80000
DDSD_PITCH = 0x8
DDSD_LINEARSIZE = 0x80000
DDSCAPS2_CUBEMAP = 0x200
DDSCAPS2_VOLUME = 0x200000
D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3
D3D10_RESOURCE_MISC_TEXTURECUBE = 0x4


class DDSConversionError(ValueError):
    """The DDS is malformed or has no safe legacy-to-DXGI mapping."""


FOURCC_TO_DXGI = {
    b"DXT1": 71,   # BC1_UNORM
    b"DXT2": 74,   # BC2_UNORM (legacy premultiplied-alpha marker is discarded)
    b"DXT3": 74,   # BC2_UNORM
    b"DXT4": 77,   # BC3_UNORM (legacy premultiplied-alpha marker is discarded)
    b"DXT5": 77,   # BC3_UNORM
    b"ATI1": 80,   # BC4_UNORM
    b"BC4U": 80,
    b"BC4S": 81,   # BC4_SNORM
    b"ATI2": 83,   # BC5_UNORM
    b"BC5U": 83,
    b"BC5S": 84,   # BC5_SNORM
}

# Legacy D3DFORMAT values stored in the FourCC field.
D3DFORMAT_TO_DXGI = {
    36: 11,   # A16B16G16R16 -> R16G16B16A16_UNORM
    110: 10,  # Q16W16V16U16 -> R16G16B16A16_SNORM
    111: 54,  # R16F
    112: 34,  # G16R16F
    113: 10,  # A16B16G16R16F
    114: 41,  # R32F
    115: 16,  # G32R32F
    116: 2,   # A32B32G32R32F
}


def _mask_format(flags: int, bits: int, red: int, green: int,
                 blue: int, alpha: int) -> int | None:
    key = (bits, red, green, blue, alpha)
    if flags & DDPF_RGB:
        return {
            (32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000): 28,
            (32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0x00000000): 28,
            (32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000): 87,
            (32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0x00000000): 88,
            (16, 0x0000F800, 0x000007E0, 0x0000001F, 0x00000000): 85,
            (16, 0x00007C00, 0x000003E0, 0x0000001F, 0x00008000): 86,
            (16, 0x00000F00, 0x000000F0, 0x0000000F, 0x0000F000): 115,
        }.get(key)
    if flags & DDPF_LUMINANCE:
        return {
            (8, 0x000000FF, 0, 0, 0): 61,
            (16, 0x0000FFFF, 0, 0, 0): 56,
            (16, 0x000000FF, 0, 0, 0x0000FF00): 49,
        }.get(key)
    if flags & DDPF_ALPHA and bits == 8 and alpha == 0xFF:
        return 65
    if flags & DDPF_BUMPDUDV:
        return {
            (16, 0x000000FF, 0x0000FF00, 0, 0): 51,
            (32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000): 31,
        }.get(key)
    return None


def is_dx10(data: bytes | bytearray | memoryview) -> bool:
    raw = bytes(data)
    return (len(raw) >= 148 and raw[:4] == DDS_MAGIC
            and struct.unpack_from("<I", raw, 4)[0] == DDS_HEADER_SIZE
            and raw[84:88] == b"DX10")


def _convert_rgb24(raw: bytes, width: int, height: int, mip_count: int,
                   faces: int, rgb_order: bool) -> bytes:
    """Expand tightly packed or DWORD-row-aligned RGB24/BGR24 to 32 bpp."""
    source = raw[128:]
    layouts: list[tuple[int, int, int]] = []
    mip_width, mip_height = width, height
    for _ in range(mip_count):
        tight = mip_width * 3
        layouts.append((mip_width, mip_height, tight))
        mip_width, mip_height = max(1, mip_width // 2), max(1, mip_height // 2)
    tight_size = faces * sum(row * rows for _width, rows, row in layouts)
    aligned_size = faces * sum(((row + 3) & ~3) * rows
                               for _width, rows, row in layouts)
    if len(source) == tight_size:
        aligned = False
    elif len(source) == aligned_size:
        aligned = True
    else:
        raise DDSConversionError(
            f"RGB24 surface size {len(source)} does not match tight "
            f"({tight_size}) or DWORD-aligned ({aligned_size}) layout")

    output = bytearray()
    offset = 0
    for _face in range(faces):
        for mip_width, mip_height, tight_row in layouts:
            source_pitch = (tight_row + 3) & ~3 if aligned else tight_row
            for _row in range(mip_height):
                row = source[offset:offset + tight_row]
                offset += source_pitch
                for pixel in range(mip_width):
                    first, green, third = row[pixel * 3:pixel * 3 + 3]
                    if rgb_order:
                        output += bytes((first, green, third, 255))
                    else:
                        output += bytes((first, green, third, 255))
    return bytes(output)


def _repair_legacy_bc_mip_count(header: bytearray, surface_size: int,
                                width: int, height: int, faces: int,
                                dxgi_format: int) -> None:
    """Repair a wrong legacy mip count when the payload gives one exact answer."""
    block_bytes = (8 if dxgi_format in (70, 71, 72, 79, 80, 81)
                   else 16 if dxgi_format in (*range(73, 79), *range(82, 85),
                                              *range(94, 100)) else 0)
    if not block_bytes:
        return
    declared = struct.unpack_from("<I", header, 28)[0] or 1
    maximum = max(width, height).bit_length()

    def chain_size(levels: int) -> int:
        total = 0
        mip_width, mip_height = width, height
        for _ in range(levels):
            total += max(1, (mip_width + 3) // 4) * max(1, (mip_height + 3) // 4) * block_bytes
            mip_width, mip_height = max(1, mip_width // 2), max(1, mip_height // 2)
        return total * faces

    if chain_size(declared) == surface_size:
        return
    matches = [levels for levels in range(1, maximum + 1)
               if chain_size(levels) == surface_size]
    if len(matches) != 1:
        raise DDSConversionError(
            f"BC surface size {surface_size} does not match declared mip count "
            f"{declared} and cannot be inferred unambiguously")
    repaired = matches[0]
    struct.pack_into("<I", header, 28, repaired)
    flags = struct.unpack_from("<I", header, 8)[0]
    struct.pack_into("<I", header, 8, flags | 0x20000)  # DDSD_MIPMAPCOUNT


def convert_to_dx10(data: bytes | bytearray | memoryview) -> bytes:
    """Return a validated DX10 DDS, converting a supported legacy header."""
    raw = bytes(data)
    if len(raw) < 128 or raw[:4] != DDS_MAGIC:
        raise DDSConversionError("not a DDS file")
    if struct.unpack_from("<I", raw, 4)[0] != DDS_HEADER_SIZE:
        raise DDSConversionError("invalid DDS_HEADER size")
    if struct.unpack_from("<I", raw, 76)[0] != DDS_PIXELFORMAT_SIZE:
        raise DDSConversionError("invalid DDS_PIXELFORMAT size")
    height, width = struct.unpack_from("<II", raw, 12)
    if not width or not height:
        raise DDSConversionError("DDS width and height must be non-zero")

    pixel_flags = struct.unpack_from("<I", raw, 80)[0]
    fourcc = raw[84:88]
    if fourcc == b"DX10":
        if len(raw) < 148:
            raise DDSConversionError("truncated DDS_HEADER_DXT10")
        fmt, dimension, _misc, array_size, _misc2 = struct.unpack_from("<5I", raw, 128)
        if not fmt or dimension != D3D10_RESOURCE_DIMENSION_TEXTURE2D or array_size != 1:
            raise DDSConversionError(
                "only single 2D DX10 textures and cubemaps are supported")
        return raw

    if struct.unpack_from("<I", raw, 112)[0] & DDSCAPS2_VOLUME:
        raise DDSConversionError("legacy volume textures cannot be packed as HashFS v2 images")

    dxgi_format: int | None = None
    converted_surface: bytes | None = None
    if pixel_flags & DDPF_FOURCC:
        dxgi_format = FOURCC_TO_DXGI.get(fourcc)
        if dxgi_format is None:
            dxgi_format = D3DFORMAT_TO_DXGI.get(struct.unpack("<I", fourcc)[0])
    else:
        bits, red, green, blue, alpha = struct.unpack_from("<5I", raw, 88)
        dxgi_format = _mask_format(pixel_flags, bits, red, green, blue, alpha)
        if (dxgi_format is None and pixel_flags & DDPF_RGB and bits == 24
                and alpha == 0):
            if (red, green, blue) == (0x00FF0000, 0x0000FF00, 0x000000FF):
                dxgi_format = 87  # input bytes B,G,R -> B8G8R8A8_UNORM
                rgb_order = False
            elif (red, green, blue) == (0x000000FF, 0x0000FF00, 0x00FF0000):
                dxgi_format = 28  # input bytes R,G,B -> R8G8B8A8_UNORM
                rgb_order = True
            else:
                rgb_order = False
            if dxgi_format is not None:
                mip_count = struct.unpack_from("<I", raw, 28)[0] or 1
                caps2 = struct.unpack_from("<I", raw, 112)[0]
                faces = 6 if caps2 & DDSCAPS2_CUBEMAP else 1
                converted_surface = _convert_rgb24(
                    raw, width, height, mip_count, faces, rgb_order)
    if dxgi_format is None:
        printable = fourcc.rstrip(b"\0").decode("ascii", "replace")
        raise DDSConversionError(
            f"unsupported legacy DDS pixel format (flags=0x{pixel_flags:X}, "
            f"fourcc={printable!r})")

    header = bytearray(raw[:128])
    struct.pack_into("<II4s5I", header, 76, DDS_PIXELFORMAT_SIZE,
                     DDPF_FOURCC, b"DX10", 0, 0, 0, 0, 0)
    caps2 = struct.unpack_from("<I", header, 112)[0]
    misc = D3D10_RESOURCE_MISC_TEXTURECUBE if caps2 & DDSCAPS2_CUBEMAP else 0
    faces = 6 if misc else 1
    if converted_surface is None:
        _repair_legacy_bc_mip_count(
            header, len(raw) - 128, width, height, faces, dxgi_format)
    if converted_surface is not None:
        flags = struct.unpack_from("<I", header, 8)[0]
        struct.pack_into("<I", header, 8,
                         (flags | DDSD_PITCH) & ~DDSD_LINEARSIZE)
        struct.pack_into("<I", header, 20, width * 4)
    alpha_mode = 2 if fourcc in (b"DXT2", b"DXT4") else 0
    dx10 = struct.pack("<5I", dxgi_format,
                       D3D10_RESOURCE_DIMENSION_TEXTURE2D, misc, 1, alpha_mode)
    return bytes(header) + dx10 + (converted_surface
                                   if converted_surface is not None else raw[128:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a supported legacy DDS header to DDS DX10 losslessly.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        converted = convert_to_dx10(args.input.read_bytes())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(converted)
    except (OSError, DDSConversionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DDSConversionError", "convert_to_dx10", "is_dx10"]
