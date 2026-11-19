"""EDID parsing and retrieval.

The parser follows the interpretation used by ``learn-edid`` (itself a port of
the edid-decode rules): base block, display descriptors, CTA-861 and DisplayID
extensions.  Only :func:`read` talks to macOS; the rest is pure Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd, sqrt

from . import cg as cgs, corefoundation as cf, iokit

HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
BLOCK_SIZE = 128

PNP_VENDORS = {
    "ACR": "Acer",
    "AOC": "AOC",
    "APP": "Apple",
    "ASU": "ASUSTeK",
    "AUO": "AU Optronics",
    "BNQ": "BenQ",
    "CMN": "Chimei Innolux",
    "DEL": "Dell",
    "ELE": "Element",
    "GSM": "LG Electronics",
    "HKC": "HKC",
    "HPN": "HP",
    "HWP": "HP",
    "INL": "Innolux",
    "IVM": "Iiyama",
    "LEN": "Lenovo",
    "LGD": "LG Display",
    "MEI": "Panasonic",
    "MSI": "MSI",
    "PHL": "Philips",
    "SAM": "Samsung",
    "SDC": "Samsung Display",
    "SNY": "Sony",
    "VSC": "ViewSonic",
}

DIGITAL_DEPTHS = {1: 6, 2: 8, 3: 10, 4: 12, 5: 14, 6: 16}
DIGITAL_INTERFACES = {
    0: "undefined",
    1: "DVI",
    2: "HDMI-a",
    3: "HDMI-b",
    4: "MDDI",
    5: "DisplayPort",
}
ANALOG_LEVELS = {
    0: "0.700/0.300 V",
    1: "0.714/0.286 V",
    2: "1.000/0.400 V",
    3: "0.700/0.000 V",
}
DIGITAL_TYPES = [
    "RGB 4:4:4",
    "RGB 4:4:4 + YCbCr 4:4:4",
    "RGB 4:4:4 + YCbCr 4:2:2",
    "RGB 4:4:4 + YCbCr 4:4:4 + YCbCr 4:2:2",
]
ANALOG_TYPES = ["monochrome or grayscale", "RGB color", "non-RGB color", "undefined"]

# (byte index, bit) of the three established timing bytes.
ESTABLISHED = {
    (0, 7): "720x400 @ 70 Hz",
    (0, 6): "720x400 @ 88 Hz",
    (0, 5): "640x480 @ 60 Hz",
    (0, 4): "640x480 @ 67 Hz",
    (0, 3): "640x480 @ 72 Hz",
    (0, 2): "640x480 @ 75 Hz",
    (0, 1): "800x600 @ 56 Hz",
    (0, 0): "800x600 @ 60 Hz",
    (1, 7): "800x600 @ 72 Hz",
    (1, 6): "800x600 @ 75 Hz",
    (1, 5): "832x624 @ 75 Hz",
    (1, 4): "1024x768 @ 87 Hz (i)",
    (1, 3): "1024x768 @ 60 Hz",
    (1, 2): "1024x768 @ 70 Hz",
    (1, 1): "1024x768 @ 75 Hz",
    (1, 0): "1280x1024 @ 75 Hz",
    (2, 7): "1152x870 @ 75 Hz",
}

ESTABLISHED_III = {
    (0, 7): "640x350 @ 85 Hz",
    (0, 6): "640x400 @ 85 Hz",
    (0, 5): "720x400 @ 85 Hz",
    (0, 4): "640x480 @ 85 Hz",
    (0, 3): "848x480 @ 60 Hz",
    (0, 2): "800x600 @ 85 Hz",
    (0, 1): "1024x768 @ 85 Hz",
    (0, 0): "1152x864 @ 75 Hz",
    (1, 7): "1280x768 @ 60 Hz (RB)",
    (1, 6): "1280x768 @ 60 Hz",
    (1, 5): "1280x768 @ 75 Hz",
    (1, 4): "1280x800 @ 60 Hz (RB)",
    (1, 3): "1280x800 @ 60 Hz",
    (1, 2): "1280x800 @ 75 Hz",
    (1, 1): "1280x960 @ 60 Hz",
    (1, 0): "1280x960 @ 85 Hz",
    (2, 7): "1280x1024 @ 60 Hz",
    (2, 6): "1280x1024 @ 85 Hz",
    (2, 5): "1360x768 @ 60 Hz",
    (2, 4): "1440x900 @ 60 Hz (RB)",
    (2, 3): "1440x900 @ 60 Hz",
    (2, 2): "1440x900 @ 75 Hz",
    (2, 1): "1400x1050 @ 60 Hz (RB)",
    (2, 0): "1400x1050 @ 60 Hz",
    (3, 7): "1400x1050 @ 75 Hz",
    (3, 6): "1400x1050 @ 85 Hz",
    (3, 5): "1680x1050 @ 60 Hz (RB)",
    (3, 4): "1680x1050 @ 60 Hz",
    (3, 3): "1680x1050 @ 75 Hz",
    (3, 2): "1600x1200 @ 60 Hz",
    (3, 1): "1600x1200 @ 65 Hz",
    (3, 0): "1600x1200 @ 70 Hz",
    (4, 7): "1600x1200 @ 75 Hz",
    (4, 6): "1600x1200 @ 85 Hz",
    (4, 5): "1792x1344 @ 60 Hz",
    (4, 4): "1792x1344 @ 75 Hz",
    (4, 3): "1856x1392 @ 60 Hz",
    (4, 2): "1856x1392 @ 75 Hz",
    (4, 1): "1920x1200 @ 60 Hz (RB)",
    (4, 0): "1920x1200 @ 60 Hz",
    (5, 7): "1920x1200 @ 75 Hz",
    (5, 6): "1920x1440 @ 60 Hz",
    (5, 5): "1920x1440 @ 75 Hz",
}

STANDARD_ASPECTS = {0: (16, 10), 1: (4, 3), 2: (5, 4), 3: (16, 9)}

SYNC_TYPES = {
    0: "analog composite",
    1: "bipolar analog composite",
    2: "digital composite",
    3: "digital separate",
}

STEREO_MODES = {
    (0, 0): "none",
    (0, 1): "none",
    (1, 0): "field sequential, right image during stereo sync",
    (2, 0): "field sequential, left image during stereo sync",
    (1, 1): "2-way interleaved, right image on even lines",
    (2, 1): "2-way interleaved, left image on even lines",
    (3, 0): "4-way interleaved",
    (3, 1): "side-by-side interleaved",
}

RANGE_FLAGS = {
    0x00: "default GTF",
    0x01: "no timing information",
    0x02: "secondary GTF",
    0x04: "CVT",
}

EXTENSION_TAGS = {
    0x00: "Timing Extension",
    0x02: "CTA-861",
    0x10: "VTB-EXT",
    0x20: "EDID 2.0 Extension",
    0x40: "DI-EXT",
    0x50: "LS-EXT",
    0x60: "MI-EXT",
    0x70: "DisplayID",
    0xA7: "DTCDB",
    0xAF: "DTCDB",
    0xBF: "DTCDB",
    0xF0: "Block Map",
    0xFF: "DDDB",
}

AUDIO_FORMATS = {
    1: "LPCM",
    2: "AC-3",
    3: "MPEG-1",
    4: "MP3",
    5: "MPEG-2",
    6: "AAC LC",
    7: "DTS",
    8: "ATRAC",
    9: "DSD",
    10: "E-AC-3",
    11: "DTS-HD",
    12: "MLP",
    13: "DST",
    14: "WMA Pro",
    15: "extension",
}
AUDIO_RATES = [(0, "32"), (1, "44.1"), (2, "48"), (3, "88.2"), (4, "96"),
               (5, "176.4"), (6, "192")]
AUDIO_DEPTHS = [(0, "16"), (1, "20"), (2, "24")]
AUDIO_EXT_FORMATS = {
    4: "MPEG-4 HE AAC",
    5: "MPEG-4 HE AAC v2",
    6: "MPEG-4 AAC LC",
    7: "DRA",
    8: "MPEG-4 HE AAC + MPEG Surround",
    10: "MPEG-4 HE AAC LC + MPEG Surround",
    11: "MPEG-H 3D Audio",
    12: "AC-4",
    13: "LPCM 3D Audio",
    14: "Auro-Cx",
    15: "MPEG-D USAC",
}

SPEAKER_NAMES = [
    "FL/FR",
    "LFE",
    "FC",
    "RL/RR",
    "RC",
    "FLC/FRC",
    "RLC/RRC",
    "FLW/FRW",
    "FLH/FRH",
    "TC",
    "FCH",
]

OVERSCAN_BEHAVIOR = [
    "no data",
    "always overscanned",
    "always underscanned",
    "supports both overscan and underscan",
]

COLORIMETRY_NAMES = [
    (0, "xvYCC601"),
    (1, "xvYCC709"),
    (2, "sYCC601"),
    (3, "opYCC601"),
    (4, "opRGB"),
    (5, "BT2020 cYCC"),
    (6, "BT2020 YCC"),
    (7, "BT2020 RGB"),
]

EOTF_NAMES = [
    (0, "SDR (traditional gamma)"),
    (1, "HDR (traditional gamma)"),
    (2, "SMPTE ST 2084 (PQ)"),
    (3, "HLG"),
]

HDR_DYNAMIC_TYPES = {
    1: "SMPTE ST 2094-10",
    2: "SMPTE ST 2094-20",
    3: "SMPTE ST 2094-30",
    4: "SMPTE ST 2094-40",
}

EXTENDED_TAGS = {
    0x00: "Video Capability",
    0x01: "Vendor-Specific Video",
    0x02: "VESA Display Device",
    0x03: "VESA Video Timing Block",
    0x04: "reserved for HDMI",
    0x05: "Colorimetry",
    0x06: "HDR Static Metadata",
    0x07: "HDR Dynamic Metadata",
    0x08: "Native Video Resolution",
    0x0D: "Video Format Preference",
    0x0E: "YCbCr 4:2:0 Video",
    0x0F: "YCbCr 4:2:0 Capability Map",
    0x10: "Miscellaneous Audio Fields",
    0x11: "Vendor-Specific Audio",
    0x12: "HDMI Audio",
    0x13: "Room Configuration",
    0x14: "Speaker Location",
    0x20: "InfoFrame",
    0x22: "Type VII Video Timing",
    0x23: "Type VIII Video Timing",
    0x2A: "Type X Video Timing",
    0x78: "HDMI Forum EDID Extension Override",
    0x79: "HDMI Forum Sink Capability",
    0x7A: "HDMI Forum Source-Based Tone Mapping",
}

OUI_NAMES = {
    "000c03": "HDMI Licensing (HDMI 1.4b VSDB)",
    "c45dd8": "HDMI Forum (HDMI 2.x VSDB)",
    "00001a": "AMD (FreeSync)",
    "00d046": "Dolby Labs",
    "90848b": "HDR10+ Technologies",
}

HDMI_VICS = {
    1: "3840x2160p @ 29.97/30 Hz (16:9)",
    2: "3840x2160p @ 25 Hz (16:9)",
    3: "3840x2160p @ 23.98/24 Hz (16:9)",
    4: "4096x2160p @ 23.98/24 Hz (256:135)",
}

HDMI_FORUM_FRL_RATES = [
    "not supported",
    "3 lanes @ 3 Gbps",
    "3 lanes @ 6 Gbps",
    "4 lanes @ 6 Gbps",
    "4 lanes @ 8 Gbps",
    "4 lanes @ 10 Gbps",
    "4 lanes @ 12 Gbps",
]

# (VIC, format, field rate, display aspect ratio), CTA-861.
_CEA_VICS = [
    (1, "640x480p", "59.94/60", "4:3"),
    (2, "720x480p", "59.94/60", "4:3"),
    (3, "720x480p", "59.94/60", "16:9"),
    (4, "1280x720p", "59.94/60", "16:9"),
    (5, "1920x1080i", "59.94/60", "16:9"),
    (6, "720(1440)x480i", "59.94/60", "4:3"),
    (7, "720(1440)x480i", "59.94/60", "16:9"),
    (8, "720(1440)x240p", "59.94/60", "4:3"),
    (9, "720(1440)x240p", "59.94/60", "16:9"),
    (10, "2880x480i", "59.94/60", "4:3"),
    (11, "2880x480i", "59.94/60", "16:9"),
    (12, "2880x240p", "59.94/60", "4:3"),
    (13, "2880x240p", "59.94/60", "16:9"),
    (14, "1440x480p", "59.94/60", "4:3"),
    (15, "1440x480p", "59.94/60", "16:9"),
    (16, "1920x1080p", "59.94/60", "16:9"),
    (17, "720x576p", "50", "4:3"),
    (18, "720x576p", "50", "16:9"),
    (19, "1280x720p", "50", "16:9"),
    (20, "1920x1080i", "50", "16:9"),
    (21, "720(1440)x576i", "50", "4:3"),
    (22, "720(1440)x576i", "50", "16:9"),
    (23, "720(1440)x288p", "50", "4:3"),
    (24, "720(1440)x288p", "50", "16:9"),
    (25, "2880x576i", "50", "4:3"),
    (26, "2880x576i", "50", "16:9"),
    (27, "2880x288p", "50", "4:3"),
    (28, "2880x288p", "50", "16:9"),
    (29, "1440x576p", "50", "4:3"),
    (30, "1440x576p", "50", "16:9"),
    (31, "1920x1080p", "50", "16:9"),
    (32, "1920x1080p", "23.98/24", "16:9"),
    (33, "1920x1080p", "25", "16:9"),
    (34, "1920x1080p", "29.97/30", "16:9"),
    (35, "2880x480p", "59.94/60", "4:3"),
    (36, "2880x480p", "59.94/60", "16:9"),
    (37, "2880x576p", "50", "4:3"),
    (38, "2880x576p", "50", "16:9"),
    (39, "1920x1080i (1250 total)", "50", "16:9"),
    (40, "1920x1080i", "100", "16:9"),
    (41, "1280x720p", "100", "16:9"),
    (42, "720x576p", "100", "4:3"),
    (43, "720x576p", "100", "16:9"),
    (44, "720(1440)x576i", "100", "4:3"),
    (45, "720(1440)x576i", "100", "16:9"),
    (46, "1920x1080i", "119.88/120", "16:9"),
    (47, "1280x720p", "119.88/120", "16:9"),
    (48, "720x480p", "119.88/120", "4:3"),
    (49, "720x480p", "119.88/120", "16:9"),
    (50, "720(1440)x480i", "119.88/120", "4:3"),
    (51, "720(1440)x480i", "119.88/120", "16:9"),
    (52, "720x576p", "200", "4:3"),
    (53, "720x576p", "200", "16:9"),
    (54, "720(1440)x576i", "200", "4:3"),
    (55, "720(1440)x576i", "200", "16:9"),
    (56, "720x480p", "239.76/240", "4:3"),
    (57, "720x480p", "239.76/240", "16:9"),
    (58, "720(1440)x480i", "239.76/240", "4:3"),
    (59, "720(1440)x480i", "239.76/240", "16:9"),
    (60, "1280x720p", "23.98/24", "16:9"),
    (61, "1280x720p", "25", "16:9"),
    (62, "1280x720p", "29.97/30", "16:9"),
    (63, "1920x1080p", "119.88/120", "16:9"),
    (64, "1920x1080p", "100", "16:9"),
    (65, "1280x720p", "23.98/24", "64:27"),
    (66, "1280x720p", "25", "64:27"),
    (67, "1280x720p", "29.97/30", "64:27"),
    (68, "1280x720p", "50", "64:27"),
    (69, "1280x720p", "59.94/60", "64:27"),
    (70, "1280x720p", "100", "64:27"),
    (71, "1280x720p", "119.88/120", "64:27"),
    (72, "1920x1080p", "23.98/24", "64:27"),
    (73, "1920x1080p", "25", "64:27"),
    (74, "1920x1080p", "29.97/30", "64:27"),
    (75, "1920x1080p", "50", "64:27"),
    (76, "1920x1080p", "59.94/60", "64:27"),
    (77, "1920x1080p", "100", "64:27"),
    (78, "1920x1080p", "119.88/120", "64:27"),
    (79, "1680x720p", "23.98/24", "64:27"),
    (80, "1680x720p", "25", "64:27"),
    (81, "1680x720p", "29.97/30", "64:27"),
    (82, "1680x720p", "50", "64:27"),
    (83, "1680x720p", "59.94/60", "64:27"),
    (84, "1680x720p", "100", "64:27"),
    (85, "1680x720p", "119.88/120", "64:27"),
    (86, "2560x1080p", "23.98/24", "64:27"),
    (87, "2560x1080p", "25", "64:27"),
    (88, "2560x1080p", "29.97/30", "64:27"),
    (89, "2560x1080p", "50", "64:27"),
    (90, "2560x1080p", "59.94/60", "64:27"),
    (91, "2560x1080p", "100", "64:27"),
    (92, "2560x1080p", "119.88/120", "64:27"),
    (93, "3840x2160p", "23.98/24", "16:9"),
    (94, "3840x2160p", "25", "16:9"),
    (95, "3840x2160p", "29.97/30", "16:9"),
    (96, "3840x2160p", "50", "16:9"),
    (97, "3840x2160p", "59.94/60", "16:9"),
    (98, "4096x2160p", "23.98/24", "256:135"),
    (99, "4096x2160p", "25", "256:135"),
    (100, "4096x2160p", "29.97/30", "256:135"),
    (101, "4096x2160p", "50", "256:135"),
    (102, "4096x2160p", "59.94/60", "256:135"),
    (103, "3840x2160p", "23.98/24", "64:27"),
    (104, "3840x2160p", "25", "64:27"),
    (105, "3840x2160p", "29.97/30", "64:27"),
    (106, "3840x2160p", "50", "64:27"),
    (107, "3840x2160p", "59.94/60", "64:27"),
    (108, "1280x720p", "48", "16:9"),
    (109, "1280x720p", "48", "64:27"),
    (110, "1680x720p", "48", "64:27"),
    (111, "1920x1080p", "48", "16:9"),
    (112, "1920x1080p", "48", "64:27"),
    (113, "2560x1080p", "48", "64:27"),
    (114, "3840x2160p", "48", "16:9"),
    (115, "4096x2160p", "48", "256:135"),
    (116, "3840x2160p", "48", "64:27"),
    (117, "3840x2160p", "100", "16:9"),
    (118, "3840x2160p", "119.88/120", "16:9"),
    (119, "3840x2160p", "100", "64:27"),
    (120, "3840x2160p", "119.88/120", "64:27"),
    (121, "5120x2160p", "24", "64:27"),
    (122, "5120x2160p", "25", "64:27"),
    (123, "5120x2160p", "30", "64:27"),
    (124, "5120x2160p", "48", "64:27"),
    (125, "5120x2160p", "50", "64:27"),
    (126, "5120x2160p", "60", "64:27"),
    (127, "5120x2160p", "100", "64:27"),
    (193, "5120x2160p", "120", "64:27"),
    (194, "7680x4320p", "24", "16:9"),
    (195, "7680x4320p", "25", "16:9"),
    (196, "7680x4320p", "30", "16:9"),
    (197, "7680x4320p", "48", "16:9"),
    (198, "7680x4320p", "50", "16:9"),
    (199, "7680x4320p", "60", "16:9"),
    (200, "7680x4320p", "100", "16:9"),
    (201, "7680x4320p", "120", "16:9"),
    (202, "7680x4320p", "24", "64:27"),
    (203, "7680x4320p", "25", "64:27"),
    (204, "7680x4320p", "30", "64:27"),
    (205, "7680x4320p", "48", "64:27"),
    (206, "7680x4320p", "50", "64:27"),
    (207, "7680x4320p", "60", "64:27"),
    (208, "7680x4320p", "100", "64:27"),
    (209, "7680x4320p", "120", "64:27"),
    (210, "10240x4320p", "24", "64:27"),
    (211, "10240x4320p", "25", "64:27"),
    (212, "10240x4320p", "30", "64:27"),
    (213, "10240x4320p", "48", "64:27"),
    (214, "10240x4320p", "50", "64:27"),
    (215, "10240x4320p", "60", "64:27"),
    (216, "10240x4320p", "100", "64:27"),
    (217, "10240x4320p", "120", "64:27"),
    (218, "4096x2160p", "100", "256:135"),
    (219, "4096x2160p", "120", "256:135"),
]

VIC_MODES = {
    vic: f"{fmt} @ {rate} Hz ({aspect})" for vic, fmt, rate, aspect in _CEA_VICS
}

DISPLAYID_TAGS = {
    0x00: "Product Identification",
    0x01: "Display Parameters",
    0x02: "Color Characteristics",
    0x03: "Type 1 Detailed Timings",
    0x04: "Type 2 Standard Timings",
    0x05: "Type 3 Short Timings",
    0x06: "Type 4 DMT Timings",
    0x07: "VESA Timing",
    0x08: "CTA Timing",
    0x09: "Dynamic Video Timing Range Limits",
    0x0A: "Product Serial Number",
    0x0B: "General Purpose ASCII String",
    0x0C: "Display Device Data",
    0x0D: "Interface Power Sequencing",
    0x0E: "Transfer Characteristics",
    0x0F: "Display Interface",
    0x10: "Stereo Display Interface",
    0x12: "Tiled Display",
    0x20: "Product Identification (v2)",
    0x21: "Display Parameters (v2)",
    0x22: "Type 7 Detailed Timings",
    0x23: "Type 8 Enumerated Timings",
    0x24: "Type 9 Formula Timings",
    0x25: "Dynamic Video Timing (v2)",
    0x26: "Display Interface Features",
    0x27: "Stereo Display Interface (v2)",
    0x28: "Tiled Display Topology",
    0x29: "Container ID",
    0x7E: "Vendor-Specific (v2)",
    0x7F: "Vendor-Specific",
    0x81: "CTA DisplayID",
}

DISPLAYID_PRIMARY_USE = {
    0: "extension section",
    1: "test",
    2: "generic",
    3: "TV",
    4: "desktop productivity",
    5: "desktop gaming",
    6: "presentation",
    7: "head-mounted VR",
    8: "head-mounted AR",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DetailedTiming:
    pixel_clock_khz: int
    h_active: int
    h_blank: int
    h_front: int
    h_sync: int
    v_active: int
    v_blank: int
    v_front: int
    v_sync: int
    h_size_mm: int
    v_size_mm: int
    h_border: int
    v_border: int
    h_polarity: str | None
    v_polarity: str | None
    sync_type: str
    stereo: str
    interlaced: bool
    refresh_hz: float
    preferred: bool = False

    @property
    def h_total(self) -> int:
        return self.h_active + self.h_blank

    @property
    def v_total(self) -> int:
        return self.v_active + self.v_blank

    @property
    def h_back(self) -> int:
        return self.h_total - self.h_active - self.h_front - self.h_sync

    @property
    def v_back(self) -> int:
        return self.v_total - self.v_active - self.v_front - self.v_sync

    @property
    def v_frame_active(self) -> int:
        return self.v_active * 2 if self.interlaced else self.v_active

    def to_dict(self) -> dict:
        data = {
            "pixel_clock_mhz": round(self.pixel_clock_khz / 1000, 4),
            # Active/blank/total are the per-field counts stored in the DTD.
            "active": [self.h_active, self.v_active],
            "blank": [self.h_blank, self.v_blank],
            "front_porch": [self.h_front, self.v_front],
            "sync_width": [self.h_sync, self.v_sync],
            "back_porch": [self.h_back, self.v_back],
            "total": [self.h_total, self.v_total],
            "sync_polarity": [self.h_polarity, self.v_polarity],
            "sync_type": self.sync_type,
            "stereo": self.stereo,
            "refresh_hz": round(self.refresh_hz, 4),
            "size_mm": [self.h_size_mm, self.v_size_mm],
            "border": [self.h_border, self.v_border],
            "interlaced": self.interlaced,
            "preferred": self.preferred,
        }
        if self.interlaced:
            data["frame_active"] = [self.h_active, self.v_frame_active]
        return data


@dataclass(slots=True)
class MonitorRange:
    min_vertical_hz: int
    max_vertical_hz: int
    min_horizontal_khz: int
    max_horizontal_khz: int
    max_clock_mhz: int
    formula: str

    def to_dict(self) -> dict:
        return {
            "vertical_hz": [self.min_vertical_hz, self.max_vertical_hz],
            "horizontal_khz": [self.min_horizontal_khz, self.max_horizontal_khz],
            "max_clock_mhz": self.max_clock_mhz,
            "formula": self.formula,
        }


@dataclass(slots=True)
class Descriptor:
    kind: str
    text: str | None = None
    timing: DetailedTiming | None = None
    range_limits: MonitorRange | None = None

    def to_dict(self) -> dict:
        data: dict = {"kind": self.kind}
        if self.text is not None:
            data["text"] = self.text
        if self.timing is not None:
            data["timing"] = self.timing.to_dict()
        if self.range_limits is not None:
            data["range_limits"] = self.range_limits.to_dict()
        return data


@dataclass(slots=True)
class Extension:
    tag: int
    name: str
    checksum_ok: bool
    cta: dict | None = None
    displayid: dict | None = None


def _cta_dict(cta: dict) -> dict:
    data = dict(cta)
    data["detailed_timings"] = [timing.to_dict() for timing in cta["detailed_timings"]]
    if cta["y420_capable"]:
        data["y420_capable_modes"] = list(
            dict.fromkeys(_video_label(vic) for vic in cta["y420_capable"])
        )
    return data


def _displayid_dict(displayid: dict) -> dict:
    data = dict(displayid)
    data["detailed_timings"] = [
        timing.to_dict() for timing in displayid["detailed_timings"]
    ]
    return data


@dataclass(slots=True)
class EDIDInfo:
    length: int
    block_count: int
    declared_extensions: int
    header_ok: bool
    checksums: list[bool]
    version: str
    manufacturer_id: str
    manufacturer_name: str | None
    product_code: int
    serial_number: int
    week: int | None
    year: int | None
    digital: bool
    bit_depth: int | None
    interface: str | None
    analog: dict | None
    width_cm: int
    height_cm: int
    diagonal_inches: float | None
    aspect: str
    gamma: float | None
    features: dict
    chromaticity: dict
    established: list[str]
    standard: list[str]
    descriptors: list[Descriptor] = field(default_factory=list)
    extensions: list[Extension] = field(default_factory=list)

    @property
    def product_name(self) -> str | None:
        for descriptor in self.descriptors:
            if descriptor.kind == "monitor name":
                return descriptor.text
        return None

    @property
    def serial_string(self) -> str | None:
        for descriptor in self.descriptors:
            if descriptor.kind == "serial number":
                return descriptor.text
        return None

    @property
    def detailed_timings(self) -> list[DetailedTiming]:
        return [d.timing for d in self.descriptors if d.timing is not None]

    @property
    def preferred_timing(self) -> DetailedTiming | None:
        for timing in self.detailed_timings:
            if timing.preferred:
                return timing
        return None

    @property
    def range_limits(self) -> MonitorRange | None:
        for descriptor in self.descriptors:
            if descriptor.range_limits is not None:
                return descriptor.range_limits
        return None

    @property
    def manufacture_text(self) -> str | None:
        if not self.year:
            return None
        if self.week == 0xFF:
            return f"model year {self.year}"
        if self.week:
            return f"week {self.week}, {self.year}"
        return str(self.year)

    def to_dict(self) -> dict:
        timings = self.detailed_timings
        return {
            "length": self.length,
            "blocks": self.block_count,
            "declared_extensions": self.declared_extensions,
            "header_ok": self.header_ok,
            "checksums_ok": all(self.checksums),
            "block_checksums": self.checksums,
            "version": self.version,
            "vendor": {"id": self.manufacturer_id, "name": self.manufacturer_name},
            "product": self.product_code,
            "serial": self.serial_number,
            "manufactured": {
                "week": self.week,
                "year": self.year,
                "text": self.manufacture_text,
            },
            "input": {
                "digital": self.digital,
                "bit_depth": self.bit_depth,
                "interface": self.interface,
                "analog": self.analog,
            },
            "screen": {
                "width_cm": self.width_cm,
                "height_cm": self.height_cm,
                "diagonal_inches": (
                    round(self.diagonal_inches, 2) if self.diagonal_inches else None
                ),
                "aspect": self.aspect,
            },
            "gamma": self.gamma,
            "features": self.features,
            "chromaticity": self.chromaticity,
            "established_timings": self.established,
            "standard_timings": self.standard,
            "product_name": self.product_name,
            "serial_string": self.serial_string,
            "preferred_timing": (
                self.preferred_timing.to_dict() if self.preferred_timing else None
            ),
            "detailed_timings": [timing.to_dict() for timing in timings],
            "range_limits": (
                self.range_limits.to_dict() if self.range_limits else None
            ),
            "text_descriptors": [
                {"kind": descriptor.kind, "text": descriptor.text}
                for descriptor in self.descriptors
                if descriptor.timing is None
                and descriptor.range_limits is None
                and descriptor.text is not None
                and descriptor.kind != "dummy"
            ],
            "extensions": [
                {
                    "name": extension.name,
                    "checksum_ok": extension.checksum_ok,
                    **(
                        {"cta": _cta_dict(extension.cta)}
                        if extension.cta
                        else {}
                    ),
                    **(
                        {"displayid": _displayid_dict(extension.displayid)}
                        if extension.displayid
                        else {}
                    ),
                }
                for extension in self.extensions
            ],
        }


# ---------------------------------------------------------------------------
# Reading from the system
# ---------------------------------------------------------------------------

TRANSPORT_CLASS = "IOPortTransportState"


def read(display: int) -> bytes | None:
    """Return the EDID blob the system has for ``display``, if any."""
    return read_all([display]).get(display)


def read_all(displays: list[int]) -> dict[int, bytes]:
    """Map display IDs to their EDID blobs.

    Older systems expose ``IODisplayEDID`` on the display's IOKit service,
    which ``CGDisplayIOServicePort`` resolves directly.  On Apple Silicon that
    call returns 0, so fall back to the ``EDID`` property the DCP transport
    nodes publish, matched to the CoreGraphics display by vendor/product/
    serial number.
    """
    found: dict[int, bytes] = {}
    for display in displays:
        data = _service_edid(display)
        if data:
            found[display] = data
    missing = [display for display in displays if display not in found]
    if missing:
        _assign(_transport_edids(), missing, found)
    return found


def _service_edid(display: int) -> bytes | None:
    try:
        service = cgs.CG.CGDisplayIOServicePort(display)
    except AttributeError:
        # Removed from newer releases; the transport-node path covers them.
        return None
    if not service:
        return None
    data = _registry_bytes(service, "IODisplayEDID")
    if data:
        return data
    info_ref = iokit.display_info(service)
    if not info_ref:
        return None
    try:
        return cf.data_bytes(cf.dict_get(info_ref, "IODisplayEDID")) or None
    finally:
        cf.release(info_ref)


def _transport_edids() -> list[bytes]:
    blobs: list[bytes] = []
    seen: set[bytes] = set()
    for service in iokit.services(TRANSPORT_CLASS):
        data = _registry_bytes(service, "EDID")
        if data is None:
            metadata = iokit.property_of(service, "Metadata")
            if metadata:
                try:
                    data = cf.data_bytes(cf.dict_get(metadata, "EDID"))
                finally:
                    cf.release(metadata)
        if data and data not in seen:
            seen.add(data)
            blobs.append(data)
    return blobs


def _registry_bytes(service: int, key: str) -> bytes | None:
    ref = iokit.property_of(service, key)
    if not ref:
        return None
    try:
        return cf.data_bytes(ref)
    finally:
        cf.release(ref)


def _identity(data: bytes) -> tuple[int, int, int]:
    """(vendor, product, serial) from an EDID image."""
    if len(data) < 16:
        return (0, 0, 0)
    vendor = (data[8] << 8) | data[9]
    product_le = data[10] | (data[11] << 8)
    serial_le = int.from_bytes(data[12:16], "little")
    return vendor, product_le, serial_le


def _score(data: bytes, flags: dict) -> int:
    if len(data) < 16 or data[:8] != HEADER:
        return 0
    vendor, product_le, serial_le = _identity(data)
    product_be = (data[10] << 8) | data[11]
    serial_be = int.from_bytes(data[12:16], "big")
    score = 0
    if vendor and vendor == flags["vendor_id"]:
        score += 2
    if product_le and flags["product_id"] in (product_le, product_be):
        score += 4
    if serial_le and flags["serial"] in (serial_le, serial_be):
        score += 8
    return score


def _assign(candidates: list[bytes], displays: list[int], found: dict[int, bytes]) -> None:
    identities = {display: cgs.display_flags(display) for display in displays}
    scored = []
    for index, data in enumerate(candidates):
        for display in displays:
            score = _score(data, identities[display])
            if score >= 4:
                scored.append((score, index, display))
    scored.sort(reverse=True)
    used: set[int] = set()
    for _, index, display in scored:
        if display in found or index in used:
            continue
        found[display] = candidates[index]
        used.add(index)


# ---------------------------------------------------------------------------
# Base block
# ---------------------------------------------------------------------------


def _le16(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def _text(chunk: bytes) -> str:
    return chunk.split(b"\x0a")[0].replace(b"\x00", b"").decode("ascii", "replace").strip()


def _checksum_ok(block: bytes) -> bool:
    return sum(block) % 256 == 0


def _aspect_text(width: int, height: int) -> str:
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _parse_chromaticity(base: bytes) -> dict:
    high = base[27:35]

    def coord(byte: int, shift: int, index: int) -> float:
        return ((high[index] << 2) | ((byte >> shift) & 0x03)) / 1024.0

    return {
        "red": (coord(base[25], 6, 0), coord(base[25], 4, 1)),
        "green": (coord(base[25], 2, 2), coord(base[25], 0, 3)),
        "blue": (coord(base[26], 6, 4), coord(base[26], 4, 5)),
        "white": (coord(base[26], 2, 6), coord(base[26], 0, 7)),
    }


def _parse_established(base: bytes) -> list[str]:
    modes = []
    for byte_index in range(3):
        value = base[35 + byte_index]
        for bit in range(7, -1, -1):
            name = ESTABLISHED.get((byte_index, bit))
            if name and value & (1 << bit):
                modes.append(name)
    return modes


def _parse_established_iii(block: bytes) -> list[str]:
    modes = []
    for byte_index in range(6):
        value = block[6 + byte_index]
        for bit in range(7, -1, -1):
            name = ESTABLISHED_III.get((byte_index, bit))
            if name and value & (1 << bit):
                modes.append(name)
    return modes


def _parse_standard(base: bytes) -> list[str]:
    timings = []
    for index in range(8):
        low, high = base[38 + index * 2], base[39 + index * 2]
        if (low, high) in ((0x00, 0x00), (0x00, 0x01), (0x01, 0x00), (0x01, 0x01)):
            continue
        width = (low + 31) * 8
        numerator, denominator = STANDARD_ASPECTS[(high >> 6) & 0x03]
        height = width * denominator // numerator
        timings.append(f"{width}x{height} @ {(high & 0x3F) + 60} Hz")
    return timings


def _parse_dtd(block: bytes) -> DetailedTiming | None:
    if len(block) < 18 or not _le16(block, 0):
        return None
    pixel_clock_khz = _le16(block, 0) * 10
    h_active = ((block[4] >> 4) << 8) | block[2]
    h_blank = ((block[4] & 0x0F) << 8) | block[3]
    v_active = ((block[7] >> 4) << 8) | block[5]
    v_blank = ((block[7] & 0x0F) << 8) | block[6]
    h_front = ((block[11] >> 6) << 8) | block[8]
    h_sync = (((block[11] >> 4) & 0x03) << 8) | block[9]
    v_front = (((block[11] >> 2) & 0x03) << 4) | (block[10] >> 4)
    v_sync = ((block[11] & 0x03) << 4) | (block[10] & 0x0F)
    h_size = ((block[14] >> 4) << 8) | block[12]
    v_size = ((block[14] & 0x0F) << 8) | block[13]
    flags = block[17]
    interlaced = bool(flags & 0x80)
    h_total = h_active + h_blank
    v_total = v_active + v_blank
    # An interlaced field spans one half-line more than v_total.
    v_period = v_total + 0.5 if interlaced else v_total
    refresh = (
        pixel_clock_khz * 1000 / (h_total * v_period)
        if h_total and v_period
        else 0.0
    )
    # The polarity bits only mean sync polarity for digital separate sync;
    # elsewhere they are serration / sync-on-green flags.
    sync_code = (flags >> 3) & 0x03
    return DetailedTiming(
        pixel_clock_khz=pixel_clock_khz,
        h_active=h_active,
        h_blank=h_blank,
        h_front=h_front,
        h_sync=h_sync,
        v_active=v_active,
        v_blank=v_blank,
        v_front=v_front,
        v_sync=v_sync,
        h_size_mm=h_size,
        v_size_mm=v_size,
        h_border=block[15],
        v_border=block[16],
        h_polarity=("+" if flags & 0x02 else "-") if sync_code == 3 else None,
        v_polarity=("+" if flags & 0x04 else "-") if sync_code == 3 else None,
        sync_type=SYNC_TYPES[sync_code],
        stereo=STEREO_MODES.get(((flags >> 5) & 0x03, flags & 0x01), "none"),
        interlaced=interlaced,
        refresh_hz=refresh,
    )


def _parse_range(block: bytes) -> MonitorRange:
    offsets = block[4]
    vertical_offset = offsets & 0x03
    horizontal_offset = (offsets >> 2) & 0x03
    min_vertical, max_vertical = block[5], block[6]
    min_horizontal, max_horizontal = block[7], block[8]
    if vertical_offset >= 2:
        max_vertical += 255
    if vertical_offset == 3:
        min_vertical += 255
    if horizontal_offset >= 2:
        max_horizontal += 255
    if horizontal_offset == 3:
        min_horizontal += 255
    return MonitorRange(
        min_vertical_hz=min_vertical,
        max_vertical_hz=max_vertical,
        min_horizontal_khz=min_horizontal,
        max_horizontal_khz=max_horizontal,
        max_clock_mhz=block[9] * 10,
        formula=RANGE_FLAGS.get(block[10], f"0x{block[10]:02x}"),
    )


def _parse_color_point(block: bytes) -> str:
    def point(offset: int) -> str:
        x = (((block[offset + 2] >> 2) & 0x03) << 8 | block[offset + 1]) / 1024.0
        y = (((block[offset + 2] & 0x03) << 8) | block[offset + 3]) / 1024.0
        gamma = (block[offset + 4] | (block[offset + 5] << 8)) / 100.0
        return f"#{block[offset]} x={x:.4f} y={y:.4f} gamma={gamma:.2f}"

    points = [point(5)]
    if block[11] not in (0x00, 0xFF):
        points.append(point(11))
    return "; ".join(points)


def _parse_display_descriptor(block: bytes) -> Descriptor:
    tag = block[3]
    payload = block[5:18]
    if tag in (0x00, 0x10):
        return Descriptor(kind="dummy")
    if tag == 0xFF:
        return Descriptor(kind="serial number", text=_text(payload))
    if tag == 0xFE:
        return Descriptor(kind="unspecified text", text=_text(payload))
    if tag == 0xFC:
        return Descriptor(kind="monitor name", text=_text(payload))
    if tag == 0xFD:
        return Descriptor(kind="range limits", range_limits=_parse_range(block))
    if tag == 0xFB:
        return Descriptor(kind="color point", text=_parse_color_point(block))
    if tag == 0xF7:
        modes = _parse_established_iii(block)
        return Descriptor(kind="established timings III", text=", ".join(modes))
    if tag == 0xFA:
        return Descriptor(kind="standard timings")
    if tag == 0xF9:
        return Descriptor(kind="color management")
    if tag == 0xF8:
        return Descriptor(kind="CVT 3-byte codes")
    return Descriptor(kind=f"unknown tag 0x{tag:02x}", text=payload.hex())


def _parse_descriptor(block: bytes) -> Descriptor:
    if block[0] == 0 and block[1] == 0 and block[2] == 0:
        return _parse_display_descriptor(block)
    timing = _parse_dtd(block)
    if timing is None:
        return Descriptor(kind="reserved")
    return Descriptor(kind="detailed timing", timing=timing)


# ---------------------------------------------------------------------------
# CTA-861 extension
# ---------------------------------------------------------------------------


def _video_label(vic: int) -> str:
    return VIC_MODES.get(vic, f"VIC {vic}")


def _video_labels(vics: list[int]) -> str:
    """VIC list to mode names, dropping repeats of the same VIC."""
    return ", ".join(dict.fromkeys(_video_label(vic) for vic in vics))


def _video_entry(vic: int, native: bool) -> dict:
    return {"vic": vic, "native": native, "mode": _video_label(vic)}


def _parse_svds(payload: bytes) -> list[tuple[int, bool]]:
    entries = []
    for byte in payload:
        if not byte:
            continue
        if byte & 0x40:
            entries.append((byte, False))
        else:
            entries.append((byte & 0x3F, bool(byte & 0x80)))
    return entries


def _parse_audio(payload: bytes) -> list[dict]:
    entries = []
    for index in range(0, len(payload) - 2, 3):
        header = payload[index]
        fmt = (header >> 3) & 0x0F
        entry = {
            "format": AUDIO_FORMATS.get(fmt, f"codec {fmt}"),
            "channels": (header & 0x07) + 1,
            "rates": [
                rate for bit, rate in AUDIO_RATES if payload[index + 1] & (1 << bit)
            ],
        }
        value = payload[index + 2]
        if fmt == 1:
            entry["depths"] = [
                depth for bit, depth in AUDIO_DEPTHS if value & (1 << bit)
            ]
        elif 2 <= fmt <= 8:
            entry["max_bitrate_kbps"] = value * 8
        elif fmt == 15:
            extended = value >> 3
            entry["extended_format"] = AUDIO_EXT_FORMATS.get(
                extended, f"reserved {extended}"
            )
        entries.append(entry)
    return entries


def _parse_speakers(payload: bytes) -> list[str]:
    value = 0
    for index, byte in enumerate(payload[:3]):
        value |= byte << (8 * index)
    return [name for bit, name in enumerate(SPEAKER_NAMES) if value & (1 << bit)]


def _parse_colorimetry(body: bytes) -> list[str]:
    if not body:
        return []
    return [name for bit, name in COLORIMETRY_NAMES if body[0] & (1 << bit)]


def _hdr_luminance(code: int) -> float:
    return 50.0 * (2 ** (code / 32.0))


def _parse_hdr_static(body: bytes) -> dict:
    info: dict = {
        "eotfs": [name for bit, name in EOTF_NAMES if body and body[0] & (1 << bit)],
        "static_metadata_type1": bool(len(body) > 1 and body[1] & 0x01),
    }
    if len(body) >= 5 and info["static_metadata_type1"]:
        max_luminance = _hdr_luminance(body[2])
        info["max_luminance_cd_m2"] = round(max_luminance, 1)
        info["avg_luminance_cd_m2"] = round(_hdr_luminance(body[3]), 1)
        info["min_luminance_cd_m2"] = round(
            max_luminance * (body[4] / 255.0) ** 2 / 100.0, 3
        )
    return info


def _parse_video_capability(body: bytes) -> str:
    if not body:
        return "no data"
    byte = body[0]
    return (
        f"YCbCr quantization={'selectable' if byte & 0x80 else 'no data'}, "
        f"RGB quantization={'selectable' if byte & 0x40 else 'no data'}, "
        f"picture timing={OVERSCAN_BEHAVIOR[(byte >> 4) & 0x03]}, "
        f"IT content={OVERSCAN_BEHAVIOR[(byte >> 2) & 0x03]}, "
        f"CE content={OVERSCAN_BEHAVIOR[byte & 0x03]}"
    )


def _parse_hdmi_vsdb(body: bytes) -> str:
    if len(body) < 2:
        return "too short"
    first, second = body[0], body[1]
    parts = [
        "source physical address "
        f"{(first >> 4) & 0xF}.{first & 0xF}.{(second >> 4) & 0xF}.{second & 0xF}"
    ]
    if len(body) >= 3:
        flags = body[2]
        names = [
            name
            for bit, name in (
                (0x80, "AI"),
                (0x40, "DC_48bit"),
                (0x20, "DC_36bit"),
                (0x10, "DC_30bit"),
                (0x08, "DC_Y444"),
                (0x01, "DVI_Dual"),
            )
            if flags & bit
        ]
        parts.append("deep color=" + (", ".join(names) if names else "none"))
    if len(body) >= 4:
        parts.append(f"max TMDS clock {body[3] * 5} MHz")
    if len(body) >= 5 and body[4] & 0x20:
        offset = 5
        if body[4] & 0x80:
            offset += 2
        if body[4] & 0x40:
            offset += 2
        if len(body) >= offset + 2:
            count = (body[offset + 1] >> 5) & 0x07
            vics = body[offset + 2 : offset + 2 + count]
            if vics:
                parts.append(
                    "HDMI VICs="
                    + ", ".join(f"{vic} ({HDMI_VICS.get(vic, 'unknown')})" for vic in vics)
                )
    return "; ".join(parts)


def _parse_hdmi_forum(body: bytes) -> str:
    if not body:
        return "no data"
    parts = [f"version {body[0]}"]
    if len(body) >= 2:
        parts.append(f"max TMDS character rate {body[1] * 5} MHz")
    if len(body) >= 4:
        frl = (body[3] >> 4) & 0x0F
        rate = HDMI_FORUM_FRL_RATES[frl] if frl < len(HDMI_FORUM_FRL_RATES) else f"reserved {frl}"
        parts.append(f"max FRL rate {rate}")
    if len(body) >= 7:
        vrr_min = body[5] & 0x3F
        vrr_max = ((body[5] & 0xC0) << 2) | body[6]
        if vrr_min or vrr_max:
            parts.append(f"VRR {vrr_min}-{vrr_max} Hz")
    if len(body) >= 8:
        names = [
            name
            for bit, name in (
                (0x80, "DSC 1.2a"),
                (0x40, "compressed 4:2:0"),
                (0x08, "any bits per pixel"),
                (0x04, "16 bpc"),
                (0x02, "12 bpc"),
                (0x01, "10 bpc"),
            )
            if body[7] & bit
        ]
        if names:
            parts.append("DSC=" + ", ".join(names))
    return "; ".join(parts)


def _parse_freesync(body: bytes) -> str:
    if not body:
        return "no data"
    parts = []
    if len(body) >= 2:
        parts.append(f"version {body[0]}.{body[1]}")
    if len(body) >= 4:
        parts.append(f"refresh {body[2]}-{body[3]} Hz")
    if len(body) >= 7:
        max_luminance = _hdr_luminance(body[6])
        parts.append(f"max luminance {max_luminance:.1f} cd/m²")
        if len(body) >= 8:
            code = body[7]
            parts.append(
                f"min luminance {max_luminance * (code / 255.0) ** 2 / 100.0:.3f} cd/m²"
            )
    return "; ".join(parts) or "no data"


def _parse_vendor(payload: bytes) -> dict:
    oui = payload[:3][::-1].hex() if len(payload) >= 3 else ""
    body = payload[3:]
    if oui == "000c03":
        detail = _parse_hdmi_vsdb(body)
    elif oui == "c45dd8":
        detail = _parse_hdmi_forum(body)
    elif oui == "00001a":
        detail = _parse_freesync(body)
    else:
        detail = body.hex()
    return {"oui": oui, "name": OUI_NAMES.get(oui, "unknown"), "detail": detail}


def _parse_extended(tag: int, body: bytes) -> dict:
    name = EXTENDED_TAGS.get(tag, f"unknown 0x{tag:02x}")
    if tag == 0x00:
        detail = _parse_video_capability(body)
    elif tag == 0x07:
        detail = HDR_DYNAMIC_TYPES.get(body[0], f"type {body[0]}") if body else "none"
    elif tag == 0x0D:
        detail = ", ".join(f"VIC {byte & 0x3F}" for byte in body) or "none"
    else:
        detail = body.hex()
    return {"tag": tag, "name": name, "detail": detail}


def _parse_cta(block: bytes) -> dict:
    revision, dtd_offset, flags = block[1], block[2], block[3]
    info: dict = {
        "revision": revision,
        "underscan": bool(flags & 0x80),
        "basic_audio": bool(flags & 0x40),
        "ycbcr444": bool(flags & 0x20),
        "ycbcr422": bool(flags & 0x10),
        "native_dtds": flags & 0x0F,
        "video": [],
        "y420_video": [],
        "y420_capable": [],
        "audio": [],
        "speakers": [],
        "colorimetry": [],
        "hdr_static": None,
        "vendor": [],
        "extended": [],
        "detailed_timings": [],
    }
    end = min(dtd_offset or len(block), len(block))
    video_svds: list[tuple[int, bool]] = []
    position = 4
    while position < end:
        header = block[position]
        if not header:
            break
        tag = header >> 5
        length = header & 0x1F
        payload = block[position + 1 : position + 1 + length]
        if len(payload) < length:
            break
        if tag == 1:
            info["audio"].extend(_parse_audio(payload))
        elif tag == 2:
            video_svds = _parse_svds(payload)
            info["video"] = [_video_entry(vic, native) for vic, native in video_svds]
        elif tag == 3:
            info["vendor"].append(_parse_vendor(payload))
        elif tag == 4:
            info["speakers"] = _parse_speakers(payload)
        elif tag == 7 and payload:
            ext_tag = payload[0]
            body = payload[1:]
            if ext_tag == 0x05:
                info["colorimetry"] = _parse_colorimetry(body)
            elif ext_tag == 0x06:
                info["hdr_static"] = _parse_hdr_static(body)
            elif ext_tag == 0x0E:
                info["y420_video"] = [
                    _video_entry(vic, False) for vic, _ in _parse_svds(body)
                ]
            elif ext_tag == 0x0F:
                capable = []
                for index, (vic, _) in enumerate(video_svds):
                    if index // 8 < len(body) and body[index // 8] & (1 << (index % 8)):
                        capable.append(vic)
                info["y420_capable"] = capable
            else:
                info["extended"].append(_parse_extended(ext_tag, body))
        position += 1 + length
    if dtd_offset:
        position = dtd_offset
        while position + 18 <= len(block):
            timing = _parse_dtd(block[position : position + 18])
            if timing is None:
                break
            info["detailed_timings"].append(timing)
            position += 18
    return info


# ---------------------------------------------------------------------------
# DisplayID extension
# ---------------------------------------------------------------------------


def _parse_displayid_timings(data: bytes, type7: bool) -> list[DetailedTiming]:
    timings = []
    for offset in range(0, len(data) - 19, 20):
        raw = data[offset : offset + 20]
        clock = (raw[0] | (raw[1] << 8) | (raw[2] << 16)) + 1
        pixel_clock_khz = clock if type7 else clock * 10
        flags = raw[3]
        h_active = (raw[4] | (raw[5] << 8)) + 1
        h_blank = (raw[6] | (raw[7] << 8)) + 1
        h_front = (raw[8] | ((raw[9] & 0x7F) << 8)) + 1
        h_sync = (raw[10] | (raw[11] << 8)) + 1
        v_active = (raw[12] | (raw[13] << 8)) + 1
        v_blank = (raw[14] | (raw[15] << 8)) + 1
        v_front = (raw[16] | ((raw[17] & 0x7F) << 8)) + 1
        v_sync = (raw[18] | (raw[19] << 8)) + 1
        h_total = h_active + h_blank
        v_total = v_active + v_blank
        interlaced = bool(flags & 0x01)
        v_period = v_total + 0.5 if interlaced else v_total
        timings.append(
            DetailedTiming(
                pixel_clock_khz=pixel_clock_khz,
                h_active=h_active,
                h_blank=h_blank,
                h_front=h_front,
                h_sync=h_sync,
                v_active=v_active,
                v_blank=v_blank,
                v_front=v_front,
                v_sync=v_sync,
                h_size_mm=0,
                v_size_mm=0,
                h_border=0,
                v_border=0,
                h_polarity="+" if raw[9] & 0x80 else "-",
                v_polarity="+" if raw[17] & 0x80 else "-",
                sync_type="digital separate",
                stereo="none",
                interlaced=interlaced,
                refresh_hz=(
                    pixel_clock_khz * 1000 / (h_total * v_period)
                    if h_total and v_period
                    else 0.0
                ),
                preferred=bool(flags & 0x80),
            )
        )
    return timings


def _parse_displayid(block: bytes) -> dict:
    version = block[1]
    info: dict = {
        "version": f"{version >> 4}.{version & 0x0F}",
        "primary_use": DISPLAYID_PRIMARY_USE.get(block[3], f"unknown ({block[3]})"),
        "blocks": [],
        "detailed_timings": [],
    }
    end = min(5 + block[2], len(block))
    position = 5
    while position + 3 <= end:
        tag, revision, length = block[position], block[position + 1], block[position + 2]
        if not length:
            break
        payload = block[position + 3 : position + 3 + length]
        if len(payload) < length:
            break
        name = DISPLAYID_TAGS.get(tag, f"tag 0x{tag:02x}")
        if tag in (0x03, 0x22):
            timings = _parse_displayid_timings(payload, type7=tag == 0x22)
            info["detailed_timings"].extend(timings)
            info["blocks"].append(
                {"tag": name, "revision": revision, "timings": len(timings)}
            )
        else:
            info["blocks"].append(
                {"tag": name, "revision": revision, "data": payload.hex()}
            )
        position += 3 + length
    return info


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def parse(data: bytes) -> EDIDInfo:
    if len(data) < BLOCK_SIZE:
        raise ValueError(f"EDID is too short ({len(data)} bytes)")
    base = data[:BLOCK_SIZE]
    block_count = len(data) // BLOCK_SIZE
    checksums = [
        _checksum_ok(data[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE])
        for index in range(block_count)
    ]
    major, minor = base[18], base[19]
    version = f"{major}.{minor}"

    manufacturer_raw = (base[8] << 8) | base[9]
    letters = []
    for shift in (10, 5, 0):
        code = (manufacturer_raw >> shift) & 0x1F
        letters.append(chr(code + 0x40) if 1 <= code <= 26 else "?")
    manufacturer_id = "".join(letters)

    digital = bool(base[20] & 0x80)
    bit_depth = None
    interface = None
    analog = None
    if digital:
        if (major, minor) >= (1, 4):
            bit_depth = DIGITAL_DEPTHS.get((base[20] >> 4) & 0x07)
            interface = DIGITAL_INTERFACES.get(base[20] & 0x0F, "reserved")
    else:
        analog = {
            "signal_level": ANALOG_LEVELS.get((base[20] >> 5) & 0x03, "reserved"),
            "blank_to_black": bool(base[20] & 0x10),
            "separate_sync": bool(base[20] & 0x08),
            "composite_sync": bool(base[20] & 0x04),
            "sync_on_green": bool(base[20] & 0x02),
            "serration": bool(base[20] & 0x01),
        }

    features_byte = base[24]
    display_types = DIGITAL_TYPES if digital and (major, minor) >= (1, 4) else ANALOG_TYPES
    features = {
        "standby": bool(features_byte & 0x80),
        "suspend": bool(features_byte & 0x40),
        "active_off": bool(features_byte & 0x20),
        "display_type": display_types[(features_byte >> 3) & 0x03],
        "srgb": bool(features_byte & 0x04),
        "preferred_timing": bool(features_byte & 0x02),
        "continuous_timing": bool(features_byte & 0x01),
    }

    width_cm, height_cm = base[21], base[22]
    if width_cm and height_cm:
        diagonal: float | None = sqrt(width_cm**2 + height_cm**2) / 2.54
        aspect = _aspect_text(width_cm, height_cm)
    elif width_cm:
        diagonal = None
        aspect = f"landscape {((width_cm + 99) / 100):.2f}:1"
    elif height_cm:
        diagonal = None
        aspect = f"portrait {100 / (height_cm + 99):.2f}:1"
    else:
        diagonal = None
        aspect = "undefined"

    descriptors = [
        _parse_descriptor(base[offset : offset + 18])
        for offset in range(54, 54 + 4 * 18, 18)
    ]
    timings = [descriptor.timing for descriptor in descriptors if descriptor.timing]
    if timings and features["preferred_timing"]:
        timings[0].preferred = True

    declared_extensions = base[126]
    extensions = []
    for index in range(1, block_count):
        block = data[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        tag = block[0]
        extension = Extension(
            tag=tag,
            name=EXTENSION_TAGS.get(tag, f"unknown 0x{tag:02x}"),
            checksum_ok=checksums[index],
        )
        if tag == 0x02:
            extension.cta = _parse_cta(block)
        elif tag == 0x70:
            extension.displayid = _parse_displayid(block)
        extensions.append(extension)

    return EDIDInfo(
        length=len(data),
        block_count=block_count,
        declared_extensions=declared_extensions,
        header_ok=base[:8] == HEADER,
        checksums=checksums,
        version=version,
        manufacturer_id=manufacturer_id,
        manufacturer_name=PNP_VENDORS.get(manufacturer_id),
        product_code=_le16(base, 10),
        serial_number=int.from_bytes(base[12:16], "little"),
        week=None if base[16] == 0 else base[16],
        year=None if base[17] == 0 else base[17] + 1990,
        digital=digital,
        bit_depth=bit_depth,
        interface=interface,
        analog=analog,
        width_cm=width_cm,
        height_cm=height_cm,
        diagonal_inches=diagonal,
        aspect=aspect,
        gamma=None if base[23] == 0xFF else (base[23] + 100) / 100.0,
        features=features,
        chromaticity=_parse_chromaticity(base),
        established=_parse_established(base),
        standard=_parse_standard(base),
        descriptors=descriptors,
        extensions=extensions,
    )


def validate(data: bytes) -> str | None:
    """Return why ``data`` is not a usable EDID image, or None if it is."""
    if len(data) < BLOCK_SIZE or len(data) % BLOCK_SIZE:
        return (
            f"EDID must be a non-empty multiple of {BLOCK_SIZE} bytes "
            f"(got {len(data)})"
        )
    if data[:8] != HEADER:
        return "invalid EDID header"
    blocks = len(data) // BLOCK_SIZE
    for index in range(blocks):
        block = data[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        if not _checksum_ok(block):
            return f"block {index} has a bad checksum"
    declared = data[126]
    if declared != blocks - 1:
        return f"declares {declared} extension block(s) but contains {blocks - 1}"
    return None


# ---------------------------------------------------------------------------
# Image files
# ---------------------------------------------------------------------------

HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def decode_image(raw: bytes) -> tuple[bytes, str]:
    """Decode an EDID image from raw file contents.

    A binary image (the 128-byte blocks themselves) is returned unchanged.
    Text made of hex digits with spaces or newlines in between is decoded as
    hex.  Returns ``(data, encoding)`` with *encoding* either ``"binary"`` or
    ``"hex"``.

    Raises :class:`ValueError` when the contents are text but decode as neither
    binary EDID blocks nor hex.
    """
    text = _as_text(raw)
    if text is not None:
        decoded = _decode_hex(text)
        if decoded is not None:
            return decoded, "hex"
        raise ValueError(
            "not an EDID image: expected 128-byte blocks with an EDID header, "
            "or hex text"
        )
    return raw, "binary"


def _as_text(raw: bytes) -> str | None:
    """``raw`` as text when it is printable ASCII, else ``None``."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if any(ord(char) < 32 and char not in "\t\n\v\f\r" for char in text):
        return None
    return text


def _decode_hex(text: str) -> bytes | None:
    """Decode hex text, ignoring whitespace, or return ``None`` if not hex."""
    hex_text = "".join(text.split())
    if not hex_text:
        return None
    if any(char not in HEX_DIGITS for char in hex_text):
        return None
    if len(hex_text) % 2:
        raise ValueError(f"hex EDID has an odd number of digits ({len(hex_text)})")
    return bytes.fromhex(hex_text)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _timing_text(timing: DetailedTiming, detail: bool = False) -> str:
    scan = "i" if timing.interlaced else "p"
    parts = [
        f"{timing.h_active}x{timing.v_frame_active}{scan} "
        f"@ {timing.refresh_hz:.3f} Hz",
        f"{timing.pixel_clock_khz / 1000:.3f} MHz",
    ]
    if detail:
        polarity = (
            f" Hpol {timing.h_polarity} Vpol {timing.v_polarity}"
            if timing.sync_type == "digital separate"
            else ""
        )
        parts.append(
            f"Hfront {timing.h_front} Hsync {timing.h_sync} "
            f"Hback {timing.h_back} (h-total {timing.h_total})"
            f"{polarity}"
        )
        parts.append(
            f"Vfront {timing.v_front} Vsync {timing.v_sync} "
            f"Vback {timing.v_back} (v-total {timing.v_total})"
        )
        if timing.h_size_mm and timing.v_size_mm:
            parts.append(f"{timing.h_size_mm}x{timing.v_size_mm} mm")
    return ", ".join(parts)


def _range_text(range_limits: MonitorRange) -> str:
    return (
        f"{range_limits.min_vertical_hz}-{range_limits.max_vertical_hz} Hz vertical, "
        f"{range_limits.min_horizontal_khz}-{range_limits.max_horizontal_khz} kHz horizontal, "
        f"{range_limits.max_clock_mhz} MHz max clock ({range_limits.formula})"
    )


def _manufacturer_text(info: EDIDInfo) -> str:
    if info.manufacturer_name:
        return f"{info.manufacturer_id} ({info.manufacturer_name})"
    return info.manufacturer_id


def _input_text(info: EDIDInfo) -> str:
    if info.digital:
        parts = ["digital"]
        if info.interface:
            parts.append(info.interface)
        if info.bit_depth:
            parts.append(f"{info.bit_depth} bit/color")
        return ", ".join(parts)
    parts = ["analog"]
    analog = info.analog or {}
    if analog.get("signal_level"):
        parts.append(analog["signal_level"])
    for key in (
        "separate_sync",
        "composite_sync",
        "sync_on_green",
        "serration",
        "blank_to_black",
    ):
        if analog.get(key):
            parts.append(key.replace("_", " "))
    return ", ".join(parts)


def _size_text(info: EDIDInfo) -> str:
    if not (info.width_cm and info.height_cm) or info.diagonal_inches is None:
        return f"undefined ({info.aspect})"
    return (
        f"{info.width_cm} x {info.height_cm} cm "
        f"({info.diagonal_inches:.1f}\", {info.aspect})"
    )


def _features_text(info: EDIDInfo) -> str:
    features = info.features
    parts = [features["display_type"]]
    if features["srgb"]:
        parts.append("sRGB")
    if features["preferred_timing"]:
        parts.append("preferred timing")
    if features["continuous_timing"]:
        parts.append("continuous frequency")
    for key in ("standby", "suspend", "active_off"):
        if features[key]:
            parts.append(key.replace("_", "-"))
    return ", ".join(parts)


def _chromaticity_text(info: EDIDInfo) -> str:
    points = info.chromaticity
    return ", ".join(
        f"{name} ({points[name][0]:.4f}, {points[name][1]:.4f})"
        for name in ("red", "green", "blue", "white")
    )


def _split_items(value: str) -> list[str]:
    """Split on top-level ``", "`` so parenthesis contents stay together."""
    items = []
    depth = 0
    start = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0 and value.startswith(", ", index):
            items.append(value[start:index])
            start = index + 2
    items.append(value[start:])
    return items


def _wrap_value(value: str, width: int) -> list[str]:
    if len(value) <= width:
        return [value]
    lines = []
    current = ""
    for item in _split_items(value):
        candidate = f"{current}, {item}" if current else item
        if not current or len(candidate) <= width:
            current = candidate
        else:
            lines.append(current + ",")
            current = item
    if current:
        lines.append(current)
    return lines


def _fields(
    rows: list[tuple[str, str]], indent: int = 2, line_width: int = 100
) -> list[str]:
    if not rows:
        return []
    width = max(len(name) for name, _ in rows)
    padding = " " * indent
    continuation = " " * (indent + width + 2)
    available = max(20, line_width - len(continuation))
    lines = []
    for name, value in rows:
        wrapped = _wrap_value(value, available)
        lines.append(f"{padding}{name.ljust(width)}  {wrapped[0]}")
        lines.extend(f"{continuation}{part}" for part in wrapped[1:])
    return lines


def _audio_text(entries: list[dict]) -> str:
    parts = []
    for entry in entries:
        text = f"{entry['format']} {entry['channels']} ch"
        if entry.get("rates"):
            text += f" ({'/'.join(entry['rates'])} kHz)"
        if entry.get("depths"):
            text += f" {'/'.join(entry['depths'])} bit"
        if entry.get("max_bitrate_kbps"):
            text += f" {entry['max_bitrate_kbps']} kbit/s"
        if entry.get("extended_format"):
            text += f" {entry['extended_format']}"
        parts.append(text)
    return "; ".join(parts) or "none"


def _hdr_text(hdr: dict) -> str:
    parts = [f"EOTF {', '.join(hdr['eotfs']) or 'none'}"]
    if "max_luminance_cd_m2" in hdr:
        parts.append(f"max {hdr['max_luminance_cd_m2']:.1f} cd/m²")
        parts.append(f"frame avg {hdr['avg_luminance_cd_m2']:.1f} cd/m²")
        parts.append(f"min {hdr['min_luminance_cd_m2']:.3f} cd/m²")
    return ", ".join(parts)


def _descriptor_rows(info: EDIDInfo) -> list[tuple[str, str]]:
    rows = []
    timing_index = 0
    for descriptor in info.descriptors:
        if descriptor.timing is not None:
            label = "Preferred timing" if descriptor.timing.preferred else f"DTD {timing_index}"
            rows.append((label, _timing_text(descriptor.timing, detail=True)))
            timing_index += 1
        elif descriptor.range_limits is not None:
            rows.append(("Range limits", _range_text(descriptor.range_limits)))
        elif descriptor.text is not None:
            label = descriptor.kind[0].upper() + descriptor.kind[1:]
            quoted = descriptor.kind in (
                "monitor name",
                "serial number",
                "unspecified text",
            )
            rows.append((label, f'"{descriptor.text}"' if quoted else descriptor.text))
        elif descriptor.kind != "dummy":
            label = descriptor.kind[0].upper() + descriptor.kind[1:]
            rows.append((label, ""))
    return rows


def _cta_section(cta: dict) -> list[str]:
    flags = [
        label
        for key, label in (
            ("underscan", "underscan"),
            ("basic_audio", "basic audio"),
            ("ycbcr444", "YCbCr 4:4:4"),
            ("ycbcr422", "YCbCr 4:2:2"),
        )
        if cta[key]
    ]
    if cta["native_dtds"]:
        flags.append(f"{cta['native_dtds']} native DTDs")
    lines = [
        f"  CTA-861 revision {cta['revision']}: " + (", ".join(flags) or "no flags")
    ]
    rows: list[tuple[str, str]] = []
    if cta["video"]:
        rows.append(
            (
                "Video",
                ", ".join(
                    entry["mode"] + (" [native]" if entry["native"] else "")
                    for entry in cta["video"]
                ),
            )
        )
    if cta["y420_video"]:
        rows.append(("4:2:0 video", ", ".join(e["mode"] for e in cta["y420_video"])))
    if cta["y420_capable"]:
        rows.append(("4:2:0 capable", _video_labels(cta["y420_capable"])))
    if cta["audio"]:
        rows.append(("Audio", _audio_text(cta["audio"])))
    if cta["speakers"]:
        rows.append(("Speakers", ", ".join(cta["speakers"])))
    if cta["colorimetry"]:
        rows.append(("Colorimetry", ", ".join(cta["colorimetry"])))
    if cta["hdr_static"]:
        rows.append(("HDR static", _hdr_text(cta["hdr_static"])))
    for vendor in cta["vendor"]:
        rows.append(("Vendor", f"{vendor['oui']} {vendor['name']}: {vendor['detail']}"))
    for extended in cta["extended"]:
        rows.append((extended["name"], extended["detail"]))
    for timing in cta["detailed_timings"]:
        rows.append(("DTD", _timing_text(timing)))
    lines.extend(_fields(rows, indent=4))
    return lines


def _displayid_section(displayid: dict) -> list[str]:
    lines = [
        f"  DisplayID {displayid['version']} ({displayid['primary_use']})"
    ]
    rows = []
    for block in displayid["blocks"]:
        detail = f"timings {block['timings']}" if "timings" in block else block.get("data", "")
        rows.append((block["tag"], f"revision {block['revision']}, {detail}".rstrip(", ")))
    for timing in displayid["detailed_timings"]:
        rows.append(("DTD", _timing_text(timing)))
    lines.extend(_fields(rows, indent=4))
    return lines


def report(info: EDIDInfo) -> list[str]:
    """Full human-readable EDID report (one line per entry)."""
    status = "OK" if all(info.checksums) else "BAD"
    header = "header OK" if info.header_ok else "HEADER BAD"
    lines = [
        f"EDID {info.version}, {info.block_count} block(s), {info.length} bytes, "
        f"{header}, checksums {status}"
    ]
    rows: list[tuple[str, str]] = [
        ("Manufacturer", _manufacturer_text(info)),
        ("Product code", f"0x{info.product_code:04x} ({info.product_code})"),
    ]
    if info.serial_number:
        rows.append(("Serial number", f"0x{info.serial_number:08x}"))
    if info.manufacture_text:
        rows.append(("Manufactured", info.manufacture_text))
    rows.extend(
        [
            ("Video input", _input_text(info)),
            ("Physical size", _size_text(info)),
            ("Gamma", "undefined" if info.gamma is None else f"{info.gamma:.2f}"),
            ("Features", _features_text(info)),
            ("Chromaticity", _chromaticity_text(info)),
            ("Established", ", ".join(info.established) or "none"),
            ("Standard", ", ".join(info.standard) or "none"),
        ]
    )
    rows.extend(_descriptor_rows(info))
    extensions = ", ".join(
        f"{extension.name} (checksum {'OK' if extension.checksum_ok else 'BAD'})"
        for extension in info.extensions
    )
    if info.declared_extensions != len(info.extensions):
        extensions += (
            f" (declared {info.declared_extensions}, "
            f"present {len(info.extensions)})"
        )
    rows.append(("Extensions", extensions or "none"))
    lines.extend(_fields(rows))
    for extension in info.extensions:
        if extension.cta:
            lines.extend(_cta_section(extension.cta))
        if extension.displayid:
            lines.extend(_displayid_section(extension.displayid))
    return lines


def summary(info: EDIDInfo) -> list[str]:
    """Compact EDID summary for the ``info`` command."""
    status = "checksums OK" if all(info.checksums) else "CHECKSUM MISMATCH"
    header = "header OK" if info.header_ok else "HEADER BAD"
    lines = [
        f"EDID {info.version}, {info.block_count} block(s), {info.length} bytes, "
        f"{header}, {status}"
    ]
    identity = [_manufacturer_text(info), f"product 0x{info.product_code:04x}"]
    if info.serial_number:
        identity.append(f"serial 0x{info.serial_number:08x}")
    if info.manufacture_text:
        identity.append(info.manufacture_text)
    lines.append(", ".join(identity))

    basic = ["digital" if info.digital else "analog"]
    if info.interface:
        basic.append(info.interface)
    if info.bit_depth:
        basic.append(f"{info.bit_depth} bit/color")
    if info.width_cm and info.height_cm:
        basic.append(f"{info.width_cm}x{info.height_cm} cm")
    if info.gamma is not None:
        basic.append(f"gamma {info.gamma:.2f}")
    lines.append(", ".join(basic))

    if info.preferred_timing:
        lines.append("preferred " + _timing_text(info.preferred_timing))
    if info.range_limits:
        lines.append(_range_text(info.range_limits))

    extras = []
    if info.product_name:
        extras.append(f'name "{info.product_name}"')
    if info.extensions:
        extras.append("extensions " + ", ".join(e.name for e in info.extensions))
    if extras:
        lines.append(", ".join(extras))
    return lines


def hexdump(data: bytes, indent: str = "    ") -> list[str]:
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{indent}{offset:04x}  {hex_part:<47}  {ascii_part}")
    return lines
