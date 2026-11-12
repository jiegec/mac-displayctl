"""Minimal CoreFoundation bridge (ctypes only).

Everything in this module returns raw ``CFTypeRef`` pointers (``int``/``None``).
Callers are responsible for calling :func:`release` on objects they own.
"""

from __future__ import annotations

import ctypes
from ctypes import (
    POINTER,
    byref,
    c_bool,
    c_char_p,
    c_double,
    c_int32,
    c_int64,
    c_long,
    c_uint8,
    c_uint32,
    c_ulong,
    c_void_p,
)
from pathlib import Path

CF = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

kCFStringEncodingUTF8 = 0x08000100
kCFNumberSInt64Type = 4
kCFNumberFloat64Type = 6
kCFURLPOSIXPathStyle = 0


class CFDictionaryKeyCallBacks(ctypes.Structure):
    _fields_ = [
        ("version", c_long),
        ("retain", c_void_p),
        ("release", c_void_p),
        ("copyDescription", c_void_p),
        ("equal", c_void_p),
        ("hash", c_void_p),
    ]


class CFDictionaryValueCallBacks(ctypes.Structure):
    _fields_ = [
        ("version", c_long),
        ("retain", c_void_p),
        ("release", c_void_p),
        ("copyDescription", c_void_p),
        ("equal", c_void_p),
    ]


CF.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
CF.CFStringCreateWithCString.restype = c_void_p
CF.CFStringGetLength.argtypes = [c_void_p]
CF.CFStringGetLength.restype = c_long
CF.CFStringGetMaximumSizeForEncoding.argtypes = [c_long, c_uint32]
CF.CFStringGetMaximumSizeForEncoding.restype = c_long
CF.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]
CF.CFStringGetCString.restype = c_bool
CF.CFStringGetTypeID.restype = c_ulong
CF.CFNumberGetTypeID.restype = c_ulong
CF.CFBooleanGetTypeID.restype = c_ulong
CF.CFGetTypeID.argtypes = [c_void_p]
CF.CFGetTypeID.restype = c_ulong
CF.CFRelease.argtypes = [c_void_p]
CF.CFRelease.restype = None

CF.CFNumberGetValue.argtypes = [c_void_p, c_int32, c_void_p]
CF.CFNumberGetValue.restype = c_bool

CF.CFDataGetLength.argtypes = [c_void_p]
CF.CFDataGetLength.restype = c_long
CF.CFDataGetBytePtr.argtypes = [c_void_p]
CF.CFDataGetBytePtr.restype = POINTER(c_uint8)

CF.CFArrayGetCount.argtypes = [c_void_p]
CF.CFArrayGetCount.restype = c_long
CF.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
CF.CFArrayGetValueAtIndex.restype = c_void_p

CF.CFDictionaryCreate.argtypes = [
    c_void_p,
    POINTER(c_void_p),
    POINTER(c_void_p),
    c_long,
    POINTER(CFDictionaryKeyCallBacks),
    POINTER(CFDictionaryValueCallBacks),
]
CF.CFDictionaryCreate.restype = c_void_p
CF.CFDictionaryGetValue.argtypes = [c_void_p, c_void_p]
CF.CFDictionaryGetValue.restype = c_void_p

CF.CFURLCreateFromFileSystemRepresentation.argtypes = [
    c_void_p,
    c_char_p,
    c_long,
    c_bool,
]
CF.CFURLCreateFromFileSystemRepresentation.restype = c_void_p
CF.CFURLCopyFileSystemPath.argtypes = [c_void_p, c_int32]
CF.CFURLCopyFileSystemPath.restype = c_void_p
CF.CFURLGetFileSystemRepresentation.argtypes = [c_void_p, c_bool, c_char_p, c_long]
CF.CFURLGetFileSystemRepresentation.restype = c_bool

CF.CFUUIDCreateString.argtypes = [c_void_p, c_void_p]
CF.CFUUIDCreateString.restype = c_void_p

CF.CFBooleanGetValue.argtypes = [c_void_p]
CF.CFBooleanGetValue.restype = c_bool

CF.kCFBooleanTrue = c_void_p.in_dll(CF, "kCFBooleanTrue").value
CF.kCFBooleanFalse = c_void_p.in_dll(CF, "kCFBooleanFalse").value
kCFTypeDictionaryKeyCallBacks = CFDictionaryKeyCallBacks.in_dll(
    CF, "kCFTypeDictionaryKeyCallBacks"
)
kCFTypeDictionaryValueCallBacks = CFDictionaryValueCallBacks.in_dll(
    CF, "kCFTypeDictionaryValueCallBacks"
)


def release(ref: int | None) -> None:
    if ref:
        CF.CFRelease(ref)


def cfstr(value: str) -> int:
    ref = CF.CFStringCreateWithCString(
        None, value.encode("utf-8"), kCFStringEncodingUTF8
    )
    if not ref:
        raise MemoryError("CFStringCreateWithCString failed")
    return ref


def cfbool(value: bool) -> int:
    return int(CF.kCFBooleanTrue if value else CF.kCFBooleanFalse)


def cfdict(pairs: list[tuple[int, int]] | tuple[tuple[int, int], ...]) -> int:
    n = len(pairs)
    keys = (c_void_p * n)(*[k for k, _ in pairs])
    vals = (c_void_p * n)(*[v for _, v in pairs])
    ref = CF.CFDictionaryCreate(
        None,
        keys,
        vals,
        n,
        byref(kCFTypeDictionaryKeyCallBacks),
        byref(kCFTypeDictionaryValueCallBacks),
    )
    if not ref:
        raise MemoryError("CFDictionaryCreate failed")
    return ref


def cfurl_from_path(path: str | Path) -> int:
    data = str(path).encode("utf-8")
    ref = CF.CFURLCreateFromFileSystemRepresentation(None, data, len(data), False)
    if not ref:
        raise OSError(f"cannot create CFURL for {path}")
    return ref


def cfurl_to_path(url: int | None) -> str | None:
    if not url:
        return None
    path_ref = CF.CFURLCopyFileSystemPath(url, kCFURLPOSIXPathStyle)
    if not path_ref:
        # Fall back to CFURLGetFileSystemRepresentation.
        buf = ctypes.create_string_buffer(4096)
        if CF.CFURLGetFileSystemRepresentation(url, True, buf, 4096):
            return buf.value.decode("utf-8", "replace")
        return None
    try:
        return to_str(path_ref)
    finally:
        release(path_ref)


def uuid_to_str(uuid_ref: int | None) -> str | None:
    if not uuid_ref:
        return None
    string_ref = CF.CFUUIDCreateString(None, uuid_ref)
    if not string_ref:
        return None
    try:
        return to_str(string_ref)
    finally:
        release(string_ref)


def to_str(ref: int | None) -> str | None:
    if not ref:
        return None
    if CF.CFGetTypeID(ref) != CF.CFStringGetTypeID():
        return None
    length = CF.CFStringGetLength(ref)
    if length < 0:
        return None
    size = CF.CFStringGetMaximumSizeForEncoding(length, kCFStringEncodingUTF8) + 1
    buf = ctypes.create_string_buffer(max(size, 2))
    if not CF.CFStringGetCString(ref, buf, size, kCFStringEncodingUTF8):
        return None
    return buf.value.decode("utf-8", "replace")


def to_int(ref: int | None, default: int = 0) -> int:
    if not ref:
        return default
    type_id = CF.CFGetTypeID(ref)
    if type_id == CF.CFBooleanGetTypeID():
        return int(CF.CFBooleanGetValue(ref))
    if type_id != CF.CFNumberGetTypeID():
        return default
    raw = c_int64(0)
    if CF.CFNumberGetValue(ref, kCFNumberSInt64Type, byref(raw)):
        return int(raw.value)
    rawf = c_double(0.0)
    if CF.CFNumberGetValue(ref, kCFNumberFloat64Type, byref(rawf)):
        return int(rawf.value)
    return default


def to_bool(ref: int | None, default: bool = False) -> bool:
    if not ref:
        return default
    if CF.CFGetTypeID(ref) == CF.CFBooleanGetTypeID():
        return bool(CF.CFBooleanGetValue(ref))
    return bool(to_int(ref, int(default)))


def data_bytes(ref: int | None) -> bytes | None:
    if not ref:
        return None
    n = CF.CFDataGetLength(ref)
    if n <= 0:
        return b""
    ptr = CF.CFDataGetBytePtr(ref)
    return bytes(ptr[:n])


def dict_get(ref: int | None, key: int | str) -> int | None:
    if not ref:
        return None
    key_ref = key if isinstance(key, int) else cfstr(key)
    try:
        return CF.CFDictionaryGetValue(ref, key_ref)
    finally:
        if not isinstance(key, int):
            release(key_ref)


def array_count(ref: int | None) -> int:
    if not ref:
        return 0
    return max(0, int(CF.CFArrayGetCount(ref)))


def array_get(ref: int | None, index: int) -> int | None:
    if not ref:
        return None
    if index < 0 or index >= array_count(ref):
        return None
    return CF.CFArrayGetValueAtIndex(ref, index)
