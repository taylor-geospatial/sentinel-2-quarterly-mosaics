// Where the data is, what a quarter is called, and how a URL into the product
// is built. Everything else in the app asks this module for a URL and never
// concatenates one itself, so pointing the viewer at a local tree is one
// query parameter and no code change.
//
// The product's keys are fully constructible — that is the single most
// important fact about this dataset for a static viewer. There is no API, no
// index to walk and nothing to search before a byte of imagery can be read:
// given a quarter and an MGRS subtile, every asset's URL is a string.

// The published product root. `?base=` overrides it, which is how the dev
// server (devserver.py) and any mirror are used.
const DEFAULT_BASE = "https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics";

const params = new URLSearchParams(location.search);
export const BASE = (params.get("base") || DEFAULT_BASE).replace(/\/+$/, "");

// The 4 million band COGs were published long before the catalog metadata
// beside them, so the two can legitimately live at different origins — a local
// build of the browse layer read against the real imagery, for instance.
// `?cogbase=` overrides just the imagery; by default they are the same place.
export const COG_BASE = (params.get("cogbase") || BASE).replace(/\/+$/, "");

// 2017 Q1 through 2025 Q4. The mirror carries `complete.json` markers for all
// 36; 2026 Q2 is still transferring upstream and is deliberately not here.
// A quarter is "YYYY.Qn" everywhere in this app and in the partition keys.
export const QUARTERS = [];
for (let year = 2017; year <= 2025; year++) {
  for (let q = 1; q <= 4; q++) QUARTERS.push(`${year}.Q${q}`);
}
export const YEARS = [...new Set(QUARTERS.map((q) => q.slice(0, 4)))];

export const yearOf = (quarter) => quarter.slice(0, 4);
export const quarterNum = (quarter) => Number(quarter.slice(6));
export const indexOf = (quarter) => QUARTERS.indexOf(quarter);

// The months a quarter composites, for the inspector. Not seasons: this is a
// planetary dataset and Q3 is midwinter in Patagonia, so the app never names a
// season anywhere.
const MONTHS = { 1: "Jan–Mar", 2: "Apr–Jun", 3: "Jul–Sep", 4: "Oct–Dec" };
export const monthsOf = (quarter) => MONTHS[quarterNum(quarter)];
export const startOf = (quarter) =>
  `${yearOf(quarter)}-${String((quarterNum(quarter) - 1) * 3 + 1).padStart(2, "0")}-01`;

// --- URLs into the product ------------------------------------------------
// mosaics/quarter=YYYY.Qn/ holds the per-quarter browse layer and metadata.
export const quarterDir = (quarter) => `${BASE}/mosaics/quarter=${quarter}`;
export const overviewUrl = (quarter) => `${quarterDir(quarter)}/overview.tif`;
export const itemsUrl = (quarter) => `${quarterDir(quarter)}/items.parquet`;
export const quarterThumbUrl = (quarter) => `${quarterDir(quarter)}/thumbnail.webp`;

// coverage/ is the viewer's own backend: one row per (subtile, quarter) and
// the MGRS mosaic grid as vector tiles.
export const tilesUrl = () => `${BASE}/coverage/tiles.parquet`;
export const footprintsUrl = () => `${BASE}/coverage/footprints.pmtiles`;

// The band COGs sit where they always have, beside the imagery they came from:
// {year}/{Qn}/{MGRS}_{i}_{j}/{band}.tif. Single-band Int16, nodata -32768,
// reflectance scaled by 10000, 10 m, 10008 px, in the subtile's own UTM zone.
export const BANDS = ["B02", "B03", "B04", "B08"];
export const sceneDir = (quarter, subtile) =>
  `${COG_BASE}/${yearOf(quarter)}/Q${quarterNum(quarter)}/${subtile}`;
export const bandUrl = (quarter, subtile, band) =>
  `${sceneDir(quarter, subtile)}/${band}.tif`;

// The upstream item id, preserved from CDSE so a row here can be looked up
// there. Derivable from the subtile and quarter, like everything else.
export const itemId = (quarter, subtile) =>
  `Sentinel-2_mosaic_${yearOf(quarter)}_Q${quarterNum(quarter)}_${subtile}`;
