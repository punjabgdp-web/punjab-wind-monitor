#!/usr/bin/env python3
"""
District wind report for Punjab: last 6 complete hours (PKT).

Per district and hour:
  * if the district has an airport with METARs in that hour -> mean of those METARs (observed)
  * otherwise -> Open-Meteo 10 m wind at the district HQ, mean of the hour's start and end values (model)
Hourly mean speed = scalar mean; hourly direction = vector (resultant) mean, meteorological "from".
6-hour net: mean of the 6 hourly speeds, resultant direction of the 6 hourly vectors,
resultant speed and steadiness (resultant / scalar mean).

Writes data/district_report.json and data/district_report.csv.
"""
import csv, json, math, os, sys, urllib.parse
from datetime import timedelta, datetime

sys.path.insert(0, os.path.dirname(__file__))
from fetch_data import get, records, parse_metar, UTC, PKT  # noqa: E402

# 41 districts, HQ coordinates, division, airport whose METAR represents the district (if inside it)
DISTRICTS = [
    ("Lahore", "Lahore", 31.5497, 74.3436, "OPLA"), ("Kasur", "Lahore", 31.1187, 74.4508, None),
    ("Sheikhupura", "Lahore", 31.7131, 73.9783, None), ("Nankana Sahib", "Lahore", 31.4492, 73.7124, None),
    ("Okara", "Sahiwal", 30.8138, 73.4534, None), ("Sahiwal", "Sahiwal", 30.6682, 73.1114, None),
    ("Pakpattan", "Sahiwal", 30.3436, 73.3897, None),
    ("Gujranwala", "Gujranwala", 32.1617, 74.1883, None), ("Wazirabad", "Gujranwala", 32.4430, 74.1200, None),
    ("Sialkot", "Gujranwala", 32.4945, 74.5229, "OPST"), ("Narowal", "Gujranwala", 32.1020, 74.8730, None),
    ("Hafizabad", "Gujranwala", 32.0712, 73.6880, None),
    ("Gujrat", "Gujrat", 32.5736, 74.0789, None), ("Mandi Bahauddin", "Gujrat", 32.5861, 73.4917, None),
    ("Rawalpindi", "Rawalpindi", 33.5973, 73.0479, None), ("Murree", "Rawalpindi", 33.9070, 73.3943, None),
    ("Attock", "Rawalpindi", 33.7660, 72.3609, "OPIS"), ("Chakwal", "Rawalpindi", 32.9328, 72.8630, None),
    ("Talagang", "Rawalpindi", 32.9282, 72.4158, None), ("Jhelum", "Rawalpindi", 32.9405, 73.7276, None),
    ("Sargodha", "Sargodha", 32.0836, 72.6711, None), ("Khushab", "Sargodha", 32.2967, 72.3525, None),
    ("Mianwali", "Sargodha", 32.5839, 71.5370, None), ("Bhakkar", "Sargodha", 31.6333, 71.0667, None),
    ("Faisalabad", "Faisalabad", 31.4180, 73.0790, "OPFA"), ("Jhang", "Faisalabad", 31.2681, 72.3181, None),
    ("Toba Tek Singh", "Faisalabad", 30.9709, 72.4826, None), ("Chiniot", "Faisalabad", 31.7200, 72.9789, None),
    ("Multan", "Multan", 30.1984, 71.4687, "OPMT"), ("Khanewal", "Multan", 30.3017, 71.9321, None),
    ("Vehari", "Multan", 30.0445, 72.3556, None), ("Lodhran", "Multan", 29.5339, 71.6324, None),
    ("Dera Ghazi Khan", "D.G. Khan", 30.0459, 70.6403, "OPDG"), ("Muzaffargarh", "D.G. Khan", 30.0703, 71.1933, None),
    ("Kot Addu", "D.G. Khan", 30.4700, 70.9669, None), ("Layyah", "D.G. Khan", 30.9693, 70.9428, None),
    ("Taunsa", "D.G. Khan", 30.7040, 70.6505, None), ("Rajanpur", "D.G. Khan", 29.1041, 70.3297, None),
    ("Bahawalpur", "Bahawalpur", 29.3956, 71.6836, "OPBW"), ("Bahawalnagar", "Bahawalpur", 29.9987, 73.2536, None),
    ("Rahim Yar Khan", "Bahawalpur", 28.4202, 70.2952, "OPRK"),
]
COMP = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(d):
    return COMP[int((d % 360) / 22.5 + 0.5) % 16]


def vec(spd, d):
    """'from' direction -> (u, v) of the air's motion; d None (calm/VRB) -> zero vector."""
    if d is None or spd <= 0:
        return 0.0, 0.0
    r = math.radians(d)
    return -spd * math.sin(r), -spd * math.cos(r)


def from_dir(u, v):
    if abs(u) < 1e-9 and abs(v) < 1e-9:
        return None
    return (math.degrees(math.atan2(-u, -v)) + 360) % 360


def mean_wind(samples):
    """samples: [(spd, dir_or_None)] -> (mean speed, resultant dir, resultant speed)."""
    if not samples:
        return None
    s = sum(x[0] for x in samples) / len(samples)
    us = [vec(*x) for x in samples]
    u = sum(a for a, _ in us) / len(us)
    v = sum(b for _, b in us) / len(us)
    return s, from_dir(u, v), math.hypot(u, v)


def main():
    now = datetime.now(UTC)
    end = now.replace(minute=0, second=0, microsecond=0)          # last full hour boundary
    starts = [end - timedelta(hours=k) for k in range(6, 0, -1)]   # 6 hour bins

    # METARs
    ids = ",".join(sorted({d[4] for d in DISTRICTS if d[4]}))
    metars = {}
    try:
        for r in records(get(f"https://aviationweather.gov/api/data/metar?ids={ids}&hours=9&format=raw")):
            p = parse_metar(r, now)
            if p:
                metars.setdefault(p["icao"], []).append(p)
    except Exception as e:
        print("METAR fetch failed:", e, file=sys.stderr)

    # Open-Meteo at each HQ
    q = urllib.parse.urlencode({
        "latitude": ",".join(str(d[2]) for d in DISTRICTS),
        "longitude": ",".join(str(d[3]) for d in DISTRICTS),
        "hourly": "wind_speed_10m,wind_direction_10m",
        "past_hours": 8, "forecast_hours": 1, "timezone": "UTC", "wind_speed_unit": "kmh",
    })
    js = json.loads(get("https://api.open-meteo.com/v1/forecast?" + q))
    if isinstance(js, dict):
        js = [js]
    model = [{t: (s, d) for t, s, d in zip(j["hourly"]["time"], j["hourly"]["wind_speed_10m"], j["hourly"]["wind_direction_10m"])} for j in js]

    hours_lbl = [f"{t.astimezone(PKT):%H:%M}–{(t + timedelta(hours=1)).astimezone(PKT):%H:%M}" for t in starts]
    out = []
    for i, (name, div, lat, lon, icao) in enumerate(DISTRICTS):
        hourly = []
        for t in starts:
            obs = [m for m in metars.get(icao, []) if t <= m["time"] < t + timedelta(hours=1)] if icao else []
            if obs:
                smp = [(m["wind"]["spd"], m["wind"].get("dir")) for m in obs]
                src = "METAR"
            else:
                smp = []
                for tt in (t, t + timedelta(hours=1)):
                    v = model[i].get(tt.strftime("%Y-%m-%dT%H:00"))
                    if v and v[0] is not None:
                        smp.append((v[0], v[1] if v[0] >= 0.5 else None))
                src = "MODEL"
            mw = mean_wind(smp)
            hourly.append(None if mw is None else {
                "spd": round(mw[0], 1), "dir": None if mw[1] is None else round(mw[1]),
                "cmp": "calm" if mw[1] is None or mw[0] < 1 else compass(mw[1]),
                "src": src, "n": len(smp)})
        valid = [h for h in hourly if h]
        s6 = sum(h["spd"] for h in valid) / len(valid)
        us = [vec(h["spd"], h["dir"]) for h in valid]
        u = sum(a for a, _ in us) / len(us); v = sum(b for _, b in us) / len(us)
        rd, rs = from_dir(u, v), math.hypot(u, v)
        srcs = sorted({h["src"] for h in valid})
        out.append({
            "district": name, "division": div, "lat": lat, "lon": lon, "airport": icao,
            "source": "+".join(srcs), "hourly": hourly,
            "mean_spd": round(s6, 2), "net_dir": None if rd is None else round(rd),
            "net_cmp": "variable" if rd is None else compass(rd),
            "resultant_spd": round(rs, 2), "steadiness": round(rs / s6, 2) if s6 > 0 else 0,
            "calm_hours": sum(1 for h in valid if h["spd"] < 2),
        })
    out.sort(key=lambda r: (r["mean_spd"], r["district"]))
    for k, r in enumerate(out, 1):
        r["rank"] = k

    rep = {"generated": now.isoformat(timespec="seconds"),
           "generatedPKT": now.astimezone(PKT).strftime("%d %b %Y, %H:%M PKT"),
           "window": f"{starts[0].astimezone(PKT):%d %b %Y %H:%M}–{end.astimezone(PKT):%H:%M} PKT",
           "hours": hours_lbl, "districts": out,
           "metar_counts": {k: len(v) for k, v in metars.items()}}
    base = os.path.join(os.path.dirname(__file__), "..", "data")
    with open(os.path.join(base, "district_report.json"), "w") as f:
        json.dump(rep, f, indent=1)
    with open(os.path.join(base, "district_report.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "district", "division", "source"]
                   + [f"{h} speed km/h" for h in hours_lbl] + [f"{h} dir" for h in hours_lbl]
                   + ["6h mean speed km/h", "6h net direction (deg)", "6h net direction", "resultant speed km/h", "steadiness", "calm hours (<2 km/h)"])
        for r in out:
            w.writerow([r["rank"], r["district"], r["division"], r["source"]]
                       + [h["spd"] if h else "" for h in r["hourly"]]
                       + [(f'{h["cmp"]} {h["dir"]}°' if h and h["dir"] is not None else (h["cmp"] if h else "")) for h in r["hourly"]]
                       + [r["mean_spd"], r["net_dir"] if r["net_dir"] is not None else "", r["net_cmp"], r["resultant_spd"], r["steadiness"], r["calm_hours"]])
    print("wrote district report", rep["window"], rep["metar_counts"])


if __name__ == "__main__":
    main()
