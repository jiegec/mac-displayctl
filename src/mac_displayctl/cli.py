"""mac-displayctl command line interface."""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
from pathlib import Path

from . import cadisplay as ca, cg as cgs, objc_bridge as objc, system, timing

VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _err(message: str) -> None:
    print(f"mac-displayctl: {message}", file=sys.stderr)


def _json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _yes(args) -> bool:
    return bool(getattr(args, "yes", False))


def _dry(args) -> bool:
    return bool(getattr(args, "dry_run", False))


def _confirm(args, message: str) -> bool:
    if _yes(args):
        return True
    if sys.stdin.isatty():
        try:
            answer = input(f"{message} [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")
    _err("refusing to change display settings without --yes")
    return False


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(str(value)))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)))


def _bool(value: bool) -> str:
    return "yes" if value else "no"


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def _print_timing(
    item: timing.Timing, framebuffer: timing.Framebuffer | None = None
) -> None:
    print(
        f"Timing ID {item.id}: {item.h_active}x{item.v_active} "
        f"@ {item.refresh_hz:.3f} Hz"
    )
    print(
        f"  H: active {item.h_active}  total {item.h_total}  blank {item.h_blank}  "
        f"front porch {item.h_front}  sync {item.h_sync}  back porch {item.h_back}  "
        f"polarity {item.h_polarity_text}"
    )
    print(
        f"  V: active {item.v_active}  total {item.v_total}  blank {item.v_blank}  "
        f"front porch {item.v_front}  sync {item.v_sync}  back porch {item.v_back}  "
        f"polarity {item.v_polarity_text}"
    )
    print(
        f"  pixel clock: {item.pixel_clock_hz / 1e6:.3f} MHz "
        f"(Htotal x Vtotal x refresh)"
    )
    if framebuffer and framebuffer.pixel_clock_hz:
        print(
            f"  registry PixelClock (current): {framebuffer.pixel_clock_hz / 1e6:.3f} MHz"
        )
    print(
        f"  flags: preferred={_bool(item.is_preferred)} "
        f"interlaced={_bool(item.is_interlaced)} virtual={_bool(item.is_virtual)} "
        f"timingType={item.timing_type}"
    )


def _print_gamma_values(display: int, name: str, values: dict) -> None:
    print(f"Display {display}: {name}")
    for channel, table in values.items():
        print(
            f"  {channel}: min={table[0]:.6f} max={table[1]:.6f} gamma={table[2]:.6f}"
        )


def _display_ids() -> list[int]:
    return cgs.online_displays()


def _display_title(display: int) -> str:
    obj = ca.display_object_for(display)
    if obj:
        info = ca.display_info(obj)
        if info.get("title"):
            return info["title"]
    return f"Display {display}"


def _query_displays(args) -> list[int]:
    """Displays targeted by a read-only command."""
    if getattr(args, "display", None):
        return [_resolve_display(args.display)]
    return _display_ids()


def _resolve_display(spec: str | None) -> int:
    ids = _display_ids()
    if not ids:
        raise RuntimeError("no online displays found")
    if spec is None or spec.lower() in ("main", "primary"):
        main = cgs.main_display()
        if main in ids:
            return main
        return ids[0]
    try:
        wanted = int(spec, 0)
    except ValueError:
        wanted = None
    if wanted is not None:
        if wanted not in ids:
            raise RuntimeError(f"display {wanted} is not online (online: {ids})")
        return wanted
    needle = spec.casefold()
    matches = []
    for display in ids:
        obj = ca.display_object_for(display)
        names = [f"display {display}"]
        if obj:
            info = ca.display_info(obj)
            names.extend(
                str(v)
                for v in (
                    info.get("title"),
                    info.get("name"),
                    info.get("product_name"),
                    info.get("unique_id"),
                )
                if v
            )
        if any(needle == name.casefold() for name in names):
            matches.append(display)
    if not matches:
        raise RuntimeError(f"no display matches {spec!r} (online: {ids})")
    if len(matches) > 1:
        raise RuntimeError(f"{spec!r} is ambiguous: displays {matches}")
    return matches[0]


def _ca_object(display: int) -> int:
    obj = ca.display_object_for(display)
    if not obj:
        raise RuntimeError(
            f"Core Animation display object not found for display {display}"
        )
    return obj


def _current_cgs_mode(display: int) -> cgs.CGSMode | None:
    current_id = cgs.current_mode_id(display)
    if current_id is None:
        return None
    return cgs.find_mode(display, current_id)


def _display_state(display: int, with_profiles: bool = True) -> dict:
    flags = cgs.display_flags(display)
    current = _current_cgs_mode(display)
    cg_mode = cgs.cg_display_mode_info(display)
    ca_obj = ca.display_object_for(display)
    ca_info = ca.display_info(ca_obj) if ca_obj else {}
    ca_cur = ca.current_mode(ca_obj) if ca_obj else None

    hdr_supported = _safe(lambda: system.hdr_supported(display), False)
    hdr_on = _safe(lambda: system.hdr_enabled(display), False)
    profile = (
        _safe(lambda: system.current_profile(display), None) if with_profiles else None
    )
    preset_index = _safe(lambda: system.current_preset_index(display), -1)
    preset_list = _safe(lambda: system.presets(display), []) if with_profiles else []
    preset_name = None
    for preset in preset_list:
        if preset.index == preset_index:
            preset_name = preset.name
            break
    brightness = _safe(lambda: system.get_brightness(display), None)

    return {
        "id": display,
        "name": ca_info.get("title") or f"Display {display}",
        "ca_info": ca_info,
        "builtin": flags["builtin"],
        "main": flags["main"],
        "vendor_id": flags["vendor_id"],
        "product_id": flags["product_id"],
        "serial": flags["serial"],
        "uuid": _safe(lambda: system.display_uuid(display), None),
        "rotation": flags["rotation"],
        "cgs": current.to_dict() if current else None,
        "cg": cg_mode,
        "ca": ca_cur.to_dict() if ca_cur else None,
        "hdr": {
            "supported": hdr_supported,
            "enabled": hdr_on,
            "ca_hdr_mode": ca_cur.hdr_mode if ca_cur else None,
        },
        "profile": profile.to_dict() if profile else None,
        "preset": {
            "index": preset_index,
            "name": preset_name,
            "count": len(preset_list),
        },
        "brightness": brightness,
        "brightness_capable": _safe(lambda: system.brightness_capable(display), False),
        "pixels": {
            "wide": flags["pixels_wide"],
            "high": flags["pixels_high"],
        },
    }


# ---------------------------------------------------------------------------
# list / info
# ---------------------------------------------------------------------------


def cmd_list(args) -> int:
    displays = _display_ids()
    if getattr(args, "display", None):
        displays = [_resolve_display(args.display)]
    states = [_display_state(display) for display in displays]
    if args.json:
        _json(states[0] if args.display and len(states) == 1 else states)
        return 0
    rows = []
    for state in states:
        cgs_mode = state["cgs"]
        ca_mode = state["ca"]
        resolution = (
            f"{cgs_mode['width']}x{cgs_mode['height']}"
            if cgs_mode
            else f"{state['pixels']['wide']}x{state['pixels']['high']}"
        )
        ca_resolution = f"{ca_mode['width']}x{ca_mode['height']}" if ca_mode else "?"
        refresh = f"{cgs_mode['refresh_hz']:.2f}" if cgs_mode else "?"
        depth = cgs_mode["depth"] if cgs_mode else "?"
        color = ca_mode["label"] if ca_mode else "?"
        profile = Path(state["profile"]["path"]).name if state["profile"] else "?"
        rows.append(
            [
                str(state["id"]),
                "*" if state["main"] else "",
                _bool(state["builtin"]),
                state["name"],
                resolution,
                ca_resolution,
                refresh,
                depth,
                color,
                _bool(state["hdr"]["enabled"]),
                profile,
            ]
        )
    _table(
        [
            "ID",
            "Main",
            "Builtin",
            "Name",
            "Logical Resolution",
            "Core Animation Resolution",
            "Hz",
            "Depth",
            "Core Animation color mode",
            "HDR",
            "ICC profile",
        ],
        rows,
    )
    return 0


def _print_info_state(state: dict, timing_result) -> None:
    print(f"Display {state['id']}: {state['name']}")
    print(
        f"  Type: {'built-in' if state['builtin'] else 'external'}"
        f"    Main: {_bool(state['main'])}"
    )
    print(
        f"  Vendor: 0x{state['vendor_id']:04x}"
        f"    Product: 0x{state['product_id']:04x}"
        f"    Serial: {state['serial']}"
    )
    print(f"  UUID: {state['uuid'] or 'unknown'}")
    print(f"  Rotation: {state['rotation']:.1f}°")

    cgs_mode = state["cgs"]
    if cgs_mode:
        print(
            f"  Core Graphics Services (CGS) mode: {cgs_mode['width']}x{cgs_mode['height']} @ {cgs_mode['refresh_hz']:.3f} Hz"
            f" — {cgs_mode['depth']}"
        )
        print(f"            encoding {cgs_mode['pixel_encoding']}")
        print(
            f"            mode ID {cgs_mode['mode_id']},"
            f" I/O display mode ID {cgs_mode['iodisplay_mode_id']},"
            f" I/O flags {cgs_mode['io_flags']}, scale {cgs_mode['resolution']:g}x"
        )

    cg = state["cg"]
    if cg:
        depth = cgs.depth_name(cg["bits_per_pixel"])
        print(
            f"  Core Graphics (CG) mode:  {cg['width']}x{cg['height']} @ {cg['refresh_hz']:.2f} Hz"
            f" — {depth}"
        )
        print(f"            encoding {cg['pixel_encoding']}")
        print(
            f"            I/O display mode ID {cg['iodisplay_mode_id']},"
            f" I/O flags 0x{cg['io_flags']:08x}"
        )
        if cg.get("pixel_width"):
            print(
                f"            backing pixels {cg['pixel_width']}x{cg['pixel_height']}"
                f" (logical {cg['width']}x{cg['height']})"
            )

    ca_mode = state["ca"]
    if ca_mode:
        print(f"  Core Animation (CA) mode:  {ca_mode['label']}")
        print(f"            colorMode {ca_mode['color_mode']}")

    hdr = state["hdr"]
    hdr_text = "supported" if hdr["supported"] else "unsupported"
    hdr_text += ", " + ("enabled" if hdr["enabled"] else "disabled")
    if hdr.get("ca_hdr_mode"):
        hdr_text += f" (mode: {hdr['ca_hdr_mode']})"
    print(f"  High Dynamic Range (HDR): {hdr_text}")
    print(
        f"  International Color Consortium (ICC) profile: {state['profile']['path'] if state['profile'] else 'none'}"
    )

    preset = state["preset"]
    if preset["index"] < 0:
        preset_text = "none"
    elif preset["name"]:
        preset_text = f"{preset['index']} ({preset['name']})"
    else:
        preset_text = str(preset["index"])
    print(f"  Preset: {preset_text}")

    if state["brightness"] is None:
        brightness_text = "not available"
    else:
        brightness_text = f"{state['brightness']:.3f}"
        if not state["brightness_capable"]:
            brightness_text += " (read-only)"
    print(f"  Brightness: {brightness_text}")
    gamma = state.get("gamma")
    if gamma:
        print("  Gamma:")
        for channel, values in gamma.items():
            print(
                f"    {channel}: min={values[0]:.6f}"
                f" max={values[1]:.6f} gamma={values[2]:.6f}"
            )

    if timing_result:
        _print_timing(timing_result[0], timing_result[1])


def cmd_info(args) -> int:
    displays = [_resolve_display(args.display)] if args.display else _display_ids()
    if not displays:
        raise RuntimeError("no online displays found")

    entries = []
    for display in displays:
        state = _display_state(display)
        gamma = _safe(lambda: system.get_gamma(display).to_dict(), None)
        if gamma:
            state["gamma"] = gamma
        timing_result = _safe(lambda: timing.current(display), None)
        if timing_result:
            item, framebuffer = timing_result
            state["timing"] = {
                "current": item.to_dict(),
                "framebuffer": {
                    "vendor": framebuffer.vendor,
                    "product": framebuffer.product,
                    "name": framebuffer.name,
                    "native": [framebuffer.native_width, framebuffer.native_height],
                    "pixel_clock_mhz": (
                        round(framebuffer.pixel_clock_hz / 1e6, 4)
                        if framebuffer.pixel_clock_hz
                        else None
                    ),
                },
            }
        entries.append((state, timing_result))

    if args.json:
        payload = [state for state, _ in entries]
        _json(payload[0] if args.display and len(payload) == 1 else payload)
        return 0

    for index, (state, timing_result) in enumerate(entries):
        if index:
            print()
        _print_info_state(state, timing_result)
    return 0


# ---------------------------------------------------------------------------
# modes
# ---------------------------------------------------------------------------


def _mode_filters(args) -> dict:
    return {
        "width": args.width,
        "height": args.height,
        "refresh": args.refresh,
        "bpp": args.depth,
        "pixel_encoding": args.pixel_encoding,
    }


def cmd_modes(args) -> int:
    filters = _mode_filters(args)
    entries = []
    for display in _query_displays(args):
        modes = cgs.list_modes(display)
        current_id = cgs.current_mode_id(display)
        if any(value is not None for value in filters.values()):
            modes = cgs.filter_modes(modes, **filters)
        modes.sort(key=lambda m: (m.width, m.height, m.refresh_hz, -m.bpp, m.mode_id))
        entries.append(
            {
                "display": display,
                "name": _display_title(display),
                "current_mode_id": current_id,
                "modes": modes,
            }
        )

    if args.json:
        payload = [
            {
                "display": entry["display"],
                "current_mode_id": entry["current_mode_id"],
                "modes": [mode.to_dict() for mode in entry["modes"]],
            }
            for entry in entries
        ]
        _json(payload[0] if args.display and len(payload) == 1 else payload)
        return 0

    for index, entry in enumerate(entries):
        if index:
            print()
        print(f"Display {entry['display']}: {entry['name']}")
        rows = []
        for mode in entry["modes"]:
            rows.append(
                [
                    "*" if mode.mode_id == entry["current_mode_id"] else "",
                    str(mode.mode_id),
                    f"{mode.width}x{mode.height}",
                    f"{mode.refresh_hz:.3f}",
                    mode.pixel_encoding,
                    str(mode.bpp),
                    cgs.depth_name(mode.bpp),
                    f"0x{mode.io_flags:08x}",
                    str(mode.iodisplay_mode_id),
                    str(mode.depth_slot),
                ]
            )
        _table(
            [
                "",
                "ModeID",
                "Resolution",
                "Hz",
                "Pixel encoding",
                "bpp",
                "Depth",
                "IOFlags",
                "IODisplayID",
                "slot",
            ],
            rows,
        )
        print(
            f"\n{len(entry['modes'])} Core Graphics Services (CGS) mode(s); "
            f"current mode ID {entry['current_mode_id']}"
        )
    return 0


# ---------------------------------------------------------------------------
# colors
# ---------------------------------------------------------------------------


def _color_filters(args) -> dict:
    return {
        "width": args.width,
        "height": args.height,
        "refresh": args.refresh,
        "bit_depth": args.bit_depth,
        "pixel_format": args.format,
        "subsampling": args.subsampling,
        "range_": args.range,
        "hdr": args.hdr,
        "gamut": args.gamut,
        "color_mode": args.color_mode,
    }


def _print_ca_modes(
    modes: list[ca.CAMode], current: ca.CAMode | None, limit: int = 0
) -> None:
    rows = []
    for mode in modes[: limit or None]:
        current_mark = "*" if current and mode.is_same(current) else ""
        rows.append(
            [
                current_mark,
                str(mode.index),
                f"{mode.width}x{mode.height}",
                f"{mode.refresh_hz:.2f}",
                str(mode.bit_depth),
                mode.pixel_format,
                mode.subsampling,
                mode.range,
                mode.hdr_mode or "?",
                mode.color_gamut or "?",
                mode.color_mode,
                mode.description,
            ]
        )
    _table(
        [
            "",
            "#",
            "Resolution",
            "Hz",
            "Bit",
            "Format",
            "Sub",
            "Range",
            "HDR",
            "Gamut",
            "colorMode",
            "description",
        ],
        rows,
    )


def cmd_colors(args) -> int:
    filters = _color_filters(args)
    entries = []
    for display in _query_displays(args):
        ca_obj = _ca_object(display)
        current = ca.current_mode(ca_obj)
        all_modes = ca.available_modes(ca_obj)
        modes = all_modes
        if any(value is not None for value in filters.values()):
            modes = ca.select_modes(modes, **filters)
        if args.current:
            if not current:
                _err(f"display {display}: no current Core Animation display mode")
                return 1
            current.index = ca.index_of(all_modes, current)
            modes = [current]
        entries.append(
            {
                "display": display,
                "name": _display_title(display),
                "current": current,
                "modes": modes,
            }
        )

    if args.json:
        payload = [
            {
                "display": entry["display"],
                "current": entry["current"].to_dict() if entry["current"] else None,
                "modes": [mode.to_dict() for mode in entry["modes"]],
            }
            for entry in entries
        ]
        _json(payload[0] if args.display and len(payload) == 1 else payload)
        return 0

    for index, entry in enumerate(entries):
        if index:
            print()
        print(f"Display {entry['display']}: {entry['name']}")
        _print_ca_modes(entry["modes"], entry["current"], args.limit)
        print(f"\n{len(entry['modes'])} Core Animation display mode(s)")
        if entry["current"]:
            print(f"current: {entry['current'].format_label}")
    return 0


# ---------------------------------------------------------------------------
# setters
# ---------------------------------------------------------------------------


def cmd_set_mode(args) -> int:
    display = _resolve_display(args.display)
    modes = cgs.list_modes(display)
    current = _current_cgs_mode(display)
    ca_obj = _safe(lambda: _ca_object(display), None)
    ca_before = ca.current_mode(ca_obj) if ca_obj else None
    selected = None
    if args.mode_id is not None:
        selected = cgs.find_mode(display, args.mode_id)
        if not selected:
            _err(
                f"Core Graphics Services (CGS) mode ID {args.mode_id} not found on display {display}"
            )
            return 1
    else:
        width = (
            args.width
            if args.width is not None
            else (current.width if current else None)
        )
        height = (
            args.height
            if args.height is not None
            else (current.height if current else None)
        )
        refresh = args.refresh if args.refresh is not None else None
        selected = cgs.select_mode(
            modes,
            width=width,
            height=height,
            refresh=refresh,
            bpp=args.depth,
            pixel_encoding=args.pixel_encoding,
            prefer_bpp=current.bpp if current else None,
            prefer_mode_id=current.mode_id if current else None,
        )
    if not selected:
        _err("no matching Core Graphics Services (CGS) mode")
        return 1

    print(
        f"display {display}: mode ID {selected.mode_id} "
        f"{selected.width}x{selected.height} @ {selected.refresh_hz:.3f} Hz "
        f"{selected.pixel_encoding} ({selected.bpp} bpp)"
    )
    if _dry(args):
        print("dry run: no change")
        return 0
    if not _confirm(args, "apply this Core Graphics Services (CGS) mode"):
        return 1
    cgs.set_mode(display, selected.mode_id)

    # Preserve the previous color mode across a resolution change when the new
    # mode still offers it.
    if ca_obj and ca_before and ca_before.color_mode not in ("", "auto"):
        try:
            ca_modes = ca.available_modes(ca_obj)
            ca_matches = ca.select_modes(
                ca_modes,
                width=ca_before.width,
                height=ca_before.height,
                refresh=ca_before.refresh_hz,
                bit_depth=ca_before.bit_depth,
                pixel_format=ca_before.pixel_format,
                subsampling=ca_before.subsampling,
                range_=ca_before.range,
                hdr=ca_before.hdr_mode,
            )
            if ca_matches:
                if system.hdr_supported(display):
                    target_hdr = ca.is_hdr(ca_before)
                    if system.hdr_enabled(display) != target_hdr:
                        system.set_hdr(display, target_hdr)
                ca.set_mode(ca_obj, ca_matches[0])
        except Exception as exc:
            _err(f"note: could not preserve color mode: {exc}")

    after = _current_cgs_mode(display)
    if after:
        print(
            f"now: mode ID {after.mode_id} {after.width}x{after.height} "
            f"@ {after.refresh_hz:.3f} Hz {after.pixel_encoding} ({after.bpp} bpp)"
        )
    after_ca = ca.current_mode(ca_obj) if ca_obj else None
    if after_ca:
        print(f"CA: {after_ca.format_label} ({after_ca.color_mode})")
    return 0


def cmd_set_color(args) -> int:
    display = _resolve_display(args.display)
    ca_obj = _ca_object(display)
    current = ca.current_mode(ca_obj)
    modes = ca.available_modes(ca_obj)

    if args.index is None and not any(
        value is not None
        for value in (
            args.width,
            args.height,
            args.refresh,
            args.bit_depth,
            args.format,
            args.subsampling,
            args.range,
            args.hdr,
            args.gamut,
            args.color_mode,
        )
    ):
        _err("set-color needs at least one filter or --index (use --list to inspect)")
        return 1

    if args.index is not None:
        if args.index < 0 or args.index >= len(modes):
            _err(
                f"Core Animation mode index {args.index} out of range 0..{len(modes) - 1}"
            )
            return 1
        matches = [modes[args.index]]
    else:
        width = args.width
        height = args.height
        refresh = args.refresh
        if not args.any_resolution and width is None and height is None and current:
            width, height = current.width, current.height
        if not args.any_refresh and refresh is None and current:
            refresh = current.refresh_hz
        matches = ca.select_modes(
            modes,
            width=width,
            height=height,
            refresh=refresh,
            bit_depth=args.bit_depth,
            pixel_format=args.format,
            subsampling=args.subsampling,
            range_=args.range,
            hdr=args.hdr,
            gamut=args.gamut,
            color_mode=args.color_mode,
        )
    if not matches:
        _err("no matching Core Animation display mode")
        if current:
            _err(f"current is {current.format_label} ({current.color_mode})")
        return 1

    if args.list:
        _print_ca_modes(matches, current, args.limit)
        return 0

    selected = matches[0]
    print(
        f"display {display}: Core Animation mode #{selected.index} "
        f"{selected.format_label}"
    )
    print(f"  colorMode={selected.color_mode!r} description={selected.description!r}")
    if len(matches) > 1:
        print(f"  ({len(matches)} matches; use --list to see them all)")
    if _dry(args):
        print("dry run: no change")
        return 0
    if not _confirm(args, "apply this Core Animation display mode"):
        return 1

    # Ordering matters: WindowServer re-evaluates the CA mode when the HDR flag
    # changes.  Set the SkyLight HDR flag first, then apply the CA mode, so the
    # requested bit depth / subsampling / range is the one that sticks.
    try:
        if system.hdr_supported(display):
            target_hdr = ca.is_hdr(selected)
            if system.hdr_enabled(display) != target_hdr:
                system.set_hdr(display, target_hdr)
    except Exception as exc:
        _err(f"note: SkyLight HDR sync skipped: {exc}")

    ca.set_mode(ca_obj, selected)

    after = ca.current_mode(ca_obj)
    if after:
        after.index = ca.index_of(modes, after)
        print(f"now: #{after.index} {after.format_label} ({after.color_mode!r})")
        if not after.is_same(selected):
            _err(
                "note: macOS applied a different Core Animation display mode than requested; "
                "the active Core Graphics Services (CGS)/IOFB mode constrains which color modes are usable "
                "(use `colors --current` to see the effective mode)"
            )
    return 0


def cmd_set_hdr(args) -> int:
    display = _resolve_display(args.display)
    enabled = args.action == "on"
    if not system.hdr_supported(display):
        _err(
            f"display {display} does not support HDR; "
            "use -d ID to select an HDR-capable display"
        )
        return 1
    if _dry(args):
        print(f"would set HDR {'on' if enabled else 'off'}")
        return 0
    if not _confirm(args, f"turn HDR {'on' if enabled else 'off'}"):
        return 1
    system.set_hdr(display, enabled)
    print(f"display {display}: HDR enabled={_bool(system.hdr_enabled(display))}")
    return 0


def cmd_profiles(args) -> int:
    displays = _query_displays(args)
    profiles = system.list_profiles(include_all=args.all)
    current_entries = []
    for display in displays:
        profile = _safe(lambda: system.current_profile(display), None)
        current_entries.append(
            {
                "display": display,
                "name": _display_title(display),
                "profile": profile.to_dict() if profile else None,
            }
        )
    current_paths = {
        entry["profile"]["path"] for entry in current_entries if entry["profile"]
    }
    if args.json:
        _json(
            {
                "profiles": [profile.to_dict() for profile in profiles],
                "current": current_entries,
            }
        )
        return 0
    rows = []
    for profile in profiles:
        rows.append(
            [
                "*" if profile.path in current_paths else "",
                profile.path,
                profile.device_class.strip(),
                profile.color_space.strip(),
                f"0x{profile.vendor:04x}" if profile.vendor else "",
                f"0x{profile.model:04x}" if profile.model else "",
            ]
        )
    _table(["", "Profile path", "Class", "Space", "Vendor", "Model"], rows)
    print(f"\n{len(profiles)} profile(s)")
    for entry in current_entries:
        path = entry["profile"]["path"] if entry["profile"] else "none"
        print(f"  display {entry['display']} ({entry['name']}): {path}")
    return 0


def cmd_set_profile(args) -> int:
    display = _resolve_display(args.display)
    if not args.path:
        _err("set-profile needs a path")
        return 1
    path = Path(args.path).expanduser()
    info = system.profile_info_for_path(path)
    if not info:
        _err(f"not a valid ICC profile: {path}")
        return 1
    print(
        f"display {display}: set profile {path} "
        f"(class={info.device_class.strip()!r} space={info.color_space.strip()!r})"
    )
    if info.device_class != "mntr" or info.color_space != "RGB ":
        _err("warning: this is not an RGB monitor profile")
    if _dry(args):
        print("dry run: no change")
        return 0
    if not _confirm(args, "set this ColorSync display profile"):
        return 1
    system.set_profile(display, path)
    current = _safe(lambda: system.current_profile(display), None)
    print(f"current: {current.path if current else '?'}")
    return 0


def cmd_presets(args) -> int:
    entries = []
    for display in _query_displays(args):
        presets = system.presets(display)
        current_index = _safe(lambda: system.current_preset_index(display), -1)
        entries.append(
            {
                "display": display,
                "name": _display_title(display),
                "presets": presets,
                "current_index": current_index,
            }
        )

    if args.json:
        payload = [
            {
                "display": entry["display"],
                "current_index": entry["current_index"],
                "presets": [preset.to_dict() for preset in entry["presets"]],
            }
            for entry in entries
        ]
        _json(payload[0] if args.display and len(payload) == 1 else payload)
        return 0

    for index, entry in enumerate(entries):
        if index:
            print()
        print(f"Display {entry['display']}: {entry['name']}")
        if not entry["presets"]:
            print("  no CoreDisplay presets reported")
            continue
        rows = [
            [
                "*" if preset.index == entry["current_index"] else "",
                str(preset.index),
                preset.name,
                _bool(preset.valid),
                preset.uuid or "",
                _bool(preset.disable_auto_brightness),
            ]
            for preset in entry["presets"]
        ]
        _table(["", "Index", "Name", "Valid", "UUID", "Locks brightness"], rows)
    return 0


def cmd_set_preset(args) -> int:
    display = _resolve_display(args.display)
    presets = system.presets(display)
    if not args.target:
        _err("set-preset needs an index or name")
        return 1
    target: int | None = None
    try:
        target = int(args.target, 0)
    except ValueError:
        needle = args.target.casefold()
        for preset in presets:
            if preset.name.casefold() == needle:
                target = preset.index
                break
    if target is None:
        _err(f"preset {args.target!r} not found")
        return 1
    if not any(preset.index == target for preset in presets):
        _err(f"preset index {target} not found")
        return 1
    print(f"display {display}: set preset index {target}")
    if _dry(args):
        print("dry run: no change")
        return 0
    if not _confirm(args, "activate this CoreDisplay preset"):
        return 1
    system.set_preset_index(display, target)
    print(f"current preset index: {system.current_preset_index(display)}")
    return 0


def _apply_gamma(display: int, table: system.GammaTable, args) -> int:
    print(f"display {display}: set gamma table")
    _print_gamma_values(display, _display_title(display), table.to_dict())
    if _dry(args):
        print("dry run: no change")
        return 0
    if not _confirm(args, "apply this gamma table"):
        return 1
    system.set_gamma(display, table)
    print("now:")
    _print_gamma_values(
        display, _display_title(display), system.get_gamma(display).to_dict()
    )
    return 0


def cmd_set_gamma(args) -> int:
    if not math.isfinite(args.multiplier) or args.multiplier <= 0:
        _err("gamma multiplier must be a finite value > 0")
        return 1
    display = _resolve_display(args.display)
    table = system.get_gamma(display).scaled(args.multiplier)
    return _apply_gamma(display, table, args)


def cmd_set_gamma_formula(args) -> int:
    values = [float(v) for v in args.values]
    if len(values) != 9:
        _err(
            "formula needs exactly 9 values: rmin rmax rgamma gmin gmax ggamma bmin bmax bgamma"
        )
        return 1
    if not all(math.isfinite(value) for value in values):
        _err("formula values must be finite")
        return 1
    display = _resolve_display(args.display)
    return _apply_gamma(display, system.GammaTable(*values), args)


def cmd_set_brightness(args) -> int:
    if not math.isfinite(args.value) or not 0.0 <= args.value <= 1.0:
        _err("brightness must be a finite value between 0 and 1")
        return 1
    display = _resolve_display(args.display)
    if _dry(args):
        print(f"would set brightness to {args.value}")
        return 0
    if not _confirm(args, f"set brightness to {args.value}"):
        return 1
    system.set_brightness(display, args.value)
    print(f"display {display}: brightness={system.get_brightness(display)}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def _add_query(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--debug", action="store_true", help="show tracebacks")


def _add_action(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--debug", action="store_true", help="show tracebacks")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="do not ask for confirmation"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would be done"
    )


def _add_display(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-d",
        "--display",
        default=None,
        help="display ID or name (all for read-only commands, main for setters)",
    )


def _add_color_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--width", type=int, help="mode width")
    parser.add_argument("--height", type=int, help="mode height")
    parser.add_argument("--refresh", type=float, help="refresh rate in Hz")
    parser.add_argument("--bit-depth", type=int, help="8, 10, 12, 16 ...")
    parser.add_argument("--format", choices=["rgb", "ycbcr"], help="pixel format")
    parser.add_argument(
        "--subsampling", choices=["444", "422", "420"], help="chroma subsampling"
    )
    parser.add_argument(
        "--range", choices=["full", "limited"], help="quantization range"
    )
    parser.add_argument(
        "--hdr",
        help="exact hdrMode value, case-insensitive (SDR, HDR10, DolbyVision, HLG, ...)",
    )
    parser.add_argument("--gamut", help="exact colorGamut value, e.g. gamut_P3")
    parser.add_argument("--color-mode", help="exact colorMode value, e.g. RGBFullRange")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mac-displayctl",
        description=(
            "Control macOS display modes, Core Animation display color formats, HDR, ICC profiles, "
            "CoreDisplay presets and gamma through macOS display APIs."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"mac-displayctl {VERSION}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Read-only commands.
    p = sub.add_parser("list", help="list displays and current state")
    _add_query(p)
    _add_display(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("info", help="show detailed state (all displays by default)")
    _add_query(p)
    _add_display(p)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser(
        "modes",
        help="list Core Graphics Services (CGS) modes (resolution / refresh / pixel encoding)",
    )
    _add_query(p)
    _add_display(p)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--refresh", type=float)
    p.add_argument("--depth", type=int, help="bits per pixel, e.g. 30")
    p.add_argument(
        "--pixel-encoding",
        help="exact Core Graphics Services (CGS) pixel encoding string",
    )
    p.set_defaults(func=cmd_modes)

    p = sub.add_parser("colors", help="list Core Animation display color modes")
    _add_query(p)
    _add_display(p)
    _add_color_filter_args(p)
    p.add_argument("--current", action="store_true", help="show only the current mode")
    p.add_argument(
        "--limit", type=_non_negative_int, default=0, help="limit output rows"
    )
    p.set_defaults(func=cmd_colors)

    p = sub.add_parser("profiles", help="list ColorSync ICC profiles")
    _add_query(p)
    _add_display(p)
    p.add_argument(
        "--all", action="store_true", help="include non-RGB/non-monitor profiles"
    )
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("presets", help="list CoreDisplay reference presets")
    _add_query(p)
    _add_display(p)
    p.set_defaults(func=cmd_presets)

    # Changing commands.
    p = sub.add_parser(
        "set-mode",
        help="set a Core Graphics Services (CGS) mode (resolution / refresh / pixel encoding)",
    )
    _add_action(p)
    _add_display(p)
    p.add_argument(
        "--mode-id", type=int, help="exact Core Graphics Services (CGS) mode ID"
    )
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--refresh", type=float)
    p.add_argument("--depth", type=int, help="bits per pixel (8/16/30/32/...)")
    p.add_argument(
        "--pixel-encoding",
        help="exact Core Graphics Services (CGS) pixel encoding string",
    )
    p.set_defaults(func=cmd_set_mode)

    p = sub.add_parser(
        "set-color",
        help="set a Core Animation display mode (RGB/YCbCr, depth, range, HDR)",
    )
    _add_action(p)
    _add_display(p)
    p.add_argument("--index", type=int, help="index from `mac-displayctl colors`")
    _add_color_filter_args(p)
    p.add_argument(
        "--any-resolution",
        action="store_true",
        help="do not default to current resolution",
    )
    p.add_argument(
        "--any-refresh", action="store_true", help="do not default to current refresh"
    )
    p.add_argument("--list", action="store_true", help="list matching modes")
    p.add_argument(
        "--limit",
        type=_non_negative_int,
        default=0,
        help="limit output rows with --list",
    )
    p.set_defaults(func=cmd_set_color)

    p = sub.add_parser("set-profile", help="set a ColorSync ICC profile")
    _add_action(p)
    _add_display(p)
    p.add_argument("path", help="ICC profile path")
    p.set_defaults(func=cmd_set_profile)

    p = sub.add_parser("set-preset", help="activate a CoreDisplay reference preset")
    _add_action(p)
    _add_display(p)
    p.add_argument("target", help="preset index or name")
    p.set_defaults(func=cmd_set_preset)

    p = sub.add_parser("set-hdr", help="enable or disable HDR")
    _add_action(p)
    _add_display(p)
    p.add_argument("action", choices=["on", "off"])
    p.set_defaults(func=cmd_set_hdr)

    p = sub.add_parser("set-gamma", help="set a display gamma multiplier")
    _add_action(p)
    _add_display(p)
    p.add_argument("multiplier", type=float, help="gamma multiplier")
    p.set_defaults(func=cmd_set_gamma)

    p = sub.add_parser(
        "set-gamma-formula", help="set the nine display gamma formula values"
    )
    _add_action(p)
    _add_display(p)
    p.add_argument(
        "values",
        nargs=9,
        type=float,
        metavar="VALUE",
        help="rmin rmax rgamma gmin gmax ggamma bmin bmax bgamma",
    )
    p.set_defaults(func=cmd_set_gamma_formula)

    p = sub.add_parser("set-brightness", help="set DisplayServices brightness")
    _add_action(p)
    _add_display(p)
    p.add_argument("value", type=float, help="brightness 0.0 .. 1.0")
    p.set_defaults(func=cmd_set_brightness)

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
    except (RuntimeError, objc.ObjCError, system.SystemAPIError, OSError) as exc:
        _err(str(exc))
        if getattr(args, "debug", False):
            import traceback

            traceback.print_exc()
        return 1
    except Exception as exc:
        _err(f"unexpected error: {exc}")
        if getattr(args, "debug", False):
            import traceback

            traceback.print_exc()
        return 1
