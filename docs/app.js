/* Seyd‑Yaar app.js — dynamic map + aggregation + uncertainty + feedback 💠🌊 */
const $ = (id) => document.getElementById(id);

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
  $("subtitle").textContent = t.subtitle;
  $("lblRun").textContent = t.Run;
  $("lblVariant").textContent = t.Variant;
  $("lblSpecies").textContent = t.Species;
  $("lblModel").textContent = t.Model;
  $("lblMap").textContent = t.Map;
  $("lblAgg").textContent = t.Aggregation;
  $("lblFrom").textContent = t.From;
  $("lblTo").textContent = t.To;
  $("sumTop").textContent = t.Top;
  $("sumProfile").textContent = t.Profile;
  $("sumAudit").textContent = t.Audit;
  $("downloadPngBtn").textContent = t.DownloadPNG;
  $("downloadGeoBtn").textContent = t.DownloadGeo;
  $("feedbackBtn").textContent = t.Feedback;
  $("exportFbBtn").textContent = t.ExportFb;
  $("fbLblRating").textContent = t.Rating;
  $("fbLblDepth").textContent = t.Depth;
  $("fbLblNotes").textContent = t.Notes;
  $("saveFbBtn").textContent = t.SaveLocal;
  $("qcHint").textContent = t.qcHint;
  $("gapHint").textContent = t.gapHint;
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
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 12,
    attribution: '&copy; OpenStreetMap'
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);

  map.on("click", (e)=>{
    if(!e?.latlng) return;
    $("fbLat").value = e.latlng.lat.toFixed(4);
    $("fbLon").value = e.latlng.lng.toFixed(4);
  });

  // offscreen canvas
  state.canvas = document.createElement("canvas");
  state.ctx = state.canvas.getContext("2d", {willReadFrequently:false});
}

/* ------------------------------
   Colormap (RdYlGn-like)
------------------------------ */
const stops = [
  {p:0.00, c:[255, 58, 58]},
  {p:0.50, c:[255, 233, 90]},
  {p:1.00, c:[57, 255, 159]},
];
function lerp(a,b,t){return a+(b-a)*t}
function colorFor(v01){
  const v = Math.min(1, Math.max(0, v01));
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
  const el = $("legend");
  el.innerHTML = `
    <div style="font-weight:900; margin-bottom:6px">${title}</div>
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
  markerLayer.clearLayers();
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
  if(!meta){ $("auditBox").textContent="—"; return; }
  $("auditBox").textContent = JSON.stringify({
    run_id: meta.run_id,
    variant: meta.variant,
    species: meta.species,
    defaults: meta.defaults,
    ppp_model: meta.ppp_model,
    grid: meta.grid,
    times: meta.times?.length,
  }, null, 2);
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
    const url = `latest/${state.meta.paths.per_time[key]
      .replace("{time}", timeId)
      .replace("{species}", (state.species||"skipjack").toLowerCase())
      .replace("{model}", (state.modelId||"ensemble").toLowerCase())}`;
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
    const url = `latest/${state.meta.paths.per_time.conf.replace("{time}", tid)}`;
    return fetchBin(url,"f32");
  });
  const arrs = await Promise.all(promises);
  const conf = aggregatePerPixel(arrs, "mean");

  // QC mask if toggle
  if(state.qcOn){
    const qcArrs = await Promise.all(timeIsos.map(async t=>{
      const tid = timeIdFromIso(t);
      const url = `latest/${state.meta.paths.per_time.qc_chl.replace("{time}", tid)}`;
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
    const url = `latest/${tpl.replace("{time}", tid)}`;
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
  // Flat layout: latest/index.json + latest/meta.json (no runs/variants) ✅
  state.index = await fetchJson("latest/index.json");
  state.runPath = "";        // kept for backward-compat internal code
  state.variant = "auto";    // cosmetic
  state.runId = "main";      // cosmetic

  // Hide run/variant (single) for commercial UI
  const runRow = document.querySelector('.row.runRow') || document.getElementById("runRow");
  const varRow = document.querySelector('.row.variantRow') || document.getElementById("variantRow");
  if(runRow) runRow.style.display = "none";
  if(varRow) varRow.style.display = "none";

  // Load global web meta (paths + availability)
  state.runMeta = await fetchJson("latest/meta.json");
  state.meta = state.runMeta;

  // Species list
  const spSelect = $("speciesSelect");
  spSelect.innerHTML = "";
  const speciesList = (state.index.species && state.index.species.length) ? state.index.species : ["skipjack","yellowfin"];
  for(const sp of speciesList){
    const opt = document.createElement("option");
    opt.value = sp;
    opt.textContent = sp[0].toUpperCase() + sp.slice(1);
    spSelect.appendChild(opt);
  }
  if(!state.species) state.species = speciesList[0];
  spSelect.value = state.species;

  spSelect.addEventListener("change", async ()=>{
    state.species = spSelect.value;
    await refreshTimeSelectors();
  });

  await refreshTimeSelectors();
}

async function refreshVariants(){
  // no-op in flat layout
  return;
}

async function refreshTimeSelectors(){
  // Use latest/meta.json availability
  const availableTimeIds = state.runMeta?.available_time_ids || state.index?.time_ids || [];
  state.timeIds = availableTimeIds;
  state.times = availableTimeIds.map(timeIdToIso);
  state.isoToTimeId = {};
  for(let i=0;i<state.times.length;i++){ state.isoToTimeId[state.times[i]] = state.timeIds[i]; }

  // availability info panel
  const lastTid = state.runMeta?.latest_available_time_id || (state.timeIds[state.timeIds.length-1]||null);
  if($("availabilityInfo")){
    if(lastTid){
      const lastIso = timeIdToIso(lastTid);
      $("availabilityInfo").innerHTML = `<b>${lang==="fa"?"آخرین داده":"Latest available data"}</b><br>${lastIso} (UTC)`;
    }else{
      $("availabilityInfo").textContent = (lang==="fa"?"دیتایی پیدا نشد":"No data found");
    }
  }

  // Fill From/To selects
  const fromSel = $("fromTime");
  const toSel = $("toTime");
  fromSel.innerHTML = "";
  toSel.innerHTML = "";
  for(const t of state.times){
    const o1=document.createElement("option"); o1.value=t; o1.textContent=t; fromSel.appendChild(o1);
    const o2=document.createElement("option"); o2.value=t; o2.textContent=t; toSel.appendChild(o2);
  }

  // default range
  if(state.times.length){
    fromSel.value = state.times[Math.max(0, state.times.length-2)];
    toSel.value = state.times[state.times.length-1];
    state.tFromIso = fromSel.value;
    state.tToIso = toSel.value;
  }

  fromSel.onchange = ()=>{ state.tFromIso = fromSel.value; };
  toSel.onchange = ()=>{ state.tToIso = toSel.value; };

  // Ensure model select (kept)
  await refreshModelSelect();
}

async function refreshModelSelect(){
  const modelSelect = $("modelSelect");
  // keep existing options if already populated
  if(!modelSelect.options.length){
    ["Ensemble (default)"].forEach((t,i)=>{
      const opt=document.createElement("option");
      opt.value = "ensemble";
      opt.textContent = t;
      modelSelect.appendChild(opt);
    });
  }
  state.modelId = modelSelect.value || "ensemble";
  modelSelect.onchange = ()=>{ state.modelId = modelSelect.value; };
}


async function loadSpeciesMetaAndInit(){
  // Flat layout: global latest/meta.json already loaded ✅
  state.species = $("speciesSelect")?.value || state.species || "skipjack";
  state.meta = state.runMeta || state.meta;
  await initMapAndUIFromMeta();
}


/* ------------------------------
   UI events
------------------------------ */
["speciesSelect","modelSelect","mapSelect","aggSelect","t0Select","t1Select"].forEach(id=>{
  $(id).addEventListener("change", async ()=>{
    state.species = $("speciesSelect").value;
    state.model = $("modelSelect").value;
    state.map = $("mapSelect").value;
    state.agg = $("aggSelect").value;

    // if species changed, reload meta (different profile + files)
    if(id==="speciesSelect"){
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
  $("dirtyHint").textContent = (lang==="fa") ? "در حال تحلیل..." : "Analyzing…";
  $("top10Table").innerHTML = `<div class="skeleton" style="height:180px"></div>`;
  toast(lang==="fa" ? "در حال بارگذاری داده‌ها" : "Loading data…", "ok", lang==="fa"?"تحلیل":"Analyze");
  try{ await computeAndRender(); }
  catch(err){
    console.error(err);
    toast(lang==="fa" ? "داده برای این بازه هنوز آماده نیست. اگر تحلیل در حال اجراست، کمی بعد دوباره امتحان کن." : "Data not available for this selection yet. If a backend run is in progress, try again later.", "warn", lang==="fa"?"در دسترس نیست":"Not ready");
    $("dirtyHint").textContent = (lang==="fa") ? "داده هنوز آماده نیست" : "Not ready yet";
    return;
  }
  $("dirtyHint").textContent = (lang==="fa") ? "انجام شد ✅" : "Done ✅";
});

$("lookbackSelect").addEventListener("change", ()=>{ applyLookback(); setDirty("Lookback changed. Press Analyze."); });
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
  $("dirtyHint").textContent = msg || "Change settings, then press Analyze.";
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
  $("aoiStatus").textContent = on ? "AOI: active ✅ (mask applied)" : "AOI: none (using server mask)";
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
  $("filterAoiStatus").textContent = on ? "Filter: active ✅" : "Filter: none";
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
  $("minPVal").textContent = `${$("minP").value}%`;
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
  state.playing = true;
  $("playBtn").textContent = "⏸ Pause";

  // Keep the selected range length fixed, and slide it forward
  const rangeLen = Math.abs($("t1Select").selectedIndex - $("t0Select").selectedIndex);

  const tick = async ()=>{
    const i0 = $("t0Select").selectedIndex;
    const i1 = $("t1Select").selectedIndex;
    const dir = (i1 >= i0) ? 1 : -1; // preserve ordering

    let next0 = i0 + dir;
    let next1 = next0 + dir*rangeLen;

    // wrap
    if(next0 < 0) next0 = state.times.length-1;
    if(next0 >= state.times.length) next0 = 0;
    if(next1 < 0) next1 = state.times.length-1;
    if(next1 >= state.times.length) next1 = 0;

    $("t0Select").selectedIndex = next0;
    $("t1Select").selectedIndex = next1;
    setDirty();
  };

  state.timer = setInterval(tick, 900);
}
function stopPlay(){
  state.playing = false;
  $("playBtn").textContent = "▶ Play";
  if(state.timer) clearInterval(state.timer);
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
    $("fbHint").textContent = "Rate limit: please wait a few seconds 🙏";
    return;
  }
  const rating = $("fbRating").value;
  const lat = parseFloat($("fbLat").value);
  const lon = parseFloat($("fbLon").value);
  const depth = parseInt($("fbDepth").value,10);
  const notes = ($("fbNotes").value || "").slice(0, 500);

  // validation
  if(!Number.isFinite(lat) || !Number.isFinite(lon)){
    $("fbHint").textContent = "Please set lat/lon (click on map) ✅";
    return;
  }
  if(lat < state.grid.lat_min-2 || lat > state.grid.lat_max+2 || lon < state.grid.lon_min-2 || lon > state.grid.lon_max+2){
    $("fbHint").textContent = "Lat/Lon outside AOI bounds ⚠️";
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
  $("fbHint").textContent = "Saved locally ✅ (IndexedDB)";
  setTimeout(()=>{$("fbHint").textContent = "Saved to IndexedDB. Anti‑spam: rate‑limit + basic validation.";}, 2200);
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
refreshMeta().catch(err=>{
  console.error(err);
  alert("Failed to load demo data. Make sure you generated /docs/latest with the backend demo generator.");
});