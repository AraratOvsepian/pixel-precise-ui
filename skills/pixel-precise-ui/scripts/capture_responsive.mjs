#!/usr/bin/env node
/**
 * Browser-owned responsive evidence collection for strict pixel-parity runs.
 *
 * Playwright is deliberately resolved from --code-root. The skill does not
 * carry or silently download its own browser runtime.
 */

import { createHash, randomUUID } from "node:crypto";
import {
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  readlink,
  rename,
  stat,
  writeFile,
} from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

export const SCHEMA_VERSION = "2.0";
export const HARNESS_NAME = "pixel-precise-ui-capture";
export const HARNESS_VERSION = "2.0";
export const PROFILE_NAME = "common-2026-07-v1";
export const TREE_HASH_ALGORITHM = "recursive-tree-v2";

const SCRIPT_PATH = fileURLToPath(import.meta.url);
const MATRIX_PATH = path.join(
  path.dirname(SCRIPT_PATH),
  "common-responsive-matrix-2026-07-v1.json",
);
const TREE_MAGIC = Buffer.from("pixel-precise-ui:recursive-tree-v2\0", "utf8");
const ATTESTATION_MAGIC = "pixel-precise-ui:capture-attestation-v2\0";
const EXCLUDED_TREE_COMPONENTS = new Set([
  ".git",
  "node_modules",
  "build",
  "dist",
  "captures",
  "output",
  "responsive-check",
  "visual-check",
  "completion-check",
  "coverage",
  ".next",
  ".cache",
  "__pycache__",
  "venv",
  ".venv",
]);
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const ACTION_TYPES = new Set([
  "click",
  "fill",
  "check",
  "uncheck",
  "select_option",
  "press",
  "hover",
  "focus",
  "wait_for",
]);

function sha256Bytes(value) {
  return createHash("sha256").update(value).digest();
}

export function sha256Hex(value) {
  return sha256Bytes(value).toString("hex");
}

function canonicalize(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

export function stableStringify(value) {
  return JSON.stringify(canonicalize(value));
}

function nonEmptyString(value, label) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value.trim();
}

function positiveInteger(value, label) {
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${label} must be a positive integer`);
  }
  return value;
}

function finiteNumber(value, label, minimum, maximum) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be numeric`);
  }
  if (value < minimum || value > maximum) {
    throw new Error(`${label} must be between ${minimum} and ${maximum}`);
  }
  return value;
}

function isExcludedTreePath(relativePath) {
  return relativePath
    .split("/")
    .some((component) => EXCLUDED_TREE_COMPONENTS.has(component));
}

/**
 * Hash framing, reproduced by responsive_audit.py:
 *
 * magic; then, for each UTF-8 byte-sorted NFC POSIX path:
 * mode + NUL + decimal(path byte length) + ':' + path + NUL + raw content hash.
 * Regular-file content hashes raw bytes. Symlink content hashes raw target bytes.
 */
export async function fingerprintCodeTree(codeRoot) {
  const root = path.resolve(codeRoot);
  const rootStat = await stat(root);
  if (!rootStat.isDirectory()) {
    throw new Error(`--code-root is not a directory: ${root}`);
  }
  const entries = [];

  async function walk(directory, relativeDirectory) {
    const children = await readdir(directory, { withFileTypes: true });
    children.sort((left, right) =>
      Buffer.from(left.name.normalize("NFC"), "utf8").compare(
        Buffer.from(right.name.normalize("NFC"), "utf8"),
      ),
    );
    const normalizedNames = new Set();
    for (const child of children) {
      const normalizedName = child.name.normalize("NFC");
      if (normalizedNames.has(normalizedName)) {
        throw new Error(
          `Tree contains names that collide after NFC normalization: ${directory}`,
        );
      }
      normalizedNames.add(normalizedName);
      const relative = relativeDirectory
        ? `${relativeDirectory}/${normalizedName}`
        : normalizedName;
      if (isExcludedTreePath(relative)) {
        continue;
      }
      const absolute = path.join(directory, child.name);
      const metadata = await lstat(absolute);
      if (metadata.isDirectory()) {
        await walk(absolute, relative);
      } else if (metadata.isFile()) {
        const bytes = await readFile(absolute);
        entries.push({
          pathBytes: Buffer.from(relative, "utf8"),
          mode: metadata.mode & 0o100 ? "100755" : "100644",
          contentHash: sha256Bytes(bytes),
        });
      } else if (metadata.isSymbolicLink()) {
        const target = await readlink(absolute, { encoding: "buffer" });
        entries.push({
          pathBytes: Buffer.from(relative, "utf8"),
          mode: "120000",
          contentHash: sha256Bytes(target),
        });
      } else {
        throw new Error(`Unsupported special file in code tree: ${absolute}`);
      }
    }
  }

  await walk(root, "");
  entries.sort((left, right) => left.pathBytes.compare(right.pathBytes));
  const digest = createHash("sha256");
  digest.update(TREE_MAGIC);
  for (const entry of entries) {
    digest.update(Buffer.from(`${entry.mode}\0${entry.pathBytes.length}:`, "ascii"));
    digest.update(entry.pathBytes);
    digest.update(Buffer.from([0]));
    digest.update(entry.contentHash);
  }
  return {
    algorithm: TREE_HASH_ALGORITHM,
    sha256: digest.digest("hex"),
    entry_count: entries.length,
  };
}

function normalizeAction(raw, stateId, index) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error(`states.${stateId}.actions[${index}] must be an object`);
  }
  const type = nonEmptyString(raw.type, `states.${stateId}.actions[${index}].type`);
  if (!ACTION_TYPES.has(type)) {
    throw new Error(`Unsupported state action '${type}' in state '${stateId}'`);
  }
  const selector = nonEmptyString(
    raw.selector,
    `states.${stateId}.actions[${index}].selector`,
  );
  const action = { type, selector };
  if (["fill", "press"].includes(type)) {
    action.value = nonEmptyString(
      raw.value,
      `states.${stateId}.actions[${index}].value`,
    );
  }
  if (type === "select_option") {
    if (
      !(
        typeof raw.value === "string" ||
        (Array.isArray(raw.value) && raw.value.every((value) => typeof value === "string"))
      )
    ) {
      throw new Error(
        `states.${stateId}.actions[${index}].value must be a string or string array`,
      );
    }
    action.value = raw.value;
  }
  if (raw.timeout_ms !== undefined) {
    action.timeout_ms = positiveInteger(
      raw.timeout_ms,
      `states.${stateId}.actions[${index}].timeout_ms`,
    );
  }
  return action;
}

function normalizeStorage(raw, label) {
  if (raw === undefined) {
    return {};
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error(`${label} must be an object`);
  }
  const result = {};
  for (const [key, value] of Object.entries(raw)) {
    nonEmptyString(key, `${label} key`);
    if (!["string", "number", "boolean"].includes(typeof value) && value !== null) {
      throw new Error(`${label}.${key} must be a scalar JSON value`);
    }
    result[key] = value === null ? "null" : String(value);
  }
  return result;
}

function normalizeState(raw, index) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error(`states[${index}] must be an object`);
  }
  const id = nonEmptyString(raw.id, `states[${index}].id`);
  const actions = raw.actions ?? [];
  if (!Array.isArray(actions)) {
    throw new Error(`states.${id}.actions must be an array`);
  }
  const normalizedActions = actions.map((action, actionIndex) =>
    normalizeAction(action, id, actionIndex),
  );
  const localStorageValues = normalizeStorage(raw.local_storage, `states.${id}.local_storage`);
  const sessionStorageValues = normalizeStorage(
    raw.session_storage,
    `states.${id}.session_storage`,
  );
  return {
    id,
    material: raw.material ?? true,
    primary: raw.primary ?? index === 0,
    full_matrix: raw.full_matrix ?? index === 0,
    action_hash: sha256Hex(
      Buffer.from(
        stableStringify({
          local_storage: localStorageValues,
          session_storage: sessionStorageValues,
          actions: normalizedActions,
        }),
        "utf8",
      ),
    ),
    local_storage: localStorageValues,
    session_storage: sessionStorageValues,
    actions: normalizedActions,
  };
}

function stateEvidence(states) {
  return states.map(({ id, material, primary, full_matrix, action_hash }) => ({
    id,
    material,
    primary,
    full_matrix,
    action_hash,
  }));
}

function normalizeViewport(raw, index) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error(`viewports[${index}] must be an object`);
  }
  const width = positiveInteger(raw.width, `viewports[${index}].width`);
  const height = positiveInteger(raw.height, `viewports[${index}].height`);
  const baseDpr = finiteNumber(
    raw.base_dpr ?? raw.dpr ?? 1,
    `viewports[${index}].base_dpr`,
    0.5,
    4,
  );
  const zoom = finiteNumber(
    raw.zoom_percent ?? 100,
    `viewports[${index}].zoom_percent`,
    50,
    400,
  );
  const viewportClass = nonEmptyString(
    raw.class ?? "custom",
    `viewports[${index}].class`,
  );
  const accessibilityViewport = viewportClass === "accessibility-text-zoom";
  const mobile =
    viewportClass.startsWith("mobile-") ||
    (accessibilityViewport && width <= 480);
  const tablet =
    viewportClass.startsWith("tablet-") ||
    (accessibilityViewport && width > 480 && width <= 1024);
  return {
    id: nonEmptyString(raw.id ?? `viewport-${index + 1}`, `viewports[${index}].id`),
    class: viewportClass,
    width,
    height,
    base_dpr: baseDpr,
    dpr: baseDpr * (zoom / 100),
    zoom_percent: zoom,
    text_zoom_percent: finiteNumber(
      raw.text_zoom_percent ?? raw.text_zoom ?? 100,
      `viewports[${index}].text_zoom_percent`,
      100,
      400,
    ),
    effective_css_width: Math.round((width * 100) / zoom),
    effective_css_height: Math.round((height * 100) / zoom),
    effective_device_scale_factor: baseDpr * (zoom / 100),
    device_class: mobile ? "mobile" : tablet ? "tablet" : "desktop",
    is_mobile: mobile,
    has_touch: mobile || tablet,
  };
}

function normalizeRequiredElement(raw, index) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error(`required_elements[${index}] must be an object`);
  }
  const result = {
    name: nonEmptyString(raw.name, `required_elements[${index}].name`),
    selector: nonEmptyString(raw.selector, `required_elements[${index}].selector`),
    states: raw.states,
    must_intersect_viewport: raw.must_intersect_viewport ?? true,
    must_fit_horizontally: raw.must_fit_horizontally ?? true,
    must_fit_vertically: raw.must_fit_vertically ?? false,
    disallow_overlap_with: [],
  };
  if (raw.disallow_overlap_with !== undefined) {
    if (
      !Array.isArray(raw.disallow_overlap_with) ||
      !raw.disallow_overlap_with.every((value) => typeof value === "string" && value)
    ) {
      throw new Error(`required_elements[${index}].disallow_overlap_with must be strings`);
    }
    result.disallow_overlap_with = [...raw.disallow_overlap_with].sort();
  }
  return result;
}

export function expandCommonMatrix(payload) {
  if (
    !payload ||
    payload.schema_version !== "1.0" ||
    payload.profile !== PROFILE_NAME ||
    !Array.isArray(payload.viewports) ||
    !Array.isArray(payload.groups)
  ) {
    throw new Error(`Shared responsive matrix must describe '${PROFILE_NAME}'`);
  }
  const expanded = payload.viewports.map((viewport) => ({
    ...viewport,
    zoom_percent: viewport.zoom_percent ?? 100,
    text_zoom_percent: viewport.text_zoom_percent ?? 100,
    base_dpr: viewport.base_dpr ?? 1,
  }));
  for (const group of payload.groups) {
    if (!Array.isArray(group.sizes)) throw new Error("Matrix group sizes must be an array");
    const dimensions = [
      ["zoom_percent", group.zoom_percent ?? [100]],
      ["text_zoom_percent", group.text_zoom_percent ?? [100]],
      ["base_dpr", group.base_dpr ?? [1]],
    ];
    for (const size of group.sizes) {
      if (!Array.isArray(size) || size.length !== 2) throw new Error("Matrix size must be [width,height]");
      for (const zoom of dimensions[0][1]) {
        for (const textZoom of dimensions[1][1]) {
          for (const baseDpr of dimensions[2][1]) {
            expanded.push({
              class: group.class,
              width: size[0],
              height: size[1],
              zoom_percent: zoom,
              text_zoom_percent: textZoom,
              base_dpr: baseDpr,
            });
          }
        }
      }
    }
  }
  return expanded;
}

function normalizeUrl(baseUrl, route, allowRemote) {
  const base = new URL(baseUrl);
  if (!["http:", "https:"].includes(base.protocol)) {
    throw new Error("base_url must use http or https");
  }
  const localHosts = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
  if (!allowRemote && !localHosts.has(base.hostname)) {
    throw new Error(
      `Refusing non-local base URL '${base.origin}'. Pass --allow-remote only for an authorized test origin.`,
    );
  }
  const target = new URL(route, base);
  if (target.origin !== base.origin) {
    throw new Error("route must remain on the configured base_url origin");
  }
  return { base_url: base.href, target_url: target.href, route: `${target.pathname}${target.search}${target.hash}` };
}

export function normalizePlan(raw, options = {}) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("Capture config must be a JSON object");
  }
  if (raw.schema_version !== SCHEMA_VERSION) {
    throw new Error(`Capture config schema_version must be '${SCHEMA_VERSION}'`);
  }
  const states = (raw.states ?? []).map(normalizeState);
  const suppliedViewports = raw.viewports ?? [];
  if (!Array.isArray(suppliedViewports)) throw new Error("viewports must be an array");
  const matrixViewports =
    raw.include_common_matrix === false || !options.commonMatrix
      ? []
      : expandCommonMatrix(options.commonMatrix);
  const viewportInputs = [...suppliedViewports, ...matrixViewports];
  const uniqueViewportInputs = [];
  const viewportKeys = new Set();
  for (const viewport of viewportInputs) {
    const key = stableStringify({
      class: viewport.class ?? "custom",
      width: viewport.width,
      height: viewport.height,
      zoom_percent: viewport.zoom_percent ?? viewport.zoom ?? 100,
      text_zoom_percent: viewport.text_zoom_percent ?? viewport.text_zoom ?? 100,
      base_dpr: viewport.base_dpr ?? viewport.dpr ?? 1,
    });
    if (!viewportKeys.has(key)) {
      viewportKeys.add(key);
      uniqueViewportInputs.push(viewport);
    }
  }
  const viewports = uniqueViewportInputs.map((viewport, index) =>
    normalizeViewport(
      {
        ...viewport,
        id:
          viewport.id ??
          `${viewport.class ?? "custom"}-${viewport.width}x${viewport.height}-z${viewport.zoom_percent ?? viewport.zoom ?? 100}-t${viewport.text_zoom_percent ?? viewport.text_zoom ?? 100}-d${viewport.base_dpr ?? viewport.dpr ?? 1}`,
      },
      index,
    ),
  );
  const requiredElements = (raw.required_elements ?? []).map(normalizeRequiredElement);
  if (states.length === 0) {
    throw new Error("Capture config requires at least one state");
  }
  if (viewports.length === 0) {
    throw new Error("Capture config requires at least one viewport");
  }
  if (requiredElements.length === 0) {
    throw new Error("Capture config requires at least one required element");
  }
  if (!states.every((state) => [state.material, state.primary, state.full_matrix].every((value) => typeof value === "boolean"))) {
    throw new Error("Every state material/primary/full_matrix flag must be boolean");
  }
  const primaryStates = states.filter((state) => state.primary);
  if (primaryStates.length !== 1 || primaryStates[0].full_matrix !== true) {
    throw new Error("Exactly one primary state must have full_matrix:true");
  }
  for (const element of requiredElements) {
    element.states = element.states ?? states.map((state) => state.id);
    if (!Array.isArray(element.states) || element.states.length === 0 || element.states.some((id) => !states.some((state) => state.id === id))) {
      throw new Error(`Required element '${element.name}' states must reference known state ids`);
    }
    if (![element.must_intersect_viewport, element.must_fit_horizontally, element.must_fit_vertically].every((value) => typeof value === "boolean")) {
      throw new Error(`Required element '${element.name}' fit/intersection gates must be boolean`);
    }
  }
  for (const stateValue of states) {
    if (!requiredElements.some((element) => element.states.includes(stateValue.id))) {
      throw new Error(`State '${stateValue.id}' needs at least one required element`);
    }
  }
  for (const [label, values] of [
    ["state", states.map((state) => state.id)],
    ["viewport", viewports.map((viewport) => viewport.id)],
    ["required element", requiredElements.map((element) => element.name)],
  ]) {
    if (new Set(values).size !== values.length) {
      throw new Error(`Capture config contains a duplicate ${label} identifier`);
    }
  }
  const referencePixelHash = nonEmptyString(
    raw.reference_pixel_sha256,
    "reference_pixel_sha256",
  ).toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(referencePixelHash)) {
    throw new Error("reference_pixel_sha256 must be a 64-character SHA-256 hex digest");
  }
  const allowRemote = Boolean(options.allowRemote || raw.allow_remote_origin);
  const url = normalizeUrl(
    nonEmptyString(options.baseUrl ?? raw.base_url, "base_url"),
    nonEmptyString(raw.route, "route"),
    allowRemote,
  );
  const sweepRaw = raw.continuous_sweep ?? {};
  if (!sweepRaw || typeof sweepRaw !== "object" || Array.isArray(sweepRaw)) {
    throw new Error("continuous_sweep must be an object");
  }
  const stateIds = sweepRaw.state_ids ?? states.map((state) => state.id);
  if (!Array.isArray(stateIds) || !stateIds.every((value) => typeof value === "string")) {
    throw new Error("continuous_sweep.state_ids must be an array of state ids");
  }
  for (const stateId of stateIds) {
    if (!states.some((state) => state.id === stateId)) {
      throw new Error(`continuous_sweep references unknown state '${stateId}'`);
    }
  }
  const continuousSweep = {
    enabled: sweepRaw.enabled !== false,
    min_width: positiveInteger(sweepRaw.min_width ?? 320, "continuous_sweep.min_width"),
    max_width: positiveInteger(sweepRaw.max_width ?? 2560, "continuous_sweep.max_width"),
    height: positiveInteger(
      sweepRaw.height ?? viewports[0].height,
      "continuous_sweep.height",
    ),
    requested_step_px: positiveInteger(
      sweepRaw.step_px ?? 8,
      "continuous_sweep.step_px",
    ),
    max_samples_per_state: positiveInteger(
      sweepRaw.max_samples_per_state ?? 400,
      "continuous_sweep.max_samples_per_state",
    ),
    state_ids: [...new Set(stateIds)],
  };
  if (continuousSweep.max_width < continuousSweep.min_width) {
    throw new Error("continuous_sweep.max_width must be >= min_width");
  }
  const colorScheme = nonEmptyString(
    raw.color_scheme ?? "light",
    "color_scheme",
  );
  if (!["light", "dark", "no-preference"].includes(colorScheme)) {
    throw new Error("color_scheme must be light, dark, or no-preference");
  }
  const browserName = nonEmptyString(
    options.browser ?? raw.browser ?? "chromium",
    "browser",
  );
  if (browserName === "firefox" && viewports.some((viewport) => viewport.is_mobile)) {
    throw new Error(
      "Playwright Firefox cannot provide isMobile emulation; mobile evidence would be non-certifying. Use Chromium or WebKit.",
    );
  }
  return {
    schema_version: SCHEMA_VERSION,
    profile: PROFILE_NAME,
    ...url,
    allow_remote_origin: allowRemote,
    reference_pixel_sha256: referencePixelHash,
    color_profile: nonEmptyString(raw.color_profile ?? "srgb", "color_profile"),
    color_scheme: colorScheme,
    fixed_time: nonEmptyString(
      raw.fixed_time ?? "2024-01-01T00:00:00.000Z",
      "fixed_time",
    ),
    locale: nonEmptyString(raw.locale ?? "en-US", "locale"),
    timezone_id: nonEmptyString(raw.timezone_id ?? "UTC", "timezone_id"),
    browser: browserName,
    navigation_timeout_ms: positiveInteger(
      raw.navigation_timeout_ms ?? 30000,
      "navigation_timeout_ms",
    ),
    settle_timeout_ms: positiveInteger(
      raw.settle_timeout_ms ?? 10000,
      "settle_timeout_ms",
    ),
    states,
    viewports,
    required_elements: requiredElements,
    continuous_sweep: continuousSweep,
    capture_breakpoint_boundaries: raw.capture_breakpoint_boundaries !== false,
    breakpoint_height: positiveInteger(
      raw.breakpoint_height ?? viewports[0].height,
      "breakpoint_height",
    ),
    breakpoint_width: positiveInteger(
      raw.breakpoint_width ?? viewports[0].width,
      "breakpoint_width",
    ),
  };
}

function unitToPixels(value, unit) {
  if (unit === "px") return value;
  if (unit === "em" || unit === "rem") return value * 16;
  return null;
}

/** Extract width/height boundaries from modern and legacy media syntax. */
export function extractMediaBoundaries(query) {
  const matches = [];
  const patterns = [
    {
      expression: /(?<![-\w])(?:min|max)-(width|height|inline-size|block-size)\s*:\s*(-?\d+(?:\.\d+)?)\s*(px|em|rem)\b/gi,
      dimension: 1,
      value: 2,
      unit: 3,
    },
    {
      expression: /(?<![-\w])(width|height|inline-size|block-size)\s*:\s*(-?\d+(?:\.\d+)?)\s*(px|em|rem)\b/gi,
      dimension: 1,
      value: 2,
      unit: 3,
    },
    {
      expression: /(?<![-\w])(width|height|inline-size|block-size)\s*(?:<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*(px|em|rem)\b/gi,
      dimension: 1,
      value: 2,
      unit: 3,
    },
    {
      expression: /(-?\d+(?:\.\d+)?)\s*(px|em|rem)\s*(?:<=|>=|<|>)\s*(width|height|inline-size|block-size)\b/gi,
      dimension: 3,
      value: 1,
      unit: 2,
    },
  ];
  for (const pattern of patterns) {
    for (const match of query.matchAll(pattern.expression)) {
      const value = Number(match[pattern.value]);
      const unit = match[pattern.unit].toLowerCase();
      const pixels = unitToPixels(value, unit);
      if (pixels !== null && pixels > 0) {
        const rawDimension = match[pattern.dimension].toLowerCase();
        matches.push({
          raw: match[0],
          dimension: ["width", "inline-size"].includes(rawDimension) ? "width" : "height",
          value,
          unit,
          css_px: Number(pixels.toFixed(4)),
          boundary_value: Math.round(pixels),
          boundary_width: Math.round(pixels),
        });
      }
    }
  }
  const unique = new Map();
  for (const match of matches) {
    unique.set(`${match.dimension}|${match.raw}|${match.css_px}`, match);
  }
  return [...unique.values()].sort(
    (left, right) => left.css_px - right.css_px || left.raw.localeCompare(right.raw),
  );
}

export function boundaryExtractionErrors(
  query,
  boundaries = extractMediaBoundaries(query),
) {
  const feature = /(?<![-\w])(?:min-|max-)?(?:width|height|inline-size|block-size)\b/i;
  if (!feature.test(query)) return [];
  const unresolved = [
    ...query.matchAll(
      /(?:(?<![-\w])(?:min-|max-)?(?:width|height|inline-size|block-size)\b[^,)]*|[^,(]*(?<![-\w])(?:width|height|inline-size|block-size)\b)\s*(?:[:<>=]+)?\s*(?:calc|var|env|clamp|min|max)\s*\([^)]*\)/gi,
    ),
  ].map((match) => match[0]);
  const errors = unresolved.map((expression) => ({
    gate: "unresolved-responsive-boundary",
    query,
    expression,
    message: "calc()/custom-property responsive boundaries cannot be certified by this harness",
  }));
  if (boundaries.length === 0 && errors.length === 0) {
    errors.push({
      gate: "unsupported-responsive-boundary",
      query,
      expression: query,
      message: "Responsive width/height condition was not converted to an exact CSS-pixel boundary",
    });
  }
  return errors;
}

export function extractBreakpoints(query) {
  return extractMediaBoundaries(query).filter((item) => item.dimension === "width");
}

function sanitize(value) {
  return value.replace(/[^a-zA-Z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "") || "case";
}

function nowIso() {
  return new Date().toISOString();
}

class TraceWriter {
  constructor(fileHandle, tracePath) {
    this.fileHandle = fileHandle;
    this.tracePath = tracePath;
    this.sequence = 0;
    this.queue = Promise.resolve();
  }

  async record(type, data = {}) {
    this.sequence += 1;
    const event = {
      sequence: this.sequence,
      timestamp: nowIso(),
      type,
      ...data,
    };
    const write = this.queue.then(() =>
      this.fileHandle.write(`${stableStringify(event)}\n`),
    );
    this.queue = write.catch(() => {});
    await write;
  }

  async close() {
    await this.queue;
    await this.fileHandle.sync();
    await this.fileHandle.close();
    return sha256Hex(await readFile(this.tracePath));
  }
}

function parseArgs(argv) {
  const options = {
    browser: undefined,
    allowRemote: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      options.help = true;
      continue;
    }
    if (argument === "--allow-remote") {
      options.allowRemote = true;
      continue;
    }
    const names = new Map([
      ["--config", "config"],
      ["--code-root", "codeRoot"],
      ["--output-dir", "outputDir"],
      ["--asset-ledger", "assetLedger"],
      ["--base-url", "baseUrl"],
      ["--browser", "browser"],
      ["--run-id", "runId"],
    ]);
    const key = names.get(argument);
    if (!key) {
      throw new Error(`Unknown argument: ${argument}`);
    }
    index += 1;
    if (index >= argv.length) {
      throw new Error(`${argument} requires a value`);
    }
    options[key] = argv[index];
  }
  return options;
}

function usage() {
  return `Usage: node capture_responsive.mjs --config PLAN.json --code-root PROJECT [options]

Options:
  --output-dir DIR      New/empty evidence directory (default: PROJECT/captures/RUN_ID)
  --asset-ledger FILE  Asset ledger used to link visible resources
  --base-url URL        Override config base_url
  --browser NAME        chromium, firefox, or webkit (default: chromium)
  --run-id ID           Explicit shared run id
  --allow-remote        Allow an explicitly authorized non-local test origin
  -h, --help            Show this help
`;
}

async function loadJson(file, label) {
  let payload;
  try {
    payload = JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    throw new Error(`${label} could not be read as JSON: ${error.message}`);
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error(`${label} must contain a JSON object`);
  }
  return payload;
}

async function ensureNewOutputDirectory(outputDir, codeRoot) {
  const resolved = path.resolve(outputDir);
  const relative = path.relative(path.resolve(codeRoot), resolved);
  const withinCodeRoot = relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
  if (withinCodeRoot && !isExcludedTreePath(relative.split(path.sep).join("/"))) {
    throw new Error(
      "An output directory inside --code-root must be under captures, output, or another fingerprint-excluded evidence directory",
    );
  }
  await mkdir(resolved, { recursive: true });
  const existing = await readdir(resolved);
  if (existing.length !== 0) {
    throw new Error(`Evidence output directory must be empty: ${resolved}`);
  }
  return resolved;
}

async function resolvePlaywright(codeRoot) {
  const packagePath = path.join(codeRoot, "package.json");
  try {
    await stat(packagePath);
  } catch {
    throw new Error(`Target project has no package.json: ${packagePath}`);
  }
  const targetRequire = createRequire(pathToFileURL(packagePath));
  let resolved;
  let packageName;
  for (const candidate of ["playwright", "@playwright/test"]) {
    try {
      resolved = targetRequire.resolve(candidate);
      packageName = candidate;
      break;
    } catch {
      // Try the next target-project dependency.
    }
  }
  if (!resolved) {
    throw new Error(
      "Playwright is not installed in the target project. Install playwright or @playwright/test there; the harness will not use a global fallback.",
    );
  }
  const module = targetRequire(packageName);
  return { module, package_name: packageName, resolved_path: resolved };
}

async function loadLedger(ledgerPath) {
  if (!ledgerPath) {
    throw new Error(
      "Strict responsive capture requires --asset-ledger (or config asset_ledger) so visible resources can be bound to provenance",
    );
  }
  const resolved = path.resolve(ledgerPath);
  const payload = await loadJson(resolved, "Asset ledger");
  if (!Array.isArray(payload.assets)) {
    throw new Error("Asset ledger requires an assets array");
  }
  const names = new Set();
  const assets = payload.assets.map((asset, index) => {
    if (!asset || typeof asset !== "object" || Array.isArray(asset)) {
      throw new Error(`Asset ledger entry ${index} must be an object`);
    }
    const name = nonEmptyString(asset.name, `assets[${index}].name`);
    if (names.has(name)) throw new Error(`Duplicate asset ledger name: ${name}`);
    names.add(name);
    const patterns = asset.capture_urls ?? asset.url_patterns ?? [];
    if (!Array.isArray(patterns) || !patterns.every((value) => typeof value === "string")) {
      throw new Error(`Asset '${name}' capture_urls/url_patterns must be strings`);
    }
    return {
      name,
      path: typeof asset.path === "string" ? asset.path.split(path.sep).join("/") : null,
      patterns,
      kind: asset.kind ?? null,
      material: asset.material === true,
    };
  });
  return { path: resolved, sha256: sha256Hex(await readFile(resolved)), assets };
}

function matchesLedgerAsset(urlValue, explicitName, ledger) {
  if (explicitName) {
    const explicit = ledger.assets.filter((asset) => asset.name === explicitName);
    return explicit.length === 1
      ? { ledger_name: explicitName, ledger_match: "data-ledger-name" }
      : { ledger_name: null, ledger_match_error: `unknown explicit ledger name '${explicitName}'` };
  }
  let parsedPath = "";
  try {
    parsedPath = decodeURIComponent(new URL(urlValue).pathname).replace(/^\/+/, "");
  } catch {
    parsedPath = urlValue;
  }
  const candidates = ledger.assets.filter((asset) => {
    if (asset.patterns.some((pattern) => urlValue.includes(pattern))) return true;
    if (!asset.path) return false;
    const normalized = asset.path.replace(/^\.\//, "").replace(/^public\//, "");
    return parsedPath === normalized || parsedPath.endsWith(`/${normalized}`) || parsedPath.endsWith(`/${path.posix.basename(normalized)}`);
  });
  if (candidates.length === 1) {
    return { ledger_name: candidates[0].name, ledger_match: "url-or-path" };
  }
  return {
    ledger_name: null,
    ...(candidates.length > 1
      ? { ledger_match_error: `ambiguous ledger match: ${candidates.map((asset) => asset.name).join(", ")}` }
      : {}),
  };
}

function addDeterminismInitScript(context, plan, stateValue) {
  return context.addInitScript(
    ({ fixedEpoch, seedText, expectedOrigin, localStorageValues, sessionStorageValues }) => {
      const NativeDate = Date;
      class FixedDate extends NativeDate {
        constructor(...args) {
          super(...(args.length ? args : [fixedEpoch]));
        }
        static now() {
          return fixedEpoch;
        }
      }
      Object.defineProperty(globalThis, "Date", { value: FixedDate, configurable: true });
      let seed = 2166136261;
      for (const character of seedText) {
        seed ^= character.charCodeAt(0);
        seed = Math.imul(seed, 16777619) >>> 0;
      }
      Math.random = () => {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        return seed / 4294967296;
      };
      try {
        const deterministicBytes = (typedArray) => {
          const bytes = new Uint8Array(
            typedArray.buffer,
            typedArray.byteOffset,
            typedArray.byteLength,
          );
          for (let index = 0; index < bytes.length; index += 1) {
            seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
            bytes[index] = seed & 0xff;
          }
          return typedArray;
        };
        Object.defineProperty(crypto, "getRandomValues", {
          configurable: true,
          value: deterministicBytes,
        });
        Object.defineProperty(crypto, "randomUUID", {
          configurable: true,
          value: () => {
            const bytes = deterministicBytes(new Uint8Array(16));
            bytes[6] = (bytes[6] & 0x0f) | 0x40;
            bytes[8] = (bytes[8] & 0x3f) | 0x80;
            const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
            return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
          },
        });
      } catch {
        // Browser implementations may expose non-configurable crypto methods.
      }
      const applyStorage = () => {
        if (location.origin !== expectedOrigin) return;
        for (const [key, value] of Object.entries(localStorageValues)) localStorage.setItem(key, value);
        for (const [key, value] of Object.entries(sessionStorageValues)) sessionStorage.setItem(key, value);
      };
      try {
        applyStorage();
      } catch {
        addEventListener("DOMContentLoaded", applyStorage, { once: true });
      }
    },
    {
      fixedEpoch: Date.parse(plan.fixed_time),
      seedText: `${stateValue.id}:${plan.route}`,
      expectedOrigin: new URL(plan.target_url).origin,
      localStorageValues: stateValue.local_storage,
      sessionStorageValues: stateValue.session_storage,
    },
  );
}

async function installNetworkSafety(context, page, plan, eventState) {
  await context.route("**/*", async (route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    if (!SAFE_METHODS.has(method)) {
      const item = { url: request.url(), method, reason: "unsafe-http-method-blocked" };
      eventState.blocked_write_requests.push(item);
      await route.abort("blockedbyclient");
      return;
    }
    if (request.isNavigationRequest()) {
      const targetOrigin = new URL(plan.target_url).origin;
      let requestOrigin;
      try {
        requestOrigin = new URL(request.url()).origin;
      } catch {
        requestOrigin = targetOrigin;
      }
      if (requestOrigin !== targetOrigin) {
        eventState.resource_errors.push({
          url: request.url(),
          method,
          reason: "cross-origin-navigation-blocked",
        });
        await route.abort("blockedbyclient");
        return;
      }
    }
    await route.continue();
  });
  page.on("download", (download) => {
    eventState.resource_errors.push({ url: download.url(), reason: "download-blocked" });
    void download.cancel();
  });
}

function attachPageRecorders(page, trace, caseId) {
  const stateValue = {
    console_errors: [],
    console_warnings: [],
    page_errors: [],
    request_failures: [],
    resource_errors: [],
    blocked_write_requests: [],
    dialogs: [],
    popups: [],
    resources: [],
    pending: new Set(),
  };
  page.on("console", (message) => {
    const record = { type: message.type(), text: message.text(), location: message.location() };
    if (message.type() === "error") stateValue.console_errors.push(record);
    if (message.type() === "warning") stateValue.console_warnings.push(record);
    if (["error", "warning"].includes(message.type())) {
      void trace.record("console", { case_id: caseId, ...record });
    }
  });
  page.on("pageerror", (error) => {
    const record = { name: error.name, message: error.message, stack: error.stack ?? null };
    stateValue.page_errors.push(record);
    void trace.record("page_error", { case_id: caseId, ...record });
  });
  page.on("requestfailed", (request) => {
    const record = {
      url: request.url(),
      method: request.method(),
      resource_type: request.resourceType(),
      error: request.failure()?.errorText ?? "unknown",
    };
    stateValue.request_failures.push(record);
    void trace.record("request_failed", { case_id: caseId, ...record });
  });
  page.on("response", (response) => {
    const pending = (async () => {
      const request = response.request();
      const headers = await response.allHeaders().catch(() => ({}));
      const record = {
        url: response.url(),
        status: response.status(),
        ok: response.ok(),
        resource_type: request.resourceType(),
        method: request.method(),
        content_type: headers["content-type"] ?? null,
        body_sha256: null,
        body_hash_error: null,
      };
      if (["document", "stylesheet", "script", "image", "font"].includes(record.resource_type)) {
        try {
          await response.finished();
          record.body_sha256 = sha256Hex(await response.body());
        } catch (error) {
          record.body_hash_error = error.message;
        }
      }
      stateValue.resources.push(record);
      if (record.status >= 400) {
        stateValue.resource_errors.push({
          url: record.url,
          status: record.status,
          resource_type: record.resource_type,
          reason: "http-error-status",
        });
      }
      await trace.record("response", { case_id: caseId, ...record });
    })();
    stateValue.pending.add(pending);
    void pending.finally(() => stateValue.pending.delete(pending));
  });
  page.on("dialog", (dialog) => {
    const record = { type: dialog.type(), message: dialog.message() };
    stateValue.dialogs.push(record);
    void dialog.dismiss();
  });
  page.on("popup", (popup) => {
    stateValue.popups.push({ url: popup.url() });
    void popup.close();
  });
  return stateValue;
}

async function applyStateActions(page, stateValue, defaultTimeout) {
  for (const action of stateValue.actions) {
    const locator = page.locator(action.selector);
    const timeout = action.timeout_ms ?? defaultTimeout;
    if (action.type === "click") {
      const safety = await locator.first().evaluate((element) => {
        const tag = element.tagName.toLowerCase();
        const type = (element.getAttribute("type") ?? "").toLowerCase();
        const formSubmit =
          (tag === "button" && (type === "" || type === "submit")) ||
          (tag === "input" && ["submit", "image"].includes(type));
        const href = element instanceof HTMLAnchorElement ? element.href : null;
        return { formSubmit, href };
      });
      if (safety.formSubmit) {
        throw new Error(`State '${stateValue.id}' refuses click on a submit control: ${action.selector}`);
      }
      await locator.first().click({ timeout });
    } else if (action.type === "fill") {
      await locator.first().fill(action.value, { timeout });
    } else if (action.type === "check") {
      await locator.first().check({ timeout });
    } else if (action.type === "uncheck") {
      await locator.first().uncheck({ timeout });
    } else if (action.type === "select_option") {
      await locator.first().selectOption(action.value, { timeout });
    } else if (action.type === "press") {
      if (action.value === "Enter") {
        const submits = await locator.first().evaluate((element) => Boolean(element.closest("form")));
        if (submits) throw new Error(`State '${stateValue.id}' refuses Enter inside a form`);
      }
      await locator.first().press(action.value, { timeout });
    } else if (action.type === "hover") {
      await locator.first().hover({ timeout });
    } else if (action.type === "focus") {
      await locator.first().focus({ timeout });
    } else if (action.type === "wait_for") {
      await locator.first().waitFor({ state: "visible", timeout });
    }
  }
}

const STABILITY_CSS = `
*, *::before, *::after {
  animation-delay: 0s !important;
  animation-duration: 0s !important;
  animation-iteration-count: 1 !important;
  caret-color: transparent !important;
  scroll-behavior: auto !important;
  transition-delay: 0s !important;
  transition-duration: 0s !important;
}
html { view-transition-name: none !important; }
`;

async function settlePage(
  page,
  plan,
  { waitForNetwork = true, textZoomPercent = 100 } = {},
) {
  const errors = [];
  if (waitForNetwork) {
    try {
      await page.waitForLoadState("load", { timeout: plan.settle_timeout_ms });
    } catch (error) {
      errors.push({ gate: "load_state", message: error.message });
    }
    try {
      await page.waitForLoadState("networkidle", { timeout: plan.settle_timeout_ms });
    } catch (error) {
      errors.push({ gate: "network_idle", message: error.message });
    }
  }
  await page.evaluate(
    ({ content }) => {
      let style = document.getElementById("pixel-precise-ui-stability");
      if (!style) {
        style = document.createElement("style");
        style.id = "pixel-precise-ui-stability";
        document.documentElement.append(style);
      }
      style.textContent = content;
    },
    { content: `${STABILITY_CSS}\nhtml { font-size: ${textZoomPercent}% !important; }` },
  );
  try {
    await page.evaluate(async () => {
      if (document.fonts) await document.fonts.ready;
      const images = [...document.images];
      await Promise.all(
        images.map(async (image) => {
          if (!image.complete) {
            await new Promise((resolve) => {
              image.addEventListener("load", resolve, { once: true });
              image.addEventListener("error", resolve, { once: true });
            });
          }
          if (typeof image.decode === "function") await image.decode().catch(() => {});
        }),
      );
      for (const animation of document.getAnimations()) animation.cancel();
      for (const media of document.querySelectorAll("video, audio")) {
        media.pause();
        if (Number.isFinite(media.duration) && media.duration > 0) media.currentTime = 0;
      }
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
  } catch (error) {
    errors.push({ gate: "document_settle", message: error.message });
  }
  return errors;
}

async function collectBrowserProbe(page, requiredElements, { decodeImages = true } = {}) {
  return page.evaluate(
    async ({ required, decodeVisibleImages }) => {
      const round = (value) => Math.round(value * 1000) / 1000;
      const rectArray = (rect) => [round(rect.x), round(rect.y), round(rect.width), round(rect.height)];
      const visible = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          Number(style.opacity) > 0 &&
          rect.width > 0 &&
          rect.height > 0 &&
          element.getClientRects().length > 0
        );
      };
      const viewportRect = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
      const clippedByAncestors = (element, rect) => {
        let clip = { ...viewportRect };
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const style = getComputedStyle(parent);
          if (/hidden|clip|auto|scroll/.test(`${style.overflowX} ${style.overflowY}`)) {
            const parentRect = parent.getBoundingClientRect();
            clip.left = Math.max(clip.left, parentRect.left);
            clip.top = Math.max(clip.top, parentRect.top);
            clip.right = Math.min(clip.right, parentRect.right);
            clip.bottom = Math.min(clip.bottom, parentRect.bottom);
          }
        }
        const intersectionWidth = Math.max(0, Math.min(rect.right, clip.right) - Math.max(rect.left, clip.left));
        const intersectionHeight = Math.max(0, Math.min(rect.bottom, clip.bottom) - Math.max(rect.top, clip.top));
        return intersectionWidth + 0.5 < rect.width || intersectionHeight + 0.5 < rect.height;
      };
      const measured = required.map((definition) => {
        const matches = [...document.querySelectorAll(definition.selector)];
        const element = matches[0] ?? null;
        if (!element) {
          return {
            name: definition.name,
            selector: definition.selector,
            count: 0,
            visible: false,
            clipped: true,
            rect: [0, 0, 0, 0],
            disallow_overlap_with: definition.disallow_overlap_with,
          };
        }
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          name: definition.name,
          selector: definition.selector,
          count: matches.length,
          tag_name: element.tagName.toLowerCase(),
          role: element.getAttribute("role"),
          visible: visible(element),
          clipped: clippedByAncestors(element, rect),
          rect: rectArray(rect),
          scroll_size: [element.scrollWidth, element.scrollHeight],
          client_size: [element.clientWidth, element.clientHeight],
          computed_style: {
            display: style.display,
            position: style.position,
            overflow_x: style.overflowX,
            overflow_y: style.overflowY,
            font_family: style.fontFamily,
            font_size: style.fontSize,
            font_weight: style.fontWeight,
            line_height: style.lineHeight,
          },
          disallow_overlap_with: definition.disallow_overlap_with,
        };
      });
      const unexpectedOverlaps = [];
      for (let leftIndex = 0; leftIndex < measured.length; leftIndex += 1) {
        for (let rightIndex = leftIndex + 1; rightIndex < measured.length; rightIndex += 1) {
          const left = measured[leftIndex];
          const right = measured[rightIndex];
          if (!left.visible || !right.visible) continue;
          if (
            !left.disallow_overlap_with.includes(right.name) &&
            !right.disallow_overlap_with.includes(left.name)
          ) continue;
          const [lx, ly, lw, lh] = left.rect;
          const [rx, ry, rw, rh] = right.rect;
          const width = Math.min(lx + lw, rx + rw) - Math.max(lx, rx);
          const height = Math.min(ly + lh, ry + rh) - Math.max(ly, ry);
          if (width > 1 && height > 1) {
            const leftElement = document.querySelector(left.selector);
            const rightElement = document.querySelector(right.selector);
            if (
              leftElement &&
              rightElement &&
              !leftElement.contains(rightElement) &&
              !rightElement.contains(leftElement)
            ) {
              unexpectedOverlaps.push({
                elements: [left.name, right.name],
                intersection: [round(width), round(height)],
              });
            }
          }
        }
      }
      const overflowElements = [];
      for (const element of document.querySelectorAll("body *")) {
        if (overflowElements.length >= 250 || !visible(element)) continue;
        const rect = element.getBoundingClientRect();
        const ownOverflow = element.scrollWidth - element.clientWidth;
        if (rect.right > document.documentElement.clientWidth + 1 || rect.left < -1 || ownOverflow > 1) {
          overflowElements.push({
            selector_hint:
              element.id
                ? `#${CSS.escape(element.id)}`
                : `${element.tagName.toLowerCase()}${element.classList.length ? `.${[...element.classList].slice(0, 2).map(CSS.escape).join(".")}` : ""}`,
            rect: rectArray(rect),
            scroll_width: element.scrollWidth,
            client_width: element.clientWidth,
          });
        }
      }
      const mediaQueries = [];
      const cssomErrors = [];
      const stylesheets = [];
      const seenRules = new Set();
      const visitRules = (rules, source, depth = 0) => {
        if (!rules || depth > 20) return;
        for (const rule of rules) {
          if (rule instanceof CSSMediaRule) {
            const query = rule.conditionText;
            const key = `media|${source ?? "inline"}|${query}`;
            if (!seenRules.has(key)) {
              seenRules.add(key);
              mediaQueries.push({ kind: "media", query, source, matches: matchMedia(query).matches });
            }
            visitRules(rule.cssRules, source, depth + 1);
          } else if (
            typeof CSSContainerRule !== "undefined" &&
            rule instanceof CSSContainerRule
          ) {
            const query = rule.conditionText || rule.containerQuery || "";
            const containerName = rule.name || rule.containerName || null;
            if (!query) {
              cssomErrors.push({
                source,
                kind: "container",
                container_name: containerName,
                error: "CSSContainerRule did not expose condition text",
              });
              continue;
            }
            const key = `container|${source ?? "inline"}|${containerName ?? ""}|${query}`;
            if (!seenRules.has(key)) {
              seenRules.add(key);
              mediaQueries.push({
                kind: "container",
                container_name: containerName,
                query,
                source,
                matches: null,
              });
            }
            visitRules(rule.cssRules, source, depth + 1);
          } else if (
            (typeof CSSSupportsRule !== "undefined" && rule instanceof CSSSupportsRule) ||
            (typeof CSSLayerBlockRule !== "undefined" && rule instanceof CSSLayerBlockRule)
          ) {
            visitRules(rule.cssRules, source, depth + 1);
          } else if (rule instanceof CSSImportRule) {
            try {
              visitRules(rule.styleSheet?.cssRules, rule.href || source, depth + 1);
            } catch (error) {
              cssomErrors.push({ source: rule.href || source, error: String(error) });
            }
          }
        }
      };
      for (const sheet of document.styleSheets) {
        const record = { href: sheet.href, disabled: sheet.disabled, accessible: true, rule_count: null };
        try {
          record.rule_count = sheet.cssRules.length;
          visitRules(sheet.cssRules, sheet.href || "inline");
        } catch (error) {
          record.accessible = false;
          record.error = String(error);
          cssomErrors.push({ source: sheet.href, error: String(error) });
        }
        stylesheets.push(record);
      }
      const visibleResourceMap = new Map();
      const addVisibleResource = (url, element, kind, explicitLedgerName = null, image = null) => {
        if (!url) return;
        let absolute;
        try {
          absolute = new URL(url, document.baseURI).href;
        } catch {
          absolute = url;
        }
        const key = `${kind}|${absolute}`;
        const existing = visibleResourceMap.get(key) ?? {
          url: absolute,
          kind,
          element_hints: [],
          explicit_ledger_name: explicitLedgerName,
          decoded_sha256: null,
          decoded_dimensions: null,
          decode_error: null,
          image,
        };
        const hint = element.id
          ? `#${element.id}`
          : `${element.tagName.toLowerCase()}${element.classList.length ? `.${[...element.classList].slice(0, 2).join(".")}` : ""}`;
        if (!existing.element_hints.includes(hint)) existing.element_hints.push(hint);
        if (!existing.explicit_ledger_name && explicitLedgerName) existing.explicit_ledger_name = explicitLedgerName;
        if (!existing.image && image) existing.image = image;
        visibleResourceMap.set(key, existing);
      };
      for (const image of document.images) {
        if (visible(image)) {
          addVisibleResource(
            image.currentSrc || image.src,
            image,
            "image",
            image.dataset.ledgerName || null,
            image,
          );
        }
      }
      for (const video of document.querySelectorAll("video[poster]")) {
        if (visible(video)) {
          addVisibleResource(
            video.poster,
            video,
            "poster",
            video.dataset.ledgerName || null,
          );
        }
      }
      let canvasOrdinal = 0;
      for (const canvas of document.querySelectorAll("canvas")) {
        if (!visible(canvas)) continue;
        canvasOrdinal += 1;
        addVisibleResource(
          `canvas://document/${canvasOrdinal}`,
          canvas,
          "canvas",
          canvas.dataset.ledgerName || null,
          canvas,
        );
      }
      for (const element of document.querySelectorAll("body *")) {
        if (!visible(element)) continue;
        const background = getComputedStyle(element).backgroundImage;
        if (!background || background === "none") continue;
        for (const match of background.matchAll(/url\(["']?([^"')]+)["']?\)/g)) {
          addVisibleResource(match[1], element, "css-background", element.dataset.ledgerName || null);
        }
      }
      const hashImage = async (resource) => {
        if (!decodeVisibleImages || !globalThis.crypto?.subtle) return;
        let image = resource.image;
        let canvas;
        let width;
        let height;
        if (resource.kind === "canvas") {
          canvas = image;
          width = canvas.width;
          height = canvas.height;
        } else if (!image) {
          image = new Image();
          image.crossOrigin = "anonymous";
          image.src = resource.url;
          await image.decode();
        } else if (typeof image.decode === "function") {
          await image.decode();
        }
        width ??= image.naturalWidth;
        height ??= image.naturalHeight;
        if (!width || !height) throw new Error("decoded image has zero dimensions");
        if (!canvas) {
          canvas = document.createElement("canvas");
          canvas.width = width;
          canvas.height = height;
        }
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (resource.kind !== "canvas") context.drawImage(image, 0, 0);
        const rgba = context.getImageData(0, 0, width, height).data;
        const prefix = new TextEncoder().encode(`RGB:${width}x${height}:`);
        const bytes = new Uint8Array(prefix.length + width * height * 3);
        bytes.set(prefix, 0);
        let output = prefix.length;
        for (let index = 0; index < rgba.length; index += 4) {
          bytes[output++] = rgba[index];
          bytes[output++] = rgba[index + 1];
          bytes[output++] = rgba[index + 2];
        }
        const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
        resource.decoded_sha256 = [...digest].map((value) => value.toString(16).padStart(2, "0")).join("");
        resource.decoded_dimensions = [width, height];
      };
      for (const resource of visibleResourceMap.values()) {
        try {
          await hashImage(resource);
        } catch (error) {
          resource.decode_error = String(error);
        }
        delete resource.image;
      }
      const performanceResources = performance
        .getEntriesByType("resource")
        .map((entry) => ({
          url: entry.name,
          initiator_type: entry.initiatorType,
          duration_ms: round(entry.duration),
          transfer_size: entry.transferSize,
          encoded_body_size: entry.encodedBodySize,
          decoded_body_size: entry.decodedBodySize,
        }));
      const doc = document.documentElement;
      const body = document.body;
      const documentClientWidth = doc.clientWidth;
      const documentScrollWidth = doc.scrollWidth;
      const bodyScrollWidth = body?.scrollWidth ?? documentScrollWidth;
      const horizontalOverflow = Math.max(
        0,
        documentScrollWidth - documentClientWidth,
        bodyScrollWidth - documentClientWidth,
      );
      const safeAreaProbe = document.createElement("div");
      safeAreaProbe.setAttribute("aria-hidden", "true");
      safeAreaProbe.style.cssText = [
        "position:fixed",
        "left:0",
        "top:0",
        "width:0",
        "height:0",
        "visibility:hidden",
        "pointer-events:none",
        "padding-top:env(safe-area-inset-top)",
        "padding-right:env(safe-area-inset-right)",
        "padding-bottom:env(safe-area-inset-bottom)",
        "padding-left:env(safe-area-inset-left)",
      ].join(";");
      document.documentElement.append(safeAreaProbe);
      const safeAreaStyle = getComputedStyle(safeAreaProbe);
      const safeArea = {
        css_env_supported: CSS.supports("padding-top", "env(safe-area-inset-top)"),
        top_px: Number.parseFloat(safeAreaStyle.paddingTop) || 0,
        right_px: Number.parseFloat(safeAreaStyle.paddingRight) || 0,
        bottom_px: Number.parseFloat(safeAreaStyle.paddingBottom) || 0,
        left_px: Number.parseFloat(safeAreaStyle.paddingLeft) || 0,
      };
      safeAreaProbe.remove();
      let userAgentData = null;
      let userAgentDataError = null;
      if (navigator.userAgentData) {
        userAgentData = {
          mobile: Boolean(navigator.userAgentData.mobile),
          platform: navigator.userAgentData.platform || null,
          brands: Array.from(navigator.userAgentData.brands ?? [], (brand) => ({
            brand: brand.brand,
            version: brand.version,
          })),
        };
        if (typeof navigator.userAgentData.getHighEntropyValues === "function") {
          try {
            const entropy = await navigator.userAgentData.getHighEntropyValues([
              "architecture",
              "bitness",
              "fullVersionList",
              "model",
              "platformVersion",
            ]);
            userAgentData.high_entropy = entropy;
          } catch (error) {
            userAgentDataError = String(error);
          }
        }
      }
      const screenOrientation = screen.orientation
        ? {
            type: screen.orientation.type,
            angle: screen.orientation.angle,
          }
        : null;
      return {
        harness_collected: true,
        url: location.href,
        title: document.title,
        inner_width: innerWidth,
        inner_height: innerHeight,
        outer_width: outerWidth,
        outer_height: outerHeight,
        device_pixel_ratio: devicePixelRatio,
        visual_viewport: visualViewport
          ? {
              width: round(visualViewport.width),
              height: round(visualViewport.height),
              scale: round(visualViewport.scale),
              offset_left: round(visualViewport.offsetLeft),
              offset_top: round(visualViewport.offsetTop),
            }
          : null,
        device_environment: {
          navigator: {
            user_agent: navigator.userAgent,
            user_agent_data: userAgentData,
            user_agent_data_error: userAgentDataError,
            platform: navigator.platform,
            vendor: navigator.vendor,
            language: navigator.language,
            languages: [...navigator.languages],
            hardware_concurrency: navigator.hardwareConcurrency,
            device_memory_gib: navigator.deviceMemory ?? null,
            max_touch_points: navigator.maxTouchPoints,
          },
          touch: {
            touch_event_supported: "ontouchstart" in globalThis,
            touch_constructor_supported: typeof TouchEvent === "function",
            pointer_event_supported: typeof PointerEvent === "function",
            pointer_coarse: matchMedia("(pointer: coarse)").matches,
            any_pointer_coarse: matchMedia("(any-pointer: coarse)").matches,
            hover_none: matchMedia("(hover: none)").matches,
            any_hover_none: matchMedia("(any-hover: none)").matches,
          },
          screen: {
            width: screen.width,
            height: screen.height,
            avail_width: screen.availWidth,
            avail_height: screen.availHeight,
            color_depth: screen.colorDepth,
            pixel_depth: screen.pixelDepth,
            orientation: screenOrientation,
          },
          safe_area_insets: safeArea,
          preferences: {
            prefers_color_scheme_dark: matchMedia("(prefers-color-scheme: dark)").matches,
            prefers_color_scheme_light: matchMedia("(prefers-color-scheme: light)").matches,
            prefers_color_scheme_no_preference: matchMedia("(prefers-color-scheme: no-preference)").matches,
            prefers_reduced_motion_reduce: matchMedia("(prefers-reduced-motion: reduce)").matches,
          },
        },
        document_client_width: documentClientWidth,
        document_client_height: doc.clientHeight,
        document_scroll_width: documentScrollWidth,
        document_scroll_height: doc.scrollHeight,
        body_scroll_width: bodyScrollWidth,
        body_scroll_height: body?.scrollHeight ?? doc.scrollHeight,
        horizontal_overflow_px: round(horizontalOverflow),
        required_elements: measured,
        missing_required_elements: measured.filter((item) => item.count === 0).map((item) => item.name),
        duplicate_required_elements: measured.filter((item) => item.count !== 1).map((item) => ({ name: item.name, count: item.count })),
        overflow_elements: overflowElements,
        unexpected_overlaps: unexpectedOverlaps,
        media_queries: mediaQueries.sort((left, right) => `${left.source}|${left.query}`.localeCompare(`${right.source}|${right.query}`)),
        cssom_errors: cssomErrors,
        stylesheets,
        fonts: document.fonts
          ? [...document.fonts].map((font) => ({
              family: font.family,
              style: font.style,
              weight: font.weight,
              stretch: font.stretch,
              status: font.status,
            }))
          : [],
        font_set_status: document.fonts?.status ?? "unsupported",
        root_font_size_px: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
        visible_resources: [...visibleResourceMap.values()].sort((left, right) => left.url.localeCompare(right.url)),
        performance_resources: performanceResources,
      };
    },
    { required: requiredElements, decodeVisibleImages: decodeImages },
  );
}

async function decodedPngFingerprint(page, pngBytes) {
  return page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(image, 0, 0);
    const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const prefix = new TextEncoder().encode(`RGB:${canvas.width}x${canvas.height}:`);
    const bytes = new Uint8Array(prefix.length + canvas.width * canvas.height * 3);
    bytes.set(prefix, 0);
    let output = prefix.length;
    for (let index = 0; index < rgba.length; index += 4) {
      bytes[output++] = rgba[index];
      bytes[output++] = rgba[index + 1];
      bytes[output++] = rgba[index + 2];
    }
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return {
      width: canvas.width,
      height: canvas.height,
      pixel_sha256: [...digest]
        .map((value) => value.toString(16).padStart(2, "0"))
        .join(""),
    };
  }, pngBytes.toString("base64"));
}

function linkVisibleResources(probe, ledger, networkResources) {
  const byUrl = new Map(networkResources.map((resource) => [resource.url, resource]));
  probe.visible_resources = probe.visible_resources.map((resource) => {
    const link = matchesLedgerAsset(resource.url, resource.explicit_ledger_name, ledger);
    const network = byUrl.get(resource.url);
    const result = {
      url: resource.url,
      type: resource.kind === "css-background" ? "background-image" : resource.kind,
      loaded: resource.decode_error === null,
      element_hints: resource.element_hints,
      ...link,
      decoded_pixel_sha256: resource.decoded_sha256,
      decoded_dimensions: resource.decoded_dimensions,
      decode_error: resource.decode_error,
      response_body_sha256: network?.body_sha256 ?? null,
    };
    return result;
  });
  probe.unlinked_visible_resources = probe.visible_resources
    .filter((resource) => !resource.ledger_name)
    .map((resource) => ({ url: resource.url, kind: resource.kind, error: resource.ledger_match_error ?? "no ledger match" }));
  probe.undecoded_visible_rasters = probe.visible_resources
    .filter((resource) => !resource.decoded_pixel_sha256)
    .map((resource) => ({ url: resource.url, error: resource.decode_error ?? "no decoded hash" }));
  for (const network of networkResources.filter((resource) => resource.resource_type === "font")) {
    const link = matchesLedgerAsset(network.url, null, ledger);
    probe.visible_resources.push({
      url: network.url,
      type: "font",
      loaded: network.ok === true && !network.body_hash_error,
      ...link,
      decoded_pixel_sha256: null,
      decoded_dimensions: null,
      decode_error: network.body_hash_error,
      response_body_sha256: network.body_sha256,
    });
    if (!link.ledger_name) {
      probe.unlinked_visible_resources.push({
        url: network.url,
        kind: "font",
        error: link.ledger_match_error ?? "no ledger match",
      });
    }
  }
  probe.visible_resources.sort((left, right) => `${left.type}|${left.url}`.localeCompare(`${right.type}|${right.url}`));
}

function annotateConditionalQueries(probe) {
  for (const conditional of probe.media_queries) {
    const boundaries = extractMediaBoundaries(conditional.query);
    const extractionErrors = boundaryExtractionErrors(
      conditional.query,
      boundaries,
    );
    conditional.extracted_boundaries = boundaries;
    conditional.boundary_extraction_errors = extractionErrors;
    for (const error of extractionErrors) {
      probe.cssom_errors.push({
        source: conditional.source ?? "inline",
        kind: conditional.kind ?? "media",
        container_name: conditional.container_name ?? null,
        ...error,
      });
    }
  }
}

function mergeMediaEvidence(aggregate, caseValue) {
  for (const media of caseValue.probe.media_queries) {
    const key = `${media.kind ?? "media"}|${media.source ?? "inline"}|${media.container_name ?? ""}|${media.query}`;
    const existing = aggregate.get(key) ?? {
      kind: media.kind ?? "media",
      container_name: media.container_name ?? null,
      query: media.query,
      source: media.source,
      observed_matches: [],
      extracted_breakpoints: (media.extracted_boundaries ?? extractMediaBoundaries(media.query))
        .filter((item) => item.dimension === "width"),
      extracted_boundaries: media.extracted_boundaries ?? extractMediaBoundaries(media.query),
      boundary_extraction_errors:
        media.boundary_extraction_errors ?? boundaryExtractionErrors(media.query),
    };
    existing.observed_matches.push({ case_id: caseValue.id, matches: media.matches });
    aggregate.set(key, existing);
  }
}

function viewportForBrowser(viewport) {
  return {
    viewport: {
      width: viewport.effective_css_width,
      height: viewport.effective_css_height,
    },
    deviceScaleFactor: viewport.effective_device_scale_factor,
  };
}

function deviceEmulationProfile(browserName, browserVersion, viewport) {
  if (viewport.device_class === "desktop") {
    return { isMobile: false, hasTouch: false };
  }
  if (browserName === "chromium") {
    const device = viewport.device_class === "mobile" ? "Pixel 7" : "Pixel Tablet";
    const mobileToken = viewport.device_class === "mobile" ? " Mobile" : "";
    return {
      isMobile: viewport.is_mobile,
      hasTouch: viewport.has_touch,
      userAgent: `Mozilla/5.0 (Linux; Android 14; ${device}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${browserVersion}${mobileToken} Safari/537.36`,
    };
  }
  if (browserName === "webkit") {
    const platform =
      viewport.device_class === "mobile"
        ? "iPhone; CPU iPhone OS 17_6 like Mac OS X"
        : "iPad; CPU OS 17_6 like Mac OS X";
    return {
      isMobile: viewport.is_mobile,
      hasTouch: viewport.has_touch,
      userAgent: `Mozilla/5.0 (${platform}) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1`,
    };
  }
  return {
    isMobile: false,
    hasTouch: viewport.has_touch,
    userAgent: `Mozilla/5.0 (Android 14; Tablet; rv:${browserVersion}) Gecko/20100101 Firefox/${browserVersion}`,
  };
}

export function assessDeviceEmulation(probe, viewport) {
  const expected = {
    device_class: viewport.device_class,
    is_mobile: viewport.is_mobile,
    has_touch: viewport.has_touch,
    css_viewport_width: viewport.effective_css_width,
    css_viewport_height: viewport.effective_css_height,
    screen_width: viewport.width,
    screen_height: viewport.height,
  };
  const environment = probe?.device_environment;
  const navigatorEvidence = environment?.navigator;
  const touchEvidence = environment?.touch;
  const screenEvidence = environment?.screen;
  const safeAreaEvidence = environment?.safe_area_insets;
  const userAgent = navigatorEvidence?.user_agent ?? "";
  const userAgentDataMobile = navigatorEvidence?.user_agent_data?.mobile;
  const mobileIdentity =
    userAgentDataMobile === true || /iPhone|iPod|Android.*Mobile/i.test(userAgent);
  const tabletIdentity = /iPad|Tablet|Android/i.test(userAgent) && !/Android.*Mobile/i.test(userAgent);
  const maxTouchPoints = Number(navigatorEvidence?.max_touch_points);
  const hasTouch =
    Number.isFinite(maxTouchPoints) &&
    maxTouchPoints > 0 &&
    touchEvidence?.touch_event_supported === true;
  const actual = {
    mobile_identity: mobileIdentity,
    tablet_identity: tabletIdentity,
    has_touch: hasTouch,
    max_touch_points: Number.isFinite(maxTouchPoints) ? maxTouchPoints : null,
    pointer_coarse: touchEvidence?.pointer_coarse ?? null,
    any_pointer_coarse: touchEvidence?.any_pointer_coarse ?? null,
    hover_none: touchEvidence?.hover_none ?? null,
    css_viewport_width: probe?.inner_width ?? null,
    css_viewport_height: probe?.inner_height ?? null,
    screen_width: screenEvidence?.width ?? null,
    screen_height: screenEvidence?.height ?? null,
    visual_viewport_present: probe?.visual_viewport !== null && probe?.visual_viewport !== undefined,
    safe_area_css_env_supported: safeAreaEvidence?.css_env_supported ?? null,
  };
  const errors = [];
  const reject = (gate, wanted, observed) => {
    errors.push({ gate, expected: wanted, actual: observed });
  };
  if (!environment || !navigatorEvidence || !touchEvidence || !screenEvidence) {
    reject("device-environment-probe", "browser-collected navigator/touch/screen evidence", null);
    return { expected, actual, errors };
  }
  if (!actual.visual_viewport_present) {
    reject("visual-viewport", "present", null);
  }
  if (!safeAreaEvidence || safeAreaEvidence.css_env_supported !== true) {
    reject("safe-area-css-env", true, safeAreaEvidence?.css_env_supported ?? null);
  }
  if (viewport.device_class === "mobile" || viewport.device_class === "tablet") {
    if (!hasTouch) reject("touch-capability", true, actual.has_touch);
    if (touchEvidence.pointer_coarse !== true && touchEvidence.any_pointer_coarse !== true) {
      reject("coarse-pointer", true, {
        pointer_coarse: touchEvidence.pointer_coarse,
        any_pointer_coarse: touchEvidence.any_pointer_coarse,
      });
    }
    if (touchEvidence.hover_none !== true) {
      reject("hover-none", true, touchEvidence.hover_none);
    }
    if (probe.inner_width !== viewport.effective_css_width) {
      reject("css-viewport-width", viewport.effective_css_width, probe.inner_width ?? null);
    }
    if (screenEvidence.width !== viewport.width || screenEvidence.height !== viewport.height) {
      reject(
        "screen-dimensions",
        [viewport.width, viewport.height],
        [screenEvidence.width ?? null, screenEvidence.height ?? null],
      );
    }
  }
  if (viewport.device_class === "mobile" && !mobileIdentity) {
    reject("mobile-browser-identity", true, {
      user_agent: userAgent,
      user_agent_data_mobile: userAgentDataMobile ?? null,
    });
  }
  if (viewport.device_class === "tablet" && !tabletIdentity) {
    reject("tablet-browser-identity", true, userAgent);
  }
  return { expected, actual, errors };
}

async function createContext(browser, plan, viewport, stateValue) {
  const dimensions = viewportForBrowser(viewport);
  const deviceProfile = deviceEmulationProfile(
    plan.browser,
    browser.version(),
    viewport,
  );
  const context = await browser.newContext({
    ...deviceProfile,
    ...dimensions,
    screen: { width: viewport.width, height: viewport.height },
    colorScheme: plan.color_scheme,
    contrast: "no-preference",
    forcedColors: "none",
    locale: plan.locale,
    timezoneId: plan.timezone_id,
    reducedMotion: "reduce",
    serviceWorkers: "block",
    acceptDownloads: false,
  });
  await addDeterminismInitScript(context, plan, stateValue);
  if (viewport.text_zoom_percent !== 100) {
    await context.addInitScript((textZoomPercent) => {
      const apply = () => {
        if (!document.documentElement || document.getElementById("pixel-precise-ui-text-zoom")) return;
        const style = document.createElement("style");
        style.id = "pixel-precise-ui-text-zoom";
        style.textContent = `html { font-size: ${textZoomPercent}% !important; }`;
        document.documentElement.append(style);
      };
      if (document.documentElement) apply();
      else new MutationObserver((_, observer) => {
        if (document.documentElement) {
          observer.disconnect();
          apply();
        }
      }).observe(document, { childList: true, subtree: true });
    }, viewport.text_zoom_percent);
  }
  return context;
}

async function captureCase({
  browser,
  plan,
  viewport,
  stateValue,
  outputDir,
  trace,
  ledger,
  caseOrdinal,
  runId,
}) {
  const stateSlug = sanitize(stateValue.id);
  const viewportSlug = sanitize(viewport.id);
  const caseId = `${String(caseOrdinal).padStart(4, "0")}-${stateSlug}-${viewportSlug}`;
  const screenshotRelative = `screenshots/${caseId}.png`;
  const repeatRelative = `screenshots/${caseId}-repeat.png`;
  const screenshotPath = path.join(outputDir, screenshotRelative);
  const repeatPath = path.join(outputDir, repeatRelative);
  await trace.record("case_started", { case_id: caseId, state: stateValue.id, viewport });
  const context = await createContext(browser, plan, viewport, stateValue);
  const page = await context.newPage();
  page.setDefaultTimeout(plan.navigation_timeout_ms);
  page.setDefaultNavigationTimeout(plan.navigation_timeout_ms);
  const events = attachPageRecorders(page, trace, caseId);
  await installNetworkSafety(context, page, plan, events);
  const settleErrors = [];
  let fatalError = null;
  let first = Buffer.alloc(0);
  let repeat = Buffer.alloc(0);
  let firstDecoded = null;
  let repeatDecoded = null;
  let probe = null;
  const requiredForState = plan.required_elements.filter((element) =>
    element.states.includes(stateValue.id),
  );
  try {
    await page.goto(plan.target_url, { waitUntil: "domcontentloaded" });
    await applyStateActions(page, stateValue, plan.navigation_timeout_ms);
    settleErrors.push(
      ...(await settlePage(page, plan, {
        textZoomPercent: viewport.text_zoom_percent,
      })),
    );
    probe = await collectBrowserProbe(page, requiredForState, { decodeImages: true });
    annotateConditionalQueries(probe);
    first = await page.screenshot({
      path: screenshotPath,
      type: "png",
      fullPage: false,
      animations: "disabled",
      caret: "hide",
      scale: "device",
    });
    settleErrors.push(
      ...(await settlePage(page, plan, {
        waitForNetwork: false,
        textZoomPercent: viewport.text_zoom_percent,
      })),
    );
    repeat = await page.screenshot({
      path: repeatPath,
      type: "png",
      fullPage: false,
      animations: "disabled",
      caret: "hide",
      scale: "device",
    });
    firstDecoded = await decodedPngFingerprint(page, first);
    repeatDecoded = await decodedPngFingerprint(page, repeat);
  } catch (error) {
    fatalError = { name: error.name, message: error.message, stack: error.stack ?? null };
  }
  await Promise.allSettled([...events.pending]);
  if (!probe) {
    probe = {
      harness_collected: true,
      inner_width: viewport.effective_css_width,
      inner_height: viewport.effective_css_height,
      device_pixel_ratio: viewport.dpr,
      document_client_width: viewport.effective_css_width,
      document_scroll_width: viewport.effective_css_width,
      body_scroll_width: viewport.effective_css_width,
      horizontal_overflow_px: 0,
      required_elements: requiredForState.map((element) => ({
        name: element.name,
        selector: element.selector,
        count: 0,
        visible: false,
        clipped: true,
        rect: [0, 0, 0, 0],
      })),
      missing_required_elements: requiredForState.map((element) => element.name),
      overflow_elements: [],
      unexpected_overlaps: [],
      media_queries: [],
      cssom_errors: [],
      visible_resources: [],
      visual_viewport: null,
      device_environment: null,
    };
  }
  probe.console_errors = events.console_errors;
  probe.console_warnings = events.console_warnings;
  probe.page_errors = events.page_errors;
  probe.failed_resources = [...events.request_failures, ...events.resource_errors];
  probe.resource_errors = events.resource_errors;
  probe.blocked_write_requests = events.blocked_write_requests;
  probe.dialogs = events.dialogs;
  probe.popups = events.popups;
  probe.resources = events.resources.sort((left, right) => left.url.localeCompare(right.url));
  probe.settle_errors = settleErrors;
  probe.run_id = runId;
  probe.route = plan.route;
  probe.text_zoom_percent = viewport.text_zoom_percent;
  probe.device_emulation = {
    browser_name: plan.browser,
    ...assessDeviceEmulation(probe, viewport),
  };
  probe.emulation_errors = probe.device_emulation.errors;
  linkVisibleResources(probe, ledger, probe.resources);
  const capturedAt = nowIso();
  const byteIdentical = first.length > 0 && first.equals(repeat);
  const result = {
    id: caseId,
    class: viewport.class,
    harness_collected: true,
    route: plan.route,
    state_id: stateValue.id,
    collector_run_id: runId,
    captured_at: capturedAt,
    viewport: {
      width: viewport.width,
      height: viewport.height,
      base_dpr: viewport.base_dpr,
      dpr: viewport.dpr,
      zoom_percent: viewport.zoom_percent,
      text_zoom_percent: viewport.text_zoom_percent,
      effective_css_width: viewport.effective_css_width,
      effective_css_height: viewport.effective_css_height,
      effective_device_scale_factor: viewport.effective_device_scale_factor,
      device_class: viewport.device_class,
      is_mobile: viewport.is_mobile,
      has_touch: viewport.has_touch,
    },
    screenshot: screenshotRelative,
    repeat_screenshot: repeatRelative,
    screenshot_file_sha256: first.length ? sha256Hex(first) : null,
    repeat_screenshot_file_sha256: repeat.length ? sha256Hex(repeat) : null,
    screenshot_pixel_sha256: firstDecoded?.pixel_sha256 ?? null,
    repeat_pixel_sha256: repeatDecoded?.pixel_sha256 ?? null,
    capture_dimensions: firstDecoded ? [firstDecoded.width, firstDecoded.height] : null,
    byte_identical_repeat_capture: byteIdentical,
    run_metadata: `run-metadata/${caseId}.json`,
    fatal_error: fatalError,
    probe,
    visual_review: {
      status: "pending",
      reviewer: null,
      reviewed_at: null,
      reviewed_screenshot_pixel_sha256: null,
      unexpected_seams: [],
      ghosted_artifacts: [],
      distorted_assets: [],
      background_mismatches: [],
    },
  };
  await trace.record("case_completed", {
    case_id: caseId,
    byte_identical_repeat_capture: byteIdentical,
    screenshot_file_sha256: result.screenshot_file_sha256,
    repeat_screenshot_file_sha256: result.repeat_screenshot_file_sha256,
    screenshot_pixel_sha256: result.screenshot_pixel_sha256,
    repeat_pixel_sha256: result.repeat_pixel_sha256,
    fatal_error: fatalError,
    probe_summary: {
      inner_width: probe.inner_width ?? null,
      inner_height: probe.inner_height ?? null,
      device_pixel_ratio: probe.device_pixel_ratio ?? null,
      missing_required_elements: probe.missing_required_elements,
      overflow_count: probe.overflow_elements.length,
      unexpected_overlap_count: probe.unexpected_overlaps.length,
      console_error_count: probe.console_errors.length,
      page_error_count: probe.page_errors.length,
      failed_resource_count: probe.failed_resources.length,
      blocked_write_request_count: probe.blocked_write_requests.length,
      settle_error_count: probe.settle_errors.length,
      cssom_error_count: probe.cssom_errors.length,
      unlinked_visible_resource_count: probe.unlinked_visible_resources.length,
      undecoded_visible_raster_count: probe.undecoded_visible_rasters.length,
      dialog_count: probe.dialogs.length,
      popup_count: probe.popups.length,
      visible_resource_count: probe.visible_resources.length,
      device_emulation_error_count: probe.emulation_errors.length,
    },
  });
  await context.close();
  return result;
}

function casePasses(caseValue) {
  const probe = caseValue.probe;
  return (
    caseValue.fatal_error === null &&
    caseValue.byte_identical_repeat_capture === true &&
    typeof caseValue.screenshot_pixel_sha256 === "string" &&
    caseValue.screenshot_pixel_sha256 === caseValue.repeat_pixel_sha256 &&
    Number(probe.horizontal_overflow_px) <= 1 &&
    probe.missing_required_elements.length === 0 &&
    (probe.duplicate_required_elements ?? []).length === 0 &&
    probe.overflow_elements.length === 0 &&
    probe.unexpected_overlaps.length === 0 &&
    probe.console_errors.length === 0 &&
    probe.page_errors.length === 0 &&
    probe.failed_resources.length === 0 &&
    probe.blocked_write_requests.length === 0 &&
    probe.settle_errors.length === 0 &&
    probe.cssom_errors.length === 0 &&
    (probe.emulation_errors ?? []).length === 0 &&
    probe.unlinked_visible_resources.length === 0 &&
    probe.undecoded_visible_rasters.length === 0 &&
    probe.visible_resources.every((resource) => resource.loaded === true)
  );
}

function boundaryViewports(widthBreakpoints, heightBreakpoints, existing, fixedWidth, fixedHeight) {
  const keys = new Set(
    existing.map((viewport) => `${viewport.width}:${viewport.height}:${viewport.base_dpr}:${viewport.zoom_percent}:${viewport.text_zoom_percent}`),
  );
  const result = [];
  for (const breakpoint of widthBreakpoints) {
    for (const width of [breakpoint - 1, breakpoint, breakpoint + 1]) {
      if (width <= 0) continue;
      const key = `${width}:${fixedHeight}:1:100:100`;
      if (keys.has(key)) continue;
      keys.add(key);
      result.push(
        normalizeViewport(
          {
            id: `breakpoint-${breakpoint}-${width}`,
            class: "breakpoint-boundary",
            width,
            height: fixedHeight,
            base_dpr: 1,
            zoom_percent: 100,
            text_zoom_percent: 100,
          },
          existing.length + result.length,
        ),
      );
    }
  }
  for (const breakpoint of heightBreakpoints) {
    for (const height of [breakpoint - 1, breakpoint, breakpoint + 1]) {
      if (height <= 0) continue;
      const key = `${fixedWidth}:${height}:1:100:100`;
      if (keys.has(key)) continue;
      keys.add(key);
      result.push(
        normalizeViewport(
          {
            id: `height-breakpoint-${breakpoint}-${height}`,
            class: "breakpoint-boundary",
            width: fixedWidth,
            height,
            base_dpr: 1,
            zoom_percent: 100,
            text_zoom_percent: 100,
          },
          existing.length + result.length,
        ),
      );
    }
  }
  return result;
}

function sweepWidths(configuration, breakpoints) {
  const range = configuration.max_width - configuration.min_width;
  const minimumStepForCap = Math.max(
    1,
    Math.ceil(range / Math.max(1, configuration.max_samples_per_state - 1)),
  );
  const effectiveStep = Math.max(configuration.requested_step_px, minimumStepForCap);
  const widths = new Set();
  for (
    let width = configuration.min_width;
    width <= configuration.max_width;
    width += effectiveStep
  ) {
    widths.add(width);
  }
  widths.add(configuration.max_width);
  for (const breakpoint of breakpoints) {
    for (const width of [breakpoint - 1, breakpoint, breakpoint + 1]) {
      if (width >= configuration.min_width && width <= configuration.max_width) widths.add(width);
    }
  }
  return { widths: [...widths].sort((left, right) => left - right), effectiveStep };
}

async function collectSweep({ browser, plan, breakpoints, trace }) {
  const configuration = plan.continuous_sweep;
  if (!configuration.enabled) {
    return {
      harness_collected: true,
      enabled: false,
      complete: false,
      reason: "disabled-by-capture-config",
      samples: [],
    };
  }
  const { widths, effectiveStep } = sweepWidths(configuration, breakpoints);
  const samples = [];
  const errors = [];
  for (const stateId of configuration.state_ids) {
    const stateValue = plan.states.find((state) => state.id === stateId);
    const viewport = normalizeViewport(
      {
        id: `sweep-${stateId}`,
        class: "continuous-sweep",
        width: widths[0],
        height: configuration.height,
        dpr: 1,
        zoom_percent: 100,
      },
      0,
    );
    const context = await createContext(browser, plan, viewport, stateValue);
    const page = await context.newPage();
    const eventState = attachPageRecorders(page, trace, `sweep-${stateId}`);
    try {
      let consoleErrorOffset = 0;
      let requestFailureOffset = 0;
      let resourceErrorOffset = 0;
      await installNetworkSafety(context, page, plan, eventState);
      await page.goto(plan.target_url, { waitUntil: "domcontentloaded", timeout: plan.navigation_timeout_ms });
      await applyStateActions(page, stateValue, plan.navigation_timeout_ms);
      await settlePage(page, plan);
      let previousSignature = null;
      for (const width of widths) {
        await page.setViewportSize({ width, height: configuration.height });
        const settleErrors = await settlePage(page, plan, { waitForNetwork: false });
        const requiredForState = plan.required_elements.filter((element) =>
          element.states.includes(stateId),
        );
        const probe = await collectBrowserProbe(page, requiredForState, { decodeImages: false });
        annotateConditionalQueries(probe);
        const signatureInput = {
          required_elements: probe.required_elements.map((element) => ({
            name: element.name,
            visible: element.visible,
            clipped: element.clipped,
            rect: element.rect,
          })),
          matched_media_queries: probe.media_queries
            .filter((query) => query.matches)
            .map((query) => query.query)
            .sort(),
          horizontal_overflow_px: probe.horizontal_overflow_px,
        };
        const layoutSignature = sha256Hex(Buffer.from(stableStringify(signatureInput), "utf8"));
        const sample = {
          state_id: stateId,
          width,
          height: configuration.height,
          inner_width: probe.inner_width,
          inner_height: probe.inner_height,
          horizontal_overflow_px: probe.horizontal_overflow_px,
          missing_required_elements: probe.missing_required_elements,
          clipped_required_elements: probe.required_elements
            .filter((element) => element.clipped)
            .map((element) => element.name),
          overflow_elements: probe.overflow_elements,
          unexpected_overlaps: probe.unexpected_overlaps,
          failed_resources: [
            ...eventState.request_failures.slice(requestFailureOffset),
            ...eventState.resource_errors.slice(resourceErrorOffset),
          ],
          console_errors: eventState.console_errors.slice(consoleErrorOffset),
          required_element_geometry: probe.required_elements.map((element) => ({
            name: element.name,
            visible: element.visible,
            clipped: element.clipped,
            rect: element.rect,
          })),
          matched_media_queries: signatureInput.matched_media_queries,
          layout_signature: layoutSignature,
          changed_from_previous_sample: previousSignature !== null && previousSignature !== layoutSignature,
          settle_errors: settleErrors,
        };
        consoleErrorOffset = eventState.console_errors.length;
        requestFailureOffset = eventState.request_failures.length;
        resourceErrorOffset = eventState.resource_errors.length;
        previousSignature = layoutSignature;
        samples.push(sample);
        await trace.record("sweep_sample", {
          state: stateId,
          width,
          layout_signature: layoutSignature,
          horizontal_overflow_px: probe.horizontal_overflow_px,
        });
      }
    } catch (error) {
      errors.push({ state: stateId, name: error.name, message: error.message });
    } finally {
      await Promise.allSettled([...eventState.pending]);
      await context.close();
    }
  }
  return {
    harness_collected: true,
    enabled: true,
    complete: errors.length === 0,
    min_width: configuration.min_width,
    max_width: configuration.max_width,
    height: configuration.height,
    requested_step_px: configuration.requested_step_px,
    effective_step_px: effectiveStep,
    state_ids: configuration.state_ids,
    breakpoint_boundary_widths: breakpoints.flatMap((breakpoint) => [breakpoint - 1, breakpoint, breakpoint + 1]).filter((width) => width >= configuration.min_width && width <= configuration.max_width),
    sample_count: samples.length,
    samples,
    errors,
  };
}

export function manifestAttestation(payload, scriptSha256) {
  const canonicalPayload = Buffer.from(stableStringify(payload), "utf8");
  const payloadSha256 = sha256Hex(canonicalPayload);
  const attestationSha256 = sha256Hex(
    Buffer.from(`${ATTESTATION_MAGIC}${scriptSha256}\0${payloadSha256}`, "utf8"),
  );
  return {
    algorithm: "sha256-canonical-json-v1",
    payload_sha256: payloadSha256,
    attestation_sha256: attestationSha256,
  };
}

export function verifyManifestAttestation(manifest) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) return false;
  const { collector_attestation: claimed, ...payload } = manifest;
  if (!claimed || typeof claimed !== "object") return false;
  const scriptSha = payload.collector?.script_sha256;
  if (typeof scriptSha !== "string") return false;
  const expected = manifestAttestation(payload, scriptSha);
  return (
    claimed.algorithm === expected.algorithm &&
    claimed.payload_sha256 === expected.payload_sha256 &&
    claimed.attestation_sha256 === expected.attestation_sha256
  );
}

async function writeJsonAtomic(destination, payload) {
  const temporary = `${destination}.tmp-${process.pid}-${randomUUID()}`;
  await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, { flag: "wx" });
  await rename(temporary, destination);
}

function validateRunId(raw) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(raw)) {
    throw new Error("run id must be 1-128 safe characters: letters, numbers, dot, underscore, or hyphen");
  }
  return raw;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderReviewIndex(runId, route, cases) {
  const cards = cases
    .map((caseValue) => {
      const viewport = caseValue.viewport;
      const label = `${caseValue.state_id} · ${caseValue.class} · ${viewport.width}×${viewport.height}`;
      return `<article class="capture" data-state="${escapeHtml(caseValue.state_id)}" data-class="${escapeHtml(caseValue.class)}">
  <h2>${escapeHtml(label)}</h2>
  <p>zoom ${escapeHtml(viewport.zoom_percent)}% · text ${escapeHtml(viewport.text_zoom_percent)}% · base DPR ${escapeHtml(viewport.base_dpr)} · actual DPR ${escapeHtml(viewport.dpr)}</p>
  <div class="pair">
    <figure><a href="${escapeHtml(caseValue.screenshot)}"><img loading="lazy" decoding="async" src="${escapeHtml(caseValue.screenshot)}" alt="${escapeHtml(label)} primary capture"></a><figcaption>primary</figcaption></figure>
    <figure><a href="${escapeHtml(caseValue.repeat_screenshot)}"><img loading="lazy" decoding="async" src="${escapeHtml(caseValue.repeat_screenshot)}" alt="${escapeHtml(label)} repeat capture"></a><figcaption>exact repeat</figcaption></figure>
  </div>
  <dl>
    <dt>Case</dt><dd>${escapeHtml(caseValue.id)}</dd>
    <dt>Pixel SHA-256</dt><dd><code>${escapeHtml(caseValue.screenshot_pixel_sha256)}</code></dd>
    <dt>Repeat SHA-256</dt><dd><code>${escapeHtml(caseValue.repeat_pixel_sha256)}</code></dd>
    <dt>Byte-stable</dt><dd>${caseValue.byte_identical_repeat_capture ? "yes" : "NO"}</dd>
  </dl>
</article>`;
    })
    .join("\n");
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pixel Precise UI review · ${escapeHtml(runId)}</title>
<style>
:root{color-scheme:dark;font:14px/1.45 ui-sans-serif,system-ui,sans-serif;background:#0b0d11;color:#edf1f7}*{box-sizing:border-box}body{margin:0;padding:24px}header{position:sticky;top:0;z-index:2;margin:-24px -24px 24px;padding:18px 24px;background:#0b0d11ee;border-bottom:1px solid #343a46;backdrop-filter:blur(12px)}h1{margin:0;font-size:20px}header p{margin:5px 0 0;color:#aab3c2}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:20px}.capture{min-width:0;padding:14px;border:1px solid #343a46;border-radius:10px;background:#141820}.capture h2{margin:0 0 4px;font-size:15px}.capture>p{margin:0 0 12px;color:#aab3c2}.pair{display:grid;grid-template-columns:1fr 1fr;gap:8px}.pair figure{min-width:0;margin:0}.pair figcaption{padding-top:4px;text-align:center;color:#aab3c2}.capture img{display:block;width:100%;height:auto;max-height:520px;object-fit:contain;object-position:top;background:#fff;border:1px solid #343a46}.capture dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 10px;margin:12px 0 0}.capture dt{color:#aab3c2}.capture dd{min-width:0;margin:0;overflow-wrap:anywhere}.capture code{font-size:11px;color:#b8e2ff}
</style>
</head>
<body>
<header><h1>Responsive capture review</h1><p>Run ${escapeHtml(runId)} · route ${escapeHtml(route)} · ${cases.length} immutable browser captures. Visual-review records remain pending until separately signed off.</p></header>
<main class="grid">${cards}</main>
</body>
</html>
`;
}

function defaultRunId(inputFingerprint) {
  const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
  return `ppui-${stamp}-${inputFingerprint.slice(0, 12)}-${randomUUID().slice(0, 8)}`;
}

async function writeCaseRunMetadata(outputDir, manifest) {
  const directory = path.join(outputDir, "run-metadata");
  await mkdir(directory, { recursive: true });
  for (const caseValue of manifest.cases) {
    const payload = {
      schema_version: SCHEMA_VERSION,
      collector: manifest.collector,
      run: {
        ...manifest.run,
        state: caseValue.state_id,
        capture_case_id: caseValue.id,
        capture_screenshot_file_sha256: caseValue.screenshot_file_sha256,
        capture_repeat_screenshot_file_sha256: caseValue.repeat_screenshot_file_sha256,
        capture_screenshot_pixel_sha256: caseValue.screenshot_pixel_sha256,
        capture_repeat_pixel_sha256: caseValue.repeat_pixel_sha256,
        evidence_payload_sha256: manifest.collector_attestation.payload_sha256,
      },
    };
    await writeJsonAtomic(path.join(outputDir, caseValue.run_metadata), payload);
  }
}

export async function runCapture(options) {
  const configPath = path.resolve(nonEmptyString(options.config, "--config"));
  const codeRoot = path.resolve(nonEmptyString(options.codeRoot, "--code-root"));
  const rawConfig = await loadJson(configPath, "Capture config");
  const commonMatrix = await loadJson(MATRIX_PATH, "Shared responsive matrix");
  const matrixSha256 = sha256Hex(await readFile(MATRIX_PATH));
  const plan = normalizePlan(rawConfig, { ...options, commonMatrix });
  if (!Number.isFinite(Date.parse(plan.fixed_time))) {
    throw new Error("fixed_time must be an ISO-compatible date/time");
  }
  if (!["chromium", "firefox", "webkit"].includes(plan.browser)) {
    throw new Error("browser must be chromium, firefox, or webkit");
  }
  const configSha256 = sha256Hex(await readFile(configPath));
  const ledger = await loadLedger(options.assetLedger ?? rawConfig.asset_ledger);
  const codeTreeStart = await fingerprintCodeTree(codeRoot);
  const manifestStates = stateEvidence(plan.states);
  const stateSetHash = sha256Hex(Buffer.from(stableStringify(manifestStates), "utf8"));
  const inputPayload = {
    schema_version: SCHEMA_VERSION,
    profile: PROFILE_NAME,
    route: plan.route,
    target_url: plan.target_url,
    reference_pixel_sha256: plan.reference_pixel_sha256,
    color_profile: plan.color_profile,
    color_scheme: plan.color_scheme,
    fixed_time: plan.fixed_time,
    locale: plan.locale,
    timezone_id: plan.timezone_id,
    browser: plan.browser,
    required_elements: plan.required_elements,
    states: plan.states,
    viewports: plan.viewports,
    continuous_sweep: plan.continuous_sweep,
    capture_breakpoint_boundaries: plan.capture_breakpoint_boundaries,
    asset_ledger_sha256: ledger.sha256,
    config_file_sha256: configSha256,
    common_matrix_sha256: matrixSha256,
  };
  const inputFingerprint = sha256Hex(Buffer.from(stableStringify(inputPayload), "utf8"));
  const runId = validateRunId(options.runId ?? rawConfig.run_id ?? defaultRunId(inputFingerprint));
  const playwright = await resolvePlaywright(codeRoot);
  const browserType = playwright.module[plan.browser];
  if (!browserType || typeof browserType.launch !== "function") {
    throw new Error(`Target Playwright package does not expose browser '${plan.browser}'`);
  }
  const outputDir = await ensureNewOutputDirectory(
    options.outputDir ?? path.join(codeRoot, "captures", runId),
    codeRoot,
  );
  await mkdir(path.join(outputDir, "screenshots"), { recursive: true });
  const tracePath = path.join(outputDir, "capture-trace.jsonl");
  const traceHandle = await open(tracePath, "wx");
  const trace = new TraceWriter(traceHandle, tracePath);
  const startedAt = nowIso();
  await trace.record("run_started", {
    run_id: runId,
    code_tree_hash: codeTreeStart.sha256,
    input_fingerprint: inputFingerprint,
    route: plan.route,
    state_set_hash: stateSetHash,
  });
  const scriptSha256 = sha256Hex(await readFile(SCRIPT_PATH));
  let browser;
  try {
    browser = await browserType.launch({
      headless: true,
      args:
        plan.browser === "chromium"
          ? [
              "--disable-background-networking",
              "--disable-default-apps",
              "--disable-extensions",
              "--disable-sync",
              "--force-color-profile=srgb",
              "--no-first-run",
            ]
          : [],
    });
  } catch (error) {
    await trace.record("run_failed", {
      run_id: runId,
      stage: "browser_launch",
      message: error.message,
    });
    await trace.close();
    throw error;
  }
  let browserVersion;
  let cases = [];
  let sweep;
  const mediaAggregate = new Map();
  const collectionErrors = [];
  try {
    browserVersion = browser.version();
    let ordinal = 0;
    for (const stateValue of plan.states) {
      for (const viewport of plan.viewports) {
        ordinal += 1;
        const caseValue = await captureCase({
          browser,
          plan,
          viewport,
          stateValue,
          outputDir,
          trace,
          ledger,
          caseOrdinal: ordinal,
          runId,
        });
        cases.push(caseValue);
        mergeMediaEvidence(mediaAggregate, caseValue);
      }
    }
    let discoveredMediaQueries = [...mediaAggregate.values()].sort((left, right) =>
      `${left.source}|${left.query}`.localeCompare(`${right.source}|${right.query}`),
    );
    let breakpoints = [...new Set(discoveredMediaQueries.flatMap((query) => query.extracted_boundaries.filter((item) => item.dimension === "width").map((item) => item.boundary_value)))].sort((left, right) => left - right);
    let heightBreakpoints = [...new Set(discoveredMediaQueries.flatMap((query) => query.extracted_boundaries.filter((item) => item.dimension === "height").map((item) => item.boundary_value)))].sort((left, right) => left - right);
    if (plan.capture_breakpoint_boundaries) {
      const additional = boundaryViewports(
        breakpoints,
        heightBreakpoints,
        plan.viewports,
        plan.breakpoint_width,
        plan.breakpoint_height,
      );
      for (const stateValue of plan.states) {
        for (const viewport of additional) {
          ordinal += 1;
          const caseValue = await captureCase({
            browser,
            plan,
            viewport,
            stateValue,
            outputDir,
            trace,
            ledger,
            caseOrdinal: ordinal,
            runId,
          });
          cases.push(caseValue);
          mergeMediaEvidence(mediaAggregate, caseValue);
        }
      }
      discoveredMediaQueries = [...mediaAggregate.values()].sort((left, right) =>
        `${left.source}|${left.query}`.localeCompare(`${right.source}|${right.query}`),
      );
      breakpoints = [...new Set(discoveredMediaQueries.flatMap((query) => query.extracted_boundaries.filter((item) => item.dimension === "width").map((item) => item.boundary_value)))].sort((left, right) => left - right);
      heightBreakpoints = [...new Set(discoveredMediaQueries.flatMap((query) => query.extracted_boundaries.filter((item) => item.dimension === "height").map((item) => item.boundary_value)))].sort((left, right) => left - right);
    }
    sweep = await collectSweep({ browser, plan, breakpoints, trace });
  } catch (error) {
    collectionErrors.push({ name: error.name, message: error.message, stack: error.stack ?? null });
    sweep = {
      harness_collected: true,
      enabled: plan.continuous_sweep.enabled,
      complete: false,
      samples: [],
      errors: collectionErrors,
    };
  } finally {
    await browser.close();
  }
  const codeTreeEnd = await fingerprintCodeTree(codeRoot);
  if (codeTreeEnd.sha256 !== codeTreeStart.sha256) {
    collectionErrors.push({
      gate: "code_tree_changed_during_capture",
      before: codeTreeStart.sha256,
      after: codeTreeEnd.sha256,
    });
  }
  const reviewIndexRelative = "review-index.html";
  const reviewIndexPath = path.join(outputDir, reviewIndexRelative);
  const reviewIndex = renderReviewIndex(runId, plan.route, cases);
  await writeFile(reviewIndexPath, reviewIndex, { encoding: "utf8", flag: "wx" });
  const reviewIndexSha256 = sha256Hex(Buffer.from(reviewIndex, "utf8"));
  await trace.record("review_index_written", {
    path: reviewIndexRelative,
    sha256: reviewIndexSha256,
    case_count: cases.length,
  });
  const generatedAt = nowIso();
  await trace.record("run_completed", {
    run_id: runId,
    generated_at: generatedAt,
    case_count: cases.length,
    collection_error_count: collectionErrors.length,
    code_tree_hash_after: codeTreeEnd.sha256,
  });
  const traceSha256 = await trace.close();
  const discoveredMediaQueries = [...mediaAggregate.values()].sort((left, right) =>
    `${left.source}|${left.query}`.localeCompare(`${right.source}|${right.query}`),
  );
  const breakpoints = [...new Set(discoveredMediaQueries.flatMap((query) => query.extracted_boundaries.filter((item) => item.dimension === "width").map((item) => item.boundary_value)))].sort((left, right) => left - right);
  const collector = {
    name: HARNESS_NAME,
    version: HARNESS_VERSION,
    kind: "browser-harness",
    harness_collected: true,
    script_sha256: scriptSha256,
    trace_path: "capture-trace.jsonl",
    trace_sha256: traceSha256,
    playwright_package: playwright.package_name,
    playwright_resolved_path: playwright.resolved_path,
    common_matrix_path: path.basename(MATRIX_PATH),
    common_matrix_sha256: matrixSha256,
    review_index_path: reviewIndexRelative,
    review_index_sha256: reviewIndexSha256,
  };
  const run = {
    run_id: runId,
    started_at: startedAt,
    generated_at: generatedAt,
    code_tree_hash: codeTreeStart.sha256,
    code_tree_hash_after: codeTreeEnd.sha256,
    code_tree_hash_algorithm: TREE_HASH_ALGORITHM,
    code_tree_entry_count: codeTreeStart.entry_count,
    input_fingerprint: inputFingerprint,
    input_fingerprint_algorithm: "sha256-canonical-json-v1",
    config_file_sha256: configSha256,
    asset_ledger_sha256: ledger.sha256,
    reference_pixel_sha256: plan.reference_pixel_sha256,
    route: plan.route,
    state_set_hash: stateSetHash,
    browser_name: plan.browser,
    browser_version: browserVersion,
    color_profile: plan.color_profile,
    color_scheme: plan.color_scheme,
    locale: plan.locale,
    timezone_id: plan.timezone_id,
    fixed_time: plan.fixed_time,
    platform: `${os.platform()}-${os.arch()}-${os.release()}`,
    node_version: process.version,
  };
  const manifestPayload = {
    schema_version: SCHEMA_VERSION,
    profile: PROFILE_NAME,
    collector,
    run,
    route: plan.route,
    required_elements: plan.required_elements,
    states: manifestStates,
    discovered_media_queries: discoveredMediaQueries,
    breakpoints,
    sweep,
    cases,
    collection_errors: collectionErrors,
    collection_passed:
      collectionErrors.length === 0 &&
      cases.length > 0 &&
      cases.every(casePasses) &&
      sweep.enabled === true &&
      sweep.complete === true,
  };
  const manifest = {
    ...manifestPayload,
    collector_attestation: manifestAttestation(manifestPayload, scriptSha256),
  };
  const manifestPath = path.join(outputDir, "responsive-evidence.json");
  await writeJsonAtomic(manifestPath, manifest);
  await writeCaseRunMetadata(outputDir, manifest);
  return { manifest, manifestPath, outputDir };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(usage());
    return 0;
  }
  const { manifest, manifestPath } = await runCapture(options);
  process.stdout.write(
    `${JSON.stringify(
      {
        manifest: manifestPath,
        run_id: manifest.run.run_id,
        code_tree_hash: manifest.run.code_tree_hash,
        input_fingerprint: manifest.run.input_fingerprint,
        case_count: manifest.cases.length,
        collection_passed: manifest.collection_passed,
      },
      null,
      2,
    )}\n`,
  );
  return manifest.collection_passed ? 0 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === SCRIPT_PATH) {
  main()
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error) => {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 2;
    });
}
