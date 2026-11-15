"""Instant virtual EDID through IOAVService (Apple Silicon, macOS 13+).

Unlike the override plists in ``/Library/Displays``, this swaps the EDID the
display pipeline reports without root and without a restart.  The change is
not persistent: it is dropped when the display is unplugged, the machine
sleeps, or the process clears it again.
"""

from __future__ import annotations

import ctypes
import platform
from ctypes import c_uint32, c_void_p

from . import corefoundation as cf, iokit, timing

IOKIT = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

IOKIT.IOAVServiceCreateWithService.argtypes = [c_void_p, c_uint32]
IOKIT.IOAVServiceCreateWithService.restype = c_void_p
IOKIT.IOAVServiceSetVirtualEDIDMode.argtypes = [c_void_p, c_uint32, c_void_p]
IOKIT.IOAVServiceSetVirtualEDIDMode.restype = ctypes.c_int32

PROXY_CLASS = "DCPAVServiceProxy"


def unsupported_reason() -> str | None:
    """Why instant virtual EDID cannot be used here, or None if it can."""
    if platform.machine() != "arm64":
        return "instant virtual EDID is only available on Apple Silicon"
    if not hasattr(IOKIT, "IOAVServiceSetVirtualEDIDMode"):
        return "this macOS release does not export IOAVServiceSetVirtualEDIDMode"
    try:
        major = int(platform.mac_ver()[0].split(".", 1)[0])
    except (IndexError, ValueError):
        major = 0
    if major < 13:
        return "instant virtual EDID requires macOS 13 or newer"
    return None


def apply(display: int, data: bytes) -> None:
    """Show ``data`` to the display as its EDID."""
    _set(display, data)


def clear(display: int) -> None:
    """Drop a previously applied virtual EDID."""
    _set(display, None)


def _set(display: int, data: bytes | None) -> None:
    reason = unsupported_reason()
    if reason:
        raise RuntimeError(reason)
    service = _av_service(display)
    payload = 0
    try:
        payload = cf.cfdata(data) if data else 0
        result = IOKIT.IOAVServiceSetVirtualEDIDMode(
            service, 1 if data else 0, payload
        )
    finally:
        cf.release(payload)
        cf.release(service)
    if result != 0:
        raise RuntimeError(
            f"IOAVServiceSetVirtualEDIDMode failed (0x{result & 0xFFFFFFFF:08x})"
        )


def _av_service(display: int) -> int:
    pipeline = timing.pipeline(display)
    if not pipeline:
        raise RuntimeError(f"cannot locate the display pipeline for display {display}")
    prefix = f"{pipeline}:"
    for proxy in iokit.services(PROXY_CLASS):
        parent = iokit.parent_name(proxy)
        if not parent or not parent.startswith(prefix):
            continue
        service = IOKIT.IOAVServiceCreateWithService(None, proxy)
        if service:
            return service
    raise RuntimeError(f"no IOAVService found for display {display}")
