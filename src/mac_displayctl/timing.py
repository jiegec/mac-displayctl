"""Read display timing elements from the IOMobileFramebuffer service."""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_char_p, c_uint32, c_void_p
from dataclasses import dataclass, field

from . import cadisplay as ca, cg as cgs, corefoundation as cf

IOKIT = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

IOKIT.IOServiceMatching.argtypes = [c_char_p]
IOKIT.IOServiceMatching.restype = c_void_p
IOKIT.IOServiceGetMatchingServices.argtypes = [c_uint32, c_void_p, POINTER(c_uint32)]
IOKIT.IOServiceGetMatchingServices.restype = ctypes.c_int
IOKIT.IOIteratorNext.argtypes = [c_uint32]
IOKIT.IOIteratorNext.restype = c_uint32
IOKIT.IOObjectRelease.argtypes = [c_uint32]
IOKIT.IOObjectRelease.restype = ctypes.c_int
IOKIT.IORegistryEntryCreateCFProperty.argtypes = [
    c_uint32,
    c_void_p,
    c_void_p,
    c_uint32,
]
IOKIT.IORegistryEntryCreateCFProperty.restype = c_void_p

CLASS_NAMES = ("IOMobileFramebufferShim", "IOFramebuffer")


def _property(service: int, key: str) -> int | None:
    key_ref = cf.cfstr(key)
    try:
        return IOKIT.IORegistryEntryCreateCFProperty(service, key_ref, None, 0)
    finally:
        cf.release(key_ref)


def _services():
    for class_name in CLASS_NAMES:
        matching = IOKIT.IOServiceMatching(class_name.encode("ascii"))
        if not matching:
            continue
        iterator = c_uint32(0)
        if IOKIT.IOServiceGetMatchingServices(0, matching, byref(iterator)) != 0:
            continue
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


def _attr(dictionary: int | None, key: str) -> int:
    return cf.to_int(cf.dict_get(dictionary, key))


@dataclass(slots=True)
class Timing:
    id: int
    h_active: int
    h_total: int
    h_front: int
    h_sync: int
    h_back: int
    h_polarity: int
    v_active: int
    v_total: int
    v_front: int
    v_sync: int
    v_back: int
    v_polarity: int
    refresh_hz: float
    is_preferred: bool = False
    is_interlaced: bool = False
    is_virtual: bool = False
    timing_type: int = 0
    aspect_ratio: int = 0

    @property
    def h_blank(self) -> int:
        return self.h_total - self.h_active

    @property
    def v_blank(self) -> int:
        return self.v_total - self.v_active

    @property
    def pixel_clock_hz(self) -> float:
        return float(self.h_total * self.v_total) * self.refresh_hz

    @property
    def h_polarity_text(self) -> str:
        return "+" if self.h_polarity else "-"

    @property
    def v_polarity_text(self) -> str:
        return "+" if self.v_polarity else "-"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "active": [self.h_active, self.v_active],
            "total": [self.h_total, self.v_total],
            "blank": [self.h_blank, self.v_blank],
            "front_porch": [self.h_front, self.v_front],
            "sync_width": [self.h_sync, self.v_sync],
            "back_porch": [self.h_back, self.v_back],
            "sync_polarity": [self.h_polarity_text, self.v_polarity_text],
            "refresh_hz": round(self.refresh_hz, 4),
            "pixel_clock_mhz": round(self.pixel_clock_hz / 1e6, 4),
            "preferred": self.is_preferred,
            "interlaced": self.is_interlaced,
            "virtual": self.is_virtual,
            "timing_type": self.timing_type,
            "aspect_ratio": self.aspect_ratio,
        }


@dataclass(slots=True)
class Framebuffer:
    vendor: int
    product: int
    name: str | None
    native_width: int
    native_height: int
    pixel_clock_hz: int | None
    timings: list[Timing] = field(default_factory=list)
    score: int = 0


def _read_timing(dictionary: int) -> Timing | None:
    if not dictionary:
        return None
    h = cf.dict_get(dictionary, "HorizontalAttributes")
    v = cf.dict_get(dictionary, "VerticalAttributes")
    if not h or not v:
        return None

    h_active = _attr(h, "Active")
    h_total = _attr(h, "Total")
    v_active = _attr(v, "Active")
    v_total = _attr(v, "Total")
    if not h_active or not h_total or not v_active or not v_total:
        return None

    v_rate = _attr(v, "SyncRate")
    refresh = v_rate / 65536.0 if v_rate else 0.0
    return Timing(
        id=cf.to_int(cf.dict_get(dictionary, "ID")),
        h_active=h_active,
        h_total=h_total,
        h_front=_attr(h, "FrontPorch"),
        h_sync=_attr(h, "SyncWidth"),
        h_back=_attr(h, "BackPorch"),
        h_polarity=_attr(h, "SyncPolarity"),
        v_active=v_active,
        v_total=v_total,
        v_front=_attr(v, "FrontPorch"),
        v_sync=_attr(v, "SyncWidth"),
        v_back=_attr(v, "BackPorch"),
        v_polarity=_attr(v, "SyncPolarity"),
        refresh_hz=refresh,
        is_preferred=cf.to_bool(cf.dict_get(dictionary, "IsPreferred")),
        is_interlaced=cf.to_bool(cf.dict_get(dictionary, "IsInterlaced")),
        is_virtual=cf.to_bool(cf.dict_get(dictionary, "IsVirtual")),
        timing_type=cf.to_int(cf.dict_get(dictionary, "TimingType")),
        aspect_ratio=cf.to_int(cf.dict_get(dictionary, "AspectRatio")),
    )


def _read_timings(service: int) -> list[Timing]:
    array = _property(service, "TimingElements")
    if not array:
        return []
    try:
        timings = []
        for index in range(cf.array_count(array)):
            timing = _read_timing(cf.array_get(array, index))
            if timing:
                timings.append(timing)
        return timings
    finally:
        cf.release(array)


def _read_framebuffer(service: int) -> Framebuffer:
    attrs = _property(service, "DisplayAttributes")
    vendor = product = native_w = native_h = 0
    name = None
    if attrs:
        try:
            products = cf.dict_get(attrs, "ProductAttributes")
            vendor = _attr(products, "LegacyManufacturerID")
            product = _attr(products, "ProductID")
            name = cf.to_str(cf.dict_get(products, "ProductName"))
            native_w = _attr(attrs, "NativeFormatHorizontalPixels")
            native_h = _attr(attrs, "NativeFormatVerticalPixels")
        finally:
            cf.release(attrs)

    clock = _property(service, "PixelClock")
    pixel_clock = cf.to_int(clock) if clock else None
    if clock:
        cf.release(clock)

    return Framebuffer(
        vendor=vendor,
        product=product,
        name=name,
        native_width=native_w,
        native_height=native_h,
        pixel_clock_hz=pixel_clock,
        timings=_read_timings(service),
    )


def _display_identity(display: int) -> tuple[int, int, str | None, bool]:
    flags = cgs.display_flags(display)
    title = None
    obj = ca.display_object_for(display)
    if obj:
        info = ca.display_info(obj)
        title = info.get("product_name") or info.get("title")
    return flags["vendor_id"], flags["product_id"], title, flags["builtin"]


def _find_framebuffer(display: int) -> Framebuffer | None:
    vendor, product, title, builtin = _display_identity(display)
    best: Framebuffer | None = None
    for service in _services():
        info = _read_framebuffer(service)
        if not info.timings:
            continue
        score = 0
        if info.vendor and info.vendor == vendor:
            score += 2
        if info.product and info.product == product:
            score += 4
        if info.name and title and info.name == title:
            score += 2
        if builtin and info.vendor == vendor:
            score += 2
        if score <= 0:
            continue
        info.score = score
        if best is None or info.score > best.score:
            best = info
    return best


def _signal_size(display: int) -> tuple[int, int]:
    obj = ca.display_object_for(display)
    if obj:
        current = ca.current_mode(obj)
        if current and current.width and current.height:
            return current.width, current.height
    info = cgs.cg_display_mode_info(display)
    return info.get("pixel_width", 0), info.get("pixel_height", 0)


def _select_current(display: int, framebuffer: Framebuffer) -> Timing:
    width, height = _signal_size(display)
    candidates = [
        timing
        for timing in framebuffer.timings
        if timing.h_active == width and timing.v_active == height
    ]
    if not candidates:
        candidates = list(framebuffer.timings)
    preferred = [timing for timing in candidates if timing.is_preferred]
    if preferred:
        candidates = preferred
    if framebuffer.pixel_clock_hz:
        candidates.sort(
            key=lambda t: abs(t.pixel_clock_hz - framebuffer.pixel_clock_hz)
        )
    return candidates[0]


def current(display: int) -> tuple[Timing, Framebuffer] | None:
    framebuffer = _find_framebuffer(display)
    if not framebuffer or not framebuffer.timings:
        return None
    return _select_current(display, framebuffer), framebuffer
