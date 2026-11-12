# mac-displayctl

A small macOS command-line tool for display control:

- resolution, refresh rate and Core Graphics Services (CGS) pixel encoding
- RGB/YCbCr, chroma subsampling, 8/10/12-bit depth, full/limited range
- HDR on/off
- ColorSync ICC monitor profiles
- CoreDisplay reference presets
- gamma, brightness
- horizontal/vertical timing (total, blank, front porch, sync, back porch)

Implemented with the Python standard library (`ctypes`) only. `uv` pins and
manages the interpreter.

Requires macOS and Python 3.13+. The tool uses private macOS display APIs, so
behavior can vary by OS release.

## Quick start

```bash
uv python install 3.13
uv sync
uv run mac-displayctl list
uv run mac-displayctl info -d 2
uv run mac-displayctl modes -d 2
uv run mac-displayctl colors -d 2
```

## Commands

| Command | Purpose |
|---|---|
| `list [-d ID]` | one-line summary of every online display |
| `info [-d ID]` | detailed state for all displays, or one with `-d`, including current timing |
| `modes [-d ID]` | list Core Graphics Services (CGS) modes (resolution / refresh / pixel encoding) |
| `colors [-d ID]` | list Core Animation display color modes (format / depth / range / HDR) |
| `profiles [-d ID]` | list ColorSync ICC profiles |
| `presets [-d ID]` | list CoreDisplay reference presets |
| `set-mode [-d ID] ...` | set resolution, refresh or pixel encoding |
| `set-color [-d ID] ...` | set RGB/YCbCr, depth, subsampling, range, HDR |
| `set-profile [-d ID] PATH` | set an ICC profile |
| `set-preset [-d ID] N` | activate a reference preset |
| `set-hdr [-d ID] on\|off` | enable or disable HDR |
| `set-gamma [-d ID] N` | set a gamma multiplier |
| `set-gamma-formula [-d ID] ...` | set the nine transfer formula values |
| `set-brightness [-d ID] N` | set DisplayServices brightness |

Read-only commands (`list`, `info`, `modes`, `colors`, `profiles`, `presets`)
default to all online displays; use `-d ID` to narrow to one display. Changing
commands (`set-*`) default to the main display unless `-d ID` is given.
`info` reports the current profile, preset, gamma, brightness, HDR status and
timing for each display; `profiles` and `presets` list the installed sets.

## Examples

```bash
# resolution
uv run mac-displayctl set-mode -d 2 --width 1280 --height 720 --refresh 60 -y

# 8-bit SDR, YCbCr 4:2:2, limited range
uv run mac-displayctl set-color -d 2 \
  --hdr sdr --format ycbcr --subsampling 422 --bit-depth 8 --range limited -y

# 10-bit HDR10, YCbCr 4:2:2, limited range
uv run mac-displayctl set-color -d 2 \
  --hdr hdr10 --format ycbcr --subsampling 422 --bit-depth 10 --range limited -y

# 8-bit SDR, RGB 4:4:4, full range
uv run mac-displayctl set-color -d 2 \
  --hdr sdr --format rgb --bit-depth 8 --range full -y

# ICC profile
uv run mac-displayctl profiles
uv run mac-displayctl set-profile -d 2 "/Library/ColorSync/Profiles/Displays/MyDisplay.icc" -y
```

Use `--dry-run` to preview a change and `-y` to skip the confirmation prompt.
`--json` is available on listing/query commands. Multi-display queries return a
JSON array; `-d ID` returns a single object where applicable.

## License

[MIT](LICENSE)
