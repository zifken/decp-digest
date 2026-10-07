#!/usr/bin/env python3
"""Sample expiry report: IT sector (CPV 48+72), national, 6-12 month horizon.

Reads data/consol/expiries.parquet (built by expiry_build.py), emits
data/consol/expiries_upcoming.csv + .parquet (full upcoming expiries) and
report/expiry_report_it.{md,html} for the IT slice.

Filtering contract:
- cpv_family: first two CPV digits; "48" or "72" here.
- horizon: expires within [today+6m, today+12m] (calendar months).
- acheteur filter, dept filter optional (--dept 75 --acheteur SIRET).
- SIRET->name via decp-analytics' shared cache data/siret_names.json;
  missing names stay as raw SIRET with the "siret" column flagged.
- anonymized titulaires are never shown as names; column shows
  "(anonymisé)" and the anonymized flag stays True.
- montant: "montant maximum / estimé" for accords-cadres (montant of an AC
  is the maximum, not spend). Never presented as spend.
"""
import argparse
import html
import json
from datetime import date
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
CONSOL = BASE / "data" / "consol"
REPORT = BASE / "report"
ANALYTICS_DATA = Path("/home/kz/src/decp-analytics/data")
TODAY = date.today()


def load_names(sirets=()):
    p = ANALYTICS_DATA / "siret_names.json"
    cache = {}
    if p.exists():
        try:
            cache = json.loads(p.read_text())
        except Exception:
            cache = {}
    # extend the shared cache for SIRETs not seen yet (recherche-entreprises,
    # best effort, same policy as decp-analytics/enrich.py)
    missing = sorted({s for s in sirets
                      if s and s not in cache and len(str(s)) == 14})
    if missing:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "enrich", ANALYTICS_DATA.parent / "enrich.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        try:
            cache.update(mod.enrich(missing, max_new=len(missing)))
        except Exception as e:
            print(f"enrich failed ({e}); using cache only",
                  file=__import__("sys").stderr)
    return cache


def month_add(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def rank_buyers(sel: pd.DataFrame) -> pd.DataFrame:
    """Rank buyers by NUMBER of expiring contracts (review 2026-09-28).

    Accord-cadre runs are deduped: all rows of one framework (acheteur +
    idAccordCadre, falling back to the row id when the field is empty) count
    ONCE, so a single framework ceiling (e.g. 300 M€ under one AC) can never
    top the list on amount. montant_max is a MAXIMUM, labelled
    "montant maximum / estimé" in every output.
    """
    def _unit_key(r):
        ac = r["id_ac"]
        return (r["acheteur"], ac if pd.notna(ac) else f"id:{r['id']}")
    sel = sel.copy()
    sel["_unit_key"] = sel.apply(_unit_key, axis=1)
    unit = sel.drop_duplicates(subset="_unit_key")
    return (unit.groupby("acheteur")
            .agg(n_marches=("id", "size"),
                 montant_max=("montant", "max"),
                 montant_cumul=("montant", "sum"))
            .sort_values(["n_marches", "montant_max"],
                         ascending=[False, False]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpv", nargs="*", default=["48", "72"],
                    help="CPV 2-digit families (default 48 72)")
    ap.add_argument("--dept", nargs="*", default=None)
    ap.add_argument("--acheteur", default=None, help="buyer SIRET filter")
    ap.add_argument("--out-prefix", default="expiry_report_it")
    args = ap.parse_args()

    df = pd.read_parquet(CONSOL / "expiries.parquet")
    names = {}  # filled after selection, see below

    lo, hi = month_add(TODAY, 6), month_add(TODAY, 12)
    # horizon end: day before the 12-month month start (i.e. end of month 11)
    hi_end = pd.Timestamp(month_add(TODAY, 12)) - pd.Timedelta(days=1)
    lo_ts = pd.Timestamp(lo)

    up = df[(df["date_expiration"] >= lo_ts) & (df["date_expiration"] <= hi_end)]
    up.to_parquet(CONSOL / "expiries_upcoming.parquet")
    up.to_csv(CONSOL / "expiries_upcoming.csv", index=False)
    print(f"upcoming expiries (6-12m): {len(up)} rows", file=__import__('sys').stderr)

    sel = up[up["cpv"].str[:2].isin(args.cpv)]
    if args.dept:
        sel = sel[sel["dept"].isin(args.dept)]
    if args.upcoming if False else args.acheteur:
        sel = sel[sel["acheteur_id"] == args.acheteur]

    sel = sel.copy()
    all_sirets = set(sel["acheteur_id"].dropna().astype(str))
    for ts in sel["titulaires_siret"].dropna():
        all_sirets.update(str(ts).split("; "))
    names.update(load_names(all_sirets))

    def buyer_label(s):
        s = str(s)
        return names.get(s) or f"SIRET {s}"
    sel["acheteur"] = sel["acheteur_id"].map(buyer_label)
    def _tit_label(r):
        valid = ([t for t in str(r["titulaires_siret"]).split("; ")
                  if t and t != "None"]
                 if pd.notna(r["titulaires_siret"]) else [])
        shown = "; ".join(filter(None, (names.get(t) or t for t in valid)))
        if r["anonymized"]:
            return (shown + " (+ titulaire(s) non identifié(s))") if shown                 else "(anonymisé)"
        return shown or "(non communiqué)"

    sel["titulaire"] = sel.apply(_tit_label, axis=1)
    sel["montant_label"] = sel["ac"].map(
        lambda a: "montant maximum / estimé" if a else "montant")

    sel = sel.sort_values(["date_expiration", "montant"],
                          ascending=[True, False])
    total = len(sel)
    ac_n = int(sel["ac"].sum())
    anon_n = int(sel["anonymized"].sum())
    by_month = sel.groupby(sel["date_expiration"].dt.to_period("M")).size()
    by_div = sel["cpv_div"].value_counts()
    # Ranking rule (review 2026-09-28): see rank_buyers().
    top_buyers = rank_buyers(sel)

    L = []
    A = L.append
    A(f"# Expiries à venir — secteur informatique (CPV {', '.join(args.cpv)})")
    A("")
    hi_disp = (pd.Timestamp(month_add(TODAY, 12)) - pd.Timedelta(days=1)).date().isoformat()
    A(f"Généré le {TODAY.isoformat()} — horizon {lo.isoformat()} → {hi_disp}")
    A("")
    A("Estimation : date_expiration = dateNotification + dureeMois. Les DECP "
      "ne comportent pas de champ prolongation; une échéance est un signal "
      "de fenêtre de renouvellement probable, pas une date de fin certaine. "
      "Les montants des accords-cadres sont des montants maximum / estimé, "
      "pas des dépenses.")
    A("")
    A(f"**{total}** marchés informatiques expirent entre le {lo.isoformat()} "
      f"et le {hi_disp} (national).")
    A("")
    A(f"- dont accords-cadres : {ac_n} ({ac_n/total:.0%}) — montants = maxima")
    A(f"- dont gagnants anonymisés : {anon_n} ({anon_n/total:.0%})")
    A(f"- montant max/estimé cumulé : {sel['montant'].sum():,.0f} €".replace(",", " "))
    A("")
    A("## Par mois d'échéance")
    A("")
    A("| Mois | Nombre |")
    A("|---|---|")
    for p, n in by_month.items():
        A(f"| {p} | {n} |")
    A("")
    A("## Par famille CPV")
    A("")
    A("| Famille | Nombre |")
    A("|---|---|"
      )
    for d, n in by_div.items():
        A(f"| {d} | {n} |")
    A("")
    A("## Top acheteurs (par nombre de contrats arrivant à échéance)")
    A("")
    A("Les échéances d'un même accord-cadre sont comptées une fois; le "
      "montant indiqué est un montant maximum / estimé, pas une dépense.")
    A("")
    A("| Acheteur | Contrats | Montant max/estimé le plus élevé (€) |")
    A("|---|---|---|")
    for buyer, row in top_buyers.iterrows():
        A(f"| {buyer} | {int(row['n_marches'])} | {row['montant_max']:,.0f} |"
          .replace(",", " "))
    A("")
    A("## Détail (top 50 par montant)")
    A("")
    A("| Échéance | Acheteur | Objet | Titulaire(s) | Nature | Montant | Label |")
    A("|---|---|---|---|---|---|---|")
    for _, r in sel.head(50).iterrows():
        obj = str(r["objet"] or "")[:90]
        m = f"{r['montant']:,.0f}" if pd.notna(r["montant"]) else "n/c"
        A(f"| {r['date_expiration'].date()} | {r['acheteur']} | {obj} | "
          f"{r['titulaire']} | {r['nature']} | {m} | {r['montant_label']} |"
          .replace(",", " "))
    A("")
    files_used = sorted(p.name for p in CONSOL.glob("decp-*.json"))
    A(f"*Source : DECP consolidées data.gouv ({', '.join(files_used)}), "
      f"{len(df):,} marchés exploitables.".replace(",", " "))

    md = "\n".join(L)
    REPORT.mkdir(exist_ok=True)
    (REPORT / f"{args.out_prefix}.md").write_text(md)

    # minimal HTML export
    import markdown  # type: ignore
    body = markdown.markdown(md, extensions=["tables"])
    page = f"""<!doctype html><html lang=fr><meta charset=utf-8>
<title>Expiries IT — DECP</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;
padding:0 1rem;color:#1a1a2e}} table{{border-collapse:collapse;font-size:0.85em;
width:100%}} td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}
th{{background:#eef}} caption{{font-weight:bold;padding:6px}}</style>
<body>{body}</body></html>"""
    (REPORT / f"{args.out_prefix}.html").write_text(page)
    print(f"wrote report/{args.out_prefix}.md and .html")


if __name__ == "__main__":
    main()
