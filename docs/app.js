/* Seyd-Yaar – app.js (Leaflet + overlays)
   - Reads latest/meta_index.json
   - Loads runs/prod_* outputs from docs/latest/
   - Renders probability & uncertainty layers
*/

const $ = (id) => document.getElementById(id);

/* ------------------------------
   State
------------------------------ */
const state = {
  lang: localStorage.getItem("lang") || "en",
  runId: null,
  runPath: null,
  variant: "auto",
  species: localStorage.getItem("species") || "skipjack",
  model: localStorage.getItem("model") || "ensemble",
  map: localStorage.getItem("map") || "pcatch",
  agg: localStorage.getItem("agg") || "p90",
  qcOn: true,
  mask: null,
  meta: null,
  grid: null,
  times: [],
  index: null,
  playing: false,
  timer: null,
  _didFit: false,
  canvas: null
};

/* ------------------------------
   i18n
------------------------------ */
const i18n = {
  en: {
    title: "Seyd-Yaar",
    run: "Run",
    qc: "QC / Gap-Fill",
    species: "Species",
    model: "Model",
    map: "Map",
    agg: "Aggregation",
    from: "From",
    to: "To",
    play: "▶ Play",
    pause: "⏸ Pause",
    downloadPng: "Download PNG",
    downloadGeo: "Download GeoJSON",
    feedback: "+ Feedback",
    exportFeedback: "Export feedback"
  },
  fa: {
    title: "صیدیار",
    run: "ران / خروجی",
    qc: "کنترل‌کیفیت / گپ‌فیل",
    species: "گونه",
    model: "مدل",
    map: "نقشه",
    agg: "تجمیع",
    from: "از",
    to: "تا",
    play: "▶ پخش",
    pause: "⏸ توقف",
    downloadPng: "دانلود PNG",
    downloadGeo: "دانلود GeoJSON",
    feedback: "+ بازخورد",
    exportFeedback: "خروجی بازخورد"
  }
};

function applyLang(){
  const t = i18n[state.lang] || i18n.en;
  $("titleText").textContent = t.title;
  $("lblRun").textContent = t.run;
  $("lblQC").textContent = t.qc;
  $("lblSpecies").textContent = t.species;
  $("lblModel").textContent = t.model;
  $("lblMap").textContent = t.map;
  $("lblAgg").textContent = t.agg;
  $("lblFrom").textContent = t.from;
  $("lblTo").textContent = t.to;
  $("downloadPngBtn").textContent = t.downloadPng;
  $("downloadGeoBtn").textContent = t.downloadGeo;
  $("feedbackBtn").textContent = t.feedback;
  $("exportFeedbackBtn").textContent = t.exportFeedback;
  $("langBtn").textContent = (state.lang === "en") ? "FA" : "EN";
}

$("langBtn").addEventListener("click", ()=>{
  state.lang = (state.lang === "en") ? "fa" : "en";
  localStorage.setItem("lang", state.lang);
  applyLang();
});

/* ------------------------------
   Helpers: fetch
------------------------------ */
async function fetchJson(url){
  const r = await fetch(url, { cache: "no-store" });
  if(!r.ok) throw new Error(`${url} -> ${r.status}`);
  return await r.json();
}

async function fetchBin(url, dtype){
  const r = await fetch(url, { cache: "no-store" });
  if(!r.ok) throw new Error(`${url} -> ${r.status}`);
  const buf = await r.arrayBuffer();
  if(dtype === "u8") return new Uint8Array(buf);
  return new Float32Array(buf);
}

/* ------------------------------
   Time helpers
------------------------------ */
function fmtTime(iso){
  // iso: 2026-02-11T00:00:00Z
  try{
    const d = new Date(iso);
    return d.toISOString().replace("T"," ").replace(".000Z","Z");
  }catch{
    return iso;
  }
}

// Convert ISO to timeId like 0143, 0000, etc (matches backend)
function timeIdFromIso(iso){
  const d = new Date(iso);
  const hh = String(d.getUTCHours()).padStart(2,"0");
  const mm = String(d.getUTCMinutes()).padStart(2,"0");
  return `${hh}${mm}`;
}

// selected times between t0..t1 inclusive
function getSelectedTimes(){
  const t0 = $("t0Select").selectedIndex;
  const t1 = $("t1Select").selectedIndex;
  const a = Math.min(t0,t1);
  const b = Math.max(t0,t1);
  return state.times.slice(a, b+1);
}

/* ------------------------------
   Leaflet map init
------------------------------ */
let map, overlayLayer, markerLayer;

function initMap(){
  map = L.map("map", { zoomControl: false, preferCanvas: true });
  L.control.zoom({ position: "topright" }).addTo(map);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 10,
    attribution: "&copy; OpenStreetMap"
  }).addTo(map);

  overlayLayer = L.imageOverlay("", [[0,0],[0,0]], { opacity: 0.75 }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
}

function setLegend(title){
  $("legendTitle").textContent = title;
}

function mapTitle(){
  const mapKey = $("mapSelect").value;
  const modelKey = $("modelSelect").value;
  if(mapKey==="pcatch") return `Pcatch (Habitat×Ops) – ${modelKey}`;
  if(mapKey==="phab") return `Habitat – ${modelKey}`;
  if(mapKey==="pops") return "Operational Feasibility (Ops)";
  if(mapKey==="agree") return "Ensemble Agreement";
  if(mapKey==="spread") return "Ensemble Spread (Std)";
  if(mapKey==="conf") return "Confidence / Coverage";
  return `Pcatch – ${modelKey}`;
}

function colorRamp(v){
  // v in [0,1] => red->yellow->green
  v = Math.max(0, Math.min(1, v));
  let r, g, b;
  if(v < 0.5){
    const t = v/0.5;
    r = 255;
    g = Math.floor(255*t);
    b = 0;
  }else{
    const t = (v-0.5)/0.5;
    r = Math.floor(255*(1-t));
    g = 255;
    b = 0;
  }
  return [r,g,b];
}

function renderOverlay(arr, conf){
  const W = state.grid.width, H = state.grid.height;
  const canvas = state.canvas || document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  state.canvas = canvas;

  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(W, H);

  for(let i=0;i<arr.length;i++){
    const v = Number.isFinite(arr[i]) ? arr[i] : 0;
    const [r,g,b] = colorRamp(v);
    const a = conf ? Math.floor(255 * Math.max(0, Math.min(1, conf[i] ?? 1))) : 200;
    img.data[i*4+0] = r;
    img.data[i*4+1] = g;
    img.data[i*4+2] = b;
    img.data[i*4+3] = a;
  }
  ctx.putImageData(img, 0, 0);

  const dataUrl = canvas.toDataURL("image/png");
  const bounds = [
    [state.grid.lat_min, state.grid.lon_min],
    [state.grid.lat_max, state.grid.lon_max]
  ];
  overlayLayer.setUrl(dataUrl);
  overlayLayer.setBounds(bounds);
}

function topKFromArray(arr, k){
  const W = state.grid.width;
  const H = state.grid.height;
  const pts = [];
  for(let i=0;i<arr.length;i++){
    const v = arr[i];
    if(!Number.isFinite(v)) continue;
    pts.push({ idx:i, v });
  }
  pts.sort((a,b)=>b.v-a.v);
  const out = pts.slice(0,k).map(p=>{
    const y = Math.floor(p.idx / W);
    const x = p.idx % W;
    const lon = state.grid.lon_min + (state.grid.lon_max - state.grid.lon_min) * (x/(W-1));
    const lat = state.grid.lat_min + (state.grid.lat_max - state.grid.lat_min) * (y/(H-1));
    return { lon, lat, value: p.v };
  });
  return out;
}

function renderTop10(points, covs){
  markerLayer.clearLayers();
  const list = $("top10List");
  list.innerHTML = "";
  points.forEach((p, i)=>{
    const m = L.circleMarker([p.lat, p.lon], {
      radius: 6,
      weight: 1,
      color: "#fff",
      fillColor: "#39ff88",
      fillOpacity: 0.85
    }).addTo(markerLayer);

    const c = covs && covs[i] ? covs[i] : null;
    const html = `
      <div style="font-size:13px;line-height:1.35">
        <b>#${i+1}</b><br/>
        Prob: <b>${Math.round(p.value*100)}</b><br/>
        Lat/Lon: ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}<br/>
        ${c ? `SST: ${c.sst.toFixed(1)}°C<br/>Chl: ${c.chl.toFixed(3)} mg/m³<br/>Waves: ${c.hs.toFixed(2)} m<br/>Cur: ${c.cur.toFixed(2)} m/s` : ""}
      </div>
    `;
    m.bindPopup(html);

    const row = document.createElement("div");
    row.className = "rowitem";
    row.innerHTML = `<b>#${i+1}</b> ${p.lat.toFixed(3)}, ${p.lon.toFixed(3)} — ${Math.round(p.value*100)}`;
    row.onclick = ()=>{ map.setView([p.lat, p.lon], 7); m.openPopup(); };
    list.appendChild(row);
  });
}

function renderProfile(){
  const p = state.meta?.profile || {};
  $("profileBox").textContent = JSON.stringify(p, null, 2);
}

function renderAudit(){
  const m = state.meta?.meta || {};
  $("auditBox").textContent = JSON.stringify(m, null, 2);
}

/* ------------------------------
   Aggregation
------------------------------ */
function quantile(arr, q){
  const a = Array.from(arr).sort((x,y)=>x-y);
  if(a.length===0) return 0;
  const pos = (a.length-1)*q;
  const base = Math.floor(pos);
  const rest = pos-base;
  if(a[base+1] === undefined) return a[base];
  return a[base] + rest*(a[base+1]-a[base]);
}

function aggregatePerPixel(arrs, method){
  const N = arrs[0].length;
  const out = new Float32Array(N);
  for(let i=0;i<N;i++){
    const vals = [];
    for(let t=0;t<arrs.length;t++){
      const v = arrs[t][i];
      if(Number.isFinite(v)) vals.push(v);
    }
    let v=0;
    if(vals.length===0){ out[i]=NaN; continue; }

    if(method==="mean"){
      v = vals.reduce((a,b)=>a+b,0)/vals.length;
    } else if(method==="median"){
      v = quantile(vals, 0.5);
    } else if(method==="max"){
      v = Math.max(...vals);
    } else { // p90
      v = quantile(vals, 0.90);
    }
    out[i] = v;
  }
  return out;
}

/* ------------------------------
   Covariates sampling for explainability
------------------------------ */
async function loadCovAtPoints(timeIso, points){
  try{
    const tid = timeIdFromIso(timeIso);
    const covPaths = state.meta?.paths?.covariates || null;
    if(!covPaths) return null;

    const loadOne = async (name, dtype="f32")=>{
      const url = `latest/${state.runPath}/${covPaths[name].replace("{time}", tid)}`;
      return await fetchBin(url, dtype);
    };

    const sst = await loadOne("sst","f32");
    const chl = await loadOne("chl","f32");
    const hs  = await loadOne("hs","f32");
    const cur = await loadOne("cur_spd","f32");

    const W = state.grid.width;
    return points.map(p=>{
      // nearest pixel
      const x = Math.round((p.lon - state.grid.lon_min) / (state.grid.lon_max - state.grid.lon_min) * (W-1));
      const y = Math.round((p.lat - state.grid.lat_min) / (state.grid.lat_max - state.grid.lat_min) * (state.grid.height-1));
      const xi = Math.max(0, Math.min(W-1, x));
      const yi = Math.max(0, Math.min(state.grid.height-1, y));
      const idx = yi*W + xi;
      return {
        sst: sst[idx],
        chl: chl[idx],
        hs: hs[idx],
        cur: cur[idx]
      };
    });
  }catch(e){
    console.warn("Covariate sampling failed:", e);
    return null;
  }
}

/* ------------------------------
   Confidence aggregation (QC mask + analysis/forecast)
------------------------------ */
async function getConfAggregated(timeIsos){
  const W = state.grid.width, H = state.grid.height;
  const N = W*H;
  const conf = new Float32Array(N);
  conf.fill(1);

  // base mask (AOI / ocean)
  if(state.mask){
    for(let i=0;i<N;i++){
      if(state.mask[i] === 0) conf[i] = 0;
    }
  }

  // QC mask (chlorophyll) mean across time range
  if(state.qcOn && state.meta?.paths?.per_time?.qc_chl){
    try{
      const qcArrs = await Promise.all(timeIsos.map(async (t)=>{
        const tid = timeIdFromIso(t);
        const url = `latest/${state.runPath}/${state.meta.paths.per_time.qc_chl.replace("{time}", tid)}`;
        return await fetchBin(url,"u8");
      }));
      for(let i=0;i<N;i++){
        if(conf[i]===0) continue;
        let s=0, k=0;
        for(let t=0;t<qcArrs.length;t++){
          s += (qcArrs[t][i] > 0) ? 1 : 0;
          k++;
        }
        conf[i] *= (k>0 ? (s/k) : 1);
      }
    }catch(e){
      console.warn("QC aggregation failed:", e);
    }
  }

  return conf;
}

/* ------------------------------
   Core compute
------------------------------ */
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

    // choose meta key expected for selected map/model
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

    // ✅ Guard: some runs may not include every key (e.g. ppp/ensemble extras)
    const perTime = (state.meta && state.meta.paths && state.meta.paths.per_time) ? state.meta.paths.per_time : {};
    const tpl = perTime[key];

    // If missing, return a NaN-array so the app doesn't crash (and you see a warning)
    if(!tpl || typeof tpl !== "string"){
      console.warn("Missing per_time template for key:", key, "available keys:", Object.keys(perTime));
      const nan = new Float32Array(W*H);
      nan.fill(NaN);
      return nan;
    }

    const url = `latest/${state.runPath}/${tpl.replace("{time}", tid)}`;
    const dtype = (tpl.includes("u8") || tpl.includes("_u8")) ? "u8" : "f32";

    try{
      return await fetchBin(url, dtype);
    }catch(err){
      console.warn("Failed to fetch layer:", {key, url, err});
      const nan = new Float32Array(W*H);
      nan.fill(NaN);
      return nan;
    }
  }

  const arrs = await Promise.all(timeIsos.map(loadLayerForTime));
  let aggMethod = $("aggSelect").value;
  if(mapKey==="conf") aggMethod = "mean";

  const arrAgg = aggregatePerPixel(arrs, aggMethod);

  const confAgg = (mapKey==="conf")
    ? (()=>{ // visualize confidence itself
        const c = new Float32Array(arrAgg.length);
        for(let i=0;i<c.length;i++){
          c[i] = Number.isFinite(arrAgg[i]) ? 1.0 : 0.0;
        }
        return c;
      })()
    : await getConfAggregated(timeIsos);

  setLegend(mapTitle());
  renderOverlay(arrAgg, confAgg);

  if(!state._didFit){
    map.fitBounds([[state.grid.lat_min, state.grid.lon_min],[state.grid.lat_max, state.grid.lon_max]]);
    state._didFit = true;
  }

  const top = topKFromArray(arrAgg, 10);
  const midTime = timeIsos[Math.floor(timeIsos.length/2)];
  const covs = await loadCovAtPoints(midTime, top);
  renderTop10(top, covs);
}

/* ------------------------------
   Meta wiring
------------------------------ */
async function refreshMeta(){
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
  state.runPath = run.path;

  const variantSelect = $("variantSelect");
  variantSelect.innerHTML = "";
  for(const v of run.variants){
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    variantSelect.appendChild(opt);
  }

  state.variant = run.variants[0];
  variantSelect.value = state.variant;

  variantSelect.addEventListener("change", async ()=>{
    state.variant = variantSelect.value;
    await loadSpeciesMetaAndInit();
  });

  await loadSpeciesMetaAndInit();
}

async function loadSpeciesMetaAndInit(){
  state.species = $("speciesSelect").value;

  const url = `latest/${state.runPath}/variants/${state.variant}/species/${state.species}/meta.json`;
  state.meta = await fetchJson(url);
  state.grid = state.meta.grid;

  const maskUrl = `latest/${state.runPath}/${state.meta.paths.mask}`;
  state.mask = await fetchBin(maskUrl, "u8");

  state.times = state.meta.times || [];
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

  const mid = Math.floor(state.times.length/2);
  $("t0Select").selectedIndex = Math.max(0, mid-1);
  $("t1Select").selectedIndex = Math.min(state.times.length-1, mid+1);

  $("speciesSelect").value = state.species;
  $("modelSelect").value = state.model;
  $("mapSelect").value = state.map;
  $("aggSelect").value = state.agg;

  renderProfile();
  renderAudit();
  await computeAndRender();
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

    if(id==="speciesSelect"){
      await loadSpeciesMetaAndInit();
      return;
    }
    await computeAndRender();
  });
});

$("qcToggle").addEventListener("change", async ()=>{
  state.qcOn = $("qcToggle").checked;
  await computeAndRender();
});

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
  const stepH = parseInt($("stepSelect").value,10) || 6;

  // keep the current window size while playing (instead of forcing From==To)
  const span = Math.max(0, $("t1Select").selectedIndex - $("t0Select").selectedIndex);

  const tick = async ()=>{
    const i0 = $("t0Select").selectedIndex;
    let next0 = i0 + 1;
    if(next0 >= state.times.length) next0 = 0;

    let next1 = next0 + span;
    if(next1 >= state.times.length) next1 = state.times.length - 1;

    $("t0Select").selectedIndex = next0;
    $("t1Select").selectedIndex = next1;

    await computeAndRender();
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
   Download
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
  const feats = [];
  markerLayer.eachLayer(l=>{
    const latlng = l.getLatLng();
    feats.push({
      type:"Feature",
      geometry:{ type:"Point", coordinates:[latlng.lng, latlng.lat] },
      properties:{}
    });
  });
  const gj = { type:"FeatureCollection", features: feats };
  const blob = new Blob([JSON.stringify(gj)], { type:"application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `seydyaar_top10_${state.runId}_${state.species}.geojson`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

/* ------------------------------
   Boot
------------------------------ */
function boot(){
  applyLang();
  initMap();
  refreshMeta().catch(e=>{
    console.error(e);
    alert("Failed to load meta_index.json. Check docs/latest/ exists and Pages is serving it.");
  });
}

boot();
