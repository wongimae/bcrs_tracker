#!/usr/bin/env python3
"""
Hourly bin readings from the BCRS platform (bts.bcrs.sg), the same source
TheDJVG/BCRSTracking uses.

Each run reads every machine's bin status, works out how many containers each
machine took since the previous reading, and appends one line to
archive/bins/returns-YYYY-MM.txt:

    2026-09-30T14 2bs:c 2bt:1f ...

i.e. the Singapore hour of the reading, then "<machine id>:<containers>" for every
machine that took at least one container, both in base 36. Machines with no
returns that hour are left out, so quiet hours cost almost nothing.

archive/bins/state.json keeps the latest raw reading per machine (needed for the
next run's differences and for the dashboard's fullness ring).

How returns are counted, per compartment (following BCRSTracking):
  * capacity == 0 (TOMRA): current_count is a lifetime counter that only goes up,
    so returns = the increase. threshold_level is the fill percentage.
  * capacity  > 0 (RVM Systems, SG Recycle): current_count is what is in the bin.
    An increase is returns. A big drop means the bin was emptied, and whatever is
    in it now arrived since. Small wobbles are ignored.
"""
import datetime as dt, json, os, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "archive" / "bins"
STATE = DIR / "state.json"
BASE = os.environ.get("BCRS_BTS_URL", "https://bts.bcrs.sg/forapi/v2").rstrip("/")
SGT = ZoneInfo("Asia/Singapore")
WORKERS = 4
RPS = float(os.environ.get("BINS_RPS", "4"))   # about 6 minutes for 1,400 machines
MIN_SHARE = 0.5                # need readings for at least half the machines
COLOR_SUPPLIER = {"Blue": "TOMRA001", "Red": "SGRECYCLE001", "Green": "RVMS001"}


def b36(n):
    s, n = "", int(n)
    while True:
        n, r = divmod(n, 36)
        s = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + s
        if not n:
            return s


class Api:
    def __init__(self):
        self.token, self.lock = None, threading.Lock()
        self.gap, self.next_at = 1.0 / RPS, 0.0

    def _wait(self):
        with self.lock:
            now = time.monotonic()
            self.next_at = max(self.next_at, now) + self.gap
            delay = self.next_at - self.gap - now
        if delay > 0:
            time.sleep(delay)

    def _get(self, path, auth):
        headers = {"User-Agent": "bcrs-tracker-dashboard (+github actions)",
                   "Accept": "application/json", "x-bcrs-client": "web",
                   "Referer": "https://bts.bcrs.sg/"}
        if auth:
            headers["x-bcrs-map-token"] = self._token()
        self._wait()
        with urllib.request.urlopen(urllib.request.Request(BASE + path, headers=headers), timeout=20) as r:
            return json.load(r)

    def _token(self):
        with self.lock:
            tok = self.token
        if tok:
            return tok
        body = self._get("/locations/access-token", auth=False)
        inner = body.get("data") if isinstance(body.get("data"), dict) else body
        tok = next((inner.get(k) or body.get(k) for k in ("token", "accessToken", "access_token", "mapToken")
                    if inner.get(k) or body.get(k)), None)
        if not tok:
            raise RuntimeError(f"no token in access-token response: {str(body)[:200]}")
        with self.lock:
            self.token = tok
        return tok

    def get(self, path, tries=4):
        for attempt in range(tries):
            try:
                return self._get(path, auth=True)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    with self.lock:
                        self.token = None      # expired token: fetch a new one
                elif e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                    raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                if attempt == tries - 1:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError(f"{path}: gave up after {tries} tries")


def returns_for(prev, cur):
    """Containers taken by one compartment between two readings."""
    if prev is None:
        return 0
    cap, count = cur["cap"], cur["n"]
    if cap == 0:                                    # lifetime counter
        d = count - prev["n"]
        return d if 0 <= d <= 5000 else 0           # counter reset or glitch
    if count > prev["n"]:
        return min(count - prev["n"], cap)
    if prev["n"] >= 50 and count <= prev["n"] / 2:  # bin was emptied
        return min(count, cap)
    return 0


def main():
    now = (dt.datetime.fromisoformat(os.environ["BINS_NOW"]).replace(tzinfo=SGT)
           if os.environ.get("BINS_NOW") else dt.datetime.now(SGT))   # BINS_NOW: testing only
    hour = now.strftime("%Y-%m-%dT%H")
    api = Api()
    try:
        locations = api.get("/locations")["data"]
    except Exception as e:
        print(f"::warning::bin readings skipped, locations request failed: {e}")
        return
    machines = {int(r["id"]): (r.get("supplierId") or COLOR_SUPPLIER.get(r.get("coords_color"), "UNKNOWN"))
                for r in locations if r.get("id") is not None}

    def read(mid):
        try:
            return mid, api.get(f"/locations/rvms/{mid}/bin-status")["data"]
        except Exception as e:
            return mid, e

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(read, sorted(machines)))
    ok = {mid: bins for mid, bins in results if not isinstance(bins, Exception)}
    failed = len(results) - len(ok)
    if len(ok) < len(machines) * MIN_SHARE:
        print(f"::warning::only {len(ok)} of {len(machines)} machines answered; reading not saved")
        return

    old = json.loads(STATE.read_text()) if STATE.exists() else {"m": {}}
    new, line = {}, []
    for mid, bins in ok.items():
        comps = [{"type": (b.get("compartment_type") or "UNKNOWN"), "cap": int(b.get("capacity") or 0),
                  "n": int(b.get("current_count") or 0), "fill": int(b.get("threshold_level") or 0)}
                 for b in bins or []]
        prev = {c["type"]: c for c in old["m"].get(str(mid), {}).get("c", [])}
        took = sum(returns_for(prev.get(c["type"]), c) for c in comps)
        if took:
            line.append(f"{b36(mid)}:{b36(took)}")
        new[str(mid)] = {"s": machines[mid], "c": comps}
    for mid, rec in old["m"].items():     # keep machines that didn't answer this time
        new.setdefault(mid, rec)

    DIR.mkdir(parents=True, exist_ok=True)
    if old.get("t"):                        # the first run only sets the baseline
        with open(DIR / f"returns-{now:%Y-%m}.txt", "a") as f:
            f.write(" ".join([hour, *line]) + "\n")
    STATE.write_text(json.dumps({"t": now.isoformat(timespec="minutes"), "first": old.get("first") or hour,
                                 "m": dict(sorted(new.items(), key=lambda kv: int(kv[0])))},
                                separators=(",", ":")) + "\n")
    total = sum(int(t.split(":")[1], 36) for t in line)
    print(f"{hour}: {len(ok)} machines read ({failed} failed), {total:,} containers returned since last reading")


if __name__ == "__main__":
    main()
