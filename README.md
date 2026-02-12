# Seyd‑Yaar (صیدیار) — Tuna Catch Probability PWA 🐟🌊

A lightweight **installable PWA** + a **Python generator** that produces:
- **Habitat Suitability** (Phabitat)
- **Operational Feasibility** (Pops)
- **Catch Probability** (Pcatch = Phabitat × Pops) ✅ default
- **Uncertainty** maps: **Agreement** + **Spread/Std**
- **Explainability**: per‑species profile (Skipjack/Yellowfin), weights, curves, top‑10 hotspots, covariate table
- **Audit / versioning** via `meta.json` (run time, data sources, model versions, QC/gap‑fill flags, missing, etc.)

> In this demo ZIP, the generator can create **synthetic demo data** (fast) so the UI works end‑to‑end offline.
> Production hooks are included for **AIS effort (Global Fishing Watch)**, **MaxEnt/PPP presence‑only**, and **GeoTIFF/COG export**.

---

## Quick start (Demo)

### 1) Python (generator)
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt

# Fast demo (few timesteps)
python -m seydyaar demo-generate --date today --fast
```

This writes outputs to `../docs/latest/...` (static website reads from there).

### 2) Open the PWA
Open `docs/index.html` (or serve `docs/`):
```bash
cd docs
python -m http.server 8080
```
Then open the printed local URL in your browser.

---

## Production notes (what’s scaffolded + needs real credentials/data)

- **AIS Effort proxy (Global Fishing Watch)**  
  Implemented as a provider + client wrapper, but needs **API key/token** and network.
- **MaxEnt / PPP real training**  
  Works when presence points exist (AIS proxy / CSV upload). Bias correction is supported (background sampling).
- **COG/GeoTIFF**  
  Demo writes GeoTIFF; COG‑style tiling/overviews included (no `rio-cogeo` dependency).

---

## Credentials 🔐 (Copernicus username/password)

Copernicus Marine credentials are **NOT stored inside any file in this repo** ✅

The generator reads them from environment variables:

```bash
export COPERNICUS_MARINE_USERNAME="<your_user>"
export COPERNICUS_MARINE_PASSWORD="<your_password>"
```

Where it is read:

- `backend/seydyaar/pipeline/run_daily.py` → function `_try_copernicus_layers(...)`
  - reads `os.getenv("COPERNICUS_MARINE_USERNAME")` and `os.getenv("COPERNICUS_MARINE_PASSWORD")`
  - if missing, it logs an error in provider status and falls back to synthetic demo layers.

---

## AOI workflow (simple + flexible) 🗺️

In the PWA (`docs/app.html`):

- **AOI (Analysis area)**: limits the *computation + overlay + hotspot search* to your chosen region.
- **AOI (Filter results after analysis)**: a second AOI applied *after* analysis to filter the displayed pixels and Top list.

You can set each AOI via:

1) Upload GeoJSON
2) Paste GeoJSON text
3) Enter BBOX (lat/lon min/max)
4) Draw polygon/rectangle directly on the map (Leaflet‑Draw)

Time resolution is fixed at **6 hours** in the UI (and the generator uses `step_hours=6` by default).

---

## Repo layout

- `docs/` — GitHub Pages static site (PWA)
- `docs/latest/` — generated data (meta + binaries)
- `backend/seydyaar/` — generator + models + providers

---

## Credits

Designed by: **عباس آسکانی — Abbas Askani**  
**Askani Fishing Data company**


## AOI input (inside the app)
You can set the analysis area in 4 ways:
- Upload GeoJSON
- Paste GeoJSON text
- BBOX (lat/lon min/max)
- **Points list** (lat,lon per line) → builds a Polygon

There is also a second AOI used only to **filter results after analysis** (same 4 input methods).
