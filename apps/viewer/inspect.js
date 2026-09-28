// One mosaic cell, inspected: its whole nine-year history and the URLs of the
// bytes behind it.
//
// `coverage/tiles.parquet` is sorted subtile-major, which is the single
// decision that makes this panel cheap. One cell's 36 rows are a contiguous run
// in one or two row groups, so asking for a cell's history admits those groups
// by the `subtile` column's statistics and fetches a handful of column chunks
// in parallel — no scan, no index, no server. parquet.js reports the plan it
// used and the panel prints it, because "how much did that cost" is a fair
// question to ask of a page with no backend.
import { keyedRows } from "./parquet.js";
import {
  QUARTERS, YEARS, BANDS, BASE, tilesUrl, bandUrl, sceneDir, itemId,
  yearOf, quarterNum, monthsOf, startOf,
} from "./catalog.js";

// Only what the panel draws. Column chunks are the unit of a range read, so
// asking for a column nobody shows is a column's worth of bytes on the wire.
const COLUMNS = ["subtile", "mgrs_tile", "quarter", "bytes_total", "valid_fraction"];

const fmtBytes = (n) => {
  if (!Number.isFinite(Number(n))) return "—";
  const b = Number(n);
  const units = ["B", "KB", "MB", "GB"];
  let i = 0, v = b;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
};

const historyCache = new Map();

export function tileHistory(subtile) {
  if (!historyCache.has(subtile)) {
    historyCache.set(subtile, keyedRows({
      url: tilesUrl(), keyColumn: "subtile", key: subtile, columns: COLUMNS,
    }).catch((err) => ({ rows: [], plan: `history unavailable: ${err.message}` })));
  }
  return historyCache.get(subtile);
}

export class Inspector {
  constructor(root) {
    this.root = root;
    this.subtile = null;
    this.quarter = null;
    this.root.addEventListener("click", (e) => {
      const copy = e.target.closest("[data-copy]");
      if (copy) return this._copy(copy);
      if (e.target.closest("[data-close]")) this.close();
    });
  }

  async _copy(el) {
    const text = el.dataset.copy;
    try {
      await navigator.clipboard.writeText(text);
      const was = el.dataset.label || el.textContent;
      el.dataset.label = was;
      el.textContent = "Copied";
      setTimeout(() => { el.textContent = was; }, 1200);
    } catch {
      // Clipboard access can be refused; the URL is selectable text anyway.
      getSelection()?.selectAllChildren(el);
    }
  }

  close() {
    this.subtile = null;
    this.root.hidden = true;
    this.root.dispatchEvent(new CustomEvent("closed", { bubbles: true }));
  }

  // Show a cell. Renders the header immediately and fills the history in when
  // the range reads land, so a click never waits on the network to respond.
  async show(subtile, quarter, props = {}) {
    this.subtile = subtile;
    this.quarter = quarter;
    this.root.hidden = false;
    this.root.scrollTop = 0;
    this._renderShell(subtile, quarter, props);

    const { rows, plan } = await tileHistory(subtile);
    if (this.subtile !== subtile) return;   // a newer click won
    this._renderHistory(rows, plan);
  }

  // The quarter changed under an open panel: re-point the asset URLs without
  // re-reading anything.
  setQuarter(quarter) {
    this.quarter = quarter;
    if (!this.subtile || this.root.hidden) return;
    this._renderAssets();
    for (const el of this.root.querySelectorAll(".hq")) {
      el.classList.toggle("is-current", el.dataset.quarter === quarter);
    }
  }

  _renderShell(subtile, quarter, props) {
    const mgrs = props.mgrs_tile || subtile.split("_")[0];
    this.root.innerHTML = `
      <header class="ins-head">
        <div>
          <h2>${subtile}</h2>
          <p class="ins-sub">MGRS ${mgrs} · 100.08 km cell · 10 m</p>
        </div>
        <button class="ins-close" data-close type="button" aria-label="Close">&#10005;</button>
      </header>
      <section class="ins-history">
        <h3>Nine years</h3>
        <div class="hq-grid" data-history><p class="ins-wait">Reading this cell's history&hellip;</p></div>
        <div class="hq-years">${YEARS.map((y) => `<span>${y.slice(2)}</span>`).join("")}</div>
        <p class="ins-plan" data-plan></p>
      </section>
      <section class="ins-assets" data-assets></section>`;
    this._renderAssets();
  }

  _renderAssets() {
    const host = this.root.querySelector("[data-assets]");
    if (!host) return;
    const { subtile, quarter } = this;
    const id = itemId(quarter, subtile);
    const rows = BANDS.map((band) => {
      const url = bandUrl(quarter, subtile, band);
      const rel = url.startsWith(BASE) ? url.slice(BASE.length).replace(/^\//, "") : url;
      return `<li>
        <span class="band">${band}</span>
        <code class="url" title="${url}">${rel}</code>
        <button class="copy" type="button" data-copy="${url}">Copy</button>
      </li>`;
    }).join("");
    host.innerHTML = `
      <h3>${yearOf(quarter)} Q${quarterNum(quarter)} assets</h3>
      <p class="ins-sub">${monthsOf(quarter)} ${yearOf(quarter)} · composited from ${startOf(quarter)}</p>
      <dl class="ins-meta">
        <dt>Item</dt><dd><code>${id}</code></dd>
        <dt>Pixels</dt><dd>Int16, reflectance &times;10000, nodata &minus;32768</dd>
        <dt>Grid</dt><dd>10008 &times; 10008 at 10 m, in the cell's own UTM zone</dd>
      </dl>
      <ul class="asset-list">${rows}</ul>
      <button class="copy wide" type="button" data-copy="${sceneDir(quarter, subtile)}/">Copy the cell's directory URL</button>`;
  }

  _renderHistory(rows, plan) {
    const host = this.root.querySelector("[data-history]");
    const planEl = this.root.querySelector("[data-plan]");
    if (!host) return;
    if (!rows.length) {
      host.innerHTML = `<p class="ins-wait">No coverage rows for this cell yet.</p>`;
      if (planEl) planEl.textContent = plan || "";
      return;
    }
    const byQuarter = new Map(rows.map((r) => [r.quarter, r]));
    const sizes = rows.map((r) => Number(r.bytes_total)).filter(Number.isFinite);
    const max = sizes.length ? Math.max(...sizes) : 0;

    host.innerHTML = QUARTERS.map((q) => {
      const row = byQuarter.get(q);
      const bytes = row ? Number(row.bytes_total) : null;
      // The bar is the cell's size that quarter. Size is a real signal here:
      // these are lossless Int16 rasters, so a quarter that compressed small
      // is a quarter with little variation in it — heavy cloud persistence,
      // snow, or a mostly-nodata edge cell.
      const h = max && Number.isFinite(bytes) ? Math.max(3, Math.round((bytes / max) * 100)) : 0;
      const valid = row && row.valid_fraction != null
        ? ` · ${(Number(row.valid_fraction) * 100).toFixed(0)}% valid` : "";
      const title = row
        ? `${yearOf(q)} Q${quarterNum(q)} · ${fmtBytes(bytes)}${valid}`
        : `${yearOf(q)} Q${quarterNum(q)} · not published`;
      return `<button class="hq${row ? "" : " is-absent"}${q === this.quarter ? " is-current" : ""}"
        type="button" data-quarter="${q}" title="${title}" aria-label="${title}">
        <span class="hq-bar" style="height:${h}%"></span>
      </button>`;
    }).join("");

    host.querySelectorAll(".hq").forEach((el) => {
      el.addEventListener("click", () => {
        this.root.dispatchEvent(new CustomEvent("pickquarter", {
          bubbles: true, detail: { quarter: el.dataset.quarter },
        }));
      });
    });
    if (planEl) planEl.textContent = plan || "";
  }
}
