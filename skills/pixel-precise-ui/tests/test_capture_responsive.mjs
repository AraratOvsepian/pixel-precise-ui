import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assessDeviceEmulation,
  boundaryExtractionErrors,
  expandCommonMatrix,
  extractBreakpoints,
  extractMediaBoundaries,
  fingerprintCodeTree,
  manifestAttestation,
  normalizePlan,
  verifyManifestAttestation,
} from "../scripts/capture_responsive.mjs";

const MATRIX = JSON.parse(
  await readFile(
    new URL("../scripts/common-responsive-matrix-2026-07-v1.json", import.meta.url),
    "utf8",
  ),
);

async function temporaryDirectory() {
  return mkdtemp(path.join(os.tmpdir(), "pixel-precise-ui-capture-test-"));
}

test("shared common matrix expands deterministically without drift", () => {
  const expanded = expandCommonMatrix(MATRIX);
  assert.equal(expanded.length, 70);
  assert.ok(
    expanded.some(
      (viewport) =>
        viewport.class === "desktop-zoom" &&
        viewport.width === 1920 &&
        viewport.zoom_percent === 175,
    ),
  );
  assert.ok(
    expanded.some(
      (viewport) =>
        viewport.class === "accessibility-text-zoom" &&
        viewport.width === 390 &&
        viewport.text_zoom_percent === 200,
    ),
  );
  assert.ok(
    expanded.some(
      (viewport) => viewport.class === "dpr-smoke" && viewport.base_dpr === 1.5,
    ),
  );
  assert.ok(
    expanded
      .filter((viewport) => viewport.class.startsWith("mobile-"))
      .every((viewport) => viewport.base_dpr >= 2),
  );
  assert.ok(
    expanded
      .filter((viewport) => viewport.class.startsWith("tablet-"))
      .every((viewport) => viewport.base_dpr === 2),
  );
  const portraitDpr = new Map(
    expanded
      .filter((viewport) => viewport.class === "mobile-portrait")
      .map((viewport) => [`${viewport.width}x${viewport.height}`, viewport.base_dpr]),
  );
  for (const landscape of expanded.filter(
    (viewport) => viewport.class === "mobile-landscape",
  )) {
    assert.equal(
      landscape.base_dpr,
      portraitDpr.get(`${landscape.height}x${landscape.width}`),
    );
  }
});

test("plan normalizes actual DPR and hashes a complete primary state", () => {
  const plan = normalizePlan(
    {
      schema_version: "2.0",
      base_url: "http://127.0.0.1:3000",
      route: "/login",
      reference_pixel_sha256: "a".repeat(64),
      states: [{ id: "default", actions: [] }],
      required_elements: [{ name: "main", selector: "main" }],
      viewports: [],
    },
    { commonMatrix: MATRIX },
  );

  assert.equal(plan.viewports.length, 70);
  assert.equal(plan.states[0].primary, true);
  assert.equal(plan.states[0].full_matrix, true);
  assert.match(plan.states[0].action_hash, /^[a-f0-9]{64}$/);
  const zoomed = plan.viewports.find(
    (viewport) =>
      viewport.class === "desktop-zoom" && viewport.zoom_percent === 125,
  );
  assert.equal(zoomed.base_dpr, 1);
  assert.equal(zoomed.dpr, 1.25);
  assert.equal(zoomed.effective_css_width, Math.round((zoomed.width * 100) / 125));
});

test("plan carries color scheme and assigns explicit mobile/tablet device semantics", () => {
  const plan = normalizePlan({
    schema_version: "2.0",
    base_url: "http://127.0.0.1:3000",
    route: "/login",
    color_scheme: "dark",
    reference_pixel_sha256: "a".repeat(64),
    states: [{ id: "default" }],
    required_elements: [{ name: "main", selector: "main" }],
    include_common_matrix: false,
    viewports: [
      { id: "phone", class: "mobile-portrait", width: 390, height: 844 },
      { id: "phone-text", class: "accessibility-text-zoom", width: 390, height: 844, text_zoom_percent: 200 },
      { id: "tablet", class: "tablet-landscape", width: 1024, height: 768 },
      { id: "desktop", class: "desktop", width: 1440, height: 900 },
    ],
  });

  assert.equal(plan.color_scheme, "dark");
  assert.deepEqual(
    plan.viewports.map(({ device_class, is_mobile, has_touch }) => ({
      device_class,
      is_mobile,
      has_touch,
    })),
    [
      { device_class: "mobile", is_mobile: true, has_touch: true },
      { device_class: "mobile", is_mobile: true, has_touch: true },
      { device_class: "tablet", is_mobile: false, has_touch: true },
      { device_class: "desktop", is_mobile: false, has_touch: false },
    ],
  );
  assert.throws(
    () => normalizePlan({
      schema_version: "2.0",
      base_url: "http://127.0.0.1:3000",
      route: "/login",
      color_scheme: "sepia",
      reference_pixel_sha256: "a".repeat(64),
      states: [{ id: "default" }],
      required_elements: [{ name: "main", selector: "main" }],
      include_common_matrix: false,
      viewports: [{ class: "desktop", width: 1440, height: 900 }],
    }),
    /color_scheme must be light, dark, or no-preference/,
  );
});

test("mobile evidence is non-certifying unless the browser proves mobile semantics", () => {
  const viewport = {
    device_class: "mobile",
    is_mobile: true,
    has_touch: true,
    width: 390,
    height: 844,
    effective_css_width: 390,
    effective_css_height: 844,
  };
  const probe = {
    inner_width: 390,
    inner_height: 844,
    visual_viewport: { width: 390, height: 844, scale: 1 },
    device_environment: {
      navigator: {
        user_agent: "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/140.0.0.0 Mobile Safari/537.36",
        user_agent_data: { mobile: true },
        max_touch_points: 1,
      },
      touch: {
        touch_event_supported: true,
        pointer_coarse: true,
        any_pointer_coarse: true,
        hover_none: true,
      },
      screen: { width: 390, height: 844 },
      safe_area_insets: { css_env_supported: true, top_px: 0, right_px: 0, bottom_px: 0, left_px: 0 },
    },
  };
  assert.deepEqual(assessDeviceEmulation(probe, viewport).errors, []);

  const narrowDesktopProbe = structuredClone(probe);
  narrowDesktopProbe.device_environment.navigator.user_agent =
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36";
  narrowDesktopProbe.device_environment.navigator.user_agent_data.mobile = false;
  narrowDesktopProbe.device_environment.navigator.max_touch_points = 0;
  narrowDesktopProbe.device_environment.touch.touch_event_supported = false;
  narrowDesktopProbe.device_environment.touch.pointer_coarse = false;
  narrowDesktopProbe.device_environment.touch.any_pointer_coarse = false;
  narrowDesktopProbe.device_environment.touch.hover_none = false;
  const failures = assessDeviceEmulation(narrowDesktopProbe, viewport).errors.map(
    (error) => error.gate,
  );
  assert.ok(failures.includes("touch-capability"));
  assert.ok(failures.includes("coarse-pointer"));
  assert.ok(failures.includes("mobile-browser-identity"));
});

test("Firefox mobile plans are rejected instead of silently using desktop emulation", () => {
  assert.throws(
    () => normalizePlan({
      schema_version: "2.0",
      base_url: "http://127.0.0.1:3000",
      route: "/login",
      browser: "firefox",
      reference_pixel_sha256: "a".repeat(64),
      states: [{ id: "default" }],
      required_elements: [{ name: "main", selector: "main" }],
      include_common_matrix: false,
      viewports: [{ class: "mobile-portrait", width: 390, height: 844 }],
    }),
    /Firefox cannot provide isMobile emulation/,
  );
});

test("media query parser discovers width and height boundaries in legacy and range syntax", () => {
  const query =
    "(min-width: 48rem) and (height < 900px), (600px <= inline-size) and (max-height: 50em)";
  const boundaries = extractMediaBoundaries(query);
  assert.deepEqual(
    boundaries.map(({ dimension, boundary_value }) => [dimension, boundary_value]),
    [
      ["width", 600],
      ["width", 768],
      ["height", 800],
      ["height", 900],
    ],
  );
  assert.deepEqual(
    extractBreakpoints(query).map((item) => item.boundary_value),
    [600, 768],
  );
  assert.deepEqual(
    extractMediaBoundaries("(inline-size: 640px)").map((item) => item.boundary_value),
    [640],
  );
  assert.equal(
    boundaryExtractionErrors("(min-width: calc(40rem + 1px))").at(0)?.gate,
    "unresolved-responsive-boundary",
  );
  assert.equal(
    boundaryExtractionErrors("(inline-size > var(--card-breakpoint))").at(0)?.gate,
    "unresolved-responsive-boundary",
  );
});

test("recursive-tree-v2 changes for source edits but ignores evidence and dependencies", async () => {
  const root = await temporaryDirectory();
  try {
    await writeFile(path.join(root, "app.js"), "export const value = 1;\n");
    await mkdir(path.join(root, "src"));
    await writeFile(path.join(root, "src", "style.css"), "body { color: red; }\n");
    await mkdir(path.join(root, "src", "responsive-layout"));
    await writeFile(
      path.join(root, "src", "responsive-layout", "page.js"),
      "export const layout = 'one';\n",
    );
    await mkdir(path.join(root, "node_modules"));
    await writeFile(path.join(root, "node_modules", "ignored.js"), "one\n");
    await mkdir(path.join(root, "captures"));
    await writeFile(path.join(root, "captures", "ignored.json"), "{}\n");
    await mkdir(path.join(root, "responsive-check"));
    await writeFile(path.join(root, "responsive-check", "metrics.json"), "{}\n");
    await symlink("app.js", path.join(root, "entry.js"));

    const first = await fingerprintCodeTree(root);
    assert.equal(first.algorithm, "recursive-tree-v2");
    assert.equal(first.entry_count, 4);

    await writeFile(path.join(root, "node_modules", "ignored.js"), "two\n");
    await writeFile(path.join(root, "captures", "ignored.json"), '{"changed":true}\n');
    const ignoredChanges = await fingerprintCodeTree(root);
    assert.equal(ignoredChanges.sha256, first.sha256);

    await writeFile(path.join(root, "src", "style.css"), "body { color: blue; }\n");
    const sourceChange = await fingerprintCodeTree(root);
    assert.notEqual(sourceChange.sha256, first.sha256);

    await writeFile(
      path.join(root, "src", "responsive-layout", "page.js"),
      "export const layout = 'two';\n",
    );
    const responsiveSourceChange = await fingerprintCodeTree(root);
    assert.notEqual(responsiveSourceChange.sha256, sourceChange.sha256);

    await chmod(path.join(root, "app.js"), 0o755);
    const modeChange = await fingerprintCodeTree(root);
    assert.notEqual(modeChange.sha256, responsiveSourceChange.sha256);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("collector attestation detects hand edits", () => {
  const scriptHash = "f".repeat(64);
  const payload = {
    schema_version: "2.0",
    collector: { script_sha256: scriptHash, harness_collected: true },
    run: { run_id: "one" },
    cases: [],
  };
  const manifest = {
    ...payload,
    collector_attestation: manifestAttestation(payload, scriptHash),
  };
  assert.equal(verifyManifestAttestation(manifest), true);
  manifest.run.run_id = "hand-edited";
  assert.equal(verifyManifestAttestation(manifest), false);
});

test("non-local targets require explicit authorization", () => {
  assert.throws(
    () =>
      normalizePlan({
        schema_version: "2.0",
        base_url: "https://example.com",
        route: "/",
        reference_pixel_sha256: "a".repeat(64),
        states: [{ id: "default" }],
        viewports: [{ width: 320, height: 568 }],
        required_elements: [{ name: "main", selector: "main" }],
        include_common_matrix: false,
      }),
    /Refusing non-local base URL/,
  );
});
