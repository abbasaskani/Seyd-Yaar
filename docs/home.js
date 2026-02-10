let deferredPrompt = null;

const strings = {
  en: {
    h1: "Habitat × Operations → Catch Probability 🐟🌊",
    p1: "Two scientific maps—Habitat Suitability (Phabitat) and Operational Feasibility (Pops)—combine into a single catchability score: Pcatch = Phabitat × Pops. Includes uncertainty (ensemble agreement/spread), explainable top‑10 hotspots, and offline install.",
    launch: "Launch App",
    install: "Install PWA",
    prevTitle: "Latest preview"
  },
  fa: {
    h1: "زیستگاه × عملیات → احتمال صید 🐟🌊",
    p1: "دو نقشه علمی—مناسبت زیستگاه (Phabitat) و امکان‌پذیری عملیاتی (Pops)—در هم ضرب می‌شوند: Pcatch = Phabitat × Pops. همراه با عدم‌قطعیت (agreement/spread)، Top‑10 توضیح‌پذیر و نصب آفلاین.",
    launch: "اجرای اپ",
    install: "نصب اپ",
    prevTitle: "آخرین پیش‌نمایش"
  }
};

let lang = localStorage.getItem("lang") || "en";
function applyLang(){
  const t = strings[lang];
  document.getElementById("h1").textContent = t.h1;
  document.getElementById("p1").textContent = t.p1;
  document.getElementById("launchBtn").textContent = t.launch;
  document.getElementById("installBtn").textContent = t.install;
  document.getElementById("prevTitle").textContent = t.prevTitle;
  document.body.dir = (lang === "fa") ? "rtl" : "ltr";
}

document.getElementById("langToggle").addEventListener("click", ()=>{
  lang = (lang === "en") ? "fa" : "en";
  localStorage.setItem("lang", lang);
  applyLang();
});

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  const btn = document.getElementById("installBtn");
  btn.disabled = false;
});

document.getElementById("installBtn").addEventListener("click", async ()=>{
  if(!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  document.getElementById("installBtn").disabled = true;
});

async function loadMeta(){
  try{
    const r = await fetch("latest/meta_index.json", {cache:"no-store"});
    const idx = await r.json();
    const latest = idx.latest_run_id;
    const run = idx.runs.find(x=>x.run_id===latest);
    const meta = (run?.created_utc) ? new Date(run.created_utc).toISOString().slice(0,16).replace("T"," ")+" UTC" : "—";
    document.getElementById("prevMeta").textContent = meta;
  }catch(e){
    document.getElementById("prevMeta").textContent = "—";
  }
}

applyLang();
loadMeta();
