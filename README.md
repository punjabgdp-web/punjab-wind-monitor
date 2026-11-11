# Punjab Wind Monitor

Animated wind direction map for Punjab (Pakistan) airports: the **past 6 hours of observed winds** (METAR) and the **next 6 hours of official forecasts** (TAF), with gaps filled from the Open-Meteo model.

**Live site:** `https://<your-username>.github.io/<repo-name>/`

## How it works

| Part | What it does |
|---|---|
| `index.html` | The map and animation. Loads `data/latest.json` on open and checks for a newer file every 5 minutes. Falls back to an embedded snapshot if the file is missing. |
| `scripts/fetch_data.py` | Pulls METAR and TAF reports for OPIS, OPST, OPLA, VIAR, OPFA, OPMT, OPDG, OPBW and OPRK from NOAA's Aviation Weather Center, plus Open-Meteo model winds for cities and a background grid. Writes `data/latest.json`. Python standard library only. |
| `.github/workflows/update-data.yml` | Runs the script every 30 minutes and commits the new data file. You can also run it by hand: **Actions → Update wind data → Run workflow**. |

## Adding an airport

Add a line to `AIRPORTS` in `scripts/fetch_data.py` (ICAO code, name, short label, latitude, longitude) and push. The workflow reruns automatically.

## Notes

- GitHub can delay scheduled runs by a few minutes at busy times. It also pauses schedules in repositories with no activity for 60 days, but the bot's data commits normally count as activity.
- Rahim Yar Khan, Bahawalpur and D.G. Khan often publish no METAR, so they will usually show model values, tagged `MODEL` on the page.
- Data: [NOAA Aviation Weather Center Data API](https://aviationweather.gov/data/api/) · [Open-Meteo](https://open-meteo.com/)
