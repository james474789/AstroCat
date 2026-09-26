"""Hybrid recommender prototype: Telescopius candidates (cached JSON) re-ranked with AstroCat history.
Run inside the backend container: python /tmp/rec_tp.py /tmp/tpc 2026-09-26
"""
import os, sys, json, glob, math, statistics, collections
from datetime import datetime, timedelta
import psycopg2

sys.path.insert(0, "/app")
from app.services.targets import normalize_designation
from app.utils.filter_names import normalize_filter

CACHE, NIGHT = sys.argv[1], sys.argv[2]
night_dt = datetime.strptime(NIGHT, "%Y-%m-%d")
db = psycopg2.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg", "postgresql"))
cur = db.cursor()

# Rigs, named via the Telescopius equipment import + solved-scale matching
RIGS = [
    dict(name="ZS73 + ASI1600MM", scale=2.27, w=4656, h=3520, nb=True, bb=True),
    dict(name="C11 EdgeHD + ASI294MM", scale=0.34, w=4144, h=2822, nb=True, bb=True),
    dict(name="EF200 + ASI294MM", scale=2.46, w=8288, h=5644, nb=True, bb=True),
    dict(name="Sigma 105 + EOS R7", scale=6.5, w=6984, h=4660, nb=False, bb=True),
]
for r in RIGS:
    r["short"] = min(r["w"], r["h"]) * r["scale"] / 60  # arcmin

# From the local run (history-derived): hours share by kind, and median finished integration by kind
AFFINITY = {"NB": 0.41, "GAL": 0.37, "RFN": 0.11, "CL": 0.05, "PN": 0.04}
GOAL = {"NB": 9.8, "GAL": 5.0, "RFN": 4.1, "CL": 1.2, "PN": 4.0}

# ---------------------------------------------------------------- candidates
pool = {}
for f in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
    base = os.path.basename(f)[:-5]
    tag, page = (base.rsplit("_", 1) + ["1"])[:2]
    for n, x in enumerate(json.load(open(f)).get("page_results", [])):
        o = x["object"]; mid = o["main_id"]
        e = pool.setdefault(mid, dict(x=x, passes=set(), rank=9999))
        if tag in ("ha_s2", "o3", "lrgb"):
            e["passes"].add(tag)
            if tag == "ha_s2":   # the only list whose popularity order is comparable across objects
                e["rank"] = min(e["rank"], (int(page) - 1) * 120 + n)
        elif tag == "name":
            e["rank"] = 0

def moon_d_of(x):
    w = (x.get("tonight_visibility") or {}).get("windows") or [{}]
    return min((wi.get("moon_distance_deg") or 999) for wi in w)

# Empirical Telescopius preset thresholds tonight = smallest Moon distance that passed each preset
THR = {t: min(moon_d_of(e["x"]) for e in pool.values() if t in e["passes"]) for t in ("ha_s2", "o3", "lrgb")}
print("moon thresholds (deg):", {k: round(v, 1) for k, v in THR.items()})

def sep_deg(a, b):
    ra1, d1, ra2, d2 = map(math.radians, (a["ra"] * 15, a["dec"], b["ra"] * 15, b["dec"]))
    return math.degrees(math.acos(min(1, math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))))

# Merge duplicate entries of one nebula (IC1396/Sh2-131, IC1805/Sh2-190): nebular types, overlapping
# centres, comparable sizes, or one typed as the embedded cluster.
NEBTYPES = {"eneb", "h2r", "snr", "rneb", "neb", "dineb", "opcl", "stcl", "cl+n"}
items = sorted(pool.values(), key=lambda e: e["rank"])
groups = []
for e in items:
    o = e["x"]["object"]
    if not (set(o.get("types") or []) & NEBTYPES) or not o.get("major_axis"):
        groups.append([e]); continue
    placed = False
    for g in groups:
        h = g[0]["x"]["object"]
        if not (set(h.get("types") or []) & NEBTYPES) or not h.get("major_axis"): continue
        big = max(o["major_axis"], h["major_axis"]) / 3600; small = min(o["major_axis"], h["major_axis"]) / 3600
        clusterish = (o.get("types") or [""])[0] in ("opcl", "stcl") or (h.get("types") or [""])[0] in ("opcl", "stcl")
        if sep_deg(o, h) < 0.25 * big and (small >= big / 3 or clusterish):
            g.append(e); placed = True; break
    if not placed: groups.append([e])
print("pool", len(pool), "-> groups", len(groups), "| merged e.g.:",
      [" + ".join(m["x"]["object"]["main_id"] for m in g) for g in groups if len(g) > 1][:8])

def kind_of(types, name):
    t = set(types or []); p = (types or [""])[0]
    if p == "rneb": return "RFN"
    if p in ("eneb", "h2r", "snr"): return "NB"
    if p == "pneb": return "PN"
    if t & {"eneb", "h2r", "snr"}: return "NB"
    if "pneb" in t: return "PN"
    if "rneb" in t: return "RFN"
    if any(s.endswith("gx") or s in ("gxy", "gxp", "igxs") for s in t): return "GAL"
    if "nebula" in (name or "").lower(): return "NB"   # e.g. Heart/Soul typed as clusters
    if t & {"opcl", "glcl", "stcl", "gcl"}: return "CL"
    return None

# ---------------------------------------------------------------- history keyed by target_key
hist = collections.defaultdict(lambda: dict(sec=collections.Counter(), nights=set(), last=None))
cur.execute("""select target_key, filter_name, exposure_time_seconds, capture_date from images
               where frame_type='LIGHT' and subtype='SUB_FRAME' and target_key is not null
                 and exposure_time_seconds > 0 and capture_date is not null""")
for tk, fn, sec, cd in cur.fetchall():
    h = hist[tk]; h["sec"][normalize_filter(fn)] += sec
    h["nights"].add((cd - timedelta(hours=12)).date()); h["last"] = max(h["last"] or cd, cd)

def merged_history(keys):
    sec = collections.Counter(); nights = set(); last = None
    for k in keys:
        if k in hist:
            h = hist[k]; sec.update(h["sec"]); nights |= h["nights"]
            last = max(last or h["last"], h["last"])
    return sec, nights, last

LAT, FLOOR = float(os.environ.get("SITE_LAT", "56.0")), 32.0  # site latitude; learned floor from CENTALT p5

def _h(t):
    return int(t[:2]) + int(t[3:5]) / 60 if t and len(t) >= 5 else None

def hours_above(dec, transit, win):
    """Hours above FLOOR inside the dark window. Telescopius' imaging_time_hours is just the dark window."""
    t, s, e = _h(transit), _h(win.get("start")), _h(win.get("end"))
    if None in (t, s, e) or dec is None: return 0.0
    if e < s: e += 24
    mid = (s + e) / 2
    while t < mid - 12: t += 24
    while t > mid + 12: t -= 24
    phi, d = math.radians(LAT), math.radians(dec)
    c = (math.sin(math.radians(FLOOR)) - math.sin(phi) * math.sin(d)) / (math.cos(phi) * math.cos(d))
    if c >= 1: return 0.0
    h0 = 12.0 if c <= -1 else math.degrees(math.acos(c)) / 15 * 0.9973  # sidereal -> solar hours
    return max(0.0, min(e, t + h0) - max(s, t - h0))

def framing(size_arcmin, rig):
    if not size_arcmin: return 0.4, None
    r = size_arcmin / rig["short"]
    if size_arcmin * 60 / rig["scale"] < 40 or r > 3: return 0.0, r
    return math.exp(-(math.log(r / 0.5)) ** 2 / (2 * 0.7 ** 2)), r

out = []
import re
_WELL_KNOWN = re.compile(r"^(M|NGC|IC|SH 2-|C) ?\d")

def display_rank(m):
    o = m["x"]["object"]; ks = {normalize_designation(i) for i in (o.get("ids") or []) + (o.get("alt_ids") or [])}
    own = sum(sum(hist[k]["sec"].values()) for k in ks if k in hist)
    return (-own, 0 if _WELL_KNOWN.match(o["main_id"]) else 1, m["rank"])

for g in groups:
    g.sort(key=display_rank)
    e = g[0]; x = e["x"]; o = x["object"]; mid = o["main_id"]
    kind = kind_of(o.get("types"), o.get("main_name"))
    if not kind: continue
    keys = {normalize_designation(i) for m in g for i in (m["x"]["object"].get("ids") or []) + (m["x"]["object"].get("alt_ids") or [])}
    size_arcsec = max((m["x"]["object"].get("major_axis") or 0) for m in g)
    alias_ids = [m["x"]["object"]["main_id"] for m in g[1:]]
    best_rank = min(m["rank"] for m in g)
    prior = 1.0 if best_rank == 0 else (max(0.0, 1 - best_rank / 360) if best_rank < 9999 else 0.1)
    if not _WELL_KNOWN.match(mid) and not o.get("main_name"): prior *= 0.5
    sec, nights, last = merged_history(keys)
    have = sum(sec.values()) / 3600
    if have < 0.25: sec, nights, last, have = collections.Counter(), set(), None, 0.0
    days_since = (night_dt - last).days if last else None
    momentum = 1.0 if days_since is not None and days_since <= 45 else 0.0

    # moon: which Telescopius presets it passed. Soft, not hard: a near-miss keeps 60% credit.
    w = (x.get("tonight_visibility") or {}).get("windows") or [{}]
    img_h = hours_above(o.get("dec"), (x.get("tonight_times") or {}).get("transit"), w[0])
    moon_d = min((wi.get("moon_distance_deg") or 999) for wi in w)
    need = {"NB": "ha_s2", "PN": "o3", "GAL": "lrgb", "RFN": "lrgb", "CL": "lrgb"}[kind]
    if kind == "PN" and moon_d < THR["o3"]: need = "ha_s2"   # PN still doable in Ha
    short = THR[need] - moon_d
    moon_ok = 1.0 if short <= 0 else max(0.0, 1 - short / 10)   # soft: 1 deg short keeps 90%
    if moon_ok <= 0.3: continue
    moon_note = (f"Moon {moon_d:.0f}° ok for {need}" if short <= 0 else
                 f"Moon {moon_d:.0f}°, {short:.1f}° inside the {need} limit (soft penalty)")
    if kind == "PN" and need == "ha_s2": moon_note += "; OIII not tonight"

    size = size_arcsec / 60 or None
    if not size and not have: continue   # unknown size: only keep your own projects
    best = None
    for rig in RIGS:
        if need in ("ha_s2", "o3") and not rig["nb"]: continue
        fit, ratio = framing(size, rig)
        if fit > 0 and (not best or fit > best[0]): best = (fit, rig, ratio)
    if not best or img_h < 1.0: continue
    fit, rig, ratio = best

    g = GOAL[kind]; committed = len(nights) >= 2 or have >= 2 or momentum
    project = 0.0
    if committed and have > 0:
        project = min(1, (math.sqrt((have + 0.7 * img_h) / have) - 1) / 0.5) * (1.0 if have < g else 0.5)
    nb_share = sum(v for f, v in sec.items() if f in ("Ha", "OIII", "SII")) / sum(sec.values()) if sec else 0
    revisit = 1.0 if have and days_since > 3 * 365 and have < g and need != "lrgb" and nb_share < 0.3 else 0.0
    # season: target transiting early in the evening is sliding west (proxy until yearly ephemeris is used)
    tr = (x.get("tonight_times") or {}).get("transit") or ""
    try: th = int(tr[:2]) + int(tr[3:5]) / 60
    except ValueError: th = None
    urgency = max(0.0, min(1.0, (21.5 - th) / 4)) if th is not None and 14 <= th <= 21.5 else 0.0
    max_alt = (x.get("tonight_visibility") or {}).get("max_altitude") or 0
    obs = min(img_h / 7, 1) * math.sin(math.radians(max(max_alt, 1)))
    aff = AFFINITY.get(kind, 0) / max(AFFINITY.values())
    if urgency >= 0.5 and have == 0 and prior < 0.5: urgency = 0.0   # don't push obscure objects as "last chance"
    score = (0.25 * obs + 0.2 * fit + 0.2 * project + 0.15 * urgency + 0.1 * aff
             + 0.1 * revisit + 0.15 * momentum + 0.2 * prior) * moon_ok
    lane = ("Active project" if momentum else "Continue a project" if project > 0.15 and have < g
            else "Revisit in narrowband" if revisit else "Last chance this season" if urgency >= 0.5
            else "New for you" if have == 0 else "Top up")
    reasons = [f"{img_h:.1f}h above {FLOOR:.0f}°, max alt {max_alt}°", moon_note]
    if alias_ids: reasons.append("same as " + ", ".join(alias_ids[:3]))
    if ratio: reasons.append(f"fills {ratio*100:.0f}% of {rig['name']}")
    if have: reasons.append(f"have {have:.1f}h/{len(nights)}n, last {last:%Y-%m}; goal≈{g:.0f}h; " +
                            ", ".join(f"{f} {s/3600:.1f}h" for f, s in sec.most_common(3)))
    if momentum: reasons.append(f"active: {days_since} days ago")
    if urgency >= 0.5: reasons.append(f"transits {tr}, leaving the evening sky")
    out.append(dict(mid=mid, name=o.get("main_name") or "", kind=kind, score=score, lane=lane, rig=rig["name"], reasons=reasons))

out.sort(key=lambda r: -r["score"])
print(f"pool={len(pool)} scored={len(out)}")
lanes = collections.defaultdict(list)
for r in out: lanes[r["lane"]].append(r)
for lane in ("Active project", "Continue a project", "Revisit in narrowband", "Last chance this season", "Top up", "New for you"):
    rs = lanes.get(lane, [])
    print(f"\n== {lane} ({len(rs)})")
    for r in rs[:7]:
        print(f"  {r['score']:.3f} {r['mid']:13s} {r['name'][:24]:24s} {r['kind']:3s} {r['rig']}")
        print("        " + " | ".join(r["reasons"]))
