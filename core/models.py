"""
core/models.py
Shared data models for all Trust Oracle agents.
"""
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional
import json
import hashlib
from datetime import datetime, timezone


class Verdict(str, Enum):
    SAFE    = "SAFE"
    CAUTION = "CAUTION"
    REJECT  = "REJECT"


@dataclass
class WalletReputation:
    wallet_address: str
    total_escrows: int
    completed_escrows: int
    disputed_escrows: int
    completion_rate: float      # 0.0–1.0
    dispute_rate: float         # 0.0–1.0
    wallet_age_days: int
    first_tx_timestamp: Optional[str]
    score: float                # 0.0–1.0
    notes: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "WalletReputation":
        return cls(**json.loads(s))


@dataclass
class ListingVerification:
    listing_id: str
    ipfs_cid: str
    ipfs_accessible: bool
    image_count: int
    price_usdt: float
    category: str
    category_median_price: float
    price_deviation_pct: float   # % from median
    metadata_integrity: bool      # required fields present
    score: float                  # 0.0–1.0
    notes: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "ListingVerification":
        return cls(**json.loads(s))


@dataclass
class RiskAssessment:
    wallet_score: float
    listing_score: float
    composite_score: float
    verdict: Verdict
    confidence: float
    flags: list[str]    # human-readable risk flags
    notes: str

    def to_json(self) -> str:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return json.dumps(d)

    @classmethod
    def from_json(cls, s: str) -> "RiskAssessment":
        d = json.loads(s)
        d["verdict"] = Verdict(d["verdict"])
        return cls(**d)


@dataclass
class TrustReport:
    """Final deliverable from the Master agent."""
    request_id: str
    wallet_address: str
    listing_id: str

    verdict: Verdict
    confidence: float
    wallet_score: float
    listing_score: float
    composite_score: float

    flags: list[str]
    summary: str

    wallet_detail: dict
    listing_detail: dict

    timestamp: str
    report_hash: str    # keccak256 of the canonical report JSON

    def to_canonical_json(self) -> str:
        """Deterministic JSON for hashing — no report_hash field."""
        d = asdict(self)
        d["verdict"] = self.verdict.value
        d.pop("report_hash", None)
        return json.dumps(d, sort_keys=True)

    def compute_hash(self) -> str:
        canonical = self.to_canonical_json()
        return "0x" + hashlib.sha256(canonical.encode()).hexdigest()

    def to_deliverable_json(self) -> str:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return json.dumps(d, indent=2)

    @classmethod
    def build(
        cls,
        request_id: str,
        wallet_address: str,
        listing_id: str,
        wallet_rep: WalletReputation,
        listing_ver: ListingVerification,
        risk: RiskAssessment,
    ) -> "TrustReport":
        summary_parts = []
        if wallet_rep.total_escrows > 0:
            summary_parts.append(
                f"Seller has {wallet_rep.completed_escrows}/{wallet_rep.total_escrows} "
                f"completed escrows ({wallet_rep.completion_rate*100:.0f}% rate)."
            )
        if wallet_rep.disputed_escrows == 0:
            summary_parts.append("No disputes on record.")
        else:
            summary_parts.append(f"{wallet_rep.disputed_escrows} dispute(s) found.")
        if listing_ver.ipfs_accessible:
            summary_parts.append("Listing images verified on IPFS.")
        else:
            summary_parts.append("IPFS images unreachable — flag.")
        pct = abs(listing_ver.price_deviation_pct)
        if pct < 20:
            summary_parts.append(f"Price within {pct:.0f}% of category median.")
        else:
            summary_parts.append(f"Price deviates {pct:.0f}% from category median — flag.")

        report = cls(
            request_id=request_id,
            wallet_address=wallet_address,
            listing_id=listing_id,
            verdict=risk.verdict,
            confidence=risk.confidence,
            wallet_score=wallet_rep.score,
            listing_score=listing_ver.score,
            composite_score=risk.composite_score,
            flags=risk.flags,
            summary=" ".join(summary_parts),
            wallet_detail=asdict(wallet_rep),
            listing_detail=asdict(listing_ver),
            timestamp=datetime.now(timezone.utc).isoformat(),
            report_hash="",
        )
        report.report_hash = report.compute_hash()
        return report
