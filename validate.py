#!/usr/bin/env python3
"""Validate the DECP daily-delta source (dataset 5df410e8 = API DECP mirrors).

Reads every data/raw/decp-*.json file (the week of daily deltas fetched by
fetch.py) and reports:
  - volume: records per day / estimated weekly volume
  - field quality: % fill rate of the fields the digest needs
  - duplicates: same (acheteur, id) appearing across more than one file
  - lag: dateNotification vs datePublicationDonnees

Stdlib only. Run `python3 fetch.py` first, or `python3 validate.py --fetch`
to pull the latest deltas before validating.
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
RAW = BASE / "data" / "raw"

# fields the weekly digest needs
FIELDS = [
    "objet",
    "codeCPV",
    "montant",
    "dureeMois",
    "dateNotification",
    "lieuExecution",
    "titulaires",
]


def flat(rec: dict, key: str):
    v = rec.get(key)
    if key == "lieuExecution" and isinstance(v, dict):
        return v.get("code")
    return v


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="run fetch.py first")
    args = ap.parse_args(argv)
    if args.fetch:
        import fetch
        fetch.main()

    files = sorted(RAW.glob("decp-*.json"))
    if not files:
        print("no raw files; run `python3 fetch.py` first", file=sys.stderr)
        return 1

    per_day = {}
    seen = {}          # (acheteur, id) -> file stem
    dups = Counter()
    fill = {f: [0, 0] for f in FIELDS}          # filled, total
    lag_days = []                                # publication - notification
    cpv_div = Counter()

    total = 0
    for f in files:
        d = json.loads(f.read_text())
        recs = d.get("marches") or []
        if isinstance(recs, dict):
            flatrecs = []
            for v in recs.values():
                flatrecs.extend(v if isinstance(v, list) else [v])
            recs = flatrecs
        recs = [x for x in recs if isinstance(x, dict)]
        per_day[f.stem] = len(recs)
        total += len(recs)
        for r in recs:
            ach = r.get("acheteur")
            if isinstance(ach, list):
                ach = ach[0] if ach else {}
            if not isinstance(ach, dict):
                ach = {}
            key = (ach.get("id"), r.get("id"))
            if key in seen and seen[key] != f.stem:
                dups[key] += 1
            seen.setdefault(key, f.stem)
            for fld in FIELDS:
                fill[fld][1] += 1
                if flat(r, fld) not in (None, "", [], {}):
                    fill[fld][0] += 1
            for tk in (r.get("titulaires") or []):
                t = tk.get("titulaire", {})
                if t.get("typeIdentifiant") != "SIRET":
                    pass
            dn = r.get("dateNotification")
            dp = r.get("datePublicationDonnees")
            if dn and dp:
                try:
                    lag_days.append((date.fromisoformat(dp) - date.fromisoformat(dn)).days)
                except ValueError:
                    pass
            cpv = r.get("codeCPV")
            if cpv:
                cpv_div[cpv[:2]] += 1

    days = len(files)
    print(f"files: {days} daily delta files, {total} records total")
    for stem, n in per_day.items():
        print(f"  {stem}: {n}")
    if total and days:
        print(f"weekly volume estimate: {total / days * 7:.0f} records/week "
              f"(mean {statistics.mean(per_day.values()):.1f}/day)")
    print(f"duplicate (acheteur,id) across files: {sum(dups.values())} "
          f"({sum(dups.values()) / total * 100:.2f}% of {total})")
    if lag_days:
        med = statistics.median(lag_days)
        p10, p90 = sorted(lag_days)[len(lag_days)//10], sorted(lag_days)[int(len(lag_days)*0.9)]
        print(f"publication lag vs notification: median {med} d, p10 {p10} d, p90 {p90} d")
    print("field fill rates:")
    for fld, (have, n) in fill.items():
        print(f"  {fld:18s} {have:5d}/{n}  ({have/n*100:5.1f}%)")
    print("top CPV divisions:", dict(cpv_div.most_common(6)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
