"""SkyLight / CoreDisplay / ColorSync / DisplayServices wrappers."""

from __future__ import annotations

import ctypes
from ctypes import (
    CFUNCTYPE,
    POINTER,
    byref,
    c_bool,
    c_float,
    c_int,
    c_int32,
    c_uint32,
    c_void_p,
)
from dataclasses import dataclass
from pathlib import Path

from . import corefoundation as cf

_SKYLIGHT = "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight"
_COREDISPLAY = "/System/Library/Frameworks/CoreDisplay.framework/CoreDisplay"
_DISPLAYSERVICES = (
    "/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices"
)
_COLORSYNC = "/System/Library/Frameworks/ColorSync.framework/ColorSync"

_sls: ctypes.CDLL | None = None
_cd: ctypes.CDLL | None = None
_ds: ctypes.CDLL | None = None
_cs: ctypes.CDLL | None = None


class SystemAPIError(RuntimeError):
    pass


def _load(path: str, label: str) -> ctypes.CDLL:
    try:
        return ctypes.CDLL(path)
    except OSError as exc:
        raise SystemAPIError(f"cannot load {label} at {path}: {exc}") from exc


def sls() -> ctypes.CDLL:
    global _sls
    if _sls is None:
        lib = _load(_SKYLIGHT, "SkyLight")
        lib.SLSDisplaySupportsHDRMode.argtypes = [c_uint32]
        lib.SLSDisplaySupportsHDRMode.restype = c_bool
        lib.SLSDisplaySetHDRModeEnabled.argtypes = [c_uint32, c_int]
        lib.SLSDisplaySetHDRModeEnabled.restype = c_int
        lib.SLSDisplayIsHDRModeEnabled.argtypes = [c_uint32]
        lib.SLSDisplayIsHDRModeEnabled.restype = c_bool
        lib.SLSDisplaySetActivePresetIndex.argtypes = [c_uint32, c_int]
        lib.SLSDisplaySetActivePresetIndex.restype = c_int
        lib.SLSDisplayGetActivePresetIndex.argtypes = [c_uint32]
        lib.SLSDisplayGetActivePresetIndex.restype = c_int
        _sls = lib
    return _sls


def coredisplay() -> ctypes.CDLL:
    global _cd
    if _cd is None:
        lib = _load(_COREDISPLAY, "CoreDisplay")
        lib.CoreDisplay_Display_GetPresetCount.argtypes = [c_uint32]
        lib.CoreDisplay_Display_GetPresetCount.restype = c_int32
        lib.CoreDisplay_Display_CopyPresetData.argtypes = [c_uint32, c_int32]
        lib.CoreDisplay_Display_CopyPresetData.restype = c_void_p
        _cd = lib
    return _cd


def displayservices() -> ctypes.CDLL:
    global _ds
    if _ds is None:
        lib = _load(_DISPLAYSERVICES, "DisplayServices")
        lib.DisplayServicesCanChangeBrightness.argtypes = [c_uint32]
        lib.DisplayServicesCanChangeBrightness.restype = c_bool
        lib.DisplayServicesGetBrightness.argtypes = [c_uint32, POINTER(c_float)]
        lib.DisplayServicesGetBrightness.restype = c_int
        lib.DisplayServicesSetBrightness.argtypes = [c_uint32, c_float]
        lib.DisplayServicesSetBrightness.restype = c_int
        _ds = lib
    return _ds


def colorsync() -> ctypes.CDLL:
    global _cs
    if _cs is None:
        lib = _load(_COLORSYNC, "ColorSync")
        lib.ColorSyncProfileCreateWithDisplayID.argtypes = [c_uint32]
        lib.ColorSyncProfileCreateWithDisplayID.restype = c_void_p
        lib.ColorSyncProfileGetURL.argtypes = [c_void_p, POINTER(c_bool)]
        lib.ColorSyncProfileGetURL.restype = c_void_p
        lib.ColorSyncProfileCreateWithURL.argtypes = [c_void_p, c_uint32]
        lib.ColorSyncProfileCreateWithURL.restype = c_void_p
        lib.ColorSyncProfileCopyHeader.argtypes = [c_void_p]
        lib.ColorSyncProfileCopyHeader.restype = c_void_p
        lib.ColorSyncProfileCopyTag.argtypes = [c_void_p, c_void_p]
        lib.ColorSyncProfileCopyTag.restype = c_void_p
        lib.ColorSyncDeviceSetCustomProfiles.argtypes = [c_void_p, c_void_p, c_void_p]
        lib.ColorSyncDeviceSetCustomProfiles.restype = c_bool
        lib.CGDisplayCreateUUIDFromDisplayID.argtypes = [c_uint32]
        lib.CGDisplayCreateUUIDFromDisplayID.restype = c_void_p
        lib.kColorSyncDisplayDeviceClass = c_void_p.in_dll(
            lib, "kColorSyncDisplayDeviceClass"
        ).value
        lib.kColorSyncDeviceDefaultProfileID = c_void_p.in_dll(
            lib, "kColorSyncDeviceDefaultProfileID"
        ).value
        lib.kColorSyncProfileURL = c_void_p.in_dll(lib, "kColorSyncProfileURL").value
        lib.kColorSyncWaitForCacheReply = c_void_p.in_dll(
            lib, "kColorSyncWaitForCacheReply"
        ).value
        lib.ColorSyncIterateInstalledProfilesWithOptions.argtypes = [
            c_void_p,
            c_void_p,
            c_void_p,
            c_void_p,
            POINTER(c_bool),
        ]
        lib.ColorSyncIterateInstalledProfilesWithOptions.restype = c_bool
        lib.ColorSyncIterateInstalledProfiles.argtypes = [
            c_void_p,
            c_void_p,
            POINTER(c_bool),
        ]
        lib.ColorSyncIterateInstalledProfiles.restype = c_bool
        _cs = lib
    return _cs


# ---------------------------------------------------------------------------
# HDR
# ---------------------------------------------------------------------------


def hdr_supported(display: int) -> bool:
    return bool(sls().SLSDisplaySupportsHDRMode(display))


def hdr_enabled(display: int) -> bool:
    return bool(sls().SLSDisplayIsHDRModeEnabled(display))


def set_hdr(display: int, enabled: bool) -> None:
    rc = sls().SLSDisplaySetHDRModeEnabled(display, 1 if enabled else 0)
    if rc != 0:
        raise SystemAPIError(f"SLSDisplaySetHDRModeEnabled failed (rc={rc})")


# ---------------------------------------------------------------------------
# CoreDisplay reference presets
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Preset:
    index: int
    name: str
    valid: bool
    uuid: str | None
    disable_auto_brightness: bool

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "valid": self.valid,
            "uuid": self.uuid,
            "disable_auto_brightness": self.disable_auto_brightness,
        }


def presets(display: int) -> list[Preset]:
    lib = coredisplay()
    count = int(lib.CoreDisplay_Display_GetPresetCount(display))
    result: list[Preset] = []
    for index in range(max(0, count)):
        data = lib.CoreDisplay_Display_CopyPresetData(display, index)
        if not data:
            continue
        try:
            name = cf.to_str(cf.dict_get(data, "PresetName")) or f"Preset {index}"
            valid = cf.to_bool(cf.dict_get(data, "PresetValid"), True)
            uuid = cf.to_str(cf.dict_get(data, "PresetUniqueID"))
            disable_auto = cf.to_bool(
                cf.dict_get(data, "PresetHostDisableAutoBrightness"), False
            )
            result.append(
                Preset(
                    index=index,
                    name=name,
                    valid=valid,
                    uuid=uuid,
                    disable_auto_brightness=disable_auto,
                )
            )
        finally:
            cf.release(data)
    return result


def current_preset_index(display: int) -> int:
    return int(sls().SLSDisplayGetActivePresetIndex(display))


def set_preset_index(display: int, index: int) -> None:
    rc = sls().SLSDisplaySetActivePresetIndex(display, int(index))
    if rc != 0:
        raise SystemAPIError(f"SLSDisplaySetActivePresetIndex failed (rc={rc})")


# ---------------------------------------------------------------------------
# ColorSync ICC profiles
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Profile:
    path: str
    device_class: str = ""
    color_space: str = ""
    vendor: int | None = None
    model: int | None = None

    @property
    def is_rgb_monitor(self) -> bool:
        return self.device_class == "mntr" and self.color_space == "RGB "

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "device_class": self.device_class,
            "color_space": self.color_space,
            "rgb_monitor": self.is_rgb_monitor,
            "vendor": self.vendor,
            "model": self.model,
        }


_ICC_SIGNATURES = {
    "mntr",
    "scnr",
    "prtr",
    "link",
    "spac",
    "abst",
    "nmcl",
    "RGB ",
    "GRAY",
    "CMYK",
    "XYZ ",
    "Lab ",
    "Luv ",
    "YCbr",
    "Yxy ",
    "HSV ",
    "HLS ",
    "CMY ",
    "2CLR",
    "3CLR",
    "4CLR",
    "5CLR",
    "6CLR",
    "7CLR",
    "8CLR",
    "9CLR",
    "ACLR",
    "BCLR",
    "CCLR",
    "DCLR",
    "ECLR",
    "FCLR",
}


def _icc_signature(raw: bytes) -> str:
    """Decode a 4-byte ICC signature.

    ColorSyncProfileCopyHeader() returns the header words in host byte order on
    current macOS, while the on-disk ICC header is big-endian.  Accept both.
    """
    forward = raw.decode("latin-1", "replace")
    if forward in _ICC_SIGNATURES:
        return forward
    swapped = raw[::-1].decode("latin-1", "replace")
    if swapped in _ICC_SIGNATURES:
        return swapped
    return forward


def _profile_header(profile: int) -> tuple[str, str]:
    header = colorsync().ColorSyncProfileCopyHeader(profile)
    if not header:
        return "", ""
    try:
        data = cf.data_bytes(header) or b""
    finally:
        cf.release(header)
    if len(data) < 0x14:
        return "", ""
    return _icc_signature(data[0x0C:0x10]), _icc_signature(data[0x10:0x14])


def _profile_mmod(profile: int) -> tuple[int | None, int | None]:
    """Best-effort vendor/model extraction from the ``mmod`` tag."""
    key = cf.cfstr("mmod")
    try:
        tag = colorsync().ColorSyncProfileCopyTag(profile, key)
    finally:
        cf.release(key)
    if not tag:
        return None, None
    try:
        data = cf.data_bytes(tag) or b""
    finally:
        cf.release(tag)
    if len(data) < 20:
        return None, None
    # Apple ICC mmod: signature "mmod", 4 reserved bytes, then vendor ID and
    # model ID as big-endian 32-bit values.
    vendor = int.from_bytes(data[8:12], "big")
    model = int.from_bytes(data[12:16], "big")
    return vendor or None, model or None


def current_profile(display: int) -> Profile | None:
    lib = colorsync()
    profile = lib.ColorSyncProfileCreateWithDisplayID(display)
    if not profile:
        return None
    try:
        url = lib.ColorSyncProfileGetURL(profile, None)
        path = cf.cfurl_to_path(url)
        device_class, color_space = _profile_header(profile)
        vendor, model = _profile_mmod(profile)
        if not path:
            return None
        return Profile(
            path=path,
            device_class=device_class,
            color_space=color_space,
            vendor=vendor,
            model=model,
        )
    finally:
        cf.release(profile)


def list_profiles(include_all: bool = False) -> list[Profile]:
    lib = colorsync()
    results: list[Profile] = []

    Callback = CFUNCTYPE(c_bool, c_void_p, c_void_p)

    def callback(info: int, _user: int) -> bool:
        url = cf.dict_get(info, lib.kColorSyncProfileURL)
        if not url:
            return True
        path = cf.cfurl_to_path(url)
        if not path:
            return True
        profile = lib.ColorSyncProfileCreateWithURL(url, 0)
        if not profile:
            return True
        try:
            device_class, color_space = _profile_header(profile)
            vendor, model = _profile_mmod(profile)
        finally:
            cf.release(profile)
        if not include_all and not (device_class == "mntr" and color_space == "RGB "):
            return True
        results.append(
            Profile(
                path=path,
                device_class=device_class,
                color_space=color_space,
                vendor=vendor,
                model=model,
            )
        )
        return True

    cb = Callback(callback)
    options_key = lib.kColorSyncWaitForCacheReply
    options = cf.cfdict([(options_key, cf.cfbool(True))])
    all_profiles = c_bool(False)
    try:
        try:
            lib.ColorSyncIterateInstalledProfilesWithOptions(
                ctypes.cast(cb, c_void_p),
                None,
                None,
                options,
                byref(all_profiles),
            )
        except (AttributeError, OSError):
            lib.ColorSyncIterateInstalledProfiles(
                ctypes.cast(cb, c_void_p),
                None,
                byref(all_profiles),
            )
    finally:
        cf.release(options)
    # Deduplicate, preserving order.
    seen: set[str] = set()
    unique: list[Profile] = []
    for profile in results:
        if profile.path not in seen:
            seen.add(profile.path)
            unique.append(profile)
    return unique


def set_profile(display: int, path: str | Path) -> None:
    path = str(path)
    if not Path(path).is_file():
        raise SystemAPIError(f"profile not found: {path}")
    lib = colorsync()
    url = cf.cfurl_from_path(path)
    uuid = lib.CGDisplayCreateUUIDFromDisplayID(display)
    if not uuid:
        cf.release(url)
        raise SystemAPIError("CGDisplayCreateUUIDFromDisplayID failed")
    try:
        dictionary = cf.cfdict([(lib.kColorSyncDeviceDefaultProfileID, url)])
        try:
            ok = lib.ColorSyncDeviceSetCustomProfiles(
                lib.kColorSyncDisplayDeviceClass, uuid, dictionary
            )
        finally:
            cf.release(dictionary)
    finally:
        cf.release(url)
        cf.release(uuid)
    if not ok:
        raise SystemAPIError("ColorSyncDeviceSetCustomProfiles failed")


def profile_info_for_path(path: str | Path) -> Profile | None:
    url = cf.cfurl_from_path(path)
    try:
        profile = colorsync().ColorSyncProfileCreateWithURL(url, 0)
        if not profile:
            return None
        try:
            device_class, color_space = _profile_header(profile)
            vendor, model = _profile_mmod(profile)
        finally:
            cf.release(profile)
        return Profile(
            path=str(path),
            device_class=device_class,
            color_space=color_space,
            vendor=vendor,
            model=model,
        )
    finally:
        cf.release(url)


# ---------------------------------------------------------------------------
# Gamma
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GammaTable:
    red_min: float
    red_max: float
    red_gamma: float
    green_min: float
    green_max: float
    green_gamma: float
    blue_min: float
    blue_max: float
    blue_gamma: float

    def to_dict(self) -> dict:
        return {
            "red": [self.red_min, self.red_max, self.red_gamma],
            "green": [self.green_min, self.green_max, self.green_gamma],
            "blue": [self.blue_min, self.blue_max, self.blue_gamma],
        }

    def scaled(self, multiplier: float) -> "GammaTable":
        return GammaTable(
            self.red_min,
            self.red_max,
            self.red_gamma * multiplier,
            self.green_min,
            self.green_max,
            self.green_gamma * multiplier,
            self.blue_min,
            self.blue_max,
            self.blue_gamma * multiplier,
        )


def _cg() -> ctypes.CDLL:
    from . import cg as cgs

    return cgs.CG


def get_gamma(display: int) -> GammaTable:
    CG = _cg()
    values = [c_float(0.0) for _ in range(9)]
    rc = CG.CGGetDisplayTransferByFormula(
        display,
        *[byref(v) for v in values],
    )
    if rc != 0:
        raise SystemAPIError(f"CGGetDisplayTransferByFormula failed (rc={rc})")
    return GammaTable(*[float(v.value) for v in values])


def set_gamma(display: int, table: GammaTable) -> None:
    CG = _cg()
    rc = CG.CGSetDisplayTransferByFormula(
        display,
        c_float(table.red_min),
        c_float(table.red_max),
        c_float(table.red_gamma),
        c_float(table.green_min),
        c_float(table.green_max),
        c_float(table.green_gamma),
        c_float(table.blue_min),
        c_float(table.blue_max),
        c_float(table.blue_gamma),
    )
    if rc != 0:
        raise SystemAPIError(f"CGSetDisplayTransferByFormula failed (rc={rc})")


def _setup_cg_gamma() -> None:
    CG = _cg()
    CG.CGGetDisplayTransferByFormula.argtypes = [c_uint32] + [POINTER(c_float)] * 9
    CG.CGGetDisplayTransferByFormula.restype = c_int32
    CG.CGSetDisplayTransferByFormula.argtypes = [c_uint32] + [c_float] * 9
    CG.CGSetDisplayTransferByFormula.restype = c_int32


_setup_cg_gamma()


# ---------------------------------------------------------------------------
# Brightness
# ---------------------------------------------------------------------------


def brightness_capable(display: int) -> bool:
    return bool(displayservices().DisplayServicesCanChangeBrightness(display))


def get_brightness(display: int) -> float | None:
    value = c_float(0.0)
    rc = displayservices().DisplayServicesGetBrightness(display, byref(value))
    if rc != 0:
        return None
    return float(value.value)


def set_brightness(display: int, value: float) -> None:
    value = max(0.0, min(1.0, float(value)))
    rc = displayservices().DisplayServicesSetBrightness(display, value)
    if rc != 0:
        raise SystemAPIError(f"DisplayServicesSetBrightness failed (rc={rc})")


# ---------------------------------------------------------------------------
# Display UUID
# ---------------------------------------------------------------------------


def display_uuid(display: int) -> str | None:
    uuid = colorsync().CGDisplayCreateUUIDFromDisplayID(display)
    if not uuid:
        return None
    try:
        return cf.uuid_to_str(uuid)
    finally:
        cf.release(uuid)
