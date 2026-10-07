# Data quality — DECP contract-expiry forecast (build of 2026-09-28)

## Pipeline

- Input: consolidated DECP files from data.gouv.fr
  (dataset "Données essentielles de la commande publique - fichiers consolidés",
  open data, no scraping): decp-2019.json (holds 2019-2023 notifications,
  older flat schema), decp-2022.json, decp-2024.json, decp-2025.json,
  decp-2026.json (latest revision of each as of 2026-09-28).
- Builder: `expiry_build.py` (streams with ijson, ~1 min per GB).
- Output: `data/consol/expiries.parquet` + `.csv`, 1 355 661 rows
  (643 863 before the 2026-09-28 evening rebuild that added decp-2019.json).

## Coverage per notification year (usable rows)

| Année | Marchés | Note |
|---|---|---|
| 2019 | 131 095 | filled by decp-2019.json (was 1 167) |
| 2020 | 144 373 | filled by decp-2019.json (was 4 060) |
| 2021 | 179 997 | filled by decp-2019.json (was 12 751) |
| 2022 | 187 979 | filled by decp-2019.json (was 24 752) |
| 2023 | 149 025 | filled by decp-2019.json (was 56 766); still below 2024 — part of the jump is real (DECP publication reform) |
| 2024 | 213 259 | full |
| 2025 | 218 012 | full |
| 2026 | 112 301 | year to date |

Pre-2019 noise (years like 1900/2020 typos, ~200 rows total) is kept but never
material in the 6-12 month forecast window.

## Handling rules (requirement 1)

| Règle | Comportement | Volume sur ce build |
|---|---|---|
| dureeMois absent / 0 / négatif | exclu du forecast | 0 (les fichiers consolidés 2024+ remplissent dureeMois à ~100 %) |
| dureeMois > 360 mois (30 ans) | exclu (absurde/concession) | 306 |
| dateNotification absent | exclu | 24 |
| doublons (acheteur.id, id) entre fichiers annuels | dédoublonné, **la plus récente consolidation gagne** (les fichiers annuels portent des révisions : 29 % des clés communes 2025/2026 diffèrent) | 220 190 clés échangées |

Share of records usable: 643 863 / 864 340 raw marches = 74.5 % (the rest is
almost exclusively cross-file duplicates — 220 190 earlier revisions of the
same contracts — not lost contracts; only 330 rows excluded outright).

## Flags

- **Accords-cadres: 40.8 %** of all rows (`ac=True`). Detection: techniques
  field = "Accord-cadre", or "accord-cadre"/"accord cadre"/"subséquent" in
  objet/nature/modalités. For these rows `montant` is the **maximum** of the
  framework — every output labels it "montant maximum / estimé", never spend.
- **Gagnants non identifiables: 1.45 %** (`anonymized=True`): anonymized ids
  ("00001"), VAT-number ids, or malformed SIRETs. They are never displayed as
  company names; valid co-titulaires still are.
- displayed titulaires are always valid 14-digit SIRETs (test-enforced).

## Known blind spots

1. **Tacit renewals / avenants are invisible.** DECP has no extension field;
   an expiry is a *re-tender window signal*, not a guaranteed end date. The
   annual files do carry revisions (a contract re-published with a new
   duration supersedes the old row — handled), but quiet prolongations
   published nowhere are a hard limit.
2. **Buyers who don't publish.** Publication compliance is partial for
   sub-threshold marches and some collectivités; the 2022 file in particular
   is thin (8.8k marches vs 213k in 2024).
3. **Sub-seuillage.** Marchés < 40 000 € HT are outside the DECP obligation
   (formalized 2024+); the small-contract tail is missing by design.
4. **Concessions and marches de partenariat** have different duration
   semantics (up to decades); dureeMois > 360 is dropped, so long
   infrastructure contracts are out of the forecast by design.
5. **Bons de commande / AC runs**: an expiring framework generates many rows
   (one per purchase run) with montant = maximum repeated per row; buyer-level
   aggregation must dedupe by (acheteur, idAccordCadre) before counting spend.
