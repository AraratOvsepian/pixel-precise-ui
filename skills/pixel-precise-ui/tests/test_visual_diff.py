from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


SCRIPT = Path(__file__).parents[1] / "scripts" / "visual_diff.py"


class VisualDiffTests(unittest.TestCase):
    def run_diff(
        self,
        root: Path,
        *args: str,
        auto_ledger: bool = True,
        auto_run_metadata: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command_args = list(args)
        if (
            auto_ledger
            and "--strict-parity" in command_args
            and "--asset-ledger" not in command_args
        ):
            ledger = root / "asset-ledger.json"
            ledger.write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "name": "synthetic-reference",
                                "kind": "other",
                                "status": "exact",
                                "material": True,
                                "evidence": "Test fixture generated from the same pixels.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            command_args.extend(["--asset-ledger", str(ledger)])
        if (
            auto_run_metadata
            and "--strict-parity" in command_args
            and "--run-metadata" not in command_args
        ):
            source_path = Path(command_args[0])
            with Image.open(source_path) as source_image:
                source_rgb = ImageOps.exif_transpose(source_image).convert("RGB")
            digest = hashlib.sha256()
            digest.update(
                f"RGB:{source_rgb.width}x{source_rgb.height}:".encode("ascii")
            )
            digest.update(source_rgb.tobytes())
            run_metadata = root / "run-metadata.json"
            run_metadata.write_text(
                json.dumps(
                    {
                        "run": {
                            "run_id": "visual-diff-test-run",
                            "code_tree_hash": "test-code-tree-hash",
                            "reference_pixel_sha256": digest.hexdigest(),
                            "route": "/test",
                            "state_set_hash": "test-state-set-hash",
                            "state": "default",
                        }
                    }
                ),
                encoding="utf-8",
            )
            command_args.extend(["--run-metadata", str(run_metadata)])
        return subprocess.run(
            [sys.executable, str(SCRIPT), *command_args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_identical_images_pass_strict_region_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (12, 10), "#123456")
            image.save(source)
            image.save(rendered)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "panel",
                                "bounds": [2, 2, 8, 6],
                                "protected": True,
                                "max_normalized_mean_absolute_difference": 0,
                                "max_percent_pixels_over_threshold": 0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--require-dimensions",
                "--threshold",
                "0",
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertTrue(metrics["passed"])
            self.assertEqual(metrics["classification"], "diagnostic-pass")
            self.assertEqual(metrics["regions"][0]["metrics"]["pixels_over_threshold"], 0)
            self.assertTrue((output / "overlay.png").is_file())
            self.assertTrue((output / "regions" / "panel-overlay.png").is_file())

    def test_protected_region_regression_fails_even_when_global_change_is_small(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            baseline = root / "baseline.png"
            candidate = root / "candidate.png"
            regions = root / "regions.json"
            output = root / "output"
            original = Image.new("RGB", (100, 100), "#101010")
            original.save(source)
            original.save(baseline)
            changed = original.copy()
            for x in range(10, 20):
                for y in range(10, 20):
                    changed.putpixel((x, y), (255, 255, 255))
            changed.save(candidate)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "logo",
                                "bounds": [10, 10, 10, 10],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(candidate),
                "--baseline",
                str(baseline),
                "--regions",
                str(regions),
                "--fail-on-regression",
                "--regression-tolerance",
                "0",
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            gates = [violation["gate"] for violation in metrics["violations"]]
            self.assertIn("protected_region_regression", gates)
            self.assertGreater(
                metrics["regions"][0]["normalized_mad_regression"], 0
            )

    def test_dimension_gate_rejects_mismatch_without_resizing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            output = root / "output"
            Image.new("RGB", (10, 10), "white").save(source)
            Image.new("RGB", (11, 10), "white").save(rendered)

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--require-dimensions",
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertFalse(metrics["dimensions_match"])
            self.assertFalse(metrics["resized_for_comparison"])
            self.assertEqual(metrics["classification"], "diagnostic-fail")

    def test_context_gate_detects_seam_around_exact_core_crop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            regions = root / "regions.json"
            output = root / "output"
            original = Image.new("RGB", (32, 32), "#101a24")
            for x in range(14, 18):
                for y in range(14, 18):
                    original.putpixel((x, y), (245, 245, 245))
            original.save(source)
            candidate = original.copy()
            for x in range(6, 26):
                for y in range(6, 26):
                    if not (10 <= x < 22 and 10 <= y < 22):
                        candidate.putpixel((x, y), (45, 55, 65))
            candidate.save(rendered)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "logo",
                                "bounds": [10, 10, 12, 12],
                                "protected": True,
                                "max_normalized_mean_absolute_difference": 0,
                                "context_padding": 4,
                                "max_context_normalized_mean_absolute_difference": 0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--require-region-gates",
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            logo = metrics["regions"][0]
            self.assertEqual(
                logo["metrics"]["normalized_mean_absolute_difference"], 0
            )
            self.assertGreater(
                logo["context_metrics"]["normalized_mean_absolute_difference"], 0
            )
            self.assertIn(
                "context_normalized_mean_absolute_difference",
                [violation["gate"] for violation in metrics["violations"]],
            )
            self.assertTrue(
                (output / "regions" / "logo-context-difference.png").is_file()
            )

    def test_edge_gate_detects_flat_control_missing_glass_rim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            regions = root / "regions.json"
            output = root / "output"
            original = Image.new("RGB", (36, 24), "#07111b")
            drawing = ImageDraw.Draw(original)
            drawing.rounded_rectangle(
                (4, 5, 31, 18), radius=4, fill="#0c1a27", outline="#bdeeff", width=1
            )
            original.save(source)
            candidate = Image.new("RGB", (36, 24), "#07111b")
            candidate_drawing = ImageDraw.Draw(candidate)
            candidate_drawing.rounded_rectangle(
                (4, 5, 31, 18), radius=4, fill="#0c1a27"
            )
            candidate.save(rendered)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "input-surface",
                                "bounds": [4, 5, 28, 14],
                                "protected": True,
                                "max_normalized_mean_absolute_difference": 1,
                                "max_edge_normalized_mean_absolute_difference": 0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--require-region-gates",
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertIn(
                "edge_normalized_mean_absolute_difference",
                [violation["gate"] for violation in metrics["violations"]],
            )
            self.assertGreater(
                metrics["regions"][0][
                    "edge_normalized_mean_absolute_difference"
                ],
                0,
            )

    def test_strict_region_mode_rejects_protected_region_without_absolute_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (12, 12), "#123456")
            image.save(source)
            image.save(rendered)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "ungated",
                                "bounds": [2, 2, 8, 8],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--require-region-gates",
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertIn(
                "missing_absolute_region_gate",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_strict_parity_passes_only_with_lossless_stable_full_page_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (40, 30), "#123456")
            image.save(source)
            image.save(rendered)
            image.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 40, 30],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertTrue(metrics["passed"])
            self.assertTrue(metrics["strict_parity"])
            self.assertEqual(metrics["classification"], "achieved")
            self.assertEqual(metrics["exact_pixel_metrics"]["pixels_over"]["0"], 0)
            self.assertEqual(
                metrics["pixel_sha256"]["source"],
                metrics["pixel_sha256"]["rendered"],
            )
            self.assertEqual(
                metrics["stability"]["normalized_mean_absolute_difference"], 0
            )
            self.assertEqual(metrics["stability"]["exact_changed_pixels"], 0)
            self.assertTrue(metrics["stability"]["pixel_hashes_match"])
            self.assertEqual(
                metrics["stability_exact_pixel_metrics"]["changed_pixels"], 0
            )
            self.assertTrue(metrics["stability_pixel_hashes_match"])
            self.assertTrue(metrics["stability_pixel_identical"])
            self.assertEqual(
                metrics["run"]["reference_pixel_sha256"],
                metrics["pixel_sha256"]["source"],
            )

    def test_strict_parity_rejects_jpeg_bytes_hidden_behind_png_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (40, 30), "#123456")
            image.save(source)
            image.save(rendered, format="JPEG")
            image.save(stability, format="JPEG")
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 40, 30],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            lossless_violations = [
                violation
                for violation in metrics["violations"]
                if violation["gate"] == "lossless_format"
            ]
            self.assertEqual(len(lossless_violations), 2)
            self.assertTrue(all(item["actual"] == "JPEG" for item in lossless_violations))

    def test_strict_parity_rejects_non_repeatable_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (40, 30), "#123456")
            image.save(source)
            image.save(rendered)
            changed = image.copy()
            changed.putpixel((1, 1), (255, 255, 255))
            changed.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 40, 30],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertIn(
                "pixel_identical_repeat_capture",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_one_changed_pixel_in_4k_capture_fails_exact_stability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (3840, 2160), (18, 52, 86))
            image.save(source)
            image.save(rendered)
            changed = image.copy()
            changed.putpixel((3839, 2159), (19, 52, 86))
            changed.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 3840, 2160],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertEqual(
                metrics["stability"]["normalized_mean_absolute_difference"], 0
            )
            self.assertEqual(metrics["stability"]["exact_changed_pixels"], 1)
            self.assertEqual(
                metrics["stability"]["exact_pixel_metrics"]["changed_pixels"], 1
            )
            self.assertFalse(metrics["stability"]["pixel_hashes_match"])
            self.assertEqual(
                metrics["stability_exact_pixel_metrics"]["changed_pixels"], 1
            )
            self.assertFalse(metrics["stability_pixel_hashes_match"])
            self.assertFalse(metrics["stability_pixel_identical"])
            self.assertNotEqual(
                metrics["stability"]["pixel_sha256"]["rendered"],
                metrics["stability"]["pixel_sha256"]["stability_capture"],
            )
            stability_violation = next(
                violation
                for violation in metrics["violations"]
                if violation["gate"] == "pixel_identical_repeat_capture"
            )
            self.assertEqual(stability_violation["actual"], 1)

    def test_strict_parity_applies_immutable_global_limits_without_cli_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            original = Image.new("RGB", (100, 100), "#101010")
            original.save(source)
            changed = original.copy()
            drawing = ImageDraw.Draw(changed)
            drawing.rectangle((10, 10, 29, 29), fill="white")
            changed.save(rendered)
            changed.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 100, 100],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            gates = [violation["gate"] for violation in metrics["violations"]]
            self.assertIn("strict_normalized_mean_absolute_difference", gates)
            self.assertIn("strict_worst_tile_normalized_mean_absolute_difference", gates)

    def test_strict_asset_requires_a_silhouette_mask(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (40, 40), "#123456")
            image.save(source)
            image.save(rendered)
            image.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 40, 40],
                                "protected": True,
                            },
                            {
                                "name": "logo",
                                "kind": "asset",
                                "bounds": [10, 10, 20, 20],
                                "context_padding": 2,
                                "protected": True,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertIn(
                "strict_asset_mask",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_masked_asset_core_cannot_hide_a_rectangular_boundary_seam(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            mask = root / "mask.png"
            regions = root / "regions.json"
            output = root / "output"
            original = Image.new("RGB", (40, 40), "#101a24")
            source_drawing = ImageDraw.Draw(original)
            source_drawing.rectangle((18, 18, 21, 21), fill="white")
            original.save(source)
            candidate = original.copy()
            candidate_drawing = ImageDraw.Draw(candidate)
            candidate_drawing.rectangle((10, 10, 29, 29), fill="#253545")
            candidate_drawing.rectangle((18, 18, 21, 21), fill="white")
            candidate.save(rendered)
            candidate.save(stability)
            silhouette = Image.new("L", (20, 20), 0)
            silhouette_drawing = ImageDraw.Draw(silhouette)
            silhouette_drawing.rectangle((8, 8, 11, 11), fill=255)
            silhouette.save(mask)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 40, 40],
                                "protected": True,
                            },
                            {
                                "name": "logo",
                                "kind": "asset",
                                "bounds": [10, 10, 20, 20],
                                "mask": "mask.png",
                                "context_padding": 2,
                                "protected": True,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            logo = next(item for item in metrics["regions"] if item["name"] == "logo")
            self.assertEqual(
                logo["masked_metrics"]["normalized_mean_absolute_difference"], 0
            )
            self.assertIn(
                "strict_asset_boundary_discontinuity",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_strict_parity_rejects_even_one_changed_rgb_pixel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            original = Image.new("RGB", (100, 100), "#123456")
            original.save(source)
            changed = original.copy()
            changed.putpixel((50, 50), (19, 52, 86))
            changed.save(rendered)
            changed.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 100, 100],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertIn(
                "strict_exact_pixel_mismatch",
                [violation["gate"] for violation in metrics["violations"]],
            )
            self.assertEqual(metrics["exact_pixel_metrics"]["pixels_over"]["0"], 1)
            self.assertEqual(metrics["classification"], "failed")

    def test_strict_parity_blocks_without_asset_provenance_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (20, 20), "#123456")
            image.save(source)
            image.save(rendered)
            image.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 20, 20],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
                auto_ledger=False,
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertEqual(metrics["classification"], "blocked")
            self.assertIn(
                "strict_asset_ledger",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_strict_parity_blocks_without_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            output = root / "output"
            image = Image.new("RGB", (20, 20), "#123456")
            image.save(source)
            image.save(rendered)
            image.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 20, 20],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
                auto_run_metadata=False,
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertIsNone(metrics["run"])
            self.assertEqual(metrics["classification"], "blocked")
            self.assertIn(
                "strict_run_metadata",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_run_metadata_reference_hash_must_match_and_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            run_metadata_path = root / "run-metadata.json"
            output = root / "output"
            image = Image.new("RGB", (20, 20), "#123456")
            image.save(source)
            image.save(rendered)
            image.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 20, 20],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            run = {
                "run_id": "stale-run",
                "code_tree_hash": "code-tree-hash",
                "reference_pixel_sha256": "not-the-decoded-source-hash",
                "route": "/login",
                "state_set_hash": "state-set-hash",
                "state": "default",
            }
            run_metadata_path.write_text(
                json.dumps({"run": run}), encoding="utf-8"
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--run-metadata",
                str(run_metadata_path),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertEqual(metrics["run"], run)
            self.assertNotEqual(
                metrics["run"]["reference_pixel_sha256"],
                metrics["pixel_sha256"]["source"],
            )
            self.assertEqual(metrics["classification"], "blocked")
            self.assertIn(
                "run_metadata_reference_pixel_sha256",
                [violation["gate"] for violation in metrics["violations"]],
            )

    def test_strict_parity_blocks_complete_reference_raster_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            reused_asset = root / "reused-reference.bmp"
            regions = root / "regions.json"
            ledger = root / "asset-ledger.json"
            output = root / "output"
            image = Image.new("RGB", (40, 30), "#123456")
            image.putpixel((8, 7), (200, 100, 50))
            image.save(source)
            image.save(rendered)
            image.save(stability)
            image.save(reused_asset, format="BMP")
            self.assertNotEqual(source.read_bytes(), reused_asset.read_bytes())
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 40, 30],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ledger.write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "name": "declared-safe-background",
                                "kind": "image",
                                "status": "exact",
                                "material": True,
                                "evidence": "Declared as an authoritative clean plate.",
                                "path": reused_asset.name,
                                "usage": "decorative",
                                "origin": "authoritative",
                                "contains_foreground_pixels": False,
                                "contains_context_pixels": False,
                                "occluded_pixels": "none",
                                "responsive_safe": True,
                                "derivation_operations": ["authoritative"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--asset-ledger",
                str(ledger),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertEqual(metrics["exact_pixel_metrics"]["changed_pixels"], 0)
            self.assertEqual(metrics["classification"], "blocked")
            self.assertIn(
                "strict_full_reference_raster_reuse",
                [violation["gate"] for violation in metrics["violations"]],
            )
            inspection = metrics["raster_asset_inspections"][0]
            self.assertEqual(inspection["dimensions"], {"width": 40, "height": 30})
            self.assertTrue(inspection["matches_complete_source"])
            self.assertEqual(
                inspection["pixel_sha256"], metrics["pixel_sha256"]["source"]
            )

    def test_unresolved_material_font_is_an_explicit_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            ledger = root / "asset-ledger.json"
            output = root / "output"
            image = Image.new("RGB", (30, 20), "#123456")
            image.save(source)
            image.save(rendered)
            image.save(stability)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 30, 20],
                                "protected": True,
                            },
                            {
                                "name": "heading",
                                "kind": "text",
                                "bounds": [2, 2, 20, 10],
                                "protected": True,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ledger.write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "name": "unknown-font",
                                "kind": "font",
                                "status": "missing",
                                "material": True,
                                "evidence": "The flattened source does not identify a font.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--asset-ledger",
                str(ledger),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertEqual(metrics["classification"], "blocked")
            gates = [violation["gate"] for violation in metrics["violations"]]
            self.assertIn("unresolved_material_asset", gates)
            self.assertIn("strict_font_provenance", gates)

    def test_pixel_identical_reference_cannot_hide_reconstructed_responsive_plate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            rendered = root / "rendered.png"
            stability = root / "stability.png"
            regions = root / "regions.json"
            ledger = root / "asset-ledger.json"
            plate = root / "interlocked-background.png"
            output = root / "output"
            image = Image.new("RGB", (40, 30), "#123456")
            image.save(source)
            image.save(rendered)
            image.save(stability)
            Image.new("RGB", (16, 12), "#654321").save(plate)
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 40, 30],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ledger.write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "name": "interlocked-background",
                                "kind": "image",
                                "status": "derived-deterministically",
                                "material": True,
                                "evidence": "Hidden center was interpolated under the card.",
                                "path": plate.name,
                                "usage": "full-bleed-background",
                                "origin": "reference-crop",
                                "contains_foreground_pixels": False,
                                "contains_context_pixels": False,
                                "occluded_pixels": "reconstructed",
                                "responsive_safe": False,
                                "derivation_operations": [
                                    "lossless-copy",
                                    "interpolate",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_diff(
                root,
                str(source),
                str(rendered),
                "--regions",
                str(regions),
                "--asset-ledger",
                str(ledger),
                "--strict-parity",
                "--stability-capture",
                str(stability),
                "--output-dir",
                str(output),
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            metrics = json.loads((output / "metrics.json").read_text())
            self.assertEqual(metrics["exact_pixel_metrics"]["pixels_over"]["0"], 0)
            self.assertEqual(metrics["classification"], "blocked")
            gates = [violation["gate"] for violation in metrics["violations"]]
            self.assertIn("invalid_deterministic_provenance", gates)
            self.assertIn("strict_unsafe_full_bleed_background", gates)


if __name__ == "__main__":
    unittest.main()
