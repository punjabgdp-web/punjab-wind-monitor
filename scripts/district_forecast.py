#!/usr/bin/env python3
"""
District wind forecast for Punjab: next 24 hours from the next full hour (PKT).

Per district (Open-Meteo 10 m wind at the district HQ, hourly):
  * eight 3-hour blocks: scalar mean speed + vector-mean direction of the block's 3 hourly values
  * 24-hour mean speed, net (resultant) direction, steadiness, hours under 2 km/h, peak hour
For districts with an airport, the airport TAF's prevailing wind over the same 24 h is summarised too.

Writes data/district_forecast.json and data/district_forecast.csv.
"""
import csv, json, math, os, sys, urllib.parse
from datetime import timedelta, datetime

sys.path.insert(0, os.path.dirname(__file__))
from fetch_data import get, records, parse_taf, taf_at, UTC, PKT  # noqa: E402
from district_report import DISTRICTS, vec, from_dir, compass, mean_wind  # noqa: E402


def main():
    now = datetime.now(UTC)
    t0 = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    hours = [t0 + timedelta(hours=k) for k in range(24)]
    blocks = [hours[i:i + 3] for i in range(0, 24, 3)]

    q = urllib.parse.urlencode({
        "latitude": ",".join(str(d[2]) for d in DISTRICTS),
        "longitude": ",".join(str(d[3]) for d in DISTRICTS),
        "hourly": "wind_speed_10m,wind_direction_10m",
        "past_hours": 0, "forecast_hours": 27, "timezone": "UTC", "wind_speed_unit": "kmh",
    })
    js = json.loads(get("https://api.open-meteo.com/v1/forecast?" + q))
    if isinstance(js, dict):
        js = [js]
    model = [{t: (s, d) for t, s, d in zip(j["hourly"]["time"], j["hourly"]["wind_speed_10m"], j["hourly"]["wind_direction_10m"])} for j in js]

    ids = ",".join(sorted({d[4] for d in DISTRICTS if d[4]}))
    tafs = {}
    try:
        for r in records(get(f"https://aviationweather.gov/api/data/taf?ids={ids}&format=raw")):
            p = parse_taf(r, now)
            if p and p[3] and (p[0] not in tafs or p[1] > tafs[p[0]][1]):
                tafs[p[0]] = p
    except Exception as e:
        print("TAF fetch failed:", e, file=sys.stderr)

    def val(i, t):
        v = model[i].get(t.strftime("%Y-%m-%dT%H:00"))
        if not v or v[0] is None:
            return None
        return (v[0], v[1] if v[0] >= 0.5 else None)

    blbl = [f"{b[0].astimezone(PKT):%H:%M}–{(b[-1] + timedelta(hours=1)).astimezone(PKT):%H:%M}" for b in blocks]
    out = []
    for i, (name, div, lat, lon, icao) in enumerate(DISTRICTS):
        hv = [val(i, t) for t in hours]
        ok = [(t, v) for t, v in zip(hours, hv) if v]
        bl = []
        for b in blocks:
            mw = mean_wind([v for v in (val(i, t) for t in b) if v])
            bl.append(None if mw is None else {"spd": round(mw[0], 1), "dir": None if mw[1] is None else round(mw[1]),
                                               "cmp": "calm" if mw[1] is None or mw[0] < 1 else compass(mw[1])})
        mw = mean_wind([v for _, v in ok])
        peak_t, peak_v = max(ok, key=lambda x: x[1][0])
        low = [t for t, v in ok if v[0] < 2]
        rec = {"district": name, "division": div, "airport": icao, "blocks": bl,
               "mean_spd": round(mw[0], 2), "net_dir": None if mw[1] is None else round(mw[1]),
               "net_cmp": "variable" if mw[1] is None else compass(mw[1]),
               "resultant_spd": round(mw[2], 2), "steadiness": round(mw[2] / mw[0], 2) if mw[0] > 0 else 0,
               "hours_lt2": len(low),
               "peak": {"spd": round(peak_v[0], 1), "cmp": compass(peak_v[1]) if peak_v[1] is not None else "calm",
                        "time": peak_t.astimezone(PKT).strftime("%a %H:%M")}}
        if icao and icao in tafs:
            smp = []
            for t in hours:
                w = taf_at(tafs[icao], t)
                if w:
                    smp.append((w["spd"], w.get("dir")))
            if len(smp) >= 12:
                tw = mean_wind(smp)
                rec["taf"] = {"icao": icao, "hours": len(smp), "mean_spd": round(tw[0], 1),
                              "net_cmp": "variable" if tw[1] is None else compass(tw[1]),
                              "net_dir": None if tw[1] is None else round(tw[1])}
        out.append(rec)
    out.sort(key=lambda r: (r["mean_spd"], r["district"]))
    for k, r in enumerate(out, 1):
        r["rank"] = k

    rep = {"generated": now.isoformat(timespec="seconds"),
           "generatedPKT": now.astimezone(PKT).strftime("%d %b %Y, %H:%M PKT"),
           "window": f"{t0.astimezone(PKT):%d %b %H:%M} – {(t0 + timedelta(hours=24)).astimezone(PKT):%d %b %H:%M} PKT",
           "block_start_dates": [b[0].astimezone(PKT).strftime("%d %b") for b in blocks],
           "blocks": blbl, "districts": out, "tafs": sorted(tafs)}
    base = os.path.join(os.path.dirname(__file__), "..", "data")
    with open(os.path.join(base, "district_forecast.json"), "w") as f:
        json.dump(rep, f, indent=1)
    with open(os.path.join(base, "district_forecast.csv"), "w", newline="") as f:
        w = csv.writer(f)
        hdr = [f"{d} {b}" for d, b in zip(rep["block_start_dates"], blbl)]
        w.writerow(["rank", "district", "division"] + [f"{h} speed km/h" for h in hdr] + [f"{h} dir" for h in hdr]
                   + ["24h mean speed km/h", "24h net direction (deg)", "24h net direction", "steadiness",
                      "hours under 2 km/h", "peak speed km/h", "peak time PKT", "airport TAF 24h mean km/h", "airport TAF net direction"])
        for r in out:
            t = r.get("taf") or {}
            w.writerow([r["rank"], r["district"], r["division"]]
                       + [b["spd"] if b else "" for b in r["blocks"]]
                       + [(f'{b["cmp"]} {b["dir"]}°' if b and b["dir"] is not None and b["cmp"] != "calm" else (b["cmp"] if b else "")) for b in r["blocks"]]
                       + [r["mean_spd"], r["net_dir"] if r["net_dir"] is not None else "", r["net_cmp"], r["steadiness"],
                          r["hours_lt2"], r["peak"]["spd"], r["peak"]["time"], t.get("mean_spd", ""), t.get("net_cmp", "")])
    print("wrote district forecast", rep["window"], "TAFs:", rep["tafs"])


if __name__ == "__main__":
    main()
