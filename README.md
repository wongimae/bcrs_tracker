# Return Right dashboard

A daily-updating dashboard of Singapore's Return Right reverse vending machines,
with its own archive of every day's data.

## How it works

Every morning a GitHub Action:

1. Adds any missing days to `archive/`. It first tries
   [cheeaun/returnright-data](https://github.com/cheeaun/returnright-data)
   (only the last few days of history are downloaded). If that repo is down, deleted,
   or hasn't published today's snapshot, it fetches the list straight from returnright.sg.
2. Commits the new day to `archive/` in this repo.
3. Rebuilds the dashboard from the archive and deploys it to GitHub Pages.

So the dashboard keeps working, and keeps its history, even if the upstream repo disappears.

## The archive

Plain text, append-only, so each day's commit is a small diff (a few KB).

| File | What it holds |
|---|---|
| `archive/status.txt` | One line per day: `date source statuses`, one character per machine |
| `archive/index.txt` | Machine ids; line *n* is character *n* of each status line |
| `archive/machines.jsonl` | Every field except status, written only when a machine's details change |
| `archive/codes.json` | What each status character means (`.` = not listed that day) |

Source is `u` for cheeaun/returnright-data and `d` for a direct fetch.
Any day can be rebuilt exactly as `latest.json` looked:

```bash
python3 scripts/archive.py snapshot 2026-05-01 > 2026-05-01.json
```

Three fields that changed on every snapshot and carried no lasting meaning
(`rvm_last_conn`, `updatedAt`, `distance`) are not kept; upstream stopped publishing them
on 2026-09-16.

## Setup

1. Push these files to the `main` branch of a public repo.
2. **Settings → Pages → Source: GitHub Actions**.
3. **Settings → Actions → General → Workflow permissions: Read and write permissions**
   (so the Action can commit the archive).
4. **Actions → Update archive and dashboard → Run workflow**.

To test the fallback without waiting for upstream to fail, run the workflow with
**source: direct**.

## Run locally

```bash
python3 scripts/archive.py update        # add missing days
python3 scripts/compile_history.py _site
cp site/index.html _site/
python3 -m http.server -d _site 8000     # open http://localhost:8000
```
