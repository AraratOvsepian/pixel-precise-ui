# Visual Analysis and Reconstruction Worksheet

Use this worksheet before implementation and as the comparison ledger during iteration.

## 1. Source registration

For each image record:

- File and original dimensions
- Full viewport or crop
- Probable CSS viewport and device-pixel ratio
- UI state and route
- Visible fonts and available font files
- Known user overrides
- Confidence and unresolved ambiguity

Do not rescale the source until its relationship to the CSS viewport is understood. A two-times-density image may represent half as many CSS pixels.

## 2. Element inventory

Create one row per meaningful design element.

| Field | Required content |
|---|---|
| Element ID | Stable path such as `login.form.password` |
| Source bounds | Approximate `x, y, width, height` in source pixels |
| Layer | Background, surface, content, overlay, or decoration |
| Role | Layout, text, image, icon, input, button, or ornament |
| Target component | Component and file that should own it |
| Implementation | Semantic HTML, CSS, SVG, existing asset, raster asset, or icon library |
| Typography | Family, weight, size, line height, and letter spacing |
| Visual tokens | Color, spacing, radius, border, shadow, and opacity |
| Responsive behavior | Fixed, fluid, stacked, hidden, or breakpoint-dependent |
| Confidence | Confirmed, strongly inferred, or uncertain |
| Verification landmark | Edge, center, baseline, dimension, or crop boundary |

Follow the inventory with a semantic component tree. Do not mirror visual wrappers mechanically when fewer semantic containers reproduce the same structure.

## 3. Asset decision rules

- Existing exact asset: reuse it and confirm its crop, transparency, and intrinsic size.
- Text: semantic HTML; never rasterize it for visual matching.
- Surface or geometry: CSS, unless the shape is materially too complex.
- Interface icon: existing product icon system first, then a precise inline SVG.
- Logo or brand mark: existing authoritative asset; do not approximate it with a generic icon.
- Photo, texture, or complex illustration: raster or vector asset with explicit `object-fit` and focal position.
- Missing source asset: search the repository before generating a replacement. Generate or edit only with user authorization.

## 4. Token sheet

Extract the smallest sufficient set:

- Canvas, surface, primary text, secondary text, accent, status, and border colors
- Spacing increments and content gutters
- Radius and border-width steps
- Shadow and blur recipes
- Display, body, label, and utility typography
- Control heights and maximum content widths
- Reference breakpoints

Prefer scoped CSS variables or the project's existing token mechanism. Avoid one-off values when the source clearly repeats a value; avoid forcing genuinely unique source measurements into an artificial scale.

## 5. Comparison order

Fix mismatches in descending visual impact:

1. Image dimensions and viewport model
2. Page origin, overall scale, and primary centerline
3. Major component bounds
4. Text family, size, weight, wrapping, and baseline
5. Asset crop and icon geometry
6. Spacing, borders, radii, and shadows
7. Subtle color and antialiasing differences

Do not tune micro-shadows while the container is misplaced.

## 6. Iteration ledger

For each render record:

| Round | Largest mismatch | Hypothesis | Change | Metric before | Metric after | Keep/revert |
|---|---|---|---|---|---|---|

Use the diff metric as evidence, not as the only objective. A lower global difference can still hide a more noticeable typography or alignment regression.

## 7. Visual-difference outputs

Run:

```bash
python3 scripts/visual_diff.py source.png rendered.png --output-dir visual-check
```

The script writes:

- `overlay.png`: equal blend of source and render
- `difference.png`: amplified red heatmap of differences
- `metrics.json`: dimensions, mean absolute difference, threshold percentage, and difference bounds

If Pillow is unavailable, use the environment's bundled Python or install Pillow only with permission. Structural measurement and browser overlays remain valid fallbacks.

## 8. Responsive inference

A screenshot proves only its visible viewport. Preserve existing responsive behavior unless multiple references or explicit instructions prove another design. After matching the source, check one smaller and one larger viewport for clipping, overflow, unreadable controls, and broken hierarchy.

## 9. Completion rubric

- `achieved`: comparison evidence shows no meaningful visible mismatch at the target viewport.
- `closely approximated`: remaining differences are minor and explained by fonts, antialiasing, missing assets, or source ambiguity.
- `blocked`: a required asset, viewport fact, environment, or permission is missing and materially prevents further convergence.

State the result honestly and list the remaining visible differences.
