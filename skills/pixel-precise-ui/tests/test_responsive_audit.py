from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).parents[1] / "scripts" / "responsive_audit.py"
CAPTURE_SCRIPT = SCRIPT.with_name("capture_responsive.mjs")
SPEC = importlib.util.spec_from_file_location("responsive_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RESPONSIVE_AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESPONSIVE_AUDIT)


class ResponsiveAuditTests(unittest.TestCase):
    def prepare_root(self, root: Path) -> tuple[Path, Path, Path]:
        code_root = root / "app"
        code_root.mkdir()
        (code_root / "index.css").write_text("body { margin: 0; }\n", encoding="utf-8")
        reference = root / "reference.png"
        Image.new("RGB", (64, 48), "#eaf3f8").save(reference)
        trace = root / "capture-trace.jsonl"
        trace.write_text('{"event":"capture-start"}\n', encoding="utf-8")
        (root / "review-index.html").write_text(
            "<!doctype html><title>review</title>", encoding="utf-8"
        )
        return code_root, reference, trace

    def run_audit(
        self,
        root: Path,
        manifest: Path,
        ledger: Path,
        code_root: Path,
        reference: Path,
        *,
        refresh_manifest: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if refresh_manifest:
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_payload.pop("collector_attestation", None)
            manifest_payload["run"]["asset_ledger_sha256"] = RESPONSIVE_AUDIT.file_sha256(
                ledger
            )
            payload_hash = RESPONSIVE_AUDIT.canonical_sha256(manifest_payload)
            attestation_hash = RESPONSIVE_AUDIT.hashlib.sha256(
                (
                    "pixel-precise-ui:capture-attestation-v2\0"
                    + manifest_payload["collector"]["script_sha256"]
                    + "\0"
                    + payload_hash
                ).encode("utf-8")
            ).hexdigest()
            manifest.write_text(
                json.dumps(
                    {
                        **manifest_payload,
                        "collector_attestation": {
                            "algorithm": "sha256-canonical-json-v1",
                            "payload_sha256": payload_hash,
                            "attestation_sha256": attestation_hash,
                        },
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(manifest),
                "--asset-ledger",
                str(ledger),
                "--reference",
                str(reference),
                "--code-root",
                str(code_root),
                "--visual-review",
                str(root / "visual-review.json"),
                "--output-dir",
                str(root / "output"),
            ],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )

    def write_safe_ledger(self, root: Path) -> Path:
        ledger = root / "asset-ledger.json"
        ledger.write_text(
            json.dumps(
                {
                    "assets": [
                        {
                            "name": "semantic-css-ui",
                            "kind": "other",
                            "status": "exact",
                            "material": True,
                            "evidence": "No raster resource is used by this synthetic fixture.",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return ledger

    def make_case(
        self,
        root: Path,
        run_id: str,
        case_id: str,
        case_class: str,
        width: int,
        height: int,
        zoom: int = 100,
        text_zoom: int = 100,
        base_dpr: float = 1,
        *,
        state_id: str = "default",
        overflow: float = 0,
        seams: list[str] | None = None,
        unstable: bool = False,
        rect: list[float] | None = None,
        resources: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        screenshot = root / f"{case_id}.png"
        repeat = root / f"{case_id}-repeat.png"
        image = Image.new(
            "RGB", (round(width * base_dpr), round(height * base_dpr)), "#102030"
        )
        image.save(screenshot)
        repeated = image.copy()
        if unstable:
            repeated.putpixel((0, 0), (255, 255, 255))
        repeated.save(repeat)
        inner_width = width * 100 / zoom
        inner_height = height * 100 / zoom
        actual_dpr = base_dpr * zoom / 100
        semantics = RESPONSIVE_AUDIT.expected_device_semantics(case_class, width)
        inner_width = int(width * 100 / zoom + 0.5)
        inner_height = int(height * 100 / zoom + 0.5)
        has_touch = semantics["has_touch"]
        is_mobile = semantics["is_mobile"]
        if semantics["device_class"] == "mobile":
            user_agent = (
                "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
                "Chrome/test Mobile Safari/537.36"
            )
        elif semantics["device_class"] == "tablet":
            user_agent = (
                "Mozilla/5.0 (Linux; Android 14; Pixel Tablet) AppleWebKit/537.36 "
                "Chrome/test Safari/537.36"
            )
        else:
            user_agent = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/test Safari/537.36"
            )
        environment = {
            "navigator": {
                "user_agent": user_agent,
                "user_agent_data": {"mobile": is_mobile},
                "user_agent_data_error": None,
                "platform": "Linux",
                "vendor": "Google Inc.",
                "language": "en-US",
                "languages": ["en-US"],
                "hardware_concurrency": 8,
                "device_memory_gib": 8,
                "max_touch_points": 1 if has_touch else 0,
            },
            "touch": {
                "touch_event_supported": has_touch,
                "touch_constructor_supported": has_touch,
                "pointer_event_supported": True,
                "pointer_coarse": has_touch,
                "any_pointer_coarse": has_touch,
                "hover_none": has_touch,
                "any_hover_none": has_touch,
            },
            "screen": {
                "width": width,
                "height": height,
                "avail_width": width,
                "avail_height": height,
                "color_depth": 24,
                "pixel_depth": 24,
                "orientation": None,
            },
            "safe_area_insets": {
                "css_env_supported": True,
                "top_px": 0,
                "right_px": 0,
                "bottom_px": 0,
                "left_px": 0,
            },
            "preferences": {
                "prefers_color_scheme_dark": False,
                "prefers_color_scheme_light": True,
                "prefers_color_scheme_no_preference": False,
                "prefers_reduced_motion_reduce": True,
            },
        }
        emulation_expected = {
            **semantics,
            "css_viewport_width": inner_width,
            "css_viewport_height": inner_height,
            "screen_width": width,
            "screen_height": height,
        }
        emulation_actual = {
            "mobile_identity": semantics["device_class"] == "mobile",
            "tablet_identity": semantics["device_class"] == "tablet",
            "has_touch": has_touch,
            "max_touch_points": 1 if has_touch else 0,
            "pointer_coarse": has_touch,
            "any_pointer_coarse": has_touch,
            "hover_none": has_touch,
            "css_viewport_width": inner_width,
            "css_viewport_height": inner_height,
            "screen_width": width,
            "screen_height": height,
            "visual_viewport_present": True,
            "safe_area_css_env_supported": True,
        }
        screenshot_hash = RESPONSIVE_AUDIT.pixel_sha256(image)
        repeat_hash = RESPONSIVE_AUDIT.pixel_sha256(repeated)
        captured_at = dt.datetime.now(dt.timezone.utc).isoformat()
        element_rect = rect or [0, 0, min(100, inner_width), min(100, inner_height)]
        return {
            "id": case_id,
            "class": case_class,
            "state_id": state_id,
            "collector_run_id": run_id,
            "captured_at": captured_at,
            "viewport": {
                "width": width,
                "height": height,
                "base_dpr": base_dpr,
                "dpr": actual_dpr,
                "zoom_percent": zoom,
                "text_zoom_percent": text_zoom,
                **semantics,
            },
            "screenshot": screenshot.name,
            "repeat_screenshot": repeat.name,
            "screenshot_pixel_sha256": screenshot_hash,
            "repeat_pixel_sha256": repeat_hash,
            "screenshot_file_sha256": RESPONSIVE_AUDIT.file_sha256(screenshot),
            "repeat_screenshot_file_sha256": RESPONSIVE_AUDIT.file_sha256(repeat),
            "byte_identical_repeat_capture": not unstable,
            "fatal_error": None,
            "probe": {
                "harness_collected": True,
                "run_id": run_id,
                "route": "/login",
                "inner_width": inner_width,
                "inner_height": inner_height,
                "device_pixel_ratio": actual_dpr,
                "text_zoom_percent": text_zoom,
                "visual_viewport": {
                    "width": inner_width,
                    "height": inner_height,
                    "scale": 1,
                    "offset_left": 0,
                    "offset_top": 0,
                },
                "device_environment": environment,
                "device_emulation": {
                    "browser_name": "chromium",
                    "expected": emulation_expected,
                    "actual": emulation_actual,
                    "errors": [],
                },
                "emulation_errors": [],
                "document_client_width": inner_width,
                "document_scroll_width": inner_width + overflow,
                "body_scroll_width": inner_width + overflow,
                "horizontal_overflow_px": overflow,
                "required_elements": [
                    {
                        "name": "main",
                        "selector": "main",
                        "count": 1,
                        "visible": True,
                        "clipped": False,
                        "rect": element_rect,
                    }
                ],
                "visible_resources": resources or [],
                "overflow_elements": [],
                "unexpected_overlaps": [],
                "missing_required_elements": [],
                "duplicate_required_elements": [],
                "failed_resources": [],
                "console_errors": [],
                "page_errors": [],
                "blocked_write_requests": [],
                "settle_errors": [],
                "cssom_errors": [],
                "unlinked_visible_resources": [],
                "undecoded_visible_rasters": [],
                "dialogs": [],
                "popups": [],
            },
            "visual_review": {
                "status": "pass" if not seams else "fail",
                "reviewer": "codex-visual-inspection",
                "reviewed_at": captured_at,
                "reviewed_screenshot_pixel_sha256": screenshot_hash,
                "unexpected_seams": seams or [],
                "ghosted_artifacts": [],
                "distorted_assets": [],
                "background_mismatches": [],
            },
        }

    def write_manifest(
        self,
        root: Path,
        code_root: Path,
        reference: Path,
        trace: Path,
        cases: list[dict[str, object]],
        *,
        states: list[dict[str, object]] | None = None,
        breakpoints: list[int] | None = None,
        height_breakpoints: list[int] | None = None,
        discovered_queries: list[dict[str, str]] | None = None,
        collector_harness: bool = True,
        code_hash: str | None = None,
        sweep_gap: int = 20,
        sweep_enabled: bool = True,
        color_scheme: str = "light",
    ) -> Path:
        states = states or [
            {
                "id": "default",
                "material": True,
                "primary": True,
                "full_matrix": True,
                "action_hash": RESPONSIVE_AUDIT.canonical_sha256([]),
            }
        ]
        run_id = cases[0]["collector_run_id"] if cases else "run-test"
        (root / "visual-review.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "reviews": [
                        {"case_id": case["id"], **case["visual_review"]}
                        for case in cases
                    ],
                }
            ),
            encoding="utf-8",
        )
        reference_image, _ = RESPONSIVE_AUDIT.load_capture(reference)
        reference_hash = RESPONSIVE_AUDIT.pixel_sha256(reference_image)
        current_code_hash = code_hash or RESPONSIVE_AUDIT.tree_sha256(code_root)
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        trace_events: list[dict[str, object]] = [
            {
                "timestamp": timestamp,
                "type": "run_started",
                "run_id": run_id,
            }
        ]
        for case in cases:
            viewport = case["viewport"]
            probe = case["probe"]
            trace_events.extend(
                [
                    {
                        "timestamp": timestamp,
                        "type": "case_started",
                        "case_id": case["id"],
                        "state": case["state_id"],
                        "viewport": {**viewport, "class": case["class"]},
                    },
                    {
                        "timestamp": timestamp,
                        "type": "case_completed",
                        "case_id": case["id"],
                        "byte_identical_repeat_capture": case[
                            "byte_identical_repeat_capture"
                        ],
                        "screenshot_file_sha256": case["screenshot_file_sha256"],
                        "repeat_screenshot_file_sha256": case[
                            "repeat_screenshot_file_sha256"
                        ],
                        "screenshot_pixel_sha256": case["screenshot_pixel_sha256"],
                        "repeat_pixel_sha256": case["repeat_pixel_sha256"],
                        "fatal_error": case["fatal_error"],
                        "probe_summary": {
                            "inner_width": probe["inner_width"],
                            "inner_height": probe["inner_height"],
                            "device_pixel_ratio": probe["device_pixel_ratio"],
                            "missing_required_elements": probe[
                                "missing_required_elements"
                            ],
                            "overflow_count": len(probe["overflow_elements"]),
                            "unexpected_overlap_count": len(
                                probe["unexpected_overlaps"]
                            ),
                            "console_error_count": len(probe["console_errors"]),
                            "page_error_count": len(probe["page_errors"]),
                            "failed_resource_count": len(probe["failed_resources"]),
                            "blocked_write_request_count": len(
                                probe["blocked_write_requests"]
                            ),
                            "settle_error_count": len(probe["settle_errors"]),
                            "cssom_error_count": len(probe["cssom_errors"]),
                            "unlinked_visible_resource_count": len(
                                probe["unlinked_visible_resources"]
                            ),
                            "undecoded_visible_raster_count": len(
                                probe["undecoded_visible_rasters"]
                            ),
                            "dialog_count": len(probe["dialogs"]),
                            "popup_count": len(probe["popups"]),
                            "visible_resource_count": len(probe["visible_resources"]),
                            "device_emulation_error_count": len(
                                probe["emulation_errors"]
                            ),
                        },
                    },
                ]
            )
        trace_events.append(
            {
                "timestamp": timestamp,
                "type": "run_completed",
                "run_id": run_id,
                "generated_at": timestamp,
                "case_count": len(cases),
                "collection_error_count": 0,
                "code_tree_hash_after": current_code_hash,
            }
        )
        for sequence, event in enumerate(trace_events, 1):
            event["sequence"] = sequence
        trace.write_text(
            "\n".join(
                json.dumps(event, sort_keys=True, separators=(",", ":"))
                for event in trace_events
            )
            + "\n",
            encoding="utf-8",
        )
        primary_id = next(state["id"] for state in states if state["primary"])
        samples = []
        if sweep_enabled:
            for width in range(320, 2561, sweep_gap):
                samples.append(
                    {
                        "width": width,
                        "height": 720,
                        "state_id": primary_id,
                        "horizontal_overflow_px": 0,
                        "overflow_elements": [],
                        "clipped_required_elements": [],
                        "unexpected_overlaps": [],
                        "missing_required_elements": [],
                        "failed_resources": [],
                        "console_errors": [],
                        "settle_errors": [],
                    }
                )
            if samples[-1]["width"] != 2560:
                samples.append({**samples[-1], "width": 2560})
        query_evidence = discovered_queries or []
        discovered_widths = sorted(
            {
                boundary["boundary_value"]
                for query in query_evidence
                if query.get("kind", "media") == "media"
                for boundary in query.get("extracted_boundaries", [])
                if boundary.get("dimension") == "width"
            }
        )
        discovered_heights = sorted(
            {
                boundary["boundary_value"]
                for query in query_evidence
                if query.get("kind", "media") == "media"
                for boundary in query.get("extracted_boundaries", [])
                if boundary.get("dimension") == "height"
            }
        )
        selected_widths = sorted(set(breakpoints or []))
        selected_heights = sorted(set(height_breakpoints or []))
        breakpoint_capture_enabled = bool(selected_widths or selected_heights)
        manifest = root / "responsive.json"
        payload = {
                    "schema_version": "2.0",
                    "profile": "common-2026-08-v2",
                    "route": "/login",
                    "collector": {
                        "name": "pixel-precise-ui-capture",
                        "version": "2.1",
                        "harness_collected": collector_harness,
                        "script_sha256": RESPONSIVE_AUDIT.file_sha256(CAPTURE_SCRIPT),
                        "common_matrix_path": "common-responsive-matrix-2026-08-v2.json",
                        "common_matrix_sha256": RESPONSIVE_AUDIT.file_sha256(
                            CAPTURE_SCRIPT.with_name(
                                "common-responsive-matrix-2026-08-v2.json"
                            )
                        ),
                        "trace_path": trace.name,
                        "trace_sha256": RESPONSIVE_AUDIT.file_sha256(trace),
                        "review_index_path": "review-index.html",
                        "review_index_sha256": RESPONSIVE_AUDIT.file_sha256(
                            root / "review-index.html"
                        ),
                    },
                    "run": {
                        "run_id": run_id,
                        "generated_at": timestamp,
                        "code_tree_hash": current_code_hash,
                        "code_tree_hash_after": current_code_hash,
                        "code_tree_hash_algorithm": "recursive-tree-v2",
                        "input_fingerprint": "f" * 64,
                        "asset_ledger_sha256": "pending-test-ledger",
                        "reference_pixel_sha256": reference_hash,
                        "route": "/login",
                        "state_set_hash": RESPONSIVE_AUDIT.canonical_sha256(states),
                        "browser_name": "chromium",
                        "browser_version": "test",
                        "color_profile": "srgb",
                        "color_scheme": color_scheme,
                    },
                    "states": states,
                    "required_elements": [
                        {
                            "name": "main",
                            "selector": "main",
                            "states": [state["id"] for state in states],
                            "must_intersect_viewport": True,
                            "must_fit_horizontally": True,
                            "must_fit_vertically": True,
                            "disallow_overlap_with": [],
                        }
                    ],
                    "discovered_media_queries": [
                        {
                            "kind": "media",
                            "boundary_extraction_errors": [],
                            **query,
                        }
                        for query in query_evidence
                    ],
                    "breakpoints": selected_widths,
                    "height_breakpoints": selected_heights,
                    "breakpoint_capture": {
                        "enabled": breakpoint_capture_enabled,
                        "maximum_boundaries": 8,
                        "discovered_widths": discovered_widths,
                        "discovered_heights": discovered_heights,
                        "selected_widths": selected_widths,
                        "selected_heights": selected_heights,
                    },
                    "sweep": {
                        "harness_collected": True,
                        "enabled": sweep_enabled,
                        "complete": sweep_enabled,
                        "reason": None if sweep_enabled else "disabled-by-capture-config",
                        "samples": samples,
                    },
                    "cases": cases,
                    "case_budget": {
                        "profile_limit": 80,
                        "actual_cases": len(cases),
                    },
                    "collection_errors": [],
                    "collection_passed": True,
                }
        payload_hash = RESPONSIVE_AUDIT.canonical_sha256(payload)
        attestation_hash = RESPONSIVE_AUDIT.hashlib.sha256(
            (
                "pixel-precise-ui:capture-attestation-v2\0"
                + payload["collector"]["script_sha256"]
                + "\0"
                + payload_hash
            ).encode("utf-8")
        ).hexdigest()
        manifest.write_text(
            json.dumps(
                {
                    **payload,
                    "collector_attestation": {
                        "algorithm": "sha256-canonical-json-v1",
                        "payload_sha256": payload_hash,
                        "attestation_sha256": attestation_hash,
                    },
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def complete_cases(self, root: Path, run_id: str) -> list[dict[str, object]]:
        return [
            self.make_case(
                root,
                run_id,
                f"case-{index}",
                required["class"],
                required["width"],
                required["height"],
                required["zoom"],
                required.get("text_zoom", 100),
                required.get("base_dpr", 1),
            )
            for index, required in enumerate(RESPONSIVE_AUDIT.COMMON_VIEWPORTS)
        ]

    def load_metrics(self, root: Path) -> dict[str, object]:
        return json.loads((root / "output" / "responsive-metrics.json").read_text())

    def test_responsive_named_source_directory_is_fingerprinted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "responsive-layout"
            source.mkdir(parents=True)
            page = source / "page.js"
            page.write_text("export const layout = 'one';\n", encoding="utf-8")
            first = RESPONSIVE_AUDIT.tree_sha256(root)
            first_js = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "--eval",
                    (
                        f"import {{ fingerprintCodeTree }} from {json.dumps(CAPTURE_SCRIPT.as_uri())};"
                        f"console.log((await fingerprintCodeTree({json.dumps(str(root))})).sha256);"
                    ),
                ],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            page.write_text("export const layout = 'two';\n", encoding="utf-8")
            second = RESPONSIVE_AUDIT.tree_sha256(root)
            second_js = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "--eval",
                    (
                        f"import {{ fingerprintCodeTree }} from {json.dumps(CAPTURE_SCRIPT.as_uri())};"
                        f"console.log((await fingerprintCodeTree({json.dumps(str(root))})).sha256);"
                    ),
                ],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()

        self.assertNotEqual(first, second)
        self.assertEqual(first, first_js)
        self.assertEqual(second, second_js)

    def test_complete_current_matrix_is_responsive_certified_but_not_overall_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            cases = self.complete_cases(root, "run-complete")
            manifest = self.write_manifest(root, code_root, reference, trace, cases)
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            metrics = self.load_metrics(root)
            self.assertTrue(metrics["passed"])
            self.assertEqual(metrics["classification"], "responsive-certified")
            self.assertFalse(metrics["completion_eligible"])
            self.assertEqual(metrics["missing_common_viewports"], [])
            self.assertEqual(
                metrics["validator"]["name"], "pixel-precise-ui-responsive-audit"
            )
            self.assertEqual(metrics["validator"]["version"], "2.1")
            self.assertEqual(metrics["replay"]["schema_version"], "1.0")
            self.assertTrue(Path(metrics["replay"]["manifest"]).is_absolute())
            self.assertTrue(
                all(case["device_emulation"]["audit_validated"] for case in metrics["cases"])
            )

    def test_compact_profile_certifies_with_continuous_sweep_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            cases = self.complete_cases(root, "run-compact-no-sweep")
            manifest = self.write_manifest(
                root,
                code_root,
                reference,
                trace,
                cases,
                sweep_enabled=False,
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            metrics = self.load_metrics(root)
            self.assertEqual(metrics["classification"], "responsive-certified")
            self.assertFalse(metrics["sweep"]["enabled"])
            self.assertEqual(metrics["sweep"]["samples"], [])

    def test_hand_authored_collector_claim_cannot_certify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            manifest = self.write_manifest(
                root, code_root, reference, trace, [], collector_harness=False
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            metrics = self.load_metrics(root)
            self.assertIn(
                "automated_capture_harness",
                [violation["gate"] for violation in metrics["violations"]],
            )
            self.assertEqual(metrics["classification"], "blocked")

    def test_run_requires_supported_color_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            manifest = self.write_manifest(
                root,
                code_root,
                reference,
                trace,
                [],
                color_scheme="sepia",
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("run.color_scheme", result.stderr)

    def test_mobile_case_requires_real_emulation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            case = self.make_case(
                root, "run-device", "device-case", "mobile-portrait", 390, 844, base_dpr=3
            )
            case["probe"]["device_emulation"]["errors"] = [
                {"gate": "touch-capability", "expected": True, "actual": False}
            ]
            case["probe"]["emulation_errors"] = list(
                case["probe"]["device_emulation"]["errors"]
            )
            manifest = self.write_manifest(root, code_root, reference, trace, [case])
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            metrics = self.load_metrics(root)
            gates = {violation["gate"] for violation in metrics["violations"]}
            self.assertIn("device_emulation_errors", gates)
            audited_case = metrics["cases"][0]
            self.assertEqual(audited_case["viewport"]["device_class"], "mobile")
            self.assertEqual(audited_case["emulation_errors"], case["probe"]["emulation_errors"])
            self.assertIn("device_environment", audited_case["browser_evidence"])

    def test_case_device_fields_must_match_matrix_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            case = self.make_case(
                root, "run-device-class", "wrong-device", "mobile-portrait", 390, 844, base_dpr=3
            )
            case["viewport"].update(
                {"device_class": "desktop", "is_mobile": False, "has_touch": False}
            )
            manifest = self.write_manifest(root, code_root, reference, trace, [case])
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(
                "viewport_device_semantics",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_trace_case_hashes_must_correlate_with_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            case = self.make_case(
                root, "run-trace", "trace-case", "mobile-portrait", 320, 568, base_dpr=2
            )
            manifest = self.write_manifest(root, code_root, reference, trace, [case])
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["cases"][0]["screenshot_pixel_sha256"] = "0" * 64
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(
                "capture_trace_manifest_correlation",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_case_fatal_error_is_blocking_even_when_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            case = self.make_case(
                root, "run-fatal", "fatal-case", "mobile-portrait", 320, 568, base_dpr=2
            )
            case["fatal_error"] = {"name": "Error", "message": "probe failed"}
            manifest = self.write_manifest(root, code_root, reference, trace, [case])
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            metrics = self.load_metrics(root)
            self.assertIn(
                "fatal_capture_error",
                [violation["gate"] for violation in metrics["violations"]],
            )
            self.assertEqual(metrics["classification"], "blocked")

    def test_collection_errors_cannot_be_overridden_by_a_pass_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            manifest = self.write_manifest(root, code_root, reference, trace, [])
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["collection_errors"] = [
                {"gate": "browser_launch", "message": "browser unavailable"}
            ]
            payload["collection_passed"] = True
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(
                "capture_collection_failed",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_missing_common_viewport_fails_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            manifest = self.write_manifest(root, code_root, reference, trace, [])
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(
                "missing_common_viewport",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_unstable_overflowing_outside_case_with_seam_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            case = self.make_case(
                root,
                "run-broken",
                "broken",
                "mobile-portrait",
                320,
                568,
                overflow=24,
                seams=["rectangular plate behind login card"],
                unstable=True,
                rect=[400, 0, 100, 100],
            )
            manifest = self.write_manifest(root, code_root, reference, trace, [case])
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            gates = [violation["gate"] for violation in self.load_metrics(root)["violations"]]
            self.assertIn("pixel_identical_repeat_capture", gates)
            self.assertIn("horizontal_overflow", gates)
            self.assertIn("required_element_outside_viewport", gates)
            self.assertIn("unexpected_seams", gates)

    def test_discovered_breakpoint_requires_minus_one_exact_plus_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            cases = [
                self.make_case(
                    root, "run-breakpoint", f"bp-{width}", "breakpoint-boundary", width, 700
                )
                for width in (699, 700)
            ]
            manifest = self.write_manifest(
                root,
                code_root,
                reference,
                trace,
                cases,
                breakpoints=[700],
                discovered_queries=[
                    {
                        "query": "(min-width: 43.75em)",
                        "extracted_boundaries": [
                            {
                                "dimension": "width",
                                "boundary_value": 700,
                                "css_px": 700,
                                "unit": "em",
                                "value": 43.75,
                            }
                        ],
                    }
                ],
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            missing = self.load_metrics(root)["missing_breakpoint_cases"]
            self.assertIn(701, {item["required_value"] for item in missing})

    def test_container_boundary_is_not_misclassified_as_viewport_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            cases = self.complete_cases(root, "run-container-query")
            manifest = self.write_manifest(
                root,
                code_root,
                reference,
                trace,
                cases,
                discovered_queries=[
                    {
                        "kind": "container",
                        "query": "(inline-size > var(--card-breakpoint))",
                        "extracted_boundaries": [],
                        "boundary_extraction_errors": [
                            {
                                "gate": "unresolved-responsive-boundary",
                                "expression": "inline-size > var(--card-breakpoint)",
                            }
                        ],
                    }
                ],
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            metrics = self.load_metrics(root)
            self.assertEqual(metrics["discovered_width_breakpoints"], [])
            self.assertEqual(metrics["missing_breakpoint_cases"], [])
            self.assertEqual(metrics["classification"], "responsive-certified")

    def test_stale_code_tree_hash_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            manifest = self.write_manifest(
                root, code_root, reference, trace, [], code_hash="0" * 64
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(
                "stale_capture_evidence",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def raster_fields(
        self,
        path: Path,
        *,
        name: str = "asset",
        kind: str = "image",
        status: str = "exact",
        usage: str = "full-bleed-background",
        origin: str = "repository",
        operations: list[str] | None = None,
        material: bool = True,
    ) -> dict[str, object]:
        with Image.open(path) as opened:
            image = opened.copy()
        return {
            "name": name,
            "kind": kind,
            "status": status,
            "material": material,
            "evidence": "Synthetic raster fixture.",
            "usage": usage,
            "origin": origin,
            "contains_foreground_pixels": False,
            "contains_context_pixels": False,
            "occluded_pixels": "none",
            "responsive_safe": True,
            "derivation_operations": operations or ["repository-source"],
            "path": path.name,
            "file_sha256": RESPONSIVE_AUDIT.file_sha256(path),
            "pixel_sha256": RESPONSIVE_AUDIT.pixel_sha256(image),
            "intrinsic_dimensions": [image.width, image.height],
        }

    def run_with_asset(self, root: Path, asset: dict[str, object]) -> dict[str, object]:
        code_root, reference, trace = self.prepare_root(root)
        manifest = self.write_manifest(root, code_root, reference, trace, [])
        ledger = root / "asset-ledger.json"
        ledger.write_text(json.dumps({"assets": [asset]}), encoding="utf-8")
        result = self.run_audit(root, manifest, ledger, code_root, reference)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        return self.load_metrics(root)

    def test_png_mislabeled_other_and_material_false_cannot_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plate = root / "plate.png"
            Image.new("RGB", (80, 60), "#eef7fb").save(plate)
            metrics = self.run_with_asset(
                root, self.raster_fields(plate, kind="other", material=False)
            )
            self.assertIn(
                "raster_kind_mismatch",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_generated_exact_and_unknown_operation_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plate = root / "plate.png"
            Image.new("RGB", (80, 60), "#eef7fb").save(plate)
            metrics = self.run_with_asset(
                root,
                self.raster_fields(
                    plate, origin="generated", operations=["content-aware-fill"]
                ),
            )
            gates = [violation["gate"] for violation in metrics["violations"]]
            self.assertIn("invalid_exact_origin", gates)
            self.assertIn("unsupported_derivation_operation", gates)

    def test_one_transparent_pixel_is_not_an_isolated_silhouette(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            icon = root / "icon.png"
            image = Image.new("RGBA", (40, 40), (255, 255, 255, 255))
            image.putpixel((0, 0), (255, 255, 255, 0))
            image.save(icon)
            asset = self.raster_fields(icon, kind="icon", usage="isolated-asset")
            asset["alpha"] = RESPONSIVE_AUDIT.alpha_statistics(image)
            metrics = self.run_with_asset(root, asset)
            self.assertIn(
                "isolated_asset_alpha",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_complete_reference_loaded_as_asset_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            asset = self.raster_fields(reference, name="page-reference")
            manifest = self.write_manifest(root, code_root, reference, trace, [])
            ledger = root / "asset-ledger.json"
            ledger.write_text(json.dumps({"assets": [asset]}), encoding="utf-8")
            result = self.run_audit(root, manifest, ledger, code_root, reference)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(
                "full_reference_reuse",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_split_reference_crops_cannot_reassemble_full_page_plate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            with Image.open(reference) as source:
                left_image = source.crop((0, 0, 32, 48))
                right_image = source.crop((32, 0, 64, 48))
            left_path = root / "left-half.png"
            right_path = root / "right-half.png"
            left_image.save(left_path)
            right_image.save(right_path)
            left = self.raster_fields(
                left_path,
                name="left-half",
                origin="reference-crop",
                operations=["lossless-crop"],
            )
            left["source_bounds"] = [0, 0, 32, 48]
            right = self.raster_fields(
                right_path,
                name="right-half",
                origin="reference-crop",
                operations=["lossless-crop"],
            )
            right["source_bounds"] = [32, 0, 32, 48]
            ledger = root / "asset-ledger.json"
            ledger.write_text(json.dumps({"assets": [left, right]}), encoding="utf-8")
            manifest = self.write_manifest(root, code_root, reference, trace, [])
            result = self.run_audit(root, manifest, ledger, code_root, reference)

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(
                "cumulative_reference_crop_plate",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_unledgered_visible_resource_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            case = self.make_case(
                root,
                "run-resource",
                "resource",
                "mobile-portrait",
                320,
                568,
                resources=[
                    {
                        "type": "background-image",
                        "url": "/mystery.png",
                        "loaded": True,
                        "ledger_name": "missing",
                        "decoded_pixel_sha256": "1" * 64,
                    }
                ],
            )
            manifest = self.write_manifest(root, code_root, reference, trace, [case])
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(
                "unledgered_visible_resource",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_missing_independent_visual_review_cannot_certify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            case = self.make_case(
                root, "run-review", "review-case", "mobile-portrait", 320, 568
            )
            manifest = self.write_manifest(root, code_root, reference, trace, [case])
            (root / "visual-review.json").write_text(
                json.dumps(
                    {"schema_version": "1.0", "run_id": "run-review", "reviews": []}
                ),
                encoding="utf-8",
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(
                "missing_independent_visual_review",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_manifest_tampering_breaks_collector_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            manifest = self.write_manifest(root, code_root, reference, trace, [])
            ledger = self.write_safe_ledger(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["route"] = "/tampered"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = self.run_audit(
                root,
                manifest,
                ledger,
                code_root,
                reference,
                refresh_manifest=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(
                "capture_manifest_attestation",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_continuous_sweep_gap_over_twenty_pixels_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            manifest = self.write_manifest(
                root, code_root, reference, trace, [], sweep_gap=40
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(
                "continuous_width_gap",
                [violation["gate"] for violation in self.load_metrics(root)["violations"]],
            )

    def test_material_secondary_state_requires_four_compact_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            states = [
                {
                    "id": "default",
                    "material": True,
                    "primary": True,
                    "full_matrix": True,
                    "action_hash": "a" * 64,
                },
                {
                    "id": "error",
                    "material": True,
                    "primary": False,
                    "full_matrix": False,
                    "action_hash": "b" * 64,
                },
            ]
            manifest = self.write_manifest(
                root, code_root, reference, trace, [], states=states
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            missing = self.load_metrics(root)["missing_material_state_viewports"]
            self.assertEqual(
                {item["class"] for item in missing},
                {
                    "mobile-portrait",
                    "mobile-landscape",
                    "tablet-portrait",
                    "desktop",
                },
            )

    def test_material_secondary_state_requires_exact_anchor_dpr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = self.prepare_root(root)
            states = [
                {
                    "id": "default",
                    "material": True,
                    "primary": True,
                    "full_matrix": True,
                    "action_hash": "a" * 64,
                },
                {
                    "id": "error",
                    "material": True,
                    "primary": False,
                    "full_matrix": False,
                    "action_hash": "b" * 64,
                },
            ]
            cases = self.complete_cases(root, "run-secondary-dpr")
            for index, required in enumerate(
                RESPONSIVE_AUDIT.SECONDARY_STATE_ANCHORS
            ):
                cases.append(
                    self.make_case(
                        root,
                        "run-secondary-dpr",
                        f"error-{index}",
                        required["class"],
                        required["width"],
                        required["height"],
                        required["zoom"],
                        required.get("text_zoom", 100),
                        1.5,
                        state_id="error",
                    )
                )
            manifest = self.write_manifest(
                root, code_root, reference, trace, cases, states=states
            )
            result = self.run_audit(
                root, manifest, self.write_safe_ledger(root), code_root, reference
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            missing = self.load_metrics(root)["missing_material_state_viewports"]
            self.assertEqual(len(missing), 4)
            self.assertEqual(
                {item["base_dpr"] for item in missing},
                {1, 2, 3},
            )

    def test_compact_common_sizes_are_in_profile(self) -> None:
        profile = {
            (entry["class"], entry["width"], entry["height"])
            for entry in RESPONSIVE_AUDIT.COMMON_VIEWPORTS
        }
        for expected in {
            ("mobile-portrait", 360, 800),
            ("mobile-portrait", 393, 873),
            ("mobile-portrait", 390, 844),
            ("mobile-portrait", 414, 896),
            ("mobile-landscape", 844, 390),
            ("tablet-portrait", 768, 1024),
            ("tablet-landscape", 1280, 800),
            ("desktop", 1280, 720),
            ("desktop", 1366, 768),
            ("desktop", 1536, 864),
            ("desktop", 1920, 1080),
            ("desktop-zoom", 1366, 768),
            ("accessibility-text-zoom", 390, 844),
        }:
            self.assertIn(expected, profile)
        self.assertEqual(len(RESPONSIVE_AUDIT.COMMON_VIEWPORTS), 13)
        mobile = [
            entry
            for entry in RESPONSIVE_AUDIT.COMMON_VIEWPORTS
            if entry["class"].startswith("mobile-")
        ]
        tablets = [
            entry
            for entry in RESPONSIVE_AUDIT.COMMON_VIEWPORTS
            if entry["class"].startswith("tablet-")
        ]
        self.assertTrue(all(entry["base_dpr"] >= 2 for entry in mobile))
        self.assertTrue(all(entry["base_dpr"] == 2 for entry in tablets))
        portrait_dpr = {
            (entry["width"], entry["height"]): entry["base_dpr"]
            for entry in mobile
            if entry["class"] == "mobile-portrait"
        }
        for entry in mobile:
            if entry["class"] == "mobile-landscape":
                self.assertEqual(
                    entry["base_dpr"],
                    portrait_dpr[(entry["height"], entry["width"])],
                )


if __name__ == "__main__":
    unittest.main()
