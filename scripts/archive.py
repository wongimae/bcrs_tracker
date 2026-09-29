#!/usr/bin/env python3
"""
Compact, append-only archive of Return Right machine snapshots.

Layout (all plain text, so each daily commit is a small diff):
  archive/index.txt       machine ids, one per line, in order of first appearance
  archive/codes.json      one-character code -> raw status (null if missing)
  archive/status.txt      one line per day: "<date> <source> <one char per machine>"
                          char n belongs to line n of index.txt; "." = not listed
                          source: u = cheeaun/returnright-data, d = fetched directly
  archive/machines.jsonl  every field except status, written only when it changes:
                          {"d": first date seen with these values, "id": ..., "f": {...}}

Fields that change on every snapshot and carry no lasting meaning
(VOLATILE below) are not kept. Upstream stopped publishing them on 2026-09-16.

Commands:
  archive.py update [--source auto|upstream|direct]   add any missing days
  archive.py snapshot YYYY-MM-DD                      print that day's latest.json
"""
import argparse, datetime as dt, json, subprocess, sys, tempfile, time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "archive"
UPSTREAM = "https://github.com/cheeaun/returnright-data.git"
FILE = "data/latest.json"
SGT = ZoneInfo("Asia/Singapore")
VOLATILE = {"rvm_last_conn", "updatedAt", "distance"}
BASE_CODES = {"R": "RUNNING", "F": "FULL", "E": "ERROR", "O": "OFFLINE", "M": "MAINTENANCE",
              "C": "CLEANING", "S": "SUSPENDED", "U": "UNKNOWN", "_": None}
SPARE = "abcdefghijklmnopqrstuvwxyz0123456789GHIJKLNPQTVWXYZ"
MIN_SHARE = 0.5  # refuse a snapshot with fewer than half the previous day's machines


class Archive:
    def __init__(self, d=DIR):
        self.dir = d
        self.ids = [int(x) for x in self._read("index.txt").split()]
        self.pos = {m: i for i, m in enumerate(self.ids)}
        c = self._read("codes.json")
        self.status_of = json.loads(c) if c else dict(BASE_CODES)
        self.code_of = {v: k for k, v in self.status_of.items()}
        self.days = []  # [(date, source, chars)]
        for line in self._read("status.txt").splitlines():
            if line.strip():
                d, s, chars = line.split(" ", 2)
                self.days.append((d, s, chars))
        self.meta = {}  # id -> [(date, fields)]
        for line in self._read("machines.jsonl").splitlines():
            if line.strip():
                r = json.loads(line)
                self.meta.setdefault(r["id"], []).append((r["d"], r["f"]))
        self._new_meta, self._new_days = [], []

    def _read(self, name):
        p = self.dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    @property
    def last_date(self):
        return self.days[-1][0] if self.days else None

    def code_for(self, status):
        if status not in self.code_of:  # a status never seen before gets the next spare code
            code = next(c for c in SPARE if c not in self.status_of)
            self.status_of[code] = status; self.code_of[status] = code
        return self.code_of[status]

    def add_day(self, date, records, source):
        if self.last_date and date <= self.last_date:
            return False
        prev = sum(ch != "." for ch in self.days[-1][2]) if self.days else 0
        if not records or len(records) < prev * MIN_SHARE:
            raise ValueError(f"{date}: {len(records)} machines vs {prev} the day before; not archived")
        chars = {}
        for r in records:
            mid = r["id"]
            if mid not in self.pos:
                self.pos[mid] = len(self.ids); self.ids.append(mid)
            chars[self.pos[mid]] = self.code_for(r.get("status"))
            f = {k: v for k, v in r.items() if k not in VOLATILE and k not in ("status", "id")}
            hist = self.meta.setdefault(mid, [])
            if not hist or hist[-1][1] != f:
                hist.append((date, f)); self._new_meta.append({"d": date, "id": mid, "f": f})
        line = "".join(chars.get(i, ".") for i in range(len(self.ids)))
        self.days.append((date, source, line)); self._new_days.append(self.days[-1])
        return True

    def save(self):
        if not self._new_days:
            return
        self.dir.mkdir(exist_ok=True)
        (self.dir / "index.txt").write_text("".join(f"{m}\n" for m in self.ids))
        (self.dir / "codes.json").write_text(json.dumps(self.status_of, indent=1, ensure_ascii=False) + "\n")
        with open(self.dir / "status.txt", "a", encoding="utf-8") as f:
            for d, s, chars in self._new_days:
                f.write(f"{d} {s} {chars}\n")
        with open(self.dir / "machines.jsonl", "a", encoding="utf-8") as f:
            for r in self._new_meta:
                f.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n")
        self._new_meta, self._new_days = [], []

    def snapshot(self, date):
        """Rebuild that day's records (volatile fields excepted)."""
        row = next((r for r in self.days if r[0] == date), None)
        if not row:
            raise KeyError(date)
        decode = self.status_of
        out = []
        for i, ch in enumerate(row[2]):
            if ch == ".":
                continue
            mid = self.ids[i]
            fields = [f for d, f in self.meta[mid] if d <= date][-1]
            rec = {"id": mid}
            for k, v in fields.items():
                rec[k] = v
                if k == "postalCode":
                    rec["status"] = decode[ch]
            rec.setdefault("status", decode[ch])
            out.append(rec)
        return sorted(out, key=lambda r: r["id"])


# ---------- sources ----------

def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout

def from_upstream(since):
    """Yield (date, records) for days after `since` from cheeaun/returnright-data."""
    try:
        git("ls-remote", "--heads", UPSTREAM)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"upstream unreachable: {e.stderr.strip()[:200]}")
    tmp = tempfile.mkdtemp()
    args = ["clone", "--quiet", "--single-branch"]
    if since:  # only fetch recent commits; full history on first run
        start = dt.date.fromisoformat(since) - dt.timedelta(days=2)
        args.append(f"--shallow-since={start.isoformat()}")
    try:
        git(*args, UPSTREAM, tmp)
    except subprocess.CalledProcessError as e:
        if "no commits selected" in e.stderr:
            return
        raise RuntimeError(f"upstream clone failed: {e.stderr.strip()[:200]}")
    by_day = {}
    for line in filter(None, git("log", "--reverse", "--format=%H %cs", "--", FILE, cwd=tmp).split("\n")):
        sha, day = line.split()
        by_day[day] = sha
    for day in sorted(by_day):
        if since and day <= since:
            continue
        try:
            yield day, json.loads(git("show", f"{by_day[day]}:{FILE}", cwd=tmp))["data"]
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            print(f"upstream {day}: unreadable ({e}), skipped", file=sys.stderr)

def from_returnright():
    """Fetch today's list straight from returnright.sg, the way its public map does."""
    base = "https://returnright.sg"
    def get(path, extra=None):
        req = urllib.request.Request(base + path, headers={
            "user-agent": "Mozilla/5.0 (bcrs-tracker)", "accept": "application/json",
            "x-bcrs-client": "web", "referer": f"{base}/px/", **(extra or {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    last = None
    for attempt in range(3):  # a fresh token each attempt
        try:
            token = get("/px-api/locations/access-token")["data"]["token"]
            data = get("/px-api/locations", {"x-bcrs-map-token": token})["data"]
            if not isinstance(data, list):
                raise ValueError("unexpected response shape")
            def usable(x):
                def coord(v):
                    try: return v is not None and str(v).strip() != "" and float(v) == float(v)
                    except (TypeError, ValueError): return False
                return bool((x.get("locationName") or "").strip()) or (coord(x.get("latitude")) and coord(x.get("longitude")))
            return sorted(filter(usable, data), key=lambda x: x["id"])
        except Exception as e:  # network, HTTP, JSON or shape errors
            last = e; time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"returnright.sg fetch failed: {last}")


def update(source="auto"):
    a = Archive()
    today = dt.datetime.now(SGT).date().isoformat()
    added, problems = [], []
    if source in ("auto", "upstream"):
        try:
            for day, records in from_upstream(a.last_date):
                if day > today:
                    continue
                try:
                    if a.add_day(day, records, "u"): added.append(f"{day} (upstream)")
                except ValueError as e:
                    problems.append(str(e))
        except RuntimeError as e:
            problems.append(str(e))
    if source == "direct" or (source == "auto" and a.last_date != today):
        try:
            if a.add_day(today, from_returnright(), "d"): added.append(f"{today} (returnright.sg)")
        except (RuntimeError, ValueError) as e:
            problems.append(str(e))
    a.save()
    for p in problems:
        print(f"::warning::{p}")
    if not added:
        print(f"nothing new; archive ends {a.last_date}")
    elif len(added) <= 5:
        print("added " + ", ".join(added))
    else:
        print(f"added {len(added)} days: {added[0]} ... {added[-1]}")
    return a.last_date == today


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update"); u.add_argument("--source", default="auto", choices=["auto", "upstream", "direct"])
    s = sub.add_parser("snapshot"); s.add_argument("date")
    args = ap.parse_args()
    if args.cmd == "update":
        update(args.source)
    else:
        json.dump({"status": "ok", "data": Archive().snapshot(args.date)}, sys.stdout, indent=2, ensure_ascii=False)
        print()
