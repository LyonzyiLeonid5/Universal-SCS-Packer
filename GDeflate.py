"""Genuine GDeflate compression through the Microsoft DirectStorage codec.

The redistributable DirectStorage runtime is loaded explicitly because a
PyInstaller one-file executable extracts bundled DLLs beside this module, not
beside the outer executable. ``dstoragecore.dll`` must be loaded before
``dstorage.dll`` or codec creation can silently fail with E_NOTIMPL.

Each worker thread owns a codec instance. Microsoft documents compression
codec objects as non-thread-safe, while the packer prepares textures in
parallel.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path


class GDeflateError(ValueError):
    """The DirectStorage GDeflate codec is unavailable or compression failed."""


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


_CODEC_IID = _Guid(
    0x84EF5121,
    0x9B43,
    0x4D03,
    (ctypes.c_ubyte * 8)(0xB5, 0xC1, 0xCC, 0x34, 0x60, 0x6B, 0x26, 0x2D),
)
_GDEFLATE_FORMAT = 1
_SETTINGS = {"min": -1, "medium": 0, "max": 1}

_load_lock = threading.Lock()
_thread_local = threading.local()
_core_dll = None
_api_dll = None
_create_codec = None
_dll_directory_handles: list[object] = []


def _runtime_directories() -> list[Path]:
    roots: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(bundle_root))
    roots.append(Path(__file__).resolve().parent)
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)

    result: list[Path] = []
    for root in roots:
        for candidate in (root / "DirectStorage", root):
            if candidate not in result:
                result.append(candidate)
    return result


def _find_runtime() -> Path:
    checked: list[str] = []
    for directory in _runtime_directories():
        checked.append(str(directory))
        if ((directory / "dstorage.dll").is_file()
                and (directory / "dstoragecore.dll").is_file()):
            return directory
    raise GDeflateError(
        "Microsoft DirectStorage runtime is missing (dstorage.dll and "
        f"dstoragecore.dll); checked: {', '.join(checked)}")


def _load_runtime():
    global _core_dll, _api_dll, _create_codec
    if _create_codec is not None:
        return _create_codec
    if sys.platform != "win32":
        raise GDeflateError("GDeflate compression currently requires Windows")
    with _load_lock:
        if _create_codec is not None:
            return _create_codec
        directory = _find_runtime()
        if hasattr(os, "add_dll_directory"):
            _dll_directory_handles.append(os.add_dll_directory(str(directory)))
        try:
            # Loading the core first is required when running from Python or a
            # PyInstaller extraction directory.
            _core_dll = ctypes.WinDLL(str(directory / "dstoragecore.dll"))
            _api_dll = ctypes.WinDLL(str(directory / "dstorage.dll"))
            create = _api_dll.DStorageCreateCompressionCodec
        except (AttributeError, OSError) as exc:
            raise GDeflateError(
                f"Cannot load Microsoft DirectStorage runtime from {directory}: {exc}") from exc
        create.argtypes = [
            ctypes.c_uint8,
            ctypes.c_uint32,
            ctypes.POINTER(_Guid),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        create.restype = ctypes.c_long
        _create_codec = create
        return create


def _failed(hresult: int) -> bool:
    return hresult < 0


def _hresult(hresult: int) -> str:
    return f"0x{hresult & 0xFFFFFFFF:08X}"


class _Codec:
    def __init__(self) -> None:
        create = _load_runtime()
        pointer = ctypes.c_void_p()
        # One DirectStorage worker per codec: parallelism is provided by the
        # packer's ThreadPoolExecutor and codecs themselves are not thread-safe.
        result = create(_GDEFLATE_FORMAT, 1, ctypes.byref(_CODEC_IID),
                        ctypes.byref(pointer))
        if _failed(result) or not pointer.value:
            raise GDeflateError(
                f"DStorageCreateCompressionCodec failed ({_hresult(result)})")
        self.pointer = pointer
        vtable = ctypes.cast(
            pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        self._compress = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        )(vtable[3])
        self._bound = ctypes.WINFUNCTYPE(
            ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t)(vtable[5])
        self._release = ctypes.WINFUNCTYPE(
            ctypes.c_ulong, ctypes.c_void_p)(vtable[2])

    def close(self) -> None:
        if self.pointer.value:
            self._release(self.pointer)
            self.pointer = ctypes.c_void_p()

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, OSError):
            pass

    def compress(self, source: bytes, setting: int) -> bytes:
        capacity = self._bound(self.pointer, len(source))
        if capacity <= 0:
            raise GDeflateError("DirectStorage returned an invalid compression bound")
        input_buffer = ctypes.create_string_buffer(source, len(source))
        output_buffer = ctypes.create_string_buffer(capacity)
        output_size = ctypes.c_size_t()
        result = self._compress(
            self.pointer,
            input_buffer,
            len(source),
            setting,
            output_buffer,
            capacity,
            ctypes.byref(output_size),
        )
        if _failed(result):
            raise GDeflateError(
                f"DirectStorage GDeflate compression failed ({_hresult(result)})")
        if output_size.value > capacity:
            raise GDeflateError("DirectStorage returned an invalid compressed size")
        return output_buffer.raw[:output_size.value]


def _thread_codec() -> _Codec:
    codec = getattr(_thread_local, "codec", None)
    if codec is None:
        codec = _Codec()
        _thread_local.codec = codec
    return codec


def compress(data: bytes | bytearray | memoryview, level: str = "max") -> bytes:
    """Compress *data* as a genuine GDeflate stream.

    ``level`` maps the packer's ``min``, ``medium`` and ``max`` vocabulary to
    DirectStorage's FASTEST, DEFAULT and BEST_RATIO settings. ``no`` returns
    the original surface without loading DirectStorage so the caller can emit
    an uncompressed HashFS v2 texture record.
    """
    source = bytes(data)
    if level == "no":
        return source
    try:
        setting = _SETTINGS[level]
    except KeyError as exc:
        raise GDeflateError(f"unsupported GDeflate compression level: {level}") from exc
    if not source:
        return b""
    return _thread_codec().compress(source, setting)


__all__ = ["GDeflateError", "compress"]
