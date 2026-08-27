#!/usr/bin/env python3
"""Issue the sole overall completion decision for a pixel-precise UI run."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
CERTIFIER_NAME = "pixel-precise-ui-run-certifier"
CERTIFIER_VERSION = "2.0"
VISUAL_VALIDATOR_NAME = "pixel-precise-ui-visual-diff"
VISUAL_VALIDATOR_VERSION = "2.0"
RESPONSIVE_VALIDATOR_NAME = "pixel-precise-ui-responsive-audit"
RESPONSIVE_VALIDATOR_VERSION = "2.0"
RESPONSIVE_SCHEMA_VERSION = "2.0"
RESPONSIVE_PROFILE = "common-2026-07-v1"
COLLECTOR_NAME = "pixel-precise-ui-capture"
COLLECTOR_VERSION = "2.0"
MAX_EVIDENCE_AGE_HOURS = 24
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
TREE_EXCLUDED_DIRECTORIES = {
    ".git",
    ".next",
    ".cache",
    ".venv",
    "__pycache__",
    "build",
    "captures",
    "completion-check",
    "coverage",
    "dist",
    "node_modules",
    "output",
    "responsive-check",
    "venv",
    "visual-check",
}
SHARED_RUN_FIELDS = (
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Join a passing strict visual result to passing browser-collected "
            "responsive evidence and emit the only completion-eligible result."
        )
    )
    parser.add_argument("visual_metrics", type=Path, help="visual_diff.py metrics.json")
    parser.add_argument(
        "responsive_metrics", type=Path, help="responsive_audit.py responsive-metrics.json"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("completion-check"),
        help="Directory for completion-certificate.json",
    )
    return parser.parse_args()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    """Recompute the collector's recursive-tree-v2 fingerprint around replay."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Replay code root not found: {root}")
    digest = hashlib.sha256()
    digest.update(b"pixel-precise-ui:recursive-tree-v2\0")
    entries: list[tuple[bytes, Path]] = []
    for directory, child_directories, names in os.walk(root, followlinks=False):
        relative_directory = Path(directory).relative_to(root)
        kept_directories: list[str] = []
        for name in child_directories:
            relative = relative_directory / name
            if any(component in TREE_EXCLUDED_DIRECTORIES for component in relative.parts):
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
            if any(component in TREE_EXCLUDED_DIRECTORIES for component in relative.parts):
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
            raise ValueError(f"Unsupported replay code-tree entry: {path}")
        digest.update(mode)
        digest.update(b"\0")
        digest.update(str(len(relative)).encode("ascii"))
        digest.update(b":")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def require_existing_path(value: Any, label: str, *, directory: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute so replay is cwd-independent")
    path = path.resolve()
    if directory:
        if not path.is_dir():
            raise FileNotFoundError(f"{label} directory not found: {path}")
    elif not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {path}")
    return path


def optional_existing_path(value: Any, label: str) -> Path | None:
    if value is None:
        return None
    return require_existing_path(value, label)


def snapshot_files(paths: list[Path]) -> dict[str, str]:
    return {str(path): file_sha256(path) for path in sorted(set(paths))}


def resolve_linked_path(raw: Any, owner: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = owner.parent / path
    return require_existing_path(str(path.resolve()), label)


def linked_evidence_paths(
    manifest_path: Path, ledger_path: Path, regions_path: Path
) -> list[Path]:
    """Enumerate files transitively read by the two replayed validators."""
    paths: list[Path] = []
    manifest = load_object(manifest_path, "responsive replay manifest")
    collector = require_object(manifest, "collector", "responsive replay manifest")
    for field in ("trace_path", "review_index_path"):
        paths.append(
            resolve_linked_path(
                collector.get(field), manifest_path, f"responsive collector.{field}"
            )
        )
    cases = require_array(manifest, "cases", "responsive replay manifest")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"responsive replay manifest case {index} must be an object")
        for field in ("screenshot", "repeat_screenshot"):
            paths.append(
                resolve_linked_path(
                    case.get(field),
                    manifest_path,
                    f"responsive case {index}.{field}",
                )
            )
    ledger = load_object(ledger_path, "replay asset ledger")
    assets = require_array(ledger, "assets", "replay asset ledger")
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ValueError(f"replay asset ledger entry {index} must be an object")
        raw_path = asset.get("path")
        if raw_path is not None:
            paths.append(
                resolve_linked_path(
                    raw_path, ledger_path, f"replay asset ledger entry {index}.path"
                )
            )
    regions = load_object(regions_path, "replay region manifest")
    region_items = require_array(regions, "regions", "replay region manifest")
    for index, region in enumerate(region_items):
        if not isinstance(region, dict):
            raise ValueError(f"replay region {index} must be an object")
        raw_mask = region.get("mask")
        if raw_mask is not None:
            paths.append(
                resolve_linked_path(
                    raw_mask, regions_path, f"replay region {index}.mask"
                )
            )
    return paths


def run_validator(command: list[str], label: str) -> int:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode not in {0, 1}:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise ValueError(
            f"{label} replay failed with exit code {completed.returncode}: {detail}"
        )
    return completed.returncode


def validator_identity(
    payload: dict[str, Any], label: str, name: str, version: str, script: Path
) -> None:
    identity = require_object(payload, "validator", label)
    expected_hash = file_sha256(script)
    if (
        identity.get("name") != name
        or identity.get("version") != version
        or identity.get("script_sha256") != expected_hash
    ):
        raise ValueError(
            f"{label} was not produced by the current {name} {version} script"
        )


def visual_replay_command(
    replay: dict[str, Any], output_dir: Path
) -> tuple[list[str], list[Path]]:
    if replay.get("schema_version") != "1.0":
        raise ValueError("visual metrics replay.schema_version must be '1.0'")
    source = require_existing_path(replay.get("source"), "visual replay.source")
    rendered = require_existing_path(replay.get("rendered"), "visual replay.rendered")
    regions = require_existing_path(replay.get("regions"), "visual replay.regions")
    stability = require_existing_path(
        replay.get("stability_capture"), "visual replay.stability_capture"
    )
    ledger = require_existing_path(
        replay.get("asset_ledger"), "visual replay.asset_ledger"
    )
    run_metadata = require_existing_path(
        replay.get("run_metadata"), "visual replay.run_metadata"
    )
    baseline = optional_existing_path(replay.get("baseline"), "visual replay.baseline")
    if replay.get("strict_parity") is not True:
        raise ValueError("Joint certification requires visual replay.strict_parity=true")
    command = [
        sys.executable,
        str(Path(__file__).with_name("visual_diff.py")),
        str(source),
        str(rendered),
        "--regions",
        str(regions),
        "--stability-capture",
        str(stability),
        "--asset-ledger",
        str(ledger),
        "--run-metadata",
        str(run_metadata),
        "--strict-parity",
        "--output-dir",
        str(output_dir),
        "--threshold",
        str(replay.get("threshold", 8)),
        "--regression-tolerance",
        str(replay.get("regression_tolerance", 0.001)),
    ]
    if baseline is not None:
        command.extend(["--baseline", str(baseline)])
    for field, flag in (
        ("fail_over_pct", "--fail-over-pct"),
        ("max_normalized_mad", "--max-normalized-mad"),
    ):
        value = replay.get(field)
        if value is not None:
            command.extend([flag, str(value)])
    for field, flag in (
        ("fail_on_regression", "--fail-on-regression"),
        ("require_dimensions", "--require-dimensions"),
        ("require_region_gates", "--require-region-gates"),
    ):
        value = replay.get(field, False)
        if not isinstance(value, bool):
            raise ValueError(f"visual replay.{field} must be boolean")
        if value:
            command.append(flag)
    paths = [source, rendered, regions, stability, ledger, run_metadata]
    if baseline is not None:
        paths.append(baseline)
    return command, paths


def responsive_replay_command(
    replay: dict[str, Any], output_dir: Path
) -> tuple[list[str], list[Path], Path]:
    if replay.get("schema_version") != "1.0":
        raise ValueError("responsive metrics replay.schema_version must be '1.0'")
    manifest = require_existing_path(
        replay.get("manifest"), "responsive replay.manifest"
    )
    ledger = require_existing_path(
        replay.get("asset_ledger"), "responsive replay.asset_ledger"
    )
    reference = require_existing_path(
        replay.get("reference"), "responsive replay.reference"
    )
    code_root = require_existing_path(
        replay.get("code_root"), "responsive replay.code_root", directory=True
    )
    review = require_existing_path(
        replay.get("visual_review"), "responsive replay.visual_review"
    )
    command = [
        sys.executable,
        str(Path(__file__).with_name("responsive_audit.py")),
        str(manifest),
        "--asset-ledger",
        str(ledger),
        "--reference",
        str(reference),
        "--code-root",
        str(code_root),
        "--visual-review",
        str(review),
        "--output-dir",
        str(output_dir),
    ]
    return command, [manifest, ledger, reference, review], code_root


def replay_validators(
    supplied_visual: dict[str, Any], supplied_responsive: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Replay current validators from raw evidence; never trust passed JSON fields."""
    visual_script = Path(__file__).with_name("visual_diff.py")
    responsive_script = Path(__file__).with_name("responsive_audit.py")
    validator_identity(
        supplied_visual,
        "visual metrics",
        VISUAL_VALIDATOR_NAME,
        VISUAL_VALIDATOR_VERSION,
        visual_script,
    )
    validator_identity(
        supplied_responsive,
        "responsive metrics",
        RESPONSIVE_VALIDATOR_NAME,
        RESPONSIVE_VALIDATOR_VERSION,
        responsive_script,
    )
    visual_replay = require_object(supplied_visual, "replay", "visual metrics")
    responsive_replay = require_object(
        supplied_responsive, "replay", "responsive metrics"
    )
    with tempfile.TemporaryDirectory(prefix="pixel-precise-ui-certify-") as directory:
        root = Path(directory)
        visual_output = root / "visual"
        responsive_output = root / "responsive"
        visual_command, visual_paths = visual_replay_command(
            visual_replay, visual_output
        )
        responsive_command, responsive_paths, code_root = responsive_replay_command(
            responsive_replay, responsive_output
        )
        manifest_path = require_existing_path(
            responsive_replay.get("manifest"), "responsive replay.manifest"
        )
        ledger_path = require_existing_path(
            responsive_replay.get("asset_ledger"), "responsive replay.asset_ledger"
        )
        regions_path = require_existing_path(
            visual_replay.get("regions"), "visual replay.regions"
        )
        linked_paths = linked_evidence_paths(
            manifest_path, ledger_path, regions_path
        )
        raw_paths = list(
            dict.fromkeys([*visual_paths, *responsive_paths, *linked_paths])
        )
        raw_before = snapshot_files(raw_paths)
        code_before = tree_sha256(code_root)
        visual_returncode = run_validator(visual_command, "Strict visual validator")
        responsive_returncode = run_validator(
            responsive_command, "Responsive validator"
        )
        code_after = tree_sha256(code_root)
        raw_after = snapshot_files(raw_paths)
        if code_before != code_after:
            raise ValueError("Code tree changed while validators were replayed")
        if raw_before != raw_after:
            raise ValueError("Raw certification evidence changed while validators replayed")
        fresh_visual_path = visual_output / "metrics.json"
        fresh_responsive_path = responsive_output / "responsive-metrics.json"
        fresh_visual = load_object(fresh_visual_path, "replayed visual metrics")
        fresh_responsive = load_object(
            fresh_responsive_path, "replayed responsive metrics"
        )
        validator_identity(
            fresh_visual,
            "replayed visual metrics",
            VISUAL_VALIDATOR_NAME,
            VISUAL_VALIDATOR_VERSION,
            visual_script,
        )
        validator_identity(
            fresh_responsive,
            "replayed responsive metrics",
            RESPONSIVE_VALIDATOR_NAME,
            RESPONSIVE_VALIDATOR_VERSION,
            responsive_script,
        )
        replayed_run = require_object(
            fresh_responsive, "run", "replayed responsive metrics"
        )
        if code_before != replayed_run.get("code_tree_hash"):
            raise ValueError(
                "Certifier and responsive validator disagree on the current code-tree "
                "fingerprint"
            )
        report = {
            "mode": "independent-validator-replay-v1",
            "visual_validator_sha256": file_sha256(visual_script),
            "responsive_validator_sha256": file_sha256(responsive_script),
            "visual_validator_returncode": visual_returncode,
            "responsive_validator_returncode": responsive_returncode,
            "code_tree_sha256_before": code_before,
            "code_tree_sha256_after": code_after,
            "raw_input_sha256_before": raw_before,
            "raw_input_sha256_after": raw_after,
            "replayed_visual_metrics_sha256": file_sha256(fresh_visual_path),
            "replayed_responsive_metrics_sha256": file_sha256(fresh_responsive_path),
        }
        return fresh_visual, fresh_responsive, report


def script_sha256() -> str:
    return file_sha256(Path(__file__))


def append_gate(
    gates: list[dict[str, Any]],
    violations: list[dict[str, Any]],
    name: str,
    passed: bool,
    **details: Any,
) -> None:
    gate = {"name": name, "passed": bool(passed), **details}
    gates.append(gate)
    if not passed:
        violations.append({"gate": name, **details})


def require_object(payload: dict[str, Any], field: str, label: str) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"{label}.{field} must be an object")
    return value


def require_array(payload: dict[str, Any], field: str, label: str) -> list[Any]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{label}.{field} must be an array")
    return value


def require_run(payload: dict[str, Any], label: str) -> dict[str, Any]:
    run = require_object(payload, "run", label)
    missing = [
        field
        for field in SHARED_RUN_FIELDS
        if not isinstance(run.get(field), str) or not run[field].strip()
    ]
    if missing:
        raise ValueError(f"{label}.run is missing required fields: {', '.join(missing)}")
    for field in (
        "code_tree_hash",
        "code_tree_hash_after",
        "input_fingerprint",
        "asset_ledger_sha256",
        "reference_pixel_sha256",
    ):
        if not SHA256_PATTERN.fullmatch(run[field]):
            raise ValueError(f"{label}.run.{field} must be a lowercase SHA-256 digest")
    if run["code_tree_hash_algorithm"] != "recursive-tree-v2":
        raise ValueError(
            f"{label}.run.code_tree_hash_algorithm must be 'recursive-tree-v2'"
        )
    return run


def parse_timestamp(value: str, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def exact_visual_gates(
    visual: dict[str, Any], gates: list[dict[str, Any]], violations: list[dict[str, Any]]
) -> None:
    visual_violations = require_array(visual, "violations", "visual metrics")
    visual_blockers = require_array(visual, "blockers", "visual metrics")
    append_gate(
        gates,
        violations,
        "visual_strict_mode",
        visual.get("strict_parity") is True,
        actual=visual.get("strict_parity"),
    )
    append_gate(
        gates,
        violations,
        "visual_passed",
        visual.get("passed") is True
        and visual.get("classification") == "achieved"
        and not visual_violations
        and not visual_blockers,
        passed_flag=visual.get("passed"),
        classification=visual.get("classification"),
        violation_count=len(visual_violations),
        blocker_count=len(visual_blockers),
    )
    append_gate(
        gates,
        violations,
        "visual_dimensions",
        visual.get("dimensions_match") is True
        and visual.get("stability_dimensions_match") is True
        and visual.get("resized_for_comparison") is False,
        dimensions_match=visual.get("dimensions_match"),
        stability_dimensions_match=visual.get("stability_dimensions_match"),
        resized_for_comparison=visual.get("resized_for_comparison"),
    )
    pixels = require_object(visual, "pixel_sha256", "visual metrics")
    source_hash = pixels.get("source")
    rendered_hash = pixels.get("rendered")
    stability_hash = pixels.get("stability_capture")
    hashes_are_exact = (
        isinstance(source_hash, str)
        and SHA256_PATTERN.fullmatch(source_hash) is not None
        and source_hash == rendered_hash == stability_hash
    )
    append_gate(
        gates,
        violations,
        "visual_pixel_hash_identity",
        hashes_are_exact,
        source=source_hash,
        rendered=rendered_hash,
        stability_capture=stability_hash,
    )
    exact = require_object(visual, "exact_pixel_metrics", "visual metrics")
    stability_exact = require_object(
        visual, "stability_exact_pixel_metrics", "visual metrics"
    )
    append_gate(
        gates,
        violations,
        "visual_zero_changed_pixels",
        exact.get("changed_pixels") == 0
        and exact.get("max_channel_difference") == 0
        and stability_exact.get("changed_pixels") == 0
        and stability_exact.get("max_channel_difference") == 0
        and visual.get("stability_pixel_identical") is True
        and visual.get("stability_pixel_hashes_match") is True,
        source_render_changed_pixels=exact.get("changed_pixels"),
        source_render_max_channel_difference=exact.get("max_channel_difference"),
        repeat_changed_pixels=stability_exact.get("changed_pixels"),
        repeat_max_channel_difference=stability_exact.get("max_channel_difference"),
        stability_pixel_identical=visual.get("stability_pixel_identical"),
        stability_pixel_hashes_match=visual.get("stability_pixel_hashes_match"),
    )
    append_gate(
        gates,
        violations,
        "visual_no_upstream_completion_claim",
        visual.get("completion_eligible") is not True,
        upstream_value=visual.get("completion_eligible"),
    )


def responsive_gates(
    responsive: dict[str, Any],
    gates: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> None:
    responsive_violations = require_array(
        responsive, "violations", "responsive metrics"
    )
    responsive_blockers = require_array(responsive, "blockers", "responsive metrics")
    append_gate(
        gates,
        violations,
        "responsive_schema",
        responsive.get("schema_version") == RESPONSIVE_SCHEMA_VERSION
        and responsive.get("profile") == RESPONSIVE_PROFILE,
        schema_version=responsive.get("schema_version"),
        profile=responsive.get("profile"),
    )
    append_gate(
        gates,
        violations,
        "responsive_passed",
        responsive.get("passed") is True
        and responsive.get("classification") == "responsive-certified"
        and not responsive_violations
        and not responsive_blockers,
        passed_flag=responsive.get("passed"),
        classification=responsive.get("classification"),
        violation_count=len(responsive_violations),
        blocker_count=len(responsive_blockers),
    )
    collector = require_object(responsive, "collector", "responsive metrics")
    trace_hash = collector.get("trace_sha256")
    script_hash = collector.get("script_sha256")
    append_gate(
        gates,
        violations,
        "responsive_browser_harness",
        collector.get("name") == COLLECTOR_NAME
        and collector.get("version") == COLLECTOR_VERSION
        and collector.get("harness_collected") is True
        and isinstance(trace_hash, str)
        and SHA256_PATTERN.fullmatch(trace_hash) is not None
        and isinstance(script_hash, str)
        and SHA256_PATTERN.fullmatch(script_hash) is not None,
        collector_name=collector.get("name"),
        collector_version=collector.get("version"),
        harness_collected=collector.get("harness_collected"),
        trace_sha256=trace_hash,
        script_sha256=script_hash,
    )
    capture_script = Path(__file__).with_name("capture_responsive.mjs")
    computed_script_hash = (
        file_sha256(capture_script) if capture_script.is_file() else None
    )
    append_gate(
        gates,
        violations,
        "responsive_capture_harness_fingerprint",
        script_hash == computed_script_hash,
        declared=script_hash,
        computed=computed_script_hash,
    )
    manifest_hash = responsive.get("manifest_file_sha256")
    review_hash = responsive.get("visual_review_file_sha256")
    append_gate(
        gates,
        violations,
        "responsive_evidence_fingerprints",
        isinstance(manifest_hash, str)
        and SHA256_PATTERN.fullmatch(manifest_hash) is not None
        and isinstance(review_hash, str)
        and SHA256_PATTERN.fullmatch(review_hash) is not None,
        manifest_file_sha256=manifest_hash,
        visual_review_file_sha256=review_hash,
    )
    append_gate(
        gates,
        violations,
        "responsive_no_upstream_completion_claim",
        responsive.get("completion_eligible") is False,
        upstream_value=responsive.get("completion_eligible"),
    )


def join_gates(
    visual: dict[str, Any],
    responsive: dict[str, Any],
    visual_run: dict[str, Any],
    responsive_run: dict[str, Any],
    gates: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    mismatches = {
        field: {"visual": visual_run.get(field), "responsive": responsive_run.get(field)}
        for field in SHARED_RUN_FIELDS
        if visual_run.get(field) != responsive_run.get(field)
    }
    append_gate(
        gates,
        violations,
        "shared_run_identity",
        not mismatches,
        mismatches=mismatches,
    )
    evidence_time = parse_timestamp(
        responsive_run["generated_at"], "responsive metrics.run.generated_at"
    )
    age_hours = (
        dt.datetime.now(dt.timezone.utc) - evidence_time
    ).total_seconds() / 3600
    append_gate(
        gates,
        violations,
        "fresh_joint_evidence",
        -0.1 <= age_hours <= MAX_EVIDENCE_AGE_HOURS,
        age_hours=round(age_hours, 3),
        maximum_hours=MAX_EVIDENCE_AGE_HOURS,
    )
    append_gate(
        gates,
        violations,
        "responsive_current_inputs",
        responsive.get("current_code_tree_hash") == responsive_run["code_tree_hash"]
        and responsive.get("reference_pixel_sha256")
        == responsive_run["reference_pixel_sha256"]
        and responsive_run["code_tree_hash_after"] == responsive_run["code_tree_hash"],
        responsive_current_code_tree_hash=responsive.get("current_code_tree_hash"),
        responsive_run_code_tree_hash=responsive_run["code_tree_hash"],
        responsive_run_code_tree_hash_after=responsive_run["code_tree_hash_after"],
        responsive_reference_pixel_sha256=responsive.get("reference_pixel_sha256"),
        responsive_run_reference_pixel_sha256=responsive_run["reference_pixel_sha256"],
    )
    visual_state = visual_run.get("state")
    case_id = visual_run.get("capture_case_id")
    cases = require_array(responsive, "cases", "responsive metrics")
    matching_case = next(
        (
            case
            for case in cases
            if isinstance(case, dict) and isinstance(case_id, str) and case.get("id") == case_id
        ),
        None,
    )
    case_identity_passed = (
        isinstance(visual_state, str)
        and bool(visual_state)
        and isinstance(case_id, str)
        and bool(case_id)
        and isinstance(matching_case, dict)
        and matching_case.get("state_id") == visual_state
        and matching_case.get("passed") is True
    )
    append_gate(
        gates,
        violations,
        "visual_capture_case_identity",
        case_identity_passed,
        visual_state=visual_state,
        capture_case_id=case_id,
        responsive_case_state=(
            matching_case.get("state_id") if isinstance(matching_case, dict) else None
        ),
        responsive_case_passed=(
            matching_case.get("passed") if isinstance(matching_case, dict) else None
        ),
    )
    case_viewport = (
        matching_case.get("viewport") if isinstance(matching_case, dict) else None
    )
    case_emulation = (
        matching_case.get("device_emulation")
        if isinstance(matching_case, dict)
        else None
    )
    browser_evidence = (
        matching_case.get("browser_evidence")
        if isinstance(matching_case, dict)
        else None
    )
    case_emulation_passed = (
        isinstance(case_viewport, dict)
        and case_viewport.get("device_class") in {"mobile", "tablet", "desktop"}
        and isinstance(case_viewport.get("is_mobile"), bool)
        and isinstance(case_viewport.get("has_touch"), bool)
        and isinstance(case_emulation, dict)
        and case_emulation.get("browser_name") == responsive_run["browser_name"]
        and case_emulation.get("audit_validated") is True
        and case_emulation.get("errors") == []
        and matching_case.get("emulation_errors") == []
        and isinstance(browser_evidence, dict)
        and isinstance(browser_evidence.get("visual_viewport"), dict)
        and isinstance(browser_evidence.get("device_environment"), dict)
    )
    append_gate(
        gates,
        violations,
        "responsive_case_device_emulation",
        case_emulation_passed,
        capture_case_id=case_id,
        viewport=case_viewport,
        device_emulation=case_emulation,
        emulation_errors=(
            matching_case.get("emulation_errors")
            if isinstance(matching_case, dict)
            else None
        ),
    )
    pixel_hashes = require_object(visual, "pixel_sha256", "visual metrics")
    rendered_hash = pixel_hashes.get("rendered")
    repeat_hash = pixel_hashes.get("stability_capture")
    responsive_rendered_hash = (
        matching_case.get("pixel_sha256") if isinstance(matching_case, dict) else None
    )
    responsive_repeat_hash = (
        matching_case.get("repeat_pixel_sha256")
        if isinstance(matching_case, dict)
        else None
    )
    run_rendered_hash = visual_run.get("capture_screenshot_pixel_sha256")
    run_repeat_hash = visual_run.get("capture_repeat_pixel_sha256")
    append_gate(
        gates,
        violations,
        "visual_capture_pixel_identity",
        isinstance(matching_case, dict)
        and rendered_hash == responsive_rendered_hash
        and repeat_hash == responsive_repeat_hash
        and run_rendered_hash == rendered_hash
        and run_repeat_hash == repeat_hash,
        visual_rendered=rendered_hash,
        responsive_rendered=responsive_rendered_hash,
        visual_repeat=repeat_hash,
        responsive_repeat=responsive_repeat_hash,
        run_metadata_rendered=run_rendered_hash,
        run_metadata_repeat=run_repeat_hash,
    )
    append_gate(
        gates,
        violations,
        "reference_identity",
        pixel_hashes.get("source")
        == visual_run.get("reference_pixel_sha256")
        == responsive_run.get("reference_pixel_sha256")
        == responsive.get("reference_pixel_sha256"),
        visual_source=pixel_hashes.get("source"),
        run_reference=visual_run.get("reference_pixel_sha256"),
        responsive_reference=responsive.get("reference_pixel_sha256"),
    )
    states = require_array(responsive, "states", "responsive metrics")
    known_states = {
        state.get("id") for state in states if isinstance(state, dict)
    }
    append_gate(
        gates,
        violations,
        "visual_state_in_responsive_state_set",
        visual_state in known_states,
        visual_state=visual_state,
        responsive_states=sorted(value for value in known_states if isinstance(value, str)),
    )
    return matching_case if isinstance(matching_case, dict) else None


def certify(
    visual: dict[str, Any],
    responsive: dict[str, Any],
    visual_path: Path,
    responsive_path: Path,
    replay_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    gates: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    visual_run = require_run(visual, "visual metrics")
    responsive_run = require_run(responsive, "responsive metrics")
    if not isinstance(visual_run.get("state"), str) or not visual_run["state"].strip():
        raise ValueError("visual metrics.run.state must be a non-empty string")
    if (
        not isinstance(visual_run.get("capture_case_id"), str)
        or not visual_run["capture_case_id"].strip()
    ):
        raise ValueError("visual metrics.run.capture_case_id must be a non-empty string")

    append_gate(
        gates,
        violations,
        "independent_validator_replay",
        isinstance(replay_report, dict)
        and replay_report.get("mode") == "independent-validator-replay-v1"
        and replay_report.get("code_tree_sha256_before")
        == replay_report.get("code_tree_sha256_after")
        and replay_report.get("raw_input_sha256_before")
        == replay_report.get("raw_input_sha256_after")
        and replay_report.get("visual_validator_sha256")
        == file_sha256(Path(__file__).with_name("visual_diff.py"))
        and replay_report.get("responsive_validator_sha256")
        == file_sha256(Path(__file__).with_name("responsive_audit.py")),
        replay_mode=(replay_report or {}).get("mode")
        if isinstance(replay_report, dict)
        else None,
    )

    exact_visual_gates(visual, gates, violations)
    responsive_gates(responsive, gates, violations)
    matching_case = join_gates(
        visual,
        responsive,
        visual_run,
        responsive_run,
        gates,
        violations,
    )
    completion_eligible = not violations and all(gate["passed"] for gate in gates)
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    certificate = {
        "schema_version": SCHEMA_VERSION,
        "certifier": {
            "name": CERTIFIER_NAME,
            "version": CERTIFIER_VERSION,
            "script_sha256": script_sha256(),
        },
        "generated_at": generated_at,
        "run": {
            field: responsive_run[field]
            for field in SHARED_RUN_FIELDS
        }
        | {
            "state": visual_run["state"],
            "capture_case_id": visual_run["capture_case_id"],
        },
        "inputs": {
            "authority": "replayed-current-validators",
            "supplied_metric_fields_trusted": False,
            "visual_metrics": {
                "path": str(visual_path),
                "file_sha256": file_sha256(visual_path),
            },
            "responsive_metrics": {
                "path": str(responsive_path),
                "file_sha256": file_sha256(responsive_path),
            },
        },
        "validator_replay": replay_report,
        "visual_result": {
            "classification": visual.get("classification"),
            "passed": visual.get("passed"),
            "source_pixel_sha256": visual.get("pixel_sha256", {}).get("source"),
            "rendered_pixel_sha256": visual.get("pixel_sha256", {}).get("rendered"),
        },
        "responsive_result": {
            "classification": responsive.get("classification"),
            "passed": responsive.get("passed"),
            "manifest_file_sha256": responsive.get("manifest_file_sha256"),
            "case_id": matching_case.get("id") if matching_case else None,
        },
        "gates": gates,
        "violations": violations,
        "classification": (
            "achieved-and-responsive-certified"
            if completion_eligible
            else "not-completion-eligible"
        ),
        "passed": completion_eligible,
        "completion_eligible": completion_eligible,
        "authority_note": (
            "This joint certificate is the only machine result that can authorize an "
            "overall completion claim for the run."
        ),
    }
    return certificate, completion_eligible


def write_json_atomic(destination: Path, payload: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    args = parse_args()
    supplied_visual = load_object(args.visual_metrics, "Visual metrics")
    supplied_responsive = load_object(args.responsive_metrics, "Responsive metrics")
    visual, responsive, replay_report = replay_validators(
        supplied_visual, supplied_responsive
    )
    certificate, completion_eligible = certify(
        visual,
        responsive,
        args.visual_metrics,
        args.responsive_metrics,
        replay_report,
    )
    output_path = args.output_dir / "completion-certificate.json"
    write_json_atomic(output_path, certificate)
    print(json.dumps(certificate, indent=2))
    return 0 if completion_eligible else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
