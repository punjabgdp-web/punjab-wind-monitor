#!/usr/bin/env python3
"""
Build data/latest.json for the Punjab wind animation.

Window: 13 hourly slots in PKT (UTC+5), from 6 h before the current hour to 6 h after.
  * Airports, past slots    -> METAR (aviationweather.gov), closest report within the slot
  * Airports, future slots  -> TAF (aviationweather.gov), base + FM + BECMG groups
  * Any gap, and all cities / grid points -> Open-Meteo model (tagged MODEL)

Standard library only, so it runs on a bare GitHub Actions runner.
"""
import json, math, re, sys, time, urllib.request, urllib.parse, os
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
PKT = timezone(timedelta(hours=5))
KT, MPS = 1.852, 3.6
UA = {"User-Agent": "punjab-wind-monitor/1.0 (github pages; contact via repo issues)"}

AIRPORTS = [  # id, display name, short label, lat, lon
    ("OPIS", "Islamabad Int'l (OPIS)",           "Islamabad",      33.5490, 72.8257),
    ("OPST", "Sialkot Int'l (OPST)",             "Sialkot",        32.5356, 74.3639),
    ("OPLA", "Lahore Allama Iqbal Int'l (OPLA)", "Lahore AIIAP",   31.5216, 74.4036),
    ("VIAR", "Amritsar (VIAR, India)",           "Amritsar",       31.7078, 74.7992),
    ("OPFA", "Faisalabad Int'l (OPFA)",          "Faisalabad",     31.3650, 72.9948),
    ("OPMT", "Multan Int'l (OPMT)",              "Multan",         30.2032, 71.4191),
    ("OPDG", "Dera Ghazi Khan (OPDG)",           "D.G. Khan",      29.9610, 70.4859),
    ("OPBW", "Bahawalpur (OPBW)",                "Bahawalpur",     29.3481, 71.7180),
    ("OPRK", "Rahim Yar Khan, Shaikh Zayed (OPRK)", "Rahim Yar Khan", 28.3839, 70.2796),
]
CITIES = [  # model-only reference points
    ("LHR", "Lahore city",  31.55, 74.30), ("SKP", "Sheikhupura", 31.713, 73.978),
    ("GRW", "Gujranwala",   32.15, 74.19), ("KSR", "Kasur",       31.117, 74.433),
    ("NRW", "Narowal",      32.10, 74.867),("SGD", "Sargodha",    32.08, 72.67),
    ("SWL", "Sahiwal",      30.66, 73.11), ("JHG", "Jhang",       31.27, 72.32),
    ("BWN", "Bahawalnagar", 29.99, 73.25), ("MWI", "Mianwali",    32.58, 71.54),
    ("LYH", "Layyah",       30.96, 70.94), ("JLM", "Jhelum",      32.94, 73.73),
    ("OKR", "Okara",        30.81, 73.45), ("SDQ", "Sadiqabad",   28.31, 70.13),
]
GRID = [(round(la, 2), round(lo, 2))
        for la in [28.0 + 0.65 * i for i in range(10)]
        for lo in [69.6 + 0.72 * j for j in range(9)]]


# ---------------------------------------------------------------- HTTP
def get(url, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


# ---------------------------------------------------------------- parsing helpers
WIND_RE = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)\b")
VIS_RE = re.compile(r"\s(\d{4})(?:\s|NDV)")


def wind_from(text):
    """First wind group in text -> dict or None."""
    m = WIND_RE.search(text)
    if not m:
        return None
    d, s, g, u = m.groups()
    f = KT if u == "KT" else MPS
    spd = int(s) * f
    if d != "VRB" and int(d) == 0 and int(s) == 0:
        return {"calm": True, "spd": 0.0}
    w = {"spd": round(spd, 1)}
    if d == "VRB":
        w["vrb"] = True
    else:
        w["dir"] = int(d) % 360
    if g:
        w["gust"] = round(int(g) * f, 1)
    return w


def resolve_day_hour(day, hour, minute, ref):
    """DD HH MM (UTC) near reference datetime -> aware datetime (handles month roll, HH=24)."""
    best = None
    for dm in (-1, 0, 1):
        y, mo = ref.year, ref.month + dm
        if mo == 0:
            y, mo = y - 1, 12
        if mo == 13:
            y, mo = y + 1, 1
        try:
            base = datetime(y, mo, day, tzinfo=UTC)
        except ValueError:
            continue
        cand = base + timedelta(hours=hour, minutes=minute)
        if best is None or abs(cand - ref) < abs(best - ref):
            best = cand
    return best


def parse_metar(raw, now):
    m = re.search(r"\b([A-Z]{4})\s+(\d{2})(\d{2})(\d{2})Z\b", raw)
    if not m:
        return None
    icao, dd, hh, mi = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    t = resolve_day_hour(dd, hh, mi, now)
    rest = raw[m.end():]
    w = wind_from(rest.split(" RMK")[0].split(" BECMG")[0].split(" TEMPO")[0].split(" NOSIG")[0])
    if w is None:
        return None
    v = VIS_RE.search(" " + rest + " ")
    note = f"vis {int(v.group(1))} m" if v else None
    return {"icao": icao, "time": t, "wind": w, "note": note, "raw": raw.strip()}


def parse_taf(raw, now):
    """-> (icao, valid_from, valid_to, [(t_from, wind), ...]) using base, FM and BECMG groups."""
    txt = " ".join(raw.split())
    m = re.search(r"\b([A-Z]{4})\s+(\d{2})(\d{2})(\d{2})Z\s+(\d{2})(\d{2})/(\d{2})(\d{2})\b", txt)
    if not m:
        return None
    icao = m.group(1)
    issued = resolve_day_hour(int(m.group(2)), int(m.group(3)), int(m.group(4)), now)
    vf = resolve_day_hour(int(m.group(5)), int(m.group(6)), 0, issued)
    vt = resolve_day_hour(int(m.group(7)), int(m.group(8)), 0, issued + timedelta(hours=12))
    body = txt[m.end():]
    # split into change groups
    parts = re.split(r"\s(?=(?:FM\d{6}|BECMG|TEMPO|PROB\d{2}))", " " + body)
    changes = []
    current = None
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p.startswith("TEMPO") or p.startswith("PROB"):
            continue  # temporary/probabilistic groups are not the prevailing wind
        if p.startswith("FM"):
            mm = re.match(r"FM(\d{2})(\d{2})(\d{2})", p)
            t0 = resolve_day_hour(int(mm.group(1)), int(mm.group(2)), int(mm.group(3)), issued)
            w = wind_from(p[8:])
            if w:
                current = w
            changes.append((t0, current))
        elif p.startswith("BECMG"):
            mm = re.match(r"BECMG\s+(\d{2})(\d{2})/(\d{2})(\d{2})", p)
            if not mm:
                continue
            a = resolve_day_hour(int(mm.group(1)), int(mm.group(2)), 0, issued)
            b = resolve_day_hour(int(mm.group(3)), int(mm.group(4)), 0, issued)
            w = wind_from(p[mm.end():])
            if w:
                current = w
                changes.append((a + (b - a) / 2, current))
        else:  # base forecast
            w = wind_from(p)
            if w:
                current = w
                changes.append((vf, current))
    changes.sort(key=lambda c: c[0])
    return icao, vf, vt, changes


def taf_at(taf, t):
    if not taf:
        return None
    _, vf, vt, ch = taf
    if not (vf <= t < vt):
        return None
    w = None
    for t0, wd in ch:
        if t0 <= t:
            w = wd
    return w


def records(text):
    """Split raw API text into report records (handles wrapped TAF lines)."""
    out, cur = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        if re.match(r"^(METAR|SPECI|TAF)\b|^[A-Z]{4}\s+\d{6}Z", line.strip()) and cur:
            out.append(" ".join(cur)); cur = []
        cur.append(line.strip())
    if cur:
        out.append(" ".join(cur))
    return out


# ---------------------------------------------------------------- main
def main():
    now = datetime.now(UTC)
    h0 = now.replace(minute=0, second=0, microsecond=0)
    slots = [h0 + timedelta(hours=k) for k in range(-6, 7)]
    ids = ",".join(a[0] for a in AIRPORTS)
    log = {"metar": 0, "taf": 0, "errors": []}

    # METAR (last 8 h) and TAF
    metars = {}
    try:
        txt = get(f"https://aviationweather.gov/api/data/metar?ids={ids}&hours=8&format=raw")
        for r in records(txt):
            p = parse_metar(r, now)
            if p:
                metars.setdefault(p["icao"], []).append(p); log["metar"] += 1
    except Exception as e:
        log["errors"].append(str(e))
    tafs = {}
    try:
        txt = get(f"https://aviationweather.gov/api/data/taf?ids={ids}&format=raw")
        for r in records(txt):
            p = parse_taf(r, now)
            if p and p[3]:
                if p[0] not in tafs or p[1] > tafs[p[0]][1]:
                    tafs[p[0]] = p
                log["taf"] += 1
    except Exception as e:
        log["errors"].append(str(e))

    # Open-Meteo model for every point (airports, cities, grid)
    pts = [(a[3], a[4]) for a in AIRPORTS] + [(c[2], c[3]) for c in CITIES] + GRID
    model = [None] * len(pts)
    try:
        step = 60
        for i in range(0, len(pts), step):
            chunk = pts[i:i + step]
            q = urllib.parse.urlencode({
                "latitude": ",".join(str(p[0]) for p in chunk),
                "longitude": ",".join(str(p[1]) for p in chunk),
                "hourly": "wind_speed_10m,wind_direction_10m",
                "past_hours": 7, "forecast_hours": 8, "timezone": "UTC", "wind_speed_unit": "kmh",
            })
            js = json.loads(get("https://api.open-meteo.com/v1/forecast?" + q))
            if isinstance(js, dict):
                js = [js]
            for k, j in enumerate(js):
                h = j["hourly"]
                model[i + k] = {t: (s, d) for t, s, d in zip(h["time"], h["wind_speed_10m"], h["wind_direction_10m"])}
    except Exception as e:
        log["errors"].append("open-meteo: " + str(e))

    def mval(idx, t, note=None):
        mm = model[idx]
        if not mm:
            return None
        v = mm.get(t.strftime("%Y-%m-%dT%H:00"))
        if not v or v[0] is None or v[1] is None:
            return None
        s, d = v
        o = {"spd": round(s, 1), "src": "MODEL"}
        if s < 0.5:
            o["calm"] = True; o["spd"] = 0.0
        else:
            o["dir"] = round(d)
        if note:
            o["note"] = note
        return o

    sites = []
    for ai, (icao, name, short, lat, lon) in enumerate(AIRPORTS):
        series = []
        obs = metars.get(icao, [])
        for si, t in enumerate(slots):
            o = None
            if t <= now:
                near = [m for m in obs if -timedelta(minutes=35) <= m["time"] - t <= timedelta(minutes=25)]
                if near:
                    m = min(near, key=lambda m: abs(m["time"] - t))
                    o = dict(m["wind"], src="METAR")
                    lbl = m["time"].astimezone(PKT).strftime("%H:%M")
                    o["note"] = (f"{lbl} report" + (f" · {m['note']}" if m["note"] else ""))
                if o is None:
                    w = taf_at(tafs.get(icao), t)
                    if w and si >= 5:
                        o = dict(w, src="TAF", note="no METAR yet — TAF shown")
                if o is None:
                    o = mval(ai, t, "no METAR — model value")
            else:
                w = taf_at(tafs.get(icao), t)
                o = dict(w, src="TAF") if w else mval(ai, t, "no TAF — model value")
            series.append(o)
        last = max(obs, key=lambda m: m["time"]) if obs else None
        sites.append({"id": icao, "major": 1, "name": name, "short": short, "lat": lat, "lon": lon,
                      "hasMetar": bool(obs), "hasTaf": icao in tafs,
                      "lastMetar": last["raw"] if last else None,
                      "lastMetarTime": last["time"].isoformat() if last else None,
                      "taf": None, "s": series})
    for ci, (cid, name, lat, lon) in enumerate(CITIES):
        idx = len(AIRPORTS) + ci
        sites.append({"id": cid, "major": 0, "name": name + " — model", "short": name, "lat": lat, "lon": lon,
                      "s": [mval(idx, t) for t in slots]})
    grid = []
    for gi, (lat, lon) in enumerate(GRID):
        idx = len(AIRPORTS) + len(CITIES) + gi
        grid.append({"lat": lat, "lon": lon, "s": [mval(idx, t) for t in slots]})

    lp = h0.astimezone(PKT)
    out = {
        "generated": now.isoformat(timespec="seconds"),
        "generatedPKT": now.astimezone(PKT).strftime("%d %b %Y, %H:%M PKT"),
        "times": [t.astimezone(PKT).strftime("%H:%M") for t in slots],
        "dateLabel": lp.strftime("%a %d %b %Y") + " · PKT",
        "nowIdx": 6,
        "sites": sites,
        "grid": grid,
        "stats": log,
    }
    os.makedirs(os.path.join(os.path.dirname(__file__), "..", "data"), exist_ok=True)
    dst = os.path.join(os.path.dirname(__file__), "..", "data", "latest.json")
    if not (log["metar"] or any(model)):
        print("No data fetched; keeping previous file.", log, file=sys.stderr)
        sys.exit(1)
    with open(dst, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {dst}: {log['metar']} METARs, {log['taf']} TAFs, errors={log['errors']}")


if __name__ == "__main__":
    main()
