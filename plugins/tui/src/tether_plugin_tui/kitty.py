"""Images over the kitty graphics protocol, drawn with Unicode placeholders.

https://sw.kovidgoyal.net/kitty/graphics-protocol/#unicode-placeholders

The image is sent to the terminal once and given a *virtual placement* of
`cols x rows` cells. The widget then draws ordinary text: one placeholder
character U+10EEEE per cell, whose foreground colour encodes the image id and
whose combining marks encode the cell's row and column. To Textual it is just
coloured text, so scrolling, clipping and redraws need no special handling.

Only the kitty protocol is supported, no Sixel. Terminals known to implement
placeholders: kitty and Ghostty.
"""

import base64
import fcntl
import itertools
import os
import random
import struct
import sys
import termios
from dataclasses import dataclass
from typing import Literal

from rich.style import Style
from rich.text import Text

type ImageMode = Literal["auto", "on", "off"]

PLACEHOLDER = "\U0010eeee"
CHUNK = 4096
"""Base64 bytes per escape sequence, the protocol's limit."""

# Row/column numbers as combining marks: kitty's gen/rowcolumn-diacritics.txt.
# fmt: off
_DIACRITIC_CODES = (
    0x0305, 0x030D, 0x030E, 0x0310, 0x0312, 0x033D, 0x033E, 0x033F, 0x0346, 0x034A, 0x034B, 0x034C, 0x0350,
    0x0351, 0x0352, 0x0357, 0x035B, 0x0363, 0x0364, 0x0365, 0x0366, 0x0367, 0x0368, 0x0369, 0x036A, 0x036B,
    0x036C, 0x036D, 0x036E, 0x036F, 0x0483, 0x0484, 0x0485, 0x0486, 0x0487, 0x0592, 0x0593, 0x0594, 0x0595,
    0x0597, 0x0598, 0x0599, 0x059C, 0x059D, 0x059E, 0x059F, 0x05A0, 0x05A1, 0x05A8, 0x05A9, 0x05AB, 0x05AC,
    0x05AF, 0x05C4, 0x0610, 0x0611, 0x0612, 0x0613, 0x0614, 0x0615, 0x0616, 0x0617, 0x0657, 0x0658, 0x0659,
    0x065A, 0x065B, 0x065D, 0x065E, 0x06D6, 0x06D7, 0x06D8, 0x06D9, 0x06DA, 0x06DB, 0x06DC, 0x06DF, 0x06E0,
    0x06E1, 0x06E2, 0x06E4, 0x06E7, 0x06E8, 0x06EB, 0x06EC, 0x0730, 0x0732, 0x0733, 0x0735, 0x0736, 0x073A,
    0x073D, 0x073F, 0x0740, 0x0741, 0x0743, 0x0745, 0x0747, 0x0749, 0x074A, 0x07EB, 0x07EC, 0x07ED, 0x07EE,
    0x07EF, 0x07F0, 0x07F1, 0x07F3, 0x0816, 0x0817, 0x0818, 0x0819, 0x081B, 0x081C, 0x081D, 0x081E, 0x081F,
    0x0820, 0x0821, 0x0822, 0x0823, 0x0825, 0x0826, 0x0827, 0x0829, 0x082A, 0x082B, 0x082C, 0x082D, 0x0951,
    0x0953, 0x0954, 0x0F82, 0x0F83, 0x0F86, 0x0F87, 0x135D, 0x135E, 0x135F, 0x17DD, 0x193A, 0x1A17, 0x1A75,
    0x1A76, 0x1A77, 0x1A78, 0x1A79, 0x1A7A, 0x1A7B, 0x1A7C, 0x1B6B, 0x1B6D, 0x1B6E, 0x1B6F, 0x1B70, 0x1B71,
    0x1B72, 0x1B73, 0x1CD0, 0x1CD1, 0x1CD2, 0x1CDA, 0x1CDB, 0x1CE0, 0x1DC0, 0x1DC1, 0x1DC3, 0x1DC4, 0x1DC5,
    0x1DC6, 0x1DC7, 0x1DC8, 0x1DC9, 0x1DCB, 0x1DCC, 0x1DD1, 0x1DD2, 0x1DD3, 0x1DD4, 0x1DD5, 0x1DD6, 0x1DD7,
    0x1DD8, 0x1DD9, 0x1DDA, 0x1DDB, 0x1DDC, 0x1DDD, 0x1DDE, 0x1DDF, 0x1DE0, 0x1DE1, 0x1DE2, 0x1DE3, 0x1DE4,
    0x1DE5, 0x1DE6, 0x1DFE, 0x20D0, 0x20D1, 0x20D4, 0x20D5, 0x20D6, 0x20D7, 0x20DB, 0x20DC, 0x20E1, 0x20E7,
    0x20E9, 0x20F0, 0x2CEF, 0x2CF0, 0x2CF1, 0x2DE0, 0x2DE1, 0x2DE2, 0x2DE3, 0x2DE4, 0x2DE5, 0x2DE6, 0x2DE7,
    0x2DE8, 0x2DE9, 0x2DEA, 0x2DEB, 0x2DEC, 0x2DED, 0x2DEE, 0x2DEF, 0x2DF0, 0x2DF1, 0x2DF2, 0x2DF3, 0x2DF4,
    0x2DF5, 0x2DF6, 0x2DF7, 0x2DF8, 0x2DF9, 0x2DFA, 0x2DFB, 0x2DFC, 0x2DFD, 0x2DFE, 0x2DFF, 0xA66F, 0xA67C,
    0xA67D, 0xA6F0, 0xA6F1, 0xA8E0, 0xA8E1, 0xA8E2, 0xA8E3, 0xA8E4, 0xA8E5, 0xA8E6, 0xA8E7, 0xA8E8, 0xA8E9,
    0xA8EA, 0xA8EB, 0xA8EC, 0xA8ED, 0xA8EE, 0xA8EF, 0xA8F0, 0xA8F1, 0xAAB0, 0xAAB2, 0xAAB3, 0xAAB7, 0xAAB8,
    0xAABE, 0xAABF, 0xAAC1, 0xFE20, 0xFE21, 0xFE22, 0xFE23, 0xFE24, 0xFE25, 0xFE26, 0x10A0F, 0x10A38,
    0x1D185, 0x1D186, 0x1D187, 0x1D188, 0x1D189, 0x1D1AA, 0x1D1AB, 0x1D1AC, 0x1D1AD, 0x1D242, 0x1D243,
    0x1D244,
)
# fmt: on
DIACRITICS = tuple(chr(c) for c in _DIACRITIC_CODES)

_ids = itertools.count(random.randrange(1, 1 << 23))
"""Image ids below 2**24, to fit a 24-bit colour; a random start avoids clashes with other apps."""


def supported(mode: ImageMode = "auto", env: dict[str, str] | None = None) -> bool:
    if mode != "auto":
        return mode == "on"
    env = dict(os.environ) if env is None else env
    term, program = env.get("TERM", ""), env.get("TERM_PROGRAM", "").lower()
    return (
        "KITTY_WINDOW_ID" in env or term == "xterm-kitty" or program == "ghostty" or term == "xterm-ghostty"
    )


def png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG header, or None if `data` is not a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def to_png(data: bytes) -> bytes | None:
    """PNG as is; other formats only if Pillow happens to be installed."""
    if png_size(data) is not None:
        return data
    try:
        import io

        from PIL import Image
    except ImportError:
        return None
    try:
        out = io.BytesIO()
        Image.open(io.BytesIO(data)).save(out, format="PNG")
    except Exception:
        return None
    return out.getvalue()


def cell_size() -> tuple[float, float]:
    """Pixel size of one terminal cell, from TIOCGWINSZ; a 1:2 guess when the terminal does not say."""
    try:
        rows, cols, xpix, ypix = struct.unpack(
            "HHHH", fcntl.ioctl(sys.__stdout__.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        )
        if rows and cols and xpix and ypix:
            return xpix / cols, ypix / rows
    except OSError, AttributeError, ValueError:
        pass
    return 10.0, 20.0


def fit(
    width: int, height: int, *, max_cols: int, max_rows: int, cell: tuple[float, float]
) -> tuple[int, int]:
    """Cells to give an image: native size at most, shrunk to the box, aspect ratio kept."""
    cell_w, cell_h = cell
    cols = width / cell_w
    rows = height / cell_h
    scale = min(1.0, max_cols / cols, max_rows / rows)
    return max(1, min(round(cols * scale), max_cols)), max(1, min(round(rows * scale), max_rows))


@dataclass
class KittyImage:
    png: bytes
    cols: int
    rows: int
    id: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = next(_ids)
        if self.cols > len(DIACRITICS) or self.rows > len(DIACRITICS):
            raise ValueError(f"at most {len(DIACRITICS)} cells per side")

    def transmit(self) -> str:
        """Upload the PNG and create its virtual placement (`U=1`); `q=2` keeps the terminal silent."""
        payload = base64.standard_b64encode(self.png).decode()
        chunks = [payload[i : i + CHUNK] for i in range(0, len(payload), CHUNK)] or [""]
        out = []
        for n, chunk in enumerate(chunks):
            more = int(n < len(chunks) - 1)
            if n == 0:
                keys = f"a=T,U=1,f=100,t=d,i={self.id},c={self.cols},r={self.rows},q=2,m={more}"
            else:
                keys = f"q=2,m={more}"
            out.append(f"\x1b_G{keys};{chunk}\x1b\\")
        return "".join(out)

    def delete(self) -> str:
        """Free the image and its placements in the terminal."""
        return f"\x1b_Ga=d,d=I,i={self.id},q=2\x1b\\"

    def lines(self) -> list[Text]:
        """The placeholder cells, one `Text` per row."""
        style = Style(color=f"#{self.id:06x}")
        return [
            Text(
                "".join(PLACEHOLDER + DIACRITICS[row] + DIACRITICS[col] for col in range(self.cols)),
                style=style,
            )
            for row in range(self.rows)
        ]
