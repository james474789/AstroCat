"""Prototype of the R1 target recommender (research doc §4) run against the live DB.
Run inside the backend container: python /tmp/rec_proto.py [YYYY-MM-DD]
"""
import os, re, sys, math, statistics, collections, json
from datetime import datetime, timedelta, timezone
import numpy as np
import psycopg2
from astropy.time import Time
from astropy.coordinates import EarthLocation, AltAz, get_body, get_sun
import astropy.units as u

sys.path.insert(0, "/app")
from app.services.targets import normalize_designation
from app.utils.filter_names import normalize_filter

NIGHT = sys.argv[1] if len(sys.argv) > 1 else "2026-09-26"
STEP_MIN = 5
dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg", "postgresql")
db = psycopg2.connect(dsn)
cur = db.cursor()

def q(sql, *a):
    cur.execute(sql, a)
    return cur.fetchall()

# ---------------------------------------------------------------- site (learned)
rows = q("""select round((raw_header->>'SITELAT')::numeric,2), round((raw_header->>'SITELONG')::numeric,2), count(*)
            from images where jsonb_typeof(raw_header)='object'
              and (raw_header->>'SITELAT') ~ '^-?[0-9.]+$' and (raw_header->>'SITELONG') ~ '^-?[0-9.]+$'
            group by 1,2 order by 3 desc limit 1""")
LAT, LON = float(rows[0][0]), float(rows[0][1])
loc = EarthLocation(lat=LAT * u.deg, lon=LON * u.deg, height=100 * u.m)

# ---------------------------------------------------------------- learned horizon
alt_az = q("""select (raw_header->>'CENTAZ')::float, (raw_header->>'CENTALT')::float from images
              where jsonb_typeof(raw_header)='object' and frame_type='LIGHT'
                and (raw_header->>'CENTAZ') ~ '^-?[0-9.]+$' and (raw_header->>'CENTALT') ~ '^-?[0-9.]+$'""")
all_alt = [a for _, a in q("""select 0,(raw_header->>'CENTALT')::float from images where jsonb_typeof(raw_header)='object'
              and frame_type='LIGHT' and (raw_header->>'CENTALT') ~ '^-?[0-9.]+$'""")]
FLOOR = float(np.percentile(all_alt, 5))
BINS = 12
horizon = np.full(BINS, FLOOR)
by_bin = collections.defaultdict(list)
for az, alt in alt_az:
    by_bin[int((az % 360) // (360 / BINS))].append(alt)
for b, alts in by_bin.items():
    if len(alts) >= 50:
        horizon[b] = float(np.percentile(alts, 5))
horizon = np.clip(horizon, 15, FLOOR + 10)

# ---------------------------------------------------------------- rigs (active = used in last 2y, >=50 subs)
RIGS = [  # derived from the rig query; filter sets from filter_name
    dict(name="ASI1600MM + ~345mm", scale=2.27, w=4656, h=3520, filters={"L", "R", "G", "B", "Ha", "OIII"}),
    dict(name="ASI294MM + C11", scale=0.34, w=4144, h=2822, filters={"L", "R", "G", "B", "Ha", "OIII", "SII"}),
    dict(name="ASI294MM + 200mm", scale=2.46, w=8288, h=5644, filters={"L", "R", "G", "B", "Ha", "OIII"}),
    dict(name="EOS R7 (OSC) + ~100mm", scale=6.5, w=6984, h=4660, filters={"OSC"}),
]
for r in RIGS:
    r["fov_w"] = r["w"] * r["scale"] / 60  # arcmin
    r["fov_h"] = r["h"] * r["scale"] / 60

# ---------------------------------------------------------------- candidates
cands = {}  # canonical key -> dict

def add(key, name, ra, dec, size, kind, cat, mag, aliases):
    key = normalize_designation(key)
    if key in cands:
        cands[key]["aliases"] |= {normalize_designation(a) for a in aliases if a}
        return
    cands[key] = dict(key=key, name=name, ra=ra, dec=dec, size=size, kind=kind, cat=cat, mag=mag,
                      aliases={key} | {normalize_designation(a) for a in aliases if a})

REFLECTION = {"NGC1432", "NGC1435", "NGC7023", "C4", "M45", "M78", "NGC2068", "NGC1977", "IC2118", "NGC1333", "NGC6726", "IC4601", "NGC2245", "NGC2247", "IC348"}

def kind_of(t):
    t = (t or "").lower()
    if t == "pn" or "planetary" in t:
        return "PN"
    if "rfn" in t or "reflection" in t:
        return "RFN"
    if any(s in t for s in ("emission", "hii", "snr", "supernova", "emn")):
        return "NB"
    if t in ("neb", "cl+n", "nebula"):
        return "NEB?"   # ambiguous: NB only if a Sharpless HII region confirms it
    if t.startswith("g") or "galax" in t:
        return "GAL"
    if "rfn" in t or "reflection" in t:
        return "RFN"
    if "cl" in t or "cluster" in t:
        return "CL"
    return None

def size_of(s):
    if s is None: return None
    if isinstance(s, (int, float)): return float(s)
    m = re.findall(r"\d+(?:\.\d+)?", str(s))
    return float(m[0]) if m else None

for d, cn, ngc, ra, dec, t, mag, sz in q("select designation, common_name, ngc_designation, ra_degrees, dec_degrees, object_type, apparent_magnitude, angular_size_arcmin from messier_catalog"):
    add(d, cn or d, ra, dec, size_of(sz), kind_of(t), "M", mag, [ngc])
alias_to_key = {a: k for k, c in cands.items() for a in c["aliases"]}

for d, cn, src, ra, dec, t, mag, ma in q("select designation, common_name, source_designation, ra_degrees, dec_degrees, object_type, apparent_magnitude, major_axis_arcmin from caldwell_catalog"):
    k = alias_to_key.get(normalize_designation(src))
    if k: cands[k]["aliases"].add(normalize_designation(d)); continue
    add(d, cn or d, ra, dec, ma, kind_of(t), "C", mag, [src])
alias_to_key = {a: k for k, c in cands.items() for a in c["aliases"]}

for d, cn, m, ic, ra, dec, t, mag, ma in q("select designation, common_name, messier_designation, ic_designation, ra_degrees, dec_degrees, object_type, apparent_magnitude, major_axis_arcmin from ngc_catalog"):
    nk = normalize_designation(d)
    k = alias_to_key.get(nk) or alias_to_key.get(normalize_designation(m))
    if k:
        cands[k]["aliases"] |= {nk, normalize_designation(ic)}
        if cands[k]["size"] is None: cands[k]["size"] = ma
        continue
    kd = kind_of(t)
    if kd is None or t == "Dup": continue
    # imageability gate for the big general catalog
    if not ((mag is not None and mag <= 11.5) or (ma or 0) >= 8 or (kd == "NB" and (ma or 0) >= 3)):
        continue
    add(d, cn or d, ra, dec, ma, kd, "NGC/IC", mag, [ic])

# Sh2: merge into an existing nebula if centred within 0.25 deg, else add
neb = [(k, c) for k, c in cands.items() if c["kind"] in ("NB", "RFN", "NEB?")]
for d, cn, ra, dec, ma in q("select designation, common_name, ra_degrees, dec_degrees, major_axis_arcmin from sh2_catalog"):
    sk = normalize_designation(d)
    hit = None
    for k, c in neb:
        dd = math.degrees(math.acos(min(1, math.sin(math.radians(dec)) * math.sin(math.radians(c["dec"])) +
             math.cos(math.radians(dec)) * math.cos(math.radians(c["dec"])) * math.cos(math.radians(ra - c["ra"])))))
        if dd < 0.25: hit = k; break
    if hit:
        cands[hit]["aliases"].add(sk)
        cands[hit]["size"] = max(cands[hit]["size"] or 0, ma or 0) or None
        if cands[hit]["kind"] == "NEB?": cands[hit]["kind"] = "NB"
        continue
    if (ma or 0) >= 10:
        add(d, cn or d, ra, dec, ma, "NB", "Sh2", None, [])

for c in cands.values():
    if c["aliases"] & REFLECTION: c["kind"] = "RFN"
    elif c["kind"] == "NEB?": c["kind"] = "RFN"   # unconfirmed nebula: treat as broadband (conservative)
C = list(cands.values())
alias_to_key = {a: c["key"] for c in C for a in c["aliases"]}

# ---------------------------------------------------------------- history per candidate
hist = collections.defaultdict(lambda: dict(sec=collections.Counter(), nights=set(), scales=[], first=None, last=None))
for tk, fn, sec, cd, sc in q("""select target_key, filter_name, exposure_time_seconds, capture_date, pixel_scale_arcsec from images
        where frame_type='LIGHT' and subtype='SUB_FRAME' and target_key is not null and exposure_time_seconds > 0 and capture_date is not null"""):
    k = alias_to_key.get(tk)
    if not k: continue
    h = hist[k]
    h["sec"][normalize_filter(fn)] += sec
    n = (cd - timedelta(hours=12)).date()
    h["nights"].add(n)
    if sc and 0.05 < sc < 300 and abs(sc - 72) > 1e-3: h["scales"].append(sc)
    h["first"] = min(h["first"] or cd, cd); h["last"] = max(h["last"] or cd, cd)
masters = {alias_to_key.get(tk) for (tk,) in q("select distinct target_key from images where subtype='INTEGRATION_MASTER' and target_key is not null")}

# affinity: share of hours by kind; inferred goal = median hours of targets with a master, per kind
kind_hours = collections.Counter(); goal_samples = collections.defaultdict(list)
for k, h in hist.items():
    hrs = sum(h["sec"].values()) / 3600
    kind_hours[cands[k]["kind"]] += hrs
    if k in masters and len(h["nights"]) >= 2: goal_samples[cands[k]["kind"]].append(hrs)
tot = sum(kind_hours.values()) or 1
affinity = {kd: v / tot for kd, v in kind_hours.items()}
all_goal = [x for v in goal_samples.values() for x in v]
goal = {kd: statistics.median(goal_samples[kd]) if len(goal_samples[kd]) >= 3 else statistics.median(all_goal) for kd in kind_hours}

# ---------------------------------------------------------------- ephemeris helpers
RA = np.radians([c["ra"] for c in C]); DEC = np.radians([c["dec"] for c in C]); PHI = math.radians(LAT)

def night_grid(date_str):
    t0 = Time(f"{date_str} 16:00") ; t1 = t0 + 16 * u.hour   # UTC, covers dusk..dawn at this longitude
    ts = t0 + np.arange(0, 16 * 60, STEP_MIN) * u.min
    fr = AltAz(obstime=ts, location=loc)
    sun = get_sun(ts).transform_to(fr).alt.deg
    moon_c = get_body("moon", ts, loc)
    moon = moon_c.transform_to(fr)
    sun_c = get_sun(ts)
    elong = sun_c.separation(moon_c).deg
    illum = (1 - np.cos(np.radians(elong))) / 2
    lst = np.radians(ts.sidereal_time("apparent", longitude=LON * u.deg).deg)
    return ts, sun, moon.alt.deg, np.radians(moon_c.ra.deg), np.radians(moon_c.dec.deg), illum, lst

def altaz(lst):
    H = lst[None, :] - RA[:, None]
    sa = np.sin(DEC)[:, None] * math.sin(PHI) + np.cos(DEC)[:, None] * math.cos(PHI) * np.cos(H)
    alt = np.degrees(np.arcsin(np.clip(sa, -1, 1)))
    az = np.degrees(np.arctan2(-np.cos(DEC)[:, None] * np.sin(H),
                               np.sin(DEC)[:, None] * math.cos(PHI) - np.cos(DEC)[:, None] * math.sin(PHI) * np.cos(H))) % 360
    return alt, az

def lorentz(D, W, age):  # age in days since new moon
    return D / (1 + ((0.5 - age / 29.53) / (W / 29.53)) ** 2)

MOON_RULES = {"BB": (120, 14), "Ha": (40, 10), "SII": (40, 10), "OIII": (70, 10)}

def usable(date_str):
    ts, sun, malt, mra, mdec, illum, lst = night_grid(date_str)
    alt, az = altaz(lst)
    dark = sun < -18
    hz = horizon[(az // (360 / BINS)).astype(int)]
    up = (alt > hz) & dark[None, :]
    sep = np.degrees(np.arccos(np.clip(np.sin(DEC)[:, None] * np.sin(mdec)[None, :] +
          np.cos(DEC)[:, None] * np.cos(mdec)[None, :] * np.cos(RA[:, None] - mra[None, :]), -1, 1)))
    k = float(np.mean(illum[dark])) if dark.any() else float(illum.mean())
    age = math.degrees(math.acos(1 - 2 * k)) / 360 * 29.53  # days from new (waxing/waning symmetric)
    moon_up = (malt > 0)[None, :]
    ok = {f: up & (~moon_up | (sep >= lorentz(D, W, age))) for f, (D, W) in MOON_RULES.items()}
    w = np.sin(np.radians(np.clip(alt, 0, 90)))  # 1/airmass weighting
    return dict(ts=ts, sun=sun, dark=dark, alt=alt, up=up, ok=ok, w=w, sep=sep, illum=k, age=age, malt=malt)

tonight = usable(NIGHT)
hrs = lambda m: m.sum(axis=1) * STEP_MIN / 60
up_h = hrs(tonight["up"]); wq = (tonight["up"] * tonight["w"]).sum(axis=1) * STEP_MIN / 60
okh = {f: hrs(m) for f, m in tonight["ok"].items()}
okh["NB"] = np.maximum(okh["Ha"], okh["SII"])

# season: usable hours on the same night each week for the next 12 weeks
future = []
d0 = datetime.strptime(NIGHT, "%Y-%m-%d")
for wk in range(1, 13):
    future.append(hrs(usable((d0 + timedelta(days=7 * wk)).strftime("%Y-%m-%d"))["up"]))
future = np.array(future)  # (12, N)

# ---------------------------------------------------------------- score
def framing(size, rig):
    if not size: return 0.4, None
    short = min(rig["fov_w"], rig["fov_h"]); r = size / short
    px = size * 60 / rig["scale"]
    if px < 40: return 0.0, r
    if r > 3: return 0.0, r
    return math.exp(-(math.log(r / 0.5)) ** 2 / (2 * 0.7 ** 2)), r

out = []
per_rig = collections.defaultdict(list)
for i, c in enumerate(C):
    if up_h[i] < 1.0: continue
    kind = c["kind"]
    if kind is None: continue
    best = None
    for rig in RIGS:
        fit, ratio = framing(c["size"], rig)
        if fit <= 0: continue
        nb_ok = kind in ("NB",) and ("Ha" in rig["filters"])
        if kind == "PN" and "OIII" in rig["filters"]: nb_ok = True
        mode = "NB" if nb_ok else "BB"
        avail = okh["NB"][i] if mode == "NB" else okh["BB"][i]
        if kind == "PN" and mode == "NB": avail = okh["OIII"][i]
        if avail < 1.0: continue
        cand = (fit * min(avail, 6) / 6, rig, fit, ratio, mode, avail)
        per_rig[rig["name"]].append((fit * min(avail, 6) / 6, c["key"], c["name"], mode, round(avail, 1), round(ratio or 0, 2)))
        if not best or cand[0] > best[0]: best = cand
    if not best: continue
    _, rig, fit, ratio, mode, avail = best
    h = hist.get(c["key"])
    have = sum(h["sec"].values()) / 3600 if h else 0.0
    if have < 0.25: h, have = None, 0.0
    nights = len(h["nights"]) if h else 0
    days_since = (datetime.strptime(NIGHT, "%Y-%m-%d") - h["last"]).days if h else None
    momentum = 1.0 if days_since is not None and days_since <= 45 else 0.0
    nb_share = (sum(v for f, v in h["sec"].items() if f in ("Ha", "OIII", "SII")) / sum(h["sec"].values())) if h else 0
    g = goal.get(kind, statistics.median(all_goal))
    delta = 0.7 * avail
    committed = nights >= 2 or have >= 2 or momentum
    if h and committed:
        marginal = math.sqrt((have + delta) / have) - 1
        project = min(1, marginal / 0.5) * (1.0 if have < g else 0.5)
    else:
        project = 0
    # seasonal urgency: tonight vs. the next 12 weeks
    fut = future[:, i]; weeks_left = int(np.argmax(fut < 1.0)) if (fut < 1.0).any() else 12
    urgency = 1 - weeks_left / 12 if up_h[i] >= fut.max() * 0.8 else 0.0
    prior = {"M": 1.0, "C": 0.9, "Sh2": 0.6, "NGC/IC": 0.55}[c["cat"]]
    aff = affinity.get(kind, 0) / max(affinity.values())
    obs = min(wq[i] / 5, 1)
    revisit = 0
    if h and days_since > 3 * 365 and have < g and mode == "NB" and nb_share < 0.3:
        revisit = 1   # old, short, broadband/OSC-only data on a target you can now shoot in narrowband
    score = 0.25 * obs + 0.2 * fit + 0.2 * project + 0.15 * urgency + 0.1 * aff + 0.1 * prior + 0.1 * revisit + 0.15 * momentum
    reasons = []
    reasons.append(f"{avail:.1f}h clear of Moon ({mode})")
    if ratio: reasons.append(f"fills {ratio*100:.0f}% of {rig['name']} short side")
    if have: reasons.append(f"have {have:.1f}h/{len(h['nights'])} nights, last {h['last']:%Y-%m}; goal≈{g:.0f}h")
    if h and h["sec"]:
        reasons.append("filters " + ", ".join(f"{f} {s/3600:.1f}h" for f, s in h["sec"].most_common(4)))
    if momentum: reasons.append(f"active: last imaged {days_since} days ago")
    if urgency > 0.3: reasons.append(f"window closes in ~{weeks_left} wk")
    if revisit: reasons.append(f"revisit: only {nb_share*100:.0f}% narrowband so far")
    lane = ("Active project" if momentum else
            "Continue a project" if project > 0.15 and have < g else
            "Revisit with better gear" if revisit else
            "Last chance this season" if urgency >= 0.5 else
            "New for you" if have == 0 else "Top up")
    # transit time
    j = int(np.argmax(np.where(tonight["up"][i], tonight["alt"][i], -99)))
    out.append(dict(key=c["key"], name=c["name"], kind=kind, score=round(score, 3), lane=lane, rig=rig["name"], mode=mode,
                    best_utc=tonight["ts"][j].datetime.strftime("%H:%M"), max_alt=round(float(tonight["alt"][i][j])),
                    moon_sep=round(float(tonight["sep"][i][j])), reasons=reasons, have=round(have, 1)))

out.sort(key=lambda r: -r["score"])
dark_idx = np.where(tonight["dark"])[0]
summary = dict(site=[LAT, LON], floor=round(FLOOR, 1), horizon=[round(x) for x in horizon],
               dark_utc=[tonight["ts"][dark_idx[0]].datetime.strftime("%H:%M"), tonight["ts"][dark_idx[-1]].datetime.strftime("%H:%M")] if len(dark_idx) else None,
               moon_illum=round(tonight["illum"], 3), moon_up_dark_frac=round(float((tonight["malt"][tonight["dark"]] > 0).mean()), 2),
               affinity={k: round(v, 2) for k, v in affinity.items()}, goals={k: round(v, 1) for k, v in goal.items()},
               n_candidates=len(C), n_feasible=len(out))
print(json.dumps(summary, default=str))
lanes = collections.defaultdict(list)
for r in out: lanes[r["lane"]].append(r)
for lane, rs in lanes.items():
    print(f"\n== {lane} ({len(rs)})")
    for r in rs[:6]:
        print(f"  {r['score']:.3f} {r['key']:9s} {r['name'][:28]:28s} {r['kind']:3s} {r['rig']:22s} best {r['best_utc']}Z alt {r['max_alt']} moon {r['moon_sep']}°")
        print("        " + " | ".join(r["reasons"]))

print()
print("== Per-rig: moon-clear hours and best fits")
for rig in RIGS:
    rs = sorted(per_rig[rig["name"]], reverse=True)[:5]
    print(f"  {rig['name']:24s} FOV {rig['fov_w']/60:.2f}x{rig['fov_h']/60:.2f} deg  feasible={len(per_rig[rig['name']])}  " + "; ".join(f"{k}({m},{a}h,fill {r})" for _, k, n, m, a, r in rs))
for key in ("C20", "NGC7000", "C4", "NGC1432", "IC1396", "M76"):
    hit = [r for r in out if r["key"] == key]
    print("  check", key, hit[0]["lane"] + " " + str(hit[0]["score"]) + " " + " | ".join(hit[0]["reasons"]) if hit else "not feasible", cands.get(key, {}).get("kind"), cands.get(key, {}).get("size"))
