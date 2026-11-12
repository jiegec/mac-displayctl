"""QuartzCore ``CADisplay`` / ``CADisplayMode`` bridge.

A CGS mode controls resolution and refresh; a CADisplayMode controls
RGB/YCbCr, subsampling, bit depth, range and HDR.
"""

from __future__ import annotations

import time
from ctypes import c_bool, c_double, c_uint32, c_uint64
from dataclasses import dataclass

from . import corefoundation as cf, objc_bridge as objc

CADisplay = objc.get_class("CADisplay")


def displays() -> list[int]:
    arr = objc.send_class(CADisplay, "displays")
    return objc.array_items(arr)


def display_object_for(display_id: int) -> int | None:
    for display in displays():
        if int(objc.send(display, "displayId", restype=c_uint32)) == display_id:
            return display
    return None


def _str(obj: int, selector: str) -> str | None:
    if not obj or not objc.responds(obj, selector):
        return None
    try:
        return cf.to_str(objc.send(obj, selector))
    except objc.ObjCError:
        return None


def _uint(obj: int, selector: str, default: int = 0) -> int:
    if not obj or not objc.responds(obj, selector):
        return default
    try:
        return int(objc.send(obj, selector, restype=c_uint64))
    except objc.ObjCError:
        return default


def _double(obj: int, selector: str, default: float = 0.0) -> float:
    if not obj or not objc.responds(obj, selector):
        return default
    try:
        return float(objc.send(obj, selector, restype=c_double))
    except objc.ObjCError:
        return default


def _bool(obj: int, selector: str, default: bool = False) -> bool:
    if not obj or not objc.responds(obj, selector):
        return default
    try:
        return bool(objc.send(obj, selector, restype=c_bool))
    except objc.ObjCError:
        return default


# Exact colorMode -> (subsampling, range).  Unknown values stay unknown rather
# than being inferred from free-text descriptions.
COLOR_MODE_LAYOUT: dict[str, tuple[str, str]] = {
    "auto": ("444", "unknown"),
    "RGBFullRange": ("444", "full"),
    "RGBLimitedRange": ("444", "limited"),
    "YCbCr444LimitedRange": ("444", "limited"),
    "YCbCr422LimitedRange": ("422", "limited"),
    "YCbCr420LimitedRange": ("420", "limited"),
    "HDR10_RGBFullRange": ("444", "full"),
    "HDR10_RGBLimitedRange": ("444", "limited"),
    "HDR10_422LimitedRange": ("422", "limited"),
    "HDR10_420LimitedRange": ("420", "limited"),
}


@dataclass(slots=True)
class CAMode:
    index: int
    obj: int
    width: int
    height: int
    refresh_hz: float
    bit_depth: int
    color_mode: str
    hdr_mode: str
    color_gamut: str
    is_ycbcr: bool
    description: str
    is_virtual: bool = False

    @property
    def subsampling(self) -> str:
        return COLOR_MODE_LAYOUT.get(self.color_mode, ("unknown", "unknown"))[0]

    @property
    def range(self) -> str:
        return COLOR_MODE_LAYOUT.get(self.color_mode, ("unknown", "unknown"))[1]

    @property
    def pixel_format(self) -> str:
        return "YCbCr" if self.is_ycbcr else "RGB"

    @property
    def format_label(self) -> str:
        bits = f"{self.bit_depth}-bit" if self.bit_depth else "?-bit"
        sub = {"444": "4:4:4", "422": "4:2:2", "420": "4:2:0"}.get(
            self.subsampling, "?"
        )
        rng = {"full": "Full", "limited": "Limited"}.get(self.range, "?")
        return f"{bits} {self.pixel_format} {sub} {rng} Range {self.hdr_mode or '?'}"

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "width": self.width,
            "height": self.height,
            "refresh_hz": round(self.refresh_hz, 4),
            "bit_depth": self.bit_depth,
            "pixel_format": self.pixel_format,
            "subsampling": self.subsampling,
            "range": self.range,
            "hdr_mode": self.hdr_mode,
            "gamut": self.color_gamut,
            "color_mode": self.color_mode,
            "description": self.description,
            "is_virtual": self.is_virtual,
            "label": self.format_label,
        }

    def is_same(self, other: "CAMode") -> bool:
        if not self.obj or not other.obj:
            return False
        try:
            return bool(objc.send(self.obj, "isEqual:", other.obj, restype=c_bool))
        except objc.ObjCError:
            return (
                self.description == other.description
                and abs(self.refresh_hz - other.refresh_hz) < 0.01
            )


def _read_mode(obj: int, index: int) -> CAMode:
    return CAMode(
        index=index,
        obj=obj,
        width=_uint(obj, "width"),
        height=_uint(obj, "height"),
        refresh_hz=_double(obj, "refreshRate"),
        bit_depth=_uint(obj, "bitDepth"),
        color_mode=_str(obj, "colorMode") or "",
        hdr_mode=_str(obj, "hdrMode") or "",
        color_gamut=_str(obj, "colorGamut") or "",
        is_ycbcr=_bool(obj, "colorModeIsYCbCr"),
        description=objc.description(obj) or "",
        is_virtual=_bool(obj, "isVirtual"),
    )


def available_modes(display_obj: int) -> list[CAMode]:
    arr = objc.send(display_obj, "availableModes")
    result: list[CAMode] = []
    for index, obj in enumerate(objc.array_items(arr)):
        mode = _read_mode(obj, index)
        if mode.width > 0 and mode.height > 0:
            result.append(mode)
    return result


def current_mode(display_obj: int) -> CAMode | None:
    obj = objc.send(display_obj, "currentMode")
    if not obj:
        return None
    return _read_mode(obj, -1)


def index_of(modes: list[CAMode], target: CAMode | None) -> int:
    if target is None:
        return -1
    for mode in modes:
        if mode.is_same(target):
            return mode.index
    return target.index


def set_mode(display_obj: int, mode: CAMode | int) -> None:
    obj = mode.obj if isinstance(mode, CAMode) else int(mode)
    if not obj:
        raise ValueError("invalid CADisplayMode")
    # Calling -update afterwards makes WindowServer re-evaluate and can replace
    # the requested mode with the display's preferred mode.
    objc.send(display_obj, "setCurrentMode:", obj)
    time.sleep(0.05)


def display_info(display_obj: int) -> dict:
    info = {
        "name": _str(display_obj, "name"),
        "product_name": _str(display_obj, "productName"),
        "device_name": _str(display_obj, "deviceName"),
        "transport": _str(display_obj, "transportType"),
        "unique_id": _str(display_obj, "uniqueId"),
        "uuid": objc.description(objc.send(display_obj, "UUID"))
        if objc.responds(display_obj, "UUID")
        else None,
        "is_external": _bool(display_obj, "isExternal"),
        "is_reference": _bool(display_obj, "isReference"),
        "supports_extended_colors": _bool(display_obj, "supportsExtendedColors"),
        "allows_virtual_modes": _bool(display_obj, "allowsVirtualModes"),
        "point_scale": _uint(display_obj, "pointScale", 1),
        "display_type": _uint(display_obj, "displayType", 0),
    }
    info["title"] = info["product_name"] or info["name"] or "Display"
    return info


def select_modes(
    modes: list[CAMode],
    *,
    width: int | None = None,
    height: int | None = None,
    refresh: float | None = None,
    bit_depth: int | None = None,
    pixel_format: str | None = None,
    subsampling: str | None = None,
    range_: str | None = None,
    hdr: str | None = None,
    gamut: str | None = None,
    color_mode: str | None = None,
) -> list[CAMode]:
    candidates = list(modes)
    if width is not None:
        candidates = [m for m in candidates if m.width == width]
    if height is not None:
        candidates = [m for m in candidates if m.height == height]
    if refresh is not None:
        candidates = [
            m
            for m in candidates
            if abs(m.refresh_hz - refresh) <= max(0.6, refresh * 0.01)
        ]
    if bit_depth is not None:
        candidates = [m for m in candidates if m.bit_depth == bit_depth]
    if pixel_format:
        pf = pixel_format.casefold()
        if pf == "rgb":
            candidates = [m for m in candidates if not m.is_ycbcr]
        elif pf == "ycbcr":
            candidates = [m for m in candidates if m.is_ycbcr]
        else:
            raise ValueError(f"unknown pixel format: {pixel_format!r}")
    if subsampling:
        candidates = [m for m in candidates if m.subsampling == subsampling]
    if range_:
        candidates = [m for m in candidates if m.range == range_.casefold()]
    if hdr:
        candidates = [m for m in candidates if _match_hdr(m, hdr)]
    if gamut:
        g = gamut.casefold()
        candidates = [m for m in candidates if m.color_gamut.casefold() == g]
    if color_mode:
        c = color_mode.casefold()
        candidates = [m for m in candidates if m.color_mode.casefold() == c]
    candidates.sort(key=lambda m: (m.is_virtual, m.index))
    return candidates


def is_hdr(mode: CAMode) -> bool:
    """True when hdrMode is anything other than SDR."""
    return mode.hdr_mode.strip().casefold() not in ("", "sdr")


def _match_hdr(mode: CAMode, wanted: str) -> bool:
    """Exact, case-insensitive match against hdrMode."""
    return mode.hdr_mode.strip().casefold() == wanted.strip().casefold()
