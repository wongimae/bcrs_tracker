#!/usr/bin/env python3
"""
Rebuild the daily history of Return Right machines from the git history of
data/latest.json in a clone of cheeaun/returnright-data.

Usage: python3 compile_history.py <path-to-clone> <output-dir>
Writes <output-dir>/history.json (for the dashboard) and daily_summary.csv.
"""
import csv, json, subprocess, sys
from collections import Counter
from pathlib import Path

REPO, OUT = Path(sys.argv[1]), Path(sys.argv[2])
FILE = "data/latest.json"
CODE = {"RUNNING": "R", "FULL": "F", "ERROR": "E", "OFFLINE": "O",
        "MAINTENANCE": "M", "CLEANING": "C", "SUSPENDED": "S", "UNKNOWN": "U"}

def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True, check=True).stdout

def clean(v):
    return (v or "").strip()

# Every commit that touched the file, oldest first. %cs is the commit date in the
# committer's own timezone (+08:00 for this repo), so days are Singapore days.
by_day = {}
for line in filter(None, git("log", "--reverse", "--format=%H %cs", "--", FILE).split("\n")):
    sha, day = line.split()
    by_day[day] = sha  # last commit of the day wins

days, snapshots = [], []
for day, sha in sorted(by_day.items()):
    try:
        snapshots.append(json.loads(git("show", f"{sha}:{FILE}"))["data"])
        days.append(day)
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        print(f"skipping {day} ({sha[:7]}): unreadable", file=sys.stderr)

N = len(days)
machines, summary, statuses = {}, [], set()
for i, records in enumerate(snapshots):
    raw = Counter()
    for r in records:
        status = clean(r.get("status")).upper() or "UNKNOWN"
        raw[status] += 1
        m = machines.setdefault(r["id"], {"s": ["."] * N})
        m["s"][i] = CODE.get(status, "U")
        try:
            m["la"], m["lo"] = round(float(r["latitude"]), 5), round(float(r["longitude"]), 5)
        except (TypeError, ValueError, KeyError):
            m.setdefault("la", None); m.setdefault("lo", None)
        m["n"] = clean(r.get("locationName"))
        m["a"] = clean(r.get("address"))
        m["h"] = clean(r.get("rvmOpeningHours"))
    statuses.update(raw)
    summary.append({"date": days[i], "total": len(records), **raw})

OUT.mkdir(parents=True, exist_ok=True)
history = {"days": days,
           "m": [{"id": k, **{f: ("".join(v) if f == "s" else v) for f, v in m.items()}}
                 for k, m in sorted(machines.items())]}
(OUT / "history.json").write_text(json.dumps(history, separators=(",", ":"), ensure_ascii=False))

with open(OUT / "daily_summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "total", *sorted(statuses)], restval=0)
    w.writeheader(); w.writerows(summary)

print(f"{N} days ({days[0]} to {days[-1]}), {len(machines)} machines")
