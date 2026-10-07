#!/usr/bin/env python3
"""Build the contract-expiry forecast dataset from consolidated DECP files.

Source: data.gouv.fr dataset "Données essentielles de la commande publique -
fichiers consolidés" (annual decp-YYYY.json files), open data, no scraping.

Expiry estimate = dateNotification + dureeMois. The DECP consolidated format
has no fields for tacit extensions, so an expiring contract is a *signal of a
likely re-tender window*, not a guarantee.

Handling rules (requirement 1):
- dureeMois missing / <= 0 -> dropped from the forecast, counted as unusable.
- dureeMois > 360 (30y, concession-level or data error) -> dropped, absurd.
- dateNotification missing -> dropped.
- Accords-cadres: montant is the MAXIMUM of the framework, not spend. Rows
  detected as accords-cadres are labelled ac=True; any amount shown is
  "montant maximum / estimé".
- Anonymized titulaires (ids like "00001", non-14-digit) are flagged, never
  displayed as company names.

Dedupe: files processed oldest-first, key (acheteur.id, id); the newest
consolidation of a key wins (annual files carry revised values).
"""
import json
import sys
from datetime import date
from pathlib import Path

import ijson
import pandas as pd

BASE = Path(__file__).resolve().parent
CONSOL = BASE / "data" / "consol"

CPV_DIV = {
    "30": "Informatique (matériel)", "32": "Électronique",
    "43": "Travaux (bâtiment, ingénierie)", "45": "Travaux de construction",
    "48": "Logiciels et services informatiques",
    "50": "Réparation et entretien", "55": "Restauration, hôtellerie",
    "60": "Transports", "63": "Services de transport",
    "64": "Postal et télécoms", "65": "Services publics (eau, déchets)",
    "70": "Immobilier", "71": "Services d'architecture et ingénierie",
    "72": "Services informatiques", "73": "R&D et services techniques",
    "75": "Services administratifs", "79": "Conseil, marketing, recrutement",
    "80": "Formation", "85": "Santé et social", "90": "Déchets, environnement",
}


def norm_dept(code):
    s = str(code or "").strip()
    if not s.isdigit():
        return None
    if s.startswith("97") or s.startswith("98"):
        return s[:3]
    return s[:2]


def parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def is_anonymized(tid):
    """No usable company identity: anonymized code, VAT number, or malformed.

    Real French SIRETs are exactly 14 digits; everything else is flagged and
    never displayed as a company name.
    """
    return not (tid and tid.isdigit() and len(tid) == 14)


def detect_ac(m):
    """True = framework-agreement family (montant = maximum, not spend).

    Signals: techniques ['Accord-cadre'], "accord-cadre"/"accord cadre" in
    objet/nature, or a "marché subséquent" (purchase run under an existing
    framework). "sans accord-cadre" negates. Not detected via idAccordCadre:
    the consolidated format's field is sparsely filled.
    """
    obj = str(m.get("objet") or "").lower()
    if "sans accord-cadre" in obj:
        return False
    blob = str([m.get("nature") or "", m.get("formePrix") or "",
                m.get("techniques") or "", m.get("modalitesExecution") or "",
                obj]).lower()
    return ("accord-cadre" in blob or "accord cadre" in blob
            or "subséquent" in blob or "subsequent" in blob)


def acheteur_id(m):
    """Buyer id: 2024+ files nest it (acheteur.id), older files use a flat
    "acheteur.id" string key."""
    a = m.get("acheteur")
    if isinstance(a, dict):
        return a.get("id")
    return m.get("acheteur.id")


def titulaire_ids(m):
    out = []
    ts = m.get("titulaires")
    if isinstance(ts, str):  # rare malformed 2022 rows
        return [ts]
    for t in (ts or []):
        if isinstance(t, dict):
            # 2024+ files: {"titulaire": {"id": ...}}; pre-2024 files: the
            # typeIdentifiant/id pair sits directly on the entry
            tid = (t.get("titulaire") or {}).get("id") if \
                "titulaire" in t else t.get("id")
            if tid:
                out.append(str(tid))
        elif t is not None:
            out.append(str(t))
    return out


def norm(m):
    """Return a normalized row dict, or None if dropped (with reason)."""
    dn = parse_date(m.get("dateNotification"))
    d = m.get("dureeMois")
    try:
        d = int(d) if d is not None else None
    except (ValueError, TypeError):
        d = None
    if dn is None:
        return "dropped_nodate"
    if d is None or d <= 0:
        return "dropped_noduree"
    if d > 360:
        return "dropped_absurd"
    tids = titulaire_ids(m)
    cpv = str(m.get("codeCPV") or "")  # older files: sometimes numeric
    return {
        "acheteur_id": str(acheteur_id(m)) if acheteur_id(m) is not None else None,
        "id": str(m.get("id")) if m.get("id") is not None else None,
        "id_ac": str(m.get("idAccordCadre")) if m.get("idAccordCadre") else None,
        "objet": m.get("objet"),
        "date_notification": str(dn),
        "duree_mois": d,
        "montant": m.get("montant"),
        "cpv": cpv,
        "cpv_div": CPV_DIV.get(cpv[:2], (cpv[:2] or "?") + "?"),
        "dept": norm_dept((m.get("lieuExecution") or {}).get("code")),
        "nature": m.get("nature"),
        "ac": detect_ac(m),
        "anonymized": any(is_anonymized(t) for t in tids) or not tids,
        "titulaires_siret": "; ".join(t for t in tids if not is_anonymized(t)) or None,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=None,
                    help="specific files; default: all data/consol/decp-*.json")
    args = ap.parse_args()
    files = [Path(p) for p in args.files] if args.files else \
        sorted(CONSOL.glob("decp-*.json"))
    stats = {"superseded": 0, "dropped_nodate": 0, "dropped_noduree": 0,
             "dropped_absurd": 0, "rows": 0}
    # files are processed oldest-first; the NEWEST consolidation of a
    # (acheteur, id) wins (annual files carry revised durations/amounts)
    rows_by_key = {}
    dropped_keys = set()
    for f in files:
        with open(f, "rb") as fh:
            head = fh.read(1 << 20)
            i = head.find(b'"marches"')
            j = head.find(b":", i)
            arr = head[j + 1:j + 6].lstrip()[:1] == b"[" if i >= 0 and j >= 0 else False
            # 2024+ files: {"marches":{"marche":[...]}}; 2019/2022 files:
            # {"marches":[...]} (the 2019 file is 943 MB -> stream, never
            # json.loads)
            path = "marches.item" if arr else "marches.marche.item"
            fh.seek(0)
            data = ijson.items(fh, path)
            for m in data:
                key = (str(acheteur_id(m)) if acheteur_id(m) is not None else None,
                       str(m.get("id")) if m.get("id") is not None else None)
                if key in rows_by_key or key in dropped_keys:
                    stats["superseded"] += 1
                    dropped_keys.discard(key)
                r = norm(m)
                if isinstance(r, str):
                    if key not in rows_by_key:
                        stats[r] += 1
                        dropped_keys.add(key)
                    continue
                rows_by_key[key] = r
        print(f"{f.name}: total {len(rows_by_key)}", file=sys.stderr)

    recs = list(rows_by_key.values())
    stats["rows"] = len(recs)
    df = pd.DataFrame(recs)
    df["montant"] = pd.to_numeric(df["montant"], errors="coerce")
    df["date_notification"] = pd.to_datetime(df["date_notification"])
    # expiry = notification + N calendar months
    df["date_expiration"] = df.apply(
        lambda r: (r["date_notification"].to_period("M") + int(r["duree_mois"])
                   ).to_timestamp("M"), axis=1)
    df.to_parquet(CONSOL / "expiries.parquet")
    df.to_csv(CONSOL / "expiries.csv", index=False)
    stats["ac_share"] = round(float(df["ac"].mean()), 4)
    stats["anonymized_share"] = round(float(df["anonymized"].mean()), 4)
    stats["volume_per_notification_year"] = {
        str(k): int(v) for k, v in
        df["date_notification"].dt.year.value_counts().sort_index().to_dict().items()}
    (CONSOL / "build_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1))
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
