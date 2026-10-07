#!/usr/bin/env python3
"""Weekly DECP digest pipeline: fetch (fetch.py) -> dedupe -> LLM summarize -> MD+HTML.

Dedupe is persistent: data/seen.json records (acheteur.id, id) of every notice
already digested in a previous run, so re-running never repeats a notice.
Notable awards are summarized by an LLM via OpenRouter (ZDR-only models,
small batches, cheap model) when --llm is passed; otherwise the digest is
deterministic. Output: data/out/digest-YYYY-MM-DD.{md,html}.

Filters for personalized digests:
  --sector 48,72     CPV divisions to keep
  --region Île-de-France   département -> région filter
Stdlib only (plus stdlib urllib for the LLM call).
"""
import json
import argparse
import html
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from regions import region_of

BASE = Path(__file__).resolve().parent
RAW = BASE / "data" / "raw"
OUT = BASE / "data" / "out"
SEEN = BASE / "data" / "seen.json"

# ZDR-only routing: this model is on the ZDR endpoint list
# (https://openrouter.ai/api/v1/endpoints/zdr). Never switch to a
# non-ZDR provider. $0.045/M prompt tokens.
LLM_MODEL = os.environ.get("DIGEST_LLM_MODEL", "z-ai/glm-5.3-flash")
LLM_URL = "https://openrouter.ai/api/v1/chat/completions"

CPV_DIV = {
    "03": "Produits agricoles", "09": "Carburants", "14": "Mines/minéraux",
    "15": "Alimentation", "16": "Vêtements/cuir", "18": "Papier/impression",
    "19": "Produits chimiques", "22": "Textiles", "24": "Produits divers",
    "30": "Informatique (matériel)", "31": "Électronique/optique",
    "32": "Équipements électriques", "33": "Équipements médicaux/précision",
    "34": "Transports (matériel)", "35": "Sécurité/défense",
    "37": "Sport/loisirs", "38": "Instruments de mesure",
    "39": "Mobilier", "41": "Eau", "42": "Travaux (génie civil)",
    "43": "Bâtiment (travaux)", "44": "Matériaux construction",
    "45": "Travaux de construction", "48": "Logiciels/services IT",
    "50": "Réparation/maintenance", "51": "Installation",
    "55": "Hôtellerie/restauration", "60": "Services de transport",
    "63": "Services logistiques", "64": "Services postaux",
    "66": "Services financiers", "71": "Services d'ingénierie/études",
    "72": "Services IT (conseil, dev)", "73": "R&D",
    "75": "Services publics/administratifs", "79": "Services aux entreprises",
    "80": "Enseignement/formation", "85": "Santé/social",
    "90": "Environnement/propreté", "92": "Services juridiques",
    "98": "Autres services",
}

DEPT = {"75": "Paris", "69": "Rhône", "13": "Bouches-du-Rhône", "31": "Haute-Garonne",
        "33": "Gironde", "44": "Loire-Atlantique", "59": "Nord", "67": "Bas-Rhin",
        "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis",
        "94": "Val-de-Marne", "78": "Yvelines", "77": "Seine-et-Marne"}


def load_all():
    marches = {}
    for f in sorted(RAW.glob("decp-*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception as e:
            print(f"WARN: skip {f.name}: {e}", file=sys.stderr)
            continue
        for m in d.get("marches", {}).get("marche", []):
            mid = m.get("id")
            if mid:
                marches[mid] = m  # later files win
    return marches


def dedupe_key(m):
    return f"{(m.get('acheteur') or {}).get('id', '?')}|{m.get('id', '?')}"


def load_seen():
    if SEEN.exists():
        return set(json.loads(SEEN.read_text()).get("seen", []))
    return set()


def save_seen(seen):
    # cap the file at the last 50k keys
    keys = sorted(seen)[-50000:]
    SEEN.write_text(json.dumps({"seen": keys}, ensure_ascii=False))


def fmt_montant(v):
    if v is None:
        return "n/d"
    if v >= 1e6:
        return f"{v/1e6:.1f} M€"
    return f"{v/1000:.0f} k€"


def cpv_family(code):
    return CPV_DIV.get((code or "")[:2], (code or "")[:2] + "?")


def enrich_siret(siret_cache, siret):
    """Return (nom, ville) for a SIRET via recherche-entreprises (best effort)."""
    if siret in siret_cache:
        return siret_cache[siret]
    info = ("", "")
    try:
        url = f"https://recherche-entreprises.api.gouv.fr/search?q={siret}&mtm_campaign=api"
        req = urllib.request.Request(url, headers={"User-Agent": "decp-digest/0.1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        res = d.get("results") or []
        if res:
            e = res[0]
            nom = e.get("nom_complet") or e.get("denomination", "")
            addr = (e.get("siege") or {}).get("adresse") or ""
            info = (nom, addr)
    except Exception:
        pass
    siret_cache[siret] = info
    return info


# ---------------- LLM summarize ----------------

def llm_batch(notices, api_key):
    """One batch of <=12 notices -> dict {dedupe_key: summary_bullet} via OpenRouter."""
    lines = []
    for m in notices:
        dep = (m.get("lieuExecution") or {}).get("code", "?")
        dep_name = DEPT.get(dep, dep)
        lines.append(
            f"- cle: {dedupe_key(m)} | montant: {fmt_montant(m.get('montant'))} | "
            f"CPV: {cpv_family(m.get('codeCPV'))} ({m.get('codeCPV', '?')}) | "
            f"lieu: {dep_name} | notifie: {m.get('dateNotification', '?')} | "
            f"duree: {m.get('dureeMois', '?')} mois | objet: {m.get('objet', 'n/d')[:200]}"
        )
    prompt = (
        "Voici des avis d'attribution de marchés publics français récents. "
        "Pour chaque avis, écris une ligne résumant ce qui a été acheté, pour qui "
        "(dépôt) et pourquoi c'est notable (montant, secteur, zone). Style: bullet "
        "concis en français, pas de flatterie, pas de conclusion générale. "
        "Format de sortie OBLIGATOIRE: une ligne par avis, exactement "
        "``cle:::resume`` où cle est la cle donnée.\n\n" + "\n".join(lines)
    )
    req = urllib.request.Request(
        LLM_URL,
        data=json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 6000,
            "temperature": 0.3,
        }).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/kz/decp-digest",
            "X-Title": "decp-digest",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    msg = d["choices"][0]["message"]
    text = msg.get("content") or msg.get("reasoning") or ""
    out = {}
    for line in text.splitlines():
        if ":::" in line:
            k, _, s = line.partition(":::")
            k = k.strip().strip("`*- ")
            out[k] = s.strip()
    return out


def summarize_notable(notable, api_key, max_calls=3):
    """Batched LLM summaries; returns {dedupe_key: bullet}, best-effort."""
    if not api_key:
        print("WARN: no OPENROUTER_API_KEY, skipping LLM summaries", file=sys.stderr)
        return {}
    summaries = {}
    for i in range(0, len(notable), 12):
        if i // 12 >= max_calls:
            break
        batch = notable[i:i + 12]
        try:
            got = llm_batch(batch, api_key)
            summaries.update(got)
            print(f"llm batch {i//12 + 1}: {len(got)}/{len(batch)} summarized")
        except Exception as e:
            print(f"WARN: LLM batch {i//12 + 1} failed: {e}", file=sys.stderr)
    return summaries


def deterministic_bullet(m):
    dep = (m.get("lieuExecution") or {}).get("code", "?")
    return (f"{fmt_montant(m.get('montant'))} — {m.get('objet', 'n/d')[:140]} "
            f"[{cpv_family(m.get('codeCPV'))}, {DEPT.get(dep, dep)}, "
            f"notifié {m.get('dateNotification', '?')}]")


# ---------------- main ----------------

def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--sector", help="comma-separated CPV divs to keep (e.g. 48,72,30)")
    ap.add_argument("--region", help="région filter (e.g. Île-de-France, PACA)")
    ap.add_argument("--outdir", help="alternate output dir (relative to repo)")
    ap.add_argument("--title", default="Digest DECP")
    ap.add_argument("--llm", action="store_true", help="summarize notable awards via OpenRouter")
    ap.add_argument("--max-llm-calls", type=int, default=3, help="cap LLM batches (cost discipline)")
    ap.add_argument("--no-state", action="store_true",
                    help="ignore seen.json for this run (still updates it)")
    a = ap.parse_args()
    keep = set(a.sector.split(",")) if a.sector else None
    outdir = (BASE / a.outdir) if a.outdir else OUT
    outdir.mkdir(parents=True, exist_ok=True)
    OUT = outdir

    marches = load_all()
    week_start = date.today() - timedelta(days=7)

    seen = load_seen()
    base_seen = set() if a.no_state else seen

    def in_week(m):
        ds = m.get("dateNotification") or ""
        try:
            return datetime.strptime(ds, "%Y-%m-%d").date() >= week_start
        except ValueError:
            return False

    awards = []
    for m in marches.values():
        if not in_week(m) or dedupe_key(m) in base_seen:
            continue
        if keep and (m.get("codeCPV") or "")[:2] not in keep:
            continue
        if a.region and region_of((m.get("lieuExecution") or {}).get("code", "")) != a.region:
            continue
        awards.append(m)
    awards.sort(key=lambda m: (m.get("dateNotification") or "", m.get("montant") or 0),
                reverse=True)

    today = date.today().isoformat()
    total = len(awards)
    known = sum(1 for m in awards if m.get("montant"))
    volume = sum(m.get("montant") or 0 for m in awards)

    if total == 0:
        print("no new notices since last run — digest unchanged")
        return 0

    by_cpv = Counter()
    vol_cpv = defaultdict(int)
    by_dept = Counter()
    by_acheteur = Counter()
    by_region = Counter()
    for m in awards:
        fam = cpv_family(m.get("codeCPV"))
        by_cpv[fam] += 1
        vol_cpv[fam] += m.get("montant") or 0
        dep = (m.get("lieuExecution") or {}).get("code", "?")
        by_dept[dep] += 1
        by_region[region_of(dep) or "?"] += 1
        by_acheteur[(m.get("acheteur") or {}).get("id", "?")] += 1

    # notable = largest 15 by montant
    notable = [m for m in awards if m.get("montant")][:15]
    notable.sort(key=lambda m: m["montant"], reverse=True)

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    summaries = summarize_notable(notable, api_key, a.max_llm_calls) if a.llm else {}

    siret_cache = {}
    winners = Counter()
    for m in notable:
        for t in (m.get("titulaires") or []):
            s = (t.get("titulaire") or {}).get("id")
            if s:
                winners[enrich_siret(siret_cache, s)[0] or s] += 1

    scope = a.title
    if a.region:
        scope += f" — région {a.region}"
    if a.sector:
        scope += f" — CPV {a.sector}"

    # ---- Markdown ----
    L = [f"# {scope} — semaine du {week_start.isoformat()} au {today}\n"]
    L.append(f"**{total} nouveaux marchés attribués** (déjà digérés exclus) · "
             f"montant cumulé connu : **{fmt_montant(volume)}** "
             f"({known}/{total} avec montant publié)\n")
    L.append("## Répartition par secteur\n")
    L.append("| Secteur | Nb | Montant |")
    L.append("|---|---:|---:|")
    for fam, c in by_cpv.most_common(10):
        L.append(f"| {fam} | {c} | {fmt_montant(vol_cpv[fam])} |")
    L.append("\n## Par région (top 8)\n")
    L.append(", ".join(f"{r} ({c})" for r, c in by_region.most_common(8)))
    L.append("\n## Par département (top 10)\n")
    L.append(", ".join(f"{DEPT.get(d, d)} ({c})" for d, c in by_dept.most_common(10)))
    L.append("\n## Marchés notables (LLM)\n" if summaries else
             "\n## Plus gros marchés notifiés cette semaine\n")
    for m in notable:
        k = dedupe_key(m)
        if k in summaries:
            L.append(f"- {summaries[k]}")
        else:
            deps = (m.get("lieuExecution") or {}).get("code", "?")
            wl = [enrich_siret(siret_cache, (t.get("titulaire") or {}).get("id", ""))[0]
                  or (t.get("titulaire") or {}).get("id", "n/d")
                  for t in (m.get("titulaires") or [])[:3]]
            L.append(f"- **{fmt_montant(m['montant'])}** — {m.get('objet', 'n/d')[:140]} "
                     f"[CPV {m.get('codeCPV','?')}, dép. {DEPT.get(deps, deps)}, "
                     f"notifié {m.get('dateNotification','?')}] "
                     f"→ {', '.join(wl) or 'n/d'}")
    top_win = ", ".join(f"{w} ({c})" for w, c in winners.most_common(8))
    L.append(f"\n## Gagnants récurrents (top 15 marchés)\n{top_win or 'n/d'}\n")
    L.append("---\nSource : data.gouv.fr, dataset API DECP (deltas quotidiens). "
             "Montants = totaux estimés publiés par l'acheteur. "
             + ("Résumés LLM par " + LLM_MODEL + " via OpenRouter (ZDR)."
                if summaries else "Généré sans LLM."))
    md = "\n".join(L)
    (OUT / f"digest-{today}.md").write_text(md)
    # ---- HTML ----
    def esc(s):
        return html.escape(str(s or ""))

    H = [f"""<!doctype html><html lang=fr><meta charset=utf-8>
<title>{esc(scope)} {today}</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:820px;margin:2em auto;padding:0 1em;color:#1a1a2e}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:.92em}}
h1{{color:#0b3d91}} .meta{{color:#555;font-size:.9em}} .sum{{color:#333;font-size:.95em}}</style>
<h1>{esc(scope)} — semaine du {week_start.isoformat()} au {today}</h1>
<p><b>{total} nouveaux marchés attribués</b> · montant cumulé connu {esc(fmt_montant(volume))} ({known}/{total})</p>
<h2>Par secteur</h2><table><tr><th>Secteur</th><th>Nb</th><th>Montant</th></tr>"""]
    for fam, c in by_cpv.most_common(10):
        H.append(f"<tr><td>{esc(fam)}</td><td>{c}</td><td>{esc(fmt_montant(vol_cpv[fam]))}</td></tr>")
    H.append(f"</table><h2>Par région</h2><p>{esc(', '.join(f'{r} ({c})' for r, c in by_region.most_common(8)))}</p>")
    if not summaries:
        H.append("<h2>Plus gros marchés de la semaine</h2><table>"
                 "<tr><th>Montant</th><th>Objet</th><th>CPV</th><th>Dép.</th>"
                 "<th>Attributaire</th></tr>")
    H.append("<h2>Marchés notables</h2>" if summaries else "")
    for m in notable:
        k = dedupe_key(m)
        if k in summaries:
            H.append(f"<p class=sum>• {esc(summaries[k])}</p>")
        else:
            deps = (m.get("lieuExecution") or {}).get("code", "?")
            wl = [enrich_siret(siret_cache, (t.get("titulaire") or {}).get("id", ""))[0]
                  or (t.get("titulaire") or {}).get("id", "n/d")
                  for t in (m.get("titulaires") or [])[:2]]
            H.append(f"<tr><td>{esc(fmt_montant(m.get('montant')))}</td>"
                     f"<td>{esc((m.get('objet') or 'n/d')[:130])}</td>"
                     f"<td>{esc(m.get('codeCPV','?'))}</td><td>{esc(DEPT.get(deps, deps))}</td>"
                     f"<td>{esc(', '.join(wl) or 'n/d')}</td>")
    if not summaries:
        H.append("</table>")
    H.append(f"<p class=meta>Source : data.gouv.fr / API DECP. "
             f"Régions : {esc(', '.join(f'{r} ({c})' for r, c in by_region.most_common(8)))}</p></html>")
    (OUT / f"digest-{today}.html").write_text("\n".join(H))

    # update persistent dedupe state with everything loaded this run
    seen.update(dedupe_key(m) for m in marches.values())
    save_seen(seen)

    print(f"digest written: {total} nouveaux marchés, {fmt_montant(volume)}")
    print(f"  -> {OUT / f'digest-{today}.md'}")
    print(f"  -> {OUT / f'digest-{today}.html'}")
    if a.region or a.sector:
        print(f"  (scope: sector={a.sector} region={a.region}; "
              f"seen.json includes ALL loaded notices so filtered runs still dedupe globally)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
