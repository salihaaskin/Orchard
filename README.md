# Chill at My Orchard

Reboot the Earth Hackathon (UC Davis x UN, Oct 23–24, 2026) · Chill Hours Challenge

Chill and growing degree days (GDD) for a grower's **own orchard**, with a stated error, in the two places CIMIS fails them: where elevation changes quickly, and where stations were removed (e.g. Durham, station 12, closed July 2025).

It answers the grower's three questions:

1. How much chill or GDD have I accumulated so far?
2. How does that compare to past years?
3. How much should I expect in the next week or two?

## Run it in VS Code (Mac, Windows or Linux)

You need [Python 3.12](https://www.python.org/downloads/), [VS Code](https://code.visualstudio.com/) with the **Python** extension, and Git.

**One-time setup:**

```bash
git clone https://github.com/ada-lsw/chill-at-my-orchard
cd chill-at-my-orchard
python -m venv .venv
```

Activate the environment, then install both sets of libraries:

```bash
# Mac / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r backend/requirements.txt -r frontend/requirements.txt
```

In VS Code: open the `chill-at-my-orchard` folder, then Command Palette → **Python: Select Interpreter** → pick `.venv`.

**The key:** copy `.env.example` to `.env` in the repo root and fill it in. `.env` is git-ignored; never commit it.

```
DATA_SOURCE=cimis
CIMIS_APP_KEY=your-key-here
```

Leave `DATA_SOURCE=synthetic` to work offline with fake data.

**Run:** Run and Debug panel (Ctrl/Cmd+Shift+D) → **Backend + Frontend** → play. Or in two terminals:

```bash
cd backend && uvicorn app.main:app --reload
cd frontend && streamlit run app.py
```

- App: http://localhost:8501
- API docs: http://localhost:8000/docs

**Tests:** `cd backend && python -m pytest -q`

Docker files are kept in the repo as an optional alternative (`docker compose up --build`), but the team runs without it.

## How it's built

```
frontend/  Streamlit app — map, demo buttons, three answers, chart, proof panel
              talks to the backend over HTTP only (BACKEND_URL)
backend/   FastAPI
  app/data.py      CIMIS fetch + cache, gap filling, synthetic test data
  app/chill.py     chill hours, Utah units, chill portions (Dynamic Model), GDD
  app/interp.py    estimate at any point: IDW + elevation lapse rate; leave-one-out
  app/forecast.py  Open-Meteo 14-day forecast, cached for offline demos
  app/openmeteo_hist.py  Open-Meteo past hourly temperatures (ERA5-Land), cached
  app/source_compare.py  scores nearest station vs IDW vs Open-Meteo against a real station
  app/source_select.py   picks the source per orchard from validation results, with expected error
  scripts/build_validation_table.py  rebuilds data/validation/loso_table.csv (python -m scripts.build_validation_table)
  app/seasons.py   season windows (chill from Nov 1, GDD from Mar 1)
  app/main.py      API: /estimate, /validate, /stations, /demo, /health
  app/config.py    all shared settings — change values here, not in code
data/cache/  cached station data (git-ignored, shared into Docker as a volume)
```

| Endpoint | What it answers |
| --- | --- |
| `GET /estimate?lat=&lon=&as_of=&crop=&exclude=` | Questions 1–3 for any point; `uncertainty` gives the expected error (± and ~80% range) of the hourly temperature and each chill total, from stations we hid and estimated |
| `GET /validate?station_id=&as_of=` | Hide a station, estimate it, report error with and without the elevation adjustment; `method_comparison` scores nearest station vs IDW vs Open-Meteo (MAE, RMSE, bias, % within ±2 °C, chill/GDD error) on the same hours |
| `GET /validate/all?lat=&lon=&radius_km=&max_stations=` | Same comparison for every active station near a point, grouped by distance to the nearest station |
| `GET /source?lat=&lon=` | Source Selection Engine: which method to trust at this orchard and the error to expect, from similar stations in `data/validation/loso_table.csv` |
| `GET /source/evaluation` | On stations never seen: does choosing a method per orchard beat always using one, and which model best predicts the reported error (the winner uses the IDW-vs-Open-Meteo disagreement) |

## Data contract

Every stage reads and writes one hourly table:
`station_id, lat, lon, elev_m, datetime, temp_F, source, is_filled`

## Who owns what

| Folder | Owner |
| --- | --- |
| `backend/app/data.py` | Data |
| `backend/app/chill.py` | Chill and GDD |
| `backend/app/interp.py` | Interpolation and validation |
| `frontend/` | App |
| `backend/app/forecast.py`, `pitch/` | Forecast, pitch, submission |

## Team rules

- Work on a branch, open small pull requests into `main`, and pull before every standup.
- Tests run on every push (GitHub Actions). Run them locally with `cd backend && python -m pytest -q`.
- Never commit `.env` or the CIMIS key.
- Code freeze: Saturday 2:00pm.

## Known gaps (TODO)

- [ ] Confirm CIMIS response field names against a real response (`HlyAirTmp`, `Hour`, station coordinates)
- [ ] Pick the foothill demo station (`config.DEMO_ORCHARDS["foothill"]`)
- [ ] Confirm GDD base temperatures with FNRIC (`config.GDD_BASE_F`)
- [ ] Cross-check chill portions for one station against chillR
- [ ] Optional: PRISM fallback for orchards far from any station

## Data and credits

CIMIS (California Department of Water Resources) · Open-Meteo (forecast, elevation, and Historical Weather API; Copernicus DEM; ERA5-Land, contains modified Copernicus Climate Change Service information) · chill models as described by UC Davis FNRIC · Dynamic Model after Fishman et al. (1987) and Erez et al. (1990), following chillR (Luedeling).
