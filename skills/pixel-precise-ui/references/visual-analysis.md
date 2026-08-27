# Visual Analysis and Reconstruction Worksheet

Create this evidence before implementation. Strict runs must also follow [strict-parity.md](strict-parity.md).

## 1. Source registration

For each reference record:

- file and original dimensions;
- full viewport or crop;
- probable CSS viewport and device-pixel ratio;
- route, responsive breakpoint, and UI state;
- visible fonts and available font files;
- known user overrides;
- confidence (`confirmed`, `strongly inferred`, or `uncertain`);
- unresolved ambiguity and its completion impact.

Do not resize the source to force a match. Validate a probable scale using several control heights, gutters, and text sizes.

## 2. Asset ledger

List every material image, logo, icon, texture, and font before coding.

| Asset | Source bounds | Candidate | Status | Evidence | Implementation | Completion impact |
|---|---|---|---|---|---|---|

Statuses are `exact`, `derived-deterministically`, `approximate`, and `missing`.

- Reuse an exact authoritative asset and verify crop, transparency, intrinsic size, and color.
- For isolated marks, inspect alpha plus corner and edge pixels. Record whether the crop contains surrounding-surface pixels and add a padded context comparison crossing the asset boundary.
- Keep text semantic HTML.
- Use CSS for ordinary geometry and surfaces.
- Use the product icon system or a precise SVG for interface icons.
- Never approximate a brand mark in strict mode.
- Use raster/vector assets for genuinely complex imagery.
- Treat occluded pixels in a flattened composite as unavailable, not inferable facts.
- Reject background candidates that retain baked foreground text, control silhouettes, button gradients, shadows, or glows. Blur does not turn a contaminated screenshot crop into a clean plate.

## 3. Element inventory

Create one row per meaningful design element.

| Field | Required content |
|---|---|
| Element ID | Stable path such as `login.form.password` |
| Source bounds | `x, y, width, height` in registered source pixels |
| Layer | Background, surface, content, overlay, or decoration |
| Role | Layout, text, image, icon, input, button, or ornament |
| Target owner | Component and file |
| Implementation | HTML, CSS, SVG, exact asset, or deterministic crop |
| Typography | Computed family, file, weight, size, line height, spacing |
| Visual tokens | Color, spacing, radius, border, shadow, opacity |
| Responsive behavior | Fixed, fluid, stacked, hidden, or evidence-limited |
| Confidence | Confirmed, strongly inferred, or uncertain |
| Landmark | Edge, center, baseline, dimension, or crop boundary |
| Protected region | Region manifest name covering the element |

Follow with the smallest semantic component tree that preserves behavior.

## 4. Computed-style audit

For the target render, record actual browser-computed values for:

- body and role-specific font family;
- font size, weight, line height, and letter spacing;
- element bounding rectangles;
- box sizing, padding, border, and radius;
- background image sizing and focal position;
- active media queries;
- device-pixel ratio.

Framework declarations do not count as proof. Confirm the computed result after global resets and font loading.

## 5. Token sheet

Extract the smallest sufficient token set:

- canvas, surfaces, primary/secondary text, accents, borders;
- repeated spacing and content gutters;
- radii and border widths;
- shadow, blur, and blend recipes;
- display, body, label, and utility typography;
- control heights and maximum widths;
- reference-supported breakpoints.

Prefer page-scoped variables or existing product tokens. Do not force unique measured values into an artificial scale.

## 6. Protected region manifest

Create a JSON manifest consumed by `scripts/visual_diff.py`:

```json
{
  "regions": [
    {
      "name": "email-glass-surface",
      "bounds": [579, 443, 515, 66],
      "protected": true,
      "max_normalized_mean_absolute_difference": 0.02,
      "max_edge_normalized_mean_absolute_difference": 0.02
    },
    {
      "name": "logo",
      "bounds": [775, 108, 122, 99],
      "protected": true,
      "max_normalized_mean_absolute_difference": 0.01,
      "context_padding": 16,
      "max_context_normalized_mean_absolute_difference": 0.01
    }
  ]
}
```

Use `[x, y, width, height]`. Protect every visually important region and each repeated dimensional control separately. Use `context_padding` for isolated assets and the edge gate for glass, bevels, rims, inset highlights, and other high-frequency material structure. Every protected region in strict mode needs an absolute gate; regression-only regions are insufficient. Configure thresholds for the fixed capture environment; do not weaken them merely to pass the current render.

### Material stack worksheet

For each visibly dimensional surface, record:

| Surface | Transmitted backdrop | Body/fill | Rim | Specular | Inset depth | Contact shadow/glow | Implementation evidence |
|---|---|---|---|---|---|---|---|

Verify the implemented layer stack with computed styles and DOM structure. If the reference visibly transmits or refracts its backdrop, record the actual `backdrop-filter` or equivalent exact-layer mechanism. A flat fill plus one border is a structural mismatch even when the control's bounds and average color are close.

## 7. Comparison order

Fix mismatches in descending impact:

1. Source registration and capture stability
2. Page origin, scale, and centerlines
3. Major component bounds
4. Exact typography and wrapping
5. Asset crop and icon geometry
6. Spacing, borders, radii, and shadows
7. Color, texture, and antialiasing

Do not tune micro-effects while the viewport, font, or container is wrong.

## 8. Iteration ledger

| Round | Target | Hypothesis | Single change | Regional metric before | After | Regressions | Keep/revert |
|---|---|---|---|---|---|---|---|

Use the previous accepted render as `--baseline`. A lower global score does not justify a protected-region regression.

## 9. Visual-difference outputs

```bash
python3 scripts/visual_diff.py source.png rendered.png \
  --regions regions.json \
  --baseline previous.png \
  --fail-on-regression \
  --require-region-gates \
  --require-dimensions \
  --output-dir visual-check
```

The script writes overlay, heatmap, global metrics, regional metrics, context overlays, edge-detail metrics, regression deltas, and gate violations. It never resizes either input.

## 10. Responsive inference

A reference proves only its registered viewport. Match that state first. Check smaller and larger viewports for robustness, but do not score them as parity targets without their own reference images.

## 11. Completion rubric

- `achieved`: deterministic inputs, stable capture, all protected gates pass, structural landmarks/wrapping match, and normal-size overlay is visually indistinguishable.
- `closely approximated`: strong result with explained visible differences and no exact-match claim.
- `blocked`: missing authoritative asset, hidden source pixels, unresolved viewport, or unavailable deterministic capture materially prevents convergence.

State the result honestly and list every remaining visible difference.
