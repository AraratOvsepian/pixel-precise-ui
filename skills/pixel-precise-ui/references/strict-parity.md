# Strict Parity Protocol

Use this protocol when the request requires pixel-precise, pixel-perfect, exact, or indistinguishable reproduction.

## 1. Preflight gates

Do not edit code until these questions have evidence-backed answers.

### Viewport gate

Record:

- source pixel dimensions;
- whether it is a full viewport or crop;
- probable CSS viewport and device-pixel ratio;
- target route and visible state;
- the exact capture viewport and DPR that will be used.

Validate the scale using several plausible control and typography dimensions. Mark the scale `confirmed`, `strongly inferred`, or `uncertain`. An uncertain scale blocks strict completion.

### Asset gate

Create an asset ledger:

| Asset | Required appearance | Candidate | Status | Evidence | Strict impact |
|---|---|---|---|---|---|
| Example logo | Exact mark | `public/logo.svg` | exact | visual/source match | none |

Allowed statuses:

- `exact`: authoritative asset with verified crop and dimensions;
- `derived-deterministically`: lossless crop, mask, or transformation whose visible source pixels are preserved;
- `approximate`: redrawn, generated, substituted, or inferred;
- `missing`: no usable source exists.

In strict mode, a materially visible `approximate` or `missing` asset prevents `achieved`. Do not use image generation to conceal the blocker. A composite screenshot cannot disclose background or texture pixels hidden by foreground content.

### Asset-isolation gate

For every isolated logo, icon, badge, or cutout:

- inspect the candidate's alpha channel, edge pixels, and corner pixels;
- reject an opaque screenshot crop whose backing surface is baked into the asset unless that backing is intentionally part of the authoritative asset;
- do not label a context-contaminated crop `exact`, even when its core pixels are a lossless crop;
- register both the core bounds and a padded context region that crosses every asset boundary;
- require the context gate to pass and inspect its normal-size overlay for seams.

A `0.00000` core score is not evidence of correct integration when the surrounding context was excluded.

### Material-fidelity gate

When the reference visibly shows glass, metal, embossing, translucency, refraction, or another dimensional material, decompose the surface before implementation:

| Layer | Evidence to record |
|---|---|
| transmitted backdrop | visible refraction, blur, magnification, or color shift |
| body/fill | gradient direction, opacity, tint, and texture |
| rim | outer silhouette, bright/dark edge balance, and corner behavior |
| specular highlight | position, falloff, and intensity |
| inset depth | internal shadow/highlight and apparent thickness |
| contact shadow/glow | offset, spread, and interaction with the surrounding surface |

Inspect the implemented computed styles and layer structure. If the reference demonstrates transmitted background or multi-layer depth, a flat alpha fill with one border and no equivalent material layers cannot be accepted merely because its large-region average color is close. Protect each repeated material-bearing control separately rather than allowing a whole form region to dilute the mismatch.

Do not use a screenshot-derived background plate that retains blurred control silhouettes, labels, button gradients, or their foreground shadows/glows behind the live components. Such a plate is background-contaminated and invalid for strict acceptance.

### Typography gate

Verify both declared and computed styles for each text role:

- loaded font file and computed family;
- size, weight, line height, and letter spacing;
- text-transform and smoothing;
- width, wrap points, and baseline.

Do not infer the active font from framework configuration. Global CSS and fallback loading may override it.

### Capture gate

The capture environment must provide:

- exact CSS viewport and DPR;
- fixed browser/version and color environment;
- completed font loading (`document.fonts.ready` when available);
- disabled animation, transition, caret, and dynamic timestamps;
- stable state and no blocking console/build errors.

Take two unchanged screenshots. Use the diff script with threshold zero or an explicitly justified antialias tolerance. Unstable captures must be fixed before reference comparison.

## 2. Region manifest

Create a JSON file for the important visual regions:

```json
{
  "regions": [
    {
      "name": "panel",
      "bounds": [521, 73, 630, 796],
      "protected": true,
      "max_normalized_mean_absolute_difference": 0.02,
      "max_percent_pixels_over_threshold": 5.0
    },
    {
      "name": "logo",
      "bounds": [775, 108, 122, 99],
      "protected": true,
      "max_normalized_mean_absolute_difference": 0.02,
      "context_padding": 16,
      "max_context_normalized_mean_absolute_difference": 0.02
    },
    {
      "name": "email-glass-surface",
      "bounds": [579, 443, 515, 66],
      "protected": true,
      "max_normalized_mean_absolute_difference": 0.02,
      "max_edge_normalized_mean_absolute_difference": 0.02
    }
  ]
}
```

`bounds` are `[x, y, width, height]` in source pixels. `context_padding` is either one positive integer or `[left, top, right, bottom]`; it expands the comparison across an asset boundary. The edge metric compares high-frequency structure and exposes missing rims, bevels, inset highlights, and similar material detail. Choose tolerances for the fixed environment and source type rather than treating the example values as universal. Brand marks, text, and controls normally require tighter gates than photographic texture.

Run strict manifests with `--require-region-gates`. Every protected region must have at least one absolute gate. A baseline-only comparison answers “did it get worse?” but not “is it good enough?” A region with `context_padding` must also define `max_context_normalized_mean_absolute_difference`.

Do not omit a visibly important region merely because it scores poorly.

## 3. Focused iteration contract

Keep a ledger for every accepted or rejected render:

| Round | Target region | Hypothesis | Single change | Before | After | Other regressions | Decision |
|---|---|---|---|---|---|---|---|

Rules:

1. Establish a saved baseline render before a change.
2. Change one subsystem: geometry, typography, one asset, one surface, or one state.
3. Compare candidate versus source and baseline.
4. Reject and revert if the intended region does not improve.
5. Reject and revert if a protected region regresses beyond the manifest or command tolerance.
6. Inspect the overlay at normal size even when metrics pass.
7. Reject any visible rectangular seam, halo discontinuity, baked control ghost, or flattened material cue regardless of a passing core or global score.

Avoid compensating transforms that align one landmark while leaving the underlying font, intrinsic dimensions, or flow incorrect.

## 4. Completion decisions

### Achieved

- capture is stable and correctly registered;
- authoritative/deterministic assets cover all material pixels;
- protected regional gates pass;
- isolated assets pass their padded context gates without visible seams;
- dimensional surfaces pass individual color and edge-detail gates and reproduce the evidenced material stack;
- structural landmarks and wrapping match;
- overlay is visually indistinguishable at normal size.

### Closely approximated

- layout and behavior are strong;
- visible differences remain in assets, typography, material, or antialiasing;
- the user accepted approximation or exactness was not requested.

### Blocked

- required original assets or hidden pixels are unavailable;
- source viewport/DPR cannot be resolved;
- exact capture cannot be produced;
- functional semantic HTML conflicts with a flattened composite in a way that cannot be reconstructed exactly.

Blocked does not mean stop all useful work. Complete deterministic regions when authorized, document the boundary, and request the minimum missing input.
