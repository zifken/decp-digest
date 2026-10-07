# decp-digest

Weekly digest of new French public-procurement award notices (DECP) —
fetch, dedupe, summarize, deliver by email. Stdlib Python only.

## Data source (validated 2026-09-28)

Dataset: [API DECP](https://www.data.gouv.fr/datasets/api-decp)
(`5df410e86f44413a91d34be3`), daily delta files
`decp-DDMMYYYY-NNN-0130.json` published by DAF/economie.gouv.fr.

- **Cadence:** several files per day (numbered `NNN` = file index for the
  day), reliably at least one per day. `fetch.py` watermarks on the last
  processed title and only downloads newer files, so re-running is cheap.
- **Format:** `{"marches": {"marche": [record, ...]}}` — note the nested
  list under the `"marche"` key (schema quirk worth remembering).
- **Volume:** ~100 records/day measured over a full week (729 records in
  7 daily files, week of 2026-09-21→27) ≈ **700–750 new notices/week**.
  Daily spread 3–186 (weekday-heavy, last day of a weekend partial).
- **Duplicates:** ~3% of records repeat across consecutive daily files
  (corrections / late pushes). Dedupe key: `(acheteur.id, id)` — works,
  21 dupes over the sample week.

### Field quality (729 records, week of 21–27 Sep 2026)

| field | fill |
|---|---|
| objet (title) | 100% |
| codeCPV (sector) | 99.7% |
| montant (amount) | 99.7% |
| dureeMois (duration) | 100% |
| dateNotification | 99.7% |
| lieuExecution.code (département) | 99.7% |
| titulaires (supplier SIRET) | 99.7% |

Good enough to drive the digest. Caveats:

- **No names.** `acheteur` and `titulaire` carry only SIRET ids — buyer
  and supplier display names need a SIRENE/sirene.fr join (nice-to-have
  enrichment, not required for the digest).
- **Geography** is département code via `lieuExecution` (map to region
  with a static code table).
- **Lag:** publication follows notification by a median of 6 days
  (p10 = 0, p90 = 208 — some old markets get republished late), so a
  weekly digest grouped by `datePublicationDonnees` captures genuinely
  new notices; grouping by notification date would miss ~1 week.

Top CPV divisions in the sample: 45 (construction), 71 (engineering),
50 (vehicle services), 79 (business services), 33 (medical/IT equipment).

### Verdict

The daily delta files support the weekly digest as-is: one cron fetch,
~750 records/week, dedupe by (acheteur, id), all digest fields ≥99.7%
filled. `validate.py` re-derives everything above.

## Contract-expiry forecast (pivot 2026-09-28)

New core product: contracts about to expire (re-tender windows), from the
[consolidated DECP files](https://www.data.gouv.fr/datasets/donnees-essentielles-de-la-commande-publique-fichiers-consolides)
(open data). Expiry = `dateNotification + dureeMois`; an expiry is a
re-tender-window signal, not a guaranteed end (no extension field in DECP).

    .venv/bin/python expiry_build.py    # data/consol/decp-*.json -> expiries.parquet/csv + build_stats.json
    .venv/bin/python expiry_report.py   # IT (CPV 48+72) 6-12m report -> report/expiry_report_it.{md,html}
                                        #   + data/consol/expiries_upcoming.{parquet,csv} (6-12m, all sectors)
    .venv/bin/python enrich_cache.py    # fill shared SIRET->name cache (slow, rate-limited 429s)
    .venv/bin/python -m pytest test_expiry.py        # unit tests
    .venv/bin/python -m pytest test_expiry.py -m slow  # + built-dataset invariants

Filters: `expiry_report.py --cpv 48 72 --dept 75 --acheteur SIRET`.
Accords-cadres (40.8% of rows) are labelled; their `montant` is the maximum
of the framework, never presented as spend. Anonymized/malformed titulaire
ids are flagged, never shown as names. Data-quality summary:
`report/data_quality.md`. Sample IT report: `report/expiry_report_it.md`.

## Usage

    ./run.sh                # fetch new deltas + build digest + email
    python3 fetch.py        # fetch daily deltas only (watermarked)
    python3 digest.py [--cpv 45] [--outdir data/out]
    python3 validate.py [--fetch]   # volume / field-quality / dedupe report

## Pipeline (digest.py)

`fetch.py` (watermarked daily deltas -> data/raw/) then `digest.py`:

1. Load all raw files, dedupe by `(acheteur.id, id)`, keep awards notified
   in the last 7 days **minus everything already digested in a previous
   run** (persistent state in `data/seen.json`, capped at 50k keys).
   A run with nothing new writes nothing and leaves the existing digest.
2. Optional LLM pass (`--llm`): the 15 largest awards go to OpenRouter in
   batches of 12 (max `--max-llm-calls` calls), model `z-ai/glm-5.3-flash`
   — on the ZDR-only endpoint list, $0.045/M prompt tokens (~1-2 cents per
   weekly digest). Key read from `OPENROUTER_API_KEY` (hermes .env). Any
   batch/key that fails falls back to deterministic bullets.
3. Output: `data/out/digest-YYYY-MM-DD.{md,html}`, organized by sector
   (CPV division), région and département, with a winners table
   (SIRET enriched via recherche-entreprises.api.gouv.fr, best effort).

Filters for personalized digests:
  `--sector 48,72` (CPV divisions) · `--region Île-de-France` (dept->région
  table in regions.py) · `--no-state` to ignore the seen-state for a run
  (e.g. a filtered sample digest on already-digested data; the state still
  gets updated). `--outdir` / `--title` for alternate scope runs.

Tested end-to-end 2026-09-28 on real data: 263 new awards / 283.6 M€ with
15/15 LLM summaries; immediate second run -> "no new notices".
