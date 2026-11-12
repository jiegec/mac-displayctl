"""CoreGraphics / SkyLight CGS mode enumeration and setters."""

from __future__ import annotations

import ctypes
import math
from ctypes import (
    POINTER,
    byref,
    c_bool,
    c_double,
    c_int32,
    c_uint32,
    c_void_p,
)
from dataclasses import dataclass

from . import corefoundation as cf

CG = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")

kCGConfigureForSession = 2
kCGSAllDisplays = 99

CG.CGMainDisplayID.restype = c_uint32
CG.CGDisplayIsOnline.argtypes = [c_uint32]
CG.CGDisplayIsOnline.restype = c_bool
CG.CGDisplayIsMain.argtypes = [c_uint32]
CG.CGDisplayIsMain.restype = c_bool
CG.CGDisplayIsBuiltin.argtypes = [c_uint32]
CG.CGDisplayIsBuiltin.restype = c_bool
CG.CGDisplayIsInMirrorSet.argtypes = [c_uint32]
CG.CGDisplayIsInMirrorSet.restype = c_bool
CG.CGDisplayPrimaryDisplay.argtypes = [c_uint32]
CG.CGDisplayPrimaryDisplay.restype = c_uint32
CG.CGDisplayVendorNumber.argtypes = [c_uint32]
CG.CGDisplayVendorNumber.restype = c_uint32
CG.CGDisplayModelNumber.argtypes = [c_uint32]
CG.CGDisplayModelNumber.restype = c_uint32
CG.CGDisplaySerialNumber.argtypes = [c_uint32]
CG.CGDisplaySerialNumber.restype = c_uint32
CG.CGDisplayRotation.argtypes = [c_uint32]
CG.CGDisplayRotation.restype = c_double
CG.CGDisplayPixelsWide.argtypes = [c_uint32]
CG.CGDisplayPixelsWide.restype = ctypes.c_size_t
CG.CGDisplayPixelsHigh.argtypes = [c_uint32]
CG.CGDisplayPixelsHigh.restype = ctypes.c_size_t

CG.CGBeginDisplayConfiguration.argtypes = [POINTER(c_void_p)]
CG.CGBeginDisplayConfiguration.restype = c_int32
CG.CGCompleteDisplayConfiguration.argtypes = [c_void_p, c_uint32]
CG.CGCompleteDisplayConfiguration.restype = c_int32
CG.CGConfigureDisplayMirrorOfDisplay.argtypes = [c_void_p, c_uint32, c_uint32]
CG.CGConfigureDisplayMirrorOfDisplay.restype = c_int32

CG.CGSGetDisplayList.argtypes = [c_int32, POINTER(c_uint32), POINTER(c_uint32)]
CG.CGSGetDisplayList.restype = c_int32
CG.CGSGetNumberOfDisplayModes.argtypes = [c_uint32, POINTER(c_uint32)]
CG.CGSGetNumberOfDisplayModes.restype = c_int32
CG.CGSGetDisplayModeDescriptionOfLength.argtypes = [
    c_uint32,
    c_uint32,
    c_void_p,
    c_uint32,
]
CG.CGSGetDisplayModeDescriptionOfLength.restype = c_int32
CG.CGSGetCurrentDisplayMode.argtypes = [c_uint32, POINTER(c_uint32)]
CG.CGSGetCurrentDisplayMode.restype = c_int32
CG.CGSConfigureDisplayMode.argtypes = [c_void_p, c_uint32, c_uint32]
CG.CGSConfigureDisplayMode.restype = c_int32

CG.CGDisplayCopyDisplayMode.argtypes = [c_uint32]
CG.CGDisplayCopyDisplayMode.restype = c_void_p
CG.CGDisplayModeCopyPixelEncoding.argtypes = [c_void_p]
CG.CGDisplayModeCopyPixelEncoding.restype = c_void_p
CG.CGDisplayModeGetWidth.argtypes = [c_void_p]
CG.CGDisplayModeGetWidth.restype = ctypes.c_size_t
CG.CGDisplayModeGetHeight.argtypes = [c_void_p]
CG.CGDisplayModeGetHeight.restype = ctypes.c_size_t
CG.CGDisplayModeGetPixelWidth.argtypes = [c_void_p]
CG.CGDisplayModeGetPixelWidth.restype = ctypes.c_size_t
CG.CGDisplayModeGetPixelHeight.argtypes = [c_void_p]
CG.CGDisplayModeGetPixelHeight.restype = ctypes.c_size_t
CG.CGDisplayModeGetRefreshRate.argtypes = [c_void_p]
CG.CGDisplayModeGetRefreshRate.restype = c_double
CG.CGDisplayModeGetIOFlags.argtypes = [c_void_p]
CG.CGDisplayModeGetIOFlags.restype = c_uint32
CG.CGDisplayModeGetIODisplayModeID.argtypes = [c_void_p]
CG.CGDisplayModeGetIODisplayModeID.restype = c_int32
CG.CGDisplayModeRelease.argtypes = [c_void_p]
CG.CGDisplayModeRelease.restype = None

MODE_DESC_SIZE = 0xDC

# Pixel-encoding string -> bits per pixel.
PIXEL_ENCODINGS: dict[str, int] = {
    "PPPPPPPP": 8,
    "-RRRRRGGGGGBBBBB": 16,
    "--------RRRRRRRRGGGGGGGGBBBBBBBB": 32,
    "--RRRRRRRRRRGGGGGGGGGGBBBBBBBBBB": 30,
    "-16R16G16B16": 64,
    "-16FR16FG16FB16": 48,
    "-32FR32FG32FB32": 96,
}


def parse_pixel_encoding(encoding: str | None) -> int:
    """Map a CGS/CG pixel-encoding string to bits per pixel."""
    if not encoding:
        return 0
    return PIXEL_ENCODINGS.get(encoding.strip(), 0)


def bits_to_color_mask(bpp: int) -> int:
    """Map bits per pixel to the CGS depth mask."""
    return {
        30: 0x040,
        48: 0x100,
        96: 0x200,
        64: 0x080,
    }.get(bpp, bpp)


def color_mask_slot(mask: int) -> int:
    """log2(mask) - 3, i.e. the sysModeID[] depth slot."""
    if mask <= 0:
        return -1
    return int(math.floor(math.log2(mask))) - 3


def depth_name(bpp: int) -> str:
    return {
        8: "8-bit",
        16: "16-bit",
        30: "30-bit (10-10-10)",
        32: "32-bit (8-8-8-8)",
        48: "48-bit (16F)",
        64: "64-bit (16-bit int)",
        96: "96-bit (32F)",
    }.get(bpp, f"{bpp}-bit" if bpp else "unknown")


@dataclass(slots=True)
class CGSMode:
    index: int
    mode_id: int
    flags: int
    width: int
    height: int
    pixel_encoding: str
    refresh_hz: float
    io_flags: int
    iodisplay_mode_id: int
    resolution: float

    @property
    def bpp(self) -> int:
        return parse_pixel_encoding(self.pixel_encoding)

    @property
    def color_mask(self) -> int:
        return bits_to_color_mask(self.bpp)

    @property
    def depth_slot(self) -> int:
        return color_mask_slot(self.color_mask)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "mode_id": self.mode_id,
            "width": self.width,
            "height": self.height,
            "refresh_hz": round(self.refresh_hz, 4),
            "pixel_encoding": self.pixel_encoding,
            "bits_per_pixel": self.bpp,
            "depth": depth_name(self.bpp),
            "color_mask": f"0x{self.color_mask:03x}",
            "depth_slot": self.depth_slot,
            "io_flags": f"0x{self.io_flags:08x}",
            "iodisplay_mode_id": self.iodisplay_mode_id,
            "resolution": self.resolution,
            "flags": f"0x{self.flags:08x}",
        }


def online_displays() -> list[int]:
    count = c_uint32(0)
    CG.CGSGetDisplayList(kCGSAllDisplays, None, byref(count))
    if count.value == 0:
        return []
    ids = (c_uint32 * count.value)()
    CG.CGSGetDisplayList(kCGSAllDisplays, ids, byref(count))
    return [int(ids[i]) for i in range(count.value) if CG.CGDisplayIsOnline(ids[i])]


def main_display() -> int:
    return int(CG.CGMainDisplayID())


def current_mode_id(display: int) -> int | None:
    mode = c_uint32(0)
    if CG.CGSGetCurrentDisplayMode(display, byref(mode)) != 0:
        return None
    return int(mode.value)


def mode_count(display: int) -> int:
    count = c_uint32(0)
    if CG.CGSGetNumberOfDisplayModes(display, byref(count)) != 0:
        return 0
    return int(count.value)


def _parse_mode(display: int, index: int) -> CGSMode | None:
    raw = (ctypes.c_ubyte * MODE_DESC_SIZE)()
    if (
        CG.CGSGetDisplayModeDescriptionOfLength(display, index, raw, MODE_DESC_SIZE)
        != 0
    ):
        return None
    data = bytes(raw)
    mode_id = int.from_bytes(data[0x00:0x04], "little")
    flags = int.from_bytes(data[0x04:0x08], "little")
    width = int.from_bytes(data[0x08:0x0C], "little")
    height = int.from_bytes(data[0x0C:0x10], "little")
    encoding_raw = data[0x30 : 0x30 + 64].split(b"\x00", 1)[0]
    encoding = encoding_raw.decode("ascii", "replace")
    refresh_fixed = int.from_bytes(data[0xBC:0xC0], "little")
    io_flags = int.from_bytes(data[0xC0:0xC4], "little")
    iodisplay_mode_id = int.from_bytes(data[0xC4:0xC8], "little", signed=True)
    resolution = ctypes.c_float.from_buffer_copy(data[0xD0:0xD4]).value
    return CGSMode(
        index=index,
        mode_id=mode_id,
        flags=flags,
        width=width,
        height=height,
        pixel_encoding=encoding,
        refresh_hz=refresh_fixed / 65536.0,
        io_flags=io_flags,
        iodisplay_mode_id=iodisplay_mode_id,
        resolution=float(resolution),
    )


def list_modes(display: int) -> list[CGSMode]:
    return [
        mode
        for index in range(mode_count(display))
        if (mode := _parse_mode(display, index)) is not None
    ]


def find_mode(display: int, mode_id: int) -> CGSMode | None:
    for mode in list_modes(display):
        if mode.mode_id == mode_id:
            return mode
    return None


def cg_display_mode_info(display: int) -> dict:
    """Read the currently active CGDisplayMode (public API)."""
    result: dict = {}
    mode = CG.CGDisplayCopyDisplayMode(display)
    if not mode:
        return result
    try:
        result["width"] = int(CG.CGDisplayModeGetWidth(mode))
        result["height"] = int(CG.CGDisplayModeGetHeight(mode))
        result["pixel_width"] = int(CG.CGDisplayModeGetPixelWidth(mode))
        result["pixel_height"] = int(CG.CGDisplayModeGetPixelHeight(mode))
        result["refresh_hz"] = float(CG.CGDisplayModeGetRefreshRate(mode))
        result["io_flags"] = int(CG.CGDisplayModeGetIOFlags(mode))
        result["iodisplay_mode_id"] = int(CG.CGDisplayModeGetIODisplayModeID(mode))
        encoding_ref = CG.CGDisplayModeCopyPixelEncoding(mode)
        try:
            encoding = cf.to_str(encoding_ref)
        finally:
            cf.release(encoding_ref)
        result["pixel_encoding"] = encoding or ""
        result["bits_per_pixel"] = parse_pixel_encoding(encoding)
    finally:
        CG.CGDisplayModeRelease(mode)
    return result


def display_flags(display: int) -> dict:
    return {
        "builtin": bool(CG.CGDisplayIsBuiltin(display)),
        "main": bool(CG.CGDisplayIsMain(display)),
        "vendor_id": int(CG.CGDisplayVendorNumber(display)),
        "product_id": int(CG.CGDisplayModelNumber(display)),
        "serial": int(CG.CGDisplaySerialNumber(display)),
        "rotation": float(CG.CGDisplayRotation(display)),
        "pixels_wide": int(CG.CGDisplayPixelsWide(display)),
        "pixels_high": int(CG.CGDisplayPixelsHigh(display)),
    }


def filter_modes(
    modes: list[CGSMode],
    *,
    width: int | None = None,
    height: int | None = None,
    refresh: float | None = None,
    bpp: int | None = None,
    pixel_encoding: str | None = None,
) -> list[CGSMode]:
    candidates = list(modes)
    if width is not None:
        candidates = [m for m in candidates if m.width == width]
    if height is not None:
        candidates = [m for m in candidates if m.height == height]
    if refresh is not None:
        candidates = [
            m
            for m in candidates
            if abs(m.refresh_hz - refresh) <= max(0.5, refresh * 0.01)
        ]
    if bpp is not None:
        candidates = [m for m in candidates if m.bpp == bpp]
    if pixel_encoding is not None:
        candidates = [m for m in candidates if m.pixel_encoding == pixel_encoding]
    return candidates


def select_mode(
    modes: list[CGSMode],
    *,
    width: int | None = None,
    height: int | None = None,
    refresh: float | None = None,
    bpp: int | None = None,
    pixel_encoding: str | None = None,
    prefer_bpp: int | None = None,
    prefer_mode_id: int | None = None,
) -> CGSMode | None:
    candidates = filter_modes(
        modes,
        width=width,
        height=height,
        refresh=refresh,
        bpp=bpp,
        pixel_encoding=pixel_encoding,
    )
    if not candidates:
        return None
    candidates.sort(
        key=lambda m: (
            m.mode_id == prefer_mode_id,
            m.bpp == prefer_bpp,
            m.bpp,
        ),
        reverse=True,
    )
    return candidates[0]


def set_mode(display: int, mode_id: int) -> None:
    """Apply a CGS mode ID inside a CoreGraphics configuration transaction."""
    config = c_void_p()
    if CG.CGBeginDisplayConfiguration(byref(config)) != 0:
        raise RuntimeError("CGBeginDisplayConfiguration failed")

    if CG.CGDisplayIsInMirrorSet(display):
        master = int(CG.CGDisplayPrimaryDisplay(display))
        if master != display:
            CG.CGConfigureDisplayMirrorOfDisplay(config, display, master)
            CG.CGCompleteDisplayConfiguration(config, kCGConfigureForSession)
            if CG.CGBeginDisplayConfiguration(byref(config)) != 0:
                raise RuntimeError(
                    "CGBeginDisplayConfiguration failed (mirror workaround)"
                )

    if CG.CGSConfigureDisplayMode(config, display, mode_id) != 0:
        CG.CGCompleteDisplayConfiguration(config, kCGConfigureForSession)
        raise RuntimeError(f"CGSConfigureDisplayMode failed for mode ID {mode_id}")
    if CG.CGCompleteDisplayConfiguration(config, kCGConfigureForSession) != 0:
        raise RuntimeError("CGCompleteDisplayConfiguration failed")
