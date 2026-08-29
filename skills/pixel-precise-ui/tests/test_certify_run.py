from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "certify_run.py"
CAPTURE_SCRIPT = Path(__file__).parents[1] / "scripts" / "capture_responsive.mjs"
VISUAL_SCRIPT = Path(__file__).parents[1] / "scripts" / "visual_diff.py"
RESPONSIVE_SCRIPT = Path(__file__).parents[1] / "scripts" / "responsive_audit.py"


SPEC = importlib.util.spec_from_file_location("pixel_precise_certify_run", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CERTIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CERTIFIER)


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class CertifyRunTests(unittest.TestCase):
    def setUp(self) -> None:
        reference_hash = "a" * 64
        rendered_hash = reference_hash
        self.run = {
            "run_id": "ppui-test-run",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "code_tree_hash": "b" * 64,
            "code_tree_hash_after": "b" * 64,
            "code_tree_hash_algorithm": "recursive-tree-v2",
            "input_fingerprint": "c" * 64,
            "asset_ledger_sha256": "3" * 64,
            "reference_pixel_sha256": reference_hash,
            "route": "/login",
            "state_set_hash": "d" * 64,
            "browser_name": "chromium",
            "browser_version": "140.0.0.0",
            "color_profile": "srgb",
            "color_scheme": "dark",
        }
        self.visual: dict[str, Any] = {
            "strict_parity": True,
            "passed": True,
            "classification": "achieved",
            "violations": [],
            "blockers": [],
            "dimensions_match": True,
            "stability_dimensions_match": True,
            "resized_for_comparison": False,
            "stability_pixel_identical": True,
            "stability_pixel_hashes_match": True,
            "pixel_sha256": {
                "source": reference_hash,
                "rendered": rendered_hash,
                "stability_capture": rendered_hash,
            },
            "exact_pixel_metrics": {
                "changed_pixels": 0,
                "max_channel_difference": 0,
            },
            "stability_exact_pixel_metrics": {
                "changed_pixels": 0,
                "max_channel_difference": 0,
            },
            "run": {
                **self.run,
                "state": "default",
                "capture_case_id": "case-default-desktop",
                "capture_screenshot_pixel_sha256": rendered_hash,
                "capture_repeat_pixel_sha256": rendered_hash,
            },
        }
        self.responsive: dict[str, Any] = {
            "schema_version": "2.0",
            "profile": "common-2026-08-v2",
            "passed": True,
            "classification": "responsive-certified",
            "completion_eligible": False,
            "violations": [],
            "blockers": [],
            "run": deepcopy(self.run),
            "collector": {
                "name": "pixel-precise-ui-capture",
                "version": "2.1",
                "harness_collected": True,
                "trace_sha256": "e" * 64,
                "script_sha256": sha256(CAPTURE_SCRIPT),
            },
            "reference_pixel_sha256": reference_hash,
            "current_code_tree_hash": "b" * 64,
            "manifest_file_sha256": "1" * 64,
            "visual_review_file_sha256": "4" * 64,
            "states": [
                {
                    "id": "default",
                    "material": True,
                    "primary": True,
                    "full_matrix": True,
                    "action_hash": "2" * 64,
                }
            ],
            "cases": [
                {
                    "id": "case-default-desktop",
                    "state_id": "default",
                    "passed": True,
                    "pixel_sha256": rendered_hash,
                    "repeat_pixel_sha256": rendered_hash,
                    "viewport": {
                        "device_class": "desktop",
                        "is_mobile": False,
                        "has_touch": False,
                    },
                    "device_emulation": {
                        "browser_name": "chromium",
                        "audit_validated": True,
                        "errors": [],
                    },
                    "emulation_errors": [],
                    "browser_evidence": {
                        "visual_viewport": {"width": 1440, "height": 900},
                        "device_environment": {"navigator": {}, "touch": {}},
                    },
                }
            ],
        }
        self.replay_report = {
            "mode": "independent-validator-replay-v1",
            "visual_validator_sha256": sha256(VISUAL_SCRIPT),
            "responsive_validator_sha256": sha256(RESPONSIVE_SCRIPT),
            "visual_validator_returncode": 0,
            "responsive_validator_returncode": 0,
            "code_tree_sha256_before": "5" * 64,
            "code_tree_sha256_after": "5" * 64,
            "raw_input_sha256_before": {"fixture": "6" * 64},
            "raw_input_sha256_after": {"fixture": "6" * 64},
            "replayed_visual_metrics_sha256": "7" * 64,
            "replayed_responsive_metrics_sha256": "8" * 64,
        }

    def run_join_unit(
        self,
        root: Path,
        visual: dict[str, Any] | None = None,
        responsive: dict[str, Any] | None = None,
        replay_report: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Exercise only the deterministic join gates with replay already proven.

        Synthetic result dictionaries are useful fixtures for testing join logic, but
        they must never be fed through the production CLI because the CLI now replays
        both validators from raw evidence before it calls ``certify``.
        """
        visual_path = root / "visual.json"
        responsive_path = root / "responsive.json"
        visual_path.write_text(json.dumps(visual or self.visual), encoding="utf-8")
        responsive_path.write_text(
            json.dumps(responsive or self.responsive), encoding="utf-8"
        )
        return CERTIFIER.certify(
            visual or self.visual,
            responsive or self.responsive,
            visual_path,
            responsive_path,
            replay_report or self.replay_report,
        )

    def test_matching_strict_and_responsive_results_are_completion_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(Path(directory))

        self.assertTrue(eligible)
        self.assertTrue(certificate["completion_eligible"])
        self.assertEqual(
            certificate["classification"], "achieved-and-responsive-certified"
        )
        self.assertTrue(all(gate["passed"] for gate in certificate["gates"]))

    def test_run_id_mismatch_is_not_completion_eligible(self) -> None:
        responsive = deepcopy(self.responsive)
        responsive["run"]["run_id"] = "another-run"
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), responsive=responsive
            )

        self.assertFalse(eligible)
        self.assertFalse(certificate["completion_eligible"])
        self.assertIn(
            "shared_run_identity",
            {violation["gate"] for violation in certificate["violations"]},
        )

    def test_color_scheme_mismatch_is_not_completion_eligible(self) -> None:
        visual = deepcopy(self.visual)
        visual["run"]["color_scheme"] = "light"
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), visual=visual
            )

        self.assertFalse(eligible)
        identity_gate = next(
            gate for gate in certificate["gates"] if gate["name"] == "shared_run_identity"
        )
        self.assertIn("color_scheme", identity_gate["mismatches"])

    def test_visual_must_be_zero_difference_strict_evidence(self) -> None:
        visual = deepcopy(self.visual)
        visual["exact_pixel_metrics"]["changed_pixels"] = 1
        visual["passed"] = False
        visual["classification"] = "failed"
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), visual=visual
            )

        self.assertFalse(eligible)
        gates = {violation["gate"] for violation in certificate["violations"]}
        self.assertIn("visual_passed", gates)
        self.assertIn("visual_zero_changed_pixels", gates)

    def test_responsive_pass_without_harness_identity_is_rejected(self) -> None:
        responsive = deepcopy(self.responsive)
        responsive["collector"]["harness_collected"] = False
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), responsive=responsive
            )

        self.assertFalse(eligible)
        self.assertIn(
            "responsive_browser_harness",
            {violation["gate"] for violation in certificate["violations"]},
        )

    def test_visual_pixels_must_be_the_certified_responsive_case(self) -> None:
        responsive = deepcopy(self.responsive)
        responsive["cases"][0]["pixel_sha256"] = "9" * 64
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), responsive=responsive
            )

        self.assertFalse(eligible)
        self.assertIn(
            "visual_capture_pixel_identity",
            {violation["gate"] for violation in certificate["violations"]},
        )

    def test_responsive_case_must_preserve_validated_device_evidence(self) -> None:
        responsive = deepcopy(self.responsive)
        responsive["cases"][0]["device_emulation"]["audit_validated"] = False
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), responsive=responsive
            )

        self.assertFalse(eligible)
        self.assertIn(
            "responsive_case_device_emulation",
            {violation["gate"] for violation in certificate["violations"]},
        )

    def test_upstream_completion_claim_cannot_bypass_joint_gate(self) -> None:
        responsive = deepcopy(self.responsive)
        responsive["completion_eligible"] = True
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), responsive=responsive
            )

        self.assertFalse(eligible)
        self.assertIn(
            "responsive_no_upstream_completion_claim",
            {violation["gate"] for violation in certificate["violations"]},
        )

    def test_stale_responsive_metrics_cannot_be_certified_later(self) -> None:
        visual = deepcopy(self.visual)
        responsive = deepcopy(self.responsive)
        visual["run"]["generated_at"] = "2020-01-01T00:00:00Z"
        responsive["run"]["generated_at"] = "2020-01-01T00:00:00Z"
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), visual=visual, responsive=responsive
            )

        self.assertFalse(eligible)
        self.assertIn(
            "fresh_joint_evidence",
            {violation["gate"] for violation in certificate["violations"]},
        )

    def test_missing_capture_case_binding_is_schema_error(self) -> None:
        visual = deepcopy(self.visual)
        del visual["run"]["capture_case_id"]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "capture_case_id"):
                self.run_join_unit(Path(directory), visual=visual)

    def test_join_unit_cannot_pass_without_independent_replay_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            certificate, eligible = self.run_join_unit(
                Path(directory), replay_report={"mode": "synthetic"}
            )

        self.assertFalse(eligible)
        self.assertIn(
            "independent_validator_replay",
            {violation["gate"] for violation in certificate["violations"]},
        )

    def test_cli_rejects_forged_pass_metrics_without_raw_replay_evidence(self) -> None:
        """A hand-authored green JSON pair is not a certifiable CLI input."""
        visual = deepcopy(self.visual)
        responsive = deepcopy(self.responsive)
        visual["validator"] = {
            "name": "pixel-precise-ui-visual-diff",
            "version": "2.0",
            "script_sha256": sha256(VISUAL_SCRIPT),
        }
        responsive["validator"] = {
            "name": "pixel-precise-ui-responsive-audit",
            "version": "2.1",
            "script_sha256": sha256(RESPONSIVE_SCRIPT),
        }
        # Even with authentic validator fingerprints and forged pass fields, the CLI
        # must demand replayable raw inputs rather than accepting the claimed result.
        visual["replay"] = {"schema_version": "1.0", "strict_parity": True}
        responsive["replay"] = {"schema_version": "1.0"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            visual_path = root / "visual.json"
            responsive_path = root / "responsive.json"
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            responsive_path.write_text(json.dumps(responsive), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(visual_path),
                    str(responsive_path),
                    "--output-dir",
                    str(root / "certificate"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("visual replay.source", result.stderr)
            self.assertFalse(
                (root / "certificate" / "completion-certificate.json").exists()
            )

    def test_main_uses_replayed_results_not_supplied_pass_fields(self) -> None:
        """The CLI join receives fresh validator output, not user-provided booleans."""
        supplied_visual = deepcopy(self.visual)
        supplied_responsive = deepcopy(self.responsive)
        replayed_visual = deepcopy(self.visual)
        replayed_visual["passed"] = False
        replayed_visual["classification"] = "failed"
        replayed_visual["violations"] = [{"gate": "fresh-validator-failure"}]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            visual_path = root / "visual.json"
            responsive_path = root / "responsive.json"
            output_dir = root / "certificate"
            visual_path.write_text(json.dumps(supplied_visual), encoding="utf-8")
            responsive_path.write_text(
                json.dumps(supplied_responsive), encoding="utf-8"
            )
            arguments = [
                str(SCRIPT),
                str(visual_path),
                str(responsive_path),
                "--output-dir",
                str(output_dir),
            ]
            with mock.patch.object(
                CERTIFIER,
                "replay_validators",
                return_value=(
                    replayed_visual,
                    deepcopy(self.responsive),
                    deepcopy(self.replay_report),
                ),
            ) as replay, mock.patch.object(sys, "argv", arguments), contextlib.redirect_stdout(
                io.StringIO()
            ):
                returncode = CERTIFIER.main()

            replay.assert_called_once_with(supplied_visual, supplied_responsive)
            certificate = json.loads(
                (output_dir / "completion-certificate.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(returncode, 1)
        self.assertFalse(certificate["completion_eligible"])
        self.assertFalse(certificate["visual_result"]["passed"])
        self.assertEqual(certificate["visual_result"]["classification"], "failed")
        self.assertIn(
            "visual_passed",
            {violation["gate"] for violation in certificate["violations"]},
        )


if __name__ == "__main__":
    unittest.main()
