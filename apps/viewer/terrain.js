// 3D terrain under the imagery.
//
// Two public raster-DEM sources were measured for this, both Terrarium-encoded
// so the MapLibre configuration is identical either way and switching is one
// query parameter:
//
//   Mapterhorn        https://tiles.mapterhorn.com/{z}/{x}/{y}.webp
//   AWS Terrain Tiles https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
//
// Mapterhorn is the default, for four reasons, in the order they mattered:
//
//   * Bytes per unit area, which is what a terrain layer actually costs on a
//     slow connection. Mapterhorn serves 512-pixel tiles; AWS serves 256. Over
//     the same ground at z6, one Mapterhorn tile measured 204-265 KB against
//     four AWS tiles totalling roughly 340 KB — fewer bytes and a quarter of
//     the requests.
//   * Delivery. Mapterhorn is behind Cloudflare and answered with
//     `cf-cache-status: HIT` over HTTP/2; the AWS bucket is a raw S3 origin on
//     HTTP/1.1 with no CDN in front, and its per-tile latency showed it.
//   * Resolution. Mapterhorn is Copernicus GLO-30 globally with national
//     LiDAR (1 m and finer across much of Europe, Japan, Australia) merged in
//     where it exists. AWS Terrain Tiles is largely SRTM and has not been
//     rebuilt in years. Terrain is the backdrop for 10 m imagery here, so the
//     difference is visible.
//   * It publishes a TileJSON that declares `encoding: terrarium` and carries
//     its own attribution string, so neither is hardcoded here.
//
// AWS keeps its place as a fallback because Mapterhorn is a small
// NLnet-funded project and this app should not go flat if it goes away.
// `?terrain=aws` switches, `?terrain=off` skips terrain entirely.
//
// Both require attribution and the app shows it; see index.html's footer and
// MapLibre's own attribution control, which picks up the TileJSON's string.

const params = new URLSearchParams(location.search);
export const TERRAIN_CHOICE = params.get("terrain") || "mapterhorn";

const SOURCES = {
  mapterhorn: {
    id: "terrain-mapterhorn",
    spec: { type: "raster-dem", url: "https://tiles.mapterhorn.com/tilejson.json" },
    // The TileJSON names tileSize 512, encoding terrarium and the attribution.
    label: "Mapterhorn",
  },
  aws: {
    id: "terrain-aws",
    spec: {
      type: "raster-dem",
      tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
      encoding: "terrarium",
      tileSize: 256,
      maxzoom: 13,
      attribution:
        '<a href="https://registry.opendata.aws/terrain-tiles/">Terrain Tiles</a> '
        + "(Mapzen, AWS Open Data)",
    },
    label: "AWS Terrain Tiles",
  },
};

// Exaggeration. The point of terrain here is to make a valley read as a valley
// while you watch it change colour across nine years — not to build a diorama.
// Past about 1.4 the imagery starts to smear over the slopes it is draped on
// and the 10 m detail that is the whole subject stops being legible, so the
// default is deliberately restrained and the slider below it is the user's.
export const DEFAULT_EXAGGERATION = 1.25;

export function terrainSource() {
  if (TERRAIN_CHOICE === "off") return null;
  return SOURCES[TERRAIN_CHOICE] || SOURCES.mapterhorn;
}

export function addTerrain(map, exaggeration = DEFAULT_EXAGGERATION) {
  const src = terrainSource();
  if (!src) return null;
  if (!map.getSource(src.id)) map.addSource(src.id, src.spec);
  map.setTerrain({ source: src.id, exaggeration });
  return src;
}

export function setExaggeration(map, exaggeration) {
  const src = terrainSource();
  if (!src) return;
  if (exaggeration <= 0) {
    map.setTerrain(null);
    return;
  }
  if (!map.getSource(src.id)) map.addSource(src.id, src.spec);
  map.setTerrain({ source: src.id, exaggeration });
}

// Hillshade under the imagery. It shows through wherever a quarter has no data
// — which, while the catalog is filling in, is most of the planet — so it has
// to carry the map on its own there, not merely texture it. Bright enough that
// land reads as land against the sea colour, and still dark enough to sit under
// 10 m imagery without tinting it.
export function hillshadeLayer() {
  const src = terrainSource();
  if (!src) return null;
  return {
    id: "hillshade",
    type: "hillshade",
    source: src.id,
    paint: {
      "hillshade-exaggeration": 0.6,
      "hillshade-shadow-color": "#0c1a29",
      "hillshade-highlight-color": "#93aec9",
      "hillshade-accent-color": "#1b2c3f",
      "hillshade-illumination-direction": 315,
    },
  };
}
