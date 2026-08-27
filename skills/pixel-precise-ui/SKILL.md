---
name: pixel-precise-ui
description: Recreate interfaces from supplied reference images in an existing frontend codebase, using strict reference-parity, asset-safety, responsive-matrix, and regression gates. Use for screenshot-to-code and explicit visual matching; do not use for ordinary UI work without a visual reference.
license: MIT
---

# Pixel Precise UI

Reconstruct supplied interface references with maintainable code in the project's existing frontend stack. Preserve user intent, product behavior, semantics, accessibility, routes, and data flow.

## Choose the mode before editing

- **Strict parity** is mandatory when the user says `pixel precise`, `pixel perfect`, `exact`, or `match exactly`. Read [references/strict-parity.md](references/strict-parity.md) and follow every gate.
- **Visual approximation** applies only when the user asks for direction, inspiration, a close recreation, or explicitly accepts approximation. Approximate or generated assets are allowed, but the result cannot be reported as exact.

Never silently downgrade strict parity. If a strict run lacks an authoritative font, logo, image, viewport fact, or deterministic capture surface, exhaust repository evidence and then report the specific blocker before substituting anything.

Reference parity and responsive correctness are separate claims. Read [references/responsive-validation.md](references/responsive-validation.md) for every run. A UI is not complete until the reference result is honestly classified and the required responsive matrix is `responsive-certified`.

## Non-negotiable rules

- Read applicable repository instructions and preserve user-owned changes.
- Inspect every reference at original resolution and register its probable CSS viewport, device-pixel ratio, route, and UI state.
- Never use the complete reference screenshot as a page background, overlay, canvas, or single full-page image.
- Never create interlocking screenshot plates whose seams are hidden only when a fixed foreground component covers a reconstructed background area. Full-bleed backgrounds may not contain unknown/reconstructed occluded pixels. Component plates may not contain surrounding page context.
- Do not present an opaque crop of an isolated logo or icon as exact when the crop contains pixels from the surrounding surface. Use an authoritative transparent asset, a verified mask, or treat it as blocked. Always compare a padded context around isolated assets so a perfect core score cannot hide a rectangular seam.
- Do not build a page or component background from a screenshot crop that still contains blurred text, controls, buttons, shadows, or glows belonging to foreground UI. Background-contaminated assets are failed experiments, not clean plates.
- Keep text semantic HTML. Use CSS for ordinary geometry and surfaces, an exact product asset or precise SVG for brand/interface marks, and raster assets only for genuinely image-based content.
- In strict mode, do not generate, redraw, or approximate a missing logo, font, photograph, background plate, or complex texture. A flattened screenshot cannot reveal pixels hidden behind an opaque or translucent foreground; treat missing hidden pixels as a source limitation.
- Do not submit live forms, mutate production data, or trigger external side effects for visual state.
- Do not claim parity from geometry alone or from one global image score.
- A strict run is exact only when the decoded source and render have identical RGB pixel hashes and zero changed pixels. Similarity thresholds are iteration diagnostics, never permission to say `pixel-perfect`.
- A `passed` result from the script without `--strict-parity` is diagnostic only. It is never completion evidence for an exact-match request.
- A passing reference diff proves only the registered reference viewport. It never proves responsive correctness.
- Do not call the task done until the automated browser collector and `scripts/responsive_audit.py` pass the complete `common-2026-07-v1` matrix, every discovered width and height breakpoint at `b-1/b/b+1`, the 320–2560 continuous sweep, repeated-capture stability, material-state coverage, independent visual review, browser probes, and responsive asset-safety gates.
- Never hand-author or repair the responsive evidence manifest. It is collector-owned, hash-attested evidence whose trace must correlate with every case; record post-capture visual inspection in a separate hash-bound review file.
- `visual_diff.py` and `responsive_audit.py` are intentionally non-completing inputs. Only `scripts/certify_run.py`, after independently replaying both current validators from the raw evidence, may emit `completion_eligible: true`.

## Branch and deployment isolation

Visual implementation is local-only unless the user explicitly authorizes a commit or push. At preflight, record the current branch/worktree and inspect GitHub Actions plus repository hosting/deployment configuration. If the current branch can deploy, move the design work to a dedicated non-deploying branch before committing. Never push a design-only change to the default, production, staging, or any other deployment-matched branch unless the user explicitly requests that deployment. Immediately before an authorized push, re-check the exact destination branch against every push/workflow-run trigger and any discoverable external hosting integration; uncertainty is a blocker, not a zero-workflow result. Report the destination branch and the names/count of workflows or deployments it can trigger. Never dispatch a deployment workflow or use a live deployment as a visual test surface without separate explicit authorization.

## Tool prerequisites

Fail preflight before editing if the verification environment is incomplete. The diff and responsive-audit scripts require Python 3 with Pillow. The collector requires Node.js plus `playwright` or `@playwright/test` and its browser binary installed in the target project; it deliberately does not download browsers or alter project dependencies. Use one fixed browser/version for the bound run and record it in the evidence. Missing prerequisites are a capture blocker, never a reason to replace automated evidence with hand-authored JSON.

## Required workflow

### 1. Preflight the target

Inspect the route, component tree, styles, resets, computed fonts, available font files, product assets, local command, and running server. Read [references/visual-analysis.md](references/visual-analysis.md) and create:

- source registration with confidence labels;
- asset ledger with `exact`, `derived-deterministically`, `approximate`, or `missing` status;
- element inventory and semantic component tree;
- token sheet and computed-style audit;
- verification landmarks;
- protected comparison regions in a JSON manifest, including padded asset context and individual material-bearing controls where applicable;
- a material-layer inventory for visibly glassy, metallic, translucent, embossed, or otherwise dimensional surfaces;
- a responsive capture plan containing the full `common-2026-07-v1` viewport matrix, all material UI states, safe required-element selectors, and continuous-sweep settings; the browser collector must create the evidence manifest;
- asset-composition evidence for every visible raster asset, including usage, origin, foreground/context contamination, occluded-pixel state, derivation operations, and responsive-safety status.

In strict mode, unresolved `approximate` or `missing` assets that materially affect visible pixels block an `achieved` result. Ask for the original layered design, clean image plate, exact SVG/logo, or font file when needed.

### 2. Establish a deterministic capture

Render at the reference's exact CSS viewport and device-pixel ratio. Do not score a desktop source against a responsive mobile render.

Before capture:

- wait for the route and fonts to finish loading;
- disable motion, caret blinking, and time-dependent effects;
- reproduce only safe local state;
- inspect console/compilation errors;
- capture twice without changes and verify stability.

Both scored captures must be genuinely lossless images. Inspect their decoded format; a `.png` filename containing JPEG bytes is a failed capture. Strict validation requires the two unchanged captures to be pixel-identical.

If the available browser surface cannot reproduce the required viewport or scale, use another authorized deterministic local capture surface or report the capture limitation. Do not pretend a different viewport is equivalent.

### 3. Implement outside-in

Work in this order unless the source proves another dependency:

1. Viewport and canvas
2. Primary geometry and centerlines
3. Major surfaces
4. Exact typography and wrapping
5. Exact images, logos, and icons
6. Controls and states
7. Borders, shadows, and micro-spacing
8. Responsive behavior supported by reference evidence

Restyle existing functional components. Scope reference tokens locally. Remove failed experiments rather than accumulating overrides or positional transforms.

Build responsive geometry from normal layout, intrinsic component boundaries, and breakpoint rules. Do not align a fixed card against a separately scaled raster hole, stretch a reference canvas with `100% 100%` behind fixed-position content, or use browser zoom as a layout mechanism.

### 4. Compare by protected region

During iteration, run structural measurement, overlay inspection, and the deterministic diff script in diagnostic mode. Example:

```bash
python3 scripts/visual_diff.py source.png render.png \
  --stability-capture render-repeat.png \
  --regions regions.json \
  --asset-ledger asset-ledger.json \
  --baseline previous-render.png \
  --fail-on-regression \
  --output-dir visual-check
```

This non-strict command may emit only `diagnostic-pass` or `diagnostic-fail`; it cannot finish the task. The final strict run uses immutable run metadata from the responsive collector in the next step.

Change one subsystem per round. Record the hypothesis and relevant regional metrics before editing. Give every protected region an absolute gate; a baseline-regression check alone is insufficient when the baseline is already wrong. Use context gates around isolated assets and edge-detail gates on dimensional controls. Accept a round only when the intended region improves and no protected region exceeds its configured gate or regression tolerance. A better global score never excuses a worse logo, typography, form, or other protected region.

### 5. Certify responsive behavior

Follow [references/responsive-validation.md](references/responsive-validation.md). Use `capture_responsive.mjs`; do not fabricate probe values. It captures the common matrix with realistic mobile/tablet DPR and real mobile/touch semantics, every declared material state, discovered media/container-query boundaries, exact repeats, resource inventory, and continuous sweep. Inspect every collector-produced capture through its generated review index, write the separate visual-review file, then run:

```bash
node scripts/capture_responsive.mjs \
  --config responsive-plan.json \
  --code-root /absolute/path/to/project \
  --asset-ledger asset-ledger.json \
  --output-dir /absolute/path/to/project/captures/<run-id>

python3 scripts/responsive_audit.py \
  captures/<run-id>/responsive-evidence.json \
  --asset-ledger asset-ledger.json \
  --reference source.png \
  --code-root /absolute/path/to/project \
  --visual-review visual-review.json \
  --output-dir responsive-check

python3 scripts/visual_diff.py source.png reference-render.png \
  --stability-capture reference-render-repeat.png \
  --regions regions.json \
  --asset-ledger asset-ledger.json \
  --run-metadata captures/<run-id>/run-metadata/<reference-case>.json \
  --strict-parity \
  --output-dir visual-check

python3 scripts/certify_run.py \
  visual-check/metrics.json \
  responsive-check/responsive-metrics.json \
  --output-dir completion-check
```

If a case fails, repair one subsystem and inspect the failing case plus adjacent sizes. Any code, CSS, font, asset, state-action, or ledger change invalidates the old fingerprint; rerun the complete collector, review, audit, and joint certificate. A passing desktop reference plus a sampled mobile view is not sufficient.

### 6. Stop according to evidence

The task is complete only when `certify_run.py` replays the current validators and emits `classification: achieved-and-responsive-certified` and `completion_eligible: true`. Supplied pass booleans and classifications are never authoritative. The fresh replayed inputs must prove all of the following in the same run/code/reference/route/state environment:

- dimensions and responsive state match;
- stable captures are reproducible;
- the source and rendered decoded RGB pixel hashes match and the exact changed-pixel count is zero;
- the machine-readable asset ledger contains no material `approximate` or `missing` entry, and every asset region links to its ledger record;
- major edges and baselines are within the registered tolerance, normally one CSS pixel;
- text family, wrapping, and glyph positioning match;
- every protected region passes its configured visual gates;
- isolated assets are seamless in their padded context;
- dimensional controls reproduce the source's material cues rather than merely matching bounds and average color;
- normal-size overlay is visually indistinguishable;
- no visible mismatch is explained by a generated or approximate asset.
- the responsive audit classification is `responsive-certified` with no missing common, zoom, DPR, text-zoom, material-state, sweep, or breakpoint-boundary case.

Use `closely approximated` when the result is strong but visible differences remain. Use `blocked` when a required asset, viewport fact, capture capability, or hidden source information prevents convergence.
Do not stop because a round count was reached. Continue focused iterations while a correctable mismatch remains and the user authorized convergence. Stop only on strict success or an evidence-backed blocker that cannot be corrected from available source information; never relabel a failed strict run as achieved.

### 7. Verify and report

Run focused lint/type checks and functional checks without submitting live forms or causing external effects.

Report the mode, reference assumptions, exact capture environment, asset ledger, changed files, iteration ledger, global and regional metrics, responsive manifest, all tested viewport/DPR/zoom combinations, breakpoint-boundary coverage, functional checks, remaining mismatches, and honest dual classification. Keep the completed local page open when useful.
