#!/usr/bin/env python3
"""Build FundsTrace's native SVG identity and outline its editable lettering.

Requires fontTools. Font inputs and their licenses are documented in README.md.
Raster exports are a separate step, render.mjs, so SVG creation is deterministic
and does not depend on the fonts installed in a browser or operating system.
"""

from __future__ import annotations

import argparse
import hashlib
import html
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs/brand"
GRAPHITE = "#151A1E"
MINT = "#5BE3C0"
JADE = "#087D65"
PAPER = "#F5F7F5"
INK = "#DCE3E2"
MUTED = "#99AAA6"
TAGLINE = "资金流水追踪与关联分析"
FONT_HASHES = {
    "latin": "3ae11c49db0455a3cc33e37d380f20fdb8c7f8b41dc07625c177e3d87a9d6ae6",
    "cjk": "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da",
}


def svg(width: int, height: int, body: str, label: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(label)}">\n'
        f"  <title>{html.escape(label)}</title>\n{body}\n</svg>\n"
    )


def mark(accent: str = MINT, terminal: str = INK, background: bool = False) -> str:
    base = f'<rect width="64" height="64" rx="15" fill="{GRAPHITE}"/>' if background else ""
    return (
        base
        + f'<path d="M16 44 30 20 49 36" fill="none" stroke="{accent}" '
        'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
        + f'<g fill="{accent}"><circle cx="16" cy="44" r="5"/>'
        '<circle cx="30" cy="20" r="5"/></g>'
        + f'<circle cx="49" cy="36" r="5" fill="{terminal}"/>'
    )


class Lettering:
    def __init__(self, path: Path, weight: int, expected: str):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Font hash differs from the documented source: {path.name}")
        font = TTFont(path)
        self.font = instantiateVariableFont(font, {"wght": weight}, inplace=False)
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.units = self.font["head"].unitsPerEm

    def paths(self, value: str, x: float, y: float, size: float, fill: str, tracking: float = 0) -> str:
        parts = []
        cursor = 0.0
        for char in value:
            name = self.cmap.get(ord(char))
            if name is None:
                raise ValueError(f"Missing glyph: {char}")
            pen = SVGPathPen(self.glyphs)
            self.glyphs[name].draw(pen)
            data = pen.getCommands()
            if data:
                parts.append(f'<path transform="translate({cursor:.3f} 0)" d="{data}"/>')
            cursor += self.font["hmtx"].metrics[name][0] + tracking * self.units / size
        scale = size / self.units
        return (
            f'<g aria-label="{html.escape(value)}" fill="{fill}" '
            f'transform="translate({x} {y}) scale({scale:.6f} {-scale:.6f})">'
            + "".join(parts)
            + "</g>"
        )


def write(name: str, content: str) -> None:
    target = BRAND / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latin-font", type=Path, required=True)
    parser.add_argument("--cjk-font", type=Path, required=True)
    args = parser.parse_args()
    latin = Lettering(args.latin_font, 650, FONT_HASHES["latin"])
    cjk = Lettering(args.cjk_font, 400, FONT_HASHES["cjk"])

    icon = svg(64, 64, mark(background=True), "FundsTrace")
    write("fundstrace-icon.svg", icon)
    write("fundstrace-mark.svg", svg(64, 64, mark(), "FundsTrace · 追踪路径标识（深色背景）"))
    write("fundstrace-mark-light.svg", svg(64, 64, mark(JADE, GRAPHITE), "FundsTrace · 追踪路径标识（浅色背景）"))
    (ROOT / "static/logo.svg").write_text(icon, encoding="utf-8")
    (ROOT / "static/favicon.svg").write_text(icon, encoding="utf-8")

    for theme, background, foreground, secondary, accent in [
        ("dark", GRAPHITE, PAPER, MUTED, MINT),
        ("light", PAPER, GRAPHITE, "#52635E", JADE),
    ]:
        group = f'<g transform="translate(238 43) scale(1.625)">{mark(accent, foreground)}</g>'
        title = latin.paths("FundsTrace", 379, 106, 84, foreground, -1.5)
        tagline = cjk.paths(TAGLINE, 383, 146, 21, secondary, 2.1)
        content = group + title + tagline
        write(f"fundstrace-wordmark-{theme}.svg", svg(1080, 190, f'<rect width="1080" height="190" fill="{background}"/>' + content, "FundsTrace · " + TAGLINE))
        write(f"fundstrace-wordmark-transparent-{theme}.svg", svg(1080, 190, content, "FundsTrace · " + TAGLINE))
        source = (
            f'<rect width="1080" height="190" fill="{background}"/>' + group
            + f'<text x="379" y="106" font-family="Manrope" font-size="84" font-weight="650" letter-spacing="-1.5" fill="{foreground}">FundsTrace</text>'
            + f'<text x="383" y="146" font-family="Noto Sans SC" font-size="21" font-weight="400" letter-spacing="2.1" fill="{secondary}">{TAGLINE}</text>'
        )
        write(f"source/wordmark-{theme}-editable.svg", svg(1080, 190, source, "FundsTrace · editable lettering source"))

    board = f'<rect width="1440" height="1040" fill="{PAPER}"/><rect width="1440" height="470" fill="{GRAPHITE}"/>'
    board += latin.paths("FundsTrace", 220, 230, 100, PAPER, -1.7)
    board += cjk.paths(TAGLINE, 225, 281, 28, MUTED, 3)
    board += '<g transform="translate(95 147) scale(1.55)">' + mark() + '</g>'
    board += latin.paths("A PATH, MADE VISIBLE.", 96, 401, 15, MINT, 2.5)
    board += latin.paths("IDENTITY / 2026", 1125, 401, 14, MUTED, 1)
    board += latin.paths("01", 96, 539, 13, "#52635E", 1)
    board += cjk.paths("标识与留白", 139, 542, 20, GRAPHITE, 1)
    board += '<g transform="translate(96 580) scale(2.5)">' + mark(background=True) + '</g>'
    board += '<g transform="translate(318 590) scale(2.1)">' + mark(JADE, GRAPHITE) + '</g>'
    board += '<path d="M96 790H1338" stroke="#D6DEDA"/>'
    board += latin.paths("02", 760, 539, 13, "#52635E", 1)
    board += cjk.paths("小尺寸识别", 803, 542, 20, GRAPHITE, 1)
    for x, size in [(760, 16), (850, 32), (960, 48), (1090, 64), (1230, 96)]:
        board += f'<g transform="translate({x} {670-size/2}) scale({size/64})">' + mark(background=True) + '</g>'
        board += latin.paths(str(size), x, 750, 13, "#52635E")
    board += latin.paths("03", 96, 842, 13, "#52635E", 1)
    board += cjk.paths("色彩", 139, 845, 20, GRAPHITE, 1)
    for x, color, label in [(96,GRAPHITE,"GRAPHITE / 151A1E"),(420,MINT,"TRACE / 5BE3C0"),(744,JADE,"JADE / 087D65"),(1068,PAPER,"PAPER / F5F7F5")]:
        board += f'<rect x="{x}" y="882" width="270" height="40" fill="{color}" stroke="#C7D2CD" stroke-width="0.5"/>'
        board += latin.paths(label, x, 952, 13, "#52635E", .5)
    write("brand-board.svg", svg(1440, 1040, board, "FundsTrace 品牌板 · 原生矢量标识、深浅背景、小尺寸与色彩"))
    print("Generated SVG marks, outlined wordmarks, editable sources, static integration and brand board.")


if __name__ == "__main__":
    main()
