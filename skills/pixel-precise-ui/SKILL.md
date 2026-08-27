---
name: pixel-precise-ui
description: Recreate an interface from one or more supplied reference images in an existing frontend codebase, then render, compare, and iterate toward pixel-accurate visual parity. Use for screenshot-to-code and explicit visual-matching requests; do not use for ordinary UI work without a visual reference.
license: MIT
---

# Pixel Precise UI

Reconstruct supplied interface references with maintainable code in the project's existing frontend stack. Treat explicit user instructions as authoritative when they differ from the image.

## Non-negotiable rules

- Read applicable repository instructions and preserve user-owned changes.
- Inspect every source image at original resolution before editing. If a required image is unavailable, ask the user to attach it again.
- Preserve functional behavior, data flow, semantics, accessibility, and existing routes.
- Do not introduce a new framework when the current stack can implement the design.
- Never fake parity by using the complete reference screenshot as the page background, overlay, canvas, or single full-page image.
- Keep text as semantic HTML. Use CSS for ordinary geometry and surfaces, the existing icon system or precise SVG for icons, existing brand files for logos, and raster assets only for genuinely image-based content.
- Do not submit live forms, mutate production data, or trigger external side effects merely to reach a visual state.
- Do not claim pixel parity without comparison evidence.

## Workflow

### 1. Establish the target

Inspect the target route, components, styles, tokens, fonts, assets, local development command, and current running server state. Record each reference image's pixel dimensions and infer whether it represents a full viewport, crop, device-density export, responsive breakpoint, or UI state.

When several images are supplied, map each image to its viewport and state. Do not merge contradictory states into one layout.

### 2. Produce a visual specification

Before coding, read [references/visual-analysis.md](references/visual-analysis.md) and create its element inventory, component tree, token sheet, and verification landmarks. Plan each meaningful source element separately, but choose its correct code-native representation rather than turning every element into an image file.

Resolve uncertainty explicitly as `confirmed`, `strongly inferred`, or `uncertain`. Do not invent hidden behavior from a single static frame.

### 3. Implement outside-in

Work in this order unless the source makes another order materially better:

1. Viewport and canvas
2. Primary geometry and alignment
3. Major surfaces
4. Typography and wrapping
5. Images, logos, and icons
6. Controls and states
7. Borders, shadows, and micro-spacing
8. Responsive behavior

Restyle existing functional components instead of replacing them with static replicas. Scope reference-specific tokens to the relevant page or feature unless the image clearly defines a product-wide system.

### 4. Render the real page

Start or reuse the local development server. Open the exact target route in the available browser-testing surface at the reference viewport. Reproduce only safe local state, reload after source changes when needed, capture a screenshot, and inspect compilation and browser errors.

Match source and rendered screenshots at the same CSS viewport and scale. If the source scale is unknown, document the assumption and validate it using multiple stable landmarks.

### 5. Compare and iterate

Use all applicable comparison modes:

- Structural: measure container edges, centers, baselines, control dimensions, gaps, crop boundaries, and alignment axes.
- Overlay: compare the source and render at 50% opacity.
- Difference: run `scripts/visual_diff.py SOURCE RENDER --output-dir DIR` when Pillow is available.

Never silently resize one image to match the other. Dimension mismatch is a primary error.

Run focused rounds rather than changing unrelated layers together:

1. Geometry
2. Typography
3. Color and surfaces
4. Assets and micro-spacing
5. Responsive and interactive states

For each round, identify the largest remaining mismatch, state a cause hypothesis, make the smallest relevant change, render again, and record whether the evidence improved. Remove failed experiments instead of accumulating overrides.

If the obvious approach does not converge, deliberately test a relevant alternative such as grid versus flex, fixed versus intrinsic sizing, `cover` versus `contain`, exact-font loading versus substitution, CSS versus SVG, or pseudo-element versus markup.

### 6. Stop honestly

Stop when major boundaries and baselines are within roughly two CSS pixels, the normal-size appearance is visually indistinguishable, and remaining differences are attributable to unavailable fonts/assets, source ambiguity, or rendering antialiasing.

Also stop when two consecutive well-targeted iterations do not produce meaningful improvement. Eight comparison rounds is the normal maximum; ask before extending work when correctable differences remain.

### 7. Verify and report

After matching the reference viewport, test one reasonable smaller and one larger viewport. Check overflow, clipping, keyboard focus, labels, contrast, and reduced-motion behavior. Run focused lint or type checks without expanding into unrelated repository repairs.

Report:

1. Reference images and assumptions
2. Target route and tested viewports
3. Files and assets changed
4. Iteration rounds and comparison metrics
5. Functional and accessibility checks
6. Remaining differences and causes
7. Whether parity was achieved, closely approximated, or blocked

Keep the completed local page open when useful for the user's next review.
