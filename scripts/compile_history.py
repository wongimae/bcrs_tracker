#!/usr/bin/env python3
"""
Build the dashboard's data from the local archive (see archive.py).

Usage: python3 compile_history.py <output-dir>
Writes <output-dir>/history.json and <output-dir>/daily_summary.csv.
"""
import csv, json, sys
from collections import Counter
from pathlib import Path
from archive import Archive

OUT = Path(sys.argv[1])
# Dashboard status letters; anything unrecognised shows as offline/unknown.
DASH = {"RUNNING": "R", "FULL": "F", "ERROR": "E", "OFFLINE": "O",
        "MAINTENANCE": "M", "CLEANING": "C", "SUSPENDED": "S", "UNKNOWN": "U"}

a = Archive()
to_dash = {code: DASH.get((raw or "UNKNOWN").upper(), "U") for code, raw in a.status_of.items()}
to_dash["."] = "."

machines = []
for i, mid in enumerate(a.ids):
    f = a.meta[mid][-1][1]  # latest known details
    try:
        la, lo = round(float(f.get("latitude")), 5), round(float(f.get("longitude")), 5)
    except (TypeError, ValueError):
        la = lo = None
    machines.append({
        "id": mid,
        "s": "".join(to_dash[chars[i]] if i < len(chars) else "." for _, _, chars in a.days),
        "n": (f.get("locationName") or "").strip(), "a": (f.get("address") or "").strip(),
        "la": la, "lo": lo, "h": (f.get("rvmOpeningHours") or "").strip(),
    })

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "history.json").write_text(json.dumps(
    {"days": [d for d, _, _ in a.days], "m": sorted(machines, key=lambda m: m["id"])},
    separators=(",", ":"), ensure_ascii=False))

statuses, rows = set(), []
for d, src, chars in a.days:
    c = Counter(a.status_of[ch] or "UNKNOWN" for ch in chars if ch != ".")
    statuses.update(c)
    rows.append({"date": d, "source": "upstream" if src == "u" else "returnright.sg",
                 "total": sum(c.values()), **c})
with open(OUT / "daily_summary.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["date", "source", "total", *sorted(statuses)], restval=0)
    w.writeheader(); w.writerows(rows)

print(f"{len(a.days)} days ({a.days[0][0]} to {a.days[-1][0]}), {len(machines)} machines")


# ---------- bin readings (scripts/bins.py) ----------
BINS = Path(__file__).resolve().parent.parent / "archive" / "bins"
state_file = BINS / "state.json"
if state_file.exists():
    state = json.loads(state_file.read_text())
    hourly = {}                      # id -> day -> [24]
    net = {}                         # day -> supplier -> n
    for f in sorted(BINS.glob("returns-*.txt")):
        for line in f.read_text().splitlines():
            parts = line.split()
            if not parts:
                continue
            day, hr = parts[0][:10], int(parts[0][11:13])
            for tok in parts[1:]:
                mid, n = (int(x, 36) for x in tok.split(":"))
                hourly.setdefault(mid, {}).setdefault(day, [0] * 24)[hr] += n
                sup = state["m"].get(str(mid), {}).get("s", "UNKNOWN")
                net.setdefault(day, {}).setdefault(sup, 0)
                net[day][sup] += n
    (OUT / "m").mkdir(exist_ok=True)
    for sid, rec in state["m"].items():
        mid, comps = int(sid), rec["c"]
        h = hourly.get(mid, {})
        counters = [c for c in comps if c["cap"] == 0]
        fills = [round(c["n"] / c["cap"] * 100) if c["cap"] else c["fill"] for c in comps]
        (OUT / "m" / f"{mid}.json").write_text(json.dumps({
            "s": rec["s"],
            "life": sum(c["n"] for c in counters) if counters else sum(sum(v) for v in h.values()),
            "counter": bool(counters),
            "fill": max(fills) if fills else None,
            "comps": comps,
            "h": h,
        }, separators=(",", ":")))
    days = sorted(net)
    sups = sorted({s for d in net.values() for s in d})
    hist = json.loads((OUT / "history.json").read_text())
    hist["bins"] = {"first": state.get("first"), "read": state.get("t"), "days": days,
                    "sup": {s: [net[d].get(s, 0) for d in days] for s in sups}}
    (OUT / "history.json").write_text(json.dumps(hist, separators=(",", ":"), ensure_ascii=False))
    print(f"bin readings: {len(state['m'])} machines, {len(days)} days of returns")
