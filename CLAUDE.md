# Chill at My Orchard

Team project for the Reboot the Earth Hackathon (UC Davis x UN, Oct 23–24, 2026), Chill Hours Challenge from the UC Davis Fruit and Nut Research and Information Center (FNRIC). See `README.md` for how to run it and the endpoint list.

**Goal:** chill and growing degree days (GDD) for a grower's own orchard, with a stated error, where CIMIS fails them: steep elevation changes and removed stations (e.g. Durham, station 12). It answers: how much so far, how does that compare to past years, and what to expect in the next one to two weeks.

**Deadlines:** code freeze **Saturday 2:00pm**; submit on Unite Ideas by **3:00pm**. Judging is on technical execution, creativity, and impact for growers. A smaller thing that works beats a bigger thing that doesn't.

## How to work with teammates (read this first)

Five people with different experience levels share this repo. Everyone must be able to explain their own code to a judge. So:

- **Explain before and after.** Before writing code, briefly say what you plan to change and why. After, summarize what changed in plain language a beginner could follow.
- **Teach, don't just do.** When you use a concept the user may not know (an API call, a pandas resample, a Streamlit callback), add one sentence explaining it.
- **Keep changes small.** One feature or fix per task. If a request would touch many files, propose splitting it.
- **Stay in your lane.** Each file has an owner (table below). Only edit files for the current task, and ask before changing another owner's file or anything shared (`config.py`, the data contract, API responses).
- **Readable over clever.** Short functions, descriptive names, a docstring on every function, comments for anything non-obvious.
- **When asked to explain code,** walk through it step by step and point out edge cases, especially missing data.

## Project structure and owners

```
frontend/app.py     Streamlit app; talks to the backend over HTTP only (BACKEND_URL)   App
backend/app/
  data.py           CIMIS fetch + cache, gap filling, synthetic data                   Data
  chill.py          chill hours, Utah units, chill portions, GDD                        Chill and GDD
  interp.py         estimate at any point (IDW + elevation lapse rate), validation     Interpolation
  forecast.py       Open-Meteo 14-day forecast                                          Forecast
  seasons.py        season windows (start/end dates)
  main.py           FastAPI endpoints
  config.py         ALL shared settings; change values here, never hardcode them
backend/tests/      pytest tests
pitch/              pitch and submission materials                                      Forecast/pitch
```

**Data contract:** every stage reads and writes one hourly table with columns `station_id, lat, lon, elev_m, datetime, temp_F, source, is_filled`. Don't change it without telling the team.

## Domain rules (match the existing code)

`backend/app/chill.py` already implements these to match FNRIC. Don't rewrite them with simpler formulas.

- **Chill hours:** `32°F < T <= 45°F` (FNRIC's boundary convention). Also reported: hours at or below 45°F, Utah units (FNRIC's bins), and chill portions (Dynamic Model).
- **GDD:** single-sine method (Zalom et al. 1983) in °C·days, thresholds per crop in `config.GDD_THRESHOLDS_C` (almond: 5°C lower, 35°C upper).
- **Start dates:** chill counts from `config.CHILL_START` (Nov 1); GDD counts from bloom, `config.GDD_START` (Feb 20, an assumption to confirm with FNRIC). Every result shown to a grower must say what date counting started.
- **Missing and faulty data:** gaps up to `config.MAX_FILL_HOURS` (3 hours) are filled by linear interpolation and marked `is_filled`. Longer gaps and faulty stretches (stuck, spike, drift, impossible values; see `sensor_health.py`) are repaired only when the alarm is high confidence (nearest neighbor within `config.SENSOR_REFERENCE_MAX_KM`): anchored IDW first, then anchored Open-Meteo, then linear, the order tested in `data/validation/fill_experiment.csv`. Every repaired hour is labeled with its source and counted in the response (`sensor_alerts`, `/sensor/health`). Low-confidence alarms are reported but the data is not changed. Hours nothing can fill stay missing and are reported as `hours_missing`. `config.SENSOR_REPAIR=false` turns repair off. Never silently drop or fill data.
- **CIMIS API:** `https://et.water.ca.gov/api/data`, one station and one season per request (large requests are refused). Cache responses in `data/cache/`.
- **Out of scope:** evapotranspiration (ET).

## Running, testing, secrets

- **Run without Docker** (works in Codespaces): `cd backend && uvicorn app.main:app --reload`, then in a second terminal `cd frontend && streamlit run app.py`. Or `docker compose up --build`.
- **Data source:** `DATA_SOURCE=synthetic` (default, no key or internet needed) or `cimis`.
- **Tests:** `cd backend && python -m pytest -q`. Run before every PR; GitHub Actions runs them on every push. Add a test when you change `chill.py`, `interp.py`, or `seasons.py`.
- **Dependencies:** Python 3.12. Add packages to `backend/requirements.txt` or `frontend/requirements.txt`, and to the PR description.
- **The CIMIS key** comes from the `CIMIS_APP_KEY` environment variable (a Codespaces secret, or `.env` for Docker). **Never hardcode it, print it, log it, or commit it.** The repo and the submission will be public.
- Keep the credits in `README.md` (CIMIS, Open-Meteo, FNRIC, Dynamic Model authors) up to date when adding a data source.

## Git

- Work on a branch named for the task (`chill-portions-test`, `map-click`), never directly on `main`.
- Commit messages say what changed in plain words.
- Open a small PR into `main` and fill in the template. Another teammate reviews and merges. Pull before every standup.
