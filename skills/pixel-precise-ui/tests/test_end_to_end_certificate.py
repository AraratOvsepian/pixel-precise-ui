from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "responsive_fixture", TESTS_DIR / "test_responsive_audit.py"
)
assert FIXTURE_SPEC is not None and FIXTURE_SPEC.loader is not None
RESPONSIVE_FIXTURE = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(RESPONSIVE_FIXTURE)


class EndToEndCertificateTests(unittest.TestCase):
    def test_raw_evidence_is_replayed_before_completion_certificate(self) -> None:
        fixture = RESPONSIVE_FIXTURE.ResponsiveAuditTests(
            methodName="test_complete_current_matrix_is_responsive_certified_but_not_overall_complete"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root, reference, trace = fixture.prepare_root(root)
            run_id = "run-joint-replay"
            reference_case = fixture.make_case(
                root, run_id, "case-reference", "reference", 64, 48
            )
            with Image.open(reference) as source:
                source.save(root / str(reference_case["screenshot"]))
                source.save(root / str(reference_case["repeat_screenshot"]))
                reference_pixel_hash = RESPONSIVE_FIXTURE.RESPONSIVE_AUDIT.pixel_sha256(
                    source
                )
            for field in ("screenshot_pixel_sha256", "repeat_pixel_sha256"):
                reference_case[field] = reference_pixel_hash
            reference_case["screenshot_file_sha256"] = (
                RESPONSIVE_FIXTURE.RESPONSIVE_AUDIT.file_sha256(
                    root / str(reference_case["screenshot"])
                )
            )
            reference_case["repeat_screenshot_file_sha256"] = (
                RESPONSIVE_FIXTURE.RESPONSIVE_AUDIT.file_sha256(
                    root / str(reference_case["repeat_screenshot"])
                )
            )
            reference_case["byte_identical_repeat_capture"] = True
            reference_case["visual_review"][
                "reviewed_screenshot_pixel_sha256"
            ] = reference_pixel_hash
            cases = [reference_case, *fixture.complete_cases(root, run_id)]
            manifest = fixture.write_manifest(
                root, code_root, reference, trace, cases
            )
            ledger = fixture.write_safe_ledger(root)
            responsive_result = fixture.run_audit(
                root, manifest, ledger, code_root, reference
            )
            self.assertEqual(
                responsive_result.returncode,
                0,
                responsive_result.stdout + responsive_result.stderr,
            )
            responsive_metrics = root / "output" / "responsive-metrics.json"

            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            run_metadata = root / "run-metadata.json"
            run_metadata.write_text(
                json.dumps(
                    {
                        "run": {
                            **manifest_payload["run"],
                            "state": "default",
                            "capture_case_id": "case-reference",
                            "capture_screenshot_pixel_sha256": reference_pixel_hash,
                            "capture_repeat_pixel_sha256": reference_pixel_hash,
                        }
                    }
                ),
                encoding="utf-8",
            )
            regions = root / "regions.json"
            regions.write_text(
                json.dumps(
                    {
                        "regions": [
                            {
                                "name": "full-page",
                                "kind": "full-page",
                                "bounds": [0, 0, 64, 48],
                                "protected": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            visual_output = root / "visual-output"
            visual_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "visual_diff.py"),
                    str(reference),
                    str(root / str(reference_case["screenshot"])),
                    "--stability-capture",
                    str(root / str(reference_case["repeat_screenshot"])),
                    "--regions",
                    str(regions),
                    "--asset-ledger",
                    str(ledger),
                    "--run-metadata",
                    str(run_metadata),
                    "--strict-parity",
                    "--output-dir",
                    str(visual_output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                visual_result.returncode, 0, visual_result.stdout + visual_result.stderr
            )

            certificate_output = root / "certificate"
            certificate_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "certify_run.py"),
                    str(visual_output / "metrics.json"),
                    str(responsive_metrics),
                    "--output-dir",
                    str(certificate_output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                certificate_result.returncode,
                0,
                certificate_result.stdout + certificate_result.stderr,
            )
            certificate = json.loads(
                (certificate_output / "completion-certificate.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(certificate["completion_eligible"])
            self.assertEqual(
                certificate["classification"],
                "achieved-and-responsive-certified",
            )
            self.assertEqual(
                certificate["validator_replay"]["mode"],
                "independent-validator-replay-v1",
            )
            self.assertEqual(
                certificate["validator_replay"]["code_tree_sha256_before"],
                certificate["validator_replay"]["code_tree_sha256_after"],
            )


if __name__ == "__main__":
    unittest.main()
