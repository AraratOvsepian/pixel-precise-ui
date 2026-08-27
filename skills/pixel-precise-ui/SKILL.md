---
name: pixel-precise-ui
description: Recreate interfaces from supplied reference images in an existing frontend codebase, using strict asset, viewport, regional-diff, and regression gates for exact-match requests. Use for screenshot-to-code and explicit visual matching; do not use for ordinary UI work without a visual reference.
license: MIT
---

# Pixel Precise UI

Reconstruct supplied interface references with maintainable code in the project's existing frontend stack. Preserve user intent, product behavior, semantics, accessibility, routes, and data flow.

## Choose the mode before editing

- **Strict parity** is mandatory when the user says `pixel precise`, `pixel perfect`, `exact`, or `match exactly`. Read [references/strict-parity.md](references/strict-parity.md) and follow every gate.
- **Visual approximation** applies only when the user asks for direction, inspiration, a close recreation, or explicitly accepts approximation. Approximate or generated assets are allowed, but the result cannot be reported as exact.

Never silently downgrade strict parity. If a strict run lacks an authoritative font, logo, image, viewport fact, or deterministic capture surface, exhaust repository evidence and then report the specific blocker before substituting anything.

## Non-negotiable rules

- Read applicable repository instructions and preserve user-owned changes.
- Inspect every reference at original resolution and register its probable CSS viewport, device-pixel ratio, route, and UI state.
- Never use the complete reference screenshot as a page background, overlay, canvas, or single full-page image.
- Keep text semantic HTML. Use CSS for ordinary geometry and surfaces, an exact product asset or precise SVG for brand/interface marks, and raster assets only for genuinely image-based content.
- In strict mode, do not generate, redraw, or approximate a missing logo, font, photograph, background plate, or complex texture. A flattened screenshot cannot reveal pixels hidden behind an opaque or translucent foreground; treat missing hidden pixels as a source limitation.
- Do not submit live forms, mutate production data, or trigger external side effects for visual state.
- Do not claim parity from geometry alone or from one global image score.

## Required workflow

### 1. Preflight the target

Inspect the route, component tree, styles, resets, computed fonts, available font files, product assets, local command, and running server. Read [references/visual-analysis.md](references/visual-analysis.md) and create:

- source registration with confidence labels;
- asset ledger with `exact`, `derived-deterministically`, `approximate`, or `missing` status;
- element inventory and semantic component tree;
- token sheet and computed-style audit;
- verification landmarks;
- protected comparison regions in a JSON manifest.

In strict mode, unresolved `approximate` or `missing` assets that materially affect visible pixels block an `achieved` result. Ask for the original layered design, clean image plate, exact SVG/logo, or font file when needed.

### 2. Establish a deterministic capture

Render at the reference's exact CSS viewport and device-pixel ratio. Do not score a desktop source against a responsive mobile render.

Before capture:

- wait for the route and fonts to finish loading;
- disable motion, caret blinking, and time-dependent effects;
- reproduce only safe local state;
- inspect console/compilation errors;
- capture twice without changes and verify stability.

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

### 4. Compare by protected region

Run structural measurement, overlay inspection, and the deterministic diff script. Example:

```bash
python3 scripts/visual_diff.py source.png render.png \
  --regions regions.json \
  --baseline previous-render.png \
  --fail-on-regression \
  --require-dimensions \
  --output-dir visual-check
```

Change one subsystem per round. Record the hypothesis and relevant regional metrics before editing. Accept a round only when the intended region improves and no protected region exceeds its configured gate or regression tolerance. A better global score never excuses a worse logo, typography, form, or other protected region.

### 5. Stop according to evidence

Strict parity is `achieved` only when all of the following hold in the fixed capture environment:

- dimensions and responsive state match;
- stable captures are reproducible;
- major edges and baselines are within the registered tolerance, normally one CSS pixel;
- text family, wrapping, and glyph positioning match;
- every protected region passes its configured visual gates;
- normal-size overlay is visually indistinguishable;
- no visible mismatch is explained by a generated or approximate asset.

Use `closely approximated` when the result is strong but visible differences remain. Use `blocked` when a required asset, viewport fact, capture capability, or hidden source information prevents convergence. Eight focused rounds is the normal maximum; ask before extending when correctable mismatches remain.

### 6. Verify and report

After matching the reference viewport, test a smaller and larger viewport for overflow and accessibility without treating them as parity references unless images were supplied for those states. Run focused lint/type checks.

Report the mode, reference assumptions, exact capture environment, asset ledger, changed files, iteration ledger, global and regional metrics, functional checks, remaining mismatches, and honest classification. Keep the completed local page open when useful.
