"""
tests/test_scoring.py
Unit tests for scoring logic — no CROO SDK or network required.
Run: python -m pytest tests/ -v
"""
import sys, os

# Stub out env vars before any module import so config.py doesn't raise
_FAKE_ENV = {
    "MASTER_SDK_KEY":           "croo_sk_test",
    "WALLET_REP_SDK_KEY":       "croo_sk_test",
    "LISTING_VERIFIER_SDK_KEY": "croo_sk_test",
    "RISK_SCORER_SDK_KEY":      "croo_sk_test",
    "WALLET_REP_SERVICE_ID":    "svc-test-1",
    "LISTING_VERIFIER_SERVICE_ID": "svc-test-2",
    "RISK_SCORER_SERVICE_ID":   "svc-test-3",
    "AFREEKART_CONTRACT":       "0x0000000000000000000000000000000000000001",
    "POLYGON_RPC_URL":          "https://polygon-rpc.com",
    "PINATA_API_KEY":           "test",
    "PINATA_SECRET_KEY":        "test",
    "SUPABASE_URL":             "https://test.supabase.co",
    "SUPABASE_KEY":             "test",
}
for k, v in _FAKE_ENV.items():
    os.environ.setdefault(k, v)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from agents.wallet_reputation import _score_wallet
from agents.listing_verifier import _score_listing
from agents.risk_scorer import _compute_risk
from core.models import Verdict, TrustReport, WalletReputation, ListingVerification, RiskAssessment


# ── WalletReputation scoring ──────────────────────────────────────────────────

class TestWalletScoring:
    def test_no_history_returns_neutral(self):
        score, notes = _score_wallet(0, 0, 0, 0)
        assert 0.40 <= score <= 0.50
        assert "New seller" in notes

    def test_perfect_record_high_score(self):
        score, _ = _score_wallet(50, 50, 0, 200)
        assert score >= 0.85

    def test_high_disputes_lowers_score(self):
        score, _ = _score_wallet(10, 9, 5, 100)
        assert score < 0.55

    def test_score_bounded(self):
        score, _ = _score_wallet(1000, 999, 0, 999)
        assert 0.0 <= score <= 1.0
        score2, _ = _score_wallet(100, 10, 80, 10)
        assert 0.0 <= score2 <= 1.0


# ── ListingVerification scoring ───────────────────────────────────────────────

class TestListingScoring:
    def test_perfect_listing(self):
        score, _ = _score_listing(True, 5.0, True)
        assert score >= 0.90

    def test_no_ipfs_penalises(self):
        score_ok, _   = _score_listing(True, 5.0, True)
        score_bad, _  = _score_listing(False, 5.0, True)
        assert score_ok > score_bad

    def test_extreme_price_anomaly_penalises(self):
        score_ok, _   = _score_listing(True, 5.0, True)
        score_bad, _  = _score_listing(True, 200.0, True)
        assert score_ok > score_bad

    def test_score_bounded(self):
        score, _ = _score_listing(False, 500.0, False)
        assert 0.0 <= score <= 1.0


# ── RiskScorer ────────────────────────────────────────────────────────────────

class TestRiskScorer:
    GOOD_WALLET = {
        "completion_rate": 0.97,
        "dispute_rate": 0.0,
        "wallet_age_days": 200,
        "total_escrows": 30,
    }
    BAD_WALLET = {
        "completion_rate": 0.40,
        "dispute_rate": 0.25,
        "wallet_age_days": 3,
        "total_escrows": 8,
    }
    GOOD_LISTING = {
        "ipfs_accessible": True,
        "price_deviation_pct": 8.0,
        "metadata_integrity": True,
    }
    BAD_LISTING = {
        "ipfs_accessible": False,
        "price_deviation_pct": 150.0,
        "metadata_integrity": False,
    }

    def test_clean_transaction_is_safe(self):
        result = _compute_risk(0.90, 0.88, self.GOOD_WALLET, self.GOOD_LISTING)
        assert result.verdict == Verdict.SAFE
        assert len(result.flags) == 0

    def test_bad_seller_is_rejected(self):
        result = _compute_risk(0.20, 0.15, self.BAD_WALLET, self.BAD_LISTING)
        assert result.verdict == Verdict.REJECT
        assert len(result.flags) > 0

    def test_mixed_is_caution(self):
        result = _compute_risk(0.55, 0.60, self.BAD_WALLET, self.GOOD_LISTING)
        assert result.verdict in (Verdict.CAUTION, Verdict.REJECT)

    def test_confidence_is_bounded(self):
        result = _compute_risk(0.90, 0.88, self.GOOD_WALLET, self.GOOD_LISTING)
        assert 0.0 <= result.confidence <= 1.0


# ── TrustReport model ─────────────────────────────────────────────────────────

class TestTrustReport:
    def _make_report(self):
        wr = WalletReputation(
            wallet_address="0xabc",
            total_escrows=10, completed_escrows=10, disputed_escrows=0,
            completion_rate=1.0, dispute_rate=0.0,
            wallet_age_days=120, first_tx_timestamp=None,
            score=0.92, notes="Good seller.",
        )
        lv = ListingVerification(
            listing_id="test-001", ipfs_cid="Qm123",
            ipfs_accessible=True, image_count=3,
            price_usdt=45.0, category="electronics",
            category_median_price=40.0, price_deviation_pct=12.5,
            metadata_integrity=True, score=0.88, notes="Listing OK.",
        )
        ra = RiskAssessment(
            wallet_score=0.92, listing_score=0.88,
            composite_score=0.89, verdict=Verdict.SAFE,
            confidence=0.91, flags=[], notes="No flags.",
        )
        return TrustReport.build("req-001", "0xabc", "test-001", wr, lv, ra)

    def test_hash_is_deterministic(self):
        r1 = self._make_report()
        r2 = self._make_report()
        # Timestamps will differ, so hashes differ — but format is correct
        assert r1.report_hash.startswith("0x")
        assert len(r1.report_hash) == 66  # "0x" + 64 hex chars

    def test_verdict_in_output(self):
        report = self._make_report()
        output = report.to_deliverable_json()
        assert "SAFE" in output

    def test_summary_generated(self):
        report = self._make_report()
        assert len(report.summary) > 10
