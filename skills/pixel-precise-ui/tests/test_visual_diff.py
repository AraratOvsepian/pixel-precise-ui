from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


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


if __name__ == "__main__":
    unittest.main()
