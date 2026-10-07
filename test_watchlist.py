#!/usr/bin/env python3
"""Tests for watchlist.py (v1): profile filtering + AC dedupe + no-anonymized-names
invariant, on a synthetic frame so they run fast and offline."""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import expiry_report as er
import watchlist as wl

TODAY = date.today()


def _mkdf(rows):
    return pd.DataFrame(rows)


def _win_dates(n=6):
    """in-window month starts 6..12 months out."""
    return [pd.Timestamp(er.month_add(TODAY, n)) for n in (6, 8, 11)]


def test_profile_filtering_by_cpv_dept_buyer():
    d6, d8, d11 = _win_dates()
    df = _mkdf([
        {"acheteur_id": "10000000000001", "id": "a", "id_ac": None,
         "objet": "obj A", "cpv": "48000000-8", "cpv_div": "Services informatiques",
         "dept": "75", "nature": "Marché", "ac": False, "anonymized": False,
         "titulaires_siret": "20000000000002", "duree_mois": 12,
         "montant": 50000.0, "date_notification": d6 - pd.Timedelta(days=400),
         "date_expiration": d6},
        # same CPV but wrong dept -> filtered out
        {"acheteur_id": "30000000000003", "id": "b", "id_ac": None,
         "objet": "obj B", "cpv": "48000000-8", "cpv_div": "Services informatiques",
         "dept": "13", "nature": "Marché", "ac": False, "anonymized": False,
         "titulaires_siret": None, "duree_mois": 12, "montant": 999.0,
         "date_notification": d8 - pd.Timedelta(days=400), "date_expiration": d8},
        # different CPV family, right dept -> filtered out
        {"acheteur_id": "40000000000004", "id": "c", "id_ac": None,
         "objet": "obj C", "cpv": "45000000-7", "cpv_div": "Travaux", "dept": "75",
         "nature": "Marché", "ac": False, "anonymized": False,
         "titulaires_siret": None, "duree_mois": 12, "montant": 999.0,
         "date_notification": d11 - pd.Timedelta(days=400), "date_expiration": d11},
        # named buyer gate
        {"acheteur_id": "50000000000005", "id": "d", "id_ac": None,
         "objet": "obj D", "cpv": "48000000-8", "cpv_div": "Services informatiques",
         "dept": "75", "nature": "Marché", "ac": False, "anonymized": False,
         "titulaires_siret": None, "duree_mois": 12, "montant": 999.0,
         "date_notification": d6 - pd.Timedelta(days=400), "date_expiration": d6},
    ])
    prof = {"slug": "t", "title": "t", "cpv": ["48"], "depts": ["75"],
            "acheteurs": ["10000000000001"], "lang": "fr"}
    md, total = wl.build_profile(prof, df, {})
    assert md is not None
    assert total == 1  # only 'a' passes cpv+dept+buyer
    assert "obj A" in md and "obj B" not in md and "obj C" not in md and "obj D" not in md


def test_ac_runs_deduped_in_buyer_rank():
    d6, d8, _ = _win_dates()
    df = _mkdf([
        # one framework, 2 runs, huge ceiling, buyer A
        {"acheteur": "10000000000001", "acheteur_id": "10000000000001",
         "id": "r1", "id_ac": "AC1",
         "objet": "lot1", "cpv": "72000000-5", "cpv_div": "Services IT", "dept": "75",
         "nature": "Accord-cadre", "ac": True, "anonymized": False,
         "titulaires_siret": None, "duree_mois": 12, "montant": 10e6,
         "date_notification": d6 - pd.Timedelta(days=400), "date_expiration": d6},
        {"acheteur": "10000000000001", "acheteur_id": "10000000000001",
         "id": "r2", "id_ac": "AC1",
         "objet": "lot2", "cpv": "72000000-5", "cpv_div": "Services IT", "dept": "75",
         "nature": "Accord-cadre", "ac": True, "anonymized": False,
         "titulaires_siret": None, "duree_mois": 12, "montant": 2e6,
         "date_notification": d8 - pd.Timedelta(days=400), "date_expiration": d8},
        # buyer B: 2 separate small contracts
        {"acheteur": "20000000000002", "acheteur_id": "20000000000002",
         "id": "s1", "id_ac": None,
         "objet": "x", "cpv": "72000000-5", "cpv_div": "Services IT", "dept": "75",
         "nature": "Marché", "ac": False, "anonymized": False,
         "titulaires_siret": None, "duree_mois": 12, "montant": 1000.0,
         "date_notification": d6 - pd.Timedelta(days=400), "date_expiration": d6},
        {"acheteur": "20000000000002", "acheteur_id": "20000000000002",
         "id": "s2", "id_ac": None,
         "objet": "y", "cpv": "72000000-5", "cpv_div": "Services IT", "dept": "75",
         "nature": "Marché", "ac": False, "anonymized": False,
         "titulaires_siret": None, "duree_mois": 12, "montant": 500.0,
         "date_notification": d8 - pd.Timedelta(days=400), "date_expiration": d8},
    ])
    ranked = er.rank_buyers(df)
    # B first (2 distinct) vs A deduped to 1
    assert ranked.index[0] == "20000000000002"
    assert ranked.loc["10000000000001", "n_marches"] == 1
    # A's shown amount is the frame MAXIMUM, not the sum
    assert ranked.loc["10000000000001", "montant_max"] == 10e6


def test_no_anonymized_name_in_report(tmp_path):
    """Anonymized / malformed titulaires are NEVER rendered as a company name —
    the row shows \"(non identifié)\" instead of the SIRET-as-name."""
    d6, _, _ = _win_dates()
    df = _mkdf([
        {"acheteur_id": "10000000000001", "id": "a", "id_ac": None,
         "objet": "obj", "cpv": "72000000-5", "cpv_div": "Services IT", "dept": "75",
         "nature": "Marché", "ac": False, "anonymized": True,
         "titulaires_siret": "01234567890X",  # malformed id -> never rendered as a name
         "duree_mois": 12, "montant": 50000.0,
         "date_notification": d6 - pd.Timedelta(days=400), "date_expiration": d6},
    ])
    prof = {"slug": "t", "title": "t", "cpv": ["72"], "depts": ["75"],
            "acheteurs": None, "lang": "fr"}
    md, total = wl.build_profile(prof, df, {})
    assert md is not None
    assert total == 1
    assert "01234567890X" not in md     # raw malformed id not printed as a name
    assert "non identifi" in md         # row flagged as non-identified instead


def test_sender_identity_is_proton():
    """Sender identity is kenziferaoun@proton.me, never bioniclia (gate 7)."""
    assert wl.SENDER == "kenziferaoun@proton.me"
    assert "bioniclia" not in wl.SENDER


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))