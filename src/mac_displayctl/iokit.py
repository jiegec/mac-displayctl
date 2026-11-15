"""Minimal IOKit bridge (ctypes only).

Only the handful of functions needed to enumerate display services and read
their registry properties are wrapped here.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_char_p, c_uint32, c_void_p
from typing import Iterator

from . import corefoundation as cf

IOKIT = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

kIOMainPortDefault = 0

IOKIT.IOServiceMatching.argtypes = [c_char_p]
IOKIT.IOServiceMatching.restype = c_void_p
IOKIT.IOServiceGetMatchingServices.argtypes = [c_uint32, c_void_p, POINTER(c_uint32)]
IOKIT.IOServiceGetMatchingServices.restype = ctypes.c_int
IOKIT.IOIteratorNext.argtypes = [c_uint32]
IOKIT.IOIteratorNext.restype = c_uint32
IOKIT.IOObjectRelease.argtypes = [c_uint32]
IOKIT.IOObjectRelease.restype = ctypes.c_int
IOKIT.IORegistryEntryCreateCFProperty.argtypes = [c_uint32, c_void_p, c_void_p, c_uint32]
IOKIT.IORegistryEntryCreateCFProperty.restype = c_void_p
IOKIT.IORegistryEntryGetParentEntry.argtypes = [
    c_uint32,
    c_char_p,
    POINTER(c_uint32),
]
IOKIT.IORegistryEntryGetParentEntry.restype = ctypes.c_int
IOKIT.IORegistryEntryGetName.argtypes = [c_uint32, c_char_p]
IOKIT.IORegistryEntryGetName.restype = ctypes.c_int
IOKIT.IODisplayCreateInfoDictionary.argtypes = [c_uint32, c_uint32]
IOKIT.IODisplayCreateInfoDictionary.restype = c_void_p


def services(class_name: str) -> Iterator[int]:
    """Yield the ``io_service_t`` of every published service of a class."""
    matching = IOKIT.IOServiceMatching(class_name.encode("ascii"))
    if not matching:
        return
    iterator = c_uint32(0)
    if IOKIT.IOServiceGetMatchingServices(
        kIOMainPortDefault, matching, byref(iterator)
    ) != 0:
        return
    try:
        while True:
            service = IOKIT.IOIteratorNext(iterator.value)
            if not service:
                break
            try:
                yield service
            finally:
                IOKIT.IOObjectRelease(service)
    finally:
        IOKIT.IOObjectRelease(iterator.value)


def property_of(service: int, key: str) -> int | None:
    """Copy a registry property; the caller owns the returned reference."""
    key_ref = cf.cfstr(key)
    try:
        return IOKIT.IORegistryEntryCreateCFProperty(service, key_ref, None, 0)
    finally:
        cf.release(key_ref)


def display_info(service: int) -> int | None:
    """``IODisplayCreateInfoDictionary`` for a display service."""
    return IOKIT.IODisplayCreateInfoDictionary(service, 0)


def entry_name(entry: int) -> str | None:
    """Registry node name, e.g. ``dispext0:dcpav-service-epic:0``."""
    buffer = ctypes.create_string_buffer(256)
    if IOKIT.IORegistryEntryGetName(entry, buffer) != 0:
        return None
    return buffer.value.decode("utf-8", "replace")


def parent_name(entry: int) -> str | None:
    """Name of the node's parent in the IOService plane."""
    parent = c_uint32(0)
    if IOKIT.IORegistryEntryGetParentEntry(entry, b"IOService", byref(parent)) != 0:
        return None
    if not parent.value:
        return None
    try:
        return entry_name(parent.value)
    finally:
        IOKIT.IOObjectRelease(parent.value)
