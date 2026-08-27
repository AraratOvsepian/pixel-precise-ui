#!/usr/bin/env python3
"""Create deterministic global and regional visual-difference evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageChops, ImageFilter, ImageOps
except ModuleNotFoundError:
    print(
        "visual_diff.py requires Pillow. Use an environment that already provides it "
        "or install it with: python3 -m pip install Pillow",
        file=sys.stderr,
    )
    raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare source and rendered UI screenshots without resizing either image, "
            "with optional protected-region and baseline-regression gates."
        )
    )
    parser.add_argument("source", type=Path, help="Reference image")
    parser.add_argument("rendered", type=Path, help="Candidate implementation image")
    parser.add_argument("--baseline", type=Path, help="Previous accepted render")
    parser.add_argument("--regions", type=Path, help="JSON protected-region manifest")
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
        help="Fail when global changed pixels exceed this percentage",
    )
    parser.add_argument(
        "--max-normalized-mad",
        type=float,
        default=None,
        help="Fail when global normalized mean absolute difference exceeds this value",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Fail when a protected region regresses against --baseline",
    )
    parser.add_argument(
        "--regression-tolerance",
        type=float,
        default=0.001,
        help=(
            "Allowed normalized-MAD increase for a protected region before it is a "
            "regression (default: 0.001, or 0.1 percentage point)"
        ),
    )
    parser.add_argument(
        "--require-dimensions",
        action="store_true",
        help="Fail unless source, candidate, and baseline dimensions match exactly",
    )
    parser.add_argument(
        "--require-region-gates",
        action="store_true",
        help=(
            "Fail when a protected region has no absolute visual gate, or when a "
            "configured context area has no context gate"
        ),
    )
    args = parser.parse_args()

    if not 0 <= args.threshold <= 255:
        parser.error("--threshold must be between 0 and 255")
    for name in ("fail_over_pct",):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 100:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 100")
    for name in ("max_normalized_mad", "regression_tolerance"):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 1:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")
    if args.fail_on_regression and args.baseline is None:
        parser.error("--fail-on-regression requires --baseline")
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


def compare_images(
    source: Image.Image, rendered: Image.Image, threshold: int
) -> tuple[dict[str, Any], Image.Image, Image.Image]:
    difference = ImageChops.difference(source, rendered)
    total_pixels = source.width * source.height
    histogram = difference.histogram()
    total_channel_difference = sum(
        value * count
        for channel in range(3)
        for value, count in enumerate(histogram[channel * 256 : (channel + 1) * 256])
    )

    red, green, blue = difference.split()
    maximum_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    threshold_mask = maximum_channel.point(lambda value: 255 if value > threshold else 0)
    changed_pixels = threshold_mask.histogram()[255]
    mean_absolute_difference = total_channel_difference / (total_pixels * 3)
    changed_percentage = (changed_pixels / total_pixels) * 100

    metrics: dict[str, Any] = {
        "width": source.width,
        "height": source.height,
        "mean_absolute_difference": round(mean_absolute_difference, 6),
        "normalized_mean_absolute_difference": round(mean_absolute_difference / 255, 8),
        "pixels_over_threshold": changed_pixels,
        "percent_pixels_over_threshold": round(changed_percentage, 6),
        "difference_bounding_box": (
            list(threshold_mask.getbbox()) if threshold_mask.getbbox() else None
        ),
    }
    return metrics, difference, threshold_mask


def edge_normalized_difference(source: Image.Image, rendered: Image.Image) -> float:
    """Compare high-frequency structure so large flat areas cannot hide missing rims."""
    source_edges = ImageOps.grayscale(source).filter(ImageFilter.FIND_EDGES)
    rendered_edges = ImageOps.grayscale(rendered).filter(ImageFilter.FIND_EDGES)
    difference = ImageChops.difference(source_edges, rendered_edges)
    total = sum(value * count for value, count in enumerate(difference.histogram()))
    mean = total / (source.width * source.height)
    return round(mean / 255, 8)


def expanded_box(
    bounds: list[int], padding: int | list[int], canvas_size: tuple[int, int]
) -> tuple[int, int, int, int]:
    if isinstance(padding, int):
        left = top = right = bottom = padding
    else:
        left, top, right, bottom = padding
    x, y, width, height = bounds
    canvas_width, canvas_height = canvas_size
    return (
        max(0, x - left),
        max(0, y - top),
        min(canvas_width, x + width + right),
        min(canvas_height, y + height + bottom),
    )


def load_regions(path: Path | None, source_size: tuple[int, int]) -> list[dict[str, Any]]:
    if path is None:
        return []
    if not path.is_file():
        raise FileNotFoundError(f"Region manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    regions = payload.get("regions") if isinstance(payload, dict) else None
    if not isinstance(regions, list):
        raise ValueError("Region manifest must contain a 'regions' array")

    checked: list[dict[str, Any]] = []
    names: set[str] = set()
    source_width, source_height = source_size
    for index, raw in enumerate(regions):
        if not isinstance(raw, dict):
            raise ValueError(f"Region {index} must be an object")
        name = raw.get("name")
        bounds = raw.get("bounds")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Region {index} requires a non-empty name")
        if name in names:
            raise ValueError(f"Duplicate region name: {name}")
        names.add(name)
        if (
            not isinstance(bounds, list)
            or len(bounds) != 4
            or any(not isinstance(value, int) for value in bounds)
        ):
            raise ValueError(f"Region '{name}' bounds must be [x, y, width, height]")
        x, y, width, height = bounds
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError(f"Region '{name}' has invalid bounds: {bounds}")
        if x + width > source_width or y + height > source_height:
            raise ValueError(
                f"Region '{name}' extends outside source dimensions {source_size}: {bounds}"
            )
        for key in (
            "max_normalized_mean_absolute_difference",
            "max_edge_normalized_mean_absolute_difference",
            "max_context_normalized_mean_absolute_difference",
        ):
            value = raw.get(key)
            if value is not None and not isinstance(value, (int, float)):
                raise ValueError(f"Region '{name}' {key} must be numeric")
            if value is not None and not 0 <= float(value) <= 1:
                raise ValueError(f"Region '{name}' {key} must be between 0 and 1")
        value = raw.get("max_percent_pixels_over_threshold")
        if value is not None and (
            not isinstance(value, (int, float)) or not 0 <= float(value) <= 100
        ):
            raise ValueError(
                f"Region '{name}' max_percent_pixels_over_threshold must be 0..100"
            )
        context_padding = raw.get("context_padding")
        if context_padding is not None:
            valid_integer = isinstance(context_padding, int) and context_padding > 0
            valid_list = (
                isinstance(context_padding, list)
                and len(context_padding) == 4
                and all(isinstance(item, int) and item >= 0 for item in context_padding)
                and any(item > 0 for item in context_padding)
            )
            if not valid_integer and not valid_list:
                raise ValueError(
                    f"Region '{name}' context_padding must be a positive integer or "
                    "[left, top, right, bottom] non-negative integers"
                )
        if (
            raw.get("max_context_normalized_mean_absolute_difference") is not None
            and context_padding is None
        ):
            raise ValueError(
                f"Region '{name}' max_context_normalized_mean_absolute_difference "
                "requires context_padding"
            )
        checked.append({**raw, "name": name, "bounds": bounds})
    return checked


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "region"


def save_visuals(
    source: Image.Image, rendered: Image.Image, difference: Image.Image, output_stem: Path
) -> None:
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    Image.blend(source, rendered, 0.5).save(output_stem.with_name(output_stem.name + "-overlay.png"))
    amplified = difference.convert("L").point(lambda value: min(255, value * 4))
    ImageOps.colorize(amplified, black="#080b12", white="#ff315f").save(
        output_stem.with_name(output_stem.name + "-difference.png")
    )


def main() -> int:
    args = parse_args()
    source = load_rgb(args.source)
    rendered = load_rgb(args.rendered)
    baseline = load_rgb(args.baseline) if args.baseline else None
    regions = load_regions(args.regions, source.size)

    sizes = [source.size, rendered.size]
    if baseline is not None:
        sizes.append(baseline.size)
    canvas_size = (max(size[0] for size in sizes), max(size[1] for size in sizes))
    source_canvas = padded(source, canvas_size)
    rendered_canvas = padded(rendered, canvas_size)
    baseline_canvas = padded(baseline, canvas_size) if baseline is not None else None

    global_metrics, global_difference, _ = compare_images(
        source_canvas, rendered_canvas, args.threshold
    )
    baseline_global_metrics = None
    if baseline_canvas is not None:
        baseline_global_metrics, _, _ = compare_images(
            source_canvas, baseline_canvas, args.threshold
        )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    save_visuals(source_canvas, rendered_canvas, global_difference, output_dir / "global")
    Image.blend(source_canvas, rendered_canvas, 0.5).save(output_dir / "overlay.png")
    amplified_global = global_difference.convert("L").point(
        lambda value: min(255, value * 4)
    )
    ImageOps.colorize(
        amplified_global, black="#080b12", white="#ff315f"
    ).save(output_dir / "difference.png")

    violations: list[dict[str, Any]] = []
    dimensions_match = source.size == rendered.size
    baseline_dimensions_match = baseline is None or source.size == baseline.size
    if args.require_dimensions and not dimensions_match:
        violations.append(
            {"scope": "global", "gate": "dimensions", "message": "source and candidate differ"}
        )
    if args.require_dimensions and not baseline_dimensions_match:
        violations.append(
            {"scope": "baseline", "gate": "dimensions", "message": "source and baseline differ"}
        )
    if (
        args.fail_over_pct is not None
        and global_metrics["percent_pixels_over_threshold"] > args.fail_over_pct
    ):
        violations.append(
            {
                "scope": "global",
                "gate": "percent_pixels_over_threshold",
                "actual": global_metrics["percent_pixels_over_threshold"],
                "maximum": args.fail_over_pct,
            }
        )
    if (
        args.max_normalized_mad is not None
        and global_metrics["normalized_mean_absolute_difference"] > args.max_normalized_mad
    ):
        violations.append(
            {
                "scope": "global",
                "gate": "normalized_mean_absolute_difference",
                "actual": global_metrics["normalized_mean_absolute_difference"],
                "maximum": args.max_normalized_mad,
            }
        )

    region_results: list[dict[str, Any]] = []
    for region in regions:
        name = region["name"]
        x, y, width, height = region["bounds"]
        box = (x, y, x + width, y + height)
        source_crop = source_canvas.crop(box)
        rendered_crop = rendered_canvas.crop(box)
        region_metrics, region_difference, _ = compare_images(
            source_crop, rendered_crop, args.threshold
        )
        edge_difference = edge_normalized_difference(source_crop, rendered_crop)
        save_visuals(
            source_crop,
            rendered_crop,
            region_difference,
            output_dir / "regions" / safe_name(name),
        )

        context_bounds = None
        context_metrics = None
        context_edge_difference = None
        context_padding = region.get("context_padding")
        if context_padding is not None:
            context_box = expanded_box(region["bounds"], context_padding, source_canvas.size)
            context_bounds = [
                context_box[0],
                context_box[1],
                context_box[2] - context_box[0],
                context_box[3] - context_box[1],
            ]
            source_context = source_canvas.crop(context_box)
            rendered_context = rendered_canvas.crop(context_box)
            context_metrics, context_difference, _ = compare_images(
                source_context, rendered_context, args.threshold
            )
            context_edge_difference = edge_normalized_difference(
                source_context, rendered_context
            )
            save_visuals(
                source_context,
                rendered_context,
                context_difference,
                output_dir / "regions" / f"{safe_name(name)}-context",
            )

        baseline_metrics = None
        regression_delta = None
        baseline_edge_difference = None
        edge_regression_delta = None
        baseline_context_metrics = None
        context_regression_delta = None
        if baseline_canvas is not None:
            baseline_crop = baseline_canvas.crop(box)
            baseline_metrics, _, _ = compare_images(
                source_crop, baseline_crop, args.threshold
            )
            baseline_edge_difference = edge_normalized_difference(
                source_crop, baseline_crop
            )
            regression_delta = round(
                region_metrics["normalized_mean_absolute_difference"]
                - baseline_metrics["normalized_mean_absolute_difference"],
                8,
            )
            edge_regression_delta = round(
                edge_difference - baseline_edge_difference,
                8,
            )
            if context_padding is not None and context_bounds is not None:
                context_box = (
                    context_bounds[0],
                    context_bounds[1],
                    context_bounds[0] + context_bounds[2],
                    context_bounds[1] + context_bounds[3],
                )
                source_context = source_canvas.crop(context_box)
                baseline_context = baseline_canvas.crop(context_box)
                baseline_context_metrics, _, _ = compare_images(
                    source_context, baseline_context, args.threshold
                )
                context_regression_delta = round(
                    context_metrics["normalized_mean_absolute_difference"]
                    - baseline_context_metrics["normalized_mean_absolute_difference"],
                    8,
                )

        protected = bool(region.get("protected", True))
        region_result = {
            "name": name,
            "bounds": region["bounds"],
            "protected": protected,
            "metrics": region_metrics,
            "edge_normalized_mean_absolute_difference": edge_difference,
            "context_bounds": context_bounds,
            "context_metrics": context_metrics,
            "context_edge_normalized_mean_absolute_difference": context_edge_difference,
            "baseline_metrics": baseline_metrics,
            "baseline_edge_normalized_mean_absolute_difference": baseline_edge_difference,
            "baseline_context_metrics": baseline_context_metrics,
            "normalized_mad_regression": regression_delta,
            "edge_normalized_mad_regression": edge_regression_delta,
            "context_normalized_mad_regression": context_regression_delta,
        }
        region_results.append(region_result)

        absolute_gate_keys = (
            "max_normalized_mean_absolute_difference",
            "max_percent_pixels_over_threshold",
            "max_edge_normalized_mean_absolute_difference",
            "max_context_normalized_mean_absolute_difference",
        )
        if (
            args.require_region_gates
            and protected
            and not any(region.get(key) is not None for key in absolute_gate_keys)
        ):
            violations.append(
                {
                    "scope": name,
                    "gate": "missing_absolute_region_gate",
                    "message": "protected regions require at least one absolute visual gate",
                }
            )
        if (
            args.require_region_gates
            and protected
            and context_padding is not None
            and region.get("max_context_normalized_mean_absolute_difference") is None
        ):
            violations.append(
                {
                    "scope": name,
                    "gate": "missing_context_gate",
                    "message": "context_padding requires an absolute context gate",
                }
            )

        max_nmad = region.get("max_normalized_mean_absolute_difference")
        if max_nmad is not None and (
            region_metrics["normalized_mean_absolute_difference"] > float(max_nmad)
        ):
            violations.append(
                {
                    "scope": name,
                    "gate": "normalized_mean_absolute_difference",
                    "actual": region_metrics["normalized_mean_absolute_difference"],
                    "maximum": float(max_nmad),
                }
            )
        max_changed = region.get("max_percent_pixels_over_threshold")
        if max_changed is not None and (
            region_metrics["percent_pixels_over_threshold"] > float(max_changed)
        ):
            violations.append(
                {
                    "scope": name,
                    "gate": "percent_pixels_over_threshold",
                    "actual": region_metrics["percent_pixels_over_threshold"],
                    "maximum": float(max_changed),
                }
            )
        max_edge = region.get("max_edge_normalized_mean_absolute_difference")
        if max_edge is not None and edge_difference > float(max_edge):
            violations.append(
                {
                    "scope": name,
                    "gate": "edge_normalized_mean_absolute_difference",
                    "actual": edge_difference,
                    "maximum": float(max_edge),
                }
            )
        max_context = region.get("max_context_normalized_mean_absolute_difference")
        if (
            max_context is not None
            and context_metrics is not None
            and context_metrics["normalized_mean_absolute_difference"]
            > float(max_context)
        ):
            violations.append(
                {
                    "scope": name,
                    "gate": "context_normalized_mean_absolute_difference",
                    "actual": context_metrics[
                        "normalized_mean_absolute_difference"
                    ],
                    "maximum": float(max_context),
                }
            )
        if (
            args.fail_on_regression
            and protected
            and regression_delta is not None
            and regression_delta > args.regression_tolerance
        ):
            violations.append(
                {
                    "scope": name,
                    "gate": "protected_region_regression",
                    "actual": regression_delta,
                    "maximum": args.regression_tolerance,
                }
            )
        if (
            args.fail_on_regression
            and protected
            and edge_regression_delta is not None
            and edge_regression_delta > args.regression_tolerance
        ):
            violations.append(
                {
                    "scope": name,
                    "gate": "protected_region_edge_regression",
                    "actual": edge_regression_delta,
                    "maximum": args.regression_tolerance,
                }
            )
        if (
            args.fail_on_regression
            and protected
            and context_regression_delta is not None
            and context_regression_delta > args.regression_tolerance
        ):
            violations.append(
                {
                    "scope": name,
                    "gate": "protected_region_context_regression",
                    "actual": context_regression_delta,
                    "maximum": args.regression_tolerance,
                }
            )

    metrics = {
        "source": str(args.source),
        "rendered": str(args.rendered),
        "baseline": str(args.baseline) if args.baseline else None,
        "source_dimensions": {"width": source.width, "height": source.height},
        "rendered_dimensions": {"width": rendered.width, "height": rendered.height},
        "baseline_dimensions": (
            {"width": baseline.width, "height": baseline.height} if baseline else None
        ),
        "canvas_dimensions": {"width": canvas_size[0], "height": canvas_size[1]},
        "dimensions_match": dimensions_match,
        "baseline_dimensions_match": baseline_dimensions_match,
        "threshold": args.threshold,
        "mean_absolute_difference": global_metrics["mean_absolute_difference"],
        "normalized_mean_absolute_difference": global_metrics[
            "normalized_mean_absolute_difference"
        ],
        "pixels_over_threshold": global_metrics["pixels_over_threshold"],
        "percent_pixels_over_threshold": global_metrics[
            "percent_pixels_over_threshold"
        ],
        "difference_bounding_box": global_metrics["difference_bounding_box"],
        "global": global_metrics,
        "baseline_global": baseline_global_metrics,
        "regions": region_results,
        "regression_tolerance": args.regression_tolerance,
        "require_region_gates": args.require_region_gates,
        "violations": violations,
        "passed": not violations,
        "resized_for_comparison": False,
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2))
    return 1 if violations else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
