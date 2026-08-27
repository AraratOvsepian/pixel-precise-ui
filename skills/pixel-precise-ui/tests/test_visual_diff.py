from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


SCRIPT = Path(__file__).parents[1] / "scripts" / "visual_diff.py"


class VisualDiffTests(unittest.TestCase):
    def run_diff(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
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
            self.assertEqual(
                metrics["stability"]["normalized_mean_absolute_difference"], 0
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


if __name__ == "__main__":
    unittest.main()
