#!/usr/bin/env python3
"""Validation for the expiry forecast pipeline (stdlib only; run with pytest).

Covers requirement 6: date parsing, duration sanity, dedupe by
(acheteur.id, id) with newest-consolidation-wins, accord-cadre labelling,
anonymized titulaire flagging.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import expiry_build as eb

BASE = Path(__file__).parent
CONSOL = BASE / "data" / "consol"


# ---------- unit: helpers ----------

def test_parse_date_iso_and_garbage():
    assert eb.parse_date("2025-09-22") == date(2025, 9, 22)
    assert eb.parse_date("2025-09-22T00:00:00") == date(2025, 9, 22)
    assert eb.parse_date("") is None
    assert eb.parse_date(None) is None
    assert eb.parse_date("31/12/2025") is None  # not ISO -> dropped upstream


def test_duration_sanity_rules():
    # missing / zero / negative / absurd are all rejected
    m = {"dateNotification": "2025-01-15", "dureeMois": 0,
         "acheteur": {"id": "1"}, "id": "x"}
    assert eb.norm(m) == "dropped_noduree"
    m["dureeMois"] = None
    assert eb.norm(m) == "dropped_noduree"
    m["dureeMois"] = -5
    assert eb.norm(m) == "dropped_noduree"
    m["dureeMois"] = 361
    assert eb.norm(m) == "dropped_absurd"
    m["dureeMois"] = 360
    assert isinstance(eb.norm(m), dict)  # 30y is the accepted ceiling
    m["dateNotification"] = None
    assert eb.norm(m) == "dropped_nodate"


def test_anonymized_titulaires():
    assert eb.is_anonymized("00001") is True
    assert eb.is_anonymized("1") is True
    assert eb.is_anonymized("39103354500040") is False
    assert eb.is_anonymized(None) is True
    assert eb.is_anonymized("3910335450004x") is True  # malformed -> flagged
    m = {"dateNotification": "2025-01-15", "dureeMois": 6,
         "acheteur": {"id": "1"}, "id": "x",
         "titulaires": [{"titulaire": {"typeIdentifiant": "SIRET",
                                       "id": "00001"}}]}
    r = eb.norm(m)
    assert r["anonymized"] is True
    assert r["titulaires_siret"] is None  # never displayed as a name


def test_accord_cadre_labelling():
    ac = {"dateNotification": "2025-01-15", "dureeMois": 12,
          "acheteur": {"id": "1"}, "id": "x",
          "techniques": {"technique": ["Accord-cadre"]}, "montant": 1000}
    r = eb.norm(ac)
    assert r["ac"] is True
    # a marche subséquent run on an AC is part of the same family
    ms = {"dateNotification": "2025-01-15", "dureeMois": 12,
          "acheteur": {"id": "1"}, "id": "y",
          "modalitesExecution": {"modaliteExecution": ["Sans objet"]},
          "objet": "Marché subséquent n°2 passé sur l'accord-cadre TR-2024"}
    assert eb.norm(ms)["ac"] is True
    plain = {"dateNotification": "2025-01-15", "dureeMois": 12,
             "acheteur": {"id": "1"}, "id": "z",
             "techniques": {"technique": ["Sans objet"]}}
    assert eb.norm(plain)["ac"] is False


def test_dept_normalization():
    assert eb.norm_dept("44700") == "44"       # postal code
    assert eb.norm_dept("971") == "971"        # Outre-mer
    assert eb.norm_dept("97425") == "974"
    assert eb.norm_dept("") is None
    assert eb.norm_dept("GF") is None


# ---------- integration: build output ----------

@pytest.mark.slow
def test_built_dataset_invariants():
    pq = CONSOL / "expiries.parquet"
    if not pq.exists():
        pytest.skip("expiries.parquet not built")
    df = pd.read_parquet(pq)
    # dedupe invariant: no (acheteur_id, id) twice
    assert df.duplicated(subset=["acheteur_id", "id"]).sum() == 0
    # duration sanity invariant
    assert (df["duree_mois"] > 0).all() and (df["duree_mois"] <= 360).all()
    # dates parse and expiry >= notification + at least 1 month
    assert df["date_expiration"].notna().all()
    assert (df["date_expiration"] > df["date_notification"]).all()
    # AC labelling is boolean and present
    assert df["ac"].dtype == bool
    # displayed titulaires are ALWAYS valid 14-digit SIRETs — anonymized,
    # VAT-numbered or malformed ids are filtered out of the display column
    # (the `anonymized` flag marks rows where at least one winner — or all —
    # is not identifiable)
    disp = df["titulaires_siret"].dropna()
    bad = disp[disp.str.split("; ").apply(
        lambda ts: any(not (t.isdigit() and len(t) == 14) for t in ts))]
    assert bad.empty, bad.head()


@pytest.mark.slow
def test_build_stats_shape():
    st = json.loads((CONSOL / "build_stats.json").read_text())
    for k in ("rows", "superseded", "dropped_absurd", "ac_share",
              "anonymized_share", "volume_per_notification_year"):
        assert k in st
    assert st["rows"] > 500_000


@pytest.mark.slow
def test_build_contains_2023_notifications():
    """The decp-2019.json file carries 2019-2023 notifications. If it is
    dropped from the build, 2023 collapses back to the 2024+ files' revisions
    (~57k) instead of the full ~120k+ published in 2023 — exactly the
    4-year accords-cadres that expire inside the 6-12 month window."""
    st = json.loads((CONSOL / "build_stats.json").read_text())
    v23 = st["volume_per_notification_year"].get("2023", 0)
    assert v23 > 70_000, f"2023 notifications too thin: {v23} (decp-2019.json missing?)"


def test_rank_buyers_counts_not_amounts():
    """Review fix: top buyers ranked by NUMBER of expiring contracts, with
    accord-cadre runs deduped by (acheteur, idAccordCadre or id). A single
    big framework ceiling must not top the list on amount."""
    import expiry_report as er
    sel = pd.DataFrame([
        # one buyer, one framework, 3 runs sharing idAccordCadre, huge ceiling
        {"acheteur": "A", "id": "r1", "id_ac": "AC9", "montant": 300e6},
        {"acheteur": "A", "id": "r2", "id_ac": "AC9", "montant": 10e6},
        {"acheteur": "A", "id": "r3", "id_ac": "AC9", "montant": 5e6},
        # another buyer, many separate small contracts
        *[{"acheteur": "B", "id": f"s{i}", "id_ac": None, "montant": 1000.0}
          for i in range(4)],
    ])
    ranked = er.rank_buyers(sel)
    # B first: 4 distinct contracts vs A's deduped 1 framework
    assert ranked.index[0] == "B"
    assert ranked.loc["B", "n_marches"] == 4
    assert ranked.loc["A", "n_marches"] == 1
    # A's amount shown is the framework MAXIMUM, not the sum
    assert ranked.loc["A", "montant_max"] == 300e6


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-m", "not slow"] +
                         (["-m", ""] if "--all" in sys.argv else [])))
