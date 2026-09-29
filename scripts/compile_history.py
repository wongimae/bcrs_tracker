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
