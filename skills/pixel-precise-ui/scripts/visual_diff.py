#!/usr/bin/env python3
"""Create deterministic overlay, heatmap, and metrics for two UI screenshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageOps
except ModuleNotFoundError:
    print(
        "visual_diff.py requires Pillow. Use an environment that already provides it "
        "or install it with: python3 -m pip install Pillow",
        file=sys.stderr,
    )
    raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare source and rendered UI screenshots without resizing either image."
    )
    parser.add_argument("source", type=Path, help="Reference image")
    parser.add_argument("rendered", type=Path, help="Rendered implementation image")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("visual-check"), help="Output directory"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=20,
        help="Per-channel difference threshold from 0 to 255 (default: 20)",
    )
    parser.add_argument(
        "--fail-over-pct",
        type=float,
        default=None,
        help="Exit with status 1 when changed pixels exceed this percentage",
    )
    args = parser.parse_args()
    if not 0 <= args.threshold <= 255:
        parser.error("--threshold must be between 0 and 255")
    if args.fail_over_pct is not None and not 0 <= args.fail_over_pct <= 100:
        parser.error("--fail-over-pct must be between 0 and 100")
    return args


def load_rgb(path: Path) -> Image.Image:
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def padded(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, "#000000")
    canvas.paste(image, (0, 0))
    return canvas


def main() -> int:
    args = parse_args()
    source = load_rgb(args.source)
    rendered = load_rgb(args.rendered)

    source_size = source.size
    rendered_size = rendered.size
    canvas_size = (max(source.width, rendered.width), max(source.height, rendered.height))
    source_canvas = padded(source, canvas_size)
    rendered_canvas = padded(rendered, canvas_size)

    difference = ImageChops.difference(source_canvas, rendered_canvas)
    total_pixels = canvas_size[0] * canvas_size[1]
    histogram = difference.histogram()
    total_channel_difference = sum(
        value * count
        for channel in range(3)
        for value, count in enumerate(histogram[channel * 256 : (channel + 1) * 256])
    )

    red, green, blue = difference.split()
    maximum_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    threshold_mask = maximum_channel.point(
        lambda value: 255 if value > args.threshold else 0
    )
    changed_pixels = threshold_mask.histogram()[255]

    mean_absolute_difference = total_channel_difference / (total_pixels * 3)
    changed_percentage = (changed_pixels / total_pixels) * 100

    difference_bounds = threshold_mask.getbbox()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    Image.blend(source_canvas, rendered_canvas, 0.5).save(output_dir / "overlay.png")

    amplified = difference.convert("L").point(lambda value: min(255, value * 4))
    ImageOps.colorize(amplified, black="#080b12", white="#ff315f").save(
        output_dir / "difference.png"
    )

    metrics = {
        "source": str(args.source),
        "rendered": str(args.rendered),
        "source_dimensions": {"width": source_size[0], "height": source_size[1]},
        "rendered_dimensions": {"width": rendered_size[0], "height": rendered_size[1]},
        "canvas_dimensions": {"width": canvas_size[0], "height": canvas_size[1]},
        "dimensions_match": source_size == rendered_size,
        "threshold": args.threshold,
        "mean_absolute_difference": round(mean_absolute_difference, 6),
        "normalized_mean_absolute_difference": round(mean_absolute_difference / 255, 8),
        "pixels_over_threshold": changed_pixels,
        "percent_pixels_over_threshold": round(changed_percentage, 6),
        "difference_bounding_box": list(difference_bounds) if difference_bounds else None,
        "resized_for_comparison": False,
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2))

    if args.fail_over_pct is not None and changed_percentage > args.fail_over_pct:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
