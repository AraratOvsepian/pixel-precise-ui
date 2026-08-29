# Responsive Validation Protocol

Use this protocol for every screenshot-to-code task. A supplied screenshot proves only one registered route, state, viewport, DPR, and capture environment. Responsive certification is a separate claim.

## 1. Three gates, one completion result

The tools deliberately separate three forms of evidence:

1. `visual_diff.py` proves strict reference parity at the registered reference viewport.
2. `capture_responsive.mjs` and `responsive_audit.py` prove browser-collected responsive behavior across the maintained compact matrix and material states. Breakpoint-boundary capture and a continuous width sweep are additional evidence only when explicitly enabled.
3. `certify_run.py` treats both metric files only as replay recipes, reruns the current visual and responsive validators from the raw evidence, verifies that the code tree and every linked input stayed unchanged during replay, and then binds the fresh results to the same run, reference, route, and state set. It is the only command allowed to emit `completion_eligible: true`.

Neither `achieved` reference parity nor `responsive-certified` alone means the UI is done. If exact source information is unavailable, report the reference result as `blocked` even when responsive certification passes.

Prerequisites: use Node.js with `playwright` or `@playwright/test` installed in the target project and the selected Playwright browser binary already installed (for example, `npx playwright install chromium` under the target project's dependency policy). The audit and joint gate require Python 3 and Pillow. The collector never installs packages, downloads browsers, or substitutes a globally installed Playwright runtime; missing dependencies are a non-certifying setup error.

## 2. Compact common matrix

The machine-readable source of truth is [common-responsive-matrix-2026-08-v2.json](../scripts/common-responsive-matrix-2026-08-v2.json). Both the browser collector and validator verify its fingerprint.

The default profile contains 13 high-value cases:

- mobile portrait: 360 × 800, 390 × 844, 393 × 873, and 414 × 896;
- mobile landscape: 844 × 390;
- tablet portrait and landscape: 768 × 1024 and 1280 × 800;
- laptop/desktop: 1280 × 720, 1366 × 768, 1536 × 864, and 1920 × 1080;
- browser zoom smoke: 1366 × 768 at 200%;
- mobile text-zoom smoke: 390 × 844 at 200%.

Always add the supplied reference viewport and DPR when not already represented. Also add product-analytics targets, embedded webviews, and a declared minimum or maximum supported width when those facts exist. Do not add speculative devices. The compact profile intentionally omits ultrawide, legacy small-phone, and broad zoom permutations to keep ordinary iteration fast. Enable wider coverage only for an explicit release audit, a user request, or evidence of a failure outside the compact cases.

## 3. Browser-owned evidence

Do not hand-author or repair a responsive manifest. `responsive_audit.py` accepts only collector-owned, hash-attested browser evidence with:

- capture-script and common-matrix fingerprints;
- a canonical manifest attestation;
- a structured, hashed JSONL trace;
- exact screenshot and repeat-capture decoded RGB hashes;
- start/end code-tree fingerprints and the asset-ledger fingerprint;
- browser name/version, locale, timezone, fixed time, color profile, color scheme, route, run ID, and state-set hash;
- actual DOM geometry, overflow, media-query, resource, console, page, and network evidence;
- a hashed `review-index.html` contact sheet.

The attestation is tamper-evidence for the cooperative workflow, not a secret-key signature. The audit therefore also correlates the trace to every case and recomputes the underlying files, and the joint certifier independently replays both validators. Evidence older than 24 hours, captured from a different code tree/reference/ledger, edited after collection, or produced by a different collector version is non-certifying.

The recursive code fingerprint excludes only explicit dependency, build, cache, and evidence directories such as `node_modules`, `build`, `dist`, `.cache`, `captures`, `responsive-check`, `visual-check`, and `completion-check`. Source directories whose names merely begin with `responsive-` are not excluded. The JSONL trace must correlate one-to-one with every manifest case start/completion, exact screenshot hashes, fatal/error summary, and run-completion counts.

The harness resolves `playwright` or `@playwright/test` from the target project. It never silently downloads a browser and refuses non-local origins unless the user explicitly authorizes a remote test origin. Unsafe HTTP methods, popups, downloads, and dialogs are blocked or recorded; never use production as the visual test surface.

## 4. Capture plan

Create a plan for the route and every visually material state. The primary/default state runs across the compact matrix. A secondary state with `full_matrix:false` runs only at 390 × 844, 844 × 390, 768 × 1024, and 1366 × 768. Set `full_matrix:true` on a secondary state only when its layout materially differs across widths.

Example:

```json
{
  "schema_version": "2.0",
  "base_url": "http://127.0.0.1:3001",
  "route": "/login",
  "color_scheme": "light",
  "reference_pixel_sha256": "<decoded-reference-rgb-sha256>",
  "include_common_matrix": true,
  "capture_breakpoint_boundaries": false,
  "states": [
    {
      "id": "default",
      "material": true,
      "primary": true,
      "full_matrix": true,
      "actions": []
    },
    {
      "id": "validation-error",
      "material": true,
      "primary": false,
      "full_matrix": false,
      "actions": [
        {"type": "click", "selector": "[data-test-show-validation-error]"}
      ]
    }
  ],
  "required_elements": [
    {
      "name": "login-card",
      "selector": "main",
      "states": ["default", "validation-error"],
      "must_intersect_viewport": true,
      "must_fit_horizontally": true,
      "must_fit_vertically": false,
      "disallow_overlap_with": []
    }
  ],
  "continuous_sweep": {
    "enabled": false,
    "min_width": 320,
    "max_width": 2560,
    "step_px": 8,
    "state_ids": ["default"]
  }
}
```

Set `color_scheme` to `light`, `dark`, or `no-preference` so the context and evidence match the supplied reference. It defaults to `light`; the run record and actual `prefers-color-scheme` browser probe are audited and later joined to strict visual metrics.

Use safe state actions only. The harness freezes time and randomness, injects local/session storage before navigation, disables motion/carets, waits for fonts/images/network settling, and takes two unchanged lossless screenshots.

Run:

```bash
node scripts/capture_responsive.mjs \
  --config responsive-plan.json \
  --code-root /absolute/path/to/project \
  --asset-ledger asset-ledger.json \
  --output-dir /absolute/path/to/project/captures/<run-id>
```

The output directory must be new or empty and, when inside the project, must be under a fingerprint-excluded evidence directory such as `captures/`.

## 5. Optional breakpoint and exhaustive-sweep gates

Breakpoint capture is disabled by default. The collector still inventories accessible loaded `@media` and `@container` rules. When `capture_breakpoint_boundaries:true` is explicitly chosen, it captures the primary state at `b - 1`, `b`, and `b + 1` for every numeric viewport `@media` width or height boundary. The compact run accepts at most eight total viewport boundaries and 80 screenshot cases; it fails before creating extra boundary screenshots when either budget would be exceeded. The exact boundary remains useful because inclusive `min-width` and `max-width` rules can both apply there.

Container-query `inline-size` and `block-size` values are recorded but never converted into viewport widths: a viewport at 768px does not prove that a nested container is 768px. Validate a material container-driven layout through explicit states and required-element geometry, or treat exact container behavior as outside the compact run. Unresolved numeric viewport `@media` expressions remain a blocker when optional breakpoint capture is enabled.

The continuous 320–2560 sweep is disabled by default. Enable it only when the user explicitly requests exhaustive coverage, during a release audit where the extra runtime is accepted, or after a failure appears between the compact sizes and declared breakpoints. When enabled, the existing maximum 20-pixel gap and full-range gates apply, and the sweep runs on the primary state unless `state_ids` explicitly adds another state.

## 6. Independent visual review

Machine probes cannot reliably decide whether a legitimate rectangle is a panel or an exposed screenshot plate. Open the generated `review-index.html` and inspect every screenshot at normal size. Do not edit the attested capture manifest.

Create a separate `visual-review.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "<same-run-id>",
  "reviews": [
    {
      "case_id": "0001-default-mobile-390x844-z100-t100-d3",
      "status": "pass",
      "reviewer": "codex-visual-inspection",
      "reviewed_at": "2026-08-27T12:00:00Z",
      "reviewed_screenshot_pixel_sha256": "<exact-captured-pixel-hash>",
      "unexpected_seams": [],
      "ghosted_artifacts": [],
      "distorted_assets": [],
      "background_mismatches": []
    }
  ]
}
```

Every captured case needs a review bound to its exact screenshot hash. `pending`, a missing case, a stale hash, an unsupported reviewer type, or a non-empty anomaly array fails the audit.

## 7. Browser probe gates

For every case the audit requires:

- actual `innerWidth`, `innerHeight`, effective DPR, browser zoom, and text zoom registration;
- class-consistent device semantics: `mobile-*` and mobile accessibility cases use real Playwright `isMobile` plus touch emulation; tablet and tablet accessibility cases use touch emulation; a narrow desktop Chromium context cannot certify as mobile;
- browser-collected navigator/UA and UA-CH (when available), `maxTouchPoints`, touch/coarse-pointer/hover media results, `visualViewport`, screen/orientation, safe-area insets, reduced-motion, and color-scheme evidence with an empty emulation-error list;
- screenshot dimensions consistent with base DPR and exact decoded repeat equality;
- the configured required element selectors exactly once, visible, non-clipped, and geometrically inside their declared viewport constraints;
- no horizontal document/element overflow above one CSS pixel;
- no forbidden overlaps;
- no missing/duplicate required elements, console/page errors, blocked writes, failed resources, settle/CSSOM errors, dialogs, or popups;
- every visible image/background/font linked to the asset ledger, with visible raster decoded hashes matching the ledger.

A manifest that merely says those arrays are empty is insufficient; the collector identity, script hash, trace, manifest attestation, and evidence fingerprints must all validate.

## 8. Asset provenance gates

Every raster asset requires a real path plus validator-checked file hash, decoded RGB hash, intrinsic dimensions, origin, usage, occlusion/contamination claims, responsive-safety decision, and a non-empty allowlisted derivation operation list.

Allowed provenance-preserving operations are:

- `authoritative-source`
- `repository-source`
- `lossless-copy`
- `lossless-crop`
- `alpha-mask`
- `metadata-strip`
- `color-profile-normalize`
- `vector-render`

Generated/inferred assets cannot claim `exact` or `derived-deterministically`. Unknown operations are blocked rather than guessed safe. A decodable PNG cannot bypass raster checks by declaring `kind: other` or `material: false`.

For `origin: reference-crop`, provide `source_bounds`; the validator checks dimensions and source pixels. It rejects complete-reference reuse, individual near-full-page screenshot plates, and cumulative/adjacent mosaics in which multiple material crops (for example, two halves or four quadrants) reconstruct a near/full page. Isolated raster assets require a meaningful transparent silhouette: some alpha variation or one transparent pixel is not enough.

The collector inventories visible browser resources and requires a one-to-one ledger link. A valid ledger entry that is not the resource actually loaded by the page cannot satisfy the gate.

## 9. Audit and joint certification

Run responsive audit:

```bash
python3 scripts/responsive_audit.py \
  captures/<run-id>/responsive-evidence.json \
  --asset-ledger asset-ledger.json \
  --reference source.png \
  --code-root /absolute/path/to/project \
  --visual-review visual-review.json \
  --output-dir responsive-check
```

Then run strict reference comparison using the per-case run metadata generated by the collector:

```bash
python3 scripts/visual_diff.py source.png reference-render.png \
  --stability-capture reference-render-repeat.png \
  --regions regions.json \
  --asset-ledger asset-ledger.json \
  --run-metadata captures/<run-id>/run-metadata/<reference-case>.json \
  --strict-parity \
  --output-dir visual-check
```

Finally replay both validators from their raw-input recipes and bind only the fresh results:

```bash
python3 scripts/certify_run.py \
  visual-check/metrics.json \
  responsive-check/responsive-metrics.json \
  --output-dir completion-check
```

The supplied `passed` and `classification` fields are never trusted by this final command. Only a successful independent replay by `certify_run.py` may emit `classification: achieved-and-responsive-certified` with `completion_eligible: true`.

## 10. Iteration contract

When any case fails:

1. classify the cause as canvas, flow geometry, typography/wrapping, asset composition, breakpoint, resource, or state;
2. change one subsystem;
3. recapture the failed case and nearest widths;
4. because the code-tree fingerprint changed, produce a fresh compact harness run, including all currently enabled optional cases;
5. repeat independent visual review and all three gates.

Do not reuse evidence after CSS, code, fonts, assets, state actions, or the asset ledger changes. Do not waive a failed mobile/zoom/state case because the desktop reference improved. Stop only on joint success or an evidence-backed blocker.
