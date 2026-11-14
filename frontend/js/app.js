/* TerraSentinel 3D — Mission Control HUD logic (JPL Executive Edition) */

const API =
  location.protocol.startsWith("http")
    ? `${location.origin}/api`
    : "http://127.0.0.1:8000/api";

const COLORS = {
  earthquake: "#ff5555",
  volcano: "#ff9f43",
  flood: "#38bdf8",
  tsunami: "#c084fc",
  storm: "#ffd166",
  wildfire: "#ff8c42",
  other: "#94a3b8",
};

const REGION_BOXES = {
  world: [-90, -180, 90, 180],
  americas: [-58, -170, 72, -32],
  europe: [-36, -26, 62, 60],
  asia: [-48, 60, 70, 180],
};

const CAT_GROUPS = {
  seismic: ["earthquake", "tsunami"],
  hydro: ["flood", "volcano"],
};

let disasters = [];
let stormsIndex = null;         // id -> perfil de tormenta/tornado
let stormsPromise = null;
let satellites = [];
let satTrails = new Map();
const activeCats = new Set(["earthquake", "tsunami", "flood", "volcano", "storm"]);
const activeLayers = { heat: false, relief: true, infra: true, orbits: true, hydro: false };
let lastReport = null;
let lastTarget = null;
let lastSatLink = null;

/* ------------------- progreso de sincronización ------------------- */

const Sync = {
  _t: null,
  set(pct, detail) {
    const fill = document.getElementById("progress-fill");
    fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    document.getElementById("sync-pct").textContent = `SYNC ${Math.round(pct)}%`;
    document.getElementById("sync-detail").textContent = detail;
    clearTimeout(this._t);
    if (pct >= 100) {
      this._t = setTimeout(() => {
        fill.style.width = "0%";
        document.getElementById("sync-detail").textContent = "telemetría al día";
      }, 2600);
    }
  },
  fail(detail) {
    this.set(0, `⚠ ${detail}`);
  },
};

/* ----------------------------- globe ----------------------------- */

const globe = new Globe(document.getElementById("globe-container"), {
  animateIn: true,
})
  .globeImageUrl("vendor/img/earth-night.jpg")
  .bumpImageUrl("vendor/img/earth-topology.png")
  .backgroundImageUrl("vendor/img/night-sky.png")
  .atmosphereColor("#00ffd5")
  .atmosphereAltitude(0.18)
  .pointOfView({ lat: 22, lng: -30, altitude: 2.6 }, 0);

globe
  .htmlElementsData([])
  .htmlElement((d) => {
    const el = document.createElement("div");
    el.className = "marker";
    el.style.setProperty("--c", COLORS[d.category] || COLORS.other);
    el.innerHTML =
      `<div class="pulse"></div><div class="core"></div>` +
      `<div class="label">${escapeHtml(d.title)}</div>`;
    el.onclick = () => handleFocusTarget(d);
    return el;
  })
  .htmlLat((d) => d.lat)
  .htmlLng((d) => d.lon)
  .htmlAltitude(0.012);

globe
  .pointsData([])
  .pointLat((d) => d.lat)
  .pointLng((d) => d.lon)
  .pointAltitude((d) => d.alt)
  .pointColor((d) => d.color)
  .pointRadius((d) => d.r)
  .pointLabel((d) => d.label);

globe
  .pathsData([])
  .pathPoints((d) => d.pts)
  .pathColor((d) => d.color)
  .pathStroke(1.1)
  .pathDashLength(0.06)
  .pathDashGap(0.008)
  .pathDashAnimateTime(24000);

globe
  .arcsData([])
  .arcStartLat((d) => d.start[0])
  .arcStartLng((d) => d.start[1])
  .arcEndLat((d) => d.end[0])
  .arcEndLng((d) => d.end[1])
  .arcAltitude((d) => d.alt ?? 0.25)
  .arcColor((d) => d.color)
  .arcStroke(1.6)
  .arcDashLength(0.4)
  .arcDashGap(0.2)
  .arcDashAnimateTime(1400)
  .arcAltitudeAutoScale(0.6);

globe.onGlobeClick((coords) => {
  if (coords) handleFocusTarget({ lat: coords.lat, lon: coords.lng, title: "Zona escaneada" });
});

/* --------------------------- helpers ----------------------------- */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 2600);
}

async function api(path, opts) {
  const res = await fetch(`${API}${path}`, opts);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

function inRegion(ev) {
  const [s, w, n, e] = REGION_BOXES[document.getElementById("region-select").value];
  return ev.lat >= s && ev.lat <= n && ev.lon >= w && ev.lon <= e;
}

/* --------------------------- events ------------------------------ */

async function loadDisasters() {
  try {
    Sync.set(8, "contactando backend…");
    disasters = await api("/disasters?save=false");
    Sync.set(55, `${disasters.length} eventos EONET/USGS recibidos`);
    renderMarkers();
    document.getElementById("t-events").textContent = disasters.length;
    document.getElementById("left-status").textContent =
      `Última sincronización ${new Date().toLocaleTimeString()}`;
    setStatus(true);
    Sync.set(70, "marcadores posicionados");
    pollSatellites();
  } catch {
    setStatus(false);
    Sync.fail("backend no disponible");
    toast("⚠ Backend no disponible — reintentando…");
  }
}

function getStormsIndex() {
  if (!stormsPromise) {
    stormsPromise = api("/weather/storms")
      .then((d) => {
        stormsIndex = new Map(d.storms.map((s) => [s.external_id, s]));
        Sync.set(85, `${d.count} tormentas/tornados clasificados`);
        return stormsIndex;
      })
      .catch(() => (stormsIndex = new Map()));
  }
  return stormsPromise;
}

function renderMarkers() {
  const visible = disasters.filter(
    (ev) => activeCats.has(ev.category) && inRegion(ev)
  );
  globe.htmlElementsData(visible);
  document.getElementById("t-events").textContent = visible.length;
}

function setStatus(ok) {
  const el = document.getElementById("t-status");
  el.textContent = ok ? "● LIVE" : "○ OFFLINE";
  el.className = ok ? "ok" : "";
}

/* ------------------------- satellites ----------------------------- */

const SAT_COLOR = "#00ffd5";
const SAT_POLL_MS = 4000;
const TRAIL_MAX = 40;

async function pollSatellites() {
  try {
    Sync.set(88, "descargando efemérides CelesTrak…");
    const data = await api("/satellites/positions");
    satellites = data.positions;
    document.getElementById("t-sats").textContent = satellites.length;
    updateSatVis();
    Sync.set(94, `${satellites.length} satélites rastreados`);
  } catch {
    /* silencioso: el HUD sigue vivo sin telemetría orbital */
  }
}

function updateSatVis() {
  const pts = [];
  const paths = [];
  if (activeLayers.orbits) {
    for (const s of satellites) {
      pts.push({
        lat: s.lat,
        lon: s.lon,
        alt: s.altitude_km / 6371,
        color: SAT_COLOR,
        r: 0.28,
        label: `🛰 ${s.name}`,
      });
      let trail = satTrails.get(s.name) || [];
      trail.push([s.lat, s.lon, s.altitude_km / 6371]);
      if (trail.length > TRAIL_MAX) trail = trail.slice(-TRAIL_MAX);
      satTrails.set(s.name, trail);
      if (trail.length > 1) paths.push({ pts: trail, color: SAT_COLOR });
    }
  }
  if (activeLayers.heat) {
    for (const ev of disasters) {
      if (!["storm", "wildfire"].includes(ev.category)) continue;
      if (!activeCats.has(ev.category)) continue; // solo capas activas
      for (let i = 0; i < 5; i++) {
        const jitter = (Math.sin((ev.lat + i * 37) * 12.9898) * 43758.5453) % 1;
        const jitter2 = (Math.sin((ev.lon + i * 91) * 78.233) * 12345.6789) % 1;
        pts.push({
          lat: ev.lat + jitter * 1.6,
          lon: ev.lon + jitter2 * 1.6,
          alt: 0.006,
          color: i < 2 ? "#ff7a2f" : "#ffb056",
          r: 0.42,
          label: i === 0 ? `🔥 ${ev.title}` : "densidad térmica",
          category: ev.category,
          srcId: ev.id,
        });
      }
    }
  }
  globe.pointsData(pts);
  globe.pathsData(paths);
}

/* --------------- visor táctico de imagen satelital ---------------- */

const TAC = {
  main: { canvas: document.getElementById("tac-canvas"), pan: { x: 0, y: 0 } },
  modal: { canvas: document.getElementById("tac-modal-canvas"), pan: { x: 0, y: 0 } },
  fallback: document.getElementById("tac-fallback"),
  band: "optical",
  zoom: 13,
  MIN_ZOOM: 2,
  MAX_ZOOM: 17,
  target: { lat: 0, lon: 0 },
  satLink: null,
  tiles: new Map(),      // "z/y/x" -> {img, ok}
  pending: 0,
  open: false,
};

const BAND_FILTERS = {
  optical: "none",
  thermal:
    "grayscale(1) brightness(1.2) sepia(1) hue-rotate(-50deg) saturate(7) contrast(1.3)",
  change:
    "contrast(1.65) saturate(0.35) hue-rotate(115deg) brightness(1.1)",
};
const BAND_TAGS = {
  optical: "",
  thermal: "BANDA TÉRMICA · ANOMALÍAS DE CALOR",
  change: "ÍNDICE DE CAMBIO / DEVASTACIÓN",
};
const RING_COLORS = { critical: "#ff5555", high: "#ffb056", moderate: "#22d3ee" };

const TILE = 256;

function lon2tileX(lon, z) {
  return ((lon + 180) / 360) * Math.pow(2, z);
}
function lat2tileY(lat, z) {
  const c = Math.max(Math.min(Math.cos((Math.PI * lat) / 180), 1), 1e-6);
  const cl = Math.max(Math.min(lat, 85), -85);
  const rad = (Math.PI * cl) / 180;
  return (
    ((1 - Math.log(Math.tan(rad) + 1 / c) / Math.PI) / 2) * Math.pow(2, z)
  );
}

function safeNum(v, fallback = 0) {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const p1 = (Math.PI * lat1) / 180, p2 = (Math.PI * lat2) / 180;
  const dp = p2 - p1, dl = ((lon2 - lon1) * Math.PI) / 180;
  const h = Math.sin(dp / 2) ** 2 +
    Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.min(Math.sqrt(Math.max(h, 0)), 1));
}

function tacProxyUrl(z, y, x) {
  return `${API}/satellite-tile/${z}/${y}/${x}`;
}
function tacDirectUrl(z, y, x) {
  return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
}

function tacTelemetry(lat, lon, satLink) {
  document.getElementById("tac-lat").textContent =
    `LAT ${lat >= 0 ? "+" : ""}${safeNum(lat).toFixed(4)}`;
  document.getElementById("tac-lon").textContent =
    `LON ${lon >= 0 ? "+" : ""}${safeNum(lon).toFixed(4)}`;
  document.getElementById("tac-alt").textContent =
    `ALT ${satLink && Number.isFinite(satLink.closest?.altitude_km)
      ? Math.round(satLink.closest.altitude_km) + " km" : "—"}`;
  document.getElementById("tac-sat").textContent =
    `SAT ${satLink?.closest?.name || "—"}`;
  document.getElementById("tm-lat").textContent =
    `LAT ${safeNum(lat).toFixed(4)}`;
  document.getElementById("tm-lon").textContent =
    `LON ${safeNum(lon).toFixed(4)}`;
  document.getElementById("tm-sat").textContent =
    `SAT ${satLink?.closest?.name || "—"}`;
}

function drawTacFallback(ctx, w, h, lat, lon) {
  const g = ctx.createLinearGradient(0, 0, w, h);
  g.addColorStop(0, "#04101c");
  g.addColorStop(1, "#0a2318");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 90; i++) {
    ctx.beginPath();
    const x0 = Math.random() * w, y0 = Math.random() * h;
    ctx.moveTo(x0, y0);
    for (let j = 0; j < 5; j++) {
      ctx.lineTo(x0 + (Math.random() - 0.5) * 90, y0 + (Math.random() - 0.5) * 60);
    }
    ctx.strokeStyle = `rgba(0,255,213,${0.03 + Math.random() * 0.06})`;
    ctx.lineWidth = 0.6;
    ctx.stroke();
  }
}

function fetchTile(z, x, y, onReady) {
  if (y < 0 || y >= Math.pow(2, z)) return null;
  x = ((x % Math.pow(2, z)) + Math.pow(2, z)) % Math.pow(2, z);
  const key = `${z}/${y}/${x}`;
  let t = TAC.tiles.get(key);
  if (t) return t;
  if (TAC.tiles.size > 400) TAC.tiles.clear();
  t = { img: null, ok: false };
  TAC.tiles.set(key, t);
  TAC.pending++;
  const done = (img, ok) => {
    t.img = ok ? img : null;
    t.ok = ok;
    TAC.pending--;
    if (ok) onReady();
  };
  const img = new Image();
  img.onload = () => done(img, true);
  img.onerror = () => {
    const img2 = new Image();
    img2.onload = () => done(img2, true);
    img2.onerror = () => done(null, false);
    img2.src = tacDirectUrl(z, y, x);
  };
  img.src = tacProxyUrl(z, y, x);
  return t;
}

function metersPerPixel(lat, z) {
  return (156543.03392 * Math.cos((Math.PI * lat) / 180)) / Math.pow(2, z);
}

function renderTacView(view) {
  try {
    const ctx = view.canvas.getContext("2d");
    const w = view.canvas.width, h = view.canvas.height;
    const z = view.zoom ?? TAC.zoom;
    ctx.save();
    ctx.filter = BAND_FILTERS[TAC.band] || "none";
    ctx.fillStyle = "#02040a";
    ctx.fillRect(0, 0, w, h);

    const cx = lon2tileX(TAC.target.lon, z);
    const cy = lat2tileY(TAC.target.lat, z);
    const halfW = Math.ceil(w / 2 / TILE) + 1;
    const halfH = Math.ceil(h / 2 / TILE) + 1;
    let anyOk = false;

    for (let dy = -halfH; dy <= halfH; dy++) {
      for (let dx = -halfW; dx <= halfW; dx++) {
        const tx = Math.floor(cx) + dx;
        const ty = Math.floor(cy) + dy;
        const t = fetchTile(z, tx, ty, () => renderTacView(view));
        if (t && t.ok) {
          anyOk = true;
          ctx.drawImage(
            t.img,
            Math.round(w / 2 + view.pan.x + (tx - cx) * TILE),
            Math.round(h / 2 + view.pan.y + (ty - cy) * TILE),
            TILE, TILE
          );
        }
      }
    }
    ctx.restore();

    drawImpactRings(ctx, w, h, z, view.pan);

    const noTile = !anyOk && TAC.pending === 0;
    if (noTile) drawTacFallback(ctx, w, h, TAC.target.lat, TAC.target.lon);
    if (view === TAC.main) {
      TAC.fallback.textContent = noTile
        ? "SIN ENLACE · VISTA SINTÉTICA"
        : (BAND_TAGS[TAC.band] || "");
      TAC.fallback.classList.toggle("hidden", !noTile && !BAND_TAGS[TAC.band]);
    }
    document.getElementById("tm-zoom").textContent = `Z ${z}`;
  } catch (err) {
    console.error("TAC render:", err);
  }
}

function drawImpactRings(ctx, w, h, z, pan) {
  if (!lastReport?.risk_rings) return;
  const mpp = metersPerPixel(TAC.target.lat, z);
  const cx = w / 2 + safeNum(pan.x);
  const cy = h / 2 + safeNum(pan.y);
  for (const [zone, radiusKm] of [
    ["moderate", lastReport.risk_rings.moderate_km],
    ["high", lastReport.risk_rings.high_km],
    ["critical", lastReport.risk_rings.critical_km],
  ]) {
    const r = (safeNum(radiusKm) * 1000) / mpp;
    if (r < 2 || r > Math.max(w, h) * 12) continue;
    ctx.save();
    ctx.beginPath();
    ctx.setLineDash([8, 6]);
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = RING_COLORS[zone];
    ctx.globalAlpha = 0.85;
    ctx.lineWidth = zone === "critical" ? 2.5 : 1.5;
    ctx.shadowColor = RING_COLORS[zone];
    ctx.shadowBlur = 8;
    ctx.stroke();
    ctx.restore();
  }
}

function tacShowBand() {
  renderTacView(TAC.main);
  if (TAC.open) renderTacView(TAC.modal);
}

function tacLoad(lat, lon, satLink) {
  TAC.target = { lat: safeNum(lat), lon: safeNum(lon) };
  TAC.satLink = satLink;
  TAC.main.pan = { x: 0, y: 0 };
  TAC.modal.pan = { x: 0, y: 0 };
  tacTelemetry(lat, lon, satLink);
  renderTacView(TAC.main);
  if (TAC.open) renderTacView(TAC.modal);
}

function tacZoom(view, delta) {
  try {
    const z = view === TAC.main ? TAC.zoom : TAC.zoom;
    TAC.zoom = Math.min(Math.max(z + delta, TAC.MIN_ZOOM), TAC.MAX_ZOOM);
    view.pan = { x: 0, y: 0 };
    renderTacView(view);
    if (view !== TAC.main) renderTacView(TAC.main);
    else if (TAC.open) renderTacView(TAC.modal);
    document.getElementById("tm-zoom").textContent = `Z ${TAC.zoom}`;
  } catch (err) {
    console.error("TAC zoom:", err);
    toast("⚠ El zoom se mantuvo en un nivel seguro");
  }
}

/* -------- modal táctico a pantalla completa -------- */

function openTacModal() {
  TAC.open = true;
  TAC.modal.pan = { x: 0, y: 0 };
  const c = TAC.modal.canvas;
  c.width = Math.round(window.innerWidth * 0.88);
  c.height = Math.round(window.innerHeight * 0.82);
  document.getElementById("tac-modal").classList.remove("hidden");
  document.querySelectorAll(".band-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.band === TAC.band)
  );
  renderTacView(TAC.modal);
}

function closeTacModal() {
  TAC.open = false;
  document.getElementById("tac-modal").classList.add("hidden");
}

document.getElementById("tac-expand").addEventListener("click", openTacModal);
document.getElementById("tm-close").addEventListener("click", closeTacModal);
document.getElementById("tm-zin").addEventListener("click", () => tacZoom(TAC.modal, 1));
document.getElementById("tm-zout").addEventListener("click", () => tacZoom(TAC.modal, -1));
document.getElementById("tac-zin").addEventListener("click", () => tacZoom(TAC.main, 1));
document.getElementById("tac-zout").addEventListener("click", () => tacZoom(TAC.main, -1));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && TAC.open) closeTacModal();
});

TAC.modal.canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  tacZoom(TAC.modal, e.deltaY < 0 ? 1 : -1);
}, { passive: false });

TAC.modal.canvas.addEventListener("dblclick", closeTacModal);

(() => {
  let dragging = false, lx = 0, ly = 0;
  const c = TAC.modal.canvas;
  c.addEventListener("mousedown", (e) => {
    dragging = true; lx = e.clientX; ly = e.clientY;
    c.classList.add("dragging");
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    TAC.modal.pan.x += e.clientX - lx;
    TAC.modal.pan.y += e.clientY - ly;
    lx = e.clientX; ly = e.clientY;
    renderTacView(TAC.modal);
  });
  window.addEventListener("mouseup", () => {
    dragging = false;
    c.classList.remove("dragging");
  });
  c.addEventListener("dblclick", closeTacModal);
})();

TAC.main.canvas.addEventListener("dblclick", openTacModal);

/* ------------- tarjeta de enlace satelital ------------------------ */

function elevationAngle(sat, lat, lon) {
  const ground = haversineKm(lat, lon, safeNum(sat.lat), safeNum(sat.lon));
  return Math.atan2(safeNum(sat.altitude_km, 500), Math.max(ground, 1)) * (180 / Math.PI);
}

function updateSatLink(closest) {
  try {
    lastSatLink = closest;
    document.getElementById("sl-name").textContent = (closest?.name || "—").toUpperCase();
    const dist = Number.isFinite(closest?.distance_km)
      ? closest.distance_km
      : haversineKm(
          lastTarget.lat, lastTarget.lon,
          safeNum(closest?.lat), safeNum(closest?.lon)
        );
    const elev = elevationAngle(
      { lat: safeNum(closest?.lat), lon: safeNum(closest?.lon), altitude_km: safeNum(closest?.altitude_km, 500) },
      lastTarget.lat, lastTarget.lon
    );
    document.getElementById("sl-meta").textContent =
      `${Math.round(dist)} km · ELEV ${elev.toFixed(0)}° · ALT ${Math.round(safeNum(closest?.altitude_km, 500))} km`;
  } catch (err) {
    console.error("sat link:", err);
    document.getElementById("sl-meta").textContent = "Datos de enlace no disponibles";
  }
}

document.querySelectorAll(".band-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    TAC.band = btn.dataset.band;
    document.querySelectorAll(".band-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.band === TAC.band)
    );
    tacShowBand();
  });
});

/* ------------- tarjeta de enlace satelital ------------------------ */

function elevationAngle(sat, lat, lon) {
  const p1 = (Math.PI * lat) / 180, p2 = (Math.PI * sat.lat) / 180;
  const dl = ((sat.lon - lon) * Math.PI) / 180;
  const h = Math.sin((p2 - p1) / 2) ** 2 +
    Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  const ground = 6371 * 2 * Math.asin(Math.sqrt(h));
  return Math.atan2(sat.altitude_km, Math.max(ground, 1)) * (180 / Math.PI);
}

function updateSatLink(closest) {
  lastSatLink = closest;
  document.getElementById("sl-name").textContent = closest.name.toUpperCase();
  document.getElementById("sl-meta").textContent =
    `${Math.round(closest.distance_km)} km · ELEV ${elevationAngle(closest, lastTarget.lat, lastTarget.lon).toFixed(0)}° · ALT ${Math.round(closest.altitude_km)} km`;
}

/* ---------------- focus: closest sat + beam + flyTo --------------- */

async function handleFocusTarget(ev) {
  if (ev.category && !activeCats.has(ev.category) && ev.category !== "thermal") {
    toast("⚠ Capa oculta por filtros — actívala en el panel izquierdo");
    return;
  }
  lastTarget = ev;
  toast(`⌖ Analizando ${ev.title || "zona"}…`);
  Sync.set(20, `analizando ${ev.title || "zona"}…`);
  globe.pointOfView({ lat: ev.lat, lng: ev.lon, altitude: 1.1 }, 1200);

  const beam = [{ start: null, end: [ev.lat, ev.lon], alt: 0.25, color: ["rgba(0,255,213,0.9)", "rgba(124,58,237,0.2)"] }];

  if (ev.category === "storm") {
    Sync.set(40, "clasificando tipo-morfológica…");
    await getStormsIndex();
    const profile = stormsIndex?.get(ev.id);
    if (profile) {
      renderStormFicha(profile);
      drawStormCone(profile);
      Sync.set(70, "ficha de tormenta renderizada");
    } else {
      document.getElementById("report").innerHTML =
        `<div class="report-block"><b>TORMENTA SEVERA</b><br/>Perfil tipo-morfológico no disponible para este evento; mostrando análisis estándar.</div>`;
      const rep = await api(fetchImpactQuery(ev)).catch(() => null);
      if (rep) renderReport(rep);
    }
  } else {
    const closestP = api(`/satellites/closest?lat=${ev.lat}&lon=${ev.lon}`);
    const reportP = fetchImpactReport(ev).catch(() => null);
    const [closest, report] = await Promise.allSettled([closestP, reportP]);
    if (closest.status === "fulfilled" && closest.value) {
      const s = closest.value.closest;
      beam[0].start = [s.lat, s.lon];
      beam[0].alt = Math.max(s.altitude_km / 6371, 0.05);
      updateSatLink(s);
      toast(`🛰 ${s.name} — haz de escaneo activo (${Math.round(safeNum(s.distance_km))} km)`);
      globe.pointOfView({ lat: ev.lat, lng: ev.lon, altitude: 0.45 }, 3200);
    } else {
      toast("⚠ Sin cobertura satelital en este momento");
    }
    if (report.status === "fulfilled" && report.value) {
      renderReport(report.value);
      Sync.set(75, "diagnóstico situacional listo");
    }
  }

  globe.arcsData(beam);
  tacLoad(ev.lat, ev.lon, lastSatLink);
  Sync.set(100, "objetivo caracterizado");
}

function fetchImpactQuery(ev) {
  const q = `lat=${ev.lat}&lon=${ev.lon}`;
  return `/impact/assess?${q}&phenomenon=flood`;
}

async function fetchImpactReport(ev) {
  const q = `lat=${ev.lat}&lon=${ev.lon}`;
  const pheno = ["earthquake", "tsunami", "volcano", "flood"].includes(ev.category)
    ? ev.category
    : "earthquake";
  const mag = ev.magnitude ? `&magnitude=${ev.magnitude}` : "";
  return api(`/impact/assess?${q}&phenomenon=${pheno}${mag}`);
}

/* ------------------- ficha de tornado / tormenta ------------------- */

const TYPO_GLYPHS = {
  Wire: [7, 2, 0.9], Rope: [9, 3, 1.4], Needle: [5, 2, 0.2],
  "V-Shaped": [16, 4, -0.7], Cigar: [17, 11, 0.1], Cylinder: [14, 12, 0],
  Cone: [16, 4, 0], Landspout: [8, 7, 0.1], "Concave-Sided": [18, 6, 0.4],
  "Straight-Sided": [14, 10, 0], "Convex-Sided": [16, 11, 0.35],
  Segmented: [12, 6, 0.7], "Truncated Cone": [17, 9, 0],
  "Bulb Shaped": [10, 17, 0.25], Hourglass: [15, 15, 0.45],
  "Loop/Ring/Knot": [12, 5, 1.6],
};

function tornadoGlyphSVG(type) {
  const [tw, bw, bend] = TYPO_GLYPHS[type] || [12, 6, 0.4];
  const bx = bend > 0 ? 50 + bend * 22 : bend < 0 ? 50 + bend * 14 : 50;
  const d =
    `M ${50 - tw} 16 Q ${50 + bend * 34} 52, ${bx - bw} 88 ` +
    `L ${bx + bw} 88 Q ${50 + bend * 34} 52, ${50 + tw} 16 Z`;
  return `
  <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
    <rect x="0" y="0" width="100" height="16" fill="#232f3f"/>
    <path d="${d}" fill="#4d5e70" stroke="#8fa3b5" stroke-width="0.8"/>
    <ellipse cx="${bx}" cy="88" rx="${Math.max(bw + 4, 8)}" ry="3.5" fill="#31404f"/>
    ${bend > 0.8 ? `<path d="M ${bx + bw} 88 Q ${bx + bw + 9} 92, ${bx + bw + 3} 96" fill="none" stroke="#8fa3b5" stroke-width="1.6"/>` : ""}
  </svg>`;
}

const EF_COLORS = ["#22d3ee", "#ffe14d", "#ff9f43", "#ff7043", "#ff5555", "#c084fc"];

function renderStormFicha(p) {
  const dirRot = (p.direction_deg - 90 + 360) % 360;
  const efRow = [0, 1, 2, 3, 4, 5]
    .map(
      (i) =>
        `<span style="background:${EF_COLORS[i]}${i === p.ef_scale ? "" : ";color:#000;opacity:.3"}" ${i === p.ef_scale ? 'class="active" style="color:#000"' : ""}>EF${i}</span>`
    )
    .join("");
  const probs = Object.entries(p.regional_probability)
    .sort((a, b) => b[1] - a[1])
    .map(
      ([reg, v]) =>
        `<div class="rp-row"><span>${reg}</span><span class="rp-bar"><i style="width:${v}%"></i></span><b>${v}%</b></div>`
    )
    .join("");
  const kind = p.is_tornado ? "TORNADO" : "TORMENTA SEVERA CONVECTIVA";

  document.getElementById("report").innerHTML = "";
  const det = document.getElementById("report-details");
  det.classList.remove("hidden");

  document.getElementById("loc-block").innerHTML =
    `<h4>🌪 Clasificación tipo-morfológica</h4>` +
    `<div class="tornado-glyph-row">
       <div class="tornado-glyph">${tornadoGlyphSVG(p.typology)}</div>
       <div><b style="color:var(--accent);font-size:15px;letter-spacing:1px">${p.typology.toUpperCase()}</b><br/>
       <span class="muted">${p.typology_desc}</span></div>
     </div>` +
    `<p><b>${kind}</b> — ${escapeHtml(p.title)}</p>` +
    `<div class="ef-scale">${efRow}</div>` +
    `<div class="storm-meta">
       <span><b>${p.ef_category}</b> · ${p.ef_label}</span>
       <span><b>${p.wind_kmh} km/h</b> vientos</span>
       <span><b>⌀ ${p.diameter_km} km</b> diámetro</span>
       <span><b>${p.duration_min} min</b> duración estimada</span>
     </div>` +
    `<p class="muted">Comparativa: a diferencia de los huracanes (120–240 km/h, ⌀ 500–1800 km, días o semanas), este ${p.is_tornado ? `tornado ${p.ef_category} se forma sobre tierra en latitudes medias (20°–50°)` : "sistema convectivo se desarrolla en latitudes medias"} con vientos que pueden superar los 500 km/h y vida de minutos a horas.</p>`;

  document.getElementById("status-block").innerHTML =
    `<h4>🧭 Trayectoria prevista y firma radar</h4>` +
    `<p><span class="dir-arrow" style="display:inline-block;transform:rotate(${dirRot}deg);color:var(--accent)">➤</span>
     Rumbo <b>${p.direction_cardinal}</b> (${p.direction_deg}°) con trayectoria proyectada de <b>${p.projected_path_km} km</b>.</p>` +
    `<p class="muted"><b>Firma radar:</b> ${p.radar_signature}</p>`;

  document.getElementById("impact-block").innerHTML =
    `<h4>🗺 Proyección de afectación regional</h4>` +
    `<p>Región de desarrollo: <b>${p.continent}</b>. Probabilidad modelizada por continente:</p>` +
    `<div class="reg-prob">${probs}</div>` +
    `<p class="muted" style="margin-top:6px">${p.reference_note}</p>`;

  document.getElementById("recs-block").innerHTML =
    `<h4>✅ Datos y desastres asociados a esta categoría</h4>` +
    `<ul>` +
    `<li>Categoría ${p.ef_category} (${p.ef_label}): daños reportados históricamente desde "daños leves a tejados" (EF0) hasta "devastación total de estructuras resistentes" (EF5).</li>` +
    `<li>Riesgo principal: ${p.wind_kmh > 250 ? "estructuras colapsadas y proyectiles balísticos" : "daños en tejados, árboles caídos e interrupción de líneas eléctricas"}.</li>` +
    `<li>Seguimiento radar recomendado ante ${p.is_tornado ? "ecos de gancho persistentes" : "pulsos convectivos intensificados"}.</li>` +
    `</ul>`;
  renderTacView(TAC.main);
  if (TAC.open) renderTacView(TAC.modal);
}

function drawStormCone(p) {
  const [dLat, dLon] = destinationPoint(p.lat, p.lon, p.direction_deg, p.projected_path_km);
  globe.arcsData([
    {
      start: [p.lat, p.lon],
      end: [dLat, dLon],
      alt: 0.06,
      color: [p.is_tornado ? "rgba(255,85,85,0.9)" : "rgba(255,209,102,0.9)", "rgba(255,209,102,0.15)"],
    },
  ]);
}

function destinationPoint(lat, lon, bearingDeg, distKm) {
  const R = 6371;
  const br = (Math.PI * bearingDeg) / 180;
  const la = (Math.PI * lat) / 180, lo = (Math.PI * lon) / 180;
  const la2 = Math.asin(
    Math.sin(la) * Math.cos(distKm / R) +
      Math.cos(la) * Math.sin(distKm / R) * Math.cos(br)
  );
  const lo2 = lo + Math.atan2(
    Math.sin(br) * Math.sin(distKm / R) * Math.cos(la),
    Math.cos(distKm / R) - Math.sin(la) * Math.sin(la2)
  );
  return [(la2 * 180) / Math.PI, (lo2 * 180) / Math.PI];
}

/* ------------------------- informe ejecutivo ----------------------- */

const PHENO_LABEL = {
  earthquake: "sismo",
  tsunami: "tsunami",
  volcano: "erupción volcánica",
  flood: "inundación",
};

function riskPct(r) {
  const p = r.damage_probabilities.find((x) => x.zone === "critical");
  const popFactor = Math.min(r.infrastructure.estimated_population_at_risk / 2e6, 1);
  const score = (p?.damage_probability ?? 0.3) * 60 + popFactor * 30 +
    (r.anomalies.is_anomalous ? 10 : 0);
  return Math.min(Math.round(score), 99);
}

function statusTag(r, pct) {
  if (pct >= 75) return { cls: "tag-critical", label: "ZONA CRÍTICA / DEVASTACIÓN PROBABLE" };
  if (pct >= 50) return { cls: "tag-alert", label: "ALERTA — RIESGO ALTO" };
  if (pct >= 25) return { cls: "tag-transform", label: "EN TRANSFORMACIÓN / VIGILANCIA" };
  return { cls: "tag-stable", label: "SITUACIÓN ESTABLE" };
}

function narrative(r, loc, pct) {
  const pheno = PHENO_LABEL[r.phenomenon] || r.phenomenon;
  const mag = r.input_magnitude ? `de magnitud ${r.input_magnitude}` : "";
  const i = r.infrastructure;
  const parts = [];

  parts.push(
    `Se ha identificado un evento de tipo <b>${pheno}</b> ${mag} en las proximidades de ` +
    `<b>${loc.label}</b>. El perímetro de afectación inmediata se extiende hasta ` +
    `${r.risk_rings.critical_km} km del epicentro, con impacto moderado relevante hasta ` +
    `${r.risk_rings.moderate_km} km.`
  );

  const risks = [];
  if (i.cities.length) risks.push(`${i.cities.length} núcleos urbanos en el radio de análisis`);
  if (i.estimated_population_at_risk)
    risks.push(`aprox. <b>${i.estimated_population_at_risk.toLocaleString()}</b> personas expuestas`);
  if (i.dams.length) risks.push(`${i.dams.length} represas bajo observación estructural`);
  if (i.water.length) risks.push(`${i.water.length} cauces y cuerpos de agua (cuencas/acuíferos) vulnerables`);
  if (i.relief.length) risks.push(`${i.relief.length} formaciones de relieve relevantes para deslizamientos`);

  parts.push(
    `Sobre el terreno: ${risks.length ? risks.join("; ") + "." : "no se registran activos críticos catalogados en el radio evaluado; el impacto se concentra en terreno despoblado."} ` +
    (r.anomalies.is_anomalous
      ? `La serie temporal muestra <b>incremento anómalo de actividad</b> (${r.anomalies.activity_delta_pct}% sobre la media semanal), lo que sugiere una fase activa en evolución.`
      : `La actividad comparada con registros históricos recientes se mantiene dentro de parámetros nominales.`)
  );

  parts.push(
    `Probabilidad de daño estimada: <b>${pct}%</b> en el anillo crítico. ` +
    (i.roads.length
      ? `Se recomienda restringir el tránsito por las ${i.roads.length} vías principales identificadas dentro del radio moderado.`
      : `Mantener rutas de evacuación despejadas hacia el exterior del perímetro.`)
  );

  return parts;
}

async function renderReport(r) {
  lastReport = r;
  const loc = await api(`/geo/location?lat=${r.epicenter.lat}&lon=${r.epicenter.lon}`).catch(() => ({ label: "Zona remota" }));
  const pct = riskPct(r);
  const st = statusTag(r, pct);
  const paras = narrative(r, loc, pct);

  document.getElementById("report").innerHTML = `
    <div class="report-block">
      <h4>Diagnóstico situacional</h4>
      <span class="alert-tag ${st.cls}">${st.label}</span>
      <div class="severity-bar"><i style="width:${pct}%"></i></div>
      <div class="severity-labels"><span>BAJO</span><span>MODERADO</span><span>CRÍTICO</span></div>
      <div style="margin-top:6px"><span class="risk-pct">${pct}%</span> <span class="muted">de riesgo estimado · alerta ${r.alert_level.toUpperCase()}</span></div>
    </div>`;
  const det = document.getElementById("report-details");
  det.classList.remove("hidden");

  document.getElementById("loc-block").innerHTML =
    `<h4>📍 Ubicación exacta</h4><p>${escapeHtml(loc.label)}</p>` +
    `<p class="muted">${r.epicenter.lat.toFixed(4)}°, ${r.epicenter.lon.toFixed(4)}° · evento tipo ${PHENO_LABEL[r.phenomenon] || r.phenomenon}${r.input_magnitude ? ` de magnitud ${r.input_magnitude}` : ""}</p>`;

  document.getElementById("status-block").innerHTML =
    `<h4>🛡 Estado de afectación</h4><p>${paras[0]}</p>`;

  document.getElementById("impact-block").innerHTML =
    `<h4>🌏 Impacto sobre el terreno</h4><p>${paras[1]}</p><p>${paras[2]}</p>`;

  document.getElementById("recs-block").innerHTML =
    `<h4>✅ Plan de mitigación recomendado</h4><ul>` +
    r.recommendations.map((x) => `<li>${escapeHtml(x)}</li>`).join("") +
    `</ul>`;
  renderTacView(TAC.main);
  if (TAC.open) renderTacView(TAC.modal);
}

async function loadSnapshots() {
  const snaps = await api("/snapshots");
  const left = document.getElementById("snapshots");
  left.innerHTML = snaps.length
    ? snaps
        .map(
          (s) =>
            `<div>#${s.id} · ${new Date(s.captured_at).toLocaleString()} · ${s.event_count} eventos</div>`
        )
        .join("")
    : "Sin snapshots";
  for (const sel of ["cmp-a", "cmp-b"]) {
    const el = document.getElementById(sel);
    el.innerHTML = snaps
      .map((s) => `<option value="${s.id}">#${s.id} (${s.event_count} ev.)</option>`)
      .join("");
    el.selectedIndex = sel === "cmp-b" ? 0 : Math.min(1, snaps.length - 1);
  }
}

async function compareSnapshots() {
  const a = document.getElementById("cmp-a").value;
  const b = document.getElementById("cmp-b").value;
  if (!a || !b) return toast("Necesitas al menos 2 snapshots");
  const r = await api(`/snapshots/compare?id_a=${a}&id_b=${b}`);
  document.getElementById("cmp-result").innerHTML =
    `<div>Añadidos: <b style="color:#22d3ee">${r.added_count}</b> · Eliminados: <b style="color:#ff5555">${r.removed_count}</b></div>` +
    r.added
      .slice(0, 6)
      .map((e) => `<div style="color:#22d3ee">+ ${escapeHtml(e.title)}</div>`)
      .join("") +
    r.removed
      .slice(0, 6)
      .map((e) => `<div style="color:#ff5555">− ${escapeHtml(e.title)}</div>`)
      .join("");
  toast(`Comparación #${a} vs #${b}: +${r.added_count} / −${r.removed_count}`);
}

function exportReport() {
  if (!lastReport) return toast("Primero ejecuta un análisis de impacto");
  const blob = new Blob([JSON.stringify(lastReport, null, 2)], {
    type: "application/json",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `terrasentinel_report_${Date.now()}.json`;
  a.click();
  toast("⭳ Reporte exportado");
}

/* ------------------------- paneles colapsables --------------------- */

function setPanel(side, open) {
  const panel = document.getElementById(`panel-${side}`);
  const toggle = document.getElementById(`toggle-${side}`);
  panel.classList.toggle("collapsed", !open);
  toggle.classList.toggle("visible", !open);
}

document.querySelectorAll(".collapse-btn").forEach((btn) => {
  btn.addEventListener("click", () => setPanel(btn.dataset.panel, false));
});
document.getElementById("toggle-left").addEventListener("click", () => setPanel("left", true));
document.getElementById("toggle-right").addEventListener("click", () => setPanel("right", true));

/* ------------------------- boot ------------------------------------ */

setInterval(() => {
  document.getElementById("t-clock").textContent =
    new Date().toISOString().substr(11, 8);
}, 1000);
document.getElementById("t-clock").textContent =
  new Date().toISOString().substr(11, 8);

window.addEventListener("error", (e) => {
  const el = document.getElementById("t-status");
  if (el) { el.textContent = "○ ERROR"; el.className = ""; }
  console.error("TerraSentinel:", e.error || e.message);
});

function clearReportIfHidden() {
  if (lastTarget?.category && !activeCats.has(lastTarget.category)) {
    document.getElementById("report").innerHTML =
      `<div class="report-block"><b>CAPAS SIN DATOS VISIBLES</b><br/><span class="muted">El objetivo analizado pertenece a una capa desactivada. Marca su capa en el panel izquierdo para restaurar el análisis.</span></div>`;
    document.getElementById("report-details").classList.add("hidden");
  }
}

document.querySelectorAll('#filter-groups input').forEach((inp) => {
  inp.addEventListener("change", () => {
    if (inp.dataset.layer === "heat") {
      activeLayers.heat = inp.checked;
    } else if (inp.dataset.cat) {
      inp.checked ? activeCats.add(inp.dataset.cat) : activeCats.delete(inp.dataset.cat);
    } else if (inp.dataset.catGroup) {
      CAT_GROUPS[inp.dataset.catGroup].forEach((c) =>
        inp.checked ? activeCats.add(c) : activeCats.delete(c)
      );
    }
    renderMarkers();
    updateSatVis();
    clearReportIfHidden();
  });
});

document.querySelectorAll(".isolate-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const g = btn.dataset.group;
    // desactivar todas las capas de datos y aislar la elegida
    document.querySelectorAll('#filter-groups input').forEach((inp) => {
      if (inp.dataset.layer === "heat") {
        inp.checked = false; activeLayers.heat = false;
      } else {
        inp.checked = false;
      }
    });
    activeCats.clear();
    const inp = document.querySelector(
      `#filter-groups [data-layer="${g}"], #filter-groups [data-cat="${g}"], #filter-groups [data-cat-group="${g}"]`
    );
    if (inp) {
      inp.checked = true;
      if (g === "heat") activeLayers.heat = true;
      else if (CAT_GROUPS[g]) CAT_GROUPS[g].forEach((c) => activeCats.add(c));
      else activeCats.add(g);
    }
    renderMarkers();
    updateSatVis();
    clearReportIfHidden();
    toast(`⌖ Capa aislada: ${btn.closest(".filter-group").querySelector(".chk span")?.textContent.trim() || g}`);
  });
});

document.querySelectorAll("[data-layer]").forEach((inp) => {
  inp.addEventListener("change", () => {
    activeLayers[inp.dataset.layer] = inp.checked;
    updateSatVis();
  });
});

document.getElementById("region-select").addEventListener("change", renderMarkers);
document.getElementById("cmp-btn").addEventListener("click", compareSnapshots);
document.getElementById("export-btn").addEventListener("click", exportReport);
document.getElementById("live-btn").addEventListener("click", loadDisasters);

setInterval(loadDisasters, 30000);
setInterval(pollSatellites, SAT_POLL_MS);

loadDisasters();
pollSatellites();
(() => {
  // boot del visor: sin objetivo aún → pantalla sintética, no tiles de océano
  const c = TAC.main.canvas;
  drawTacFallback(c.getContext("2d"), c.width, c.height, 0, 0);
  TAC.fallback.textContent = "SELECCIONA UN OBJETIVO EN EL GLOBO";
  TAC.fallback.classList.remove("hidden");
})();
getStormsIndex();
loadSnapshots()
  .then(() => Sync.set(100, "sistema sincronizado"))
  .catch(() => {
    document.getElementById("snapshots").textContent =
      "⚠ Sin conexión con el backend — reintentando…";
    Sync.fail("snapshots no disponibles");
  });
