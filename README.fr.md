# decp-digest

Digest hebdomadaire des nouveaux avis d'attribution de la commande publique
francaise (DECP) — recuperation, dedoublonnage, resume, envoi par email.
Python stdlib uniquement.

## Source de donnees (validee 2026-09-28)

Jeu de donnees : [API DECP](https://www.data.gouv.fr/datasets/api-decp),
fichiers delta quotidiens `decp-DDMMYYYY-NNN-0130.json` publies par la
DAF / economie.gouv.fr.

- **Cadence** : plusieurs fichiers par jour (l'index `NNN` numerote les
  fichiers du jour), au moins un par jour. `fetch.py` garde un watermark sur
  le dernier titre traite et ne telecharge que les fichiers plus recents.
- **Volume** : ~750 nouveaux avis par semaine (729 enregistrements sur la
  semaine de validation complete 2026-09-21→27). ~3% d'enregistrements se
  repetent entre fichiers quotidiens consecutifs ; dedoublonnage sur
  `(acheteur.id, id)`.
- **Qualite des champs** : chaque champ utile du digest (objet, CPV, montant,
  duree, date de notification, departement, titulaires) rempli a 99,7% ou
  mieux.
- **Sans noms** : `acheteur` et `titulaire` ne portent que des SIRET ; les
  noms sont resous au mieux via recherche-entreprises.api.gouv.fr.
- **Retard** : la publication suit la notification avec une mediane de
  6 jours — le digest groupe donc par date de publication des donnees.

## Usage

    ./run.sh                # fetch des deltas + digest + email
    python3 fetch.py        # fetch des deltas quotidiens uniquement (watermark)
    python3 digest.py [--sector 48,72] [--region Île-de-France] [--outdir data/out]
    python3 validate.py [--fetch]   # rapport volume / qualite / dedoublonnage

## Pipeline (digest.py)

1. Charge les fichiers brutes, dedoublonne sur `(acheteur.id, id)`, garde les
   attributions notifiees dans les 7 derniers jours moins tout ce qu'un run
   precedent a deja digere (etat persistant dans `data/seen.json`).
2. Passe LLM optionnelle (`--llm`) : les 15 plus grosses attributions partent
   en resume via l'API OpenRouter, avec repli deterministe sur tout echec.
3. Sortie : `data/out/digest-YYYY-MM-DD.{md,html}`, organisee par secteur
   (division CPV), region et departement, avec une table des gagnants.

## Prevision d'echeance (produit pivot)

A partir des [fichiers consolides DECP](https://www.data.gouv.fr/datasets/donnees-essentielles-de-la-commande-publique-fichiers-consolides)
(2019-2026, 1,36 M de marchés) : echeance = `dateNotification + dureeMois`.

    python3 expiry_build.py    # consolides -> expiries.parquet/csv + build_stats.json
    python3 expiry_report.py   # rapport 6-12 mois + fenetre complete
    python3 -m pytest test_expiry.py -m slow

La couche dashboard/analyse vit dans le depot compagnon `decp-analytics`.
Version francaise du companion README : voir `decp-analytics/README.fr.md`.
English version: [README.md](README.md).
