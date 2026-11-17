"""Renders resources/app-icon.svg into a multi-size .ico for the exe, shortcuts and installer.

Qt's ICO writer stores a single image, so the ICO container is written here: an ICONDIR
header plus one PNG-compressed entry per size (supported by Windows since Vista).

Usage: python packaging/make_icon.py <output.ico>
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SVG = Path(__file__).resolve().parents[1] / "src" / "mk96_app_pc_rocket_tool" / "resources" / "app-icon.svg"
ICONDIR = struct.Struct("<HHH")  # reserved, type (1 = icon), image count
ICONDIRENTRY = struct.Struct("<BBBBHHII")  # w, h, colors, reserved, planes, bpp, size, offset


def render_png(size: int, renderer: QSvgRenderer) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data().data())


def build_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    offset = ICONDIR.size + ICONDIRENTRY.size * len(pngs)
    entries, payload = b"", b""
    for size, png in pngs:
        dimension = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += ICONDIRENTRY.pack(dimension, dimension, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    return ICONDIR.pack(0, 1, len(pngs)) + entries + payload


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    _app = QGuiApplication([])
    renderer = QSvgRenderer(QByteArray(SVG.read_bytes()))
    if not renderer.isValid():
        print(f"Invalid SVG: {SVG}", file=sys.stderr)
        return 1
    output.write_bytes(build_ico([(size, render_png(size, renderer)) for size in SIZES]))
    print(f"Icon written to {output} ({len(SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
