"""
agents/risk_scorer.py

RiskScorer sub-agent.

Receives wallet reputation score + listing verification score from Master,
applies weighted composite scoring, adds heuristic flags, and returns a
final risk assessment with SAFE / CAUTION / REJECT verdict.

Register in CROO dashboard with:
  - Service name:  "Composite Risk Scorer"
  - Price:         0.05 USDC
  - SLA:           0h 3m
  - Deliverable:   Schema (JSON)
  - Requirements:  Schema (JSON)
    {
      "wallet_score": number,
      "listing_score": number,
      "wallet_detail": object,
      "listing_detail": object,
      "request_id": "string"
    }
"""

import asyncio
import json
import logging
import os
import sys

from croo import AgentClient, EventType, Event

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core.config import (
    croo_config, RISK_SCORER_SDK_KEY,
    WALLET_WEIGHT, LISTING_WEIGHT, RISK_WEIGHT,
    SAFE_THRESHOLD, CAUTION_THRESHOLD,
)
from core.models import RiskAssessment, Verdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RiskScorer] %(message)s")
log = logging.getLogger(__name__)


def _compute_risk(
    wallet_score: float,
    listing_score: float,
    wallet_detail: dict,
    listing_detail: dict,
) -> RiskAssessment:
    """
    Weighted composite scoring with heuristic flag injection.

    Base weights from config (WALLET_WEIGHT=0.45, LISTING_WEIGHT=0.30).
    RISK_WEIGHT (0.25) is applied as a penalty pool from detected flags.

    The more flags → higher penalty → lower composite.
    """
    flags: list[str] = []

    # ── Heuristic flags ───────────────────────────────────────────────────────
    dispute_rate = wallet_detail.get("dispute_rate", 0)
    if dispute_rate > 0.10:
        flags.append(f"High dispute rate: {dispute_rate*100:.1f}%")

    completion_rate = wallet_detail.get("completion_rate", 1)
    if completion_rate < 0.70 and wallet_detail.get("total_escrows", 0) > 3:
        flags.append(f"Low completion rate: {completion_rate*100:.0f}%")

    if not listing_detail.get("ipfs_accessible", True):
        flags.append("Listing images not accessible on IPFS")

    price_dev = abs(listing_detail.get("price_deviation_pct", 0))
    if price_dev > 60:
        flags.append(f"Extreme price anomaly: {price_dev:.0f}% from category median")
    elif price_dev > 30:
        flags.append(f"Suspicious price deviation: {price_dev:.0f}% from category median")

    if not listing_detail.get("metadata_integrity", True):
        flags.append("Listing metadata incomplete")

    wallet_age = wallet_detail.get("wallet_age_days", 0)
    if wallet_age < 7:
        flags.append("Brand new wallet (< 7 days old)")

    # ── Penalty calculation ────────────────────────────────────────────────────
    # Each flag reduces the risk pool score. 3+ flags = maximum penalty.
    flag_count   = len(flags)
    risk_penalty = min(1.0, flag_count / 3.0)   # 0.0 (no flags) → 1.0 (3+ flags)
    risk_score   = 1.0 - risk_penalty            # higher = safer

    # ── Composite score ───────────────────────────────────────────────────────
    composite = (
        wallet_score  * WALLET_WEIGHT
        + listing_score * LISTING_WEIGHT
        + risk_score    * RISK_WEIGHT
    )
    composite = round(max(0.0, min(1.0, composite)), 4)

    # ── Verdict ───────────────────────────────────────────────────────────────
    if composite >= SAFE_THRESHOLD:
        verdict = Verdict.SAFE
    elif composite >= CAUTION_THRESHOLD:
        verdict = Verdict.CAUTION
    else:
        verdict = Verdict.REJECT

    # Hard overrides: certain flags force REJECT regardless of score
    hard_reject_flags = [
        f for f in flags if "Extreme price anomaly" in f or "High dispute rate" in f
    ]
    if hard_reject_flags and composite < 0.60:
        verdict = Verdict.REJECT

    # Confidence: how far from the nearest threshold boundary (0.6–1.0 range)
    if verdict == Verdict.SAFE:
        gap = composite - SAFE_THRESHOLD
    elif verdict == Verdict.CAUTION:
        gap = min(composite - CAUTION_THRESHOLD, SAFE_THRESHOLD - composite)
    else:
        gap = CAUTION_THRESHOLD - composite
    confidence = round(0.60 + min(0.40, gap * 2), 4)

    notes_parts = [f"Composite: {composite:.2f}. Verdict: {verdict.value}."]
    if flags:
        notes_parts.append(f"Flags: {'; '.join(flags)}.")
    else:
        notes_parts.append("No risk flags detected.")

    return RiskAssessment(
        wallet_score=round(wallet_score, 4),
        listing_score=round(listing_score, 4),
        composite_score=composite,
        verdict=verdict,
        confidence=confidence,
        flags=flags,
        notes=" ".join(notes_parts),
    )


# ── CAP provider loop ─────────────────────────────────────────────────────────

async def run():
    client = AgentClient(croo_config, RISK_SCORER_SDK_KEY)
    stream = await client.connect_websocket()
    log.info("RiskScorer agent online. Listening for orders...")

    def on_negotiation(e: Event):
        async def _accept():
            try:
                await client.accept_negotiation(e.negotiation_id)
                log.info(f"Accepted negotiation {e.negotiation_id}")
            except Exception as ex:
                log.error(f"Accept failed: {ex}")
        asyncio.create_task(_accept())

    def on_paid(e: Event):
        async def _handle():
            try:
                order = await client.get_order(e.order_id)
                req   = json.loads(order.requirements_text or "{}")

                wallet_score   = float(req.get("wallet_score", 0.5))
                listing_score  = float(req.get("listing_score", 0.5))
                wallet_detail  = req.get("wallet_detail", {})
                listing_detail = req.get("listing_detail", {})
                request_id     = req.get("request_id", e.order_id)

                log.info(f"[{request_id}] Scoring. Wallet={wallet_score} Listing={listing_score}")
                result = _compute_risk(wallet_score, listing_score, wallet_detail, listing_detail)

                await client.deliver_order(
                    e.order_id,
                    {
                        "deliverable_type": "schema",
                        "deliverable_schema": json.loads(result.to_json()),
                    }
                )
                log.info(f"[{request_id}] Delivered. Verdict={result.verdict.value} "
                         f"Score={result.composite_score} Confidence={result.confidence}")

            except Exception as ex:
                log.error(f"Handler error for order {e.order_id}: {ex}", exc_info=True)
                try:
                    await client.reject_order(e.order_id, f"Internal error: {str(ex)[:120]}")
                except Exception:
                    pass

        asyncio.create_task(_handle())

    stream.on(EventType.NEGOTIATION_CREATED, on_negotiation)
    stream.on(EventType.ORDER_PAID,          on_paid)

    try:
        while True:
            await asyncio.sleep(30)
    finally:
        await stream.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
