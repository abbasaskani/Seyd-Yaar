/* Seyd‑Yaar app.js — dynamic map + aggregation + uncertainty + feedback 💠🌊 */
const $ = (id) => document.getElementById(id);
const safeText = (id, txt) => { const el = $(id); if (el) el.textContent = txt; };
const safeHTML = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };

const strings = {
  en: {
    subtitle: "Catch Probability (Habitat × Ops) + Uncertainty",
    Run: "Run",
    Variant: "QC / Gap‑Fill",
    Species: "Species",
    Model: "Model",
    Map: "Map",
    Aggregation: "Aggregation",
    From: "From",
    To: "To",
    Top: "Top‑10 Hotspots",
    Profile: "Species Profile (Explainable)",
    Audit: "Audit / meta.json",
    DownloadPNG: "Download PNG",
    DownloadGeo: "Download GeoJSON",
    Feedback: "+ Feedback",
    ExportFb: "Export feedback",
    Rating: "Rating",
    Depth: "Gear depth (m)",
    Notes: "Notes (optional)",
    SaveLocal: "Save locally",
    qcHint: "Masks low‑quality pixels (opacity)",
    gapHint: "Uses precomputed gap‑filled variant",
  },
  fa: {
    subtitle: "احتمال صید (زیستگاه × عملیات) + عدم‌قطعیت",
    Run: "ران",
    Variant: "QC / گپ‌فیل",
    Species: "گونه",
    Model: "مدل",
    Map: "نقشه",
    Aggregation: "تجمیع",
    From: "از",
    To: "تا",
    Top: "۱۰ نقطه برتر",
    Profile: "پروفایل گونه (توضیح‌پذیر)",
    Audit: "Audit / meta.json",
    DownloadPNG: "دانلود PNG",
    DownloadGeo: "دانلود GeoJSON",
    Feedback: "+ فیدبک",
    ExportFb: "خروجی فیدبک",
    Rating: "امتیاز",
    Depth: "عمق ابزار (m)",
    Notes: "یادداشت (اختیاری)",
    SaveLocal: "ذخیره لوکال",
    qcHint: "پیکسل‌های بی‌کیفیت را ماسک می‌کند (شفافیت)",
    gapHint: "از نسخه گپ‌فیل‌شده استفاده می‌کند",
  }
};

let lang = localStorage.getItem("lang") || "en";
function applyLang(){
  const t = strings[lang];
  safeText("subtitle", t.subtitle);
  safeText("lblRun", t.Run);
  safeText("lblVariant", t.Variant);
  safeText("lblSpecies", t.Species);
  safeText("lblModel", t.Model);
  safeText("lblMap", t.Map);
  safeText("lblAgg", t.Aggregation);
  safeText("lblFrom", t.From);
  safeText("lblTo", t.To);
  safeText("sumTop", t.Top);
  safeText("sumProfile", t.Profile);
  safeText("sumAudit", t.Audit);
  safeText("downloadPngBtn", t.DownloadPNG);
  safeText("downloadGeoBtn", t.DownloadGeo);
  safeText("feedbackBtn", t.Feedback);
  safeText("exportFbBtn", t.ExportFb);
  safeText("fbLblRating", t.Rating);
  safeText("fbLblDepth", t.Depth);
  safeText("fbLblNotes", t.Notes);
  safeText("saveFbBtn", t.SaveLocal);
  safeText("qcHint", t.qcHint);
  safeText("gapHint", t.gapHint);
  document.body.dir = (lang === "fa") ? "rtl" : "ltr";
}
$("langToggle").addEventListener("click", ()=>{
  lang = (lang === "en") ? "fa" : "en";
  localStorage.setItem("lang", lang);
  applyLang();
});

applyLang();

/* ------------------------------
   Theme + Toasts + Mobile sheet
------------------------------ */
function setTheme(theme){
  document.body.setAttribute("data-theme", theme);
  localStorage.setItem("theme", theme);
  const btn = $("themeToggle");
  if(btn) btn.textContent = (theme === "light") ? "☀️" : "🌙";
}
setTheme(localStorage.getItem("theme") || "dark");
$("themeToggle")?.addEventListener("click", ()=>{
  const cur = document.body.getAttribute("data-theme") || "dark";
  setTheme(cur === "dark" ? "light" : "dark");
  toast(lang==="fa" ? "تم عوض شد" : "Theme switched", "ok");
});

function toast(message, kind="ok", title=""){
  const host = $("toastHost");
  if(!host) return;
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  const ttl = title || (kind==="ok" ? (lang==="fa"?"اوکی":"OK") : kind==="warn" ? (lang==="fa"?"هشدار":"Warning") : (lang==="fa"?"خطا":"Error"));
  t.innerHTML = `<div class="tTitle">${ttl}</div><div class="tMsg">${message}</div>`;
  host.appendChild(t);
  setTimeout(()=>{t.style.opacity="0";t.style.transform="translateY(6px)";}, 3200);
  setTimeout(()=>{t.remove();}, 3800);
}

// Bottom sheet behavior on mobile
const panel = $("panel");
$("sheetHandle")?.addEventListener("click", ()=>{
  panel?.classList.toggle("open");
  // Leaflet sometimes needs a resize tick after layout changes
  if(map) setTimeout(()=>map.invalidateSize(true), 80);
});

/* ------------------------------
   Data loading (meta + binaries)
------------------------------ */
const state = {
  index: null,
  runId: null,
  runPath: null,
  variant: "gapfill",
  species: localStorage.getItem("species") || "skipjack",
  model: localStorage.getItem("model") || "ensemble",
  map: localStorage.getItem("map") || "pcatch",
  agg: localStorage.getItem("agg") || "p90",
  times: [],
  t0: null,
  t1: null,
  grid: null,
  mask: null,          // Uint8Array
  meta: null,          // species meta.json
  cache: new Map(),    // url -> typed array
  overlay: null,
  canvas: null,
  ctx: null,
  playing: false,
  autoCompute: false,
  dirty: true,
  userAoi: null,
  userMask: null,
  filterAoi: null,
  filterMask: null,
  timer: null,
  qcOn: true,
  gapOn: false,
  qcMaskCache: new Map(), // timeId-> Uint8Array
};

function fmtTime(isoZ){
  try{
    const d = new Date(isoZ);
    return d.toISOString().slice(0,16).replace("T"," ");
  }catch{ return isoZ; }
}
function timeIdFromIso(isoZ){
  // Prefer run-provided time_ids mapping (supports index-style folders like 0000..0143)
  if(state && state.isoToTimeId && state.isoToTimeId[isoZ]) return state.isoToTimeId[isoZ];
  if(typeof isoZ !== "string") return "";
  // Fallback: sanitize ISO (legacy demo runs)
  return isoZ.replace(/[:\-]/g, "").replace("T","_").replace("Z","");
}

function timeIdToIso(tid){
  // Expected: YYYYMMDD_HHMMZ
  try{
    const m = String(tid).match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})Z$/);
    if(!m) return String(tid);
    const [_, y, mo, d, hh, mm] = m;
    return `${y}-${mo}-${d}T${hh}:${mm}:00Z`;
  }catch{ return String(tid); }
}
async function fetchJson(url){
  const r = await fetch(url, {cache:"no-store"});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  return r.json();
}
async function fetchBin(url, dtype){
  if(state.cache.has(url)) return state.cache.get(url);
  const r = await fetch(url);
  if(!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  const buf = await r.arrayBuffer();
  let out;
  if(dtype === "f32") out = new Float32Array(buf);
  else if(dtype === "u8") out = new Uint8Array(buf);
  else out = buf;
  state.cache.set(url, out);
  return out;
}


function pointInRing(lon, lat, ring){
  // ray casting; ring: [[lon,lat],...]
  let inside = false;
  for(let i=0,j=ring.length-1;i<ring.length;j=i++){
    const xi=ring[i][0], yi=ring[i][1];
    const xj=ring[j][0], yj=ring[j][1];
    const intersect = ((yi>lat)!==(yj>lat)) && (lon < (xj-xi)*(lat-yi)/((yj-yi)||1e-12)+xi);
    if(intersect) inside = !inside;
  }
  return inside;
}
function pointInPolygon(lon, lat, poly){
  // poly: [outerRing, hole1, hole2...]
  if(!poly || !poly.length) return false;
  if(!pointInRing(lon,lat,poly[0])) return false;
  for(let h=1;h<poly.length;h++){
    if(pointInRing(lon,lat,poly[h])) return false;
  }
  return true;
}
function pointInGeoJSON(lon, lat, gj){
  if(!gj) return false;
  const g = gj.type==="Feature" ? gj.geometry : (gj.type==="FeatureCollection" ? null : gj);
  if(g){
    const t=g.type;
    if(t==="Polygon") return pointInPolygon(lon,lat,g.coordinates);
    if(t==="MultiPolygon") return g.coordinates.some(p=>pointInPolygon(lon,lat,p));
    return false;
  }
  if(gj.type==="FeatureCollection"){
    return gj.features.some(f=>{
      const gg=f.geometry;
      if(!gg) return false;
      if(gg.type==="Polygon") return pointInPolygon(lon,lat,gg.coordinates);
      if(gg.type==="MultiPolygon") return gg.coordinates.some(p=>pointInPolygon(lon,lat,p));
      return false;
    });
  }
  return false;
}
function buildMaskFromGeoJSON(gj){
  const W = state.grid.width, H = state.grid.height;
  const lonMin = state.grid.lon_min, lonMax = state.grid.lon_max;
  const latMin = state.grid.lat_min, latMax = state.grid.lat_max;
  const dx = (lonMax - lonMin) / (W-1);
  const dy = (latMax - latMin) / (H-1);
  const m = new Uint8Array(W*H);
  for(let r=0;r<H;r++){
    const lat = latMax - r*dy;
    for(let c=0;c<W;c++){
      const lon = lonMin + c*dx;
      const idx = r*W+c;
      // Respect server land/valid mask if present
      if(state.baseMask && state.baseMask[idx]===0){ m[idx]=0; continue; }
      m[idx] = pointInGeoJSON(lon,lat,gj) ? 1 : 0;
    }
  }
  return m;
}
function combineMask(base, extra){
  if(!extra) return base;
  const out = new Uint8Array(base.length);
  for(let i=0;i<base.length;i++){
    out[i] = (base[i] && extra[i]) ? 1 : 0;
  }
  return out;
}
/* ------------------------------
   Leaflet map
------------------------------ */
let map, imageOverlay, markerLayer;
function initMap(){
  map = L.map('map', {preferCanvas:true});
  // 🗺️ Basemap with automatic fallback
  // In some networks/regions the default OSM tile endpoint may be blocked or rate-limited.
  // If we detect repeated tile errors, we automatically switch to a mirror.
  const basemaps = [
    {
      name: "OSM",
      url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      opts: { subdomains: "abc", maxZoom: 18, attribution: "&copy; OpenStreetMap" }
    },
    {
      name: "Carto",
      url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      opts: { subdomains: "abcd", maxZoom: 19, attribution: "&copy; CARTO" }
    },
    {
      name: "Esri",
      url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      opts: { maxZoom: 19, attribution: "Tiles &copy; Esri" }
    }
  ];

  let baseIdx = 0;
  let tileErrors = 0;
  let baseLayer = null;

  function setBase(i){
    baseIdx = i % basemaps.length;
    tileErrors = 0;
    if(baseLayer) map.removeLayer(baseLayer);
    const bm = basemaps[baseIdx];
    baseLayer = L.tileLayer(bm.url, {
      ...bm.opts,
      // allow images to load cross-origin without tainting (helps PNG export in some browsers)
      crossOrigin: true,
    }).addTo(map);
    baseLayer.on("tileerror", ()=>{
      tileErrors++;
      // If too many tile errors early on, fallback.
      if(tileErrors === 8){
        console.warn("Basemap tile errors; switching basemap to", basemaps[(baseIdx+1)%basemaps.length].name);
        setBase(baseIdx+1);
      }
    });
  }
  setBase(0);
  markerLayer = L.layerGroup().addTo(map);

  // ✅ IMPORTANT: Leaflet (and leaflet-draw) expects the map to have an initial
  // center + zoom. If we don't set it, enabling draw/edit tools can throw:
  // "Set map center and zoom first." (because map.getCenter() is called before load).
  // We set a sane default center for the Arabian Sea, then we later fitBounds()
  // after loading meta/grid.
  try{
    map.setView([12.0, 54.0], 5);
  }catch(_){/* ignore */}

  map.on("click", (e)=>{
    if(!e?.latlng) return;
    $("fbLat").value = e.latlng.lat.toFixed(4);
    $("fbLon").value = e.latlng.lng.toFixed(4);
  });

  // offscreen canvas
  state.canvas = document.createElement("canvas");
  state.ctx = state.canvas.getContext("2d", {willReadFrequently:false});
  try{ afterMapInit_v22(); }catch(_){ }
}

/* ------------------------------
   v22: Grid 0.5°, AOI, Time slider, DBSCAN clustering, Auto-analyze
------------------------------ */
let gridLayer = null;
// Existence check helper (HEAD preferred; fallback to GET)
const __existsCache = new Map();
async function exists(url){
  if(__existsCache.has(url)) return __existsCache.get(url);
  try{
    const r = await fetch(url, {method:"HEAD", cache:"no-store"});
    const ok = (r.status===200 || r.status===304);
    if(ok){ __existsCache.set(url,true); return true; }
    if(r.status===405 || r.status===501){
      const r2 = await fetch(url, {method:"GET", cache:"no-store"});
      const ok2 = (r2.status===200 || r2.status===304);
      __existsCache.set(url, ok2); return ok2;
    }
    __existsCache.set(url,false); return false;
  }catch(_){
    try{
      const r3 = await fetch(url, {method:"GET", cache:"no-store"});
      const ok3 = (r3.status===200 || r3.status===304);
      __existsCache.set(url, ok3); return ok3;
    }catch(__){
      __existsCache.set(url,false); return false;
    }
  }
}

function mergeAndSortTimeIds(a,b){
  const s = new Set();
  (a||[]).forEach(x=>s.add(x));
  (b||[]).forEach(x=>s.add(x));
  return Array.from(s).sort(); // YYYYMMDD_HHMMZ
}

async function scanTimeIdsFromTimesDir(){
  // Works on local python http.server (directory listing). On GitHub Pages it may return 404/HTML without listing.
  try{
    const base = `latest/${state.runPath}/variants/${state.variant}/species/${state.species}/times/`;
    const r = await fetch(base, {cache:"no-store"});
    if(!(r.status===200 || r.status===304)) return [];
    const html = await r.text();
    const reDir = /href="(\d{8}_\d{4}Z)\/"/g;
    const out=[]; let m;
    while((m=reDir.exec(html))!==null) out.push(m[1]);
    return Array.from(new Set(out));
  }catch(_){
    return [];
  }
}

function currentPerTimeKey(){
  const mapKey = $("mapSelect")?.value || "pcatch";
  const modelKey = $("modelSelect")?.value || "ensemble";
  if(mapKey==="pcatch") return `pcatch_${modelKey}`;
  if(mapKey==="phab") return (modelKey==="frontplus") ? "phab_frontplus" : "phab_scoring";
  if(mapKey==="pops") return "pops";
  if(mapKey==="agree") return "agree";
  if(mapKey==="spread") return "spread";
  if(mapKey==="conf") return "conf";
  return `pcatch_${modelKey}`;
}

async function filterTimeIdsByExistingLayer(timeIds){
  try{
    const key = currentPerTimeKey();
    const tpl = state?.meta?.paths?.per_time?.[key];
    if(!tpl || typeof tpl!=="string") return timeIds;

    const good=[];
    const CONC=6;
    for(let i=0;i<timeIds.length;i+=CONC){
      const chunk = timeIds.slice(i,i+CONC);
      const res = await Promise.all(chunk.map(async tid=>{
        const url = `latest/${state.runPath}/${tpl.replace("{time}", tid).replace("{time_id}", tid)}`;
        return (await exists(url)) ? tid : null;
      }));
      for(const x of res) if(x) good.push(x);
    }
    return good;
  }catch(_){
    return timeIds;
  }
}

function afterMapInit_v22(){
  ensurePanes();

  map.on("moveend", ()=>{ try{ drawGrid05(); }catch(_){} });
  map.on("click", (e)=>{ 
    if(($("aoiMode")?.value)!=="draw") return;
    if(!aoiStart){ aoiStart = e.latlng; toast(lang==="fa" ? "گوشه دوم را کلیک کن" : "Click second corner", "info", "AOI"); return; }
    const b = L.latLngBounds(aoiStart, e.latlng);
    aoiStart = null;
    setAOI(b);
    scheduleAnalyze();
  });
  map.on("mousemove", (e)=>{
    try{
      if(!state.lastComputed?.arrShown) return;
      const g = state.grid;
      const W=g.width, H=g.height;
      const lat=e.latlng.lat, lon=e.latlng.lng;
      if(lat<g.lat_min || lat>g.lat_max || lon<g.lon_min || lon>g.lon_max) return;

      const fx = (lon - g.lon_min) / (g.lon_max - g.lon_min);
      const fy = (g.lat_max - lat) / (g.lat_max - g.lat_min);
      const x = Math.max(0, Math.min(W-1, Math.round(fx*(W-1))));
      const y = Math.max(0, Math.min(H-1, Math.round(fy*(H-1))));
      const v = state.lastComputed.arrShown[y*W+x];
      if(!Number.isFinite(v)) return;
      const p = percentileOfValue(v);
      const txt = `${lat.toFixed(3)}, ${lon.toFixed(3)} • ${(v*100).toFixed(1)}%` + (p!=null ? ` • P${Math.round(p*100)}` : "");
      if(!hoverTooltip){
        hoverTooltip = L.tooltip({sticky:true, direction:"top", opacity:0.85}).setContent(txt);
        hoverTooltip.setLatLng(e.latlng);
        hoverTooltip.addTo(map);
      }else{
        hoverTooltip.setLatLng(e.latlng);
        hoverTooltip.setContent(txt);
      }
    }catch(_){}
  });
  setTimeout(()=>{ try{ drawGrid05(); }catch(_){} }, 150);
}

$("exportClustersBtn")?.addEventListener("click", ()=>{
  const data = state.clusters || [];
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const a=document.createElement("a"); a.href=url; a.download="clusters.json"; a.click();
  setTimeout(()=>URL.revokeObjectURL(url), 2000);
});

$("exportTopBtn")?.addEventListener("click", ()=>{
  const top = state.lastComputed?.topFiltered || [];
  const lines = ["rank,lat,lon,prob_pct,percentile"];
  top.forEach((p,i)=>{
    lines.push([i+1,p.lat,p.lon,(p.p).toFixed(1), (p.pct!=null?Math.round(p.pct*100):"")].join(","));
  });
  const blob=new Blob([lines.join("\n")], {type:"text/csv"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a"); a.href=url; a.download="top_points.csv"; a.click();
  setTimeout(()=>URL.revokeObjectURL(url), 2000);
});

// Auto-analyze with debounce
let _anTimer = null;
function scheduleAnalyze(){
  if(!$("autoAnalyzeToggle")?.checked) return;
  clearTimeout(_anTimer);
  _anTimer = setTimeout(()=>{ try{ computeAndRender(); }catch(_){} }, 320);
}

["gridToggle","avgToggle","aoiMode","clusterThreshold","clusterEpsKm","clusterMinPts","stepSelect","aggSelect","mapSelect","modelSelect"].forEach(id=>{
  $(id)?.addEventListener("change", ()=>{ 
    if(id==="gridToggle"){ drawGrid05(); }
    scheduleAnalyze();
  });
});

// Bottom time slider binds to Single time mode by default
function syncSliderFromSelect(){
  const i = $("t1Select")?.selectedIndex ?? 0;
  const s = $("timeSlider"); if(!s) return;
  s.max = Math.max(0, state.times.length-1);
  s.value = String(Math.max(0, i));
  $("timeNowLabel").textContent = state.times[i] ? new Date(state.times[i]).toISOString().replace("T"," ").slice(0,16)+"Z" : "—";
}
function syncSelectFromSlider(){
  const s = $("timeSlider"); if(!s) return;
  const i = Number(s.value||0);
  $("t0Select").selectedIndex = i;
  $("t1Select").selectedIndex = i;
  $("timeNowLabel").textContent = state.times[i] ? new Date(state.times[i]).toISOString().replace("T"," ").slice(0,16)+"Z" : "—";
}

$("timeSlider")?.addEventListener("input", ()=>{
  if($("avgToggle")?.checked) return; // slider drives single-time only
  syncSelectFromSlider();
  scheduleAnalyze();
});
$("playBtnBottom")?.addEventListener("click", ()=>{
  $("playBtn")?.click();
});



/* ------------------------------
   Colormap (RdYlGn-like)
------------------------------ */
const stops = [
  {p:0.00, c:[40, 30, 120]},   // deep indigo
  {p:0.55, c:[46, 204, 113]}, // green
  {p:1.00, c:[241, 196, 15]}, // yellow
];
function lerp(a,b,t){return a+(b-a)*t}
function colorFor(v01){
  // Dynamic scaling per selection (AOI + current layer)
  const sMin = (typeof state.scaleMin==='number') ? state.scaleMin : 0;
  const sMax = (typeof state.scaleMax==='number') ? state.scaleMax : 1;
  const denom = (sMax - sMin) || 1;
  const vScaled = (v01 - sMin) / denom;

  const v = Math.min(1, Math.max(0, vScaled));
  let a=stops[0], b=stops[stops.length-1];
  for(let i=0;i<stops.length-1;i++){
    if(v>=stops[i].p && v<=stops[i+1].p){ a=stops[i]; b=stops[i+1]; break; }
  }
  const t = (v - a.p) / (b.p - a.p + 1e-9);
  return [
    Math.round(lerp(a.c[0], b.c[0], t)),
    Math.round(lerp(a.c[1], b.c[1], t)),
    Math.round(lerp(a.c[2], b.c[2], t)),
  ];
}

/* ------------------------------
   Aggregation
------------------------------ */
function aggQuantile(q){
  const T = state._tmpT;
  const tmp = state._tmpVals;
  tmp.sort();
  const idx = Math.round((T-1)*q);
  return tmp[idx];
}

function aggregatePerPixel(arrs, method){
  // arrs: array of Float32Array length N, values 0..1 or NaN
  const N = arrs[0].length;
  const T = arrs.length;
  const out = new Float32Array(N);
  const tmp = new Float32Array(T);
  for(let i=0;i<N;i++){
    // mask applied at aggregation time (server mask × user AOI)
    if(state.analysisMask && state.analysisMask[i]===0){ out[i]=NaN; continue; }
    let k=0;
    for(let t=0;t<T;t++){
      const v = arrs[t][i];
      if(Number.isFinite(v)) tmp[k++] = v;
    }
    if(k===0){ out[i]=NaN; continue; }
    if(method==="mean"){
      let s=0; for(let j=0;j<k;j++) s+=tmp[j];
      out[i]=s/k;
    }else if(method==="max"){
      let m=-1; for(let j=0;j<k;j++) if(tmp[j]>m) m=tmp[j];
      out[i]=m;
    }else if(method==="median"){
      // sort first k values (small)
      const slice = tmp.subarray(0,k);
      slice.sort();
      out[i]=slice[Math.floor((k-1)*0.5)];
    }else if(method==="p90"){
      const slice = tmp.subarray(0,k);
      slice.sort();
      out[i]=slice[Math.floor((k-1)*0.9)];
    }else{
      let s=0; for(let j=0;j<k;j++) s+=tmp[j];
      out[i]=s/k;
    }
  }
  return out;
}

/* ------------------------------
   Rendering to overlay
------------------------------ */
function setLegend(title){
  const mn = (typeof state.scaleMin==='number') ? (state.scaleMin*100).toFixed(1) : '';
  const mx = (typeof state.scaleMax==='number') ? (state.scaleMax*100).toFixed(1) : '';
  const mm = (mn && mx) ? ` <span style="font-weight:700;opacity:.9">(${mn}–${mx}%)</span>` : '';

  const el = $("legend");
  el.innerHTML = `
    <div style="font-weight:900; margin-bottom:6px">${title}${mm}</div>
    <div class="bar"></div>
    <div class="row2"><span>Low</span><span>High</span></div>
  `;
}

function renderOverlay(arr01, conf01){
  const {width:W, height:H, bounds} = state.grid;
  state.canvas.width = W;
  state.canvas.height = H;
  const img = state.ctx.createImageData(W, H);
  const data = img.data;

  const N = W*H;

  for(let i=0;i<N;i++){
    const v = arr01[i];
    const ok = Number.isFinite(v);
    const c = ok ? colorFor(v) : [0,0,0];
    const a = ok ? Math.round(255 * Math.min(1, Math.max(0, conf01[i] ?? 1))) : 0;
    const p = i*4;
    data[p+0]=c[0];
    data[p+1]=c[1];
    data[p+2]=c[2];
    data[p+3]=a;
  }
  state.ctx.putImageData(img, 0, 0);
  const url = state.canvas.toDataURL("image/png");

  const b = [[bounds[0][0], bounds[0][1]], [bounds[1][0], bounds[1][1]]]; // [[S,W],[N,E]]
  if(!imageOverlay){
    imageOverlay = L.imageOverlay(url, b, {opacity: 1.0, interactive:false}).addTo(map);
  }else{
    imageOverlay.setUrl(url);
    imageOverlay.setBounds(b);
  }
}

/* ------------------------------
   Top‑10 extraction + UI
------------------------------ */
function topKFromArray(arr, k=10){
  const W = state.grid.width, H = state.grid.height;
  const lonMin = state.grid.lon_min, lonMax = state.grid.lon_max;
  const latMin = state.grid.lat_min, latMax = state.grid.lat_max;
  const dx = (lonMax - lonMin) / (W-1);
  const dy = (latMax - latMin) / (H-1);
  // keep best k (simple insertion)
  const best = [];
  for(let i=0;i<arr.length;i++){
    const v = arr[i];
    if(!Number.isFinite(v)) continue;
    if(best.length < k){
      best.push({i,v});
      best.sort((a,b)=>a.v-b.v);
    }else if(v > best[0].v){
      best[0] = {i,v};
      best.sort((a,b)=>a.v-b.v);
    }
  }
  best.sort((a,b)=>b.v-a.v);
  return best.map((x,rank)=>{
    const r = Math.floor(x.i / W);
    const c = x.i % W;
    const lon = lonMin + c*dx;
    const lat = latMax - r*dy;
    return {rank:rank+1, lat, lon, p: x.v};
  });
}

function renderTop10(list, covs){
  // covs optional: {sst, chl, current, waves, front}
  const lim = Array.isArray(list) ? list.length : 0;
  // Dynamic label: Top‑10 on map, up to N in table
  if($("sumTop")){
    const show = Math.min(lim, parseInt($("topLimit")?.value || "100", 10) || 100);
    safeText("sumTop", (lang === "fa")
      ? `نقاط برتر (روی نقشه: ۱۰ • جدول: ${show})`
      : `Hotspots (Map: Top‑10 • Table: ${show})`);
  }

  markerLayer.clearLayers();
  if(!lim){
    $("top10Table").innerHTML = `<div class="muted">${lang==="fa"?"هیچ نقطه‌ای با فیلتر فعلی پیدا نشد.":"No hotspots matched the current filter."}</div>`;
    return;
  }
  const rows = [];
  for(const pt of list){
    const showOnMap = (pt.rank<=10);
    const popup = `
      <div style="font-weight:900">#${pt.rank} • P=${(pt.p*100).toFixed(1)}</div>
      <div class="muted">Lat ${pt.lat.toFixed(4)} • Lon ${pt.lon.toFixed(4)}</div>
    `;
    if(showOnMap){
      const icon = L.divIcon({
        className: "",
        html: `<div class="pulseMarker" style="width:14px;height:14px;border-radius:999px;background:rgba(57,255,159,0.88);border:1px solid rgba(255,255,255,0.85)"></div>`,
        iconSize: [14,14],
        iconAnchor: [7,7]
      });
      L.marker([pt.lat, pt.lon], {icon}).addTo(markerLayer).bindPopup(popup);
    }

    const sst = covs?.sst?.[pt.rank-1];
    const chl = covs?.chl?.[pt.rank-1];
    const cur = covs?.current?.[pt.rank-1];
    const wav = covs?.waves?.[pt.rank-1];
    const pPct = pt.p*100;
    const badgeClass = (pPct>=70) ? "good" : (pPct>=40) ? "mid" : "bad";
    rows.push({
      "#": pt.rank,
      "P%": `<span class="badge ${badgeClass}">${pPct.toFixed(1)}%</span>`,
      "Lat": pt.lat.toFixed(4),
      "Lon": pt.lon.toFixed(4),
      "SST": (sst!=null)? sst.toFixed(2) : "—",
      "Chl": (chl!=null)? chl.toFixed(3) : "—",
      "Cur": (cur!=null)? cur.toFixed(2) : "—",
      "Hs": (wav!=null)? wav.toFixed(2) : "—",
    });
  }

  // table
  let html = `<table><thead><tr>${Object.keys(rows[0]||{"#":0}).map(k=>`<th>${k}</th>`).join("")}</tr></thead><tbody>`;
  for(const r of rows){
    html += `<tr>${Object.values(r).map(v=>`<td>${v}</td>`).join("")}</tr>`;
  }
  html += `</tbody></table>`;
  $("top10Table").innerHTML = html;
}

/* ------------------------------
   Profile + audit
------------------------------ */
function renderProfile(){
  const sp = state.meta?.species_profile;
  if(!sp){ $("profileBox").innerHTML = "—"; return; }
  const p = sp.priors;
  const w = sp.layer_weights;
  const refs = (sp.references||[]).map(x=>`<li>${x}</li>`).join("");
  $("profileBox").innerHTML = `
    <div><b>${sp.label?.en || ""}</b> • <span class="muted">${sp.scientific_name||""}</span></div>
    <div class="muted">Region: ${sp.region||"—"}</div>
    <div style="margin-top:8px"><b>Priors</b></div>
    <ul class="bullets">
      <li>SST opt/sigma: ${p.sst_opt_c}°C / ${p.sst_sigma_c}</li>
      <li>Chl opt: ${p.chl_opt_mg_m3} mg/m³ (σ log10=${p.chl_sigma_log10})</li>
      <li>Current opt/sigma: ${p.current_opt_m_s} m/s / ${p.current_sigma_m_s}</li>
      <li>Waves soft max: ${p.waves_hs_soft_max_m} m</li>
    </ul>
    <div><b>Layer weights</b></div>
    <ul class="bullets">
      <li>Temp: ${w.temp} • Chl: ${w.chl} • Front: ${w.front} • Current: ${w.current} • Waves: ${w.waves}</li>
    </ul>
    <div><b>Key references</b></div>
    <ul class="bullets">${refs}</ul>
    <div class="muted small">${sp.notes||""}</div>
  `;
}

function renderAudit(){
  const meta = state.meta;
  if(!meta){ safeText("auditBox", "—"); return; }
  safeText("auditBox", JSON.stringify({
    run_id: meta.run_id,
    variant: meta.variant,
    species: meta.species,
    defaults: meta.defaults,
    ppp_model: meta.ppp_model,
    grid: meta.grid,
    times: meta.times?.length,
  }, null, 2));
}

/* ------------------------------
   Compute & update view
------------------------------ */
function getSelectedTimes(){
  const i0 = $("t0Select").selectedIndex;
  const i1 = $("t1Select").selectedIndex;
  const a = Math.min(i0,i1);
  const b = Math.max(i0,i1);
  return state.times.slice(a, b+1);
}

function mapTitle(){
  const m = $("mapSelect").value;
  if(m==="pcatch") return "Pcatch (Habitat×Ops)";
  if(m==="phab") return "Habitat Suitability";
  if(m==="pops") return "Operational Feasibility";
  if(m==="agree") return "Agreement (ensemble)";
  if(m==="spread") return "Spread/Std (ensemble)";
  if(m==="conf") return "Confidence / Opacity";
  return m;
}

async function loadCovAtPoints(timeIso, points){
  // For table explainability at hotspots: sample covariates nearest grid cell
  const timeId = timeIdFromIso(timeIso);
  const W = state.grid.width, H = state.grid.height;
  const lonMin = state.grid.lon_min, lonMax = state.grid.lon_max;
  const latMin = state.grid.lat_min, latMax = state.grid.lat_max;
  const dx = (lonMax - lonMin) / (W-1);
  const dy = (latMax - latMin) / (H-1);

  async function loadArr(key, dtype){
    const url = `latest/${state.runPath}/${state.meta.paths.per_time[key].replace("{time}", timeId)}`;
    return fetchBin(url, dtype);
  }
  const [sst, chl, cur, wav] = await Promise.all([
    loadArr("sst","f32"), loadArr("chl","f32"), loadArr("current","f32"), loadArr("waves","f32")
  ]);

  const out = {sst:[], chl:[], current:[], waves:[]};
  for(const pt of points){
    const c = Math.round((pt.lon - lonMin)/dx);
    const r = Math.round((latMax - pt.lat)/dy);
    const rr = Math.min(H-1, Math.max(0, r));
    const cc = Math.min(W-1, Math.max(0, c));
    const idx = rr*W+cc;
    out.sst.push(sst[idx]);
    out.chl.push(chl[idx]);
    out.current.push(cur[idx]);
    out.waves.push(wav[idx]);
  }
  return out;
}

async function getConfAggregated(timeIsos){
  // aggregate confidence similarly to probs (but mean)
  const W = state.grid.width, H = state.grid.height;
  const promises = timeIsos.map(t=>{
    const tid = timeIdFromIso(t);
    const url = `latest/${state.runPath}/${state.meta.paths.per_time.conf.replace("{time}", tid)}`;
    return fetchBin(url,"f32");
  });
  const arrs = await Promise.all(promises);
  const conf = aggregatePerPixel(arrs, "mean");

  // QC mask if toggle
  if(state.qcOn){
    const qcArrs = await Promise.all(timeIsos.map(async t=>{
      const tid = timeIdFromIso(t);
      const url = `latest/${state.runPath}/${state.meta.paths.per_time.qc_chl.replace("{time}", tid)}`;
      return fetchBin(url,"u8");
    }));
    const qcMean = new Float32Array(conf.length);
    for(let i=0;i<conf.length;i++){
      if(state.analysisMask && state.analysisMask[i]===0){ qcMean[i]=0; continue; }
      let s=0, k=0;
      for(let t=0;t<qcArrs.length;t++){
        s += (qcArrs[t][i] > 0) ? 1 : 0;
        k++;
      }
      qcMean[i] = (k>0)? (s/k) : 1;
    }
    for(let i=0;i<conf.length;i++) conf[i] = conf[i] * qcMean[i];
  }
  return conf;
}

function applyFilterMaskToArray(arr){
  // After analysis: optionally filter results by a second AOI (post-filter)
  if(!state.filterMask) return arr;
  const out = new Float32Array(arr.length);
  for(let i=0;i<arr.length;i++){
    const v = arr[i];
    if(!Number.isFinite(v)){ out[i]=NaN; continue; }
    out[i] = (state.filterMask[i]===1) ? v : NaN;
  }
  return out;
}

function getTopFilter(){
  const minP = parseFloat($("minP")?.value ?? "0")/100;
  const lim = parseInt($("topLimit")?.value ?? "100");
  return {minP, lim};
}

function renderFromCache(){
  if(!state.lastComputed) return;
  const {arrAgg, confAgg, timeIsos} = state.lastComputed;
  const arrShown = applyFilterMaskToArray(arrAgg);
  const confShown = (confAgg && confAgg.length===arrShown.length) ? confAgg : new Float32Array(arrShown.length).fill(1);

  setLegend(mapTitle());
  renderOverlay(arrShown, confShown);

  const {minP, lim} = getTopFilter();
  const topAll = topKFromArray(arrShown, 100);
  const topFiltered = topAll.filter(x=>x.p >= minP).slice(0, Math.min(100, lim));
  // attach rank + percentile for exports
  const dist = state._distVals || [];
  topFiltered.forEach((pt, idx)=>{
    pt.rank = idx+1;
    pt.pct = (typeof percentileOfValue==='function') ? percentileOfValue(pt.p/100) : null;
  });
  state.lastComputed.topFiltered = topFiltered;


  const midTime = timeIsos[Math.floor(timeIsos.length/2)];
  loadCovAtPoints(midTime, topFiltered).then(covs=>renderTop10(topFiltered, covs));
}

async function computeAndRender(){
  localStorage.setItem("species", state.species);
  localStorage.setItem("model", state.model);
  localStorage.setItem("map", state.map);
  localStorage.setItem("agg", state.agg);

  const timeIsos = getSelectedTimes();
  const mapKey = $("mapSelect").value;
  const modelKey = $("modelSelect").value;

  const W = state.grid.width, H = state.grid.height;

  // load arrays for selected layer
  async function loadLayerForTime(timeIso){
    const tid = timeIdFromIso(timeIso);
    let key = null;
    if(mapKey==="pcatch"){
      key = `pcatch_${modelKey}`;
    }else if(mapKey==="phab"){
      key = (modelKey==="frontplus") ? "phab_frontplus" : "phab_scoring";
    }else if(mapKey==="pops"){
      key = "pops";
    }else if(mapKey==="agree"){
      key = "agree";
    }else if(mapKey==="spread"){
      key = "spread";
    }else if(mapKey==="conf"){
      key = "conf";
    }else{
      key = `pcatch_${modelKey}`;
    }
    const tpl = state.meta.paths.per_time[key];
    if(!tpl || typeof tpl !== "string"){
      console.warn("Missing layer template:", key);
      return new Float32Array(W*H).fill(NaN);
    }
    const url = `latest/${state.runPath}/${tpl.replace("{time}", tid)}`;
    return fetchBin(url, (key.endsWith("_u8")?"u8":"f32"));
  }

  const arrs = await Promise.all(timeIsos.map(loadLayerForTime));
  let aggMethod = $("aggSelect").value;
  // For conf map we always mean
  if(mapKey==="conf") aggMethod = "mean";

  const arrAgg = aggregatePerPixel(arrs, aggMethod);

  const confAgg = (mapKey==="conf")
    ? (()=>{ // visualize confidence itself (as "prob")
        const c = new Float32Array(arrAgg.length);
        for(let i=0;i<c.length;i++){
          c[i] = Number.isFinite(arrAgg[i]) ? 1.0 : 0.0;
        }
        return c;
      })()
    : await getConfAggregated(timeIsos);

  // render
  // cache raw (pre-filter)
  state.lastComputed = {arrAgg, confAgg, timeIsos};

  // render with post-filter + top filters
  renderFromCache();

  // fit bounds on first load
  if(!state._didFit){
    map.fitBounds([[state.grid.lat_min, state.grid.lon_min],[state.grid.lat_max, state.grid.lon_max]]);
    state._didFit = true;
  }

  // top10 from aggregated (for catch & habitat & ops)
  // Top table rendered inside renderFromCache()
}

/* ------------------------------
   Run/variant/species meta wiring
------------------------------ */
async function refreshMeta(){
  // read meta_index to list runs
  state.index = await fetchJson("latest/meta_index.json");
  const runSelect = $("runSelect");
  runSelect.innerHTML = "";
  for(const r of state.index.runs){
    const opt = document.createElement("option");
    opt.value = r.run_id;
    opt.textContent = `${r.run_id} (${r.fast ? "fast" : "full"})`;
    runSelect.appendChild(opt);
  }
  state.runId = state.index.latest_run_id || state.index.runs[state.index.runs.length-1]?.run_id;
  runSelect.value = state.runId;

  runSelect.addEventListener("change", async ()=>{
    state.runId = runSelect.value;
    await refreshVariants();
  });

  await refreshVariants();
}

async function refreshVariants(){
  const run = state.index.runs.find(r=>r.run_id===state.runId);
  state.runPath = run.path; // e.g., runs/demo_YYYY-MM-DD
  const variantSelect = $("variantSelect");
  variantSelect.innerHTML = "";
  for(const v of run.variants){
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    variantSelect.appendChild(opt);
  }

  // Keep gap toggle in sync with variant
  const preferred = ($("gapToggle").checked) ? "gapfill" : "base";
  state.variant = run.variants.includes(preferred) ? preferred : run.variants[0];
  variantSelect.value = state.variant;

  variantSelect.addEventListener("change", async ()=>{
    state.variant = variantSelect.value;
    $("gapToggle").checked = (state.variant === "gapfill");
    await loadSpeciesMetaAndInit();
  });

  await loadSpeciesMetaAndInit();
}

async function loadSpeciesMetaAndInit(){
  state.species = $("speciesSelect").value;
  // species meta path:
  const url = `latest/${state.runPath}/variants/${state.variant}/species/${state.species}/meta.json`;
  state.meta = await fetchJson(url);
  // run-level meta for availability reporting + deduped time catalog
  state.runMeta = await fetchJson(`latest/${state.runPath}/meta.json`).catch(()=>null);
  state.grid = state.meta.grid;

  // load server mask
  const maskUrl = `latest/${state.runPath}/${state.meta.paths.mask}`;
  state.baseMask = await fetchBin(maskUrl, "u8");

  // effective analysis mask = server mask × user AOI
  state.analysisMask = combineMask(state.baseMask, state.userMask);

  // time selects (prefer runMeta.available_time_ids to avoid listing missing future bins)
  const availableTimeIds = state.runMeta?.available_time_ids || state.meta.time_ids || [];
  state.timeIds = await filterTimeIdsByExistingLayer(availableTimeIds);
  // keep derived ISO list in sync
  state.times = state.timeIds.map(timeIdToIso);
  state.isoToTimeId = {};
  for(let i=0;i<state.times.length;i++){ state.isoToTimeId[state.times[i]] = state.timeIds[i]; }
  state.times = availableTimeIds.map(timeIdToIso);
  state.isoToTimeId = {};
  for(let i=0;i<state.times.length;i++){ state.isoToTimeId[state.times[i]] = state.timeIds[i]; }

  // availability info panel
  const lastTid = state.runMeta?.latest_available_time_id || (state.timeIds[state.timeIds.length-1]||null);
  if($("availabilityInfo")){
    if(lastTid){
      const lastIso = timeIdToIso(lastTid);
      $("availabilityInfo").innerHTML = `<b>${lang==="fa"?"آخرین دیتای موجود":"Latest available data"}</b><br><span class="muted">${fmtTime(lastIso)} (UTC)</span>`;
    }else{
      $("availabilityInfo").innerHTML = `<b>${lang==="fa"?"دیتایی یافت نشد":"No data found"}</b>`;
    }
  }
  $("t0Select").innerHTML = "";
  $("t1Select").innerHTML = "";
  for(const t of state.times){
    const o0 = document.createElement("option");
    o0.value = t; o0.textContent = fmtTime(t);
    const o1 = document.createElement("option");
    o1.value = t; o1.textContent = fmtTime(t);
    $("t0Select").appendChild(o0);
    $("t1Select").appendChild(o1);
  }
  // default range: focus on the latest available window (commercial use‑case = planning ahead)
  const last = Math.max(0, state.times.length-1);
  $("t1Select").selectedIndex = last;
  $("t0Select").selectedIndex = Math.max(0, last-2);

  // defaults persisted
  $("speciesSelect").value = state.species;
  $("modelSelect").value = state.model;
  $("mapSelect").value = state.map;
  $("aggSelect").value = state.agg;

  // Per‑species lookback memory (each species can have its own averaging window)
  try{
    const savedLb = localStorage.getItem(`lookback_${state.species}`);
    if(savedLb !== null) $("lookbackSelect").value = savedLb;
  }catch(_){/* ignore */}
  applyLookback();


  // AOI UI defaults (bbox = grid bounds)
  $("bboxLatMin").value = state.grid.lat_min.toFixed(4);
  $("bboxLatMax").value = state.grid.lat_max.toFixed(4);
  $("bboxLonMin").value = state.grid.lon_min.toFixed(4);
  $("bboxLonMax").value = state.grid.lon_max.toFixed(4);
  // Don't erase user's AOI on species switch if it exists (AOI is a user intent)
  if(!state.userMask){
    state.userMask = null;
  }
  state.analysisMask = combineMask(state.baseMask, state.userMask);
  // init filter bbox defaults too
  $("filterBboxLatMin").value = state.grid.lat_min.toFixed(4);
  $("filterBboxLatMax").value = state.grid.lat_max.toFixed(4);
  $("filterBboxLonMin").value = state.grid.lon_min.toFixed(4);
  $("filterBboxLonMax").value = state.grid.lon_max.toFixed(4);
  updateAoiStatus();

  // filter status
  updateFilterAoiStatus();

  // Leaflet draw layer + controls (once)
  if(!state._drawInit){
    state._drawInit = true;
    // Leaflet-draw calls map.getCenter() internally; make sure map is ready.
    map.whenReady(()=>{
      // Two AOIs: analysis + filter
      state.drawLayer = new L.FeatureGroup();
      map.addLayer(state.drawLayer);
      state.drawTarget = "analysis";

      // Keep last drawn shapes separated (for styling and status)
      state.drawnAnalysis = null;
      state.drawnFilter = null;

      // Focus decides where draw goes
      $("aoiText").addEventListener("focus", ()=> state.drawTarget = "analysis");
      $("filterAoiText").addEventListener("focus", ()=> state.drawTarget = "filter");

      const drawControl = new L.Control.Draw({
        edit: { featureGroup: state.drawLayer },
        draw: { polyline:false, circle:false, circlemarker:false, marker:false }
      });
      map.addControl(drawControl);

      map.on(L.Draw.Event.CREATED, (e)=>{
        // Style by target
        if(state.drawTarget === "filter"){
          if(state.drawnFilter) state.drawLayer.removeLayer(state.drawnFilter);
          e.layer.setStyle?.({color:"#ffe95a", weight:2, fillOpacity:0.05});
          state.drawnFilter = e.layer;
          state.drawLayer.addLayer(e.layer);
          const gj = e.layer.toGeoJSON();
          $("filterAoiText").value = JSON.stringify(gj, null, 2);
          applyFilterAoiFromText();
        }else{
          if(state.drawnAnalysis) state.drawLayer.removeLayer(state.drawnAnalysis);
          e.layer.setStyle?.({color:"#39ff9f", weight:2, fillOpacity:0.05});
          state.drawnAnalysis = e.layer;
          state.drawLayer.addLayer(e.layer);
          const gj = e.layer.toGeoJSON();
          $("aoiText").value = JSON.stringify(gj, null, 2);
          applyUserAoiFromText();
        }
      });
    });
  }

  renderProfile();
  renderAudit();

  // compute
  setDirty();
}

/* ------------------------------
   UI events
------------------------------ */
["speciesSelect","modelSelect","mapSelect","aggSelect","t0Select","t1Select"].forEach(id=>{
  $(id).addEventListener("change", async ()=>{
    const prevSpecies = state.species;
    state.species = $("speciesSelect").value;
    state.model = $("modelSelect").value;
    state.map = $("mapSelect").value;
    state.agg = $("aggSelect").value;

    // if species changed, reload meta (different profile + files)
    if(id==="speciesSelect"){
      // Persist per-species lookback (commercial UX: each species remembers its own average window)
      try{ localStorage.setItem(`lookback_${prevSpecies}`, $("lookbackSelect").value); }catch(_){/* ignore */}
      await loadSpeciesMetaAndInit();
      setDirty("Species changed. Press Analyze.");
      return;
    }
    setDirty();
  });
});

$("qcToggle").addEventListener("change", async ()=>{
  state.qcOn = $("qcToggle").checked;
  setDirty();
});

$("gapToggle").addEventListener("change", async ()=>{
  // Switch variant to base/gapfill if available
  const want = $("gapToggle").checked ? "gapfill" : "base";
  const run = state.index.runs.find(r=>r.run_id===state.runId);
  if(run.variants.includes(want)){
    state.variant = want;
    $("variantSelect").value = want;
    await loadSpeciesMetaAndInit();
  }else{
    // revert
    $("gapToggle").checked = (state.variant==="gapfill");
  }
});

$("analyzeBtn").addEventListener("click", async ()=>{
  state.dirty = false;
  safeText("dirtyHint", (lang==="fa") ? "در حال تحلیل..." : "Analyzing…");
  $("top10Table").innerHTML = `<div class="skeleton" style="height:180px"></div>`;
  toast(lang==="fa" ? "در حال بارگذاری داده‌ها" : "Loading data…", "ok", lang==="fa"?"تحلیل":"Analyze");
  try{ await computeAndRender(); }
  catch(err){
    console.error(err);
    toast(lang==="fa" ? "داده برای این بازه هنوز آماده نیست. اگر تحلیل در حال اجراست، کمی بعد دوباره امتحان کن." : "Data not available for this selection yet. If a backend run is in progress, try again later.", "warn", lang==="fa"?"در دسترس نیست":"Not ready");
    safeText("dirtyHint", (lang==="fa") ? "داده هنوز آماده نیست" : "Not ready yet");
    return;
  }
  safeText("dirtyHint", (lang==="fa") ? "انجام شد ✅" : "Done ✅");
});

$("lookbackSelect").addEventListener("change", ()=>{
  try{ localStorage.setItem(`lookback_${state.species}`, $("lookbackSelect").value); }catch(_){/* ignore */}
  applyLookback();
  setDirty("Lookback changed. Press Analyze.");
});
$("t1Select").addEventListener("change", ()=>{ applyLookback(); });
function applyLookback(){
  const d = parseInt($("lookbackSelect").value||"0");
  if(!d || !state.times?.length) return;
  const t1Iso = $("t1Select").value;
  const t1 = new Date(t1Iso);
  const t0 = new Date(t1.getTime() - d*24*3600*1000);
  // choose closest available time >= t0
  let bestIdx=0, bestDt=1e18;
  for(let i=0;i<state.times.length;i++){
    const tt = new Date(state.times[i]);
    const diff = Math.abs(tt.getTime() - t0.getTime());
    if(diff<bestDt){ bestDt=diff; bestIdx=i; }
  }
  $("t0Select").selectedIndex = bestIdx;
}



function setDirty(msg){
  state.dirty = true;
  safeText("dirtyHint", msg || "Change settings, then press Analyze.");
}

function parsePointsToPolygonGeoJSON(txt, name="points_poly"){
  // Accept lines like: "lat,lon" or "lat lon" or "lon,lat" if user prefixes with "lon:" (kept simple)
  const lines = (txt||"").split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
  const pts = [];
  for(const l of lines){
    const parts = l.split(/[,\s]+/).filter(Boolean);
    if(parts.length<2) continue;
    const a = parseFloat(parts[0]), b = parseFloat(parts[1]);
    if(!isFinite(a)||!isFinite(b)) continue;
    // assume lat,lon (most common). We'll treat |lat|<=90 as lat.
    let lat=a, lon=b;
    if(Math.abs(a)>90 && Math.abs(b)<=90){ lon=a; lat=b; }
    pts.push([lon, lat]);
  }
  if(pts.length < 3) throw new Error("Need at least 3 points");
  // close ring
  if(pts[0][0]!==pts[pts.length-1][0] || pts[0][1]!==pts[pts.length-1][1]) pts.push(pts[0]);
  return {type:"Feature", properties:{name}, geometry:{type:"Polygon", coordinates:[pts]}};
}

function updateAoiStatus(){
  const on = !!state.userMask;
  safeText("aoiStatus", on ? "AOI: active ✅ (mask applied)" : "AOI: none (using server mask)");
}
function applyUserAoiFromText(){
  try{
    const raw = $("aoiText").value.trim();
    if(!raw){ state.userAoi=null; state.userMask=null; updateAoiStatus(); setDirty("AOI cleared. Press Analyze."); return; }
    const gj = JSON.parse(raw);
    state.userAoi = gj;
    state.userMask = buildMaskFromGeoJSON(gj);
    state.analysisMask = combineMask(state.baseMask, state.userMask);
    updateAoiStatus();
    setDirty("AOI updated. Press Analyze.");
  }catch(err){
    alert("Invalid GeoJSON ❌");
  }
}
$("useAoiBtn").addEventListener("click", ()=>applyUserAoiFromText());
$("clearAoiBtn").addEventListener("click", ()=>{
  $("aoiText").value="";
  if(state.drawnAnalysis){ state.drawLayer?.removeLayer(state.drawnAnalysis); state.drawnAnalysis=null; }
  state.userAoi=null; state.userMask=null;
  state.analysisMask = combineMask(state.baseMask, state.userMask);
  updateAoiStatus();
  setDirty("AOI cleared. Press Analyze.");
});
$("aoiFile").addEventListener("change", async (e)=>{
  const f = e.target.files?.[0];
  if(!f) return;
  const txt = await f.text();
  $("aoiText").value = txt;
  applyUserAoiFromText();
});
$("useBboxBtn").addEventListener("click", ()=>{
  const latMin=parseFloat($("bboxLatMin").value), latMax=parseFloat($("bboxLatMax").value);
  const lonMin=parseFloat($("bboxLonMin").value), lonMax=parseFloat($("bboxLonMax").value);
  if(!isFinite(latMin)||!isFinite(latMax)||!isFinite(lonMin)||!isFinite(lonMax)){ alert("Invalid bbox"); return; }
  const poly = [[
    [lonMin,latMin],[lonMax,latMin],[lonMax,latMax],[lonMin,latMax],[lonMin,latMin]
  ]];
  const gj = {type:"Feature", properties:{name:"bbox"}, geometry:{type:"Polygon", coordinates:poly}};
  $("aoiText").value = JSON.stringify(gj, null, 2);
  // draw on map
  try{
    if(state.drawnAnalysis) state.drawLayer?.removeLayer(state.drawnAnalysis);
    const lyr = L.geoJSON(gj, {style:{color:"#39ff9f", weight:2, fillOpacity:0.05}});

$("usePointsBtn").addEventListener("click", ()=>{
  const txt = $("aoiPoints")?.value?.trim();
  if(!txt){ alert("Please paste points (lat,lon) first."); return; }
  try{
    const gj = parsePointsToPolygonGeoJSON(txt, "points");
    $("aoiText").value = JSON.stringify(gj, null, 2);
    // draw on map
    try{
      if(state.drawnAnalysis) state.drawLayer?.removeLayer(state.drawnAnalysis);
      const lyr = L.geoJSON(gj, {style:{color:"#39ff9f", weight:2, fillOpacity:0.05}});
      lyr.eachLayer(l=>{ state.drawnAnalysis = l; state.drawLayer?.addLayer(l); });
      // fit bounds
      try{ map.fitBounds(lyr.getBounds(), {padding:[20,20]}); }catch(e){}
    }catch(e){}
    applyUserAoiFromText();
  }catch(err){
    alert("Invalid points list ❌ (need ≥3 points)");
  }
});
    lyr.eachLayer(l=>{ state.drawnAnalysis = l; state.drawLayer?.addLayer(l); });
  }catch(e){}
  applyUserAoiFromText();
});

// ---- Filter AOI (post-analysis) ----
function updateFilterAoiStatus(){
  const on = !!state.filterMask;
  safeText("filterAoiStatus", on ? "Filter: active ✅" : "Filter: none");
}
function applyFilterAoiFromText(){
  try{
    const raw = $("filterAoiText").value.trim();
    if(!raw){ state.filterAoi=null; state.filterMask=null; updateFilterAoiStatus(); renderFromCache(); return; }
    const gj = JSON.parse(raw);
    state.filterAoi = gj;
    // filter mask should still respect analysis mask
    const m = buildMaskFromGeoJSON(gj);
    state.filterMask = combineMask(state.analysisMask || state.baseMask, m);
    updateFilterAoiStatus();
    renderFromCache();
  }catch(err){
    alert("Invalid Filter GeoJSON ❌");
  }
}

$("useFilterAoiBtn").addEventListener("click", ()=>applyFilterAoiFromText());
$("clearFilterAoiBtn").addEventListener("click", ()=>{
  $("filterAoiText").value="";
  if(state.drawnFilter){ state.drawLayer?.removeLayer(state.drawnFilter); state.drawnFilter=null; }
  state.filterAoi=null; state.filterMask=null;
  updateFilterAoiStatus();
  renderFromCache();
});
$("filterAoiFile").addEventListener("change", async (e)=>{
  const f = e.target.files?.[0];
  if(!f) return;
  const txt = await f.text();
  $("filterAoiText").value = txt;
  applyFilterAoiFromText();
});
$("useFilterBboxBtn").addEventListener("click", ()=>{
  const latMin=parseFloat($("filterBboxLatMin").value), latMax=parseFloat($("filterBboxLatMax").value);
  const lonMin=parseFloat($("filterBboxLonMin").value), lonMax=parseFloat($("filterBboxLonMax").value);
  if(!isFinite(latMin)||!isFinite(latMax)||!isFinite(lonMin)||!isFinite(lonMax)){ alert("Invalid bbox"); return; }
  const poly = [[[lonMin,latMin],[lonMax,latMin],[lonMax,latMax],[lonMin,latMax],[lonMin,latMin]]];
  const gj = {type:"Feature", properties:{name:"filter_bbox"}, geometry:{type:"Polygon", coordinates:poly}};
  $("filterAoiText").value = JSON.stringify(gj, null, 2);
  try{
    if(state.drawnFilter) state.drawLayer?.removeLayer(state.drawnFilter);
    const lyr = L.geoJSON(gj, {style:{color:"#ffe95a", weight:2, fillOpacity:0.05}});

$("useFilterPointsBtn").addEventListener("click", ()=>{
  const txt = $("filterAoiPoints")?.value?.trim();
  if(!txt){ alert("Please paste filter points (lat,lon) first."); return; }
  try{
    const gj = parsePointsToPolygonGeoJSON(txt, "filter_points");
    $("filterAoiText").value = JSON.stringify(gj, null, 2);
    // draw on map
    try{
      if(state.drawnFilter) state.drawLayer?.removeLayer(state.drawnFilter);
      const lyr = L.geoJSON(gj, {style:{color:"#ffe95a", weight:2, fillOpacity:0.05}});
      lyr.eachLayer(l=>{ state.drawnFilter = l; state.drawLayer?.addLayer(l); });
      try{ map.fitBounds(lyr.getBounds(), {padding:[20,20]}); }catch(e){}
    }catch(e){}
    applyFilterAoiFromText();
  }catch(err){
    alert("Invalid points list ❌ (need ≥3 points)");
  }
});
    lyr.eachLayer(l=>{ state.drawnFilter = l; state.drawLayer?.addLayer(l); });
  }catch(e){}
  applyFilterAoiFromText();
});

// ---- Top filters (client-side, no recompute) ----
$("minP").addEventListener("input", ()=>{
  safeText("minPVal", `${$("minP").value}%`);
  renderFromCache();
});
$("topLimit").addEventListener("change", ()=>renderFromCache());

/* animation */
$("playBtn").addEventListener("click", ()=>{
  if(state.playing){
    stopPlay();
  }else{
    startPlay();
  }
});

function startPlay(){
  if($("avgToggle")?.checked){
    toast(lang==="fa" ? "برای Play، حالت Average را خاموش کن" : "Turn off Average to Play", "info", "Play");
    return;
  }
  if(state.playing) return;
  state.playing = true;
  safeText("playBtn", "⏸ Pause");
  safeText("playBtnBottom", "⏸");

  // Keep selected range length fixed and slide it forward over available times
  const rangeLen = Math.abs($("t1Select").selectedIndex - $("t0Select").selectedIndex);

  let busy = false;
  const stepHours = Number($("stepSelect")?.value || 6);
  const stepN = Math.max(1, Math.round(stepHours/6)); // time list is 6h-granular

  const tick = async ()=>{
    if(!state.playing) return;
    if(busy){ state.timer = setTimeout(tick, 350); return; }
    busy = true;
    try{
      const i0 = $("t0Select").selectedIndex;
      const i1 = $("t1Select").selectedIndex;
      const dir = (i1 >= i0) ? 1 : -1;

      let next0 = i0 + dir*stepN;
      let next1 = next0 + dir*rangeLen;

      // wrap
      const n = state.times.length;
      while(next0 < 0) next0 += n;
      while(next0 >= n) next0 -= n;
      while(next1 < 0) next1 += n;
      while(next1 >= n) next1 -= n;

      $("t0Select").selectedIndex = next0;
      $("t1Select").selectedIndex = next1;

      // Recompute and redraw for the new time range
      await computeAndRender();
      try{ syncSliderFromSelect(); }catch(_){}
    }catch(e){
      console.error("Play step failed:", e);
    }finally{
      busy = false;
      state.timer = setTimeout(tick, 900);
    }
  };

  // kick off
  tick();
}

function stopPlay(){
  state.playing = false;
  safeText("playBtn", "▶ Play");
  safeText("playBtnBottom", "▶");
  if(state.timer) clearTimeout(state.timer);
  state.timer = null;
}

/* ------------------------------
   Download / Share
------------------------------ */
$("downloadPngBtn").addEventListener("click", ()=>{
  const url = state.canvas.toDataURL("image/png");
  const a = document.createElement("a");
  a.href = url;
  a.download = `seydyaar_${state.runId}_${state.variant}_${state.species}_${state.map}_${state.agg}.png`;
  document.body.appendChild(a);
  a.click();
  a.remove();
});

$("downloadGeoBtn").addEventListener("click", async ()=>{
  // create GeoJSON from current top10 markers (recompute quickly from current canvas arrays not accessible; use DOM table)
  // We'll regenerate from last render by reading markers from markerLayer
  const feats = [];
  markerLayer.eachLayer(l=>{
    const latlng = l.getLatLng();
    feats.push({
      type:"Feature",
      properties:{},
      geometry:{type:"Point", coordinates:[latlng.lng, latlng.lat]}
    });
  });
  const fc = {type:"FeatureCollection", features: feats};
  const blob = new Blob([JSON.stringify(fc, null, 2)], {type:"application/geo+json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `seydyaar_top10_${state.runId}_${state.variant}_${state.species}.geojson`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

/* ------------------------------
   Feedback (IndexedDB)
------------------------------ */
const DB_NAME = "seydyaar_feedback_db";
const STORE = "feedback";
function openDb(){
  return new Promise((resolve,reject)=>{
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      const store = db.createObjectStore(STORE, {keyPath:"id"});
      store.createIndex("ts","timestamp");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function saveFeedback(rec){
  const db = await openDb();
  return new Promise((resolve,reject)=>{
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(rec);
    tx.oncomplete = ()=>resolve(true);
    tx.onerror = ()=>reject(tx.error);
  });
}
async function listFeedback(){
  const db = await openDb();
  return new Promise((resolve,reject)=>{
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = ()=>resolve(req.result || []);
    req.onerror = ()=>reject(req.error);
  });
}
function closeModal(){ $("modal").classList.add("hidden"); }
function openModal(){ $("modal").classList.remove("hidden"); }

$("feedbackBtn").addEventListener("click", openModal);
$("closeModal").addEventListener("click", closeModal);
$("modal").addEventListener("click", (e)=>{ if(e.target.id==="modal") closeModal(); });

let lastFbTs = 0;
$("saveFbBtn").addEventListener("click", async ()=>{
  const now = Date.now();
  if(now - lastFbTs < 5000){
    safeText("fbHint", "Rate limit: please wait a few seconds 🙏");
    return;
  }
  const rating = $("fbRating").value;
  const lat = parseFloat($("fbLat").value);
  const lon = parseFloat($("fbLon").value);
  const depth = parseInt($("fbDepth").value,10);
  const notes = ($("fbNotes").value || "").slice(0, 500);

  // validation
  if(!Number.isFinite(lat) || !Number.isFinite(lon)){
    safeText("fbHint", "Please set lat/lon (click on map) ✅");
    return;
  }
  if(lat < state.grid.lat_min-2 || lat > state.grid.lat_max+2 || lon < state.grid.lon_min-2 || lon > state.grid.lon_max+2){
    safeText("fbHint", "Lat/Lon outside AOI bounds ⚠️");
    return;
  }

  const rec = {
    id: `${now}_${Math.round(lat*10000)}_${Math.round(lon*10000)}`,
    timestamp: new Date(now).toISOString(),
    lat, lon,
    species: state.species,
    gear_depth_m: depth,
    rating,
    notes,
    run_id: state.runId,
    variant: state.variant,
    model: state.model,
  };
  await saveFeedback(rec);
  lastFbTs = now;
  safeText("fbHint", "Saved locally ✅ (IndexedDB)");
  setTimeout(()=>{safeText("fbHint", "Saved to IndexedDB. Anti‑spam: rate‑limit + basic validation.");}, 2200);
  closeModal();
});

$("exportFbBtn").addEventListener("click", async ()=>{
  const all = await listFeedback();
  const blob = new Blob([JSON.stringify(all, null, 2)], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `seydyaar_feedback_export.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

/* ------------------------------
   Bootstrap
------------------------------ */
initMap();
// Ensure tiles render even if the layout/CSS loads slightly later
setTimeout(()=>{ try{ map?.invalidateSize(true); }catch(_){} }, 120);
refreshMeta().catch(err=>{
  console.error(err);
  toast(lang==="fa" ? "داده‌ای در مسیر /latest پیدا نشد. اگر هنوز خروجی تولید نکردی، Workflow GitHub Action را اجرا کن." : "No data found under /latest. If you haven't generated outputs yet, run the GitHub Action (Run generator) to create docs/latest.", "err", lang==="fa"?"خطا":"Error");
  const hint = $("dirtyHint");
  if(hint) hint.textContent = (lang==="fa") ? "داده موجود نیست — ابتدا خروجی بساز" : "No data — generate outputs first";
});
