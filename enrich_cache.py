#!/usr/bin/env python3
"""Fill the shared SIRET->name cache for the IT report's SIRETs."""
import json, time, urllib.request, sys
from pathlib import Path
import pandas as pd

CACHE = Path('/home/kz/src/decp-analytics/data/siret_names.json')
cache = json.loads(CACHE.read_text())
df = pd.read_parquet('data/consol/expiries.parquet')
sel = df[df['cpv'].str[:2].isin(['48', '72'])]
sirets = set(sel['acheteur_id'].dropna().astype(str))
for ts in sel['titulaires_siret'].dropna():
    sirets.update(s for s in str(ts).split('; ') if s)
missing = sorted(s for s in sirets if s not in cache and len(s) == 14)
print(f"to resolve: {len(missing)}", file=sys.stderr)
ok = 0
for i, s in enumerate(missing):
    try:
        url = f"https://recherche-entreprises.api.gouv.fr/search?q={s}&mtm_campaign=api"
        req = urllib.request.Request(url, headers={"User-Agent": "decp-analytics/0.1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        res = d.get("results") or []
        if res:
            cache[s] = res[0].get("nom_complet") or res[0].get("denomination") or None
            ok += 1
        else:
            cache[s] = None
    except Exception as e:
        if "429" in str(e):
            time.sleep(2.5)
            continue  # rate-limited: retry same SIRET (loop does not advance)
        print(f"stop at {i}: {e}", file=sys.stderr)
        break
    if i % 100 == 0:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True))
    time.sleep(0.55)
CACHE.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True))
print(f"resolved {ok}/{len(missing)}", file=sys.stderr)
