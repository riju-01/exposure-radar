"""Entity resolution helpers, enrichment and scoring: the deterministic core."""
import pytest

from salesintel.enrich import region_of, rule_industry, segment_of
from salesintel.etl.resolve import is_infra_org, registered_domain
from salesintel.scoring import TIER_A, load_icp, score_account, score_need, tier_of, volume_multiplier
from salesintel.signals import SIGNALS


@pytest.mark.parametrize("host,expected", [
    ("www.mit.edu", "mit.edu"),
    ("*.inksoft.com", "inksoft.com"),
    ("shop.example.co.uk", "example.co.uk"),
    ("api.service.com:8443", "service.com"),
    ("10.0.0.1", None),
    ("localhost", None),
    ("Plesk", None),
    ("sni-support-required-for-valid-ssl", None),
    ("", None),
    (None, None),
])
def test_registered_domain(host, expected):
    assert registered_domain(host) == expected


@pytest.mark.parametrize("org,infra", [
    ("Amazon Technologies Inc.", True),
    ("Hetzner Online GmbH", True),
    ("Korea Telecom", True),
    ("Ziply Fiber", True),
    ("Private Customer", True),
    ("Massachusetts Institute of Technology", False),
    ("Acme Dental LLC", False),
])
def test_infra_org(org, infra):
    assert is_infra_org(org) is infra


def test_rule_industry_is_conservative():
    assert rule_industry("cornell.edu")[0] == "education"
    assert rule_industry("city.gov")[0] == "government"
    assert rule_industry("caredental.com")[0] == "healthcare"
    assert rule_industry("zxqv.com")[0] == "unknown"  # no clue -> unknown, never a guess


def test_region_and_segment():
    assert region_of("DE") == "Europe"
    assert region_of("BR") == "Latin America"
    assert region_of(None) == "Other"
    segs = load_icp()["segments"]
    assert segment_of(1, segs) == "micro"
    assert segment_of(30, segs) == "mid_market"
    assert segment_of(5000, segs) == "enterprise"


def test_volume_multiplier_has_diminishing_returns_and_cap():
    assert volume_multiplier(0) == 0
    assert volume_multiplier(1) == 1.0
    assert volume_multiplier(4) == 1.5
    assert volume_multiplier(10_000) == 2.0


def test_single_kev_outranks_many_hygiene_issues():
    kev_need, _ = score_need({"kev_vuln": 1})
    hygiene_need, _ = score_need({"self_signed": 50, "expired_cert": 50})
    assert kev_need > hygiene_need


def test_reasons_sorted_by_points_and_worded_for_reps():
    need, reasons = score_need({"self_signed": 3, "db_unauth": 1})
    assert reasons[0]["signal"] == "db_unauth"
    assert reasons[0]["text"] == "1 database reachable from the internet without authentication"
    assert 0 < need <= 100


def test_tier_requires_need_not_just_fit():
    assert tier_of(priority=95, need=TIER_A["need"] - 1) != "A"
    assert tier_of(priority=TIER_A["priority"], need=TIER_A["need"]) == "A"


def test_hosting_telecom_fit_is_suppressed():
    common = dict(signal_counts={"kev_vuln": 2}, n_ips=10, country_code="US", cloud_share=0.0)
    assert score_account(industry="hosting_telecom", **common).fit < score_account(industry="healthcare", **common).fit


def test_signal_registry_is_well_formed():
    keys = [s.key for s in SIGNALS]
    assert len(keys) == len(set(keys))
    for s in SIGNALS:
        assert s.weight > 0 and "{n}" in s.explain and s.category in {"vulnerability", "exposure", "hygiene", "sensitive"}
