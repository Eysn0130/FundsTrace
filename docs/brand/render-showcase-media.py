#!/usr/bin/env python3
"""Frame real synthetic-demo screenshots and encode step demonstrations with Pillow.

Screenshots are only converted to RGB and proportionally resized. Captured product
controls and state are never redrawn, masked, or cropped. Captions, progress, borders,
and shadows are outside the screenshots. GIFs are sequences of actual UI steps,
not continuous screen recordings, and are encoded directly from the PNG inputs.

Example:
  python docs/brand/render-showcase-media.py \
    --font '/System/Library/Fonts/STHeiti Light.ttc'
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEDIA = ROOT / "docs" / "media" / "showcase"
DEFAULT_FONT = Path("/System/Library/Fonts/STHeiti Light.ttc")
PAPER = "#F5F7F5"
GRAPHITE = "#151A1E"
JADE = "#087D65"
MUTED = "#65756F"
LINE = "#D4DED9"
PROGRESS_TRACK = "#DDE5E0"

STORIES = {
    "import": {
        "title": "导入流水",
        "steps": [
            ("import-select.png", "选择流水文件"),
            ("import-mapping.png", "核对字段映射"),
            ("import-preview.png", "预览待导入记录"),
            ("import-complete.png", "确认导入结果"),
        ],
        "durations": [2600, 2800, 2800, 3200],
    },
    "analysis": {
        "title": "追踪与关联分析",
        "steps": [
            ("analysis-config.png", "设定分析条件"),
            ("analysis-results.png", "查看关联分析结果"),
            ("analysis-detail.png", "生成报告"),
        ],
        "durations": [2800, 3000, 3200],
    },
}


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def read_screenshot(path: Path) -> Image.Image:
    with Image.open(path) as source:
        if source.format != "PNG":
            raise ValueError(f"Screenshot must be a PNG: {path}")
        return source.convert("RGB")


def place_screenshot(
    canvas: Image.Image, source: Image.Image, box: tuple[int, int, int, int]
) -> None:
    """Contain a whole screenshot in box; draw framing outside its image area."""
    left, top, right, bottom = box
    scale = min((right - left) / source.width, (bottom - top) / source.height, 1)
    size = (round(source.width * scale), round(source.height * scale))
    image = source.resize(size, Image.Resampling.LANCZOS)
    x = left + ((right - left) - image.width) // 2
    y = top + ((bottom - top) - image.height) // 2

    # The shadow and one-pixel frame surround the screenshot; neither covers it.
    shadow = Image.new("RGBA", canvas.size)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x - 2, y + 7, x + image.width + 1, y + image.height + 9),
        radius=7,
        fill=(21, 26, 30, 24),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB"))
    ImageDraw.Draw(canvas).rectangle(
        (x - 1, y - 1, x + image.width, y + image.height), fill=LINE
    )
    canvas.paste(image, (x, y))


def render_hero(media: Path, font_path: Path) -> dict:
    source = read_screenshot(media / "workspace-focus.png")
    canvas = Image.new("RGB", (1600, 1180), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((56, 49, 61, 94), fill=JADE)
    draw.text(
        (78, 45), "从流水，看清往来路径。", font=font(font_path, 51), fill=GRAPHITE, anchor="lt"
    )
    draw.text(
        (78, 116), "真实工作台 · 合成演示", font=font(font_path, 23), fill=MUTED, anchor="lt"
    )
    place_screenshot(canvas, source, (56, 196, 1544, 1126))
    destination = media / "hero.png"
    canvas.save(destination, optimize=True)
    return {"file": destination.name, "size": list(canvas.size), "bytes": destination.stat().st_size}


def story_frame(
    screenshot: Image.Image,
    title: str,
    caption: str,
    index: int,
    count: int,
    font_path: Path,
) -> Image.Image:
    canvas = Image.new("RGB", (1200, 886), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((32, 27, 36, 55), fill=JADE)
    draw.text((50, 25), title, font=font(font_path, 30), fill=GRAPHITE, anchor="lt")
    draw.text(
        (1168, 31),
        "真实界面步骤演示 · 合成演示",
        font=font(font_path, 17),
        fill=MUTED,
        anchor="rt",
    )
    draw.text((32, 80), caption, font=font(font_path, 24), fill=GRAPHITE, anchor="lt")
    draw.text(
        (1168, 82),
        f"{index + 1:02d} / {count:02d}",
        font=font(font_path, 20),
        fill=JADE,
        anchor="rt",
    )
    gap = 8
    segment_width = (1136 - gap * (count - 1)) / count
    for step in range(count):
        x = round(32 + step * (segment_width + gap))
        end = round(x + segment_width)
        draw.rectangle((x, 117, end, 120), fill=JADE if step <= index else PROGRESS_TRACK)
    place_screenshot(canvas, screenshot, (32, 144, 1168, 854))
    return canvas


def shared_palette(frames: list[Image.Image]) -> Image.Image:
    # A single palette avoids per-step colour changes. Sample every complete
    # composition; no palette depends on only the first screen's colours.
    sample_width = 600
    sample_height = round(frames[0].height * sample_width / frames[0].width)
    samples = Image.new("RGB", (sample_width, sample_height * len(frames)))
    for index, frame in enumerate(frames):
        samples.paste(
            frame.resize((sample_width, sample_height), Image.Resampling.LANCZOS),
            (0, index * sample_height),
        )
    # Reserve a small accent budget: a frequency-only palette can average a
    # small blue button and a jade progress line into the same incorrect hue.
    # Both budgets are sampled from every real composition, with exact external
    # brand colours reserved separately.
    base = samples.quantize(colors=216, method=Image.Quantize.MEDIANCUT)
    accent_pixels = [
        pixel for pixel in samples.get_flattened_data()
        if max(pixel) > 110 and max(pixel) - min(pixel) > 45
    ]
    accents = Image.new("RGB", (max(1, len(accent_pixels)), 1), JADE)
    if accent_pixels:
        accents.putdata(accent_pixels)
    accent_palette = accents.quantize(colors=32, method=Image.Quantize.MEDIANCUT)
    fixed = [PAPER, GRAPHITE, JADE, MUTED, LINE, PROGRESS_TRACK, "#FFFFFF", "#000000"]
    palette = Image.new("P", (16, 16))
    palette.putpalette(
        base.getpalette()[:216 * 3]
        + accent_palette.getpalette()[:32 * 3]
        + [component for colour in fixed for component in ImageColor.getrgb(colour)]
    )
    return palette


def render_story(name: str, media: Path, font_path: Path) -> dict:
    story = STORIES[name]
    frames = [
        story_frame(
            read_screenshot(media / filename), story["title"], caption, index,
            len(story["steps"]), font_path,
        )
        for index, (filename, caption) in enumerate(story["steps"])
    ]
    palette = shared_palette(frames)
    encoded = [
        frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames
    ]
    destination = media / f"{name}-demo.gif"
    encoded[0].save(
        destination,
        save_all=True,
        append_images=encoded[1:],
        duration=story["durations"],
        loop=0,
        disposal=1,
        optimize=True,
    )
    poster = media / f"{name}-poster.png"
    # Save decoded GIF pixels so the reduced-motion poster matches its first frame.
    with Image.open(destination) as gif:
        first_frame = gif.convert("RGB")
        first_frame.save(poster, optimize=True)
        observed_durations = []
        for index in range(gif.n_frames):
            gif.seek(index)
            observed_durations.append(gif.info["duration"])
        if gif.n_frames != len(frames) or observed_durations != story["durations"]:
            raise RuntimeError(f"GIF frame/duration verification failed: {destination}")
    with Image.open(poster) as image:
        if ImageChops.difference(first_frame, image.convert("RGB")).getbbox():
            raise RuntimeError(f"Poster differs from GIF first frame: {poster}")
    return {
        "file": destination.name,
        "size": list(frames[0].size),
        "frames": len(frames),
        "durations_ms": observed_durations,
        "total_duration_ms": sum(observed_durations),
        "bytes": destination.stat().st_size,
        "poster": poster.name,
        "poster_matches_first_frame": True,
        "sources": [filename for filename, _ in story["steps"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument("--media-dir", type=Path, default=DEFAULT_MEDIA)
    parser.add_argument("--only", choices=("all", "hero", "import", "analysis"), default="all")
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error(f"Font not found; pass --font with a CJK font path: {args.font}")
    required = []
    if args.only in ("all", "hero"):
        required.append("workspace-focus.png")
    for name, story in STORIES.items():
        if args.only in ("all", name):
            required.extend(filename for filename, _ in story["steps"])
    missing = [str(args.media_dir / filename) for filename in required if not (args.media_dir / filename).is_file()]
    if missing:
        parser.error("Missing real UI screenshots:\n" + "\n".join(missing))
    results = []
    if args.only in ("all", "hero"):
        results.append(render_hero(args.media_dir, args.font))
    for name in STORIES:
        if args.only in ("all", name):
            results.append(render_story(name, args.media_dir, args.font))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
