"""Tiny Objective-C runtime bridge for the private CADisplay API.

The bridge reads the actual method type encoding from the runtime before each
call.  That avoids hard-coding the ABI for private methods whose return types
differ between macOS releases.
"""

from __future__ import annotations

import ctypes
from ctypes import (
    c_bool,
    c_char_p,
    c_double,
    c_float,
    c_int32,
    c_int64,
    c_uint32,
    c_uint64,
    c_void_p,
)

from . import corefoundation as cf

OBJC = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
QUARTZCORE = ctypes.CDLL(
    "/System/Library/Frameworks/QuartzCore.framework/QuartzCore",
    mode=ctypes.RTLD_GLOBAL,
)

OBJC.objc_getClass.argtypes = [c_char_p]
OBJC.objc_getClass.restype = c_void_p
OBJC.sel_registerName.argtypes = [c_char_p]
OBJC.sel_registerName.restype = c_void_p
OBJC.object_getClass.argtypes = [c_void_p]
OBJC.object_getClass.restype = c_void_p
OBJC.class_getInstanceMethod.argtypes = [c_void_p, c_void_p]
OBJC.class_getInstanceMethod.restype = c_void_p
OBJC.method_getTypeEncoding.argtypes = [c_void_p]
OBJC.method_getTypeEncoding.restype = c_char_p
OBJC.class_getName.argtypes = [c_void_p]
OBJC.class_getName.restype = c_char_p

OBJC.objc_msgSend.restype = c_void_p
OBJC.objc_msgSend.argtypes = [c_void_p, c_void_p]


class ObjCError(RuntimeError):
    pass


def get_class(name: str) -> int:
    cls = OBJC.objc_getClass(name.encode("ascii"))
    if not cls:
        raise ObjCError(f"Objective-C class {name!r} is not loaded")
    return int(cls)


def sel(name: str) -> int:
    return int(OBJC.sel_registerName(name.encode("ascii")))


def method_encoding(obj: int, name: str) -> str | None:
    """Return the type encoding of ``obj``'s method ``name``.

    ``obj`` can be an instance or a class.  For a class, ``object_getClass``
    returns the metaclass, so class methods are found too.
    """
    target = OBJC.object_getClass(obj)
    method = OBJC.class_getInstanceMethod(target, sel(name))
    if not method:
        return None
    raw = OBJC.method_getTypeEncoding(method)
    return raw.decode("ascii", "replace") if raw else None


def responds(obj: int, name: str) -> bool:
    if not obj:
        return False
    return bool(send(obj, "respondsToSelector:", sel(name), restype=c_bool))


_RESTYPES: dict[str, object] = {
    "@": c_void_p,
    "#": c_void_p,
    ":": c_void_p,
    "Q": c_uint64,
    "L": c_uint64,
    "I": c_uint32,
    "q": c_int64,
    "i": c_int32,
    "l": c_int64,
    "B": c_bool,
    "c": c_bool,
    "d": c_double,
    "f": c_float,
    "v": None,
}


def _send(obj: int, name: str, args: tuple, restype: object | None = None):
    selector = sel(name)
    if restype is None:
        encoding = method_encoding(obj, name)
        if not encoding:
            raise ObjCError(f"method {name!r} not found on {_class_name(obj)}")
        head = encoding[0]
        if head not in _RESTYPES:
            raise ObjCError(
                f"unsupported return type {encoding!r} for method {name!r} "
                f"on {_class_name(obj)}"
            )
        restype = _RESTYPES[head]

    msg = OBJC.objc_msgSend
    msg.restype = restype
    msg.argtypes = [c_void_p, c_void_p] + [_ctype_for_arg(a) for a in args]
    return msg(obj, selector, *args)


def _ctype_for_arg(arg):
    """Map naked Python values to ctypes types for objc_msgSend."""
    if isinstance(arg, bool):
        return c_bool
    if isinstance(arg, int):
        return c_void_p
    if isinstance(arg, float):
        return c_double
    if isinstance(arg, bytes):
        return c_char_p
    return type(arg)


def send(obj: int, name: str, *args, restype: object | None = None):
    return _send(int(obj), name, args, restype)


def send_class(cls: int, name: str, *args, restype: object | None = None):
    return _send(int(cls), name, args, restype)


def _class_name(obj: int) -> str:
    try:
        target = OBJC.object_getClass(obj)
        raw = OBJC.class_getName(target)
        return raw.decode("ascii", "replace") if raw else "?"
    except Exception:
        return "?"


def nsstring_to_py(ref: int | None) -> str | None:
    return cf.to_str(ref)


def description(obj: int) -> str | None:
    if not obj:
        return None
    try:
        return nsstring_to_py(send(obj, "description"))
    except ObjCError:
        return None


def array_items(ref: int | None) -> list[int]:
    """NSArray is toll-free bridged to CFArray."""
    return [cf.array_get(ref, i) for i in range(cf.array_count(ref))]
