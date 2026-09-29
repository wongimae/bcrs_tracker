# Return Right dashboard

A daily-updating dashboard for [cheeaun/returnright-data](https://github.com/cheeaun/returnright-data).

The data repo overwrites `data/latest.json` every day, so the history lives in its commits.
Each morning, the GitHub Action in this repo clones the data repo, rebuilds the day-by-day history
(`scripts/compile_history.py`), and deploys `site/index.html` plus `history.json` to GitHub Pages.

## Setup

1. Create a new public repo and push these files to its `main` branch.
2. Go to **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Go to **Actions → Build dashboard → Run workflow** to do the first build.

The site appears at `https://<your-username>.github.io/<repo-name>/`.
After that it rebuilds at 03:00 and 09:00 Singapore time, and on every push.

## Run locally

```bash
git clone https://github.com/cheeaun/returnright-data.git upstream
python3 scripts/compile_history.py upstream _site
cp site/index.html _site/
python3 -m http.server -d _site 8000   # open http://localhost:8000
```
