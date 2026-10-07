#!/usr/bin/env python3
"""Fetch new daily DECP delta files from data.gouv (dataset api-decp).

Watermark in data/state.json: last processed resource title.
Downloads only files newer than the watermark, saves to data/raw/.
Stdlib only.
"""
import json
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
RAW = BASE / "data" / "raw"
STATE = BASE / "data" / "state.json"
API = ("https://www.data.gouv.fr/api/2/datasets/"
       "5df410e86f44413a91d34be3/resources/?page={page}&page_size=50&sort=-last_modified")

# decp-DDMMYYYY-NNN-0130.json
NAME_RE = re.compile(r"decp-(\d{2})(\d{2})(\d{4})-\d+-\d+\.json$")


def slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", title)


def title_date(title: str):
    m = NAME_RE.search(title or "")
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return date(int(yyyy), int(mm), int(dd))


def list_resources(max_pages=20):
    out = []
    for p in range(1, max_pages + 1):
        with urllib.request.urlopen(API.format(page=p), timeout=60) as r:
            data = json.load(r)
        items = data.get("data", [])
        out.extend(items)
        if len(items) < 50:
            break
    return out


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    state = {}
    if STATE.exists():
        state = json.loads(STATE.read_text())
    last = state.get("last_title")
    last_d = title_date(last) if last else None
    # default first run: last 7 days
    cutoff = last_d or (date.today() - timedelta(days=8))

    fresh = []
    for res in list_resources():
        d = title_date(res.get("title", ""))
        if d is None or d <= cutoff:
            continue
        url = res.get("url")
        if not url:
            continue
        fresh.append((d, res["title"], url))
    fresh.sort()
    if not fresh:
        print("nothing new")
        return

    n = 0
    for d, title, url in fresh:
        dest = RAW / slug(title)
        if dest.exists():
            continue
        print(f"fetch {title}")
        urllib.request.urlretrieve(url, dest)
        n += 1
    state["last_title"] = fresh[-1][1]
    STATE.write_text(json.dumps(state))
    print(f"downloaded {n} files (watermark -> {state['last_title']})")


if __name__ == "__main__":
    sys.exit(main())
