#!/usr/bin/env python3
"""Watchlist report generator (v1) — monthly per-supplier expiry watchlist.

For each prospect profile in data/prospects.json, reads data/consol/expiries.parquet
(built by expiry_build.py) and emits report/watchlist/<slug>.{md,html}:

- contracts expiring in the next 6–12 months (the re-tender window), grouped by
  month of expiry, sorted by expiry date.
- per line: buyer (SIRET cache), objet, duréée, CPV family, nature,
  montant (labelled "montant maximum / estimé" for accords-cadres), titulaire(s)
  (incumbent — who the prospect displaces or partners with).
- accord-cadre runs deduped by (acheteur, id_ac fallback id) for the buyer
  ranking (reuses expiry_report.rank_buyers).
- invariants: anonymized / malformed / VAT titulaires are never shown as names
  (flagged "(anonymisé)" / "(non communiqué)"); amounts are always labelled.
- footer states the data blind spots once, in French.

Usage:  python watchlist.py [--profiles data/prospects.json] [--all]
Send identity in the footer memo is kenziferaoun@proton.me (never bioniclia).
"""
import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

import expiry_report as er  # load_names, rank_buyers, month_add

BASE = Path(__file__).resolve().parent
CONSOL = BASE / "data" / "consol"
REPORT = BASE / "report" / "watchlist"
TODAY = date.today()

SENDER = "kenziferaoun@proton.me"

FOOTER = (
    "Limites des données (à connaître avant tout contact) : la date d'échéance est "
    "estimée (dateNotification + dureeMois) — la DECP ne comporte pas de champ de "
    "prolongation, et un accord-cadre peut être reconduit tacitement, donc une "
    "échéance signale une fenêtre de renouvellement probable, pas une fin certaine. "
    "Certains acheteurs ne publient pas (sous-seuils) ; le SIRET de l'acheteur est "
    "conservé quand aucun nom public n'est disponible. Les montants des accords-cadres "
    "sont des montants maximum / estimé, pas des dépenses constatées."
)

HOWTO = (
    "Comment utiliser ce rapport : dans les 6–12 mois précédant l'échéance estimée, "
    "l'acheteur va lancer un nouvel appel d'offres (parfois avec simple reconduction "
    "si le cadre le permet). Contactez l'acheteur — ou le titulaire sortant pour une "
    "partenariat / reprise — AVANT la publication du nouvel appel, quand le besoin est "
    "encore en préparation : c'est là que se jouent les relations avec l'équipe "
    "acheteuse et l'influence sur le cahier des charges. Après publication, il est "
    "souvent trop tard pour influencer le cadre, seulement pour répondre."
)


def _buyer_label(acheteur_id, names):
    s = str(acheteur_id)
    return names.get(s) or f"SIRET {s}"


def _tit_label(r, names):
    raw = str(r["titulaires_siret"]) if pd.notna(r["titulaires_siret"]) else ""
    # only ever render a valid 14-digit SIRET as a name-as-id; anonymized /
    # malformed / VAT ids are never printed (quality bar #4)
    valid = [t for t in raw.split("; ") if t and t.isdigit() and len(t) == 14] \
        if raw else []
    shown = "; ".join(filter(None, (names.get(t) or t for t in valid)))
    if r["anonymized"]:
        return " ; ".join(filter(None, [shown or None, "(titulaire(s) non identifié(s))"])) \
            or "(non identifié)"
    return shown or "(non communiqué)"


def fmt_amount(v, label):
    if pd.notna(v):
        return f"{v:,.0f}".replace(",", " ")
    return "n/c"


def build_profile(prof, df, names):
    lo, hi = er.month_add(TODAY, 6), er.month_add(TODAY, 12)
    hi_end = pd.Timestamp(er.month_add(TODAY, 12)) - pd.Timedelta(days=1)
    up = df[(df["date_expiration"] >= pd.Timestamp(lo)) &
            (df["date_expiration"] <= hi_end)]
    sel = up[up["cpv"].str[:2].isin(prof["cpv"])]
    if prof.get("depts"):
        sel = sel[sel["dept"].isin(prof["depts"])]
    if prof.get("acheteurs"):
        sel = sel[sel["acheteur_id"].astype(str).isin(
            [str(a) for a in prof["acheteurs"]])]
    if sel.empty:
        return None, None
    sel = sel.copy()
    sel["acheteur"] = sel["acheteur_id"].map(lambda s: _buyer_label(s, names))
    sel["titulaire"] = sel.apply(lambda r: _tit_label(r, names), axis=1)
    sel["montant_label"] = sel["ac"].map(
        lambda a: "montant maximum / estimé" if a else "montant")
    sel = sel.sort_values(["date_expiration", "montant"],
                          ascending=[True, False])

    total = len(sel)
    ac_n = int(sel["ac"].sum())
    anon_n = int(sel["anonymized"].sum())
    top_buyers = er.rank_buyers(sel)
    months = sorted(sel["date_expiration"].dt.to_period("M").unique())

    L = []
    A = L.append
    hi_disp = (pd.Timestamp(er.month_add(TODAY, 12)) -
               pd.Timedelta(days=1)).date().isoformat()
    A(f"# {prof['title']}")
    A("")
    A(f"Généré le {TODAY.isoformat()} — fenêtre de renouvellement {lo.isoformat()} → {hi_disp}")
    A("")
    A(f"**{total}** marchés expirent dans cette fenêtre (estimation "
      f"dateNotification + dureeMois).")
    A("")
    A(f"- accords-cadres : {ac_n} ({ac_n/total:.0%}) — montants = maxima")
    A(f"- marchés avec titulaire(s) non identifié(s) : {anon_n} ({anon_n/total:.0%})")
    A("")
    A(HOWTO)
    A("")
    A("## Vue d'ensemble")
    A("")
    A("| Mois d'échéance | Nombre |")
    A("|---|---|")
    for p in months:
        n = int((sel["date_expiration"].dt.to_period("M") == p).sum())
        A(f"| {p} | {n} |")
    A("")
    if not top_buyers.empty:
        A("## Principaux acheteurs (par nombre de contrats arrivant à échéance)")
        A("")
        A("Les échéances d'un même accord-cadre sont comptées une fois ; le montant "
          "est un montant maximum / estimé, pas une dépense.")
        A("")
        A("| Acheteur | Contrats | Montant max/estimé le plus élevé (€) |")
        A("|---|---|---|")
        for buyer, row in top_buyers.head(15).iterrows():
            A(f"| {buyer} | {int(row['n_marches'])} | "
              f"{row['montant_max']:,.0f} |".replace(",", " "))
        A("")
    A("## Détail par mois d'échéance")
    A("")
    for p in months:
        sub = sel[sel["date_expiration"].dt.to_period("M") == p]
        A(f"### {p} ({len(sub)})")
        A("")
        A("| Échéance | Acheteur | Objet | CPV | Nature | Durée (mois) | Montant | Label | Titulaire(s) |")
        A("|---|---|---|---|---|---|---|---|---|")
        for _, r in sub.iterrows():
            obj = str(r["objet"] or "")[:110]
            A(f"| {r['date_expiration'].date()} | {r['acheteur']} | {obj} | "
              f"{r['cpv_div']} | {r['nature']} | {int(r['duree_mois'])} | "
              f"{fmt_amount(r['montant'], r['montant_label'])} | "
              f"{r['montant_label']} | {r['titulaire']} |")
        A("")
    files_used = sorted(p.name for p in CONSOL.glob("decp-*.json"))
    A("## Limites et source")
    A("")
    A(FOOTER)
    A("")
    A(f"*Source : DECP consolidées data.gouv ({', '.join(files_used)}), "
      f"{len(df):,} marchés exploitables ; noms SIRET via recherche-entreprises "
      f"(cache partagé decp-analytics)."
      .replace(",", " "))
    A("")
    A(f"*Préparé par — {SENDER}*")
    md = "\n".join(L)
    return md, total


def write_html(slug, title, md):
    import markdown  # type: ignore
    body = markdown.markdown(md, extensions=["tables"])
    page = f"""<!doctype html><html lang=fr><meta charset=utf-8>
<title>{html.escape(title)} — Watchlist DECP</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1200px;margin:2rem auto;
padding:0 1rem;color:#1a1a2e;line-height:1.45}} table{{border-collapse:collapse;
font-size:0.82em;width:100%}} td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}
th{{background:#eef}} h3{{margin-top:1.6rem}} footer{{font-size:0.85em;color:#666}}</style>
<body>{body}</body></html>"""
    (REPORT / f"{slug}.html").write_text(page)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", default=str(BASE / "data" / "prospects.json"))
    args = ap.parse_args()
    profs = json.loads(Path(args.profiles).read_text())["prospects"]
    df = pd.read_parquet(CONSOL / "expiries.parquet")

    # Resolve names ONLY for SIRETs actually displayed in a report (the filtered
    # 6-12m window), not the whole CPV scope — keeps the recherche-entreprises
    # enrichment bounded and fast. Missing names stay "SIRET <id>" (valid, flagged),
    # so the no-anonymized-as-name invariant holds regardless of cache coverage.
    lo, hi = er.month_add(TODAY, 6), er.month_add(TODAY, 12)
    hi_end = pd.Timestamp(er.month_add(TODAY, 12)) - pd.Timedelta(days=1)
    win = df[(df["date_expiration"] >= pd.Timestamp(lo)) &
             (df["date_expiration"] <= hi_end)]
    all_sirets = set()
    for p in profs:
        inscope = win[win["cpv"].str[:2].isin(p["cpv"])]
        if p.get("depts"):
            inscope = inscope[inscope["dept"].isin(p["depts"])]
        all_sirets.update(inscope["acheteur_id"].dropna().astype(str))
        for ts in inscope["titulaires_siret"].dropna():
            all_sirets.update(str(ts).split("; "))
    names = er.load_names(all_sirets)

    REPORT.mkdir(parents=True, exist_ok=True)
    for p in profs:
        md, total = build_profile(p, df, names)
        if md is None:
            print(f"[{p['slug']}] rien dans la fenêtre", file=sys.stderr)
            continue
        (REPORT / f"{p['slug']}.md").write_text(md)
        write_html(p["slug"], p["title"], md)
        print(f"[{p['slug']}] {total} marchés -> "
              f"report/watchlist/{p['slug']}.{{md,html}}")


if __name__ == "__main__":
    main()