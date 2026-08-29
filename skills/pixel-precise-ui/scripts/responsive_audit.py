#!/usr/bin/env python3
"""Validate responsive UI evidence across a fixed common-device matrix."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageChops, ImageOps
except ModuleNotFoundError:
    print(
        "responsive_audit.py requires Pillow. Use an environment that already "
        "provides it or install it with: python3 -m pip install Pillow",
        file=sys.stderr,
    )
    raise SystemExit(2)


SCHEMA_VERSION = "2.0"
PROFILE_NAME = "common-2026-08-v2"
COLLECTOR_NAME = "pixel-precise-ui-capture"
COLLECTOR_VERSION = "2.1"
VALIDATOR_NAME = "pixel-precise-ui-responsive-audit"
VALIDATOR_VERSION = "2.1"
LOSSLESS_FORMATS = {"PNG", "BMP", "TIFF"}
MAX_HORIZONTAL_OVERFLOW_PX = 1.0
ZOOM_TOLERANCE_PX = 2.0
MAX_SWEEP_GAP_PX = 20
MIN_SWEEP_WIDTH = 320
MAX_SWEEP_WIDTH = 2560
MAX_COMPACT_BREAKPOINTS = 8
MAX_COMPACT_CAPTURE_CASES = 80
MAX_EVIDENCE_AGE_HOURS = 24
COLOR_SCHEMES = {"light", "dark", "no-preference"}
DEVICE_CLASSES = {"mobile", "tablet", "desktop"}

TREE_EXCLUDED_DIRECTORIES = {
    ".git",
    ".next",
    ".cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "output",
    "responsive-check",
    "visual-check",
    "completion-check",
    "captures",
    "venv",
}

# Compact CSS viewport profile. Optional b-1/b/b+1 cases are checked only when
# breakpoint capture was explicitly enabled for a bounded media-query set.
_EXPECTED_COMMON_VIEWPORTS: tuple[dict[str, Any], ...] = (
    {"class": "mobile-portrait", "width": 360, "height": 800, "zoom": 100, "base_dpr": 3},
    {"class": "mobile-portrait", "width": 390, "height": 844, "zoom": 100, "base_dpr": 3},
    {"class": "mobile-portrait", "width": 393, "height": 873, "zoom": 100, "base_dpr": 2.75},
    {"class": "mobile-portrait", "width": 414, "height": 896, "zoom": 100, "base_dpr": 2},
    {"class": "mobile-landscape", "width": 844, "height": 390, "zoom": 100, "base_dpr": 3},
    {"class": "tablet-portrait", "width": 768, "height": 1024, "zoom": 100, "base_dpr": 2},
    {"class": "tablet-landscape", "width": 1280, "height": 800, "zoom": 100, "base_dpr": 2},
    {"class": "desktop", "width": 1280, "height": 720, "zoom": 100},
    {"class": "desktop", "width": 1366, "height": 768, "zoom": 100},
    {"class": "desktop", "width": 1536, "height": 864, "zoom": 100},
    {"class": "desktop", "width": 1920, "height": 1080, "zoom": 100},
    {"class": "desktop-zoom", "width": 1366, "height": 768, "zoom": 200},
    {
        "class": "accessibility-text-zoom",
        "width": 390,
        "height": 844,
        "zoom": 100,
        "text_zoom": 200,
        "base_dpr": 3,
    },
)


def load_common_viewports() -> tuple[dict[str, Any], ...]:
    matrix_path = Path(__file__).with_name(
        "common-responsive-matrix-2026-08-v2.json"
    )
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    if payload.get("profile") != PROFILE_NAME:
        raise ValueError(f"Shared responsive matrix profile must be '{PROFILE_NAME}'")
    viewports: list[dict[str, Any]] = []
    for raw in payload.get("viewports", []):
        viewports.append(
            {
                "class": raw["class"],
                "width": raw["width"],
                "height": raw["height"],
                "zoom": raw.get("zoom", 100),
                **(
                    {"text_zoom": raw["text_zoom"]}
                    if "text_zoom" in raw
                    else {}
                ),
                **({"base_dpr": raw["base_dpr"]} if "base_dpr" in raw else {}),
            }
        )
    for group in payload.get("groups", []):
        sizes = group.get("sizes", [])
        if not any(
            field in group
            for field in ("zoom_percent", "text_zoom_percent", "base_dpr")
        ):
            raise ValueError(f"Unsupported shared matrix group: {group}")
        for width, height in sizes:
            for zoom in group.get("zoom_percent", [100]):
                for text_zoom in group.get("text_zoom_percent", [100]):
                    for base_dpr in group.get("base_dpr", [1]):
                        entry = {
                            "class": group["class"],
                            "width": width,
                            "height": height,
                            "zoom": zoom,
                        }
                        if "text_zoom_percent" in group:
                            entry["text_zoom"] = text_zoom
                        if "base_dpr" in group:
                            entry["base_dpr"] = base_dpr
                        viewports.append(entry)
    result = tuple(viewports)
    if result != _EXPECTED_COMMON_VIEWPORTS:
        raise ValueError(
            "Shared common-responsive matrix drifted from the validator's reviewed profile"
        )
    return result


COMMON_VIEWPORTS = load_common_viewports()
SECONDARY_STATE_ANCHORS: tuple[dict[str, Any], ...] = (
    {"class": "mobile-portrait", "width": 390, "height": 844, "zoom": 100, "base_dpr": 3},
    {"class": "mobile-landscape", "width": 844, "height": 390, "zoom": 100, "base_dpr": 3},
    {"class": "tablet-portrait", "width": 768, "height": 1024, "zoom": 100, "base_dpr": 2},
    {"class": "desktop", "width": 1366, "height": 768, "zoom": 100, "base_dpr": 1},
)

ASSET_KINDS = {"font", "image", "icon", "texture", "other"}
RASTER_ASSET_KINDS = {"image", "icon", "texture"}
ASSET_STATUSES = {"exact", "derived-deterministically", "approximate", "missing"}
ASSET_USAGES = {
    "full-bleed-background",
    "component-surface",
    "isolated-asset",
    "decorative",
    "font",
    "other",
}
ASSET_ORIGINS = {
    "authoritative",
    "repository",
    "reference-crop",
    "generated",
    "inferred",
}
OCCLUDED_PIXEL_STATES = {"none", "unknown", "reconstructed"}
RECONSTRUCTION_OPERATIONS = {
    "interpolate",
    "inpaint",
    "generative-fill",
    "clone-stamp",
    "redraw",
    "blur",
    "content-aware-fill",
    "heal",
    "patch",
}
PROVENANCE_PRESERVING_OPERATIONS = {
    "authoritative-source",
    "repository-source",
    "lossless-copy",
    "lossless-crop",
    "alpha-mask",
    "metadata-strip",
    "color-profile-normalize",
    "vector-render",
}
VISIBLE_RASTER_RESOURCE_TYPES = {"image", "background-image", "poster", "canvas"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate stable screenshots, browser probes, visual review, asset safety, "
            "common viewport coverage, and declared breakpoint boundaries."
        )
    )
    parser.add_argument("manifest", type=Path, help="Responsive evidence manifest")
    parser.add_argument(
        "--asset-ledger",
        type=Path,
        required=True,
        help="Asset provenance and responsive-safety ledger",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("responsive-check"),
        help="Output directory",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        required=True,
        help="The exact source reference used by this capture run",
    )
    parser.add_argument(
        "--code-root",
        type=Path,
        required=True,
        help="Target implementation tree; its current fingerprint must match the capture",
    )
    parser.add_argument(
        "--visual-review",
        type=Path,
        required=True,
        help="Independent post-capture review records keyed to exact screenshot hashes",
    )
    return parser.parse_args()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def resolve_path(raw: Any, owner: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty path")
    path = Path(raw)
    if not path.is_absolute():
        path = owner.parent / path
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def pixel_sha256(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    digest = hashlib.sha256()
    digest.update(f"RGB:{rgb.width}x{rgb.height}:".encode("ascii"))
    digest.update(rgb.tobytes())
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    """Implement the cross-language recursive-tree-v2 source fingerprint."""
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Code root not found: {root}")
    digest = hashlib.sha256()
    digest.update(b"pixel-precise-ui:recursive-tree-v2\0")
    entries: list[tuple[bytes, Path]] = []
    for directory, child_directories, names in os.walk(root, followlinks=False):
        relative_directory = Path(directory).relative_to(root)
        kept_directories = []
        for name in child_directories:
            relative = relative_directory / name
            if any(
                component in TREE_EXCLUDED_DIRECTORIES
                for component in relative.parts
            ):
                continue
            path = root / relative
            if path.is_symlink():
                normalized = unicodedata.normalize("NFC", relative.as_posix()).encode(
                    "utf-8"
                )
                entries.append((normalized, path))
            else:
                kept_directories.append(name)
        child_directories[:] = sorted(kept_directories)
        for name in names:
            relative = relative_directory / name
            if any(
                component in TREE_EXCLUDED_DIRECTORIES
                for component in relative.parts
            ):
                continue
            path = root / relative
            normalized = unicodedata.normalize("NFC", relative.as_posix()).encode(
                "utf-8"
            )
            entries.append((normalized, path))
    for relative, path in sorted(entries, key=lambda item: item[0]):
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode):
            mode = b"120000"
            content = os.readlink(path).encode("utf-8")
        elif stat.S_ISREG(metadata.st_mode):
            mode = b"100755" if metadata.st_mode & stat.S_IXUSR else b"100644"
            content = path.read_bytes()
        else:
            raise ValueError(f"Unsupported code-tree entry type: {path}")
        content_digest = hashlib.sha256(content).digest()
        digest.update(mode)
        digest.update(b"\0")
        digest.update(str(len(relative)).encode("ascii"))
        digest.update(b":")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(content_digest)
    return digest.hexdigest()


def load_capture(path: Path) -> tuple[Image.Image, str | None]:
    with Image.open(path) as opened:
        detected_format = opened.format
        image = ImageOps.exif_transpose(opened).convert("RGB")
    return image, detected_format


def expected_device_semantics(case_class: str, width: int) -> dict[str, Any]:
    accessibility = case_class == "accessibility-text-zoom"
    mobile = case_class.startswith("mobile-") or (accessibility and width <= 480)
    tablet = case_class.startswith("tablet-") or (
        accessibility and 480 < width <= 1024
    )
    return {
        "device_class": "mobile" if mobile else "tablet" if tablet else "desktop",
        "is_mobile": mobile,
        "has_touch": mobile or tablet,
    }


def case_key(case: dict[str, Any]) -> tuple[str, int, int, int, int, float]:
    viewport = case.get("viewport")
    if not isinstance(viewport, dict):
        raise ValueError(f"Case '{case.get('id', '?')}' requires a viewport object")
    width = viewport.get("width")
    height = viewport.get("height")
    zoom = viewport.get("zoom_percent", 100)
    text_zoom = viewport.get("text_zoom_percent", 100)
    base_dpr = viewport.get("base_dpr")
    dpr = viewport.get("dpr")
    case_class = case.get("class")
    if not isinstance(case_class, str) or not case_class:
        raise ValueError(f"Case '{case.get('id', '?')}' requires a class")
    device_class = viewport.get("device_class")
    if device_class not in DEVICE_CLASSES:
        raise ValueError(
            f"Case '{case.get('id', '?')}' viewport.device_class must be mobile, "
            "tablet, or desktop"
        )
    for field in ("is_mobile", "has_touch"):
        if not isinstance(viewport.get(field), bool):
            raise ValueError(
                f"Case '{case.get('id', '?')}' viewport.{field} must be boolean"
            )
    if not isinstance(width, int) or width <= 0:
        raise ValueError(f"Case '{case.get('id', '?')}' viewport width must be positive")
    if not isinstance(height, int) or height <= 0:
        raise ValueError(f"Case '{case.get('id', '?')}' viewport height must be positive")
    if not isinstance(zoom, int) or zoom <= 0:
        raise ValueError(f"Case '{case.get('id', '?')}' zoom_percent must be positive")
    if not isinstance(text_zoom, int) or text_zoom < 100:
        raise ValueError(
            f"Case '{case.get('id', '?')}' text_zoom_percent must be an integer >= 100"
        )
    if not isinstance(base_dpr, (int, float)) or not 1 <= float(base_dpr) <= 4:
        raise ValueError(
            f"Case '{case.get('id', '?')}' viewport base_dpr must be between 1 and 4"
        )
    if not isinstance(dpr, (int, float)) or not 0.5 <= float(dpr) <= 8:
        raise ValueError(f"Case '{case.get('id', '?')}' viewport dpr must be between 0.5 and 8")
    expected_actual_dpr = float(base_dpr) * zoom / 100
    if abs(float(dpr) - expected_actual_dpr) > 0.01:
        raise ValueError(
            f"Case '{case.get('id', '?')}' viewport dpr must equal base_dpr * zoom_percent / 100"
        )
    return case_class, width, height, zoom, text_zoom, float(base_dpr)


def matches_required(case: dict[str, Any], required: dict[str, Any]) -> bool:
    case_class, width, height, zoom, text_zoom, base_dpr = case_key(case)
    if (case_class, width, height, zoom, text_zoom) != (
        required["class"],
        required["width"],
        required["height"],
        required["zoom"],
        required.get("text_zoom", 100),
    ):
        return False
    required_dpr = required.get("base_dpr")
    return required_dpr is None or abs(base_dpr - float(required_dpr)) <= 0.001


def append_violation(
    target: list[dict[str, Any]], scope: str, gate: str, **details: Any
) -> None:
    target.append({"scope": scope, "gate": gate, **details})


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_timestamp(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty ISO-8601 timestamp")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{label} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def validate_run_evidence(
    manifest: dict[str, Any],
    manifest_path: Path,
    reference_hash: str,
    current_code_hash: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    violations: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Responsive manifest schema_version must be '{SCHEMA_VERSION}'")
    collector = manifest.get("collector")
    if not isinstance(collector, dict):
        raise ValueError("Responsive manifest requires a collector object")
    expected_collector = {
        "name": COLLECTOR_NAME,
        "version": COLLECTOR_VERSION,
        "harness_collected": True,
    }
    for field, expected in expected_collector.items():
        if collector.get(field) != expected:
            blocker = {
                "scope": "collector",
                "gate": "automated_capture_harness",
                "field": field,
                "actual": collector.get(field),
                "expected": expected,
            }
            violations.append(blocker)
            blockers.append(blocker)

    capture_script = Path(__file__).with_name("capture_responsive.mjs")
    declared_script_hash = collector.get("script_sha256")
    if not capture_script.is_file() or declared_script_hash != file_sha256(capture_script):
        blocker = {
            "scope": "collector",
            "gate": "capture_harness_fingerprint",
            "declared": declared_script_hash,
            "computed": file_sha256(capture_script) if capture_script.is_file() else None,
        }
        violations.append(blocker)
        blockers.append(blocker)
    matrix_name = collector.get("common_matrix_path")
    matrix_path = Path(__file__).with_name(
        matrix_name if isinstance(matrix_name, str) else "missing-matrix"
    )
    if (
        matrix_path.name != "common-responsive-matrix-2026-08-v2.json"
        or not matrix_path.is_file()
        or collector.get("common_matrix_sha256") != file_sha256(matrix_path)
    ):
        blocker = {
            "scope": "collector",
            "gate": "common_matrix_fingerprint",
            "declared_path": matrix_name,
            "declared_hash": collector.get("common_matrix_sha256"),
            "computed_hash": file_sha256(matrix_path) if matrix_path.is_file() else None,
        }
        violations.append(blocker)
        blockers.append(blocker)

    trace_path = resolve_path(collector.get("trace_path"), manifest_path, "Collector trace")
    trace_hash = file_sha256(trace_path)
    if collector.get("trace_sha256") != trace_hash:
        blocker = {
            "scope": "collector",
            "gate": "capture_trace_fingerprint",
            "declared": collector.get("trace_sha256"),
            "computed": trace_hash,
        }
        violations.append(blocker)
        blockers.append(blocker)
    review_index_path = resolve_path(
        collector.get("review_index_path"), manifest_path, "Review index"
    )
    review_index_hash = file_sha256(review_index_path)
    if collector.get("review_index_sha256") != review_index_hash:
        blocker = {
            "scope": "collector",
            "gate": "review_index_fingerprint",
            "declared": collector.get("review_index_sha256"),
            "computed": review_index_hash,
        }
        violations.append(blocker)
        blockers.append(blocker)

    run = manifest.get("run")
    if not isinstance(run, dict):
        raise ValueError("Responsive manifest requires a run object")
    required_run_fields = (
        "run_id",
        "generated_at",
        "code_tree_hash",
        "code_tree_hash_after",
        "code_tree_hash_algorithm",
        "input_fingerprint",
        "asset_ledger_sha256",
        "reference_pixel_sha256",
        "route",
        "state_set_hash",
        "browser_name",
        "browser_version",
        "color_profile",
        "color_scheme",
    )
    for field in required_run_fields:
        if not isinstance(run.get(field), str) or not run[field].strip():
            raise ValueError(f"Responsive run.{field} must be a non-empty string")
    if run["color_scheme"] not in COLOR_SCHEMES:
        raise ValueError(
            "Responsive run.color_scheme must be light, dark, or no-preference"
        )

    try:
        trace_events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise ValueError("Collector trace must be valid JSON Lines") from error
    if not all(isinstance(event, dict) for event in trace_events):
        raise ValueError("Collector trace events must be JSON objects")
    trace_valid = (
        len(trace_events) >= 2
        and [event.get("sequence") for event in trace_events]
        == list(range(1, len(trace_events) + 1))
        and trace_events[0].get("type") == "run_started"
        and trace_events[-1].get("type") == "run_completed"
        and trace_events[0].get("run_id") == run["run_id"]
        and trace_events[-1].get("run_id") == run["run_id"]
    )
    if not trace_valid:
        blocker = {
            "scope": "collector",
            "gate": "capture_trace_structure",
            "event_count": len(trace_events),
        }
        violations.append(blocker)
        blockers.append(blocker)
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or any(
        not isinstance(case_value, dict) for case_value in raw_cases
    ):
        raise ValueError("Responsive manifest cases must be an array of objects")
    collection_errors = manifest.get("collection_errors")
    if not isinstance(collection_errors, list):
        raise ValueError("Responsive manifest collection_errors must be an array")
    correlation_errors: list[dict[str, Any]] = []
    starts_by_case: dict[str, list[dict[str, Any]]] = {}
    completions_by_case: dict[str, list[dict[str, Any]]] = {}
    for event in trace_events:
        case_id = event.get("case_id")
        if event.get("type") == "case_started" and isinstance(case_id, str):
            starts_by_case.setdefault(case_id, []).append(event)
        elif event.get("type") == "case_completed" and isinstance(case_id, str):
            completions_by_case.setdefault(case_id, []).append(event)
    manifest_case_ids = {
        case_value.get("id")
        for case_value in raw_cases
        if isinstance(case_value.get("id"), str)
    }
    trace_case_ids = set(starts_by_case) | set(completions_by_case)
    if trace_case_ids != manifest_case_ids:
        correlation_errors.append(
            {
                "field": "case_ids",
                "manifest": sorted(manifest_case_ids),
                "trace": sorted(trace_case_ids),
            }
        )
    for case_value in raw_cases:
        case_id = case_value.get("id")
        if not isinstance(case_id, str):
            continue
        starts = starts_by_case.get(case_id, [])
        completions = completions_by_case.get(case_id, [])
        if len(starts) != 1 or len(completions) != 1:
            correlation_errors.append(
                {
                    "case_id": case_id,
                    "field": "event_cardinality",
                    "started": len(starts),
                    "completed": len(completions),
                }
            )
            continue
        start = starts[0]
        completed = completions[0]
        if start.get("sequence", 0) >= completed.get("sequence", 0):
            correlation_errors.append(
                {"case_id": case_id, "field": "event_order"}
            )
        viewport = case_value.get("viewport")
        trace_viewport = start.get("viewport")
        start_mismatches: dict[str, Any] = {}
        if start.get("state") != case_value.get("state_id"):
            start_mismatches["state"] = {
                "manifest": case_value.get("state_id"),
                "trace": start.get("state"),
            }
        if not isinstance(viewport, dict) or not isinstance(trace_viewport, dict):
            start_mismatches["viewport"] = "missing"
        else:
            for field in (
                "width",
                "height",
                "base_dpr",
                "dpr",
                "zoom_percent",
                "text_zoom_percent",
                "device_class",
                "is_mobile",
                "has_touch",
            ):
                if trace_viewport.get(field) != viewport.get(field):
                    start_mismatches[field] = {
                        "manifest": viewport.get(field),
                        "trace": trace_viewport.get(field),
                    }
            if trace_viewport.get("class") != case_value.get("class"):
                start_mismatches["class"] = {
                    "manifest": case_value.get("class"),
                    "trace": trace_viewport.get("class"),
                }
        if start_mismatches:
            correlation_errors.append(
                {
                    "case_id": case_id,
                    "field": "case_started",
                    "mismatches": start_mismatches,
                }
            )
        probe = case_value.get("probe")
        expected_probe_summary = None
        if isinstance(probe, dict):
            expected_probe_summary = {
                "inner_width": probe.get("inner_width"),
                "inner_height": probe.get("inner_height"),
                "device_pixel_ratio": probe.get("device_pixel_ratio"),
                "missing_required_elements": probe.get("missing_required_elements"),
                "overflow_count": len(probe.get("overflow_elements", [])),
                "unexpected_overlap_count": len(
                    probe.get("unexpected_overlaps", [])
                ),
                "console_error_count": len(probe.get("console_errors", [])),
                "page_error_count": len(probe.get("page_errors", [])),
                "failed_resource_count": len(probe.get("failed_resources", [])),
                "blocked_write_request_count": len(
                    probe.get("blocked_write_requests", [])
                ),
                "settle_error_count": len(probe.get("settle_errors", [])),
                "cssom_error_count": len(probe.get("cssom_errors", [])),
                "unlinked_visible_resource_count": len(
                    probe.get("unlinked_visible_resources", [])
                ),
                "undecoded_visible_raster_count": len(
                    probe.get("undecoded_visible_rasters", [])
                ),
                "dialog_count": len(probe.get("dialogs", [])),
                "popup_count": len(probe.get("popups", [])),
                "visible_resource_count": len(probe.get("visible_resources", [])),
                "device_emulation_error_count": len(
                    probe.get("emulation_errors", [])
                ),
            }
        completed_expected = {
            "byte_identical_repeat_capture": case_value.get(
                "byte_identical_repeat_capture"
            ),
            "screenshot_file_sha256": case_value.get("screenshot_file_sha256"),
            "repeat_screenshot_file_sha256": case_value.get(
                "repeat_screenshot_file_sha256"
            ),
            "screenshot_pixel_sha256": case_value.get("screenshot_pixel_sha256"),
            "repeat_pixel_sha256": case_value.get("repeat_pixel_sha256"),
            "fatal_error": case_value.get("fatal_error"),
            "probe_summary": expected_probe_summary,
        }
        completed_mismatches = {
            field: {"manifest": expected, "trace": completed.get(field)}
            for field, expected in completed_expected.items()
            if completed.get(field) != expected
        }
        if completed_mismatches:
            correlation_errors.append(
                {
                    "case_id": case_id,
                    "field": "case_completed",
                    "mismatches": completed_mismatches,
                }
            )
    run_completed_events = [
        event for event in trace_events if event.get("type") == "run_completed"
    ]
    if len(run_completed_events) == 1:
        run_completed = run_completed_events[0]
        run_complete_expected = {
            "run_id": run["run_id"],
            "generated_at": run["generated_at"],
            "case_count": len(raw_cases),
            "collection_error_count": len(collection_errors),
            "code_tree_hash_after": run["code_tree_hash_after"],
        }
        run_mismatches = {
            field: {"manifest": expected, "trace": run_completed.get(field)}
            for field, expected in run_complete_expected.items()
            if run_completed.get(field) != expected
        }
        if run_mismatches:
            correlation_errors.append(
                {"field": "run_completed", "mismatches": run_mismatches}
            )
    else:
        correlation_errors.append(
            {
                "field": "run_completed_cardinality",
                "actual": len(run_completed_events),
                "expected": 1,
            }
        )
    if correlation_errors:
        blocker = {
            "scope": "collector",
            "gate": "capture_trace_manifest_correlation",
            "errors": correlation_errors,
        }
        violations.append(blocker)
        blockers.append(blocker)

    attestation = manifest.get("collector_attestation")
    payload = {
        key: value for key, value in manifest.items() if key != "collector_attestation"
    }
    payload_hash = canonical_sha256(payload)
    expected_attestation_hash = hashlib.sha256(
        (
            "pixel-precise-ui:capture-attestation-v2\0"
            + str(collector.get("script_sha256"))
            + "\0"
            + payload_hash
        ).encode("utf-8")
    ).hexdigest()
    if not isinstance(attestation, dict) or (
        attestation.get("algorithm") != "sha256-canonical-json-v1"
        or attestation.get("payload_sha256") != payload_hash
        or attestation.get("attestation_sha256") != expected_attestation_hash
    ):
        blocker = {
            "scope": "collector",
            "gate": "capture_manifest_attestation",
        }
        violations.append(blocker)
        blockers.append(blocker)
    if run["code_tree_hash_algorithm"] != "recursive-tree-v2":
        raise ValueError("Responsive run.code_tree_hash_algorithm must be recursive-tree-v2")
    generated_at = parse_timestamp(run["generated_at"], "Responsive run.generated_at")
    now = dt.datetime.now(dt.timezone.utc)
    age_hours = (now - generated_at).total_seconds() / 3600
    if age_hours < -0.1 or age_hours > MAX_EVIDENCE_AGE_HOURS:
        blocker = {
            "scope": "run",
            "gate": "stale_capture_evidence",
            "age_hours": round(age_hours, 3),
            "maximum_hours": MAX_EVIDENCE_AGE_HOURS,
        }
        violations.append(blocker)
        blockers.append(blocker)
    for field, actual, expected in (
        ("reference_pixel_sha256", run["reference_pixel_sha256"], reference_hash),
        ("code_tree_hash", run["code_tree_hash"], current_code_hash),
        ("code_tree_hash_after", run["code_tree_hash_after"], current_code_hash),
    ):
        if actual != expected:
            blocker = {
                "scope": "run",
                "gate": "stale_capture_evidence",
                "field": field,
                "captured": actual,
                "current": expected,
            }
            violations.append(blocker)
            blockers.append(blocker)
    if run["route"] != manifest.get("route"):
        blocker = {
            "scope": "run",
            "gate": "route_mismatch",
            "run_route": run["route"],
            "manifest_route": manifest.get("route"),
        }
        violations.append(blocker)
        blockers.append(blocker)
    return run, violations, blockers


def alpha_statistics(image: Image.Image) -> dict[str, Any]:
    if "A" not in image.getbands():
        return {
            "has_alpha": False,
            "bbox": None,
            "transparent_fraction": 0.0,
            "perimeter_transparent_fraction": 0.0,
        }
    alpha = image.getchannel("A")
    def values_of(channel: Image.Image) -> list[int]:
        flattened = getattr(channel, "get_flattened_data", None)
        return list(flattened() if flattened is not None else channel.getdata())

    values = values_of(alpha)
    transparent = sum(value < 255 for value in values)
    width, height = alpha.size
    perimeter: list[int] = []
    if width and height:
        perimeter.extend(values_of(alpha.crop((0, 0, width, 1))))
        if height > 1:
            perimeter.extend(values_of(alpha.crop((0, height - 1, width, height))))
        if width > 1 and height > 2:
            perimeter.extend(values_of(alpha.crop((0, 1, 1, height - 1))))
            perimeter.extend(values_of(alpha.crop((width - 1, 1, width, height - 1))))
    return {
        "has_alpha": True,
        "bbox": list(alpha.getbbox()) if alpha.getbbox() is not None else None,
        "transparent_fraction": round(transparent / max(1, len(values)), 8),
        "perimeter_transparent_fraction": round(
            sum(value < 255 for value in perimeter) / max(1, len(perimeter)), 8
        ),
    }


def rectangle_union_area(rectangles: list[tuple[int, int, int, int]]) -> int:
    if not rectangles:
        return 0
    x_values = sorted({value for rectangle in rectangles for value in (rectangle[0], rectangle[2])})
    area = 0
    for left, right in zip(x_values, x_values[1:]):
        if right <= left:
            continue
        intervals = sorted(
            (top, bottom)
            for x1, top, x2, bottom in rectangles
            if x1 < right and x2 > left and bottom > top
        )
        covered_height = 0
        if intervals:
            current_top, current_bottom = intervals[0]
            for top, bottom in intervals[1:]:
                if top <= current_bottom:
                    current_bottom = max(current_bottom, bottom)
                else:
                    covered_height += current_bottom - current_top
                    current_top, current_bottom = top, bottom
            covered_height += current_bottom - current_top
        area += (right - left) * covered_height
    return area


def adjacent_rectangle_components(
    rectangles: list[tuple[int, int, int, int]], tolerance: int = 1
) -> list[list[int]]:
    remaining = set(range(len(rectangles)))
    components: list[list[int]] = []
    while remaining:
        seed = remaining.pop()
        component = [seed]
        queue = [seed]
        while queue:
            current = queue.pop()
            left = rectangles[current]
            neighbors = []
            for candidate in remaining:
                right = rectangles[candidate]
                adjacent = (
                    left[0] <= right[2] + tolerance
                    and right[0] <= left[2] + tolerance
                    and left[1] <= right[3] + tolerance
                    and right[1] <= left[3] + tolerance
                )
                if adjacent:
                    neighbors.append(candidate)
            for neighbor in neighbors:
                remaining.remove(neighbor)
                component.append(neighbor)
                queue.append(neighbor)
        components.append(sorted(component))
    return components


def validate_assets(
    ledger_path: Path,
    reference: Image.Image,
    reference_hash: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = load_object(ledger_path, "Asset ledger")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Asset ledger must contain an 'assets' array")
    violations: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    names: set[str] = set()

    for index, raw in enumerate(assets):
        if not isinstance(raw, dict):
            raise ValueError(f"Asset ledger entry {index} must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Asset ledger entry {index} requires a non-empty name")
        if name in names:
            raise ValueError(f"Duplicate asset ledger name: {name}")
        names.add(name)
        kind = raw.get("kind")
        status = raw.get("status")
        material = raw.get("material")
        evidence = raw.get("evidence")
        if kind not in ASSET_KINDS:
            raise ValueError(f"Asset '{name}' has unsupported kind: {kind}")
        if status not in ASSET_STATUSES:
            raise ValueError(f"Asset '{name}' has unsupported status: {status}")
        if not isinstance(material, bool):
            raise ValueError(f"Asset '{name}' material must be true or false")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(f"Asset '{name}' requires non-empty evidence")

        result = {**raw, "name": name}
        resolved_candidate: Path | None = None
        decoded: Image.Image | None = None
        if isinstance(raw.get("path"), str) and raw["path"].strip():
            resolved_candidate = resolve_path(raw["path"], ledger_path, f"Asset '{name}'")
            try:
                with Image.open(resolved_candidate) as opened:
                    decoded = ImageOps.exif_transpose(opened).copy()
            except (OSError, ValueError):
                decoded = None

        is_raster = kind in RASTER_ASSET_KINDS or decoded is not None
        if decoded is not None and kind not in RASTER_ASSET_KINDS:
            blocker = {
                "scope": name,
                "gate": "raster_kind_mismatch",
                "declared_kind": kind,
                "message": "A decodable raster cannot bypass raster gates as kind other/font.",
            }
            violations.append(blocker)
            blockers.append(blocker)
        if is_raster:
            required = (
                "usage",
                "origin",
                "contains_foreground_pixels",
                "contains_context_pixels",
                "occluded_pixels",
                "responsive_safe",
                "derivation_operations",
                "path",
                "file_sha256",
                "pixel_sha256",
                "intrinsic_dimensions",
            )
            missing = [field for field in required if field not in raw]
            if missing:
                blocker = {
                    "scope": name,
                    "gate": "asset_composition_evidence",
                    "missing": missing,
                }
                violations.append(blocker)
                blockers.append(blocker)
                checked.append(result)
                continue

            usage = raw["usage"]
            origin = raw["origin"]
            occluded = raw["occluded_pixels"]
            contains_foreground = raw["contains_foreground_pixels"]
            contains_context = raw["contains_context_pixels"]
            responsive_safe = raw["responsive_safe"]
            operations = raw["derivation_operations"]
            if usage not in ASSET_USAGES:
                raise ValueError(f"Asset '{name}' has unsupported usage: {usage}")
            if origin not in ASSET_ORIGINS:
                raise ValueError(f"Asset '{name}' has unsupported origin: {origin}")
            if occluded not in OCCLUDED_PIXEL_STATES:
                raise ValueError(
                    f"Asset '{name}' occluded_pixels must be none, unknown, or reconstructed"
                )
            if not isinstance(contains_foreground, bool):
                raise ValueError(
                    f"Asset '{name}' contains_foreground_pixels must be boolean"
                )
            if not isinstance(contains_context, bool):
                raise ValueError(
                    f"Asset '{name}' contains_context_pixels must be boolean"
                )
            if not isinstance(responsive_safe, bool):
                raise ValueError(f"Asset '{name}' responsive_safe must be boolean")
            if not isinstance(operations, list) or not operations or not all(
                isinstance(operation, str) and operation for operation in operations
            ):
                raise ValueError(
                    f"Asset '{name}' derivation_operations must be non-empty strings"
                )

            normalized_operations = {operation.strip().lower() for operation in operations}
            reconstructed = sorted(RECONSTRUCTION_OPERATIONS.intersection(normalized_operations))
            unsupported_operations = sorted(
                normalized_operations - PROVENANCE_PRESERVING_OPERATIONS
            )
            if unsupported_operations:
                blocker = {
                    "scope": name,
                    "gate": "unsupported_derivation_operation",
                    "operations": unsupported_operations,
                    "allowed": sorted(PROVENANCE_PRESERVING_OPERATIONS),
                }
                violations.append(blocker)
                blockers.append(blocker)
            if status == "derived-deterministically" and (
                occluded != "none" or reconstructed or unsupported_operations
            ):
                blocker = {
                    "scope": name,
                    "gate": "invalid_deterministic_provenance",
                    "status": status,
                    "occluded_pixels": occluded,
                    "reconstruction_operations": reconstructed,
                }
                violations.append(blocker)
                blockers.append(blocker)
            if status in {"exact", "derived-deterministically"} and origin in {
                "generated",
                "inferred",
            }:
                blocker = {
                    "scope": name,
                    "gate": "invalid_exact_origin",
                    "status": status,
                    "origin": origin,
                }
                violations.append(blocker)
                blockers.append(blocker)
            if material and status in {"approximate", "missing"}:
                blocker = {
                    "scope": name,
                    "gate": "unresolved_material_asset",
                    "status": status,
                }
                violations.append(blocker)
                blockers.append(blocker)
            if not responsive_safe:
                blocker = {
                    "scope": name,
                    "gate": "asset_not_responsive_safe",
                }
                violations.append(blocker)
                blockers.append(blocker)
            if usage == "full-bleed-background" and (
                occluded != "none" or contains_foreground or reconstructed
            ):
                blocker = {
                    "scope": name,
                    "gate": "unsafe_full_bleed_background",
                    "occluded_pixels": occluded,
                    "contains_foreground_pixels": contains_foreground,
                    "reconstruction_operations": reconstructed,
                }
                violations.append(blocker)
                blockers.append(blocker)
            if usage == "component-surface" and (
                contains_context or contains_foreground or occluded != "none" or reconstructed
            ):
                blocker = {
                    "scope": name,
                    "gate": "contaminated_component_surface",
                    "contains_context_pixels": contains_context,
                    "contains_foreground_pixels": contains_foreground,
                    "occluded_pixels": occluded,
                    "reconstruction_operations": reconstructed,
                }
                violations.append(blocker)
                blockers.append(blocker)
            if usage == "isolated-asset" and (
                contains_context or contains_foreground or occluded != "none"
            ):
                blocker = {
                    "scope": name,
                    "gate": "contaminated_isolated_asset",
                }
                violations.append(blocker)
                blockers.append(blocker)

            asset_path = resolve_path(raw["path"], ledger_path, f"Asset '{name}'")
            if decoded is None:
                raise ValueError(f"Raster asset '{name}' cannot be decoded: {asset_path}")
            result["resolved_path"] = str(asset_path)
            computed_file_hash = file_sha256(asset_path)
            computed_pixel_hash = pixel_sha256(decoded)
            computed_dimensions = [decoded.width, decoded.height]
            computed_alpha = alpha_statistics(decoded)
            result["computed_file_sha256"] = computed_file_hash
            result["computed_pixel_sha256"] = computed_pixel_hash
            result["computed_intrinsic_dimensions"] = computed_dimensions
            result["computed_alpha"] = computed_alpha
            for field, actual in (
                ("file_sha256", computed_file_hash),
                ("pixel_sha256", computed_pixel_hash),
                ("intrinsic_dimensions", computed_dimensions),
            ):
                if raw.get(field) != actual:
                    blocker = {
                        "scope": name,
                        "gate": "asset_fingerprint_mismatch",
                        "field": field,
                        "declared": raw.get(field),
                        "computed": actual,
                    }
                    violations.append(blocker)
                    blockers.append(blocker)

            if computed_dimensions == [reference.width, reference.height] and (
                computed_pixel_hash == reference_hash
            ):
                blocker = {
                    "scope": name,
                    "gate": "full_reference_reuse",
                    "message": "The complete reference screenshot is loaded as an asset.",
                }
                violations.append(blocker)
                blockers.append(blocker)

            source_bounds = raw.get("source_bounds")
            if origin == "reference-crop":
                if (
                    not isinstance(source_bounds, list)
                    or len(source_bounds) != 4
                    or any(not isinstance(value, int) for value in source_bounds)
                ):
                    blocker = {
                        "scope": name,
                        "gate": "missing_reference_crop_bounds",
                    }
                    violations.append(blocker)
                    blockers.append(blocker)
                else:
                    x, y, crop_width, crop_height = source_bounds
                    bounds_valid = (
                        x >= 0
                        and y >= 0
                        and crop_width > 0
                        and crop_height > 0
                        and x + crop_width <= reference.width
                        and y + crop_height <= reference.height
                    )
                    if not bounds_valid:
                        blocker = {
                            "scope": name,
                            "gate": "invalid_reference_crop_bounds",
                            "source_bounds": source_bounds,
                        }
                        violations.append(blocker)
                        blockers.append(blocker)
                    else:
                        crop = reference.crop(
                            (x, y, x + crop_width, y + crop_height)
                        ).convert("RGB")
                        if decoded.size != crop.size:
                            blocker = {
                                "scope": name,
                                "gate": "reference_crop_dimensions",
                                "asset": list(decoded.size),
                                "crop": list(crop.size),
                            }
                            violations.append(blocker)
                            blockers.append(blocker)
                        else:
                            candidate_rgb = decoded.convert("RGB")
                            if "A" in decoded.getbands():
                                difference = ImageChops.difference(candidate_rgb, crop)
                                alpha = decoded.getchannel("A")
                                difference = Image.composite(
                                    difference,
                                    Image.new("RGB", difference.size, 0),
                                    alpha,
                                )
                                crop_matches = difference.getbbox() is None
                            else:
                                crop_matches = pixel_sha256(candidate_rgb) == pixel_sha256(crop)
                            if not crop_matches:
                                blocker = {
                                    "scope": name,
                                    "gate": "reference_crop_pixel_mismatch",
                                }
                                violations.append(blocker)
                                blockers.append(blocker)
                        crop_area = crop_width * crop_height
                        reference_area = reference.width * reference.height
                        if (
                            usage in {"full-bleed-background", "component-surface"}
                            and crop_area / max(1, reference_area) >= 0.8
                        ):
                            blocker = {
                                "scope": name,
                                "gate": "near_full_reference_plate",
                                "coverage": round(crop_area / reference_area, 6),
                            }
                            violations.append(blocker)
                            blockers.append(blocker)

            if usage == "isolated-asset":
                declared_alpha = raw.get("alpha")
                if declared_alpha != computed_alpha:
                    blocker = {
                        "scope": name,
                        "gate": "asset_alpha_evidence_mismatch",
                        "declared": declared_alpha,
                        "computed": computed_alpha,
                    }
                    violations.append(blocker)
                    blockers.append(blocker)
                if (
                    not computed_alpha["has_alpha"]
                    or computed_alpha["transparent_fraction"] < 0.05
                    or computed_alpha["perimeter_transparent_fraction"] < 0.05
                ):
                    blocker = {
                        "scope": name,
                        "gate": "isolated_asset_alpha",
                        "message": (
                            "An isolated raster needs a meaningful transparent silhouette; "
                            "one transparent pixel cannot validate an opaque screenshot crop."
                        ),
                        "computed": computed_alpha,
                    }
                    violations.append(blocker)
                    blockers.append(blocker)
        checked.append(result)
    reference_crops: list[dict[str, Any]] = []
    for asset in checked:
        bounds = asset.get("source_bounds")
        if (
            asset.get("material") is True
            and asset.get("origin") == "reference-crop"
            and isinstance(bounds, list)
            and len(bounds) == 4
            and all(isinstance(value, int) for value in bounds)
        ):
            x, y, width, height = bounds
            if (
                x >= 0
                and y >= 0
                and width > 0
                and height > 0
                and x + width <= reference.width
                and y + height <= reference.height
            ):
                reference_crops.append(
                    {
                        "name": asset["name"],
                        "rectangle": (x, y, x + width, y + height),
                    }
                )
    if len(reference_crops) >= 2:
        rectangles = [crop["rectangle"] for crop in reference_crops]
        reference_area = max(1, reference.width * reference.height)
        union_area = rectangle_union_area(rectangles)
        union_coverage = union_area / reference_area
        suspicious_components: list[dict[str, Any]] = []
        for component in adjacent_rectangle_components(rectangles):
            if len(component) < 2:
                continue
            component_rectangles = [rectangles[index] for index in component]
            bounds = [
                min(rectangle[0] for rectangle in component_rectangles),
                min(rectangle[1] for rectangle in component_rectangles),
                max(rectangle[2] for rectangle in component_rectangles),
                max(rectangle[3] for rectangle in component_rectangles),
            ]
            bounding_area = max(1, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))
            component_union = rectangle_union_area(component_rectangles)
            bounding_coverage = bounding_area / reference_area
            fill_ratio = component_union / bounding_area
            if bounding_coverage >= 0.8 and fill_ratio >= 0.75:
                suspicious_components.append(
                    {
                        "assets": [reference_crops[index]["name"] for index in component],
                        "bounds": bounds,
                        "reference_coverage": round(bounding_coverage, 6),
                        "fill_ratio": round(fill_ratio, 6),
                    }
                )
        if union_coverage >= 0.8 or suspicious_components:
            blocker = {
                "scope": "asset-ledger",
                "gate": "cumulative_reference_crop_plate",
                "assets": [crop["name"] for crop in reference_crops],
                "union_coverage": round(union_coverage, 6),
                "adjacent_components": suspicious_components,
                "message": (
                    "Material reference crops cumulatively reconstruct a near/full-page "
                    "screenshot plate."
                ),
            }
            violations.append(blocker)
            blockers.append(blocker)
    return checked, violations, blockers


def validate_states(
    manifest: dict[str, Any], run: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    states = manifest.get("states")
    if not isinstance(states, list) or not states:
        raise ValueError("Responsive manifest states must be a non-empty array")
    by_id: dict[str, dict[str, Any]] = {}
    for index, state_value in enumerate(states):
        if not isinstance(state_value, dict):
            raise ValueError(f"Responsive state {index} must be an object")
        state_id = state_value.get("id")
        if not isinstance(state_id, str) or not state_id.strip():
            raise ValueError(f"Responsive state {index} requires a non-empty id")
        if state_id in by_id:
            raise ValueError(f"Duplicate responsive state: {state_id}")
        for field in ("material", "primary", "full_matrix"):
            if not isinstance(state_value.get(field), bool):
                raise ValueError(f"Responsive state '{state_id}' {field} must be boolean")
        action_hash = state_value.get("action_hash")
        if not isinstance(action_hash, str) or not action_hash.strip():
            raise ValueError(f"Responsive state '{state_id}' requires action_hash")
        by_id[state_id] = state_value
    primary = [state for state in states if state["primary"]]
    if len(primary) != 1 or primary[0]["full_matrix"] is not True:
        raise ValueError("Exactly one primary state must require the full matrix")
    computed_hash = canonical_sha256(states)
    if run.get("state_set_hash") != computed_hash:
        raise ValueError(
            "Responsive run.state_set_hash does not match the canonical states array"
        )
    return states, by_id


def validate_required_elements(
    manifest: dict[str, Any], state_ids: set[str]
) -> dict[str, dict[str, Any]]:
    definitions = manifest.get("required_elements")
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("Responsive manifest required_elements must be non-empty")
    by_name: dict[str, dict[str, Any]] = {}
    for index, definition in enumerate(definitions):
        if not isinstance(definition, dict):
            raise ValueError(f"Required element {index} must be an object")
        name = definition.get("name")
        selector = definition.get("selector")
        states = definition.get("states")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Required element {index} requires a name")
        if name in by_name:
            raise ValueError(f"Duplicate required element name: {name}")
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError(f"Required element '{name}' requires a selector")
        if not isinstance(states, list) or not states or any(
            not isinstance(state, str) or state not in state_ids for state in states
        ):
            raise ValueError(
                f"Required element '{name}' states must reference known state ids"
            )
        for field in (
            "must_intersect_viewport",
            "must_fit_horizontally",
            "must_fit_vertically",
        ):
            if not isinstance(definition.get(field), bool):
                raise ValueError(f"Required element '{name}' {field} must be boolean")
        by_name[name] = definition
    return by_name


def load_visual_reviews(path: Path, run_id: str) -> dict[str, dict[str, Any]]:
    payload = load_object(path, "Visual review")
    if payload.get("schema_version") != "1.0":
        raise ValueError("Visual review schema_version must be '1.0'")
    if payload.get("run_id") != run_id:
        raise ValueError("Visual review run_id must match responsive capture run_id")
    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("Visual review must contain a reviews array")
    by_case: dict[str, dict[str, Any]] = {}
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise ValueError(f"Visual review entry {index} must be an object")
        case_id = review.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"Visual review entry {index} requires case_id")
        if case_id in by_case:
            raise ValueError(f"Duplicate visual review case_id: {case_id}")
        by_case[case_id] = review
    return by_case


def rectangles_overlap(first: list[float], second: list[float]) -> bool:
    return (
        min(first[0] + first[2], second[0] + second[2])
        > max(first[0], second[0])
        and min(first[1] + first[3], second[1] + second[3])
        > max(first[1], second[1])
    )


def validate_visible_resources(
    case_id: str,
    resources: Any,
    assets_by_name: dict[str, dict[str, Any]],
    reference_hash: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(resources, list):
        raise ValueError(f"Case '{case_id}' probe.visible_resources must be an array")
    violations: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    for index, resource in enumerate(resources):
        if not isinstance(resource, dict):
            raise ValueError(f"Case '{case_id}' resource {index} must be an object")
        resource_type = resource.get("type")
        url = resource.get("url")
        if not isinstance(resource_type, str) or not resource_type:
            raise ValueError(f"Case '{case_id}' resource {index} requires a type")
        if not isinstance(url, str) or not url:
            raise ValueError(f"Case '{case_id}' resource {index} requires a url")
        if resource.get("loaded") is not True:
            append_violation(
                violations, case_id, "visible_resource_not_loaded", url=url
            )
        ledger_name = resource.get("ledger_name")
        if resource_type in VISIBLE_RASTER_RESOURCE_TYPES or resource_type == "font":
            if not isinstance(ledger_name, str) or ledger_name not in assets_by_name:
                append_violation(
                    violations,
                    case_id,
                    "unledgered_visible_resource",
                    url=url,
                    type=resource_type,
                    ledger_name=ledger_name,
                )
            else:
                ledger_asset = assets_by_name[ledger_name]
                expected_hash = ledger_asset.get("computed_pixel_sha256")
                resource_hash = resource.get("decoded_pixel_sha256")
                if resource_type in VISIBLE_RASTER_RESOURCE_TYPES:
                    if not isinstance(resource_hash, str) or not resource_hash:
                        append_violation(
                            violations,
                            case_id,
                            "visible_resource_fingerprint_missing",
                            url=url,
                        )
                    elif expected_hash != resource_hash:
                        append_violation(
                            violations,
                            case_id,
                            "visible_resource_ledger_mismatch",
                            url=url,
                            resource_hash=resource_hash,
                            ledger_hash=expected_hash,
                        )
                    if resource_hash == reference_hash:
                        append_violation(
                            violations,
                            case_id,
                            "full_reference_reuse",
                            url=url,
                        )
        normalized.append(resource)
    return normalized, violations


def validate_sweep(
    manifest: dict[str, Any], primary_state_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sweep = manifest.get("sweep")
    if not isinstance(sweep, dict):
        raise ValueError("Responsive manifest requires a continuous sweep object")
    if sweep.get("harness_collected") is not True:
        raise ValueError("Responsive sweep must be produced by the capture harness")
    enabled = sweep.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("Responsive sweep.enabled must be boolean")
    samples = sweep.get("samples")
    if not isinstance(samples, list):
        raise ValueError("Responsive sweep.samples must be an array")
    if not enabled:
        if samples:
            raise ValueError("Disabled responsive sweep must not contain samples")
        if sweep.get("complete") is not False:
            raise ValueError("Disabled responsive sweep must have complete:false")
        return sweep, []
    if not samples:
        raise ValueError("Enabled responsive sweep.samples must be non-empty")
    violations: list[dict[str, Any]] = []
    primary_samples = [
        sample
        for sample in samples
        if isinstance(sample, dict) and sample.get("state_id") == primary_state_id
    ]
    widths: list[int] = []
    for index, sample in enumerate(primary_samples):
        width = sample.get("width")
        if not isinstance(width, int):
            raise ValueError(f"Sweep sample {index} width must be an integer")
        widths.append(width)
        overflow = sample.get("horizontal_overflow_px")
        if not isinstance(overflow, (int, float)):
            raise ValueError(f"Sweep sample {index} horizontal_overflow_px must be numeric")
        if float(overflow) > MAX_HORIZONTAL_OVERFLOW_PX:
            append_violation(
                violations,
                f"sweep-{width}",
                "horizontal_overflow",
                actual=overflow,
                maximum=MAX_HORIZONTAL_OVERFLOW_PX,
            )
        for field in (
            "overflow_elements",
            "clipped_required_elements",
            "unexpected_overlaps",
            "missing_required_elements",
            "failed_resources",
            "console_errors",
            "settle_errors",
        ):
            value = sample.get(field)
            if not isinstance(value, list):
                raise ValueError(f"Sweep sample {index} {field} must be an array")
            if value:
                append_violation(
                    violations,
                    f"sweep-{width}",
                    field,
                    actual=value,
                    expected=[],
                )
    unique_widths = sorted(set(widths))
    if (
        not unique_widths
        or unique_widths[0] > MIN_SWEEP_WIDTH
        or unique_widths[-1] < MAX_SWEEP_WIDTH
    ):
        append_violation(
            violations,
            "sweep",
            "continuous_width_coverage",
            actual_min=unique_widths[0] if unique_widths else None,
            actual_max=unique_widths[-1] if unique_widths else None,
            required_min=MIN_SWEEP_WIDTH,
            required_max=MAX_SWEEP_WIDTH,
        )
    gaps = [
        [left, right]
        for left, right in zip(unique_widths, unique_widths[1:])
        if right - left > MAX_SWEEP_GAP_PX
    ]
    if gaps:
        append_violation(
            violations,
            "sweep",
            "continuous_width_gap",
            gaps=gaps,
            maximum_gap=MAX_SWEEP_GAP_PX,
        )
    return sweep, violations


def discovered_boundaries(
    manifest: dict[str, Any]
) -> tuple[set[int], set[int], list[dict[str, Any]]]:
    queries = manifest.get("discovered_media_queries")
    if not isinstance(queries, list):
        raise ValueError("Responsive manifest discovered_media_queries must be an array")
    widths: set[int] = set()
    heights: set[int] = set()
    extraction_errors: list[dict[str, Any]] = []
    for index, item in enumerate(queries):
        if not isinstance(item, dict):
            raise ValueError(f"Discovered media query {index} must be an object")
        kind = item.get("kind", "media")
        if kind not in {"media", "container"}:
            raise ValueError(f"Discovered query {index} has unsupported kind '{kind}'")
        query = item.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"Discovered media query {index} requires query text")
        boundaries = item.get("extracted_boundaries")
        if not isinstance(boundaries, list):
            raise ValueError(
                f"Discovered media query {index} requires harness-extracted boundaries"
            )
        item_errors = item.get("boundary_extraction_errors")
        if not isinstance(item_errors, list):
            raise ValueError(
                f"Discovered media query {index} requires boundary_extraction_errors"
            )
        if kind == "media":
            for error in item_errors:
                if not isinstance(error, dict):
                    raise ValueError(
                        f"Discovered media query {index} boundary error must be an object"
                    )
                extraction_errors.append(
                    {
                        "query_index": index,
                        "kind": kind,
                        "query": query,
                        **error,
                    }
                )
        if kind != "media":
            continue
        for boundary in boundaries:
            if not isinstance(boundary, dict):
                raise ValueError(f"Discovered media query {index} boundary must be an object")
            dimension = boundary.get("dimension")
            value = boundary.get("boundary_value")
            if dimension not in {"width", "height"} or not isinstance(value, int) or value <= 0:
                raise ValueError(
                    f"Discovered media query {index} has an invalid extracted boundary"
                )
            (widths if dimension == "width" else heights).add(value)
    return widths, heights, extraction_errors


def validate_device_emulation(
    case_id: str,
    case_class: str,
    viewport: dict[str, Any],
    probe: dict[str, Any],
    run: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    violations: list[dict[str, Any]] = []
    width = viewport["width"]
    height = viewport["height"]
    zoom = viewport.get("zoom_percent", 100)
    semantics = expected_device_semantics(case_class, width)
    declared_semantics = {
        field: viewport.get(field)
        for field in ("device_class", "is_mobile", "has_touch")
    }
    if declared_semantics != semantics:
        append_violation(
            violations,
            case_id,
            "viewport_device_semantics",
            case_class=case_class,
            actual=declared_semantics,
            expected=semantics,
        )

    emulation = probe.get("device_emulation")
    if not isinstance(emulation, dict):
        raise ValueError(f"Case '{case_id}' probe.device_emulation must be an object")
    if emulation.get("browser_name") != run["browser_name"]:
        append_violation(
            violations,
            case_id,
            "device_emulation_browser",
            actual=emulation.get("browser_name"),
            expected=run["browser_name"],
        )
    expected_css_width = int(width * 100 / zoom + 0.5)
    expected_css_height = int(height * 100 / zoom + 0.5)
    expected_assessment = {
        **semantics,
        "css_viewport_width": expected_css_width,
        "css_viewport_height": expected_css_height,
        "screen_width": width,
        "screen_height": height,
    }
    declared_expected = emulation.get("expected")
    if not isinstance(declared_expected, dict):
        raise ValueError(
            f"Case '{case_id}' probe.device_emulation.expected must be an object"
        )
    expectation_mismatches = {
        field: {
            "declared": declared_expected.get(field),
            "computed": expected,
        }
        for field, expected in expected_assessment.items()
        if declared_expected.get(field) != expected
    }
    if expectation_mismatches:
        append_violation(
            violations,
            case_id,
            "device_emulation_expectation",
            mismatches=expectation_mismatches,
        )

    environment = probe.get("device_environment")
    if not isinstance(environment, dict):
        raise ValueError(f"Case '{case_id}' probe.device_environment must be an object")
    navigator_evidence = environment.get("navigator")
    touch_evidence = environment.get("touch")
    screen_evidence = environment.get("screen")
    safe_area = environment.get("safe_area_insets")
    preferences = environment.get("preferences")
    for label, value in (
        ("navigator", navigator_evidence),
        ("touch", touch_evidence),
        ("screen", screen_evidence),
        ("safe_area_insets", safe_area),
        ("preferences", preferences),
    ):
        if not isinstance(value, dict):
            raise ValueError(
                f"Case '{case_id}' probe.device_environment.{label} must be an object"
            )

    user_agent = navigator_evidence.get("user_agent")
    if not isinstance(user_agent, str) or not user_agent.strip():
        raise ValueError(
            f"Case '{case_id}' browser navigator.user_agent must be non-empty"
        )
    max_touch_points = navigator_evidence.get("max_touch_points")
    if (
        not isinstance(max_touch_points, (int, float))
        or isinstance(max_touch_points, bool)
        or max_touch_points < 0
    ):
        raise ValueError(
            f"Case '{case_id}' browser navigator.max_touch_points must be non-negative"
        )
    user_agent_data = navigator_evidence.get("user_agent_data")
    if user_agent_data is not None and not isinstance(user_agent_data, dict):
        raise ValueError(
            f"Case '{case_id}' browser navigator.user_agent_data must be an object or null"
        )
    if isinstance(user_agent_data, dict) and not isinstance(
        user_agent_data.get("mobile"), bool
    ):
        raise ValueError(
            f"Case '{case_id}' browser navigator.user_agent_data.mobile must be boolean"
        )
    for field in (
        "touch_event_supported",
        "touch_constructor_supported",
        "pointer_event_supported",
        "pointer_coarse",
        "any_pointer_coarse",
        "hover_none",
        "any_hover_none",
    ):
        if not isinstance(touch_evidence.get(field), bool):
            raise ValueError(
                f"Case '{case_id}' browser touch.{field} must be boolean"
            )
    for field in ("width", "height", "avail_width", "avail_height"):
        if not isinstance(screen_evidence.get(field), (int, float)) or isinstance(
            screen_evidence.get(field), bool
        ):
            raise ValueError(
                f"Case '{case_id}' browser screen.{field} must be numeric"
            )
    if not isinstance(safe_area.get("css_env_supported"), bool):
        raise ValueError(
            f"Case '{case_id}' browser safe_area_insets.css_env_supported must be boolean"
        )
    for field in ("top_px", "right_px", "bottom_px", "left_px"):
        if (
            not isinstance(safe_area.get(field), (int, float))
            or isinstance(safe_area.get(field), bool)
            or safe_area[field] < 0
        ):
            raise ValueError(
                f"Case '{case_id}' browser safe_area_insets.{field} must be non-negative"
            )
    for field in (
        "prefers_color_scheme_dark",
        "prefers_color_scheme_light",
        "prefers_color_scheme_no_preference",
        "prefers_reduced_motion_reduce",
    ):
        if not isinstance(preferences.get(field), bool):
            raise ValueError(
                f"Case '{case_id}' browser preferences.{field} must be boolean"
            )

    visual_viewport = probe.get("visual_viewport")
    if not isinstance(visual_viewport, dict):
        raise ValueError(f"Case '{case_id}' probe.visual_viewport must be an object")
    for field in ("width", "height", "scale", "offset_left", "offset_top"):
        if not isinstance(visual_viewport.get(field), (int, float)) or isinstance(
            visual_viewport.get(field), bool
        ):
            raise ValueError(
                f"Case '{case_id}' probe.visual_viewport.{field} must be numeric"
            )

    lower_user_agent = user_agent.lower()
    android_mobile = "android" in lower_user_agent and "mobile" in lower_user_agent
    ua_data_mobile = (
        user_agent_data.get("mobile") if isinstance(user_agent_data, dict) else None
    )
    mobile_identity = ua_data_mobile is True or any(
        token in lower_user_agent for token in ("iphone", "ipod")
    ) or android_mobile
    tablet_identity = any(
        token in lower_user_agent for token in ("ipad", "tablet", "android")
    ) and not android_mobile
    has_touch = max_touch_points > 0 and touch_evidence["touch_event_supported"]
    computed_actual = {
        "mobile_identity": mobile_identity,
        "tablet_identity": tablet_identity,
        "has_touch": has_touch,
        "max_touch_points": max_touch_points,
        "pointer_coarse": touch_evidence["pointer_coarse"],
        "any_pointer_coarse": touch_evidence["any_pointer_coarse"],
        "hover_none": touch_evidence["hover_none"],
        "css_viewport_width": probe.get("inner_width"),
        "css_viewport_height": probe.get("inner_height"),
        "screen_width": screen_evidence["width"],
        "screen_height": screen_evidence["height"],
        "visual_viewport_present": True,
        "safe_area_css_env_supported": safe_area["css_env_supported"],
    }
    declared_actual = emulation.get("actual")
    if not isinstance(declared_actual, dict):
        raise ValueError(
            f"Case '{case_id}' probe.device_emulation.actual must be an object"
        )
    actual_mismatches = {
        field: {"declared": declared_actual.get(field), "computed": computed}
        for field, computed in computed_actual.items()
        if declared_actual.get(field) != computed
    }
    if actual_mismatches:
        append_violation(
            violations,
            case_id,
            "device_emulation_assessment",
            mismatches=actual_mismatches,
        )

    emulation_errors = probe.get("emulation_errors")
    assessed_errors = emulation.get("errors")
    if not isinstance(emulation_errors, list) or not isinstance(assessed_errors, list):
        raise ValueError(
            f"Case '{case_id}' device emulation errors must be arrays"
        )
    if emulation_errors != assessed_errors:
        append_violation(
            violations,
            case_id,
            "device_emulation_error_identity",
            probe_errors=emulation_errors,
            assessment_errors=assessed_errors,
        )
    if emulation_errors or assessed_errors:
        append_violation(
            violations,
            case_id,
            "device_emulation_errors",
            actual=emulation_errors,
            expected=[],
        )

    evidence_failures: dict[str, Any] = {}
    if not safe_area["css_env_supported"]:
        evidence_failures["safe_area_css_env_supported"] = False
    if not preferences["prefers_reduced_motion_reduce"]:
        evidence_failures["prefers_reduced_motion_reduce"] = False
    color_scheme = run["color_scheme"]
    if color_scheme == "dark" and not preferences["prefers_color_scheme_dark"]:
        evidence_failures["prefers_color_scheme_dark"] = False
    if color_scheme == "light" and not preferences["prefers_color_scheme_light"]:
        evidence_failures["prefers_color_scheme_light"] = False
    if semantics["has_touch"]:
        if not has_touch:
            evidence_failures["has_touch"] = computed_actual["has_touch"]
        if not (
            touch_evidence["pointer_coarse"]
            or touch_evidence["any_pointer_coarse"]
        ):
            evidence_failures["coarse_pointer"] = False
        if not touch_evidence["hover_none"]:
            evidence_failures["hover_none"] = False
        if probe.get("inner_width") != expected_css_width:
            evidence_failures["css_viewport_width"] = probe.get("inner_width")
        if [screen_evidence["width"], screen_evidence["height"]] != [width, height]:
            evidence_failures["screen_dimensions"] = [
                screen_evidence["width"],
                screen_evidence["height"],
            ]
    if semantics["device_class"] == "mobile" and not mobile_identity:
        evidence_failures["mobile_identity"] = False
    if semantics["device_class"] == "tablet" and not tablet_identity:
        evidence_failures["tablet_identity"] = False
    if evidence_failures:
        append_violation(
            violations,
            case_id,
            "device_emulation_evidence",
            actual=evidence_failures,
            expected=expected_assessment,
        )

    normalized = {
        "browser_name": emulation.get("browser_name"),
        "expected": expected_assessment,
        "actual": computed_actual,
        "errors": emulation_errors,
        "audit_validated": not violations,
    }
    return normalized, violations


def validate_case(
    case: dict[str, Any],
    manifest_path: Path,
    run: dict[str, Any],
    states_by_id: dict[str, dict[str, Any]],
    required_definitions: dict[str, dict[str, Any]],
    assets_by_name: dict[str, dict[str, Any]],
    reference_hash: str,
    visual_review: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("Every responsive case requires a non-empty id")
    case_class, width, height, zoom, text_zoom, base_dpr = case_key(case)
    state_id = case.get("state_id")
    if not isinstance(state_id, str) or state_id not in states_by_id:
        raise ValueError(f"Case '{case_id}' requires a known state_id")
    if case.get("collector_run_id") != run["run_id"]:
        raise ValueError(f"Case '{case_id}' collector_run_id does not match run_id")
    parse_timestamp(case.get("captured_at"), f"Case '{case_id}' captured_at")
    violations: list[dict[str, Any]] = []
    if "fatal_error" not in case:
        raise ValueError(f"Case '{case_id}' requires fatal_error evidence")
    if case.get("fatal_error") is not None:
        append_violation(
            violations,
            case_id,
            "fatal_capture_error",
            actual=case.get("fatal_error"),
            expected=None,
        )
    if not isinstance(case.get("byte_identical_repeat_capture"), bool):
        raise ValueError(
            f"Case '{case_id}' byte_identical_repeat_capture must be boolean"
        )
    viewport = case["viewport"]
    actual_dpr = float(viewport["dpr"])
    screenshot_path = resolve_path(case.get("screenshot"), manifest_path, f"Case '{case_id}' screenshot")
    repeat_path = resolve_path(
        case.get("repeat_screenshot"), manifest_path, f"Case '{case_id}' repeat screenshot"
    )
    screenshot, screenshot_format = load_capture(screenshot_path)
    repeat, repeat_format = load_capture(repeat_path)
    screenshot_hash = pixel_sha256(screenshot)
    repeat_hash = pixel_sha256(repeat)
    screenshot_file_hash = file_sha256(screenshot_path)
    repeat_file_hash = file_sha256(repeat_path)
    expected_size = (round(width * base_dpr), round(height * base_dpr))
    if screenshot_format not in LOSSLESS_FORMATS:
        append_violation(
            violations,
            case_id,
            "lossless_format",
            path=str(screenshot_path),
            actual=screenshot_format,
        )
    if repeat_format not in LOSSLESS_FORMATS:
        append_violation(
            violations,
            case_id,
            "lossless_format",
            path=str(repeat_path),
            actual=repeat_format,
        )
    if screenshot.size != expected_size:
        append_violation(
            violations,
            case_id,
            "capture_dimensions",
            actual=list(screenshot.size),
            expected=list(expected_size),
        )
    if repeat.size != screenshot.size:
        append_violation(
            violations,
            case_id,
            "repeat_capture_dimensions",
            actual=list(repeat.size),
            expected=list(screenshot.size),
        )
    stable = screenshot.size == repeat.size and ImageChops.difference(
        screenshot, repeat
    ).getbbox() is None
    if not stable:
        append_violation(
            violations,
            case_id,
            "pixel_identical_repeat_capture",
        )
    if case["byte_identical_repeat_capture"] is not stable:
        append_violation(
            violations,
            case_id,
            "repeat_capture_attestation",
            declared=case["byte_identical_repeat_capture"],
            computed=stable,
        )
    for field, declared, computed in (
        ("screenshot_file_sha256", case.get("screenshot_file_sha256"), screenshot_file_hash),
        (
            "repeat_screenshot_file_sha256",
            case.get("repeat_screenshot_file_sha256"),
            repeat_file_hash,
        ),
        ("screenshot_pixel_sha256", case.get("screenshot_pixel_sha256"), screenshot_hash),
        ("repeat_pixel_sha256", case.get("repeat_pixel_sha256"), repeat_hash),
    ):
        if declared != computed:
            append_violation(
                violations,
                case_id,
                "capture_fingerprint_mismatch",
                field=field,
                declared=declared,
                computed=computed,
            )

    probe = case.get("probe")
    if not isinstance(probe, dict):
        raise ValueError(f"Case '{case_id}' requires a probe object")
    if probe.get("harness_collected") is not True or probe.get("run_id") != run["run_id"]:
        raise ValueError(f"Case '{case_id}' probe must be collected by this harness run")
    if probe.get("route") != run["route"]:
        append_violation(
            violations,
            case_id,
            "route_mismatch",
            actual=probe.get("route"),
            expected=run["route"],
        )
    numeric_fields = (
        "inner_width",
        "inner_height",
        "device_pixel_ratio",
        "document_client_width",
        "document_scroll_width",
        "body_scroll_width",
        "horizontal_overflow_px",
    )
    for field in numeric_fields:
        if not isinstance(probe.get(field), (int, float)):
            raise ValueError(f"Case '{case_id}' probe.{field} must be numeric")
    expected_inner_width = width * 100 / zoom
    expected_inner_height = height * 100 / zoom
    if abs(float(probe["inner_width"]) - expected_inner_width) > ZOOM_TOLERANCE_PX:
        append_violation(
            violations,
            case_id,
            "inner_width",
            actual=probe["inner_width"],
            expected=round(expected_inner_width, 3),
        )
    if abs(float(probe["inner_height"]) - expected_inner_height) > ZOOM_TOLERANCE_PX:
        append_violation(
            violations,
            case_id,
            "inner_height",
            actual=probe["inner_height"],
            expected=round(expected_inner_height, 3),
        )
    if abs(float(probe["device_pixel_ratio"]) - actual_dpr) > 0.01:
        append_violation(
            violations,
            case_id,
            "device_pixel_ratio",
            actual=probe["device_pixel_ratio"],
            expected=actual_dpr,
        )
    if probe.get("text_zoom_percent") != text_zoom:
        append_violation(
            violations,
            case_id,
            "text_zoom",
            actual=probe.get("text_zoom_percent"),
            expected=text_zoom,
        )
    device_emulation, device_violations = validate_device_emulation(
        case_id,
        case_class,
        viewport,
        probe,
        run,
    )
    violations.extend(device_violations)
    calculated_overflow = max(
        0.0,
        float(probe["document_scroll_width"])
        - float(probe["document_client_width"]),
        float(probe["body_scroll_width"])
        - float(probe["document_client_width"]),
        float(probe["horizontal_overflow_px"]),
    )
    if calculated_overflow > MAX_HORIZONTAL_OVERFLOW_PX:
        append_violation(
            violations,
            case_id,
            "horizontal_overflow",
            actual=round(calculated_overflow, 3),
            maximum=MAX_HORIZONTAL_OVERFLOW_PX,
        )

    required_elements = probe.get("required_elements")
    if not isinstance(required_elements, list) or not required_elements:
        raise ValueError(f"Case '{case_id}' probe.required_elements must be non-empty")
    elements_by_name: dict[str, dict[str, Any]] = {}
    for element in required_elements:
        if not isinstance(element, dict) or not isinstance(element.get("name"), str):
            raise ValueError(f"Case '{case_id}' has an invalid required element")
        element_name = element["name"]
        if element_name in elements_by_name:
            raise ValueError(f"Case '{case_id}' repeats required element '{element_name}'")
        elements_by_name[element_name] = element
        if element.get("visible") is not True or element.get("clipped") is not False:
            append_violation(
                violations,
                case_id,
                "required_element_visibility",
                element=element.get("name"),
                visible=element.get("visible"),
                clipped=element.get("clipped"),
            )
        rect = element.get("rect")
        if (
            not isinstance(rect, list)
            or len(rect) != 4
            or any(not isinstance(value, (int, float)) for value in rect)
            or rect[2] <= 0
            or rect[3] <= 0
        ):
            raise ValueError(
                f"Case '{case_id}' required element '{element.get('name')}' needs "
                "rect [x, y, width, height]"
            )
        definition = required_definitions.get(element_name)
        if definition is None or state_id not in definition["states"]:
            append_violation(
                violations,
                case_id,
                "unexpected_required_element",
                element=element_name,
            )
            continue
        if element.get("selector") != definition["selector"] or element.get("count") != 1:
            append_violation(
                violations,
                case_id,
                "required_element_identity",
                element=element_name,
                selector=element.get("selector"),
                expected_selector=definition["selector"],
                count=element.get("count"),
            )
        x, y, element_width, element_height = [float(value) for value in rect]
        intersects = (
            min(x + element_width, float(probe["inner_width"])) > max(x, 0.0)
            and min(y + element_height, float(probe["inner_height"])) > max(y, 0.0)
        )
        if definition["must_intersect_viewport"] and not intersects:
            append_violation(
                violations,
                case_id,
                "required_element_outside_viewport",
                element=element_name,
                rect=rect,
            )
        if definition["must_fit_horizontally"] and (
            x < -MAX_HORIZONTAL_OVERFLOW_PX
            or x + element_width
            > float(probe["inner_width"]) + MAX_HORIZONTAL_OVERFLOW_PX
        ):
            append_violation(
                violations,
                case_id,
                "required_element_horizontal_fit",
                element=element_name,
                rect=rect,
            )
        if definition["must_fit_vertically"] and (
            y < -MAX_HORIZONTAL_OVERFLOW_PX
            or y + element_height
            > float(probe["inner_height"]) + MAX_HORIZONTAL_OVERFLOW_PX
        ):
            append_violation(
                violations,
                case_id,
                "required_element_vertical_fit",
                element=element_name,
                rect=rect,
            )

    expected_names = {
        name
        for name, definition in required_definitions.items()
        if state_id in definition["states"]
    }
    missing_names = sorted(expected_names - set(elements_by_name))
    if missing_names:
        append_violation(
            violations,
            case_id,
            "missing_required_elements",
            actual=missing_names,
            expected=[],
        )
    for name, definition in required_definitions.items():
        if name not in elements_by_name or state_id not in definition["states"]:
            continue
        first = [float(value) for value in elements_by_name[name]["rect"]]
        for other_name in definition.get("disallow_overlap_with", []):
            other = elements_by_name.get(other_name)
            if other is not None and rectangles_overlap(
                first, [float(value) for value in other["rect"]]
            ):
                append_violation(
                    violations,
                    case_id,
                    "computed_unexpected_overlap",
                    elements=sorted([name, other_name]),
                )

    empty_probe_lists = (
        "overflow_elements",
        "unexpected_overlaps",
        "missing_required_elements",
        "duplicate_required_elements",
        "failed_resources",
        "console_errors",
        "page_errors",
        "blocked_write_requests",
        "settle_errors",
        "cssom_errors",
        "unlinked_visible_resources",
        "undecoded_visible_rasters",
        "dialogs",
        "popups",
    )
    for field in empty_probe_lists:
        value = probe.get(field)
        if not isinstance(value, list):
            raise ValueError(f"Case '{case_id}' probe.{field} must be an array")
        if value:
            append_violation(
                violations,
                case_id,
                field,
                actual=value,
                expected=[],
            )

    visible_resources, resource_violations = validate_visible_resources(
        case_id,
        probe.get("visible_resources"),
        assets_by_name,
        reference_hash,
    )
    violations.extend(resource_violations)

    if not isinstance(visual_review, dict):
        append_violation(
            violations,
            case_id,
            "missing_independent_visual_review",
        )
        visual_review = {
            "status": "missing",
            "reviewer": None,
            "reviewed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "reviewed_screenshot_pixel_sha256": None,
            "unexpected_seams": ["not reviewed"],
            "ghosted_artifacts": ["not reviewed"],
            "distorted_assets": ["not reviewed"],
            "background_mismatches": ["not reviewed"],
        }
    elif visual_review.get("case_id") != case_id:
        raise ValueError(f"Visual review case id mismatch for '{case_id}'")
    if visual_review.get("status") != "pass":
        append_violation(
            violations,
            case_id,
            "visual_review_status",
            actual=visual_review.get("status"),
            expected="pass",
        )
    if visual_review.get("reviewed_screenshot_pixel_sha256") != screenshot_hash:
        append_violation(
            violations,
            case_id,
            "visual_review_capture_mismatch",
            reviewed=visual_review.get("reviewed_screenshot_pixel_sha256"),
            captured=screenshot_hash,
        )
    if visual_review.get("reviewer") not in {
        "codex-visual-inspection",
        "human-visual-inspection",
        "automated-vision-review",
    }:
        append_violation(
            violations,
            case_id,
            "visual_review_method",
            actual=visual_review.get("reviewer"),
        )
    parse_timestamp(
        visual_review.get("reviewed_at"), f"Case '{case_id}' visual_review.reviewed_at"
    )
    for field in (
        "unexpected_seams",
        "ghosted_artifacts",
        "distorted_assets",
        "background_mismatches",
    ):
        value = visual_review.get(field)
        if not isinstance(value, list):
            raise ValueError(f"Case '{case_id}' visual_review.{field} must be an array")
        if value:
            append_violation(
                violations,
                case_id,
                field,
                actual=value,
                expected=[],
            )

    result = {
        "id": case_id,
        "class": case_class,
        "state_id": state_id,
        "viewport": {
            "width": width,
            "height": height,
            "base_dpr": base_dpr,
            "dpr": actual_dpr,
            "zoom_percent": zoom,
            "text_zoom_percent": text_zoom,
            "device_class": viewport["device_class"],
            "is_mobile": viewport["is_mobile"],
            "has_touch": viewport["has_touch"],
        },
        "screenshot": str(screenshot_path),
        "repeat_screenshot": str(repeat_path),
        "screenshot_format": screenshot_format,
        "repeat_screenshot_format": repeat_format,
        "capture_dimensions": list(screenshot.size),
        "expected_capture_dimensions": list(expected_size),
        "pixel_sha256": screenshot_hash,
        "repeat_pixel_sha256": repeat_hash,
        "screenshot_file_sha256": screenshot_file_hash,
        "repeat_screenshot_file_sha256": repeat_file_hash,
        "stable": stable,
        "calculated_horizontal_overflow_px": round(calculated_overflow, 3),
        "visible_resources": visible_resources,
        "device_emulation": device_emulation,
        "emulation_errors": list(probe["emulation_errors"]),
        "browser_evidence": {
            "visual_viewport": probe["visual_viewport"],
            "device_environment": probe["device_environment"],
        },
        "passed": not violations,
    }
    return result, violations


def main() -> int:
    args = parse_args()
    manifest = load_object(args.manifest, "Responsive manifest")
    if manifest.get("profile") != PROFILE_NAME:
        raise ValueError(f"Responsive manifest profile must be '{PROFILE_NAME}'")
    route = manifest.get("route")
    if not isinstance(route, str) or not route.strip():
        raise ValueError("Responsive manifest requires a non-empty route")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("Responsive manifest must contain a 'cases' array")
    breakpoints = manifest.get("breakpoints")
    if not isinstance(breakpoints, list) or any(
        not isinstance(value, int) or value <= 0 for value in breakpoints
    ):
        raise ValueError("Responsive manifest breakpoints must be positive integers")
    height_breakpoints = manifest.get("height_breakpoints")
    if not isinstance(height_breakpoints, list) or any(
        not isinstance(value, int) or value <= 0 for value in height_breakpoints
    ):
        raise ValueError("Responsive manifest height_breakpoints must be positive integers")
    breakpoint_capture = manifest.get("breakpoint_capture")
    if not isinstance(breakpoint_capture, dict):
        raise ValueError("Responsive manifest requires breakpoint_capture")
    if not isinstance(breakpoint_capture.get("enabled"), bool):
        raise ValueError("Responsive manifest breakpoint_capture.enabled must be boolean")
    case_budget = manifest.get("case_budget")
    if not isinstance(case_budget, dict):
        raise ValueError("Responsive manifest requires case_budget")

    reference, reference_format = load_capture(args.reference)
    if reference_format not in LOSSLESS_FORMATS:
        raise ValueError("Responsive reference must be a lossless PNG, BMP, or TIFF")
    reference_hash = pixel_sha256(reference)
    current_code_hash = tree_sha256(args.code_root)
    run, run_violations, blockers = validate_run_evidence(
        manifest,
        args.manifest,
        reference_hash,
        current_code_hash,
    )
    states, states_by_id = validate_states(manifest, run)
    required_definitions = validate_required_elements(manifest, set(states_by_id))
    visual_reviews = load_visual_reviews(args.visual_review, run["run_id"])
    assets, asset_violations, asset_blockers = validate_assets(
        args.asset_ledger, reference, reference_hash
    )
    blockers.extend(asset_blockers)
    violations = [*run_violations, *asset_violations]
    current_ledger_hash = file_sha256(args.asset_ledger)
    if run["asset_ledger_sha256"] != current_ledger_hash:
        blocker = {
            "scope": "run",
            "gate": "stale_capture_evidence",
            "field": "asset_ledger_sha256",
            "captured": run["asset_ledger_sha256"],
            "current": current_ledger_hash,
        }
        violations.append(blocker)
        blockers.append(blocker)
    collection_errors = manifest.get("collection_errors")
    if manifest.get("collection_passed") is not True or collection_errors != []:
        blocker = {
            "scope": "collector",
            "gate": "capture_collection_failed",
            "collection_passed": manifest.get("collection_passed"),
            "collection_errors": collection_errors,
        }
        violations.append(blocker)
        blockers.append(blocker)
    assets_by_name = {asset["name"]: asset for asset in assets}
    case_results: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("Every responsive case must be an object")
        case_id = raw_case.get("id")
        if isinstance(case_id, str) and case_id in ids:
            raise ValueError(f"Duplicate responsive case id: {case_id}")
        if isinstance(case_id, str):
            ids.add(case_id)
        result, case_violations = validate_case(
            raw_case,
            args.manifest,
            run,
            states_by_id,
            required_definitions,
            assets_by_name,
            reference_hash,
            visual_reviews.get(case_id) if isinstance(case_id, str) else None,
        )
        case_results.append(result)
        violations.extend(case_violations)

    unknown_review_cases = sorted(set(visual_reviews) - ids)
    if unknown_review_cases:
        append_violation(
            violations,
            "visual-review",
            "unknown_visual_review_cases",
            actual=unknown_review_cases,
            expected=[],
        )

    primary_state = next(state for state in states if state["primary"])
    primary_cases = [
        case for case in raw_cases if case.get("state_id") == primary_state["id"]
    ]
    missing_profile_cases = [
        required
        for required in COMMON_VIEWPORTS
        if not any(matches_required(case, required) for case in primary_cases)
    ]
    for required in missing_profile_cases:
        append_violation(
            violations,
            "coverage",
            "missing_common_viewport",
            required=required,
        )

    secondary_state_missing: list[dict[str, Any]] = []
    for state in states:
        if state["primary"] or not state["material"]:
            continue
        state_cases = [
            case for case in raw_cases if case.get("state_id") == state["id"]
        ]
        for required in SECONDARY_STATE_ANCHORS:
            if any(matches_required(case, required) for case in state_cases):
                continue
            missing = {"state_id": state["id"], **required}
            secondary_state_missing.append(missing)
            append_violation(
                violations,
                "coverage",
                "missing_material_state_viewport",
                **missing,
            )

    (
        discovered_widths,
        discovered_heights,
        boundary_extraction_errors,
    ) = discovered_boundaries(manifest)
    if boundary_extraction_errors and breakpoint_capture["enabled"]:
        blocker = {
            "scope": "coverage",
            "gate": "boundary_extraction_errors",
            "actual": boundary_extraction_errors,
            "expected": [],
        }
        violations.append(blocker)
        blockers.append(blocker)

    for field in (
        "discovered_widths",
        "discovered_heights",
        "selected_widths",
        "selected_heights",
    ):
        values = breakpoint_capture.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, int) or value <= 0 for value in values
        ):
            raise ValueError(
                f"Responsive manifest breakpoint_capture.{field} must be positive integers"
            )
    if breakpoint_capture.get("maximum_boundaries") != MAX_COMPACT_BREAKPOINTS:
        append_violation(
            violations,
            "coverage",
            "breakpoint_budget_mismatch",
            actual=breakpoint_capture.get("maximum_boundaries"),
            expected=MAX_COMPACT_BREAKPOINTS,
        )
    if set(breakpoint_capture["discovered_widths"]) != discovered_widths or set(
        breakpoint_capture["discovered_heights"]
    ) != discovered_heights:
        append_violation(
            violations,
            "coverage",
            "breakpoint_discovery_mismatch",
            declared_widths=sorted(set(breakpoint_capture["discovered_widths"])),
            discovered_widths=sorted(discovered_widths),
            declared_heights=sorted(set(breakpoint_capture["discovered_heights"])),
            discovered_heights=sorted(discovered_heights),
        )
    selected_widths = set(breakpoints)
    selected_heights = set(height_breakpoints)
    if set(breakpoint_capture["selected_widths"]) != selected_widths or set(
        breakpoint_capture["selected_heights"]
    ) != selected_heights:
        append_violation(
            violations,
            "coverage",
            "breakpoint_selection_mismatch",
        )
    expected_selected_widths = (
        discovered_widths if breakpoint_capture["enabled"] else set()
    )
    expected_selected_heights = (
        discovered_heights if breakpoint_capture["enabled"] else set()
    )
    if (
        selected_widths != expected_selected_widths
        or selected_heights != expected_selected_heights
    ):
        append_violation(
            violations,
            "coverage",
            "breakpoint_capture_scope",
            enabled=breakpoint_capture["enabled"],
            selected_widths=sorted(selected_widths),
            expected_widths=sorted(expected_selected_widths),
            selected_heights=sorted(selected_heights),
            expected_heights=sorted(expected_selected_heights),
        )
    if breakpoint_capture["enabled"] and (
        len(discovered_widths) + len(discovered_heights) > MAX_COMPACT_BREAKPOINTS
    ):
        blocker = {
            "scope": "coverage",
            "gate": "breakpoint_budget_exceeded",
            "actual": len(discovered_widths) + len(discovered_heights),
            "maximum": MAX_COMPACT_BREAKPOINTS,
        }
        violations.append(blocker)
        blockers.append(blocker)

    if (
        case_budget.get("profile_limit") != MAX_COMPACT_CAPTURE_CASES
        or case_budget.get("actual_cases") != len(raw_cases)
    ):
        append_violation(
            violations,
            "coverage",
            "case_budget_mismatch",
            declared=case_budget,
            expected_limit=MAX_COMPACT_CAPTURE_CASES,
            actual_cases=len(raw_cases),
        )
    if len(raw_cases) > MAX_COMPACT_CAPTURE_CASES:
        blocker = {
            "scope": "coverage",
            "gate": "compact_case_budget_exceeded",
            "actual": len(raw_cases),
            "maximum": MAX_COMPACT_CAPTURE_CASES,
        }
        violations.append(blocker)
        blockers.append(blocker)
    unzoomed_primary = [
        case
        for case in primary_cases
        if case.get("viewport", {}).get("zoom_percent", 100) == 100
        and case.get("viewport", {}).get("text_zoom_percent", 100) == 100
    ]
    unzoomed_widths = {case_key(case)[1] for case in unzoomed_primary}
    unzoomed_heights = {case_key(case)[2] for case in unzoomed_primary}
    missing_breakpoint_cases: list[dict[str, Any]] = []
    for breakpoint in sorted(selected_widths):
        for width in (breakpoint - 1, breakpoint, breakpoint + 1):
            if width not in unzoomed_widths:
                item = {
                    "dimension": "width",
                    "breakpoint": breakpoint,
                    "required_value": width,
                }
                missing_breakpoint_cases.append(item)
                append_violation(
                    violations,
                    "coverage",
                    "missing_breakpoint_boundary",
                    **item,
                )
    for breakpoint in sorted(selected_heights):
        for height in (breakpoint - 1, breakpoint, breakpoint + 1):
            if height not in unzoomed_heights:
                item = {
                    "dimension": "height",
                    "breakpoint": breakpoint,
                    "required_value": height,
                }
                missing_breakpoint_cases.append(item)
                append_violation(
                    violations,
                    "coverage",
                    "missing_breakpoint_boundary",
                    **item,
                )

    sweep, sweep_violations = validate_sweep(manifest, primary_state["id"])
    violations.extend(sweep_violations)
    blocking_gates = {
        "automated_capture_harness",
        "capture_harness_fingerprint",
        "capture_trace_fingerprint",
        "capture_trace_manifest_correlation",
        "capture_collection_failed",
        "fatal_capture_error",
        "device_emulation_errors",
        "device_emulation_evidence",
        "boundary_extraction_errors",
        "stale_capture_evidence",
        "full_reference_reuse",
        "near_full_reference_plate",
        "cumulative_reference_crop_plate",
        "unledgered_visible_resource",
        "raster_kind_mismatch",
        "invalid_exact_origin",
        "unsupported_derivation_operation",
        "asset_fingerprint_mismatch",
        "isolated_asset_alpha",
    }
    known_blocker_keys = {
        (blocker.get("scope"), blocker.get("gate"), json.dumps(blocker, sort_keys=True))
        for blocker in blockers
    }
    for violation in violations:
        key = (
            violation.get("scope"),
            violation.get("gate"),
            json.dumps(violation, sort_keys=True),
        )
        if violation.get("gate") in blocking_gates and key not in known_blocker_keys:
            blockers.append(violation)
            known_blocker_keys.add(key)

    output = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE_NAME,
        "validator": {
            "name": VALIDATOR_NAME,
            "version": VALIDATOR_VERSION,
            "script_sha256": file_sha256(Path(__file__)),
        },
        "replay": {
            "schema_version": "1.0",
            "manifest": str(args.manifest.resolve()),
            "asset_ledger": str(args.asset_ledger.resolve()),
            "reference": str(args.reference.resolve()),
            "code_root": str(args.code_root.resolve()),
            "visual_review": str(args.visual_review.resolve()),
        },
        "route": route,
        "run": run,
        "collector": manifest.get("collector"),
        "manifest": str(args.manifest),
        "manifest_file_sha256": file_sha256(args.manifest),
        "asset_ledger": str(args.asset_ledger),
        "visual_review": str(args.visual_review),
        "visual_review_file_sha256": file_sha256(args.visual_review),
        "reference": str(args.reference),
        "reference_pixel_sha256": reference_hash,
        "current_code_tree_hash": current_code_hash,
        "required_common_viewports": list(COMMON_VIEWPORTS),
        "required_secondary_state_anchors": list(SECONDARY_STATE_ANCHORS),
        "declared_breakpoints": sorted(set(breakpoints)),
        "declared_height_breakpoints": sorted(set(height_breakpoints)),
        "breakpoint_capture": breakpoint_capture,
        "case_budget": case_budget,
        "discovered_width_breakpoints": sorted(discovered_widths),
        "discovered_height_breakpoints": sorted(discovered_heights),
        "boundary_extraction_errors": boundary_extraction_errors,
        "states": states,
        "primary_state": primary_state["id"],
        "case_count": len(case_results),
        "cases": case_results,
        "assets": assets,
        "sweep": sweep,
        "missing_common_viewports": missing_profile_cases,
        "missing_breakpoint_cases": missing_breakpoint_cases,
        "missing_material_state_viewports": secondary_state_missing,
        "violations": violations,
        "blockers": blockers,
        "classification": (
            "responsive-certified"
            if not violations
            else "blocked"
            if blockers
            else "failed"
        ),
        "passed": not violations,
        "completion_eligible": False,
        "completion_note": (
            "Responsive certification is one input. Only certify_run.py can emit an "
            "overall completion-eligible result."
        ),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "responsive-metrics.json").write_text(
        json.dumps(output, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, indent=2))
    return 0 if not violations else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
