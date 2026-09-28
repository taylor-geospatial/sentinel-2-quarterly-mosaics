// Thirty-six Earths — nine years of cloudless Sentinel-2 mosaics, one frame
// per quarter, read straight out of object storage by the browser.
//
// There is no server here and no API behind the page. Every asset URL in this
// catalog is constructible from a quarter and an MGRS cell, the browse imagery
// is a Web Mercator COG whose internal blocks are map tiles, and the coverage
// table is a parquet file sorted so that one cell's history is one contiguous
// read. The app is those three facts wired to a scrubber.
//
// Layout of the code:
//   catalog.js    where the data is and how a URL is built
//   parquet.js    range reads over remote parquet (from the s2 explorer)
//   mosaic.js     the overview COG as a tile archive; the MapLibre protocol
//   timestack.js  36 quarters as stacked layers, and the staged prefetch
//   filmstrip.js  the scrubber, whose frames are the prefetch made visible
//   inspect.js    one cell's nine years and the URLs behind it
//   terrain.js    the raster-DEM choice, and why
// MapLibre v6 publishes named exports only — there is no default export, so
// the familiar `import maplibregl from ...` silently yields undefined.
import {
  MapLibreMap, NavigationControl, ScaleControl, addProtocol,
} from "https://cdn.jsdelivr.net/npm/maplibre-gl@6.11.2/+esm";
import { Protocol } from "https://cdn.jsdelivr.net/npm/pmtiles@4.5.0/+esm";
import {
  QUARTERS, footprintsUrl, yearOf, quarterNum, monthsOf, tilesUrl,
} from "./catalog.js";
import { registerMosaicProtocol } from "./mosaic.js";
import { TimeStack } from "./timestack.js";
import { Filmstrip } from "./filmstrip.js";
import { Inspector } from "./inspect.js";
import { addTerrain, setExaggeration, terrainSource, hillshadeLayer, DEFAULT_EXAGGERATION } from "./terrain.js";
import { SceneTier, SWAP_ZOOM } from "./scenetier.js";
import { sceneStats } from "./scene.js";
import { PRESETS } from "./bands.js";
import { warm } from "./parquet.js";

const $ = (id) => document.getElementById(id);

// --- URL state -------------------------------------------------------------
// Everything that makes a view is in the hash: camera, quarter, open cell. A
// link is the whole state, which is the only way a time scrub is shareable.
//   #q=2024.Q2/11.5/4.85/52.37/0/45/31UFT_0_0
function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  const parts = (h.get("q") || "").split("/");
  const num = (i, fallback) => (Number.isFinite(+parts[i]) && parts[i] !== "" ? +parts[i] : fallback);
  return {
    quarter: QUARTERS.includes(parts[0]) ? parts[0] : null,
    zoom: num(1, null),
    lng: num(2, null),
    lat: num(3, null),
    bearing: num(4, 0),
    pitch: num(5, 0),
    subtile: parts[6] || null,
  };
}

let writingHash = false;
function writeHash(map, quarter, subtile) {
  const c = map.getCenter();
  const parts = [
    quarter || "",
    map.getZoom().toFixed(2),
    c.lng.toFixed(4),
    c.lat.toFixed(4),
    map.getBearing().toFixed(0),
    map.getPitch().toFixed(0),
    subtile || "",
  ];
  while (parts.length && parts[parts.length - 1] === "") parts.pop();
  writingHash = true;
  history.replaceState(null, "", `${location.pathname}${location.search}#q=${parts.join("/")}`);
  queueMicrotask(() => { writingHash = false; });
}

// --- Map -------------------------------------------------------------------

const initial = readHash();

addProtocol("pmtiles", new Protocol().tile);

const map = new MapLibreMap({
  container: "map",
  // Globe is the opening state because the dataset is planetary and arriving
  // anywhere else would undersell it. MapLibre eases into mercator behaviour
  // on its own as the camera comes down, so there is no mode to manage.
  style: {
    version: 8,
    projection: { type: "globe" },
    sources: {},
    // Two flat layers: the ocean the globe sits in, and a slot marker that
    // every quarter layer is inserted before, so the stack order is fixed no
    // matter which quarters were admitted when.
    layers: [
      { id: "sea", type: "background", paint: { "background-color": "#0a1420" } },
      { id: "imagery-slot", type: "background",
        paint: { "background-color": "#000", "background-opacity": 0 } },
    ],
    sky: {
      "sky-color": "#0d1b30",
      "horizon-color": "#22384f",
      "fog-color": "#060a14",
      "fog-ground-blend": 0.6,
      "sky-horizon-blend": 0.5,
      "horizon-fog-blend": 0.4,
      "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 0.9, 6, 0.2, 9, 0],
    },
  },
  center: [initial.lng ?? 8, initial.lat ?? 28],
  zoom: initial.zoom ?? 1.4,
  bearing: initial.bearing ?? 0,
  pitch: initial.pitch ?? 0,
  maxPitch: 80,
  hash: false,
  attributionControl: { compact: false },
});

map.addControl(new NavigationControl({ visualizePitch: true }), "top-left");
map.addControl(new ScaleControl({ maxWidth: 90, unit: "metric" }), "top-left");

// --- Wiring ----------------------------------------------------------------

const stack = new TimeStack(map, { beforeId: "imagery-slot" });
const strip = new Filmstrip($("strip"), stack);
const inspector = new Inspector($("inspector"));
// Band COGs take over from the overview above SWAP_ZOOM. Its layers go under
// the grid so the cell outlines stay on top of the imagery they describe.
const scenes = new SceneTier(map, stack, addProtocol, { beforeId: "grid-fill" });

let openSubtile = initial.subtile;
let gridOn = true;

// Every tile the mosaic protocol decodes, for any quarter, is also a stroke in
// that quarter's filmstrip frame. Registered before the map asks for anything.
registerMosaicProtocol(addProtocol, (quarter, bitmap, ext) => {
  stack.noteTile(quarter);
  strip.paintTile(quarter, bitmap, ext);
});

function setSlate(quarter) {
  document.body.classList.toggle("is-empty", !quarter);
  if (!quarter) return;
  $("slate-year").textContent = yearOf(quarter);
  $("slate-q").textContent = `Q${quarterNum(quarter)}`;
  $("slate-months").textContent = monthsOf(quarter);
}

async function goTo(quarter, { instant = true } = {}) {
  const ok = await stack.show(quarter, { instant });
  if (!ok) return;
  setSlate(quarter);
  strip.setCurrent(quarter);
  scenes.setCurrentQuarter(quarter);
  scenes.refresh();
  inspector.setQuarter(quarter);
  writeHash(map, quarter, openSubtile);
}

strip.addEventListener("pick", (e) => goTo(e.detail.quarter, { instant: e.detail.instant }));
strip.addEventListener("playing", (e) => {
  $("play").setAttribute("aria-pressed", String(e.detail.playing));
  $("play").setAttribute("aria-label", e.detail.playing ? "Pause" : "Play through the quarters");
});

for (const p of Object.values(PRESETS)) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "t-toggle";
  b.textContent = p.label;
  b.dataset.preset = p.id;
  b.setAttribute("aria-pressed", String(p.id === "natural"));
  b.addEventListener("click", () => {
    scenes.setPreset(p.id);
    for (const el of $("presets").children) {
      el.setAttribute("aria-pressed", String(el.dataset.preset === p.id));
    }
  });
  $("presets").appendChild(b);
}

// The band combinations only apply to the band COGs; the overview is a
// pre-composited RGB and cannot be restretched. Saying so beats letting the
// buttons look broken at low zoom.
function updatePresetState() {
  const on = scenes.active;
  $("presets").classList.toggle("is-inactive", !on);
  $("presets").title = on
    ? "Applies to the 10 m band imagery"
    : `Zoom past ${SWAP_ZOOM} to read the 10 m bands; the browse layer is pre-composited`;
  for (const el of $("presets").children) el.disabled = !on;
}

$("play").addEventListener("click", () => strip.toggle());
$("prev").addEventListener("click", () => { strip.pause(); goTo(stack.step(-1), { instant: false }); });
$("next").addEventListener("click", () => { strip.pause(); goTo(stack.step(1), { instant: false }); });

// Arrow keys work anywhere on the page, not only when the strip has focus —
// scrubbing is the whole interface and should not need to be aimed at.
addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, [contenteditable]")) return;
  if (e.target.closest("#strip")) return;    // the strip handles its own
  const jump = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
  if (jump !== undefined) {
    e.preventDefault();
    strip.pause();
    goTo(stack.step(e.shiftKey ? jump * 4 : jump), { instant: false });
  } else if (e.key === " ") {
    e.preventDefault();
    strip.toggle();
  } else if (e.key === "Escape") {
    inspector.close();
    openSubtile = null;
    updateHighlight();
    writeHash(map, stack.current, null);
  }
});

// --- Terrain ---------------------------------------------------------------

$("exag").value = String(DEFAULT_EXAGGERATION);
$("exag").addEventListener("input", (e) => setExaggeration(map, Number(e.target.value)));

$("home").addEventListener("click", () => {
  strip.pause();
  map.flyTo({ center: [8, 28], zoom: 1.4, pitch: 0, bearing: 0, duration: 2200, essential: true });
});

$("grid").addEventListener("click", () => {
  gridOn = !gridOn;
  $("grid").setAttribute("aria-pressed", String(gridOn));
  for (const id of ["grid-line", "grid-fill"]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", gridOn ? "visible" : "none");
  }
});

// --- The MGRS grid, and clicking a cell ------------------------------------

function addFootprints() {
  if (map.getSource("footprints")) return;
  map.addSource("footprints", {
    type: "vector",
    url: `pmtiles://${footprintsUrl()}`,
    attribution: "MGRS mosaic grid",
  });
  // Subtle at mid zoom and gone at the extremes: a reference grid should tell
  // you how the data is cut without competing with what is in it.
  map.addLayer({
    id: "grid-fill",
    type: "fill",
    source: "footprints",
    "source-layer": "footprints",
    paint: {
      "fill-color": "#ff4fa3",
      "fill-opacity": ["case", ["boolean", ["feature-state", "picked"], false], 0.14, 0],
    },
  });
  map.addLayer({
    id: "grid-line",
    type: "line",
    source: "footprints",
    "source-layer": "footprints",
    paint: {
      "line-color": ["case",
        ["boolean", ["feature-state", "picked"], false], "#ff4fa3", "#6f8aa8"],
      "line-width": ["case",
        ["boolean", ["feature-state", "picked"], false], 2, 0.8],
      "line-opacity": ["interpolate", ["linear"], ["zoom"],
        2, 0, 4, 0.3, 8, 0.5, 12, 0.2],
    },
  });
}

let pickedId = null;
function updateHighlight() {
  if (pickedId !== null) {
    map.setFeatureState({ source: "footprints", sourceLayer: "footprints", id: pickedId },
      { picked: false });
    pickedId = null;
  }
  if (!openSubtile || !map.getSource("footprints")) return;
  const hits = map.querySourceFeatures("footprints", {
    sourceLayer: "footprints",
    filter: ["==", ["get", "subtile"], openSubtile],
  });
  if (hits.length && hits[0].id !== undefined) {
    pickedId = hits[0].id;
    map.setFeatureState({ source: "footprints", sourceLayer: "footprints", id: pickedId },
      { picked: true });
  }
}

map.on("click", (e) => {
  if (!map.getLayer("grid-fill")) return;
  // Query the line layer too: at low zoom a cell's fill is invisible but its
  // geometry is still there, and a click should land on the cell you can see.
  const feats = map.queryRenderedFeatures(e.point, { layers: ["grid-line", "grid-fill"] });
  if (!feats.length) return;
  const subtile = feats[0].properties?.subtile;
  if (!subtile) return;
  openSubtile = subtile;
  updateHighlight();
  inspector.show(subtile, stack.current, feats[0].properties || {});
  writeHash(map, stack.current, subtile);
});

map.on("mouseenter", "grid-line", () => { map.getCanvas().style.cursor = "pointer"; });
map.on("mouseleave", "grid-line", () => { map.getCanvas().style.cursor = ""; });

$("inspector").addEventListener("pickquarter", (e) => {
  strip.pause();
  goTo(e.detail.quarter, { instant: false });
});
$("inspector").addEventListener("closed", () => {
  openSubtile = null;
  updateHighlight();
  writeHash(map, stack.current, null);
});

// --- Boot ------------------------------------------------------------------

map.on("style.load", async () => {
  addTerrain(map, DEFAULT_EXAGGERATION);
  const hs = hillshadeLayer();
  if (hs) map.addLayer(hs, "imagery-slot");
  addFootprints();

  // Warm the coverage footer while the availability probes run, so the first
  // cell click finds it cached and costs only its column chunks.
  warm(tilesUrl());

  const state = $("intro-state");
  const available = await stack.discover();

  if (!available.length) {
    state.textContent = "No quarters are published at this URL yet.";
    state.style.color = "var(--live)";
    return;
  }

  const wanted = initial.quarter || available[available.length - 1];
  const quarter = stack.nearestAvailable(wanted);
  state.textContent = `${available.length} of ${QUARTERS.length} quarters published.`;

  // The strip needs a viewport box before it can place a tile, and the first
  // tiles land the moment a quarter is shown.
  strip.setExtent(map.getBounds());
  strip.refreshAvailability();
  updatePresetState();
  await goTo(quarter);

  $("intro").classList.add("gone");
  setTimeout(() => { $("intro").hidden = true; }, 800);

  if (openSubtile) inspector.show(openSubtile, quarter);
});

// A camera move invalidates every frame, since they all depict the old
// viewport. Wipe them and let the stack repaint as tiles arrive.
map.on("moveend", () => {
  strip.setExtent(map.getBounds());
  updateHighlight();
  scenes.refresh();
  updatePresetState();
  writeHash(map, stack.current, openSubtile);
});

map.on("sourcedata", () => updateHighlight());

// The band tier picks its cells out of the rendered MGRS grid, so it cannot
// choose them until those vector tiles have actually arrived. A moveend fires
// before that; idle fires after.
map.on("idle", () => {
  scenes.refresh();
  updatePresetState();
});

stack.addEventListener("unavailable", (e) => {
  const el = $("intro-state");
  if (el) el.textContent = `${e.detail.quarter} is not published yet.`;
});

addEventListener("hashchange", () => {
  if (writingHash) return;
  const s = readHash();
  if (s.quarter && s.quarter !== stack.current) goTo(s.quarter, { instant: false });
});

// Exposed for the browser console and for checking the app from a test
// harness: `s2.stack.available()`, `s2.map.getZoom()`.
globalThis.s2 = { map, stack, strip, inspector, scenes, sceneStats };
