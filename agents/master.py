"""
agents/master.py

TrustOracle Master Agent — the orchestrator.

This is the entry point for buyers and external agents.
It:
  1. Accepts a CAP order with seller wallet + listing info
  2. Fans out 3 *simultaneous* paid CAP sub-orders to:
       - WalletReputation agent
       - ListingVerifier agent
       - RiskScorer agent (fed results from the other two)
  3. Aggregates all results
  4. Builds a signed TrustReport (keccak256 hash on-chain via CAP delivery)
  5. Delivers the final JSON Trust Report to the original buyer

This single agent demonstrates 1-to-3 A2A composability with
real USDC settlement at every hop — exactly what the CROO judges
score highest.

Register in CROO dashboard with:
  - Service name:  "AfreeKart Trust Oracle"
  - Price:         0.50 USDC
  - SLA:           0h 15m
  - Deliverable:   Schema (JSON)
  - Requirements:  Schema (JSON)
    {
      "wallet_address": "string",         // seller's wallet
      "listing_id": "string",             // AfreeKart listing UUID
      "listing_ipfs_cid": "string",       // Pinata CID
      "category": "string",              // e.g. "electronics"
      "price_usdt": number               // listing price
    }
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict

from croo import AgentClient, EventType, Event, APIError, is_insufficient_balance

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core.config import (
    croo_config,
    MASTER_SDK_KEY,
    WALLET_REP_SERVICE_ID,
    LISTING_VERIFIER_SERVICE_ID,
    RISK_SCORER_SERVICE_ID,
)
from core.models import TrustReport, WalletReputation, ListingVerification, RiskAssessment

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Master] %(message)s")
log = logging.getLogger(__name__)


# ── Sub-agent order helpers ───────────────────────────────────────────────────

async def call_sub_agent(
    client: AgentClient,
    service_id: str,
    requirements: dict,
    agent_name: str,
    request_id: str,
    max_retries: int = 2,
) -> dict:
    """
    Full CAP lifecycle against a sub-agent:
      negotiate → pay → poll for completion → get delivery
    Returns the deliverable schema dict.
    """
    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"[{request_id}] → {agent_name} (attempt {attempt}): negotiating...")

            neg = await client.negotiate_order({
                "service_id": service_id,
                "requirements_text": json.dumps({**requirements, "request_id": request_id}),
            })
            log.info(f"[{request_id}] → {agent_name}: negotiation {neg.id} created")

            # Wait for order_created (provider auto-accepts)
            order_id = await _wait_for_order(client, neg.id, timeout=60)
            log.info(f"[{request_id}] → {agent_name}: order {order_id} on-chain")

            # Pay
            await client.pay_order(order_id)
            log.info(f"[{request_id}] → {agent_name}: payment locked in escrow")

            # Wait for delivery
            delivery = await _poll_for_delivery(client, order_id, timeout=300)
            log.info(f"[{request_id}] → {agent_name}: delivery received ✓")

            return delivery.deliverable_schema or {}

        except APIError as e:
            if is_insufficient_balance(e):
                raise RuntimeError(
                    "Master agent AA wallet has insufficient USDC. "
                    "Deposit USDC at agent.croo.network → your agent → Configure."
                ) from e
            log.warning(f"[{request_id}] → {agent_name} attempt {attempt} failed: {e}")
            if attempt == max_retries:
                raise
            await asyncio.sleep(5)

    raise RuntimeError(f"All retries exhausted for {agent_name}")


async def _wait_for_order(
    client: AgentClient,
    negotiation_id: str,
    timeout: int = 60,
) -> str:
    """Poll until negotiation has an associated order_id."""
    elapsed = 0
    while elapsed < timeout:
        neg = await client.get_negotiation(negotiation_id)
        if neg.order_id:
            return neg.order_id
        if neg.status in ("rejected", "expired"):
            raise RuntimeError(f"Negotiation {negotiation_id} {neg.status}")
        await asyncio.sleep(3)
        elapsed += 3
    raise TimeoutError(f"No order created for negotiation {negotiation_id} after {timeout}s")


async def _poll_for_delivery(
    client: AgentClient,
    order_id: str,
    timeout: int = 300,
) -> object:
    """Poll until order is completed and return the delivery object."""
    elapsed = 0
    while elapsed < timeout:
        order = await client.get_order(order_id)
        if order.status == "completed":
            return await client.get_delivery(order_id)
        if order.status in ("rejected", "expired"):
            raise RuntimeError(f"Order {order_id} {order.status}")
        await asyncio.sleep(5)
        elapsed += 5
    raise TimeoutError(f"Order {order_id} not completed after {timeout}s")


# ── Core orchestration ────────────────────────────────────────────────────────

async def run_trust_check(
    client: AgentClient,
    wallet_address: str,
    listing_id: str,
    listing_ipfs_cid: str,
    category: str,
    price_usdt: float,
    request_id: str,
) -> TrustReport:
    """
    Fan-out pattern:
      Step 1: wallet_rep + listing_verifier run *concurrently*
      Step 2: risk_scorer runs with results from step 1
      Step 3: compose TrustReport
    """
    log.info(f"[{request_id}] Starting trust check for wallet={wallet_address} listing={listing_id}")

    # ── Step 1: concurrent sub-orders ─────────────────────────────────────────
    wallet_task = asyncio.create_task(
        call_sub_agent(
            client, WALLET_REP_SERVICE_ID,
            {"wallet_address": wallet_address},
            "WalletReputation", request_id,
        )
    )
    listing_task = asyncio.create_task(
        call_sub_agent(
            client, LISTING_VERIFIER_SERVICE_ID,
            {
                "listing_id": listing_id,
                "listing_ipfs_cid": listing_ipfs_cid,
                "category": category,
                "price_usdt": price_usdt,
            },
            "ListingVerifier", request_id,
        )
    )

    wallet_raw, listing_raw = await asyncio.gather(wallet_task, listing_task)
    log.info(f"[{request_id}] Sub-agents WalletRep + ListingVerifier complete")

    wallet_rep  = WalletReputation(**wallet_raw)
    listing_ver = ListingVerification(**listing_raw)

    # ── Step 2: risk scorer ────────────────────────────────────────────────────
    risk_raw = await call_sub_agent(
        client, RISK_SCORER_SERVICE_ID,
        {
            "wallet_score":   wallet_rep.score,
            "listing_score":  listing_ver.score,
            "wallet_detail":  asdict(wallet_rep),
            "listing_detail": asdict(listing_ver),
        },
        "RiskScorer", request_id,
    )
    risk = RiskAssessment(**{**risk_raw, "verdict": risk_raw.get("verdict", "CAUTION")})
    log.info(f"[{request_id}] RiskScorer complete. Verdict={risk.verdict}")

    # ── Step 3: compose final report ──────────────────────────────────────────
    report = TrustReport.build(
        request_id=request_id,
        wallet_address=wallet_address,
        listing_id=listing_id,
        wallet_rep=wallet_rep,
        listing_ver=listing_ver,
        risk=risk,
    )
    log.info(
        f"[{request_id}] Trust Report built. "
        f"Verdict={report.verdict.value} "
        f"Confidence={report.confidence} "
        f"Hash={report.report_hash}"
    )
    return report


# ── CAP provider loop ─────────────────────────────────────────────────────────

async def run():
    client = AgentClient(croo_config, MASTER_SDK_KEY)
    stream = await client.connect_websocket()
    log.info("TrustOracle Master agent online. Listening for orders...")

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
            request_id = str(uuid.uuid4())[:8]
            try:
                order = await client.get_order(e.order_id)
                req   = json.loads(order.requirements_text or "{}")

                wallet_address   = req.get("wallet_address", "")
                listing_id       = req.get("listing_id", "")
                listing_ipfs_cid = req.get("listing_ipfs_cid", "")
                category         = req.get("category", "other")
                price_usdt       = float(req.get("price_usdt", 0))

                if not wallet_address or not listing_id:
                    await client.reject_order(
                        e.order_id,
                        "Missing required fields: wallet_address and listing_id"
                    )
                    return

                report = await run_trust_check(
                    client,
                    wallet_address=wallet_address,
                    listing_id=listing_id,
                    listing_ipfs_cid=listing_ipfs_cid,
                    category=category,
                    price_usdt=price_usdt,
                    request_id=request_id,
                )

                # Upload full report as a file (so hash is accessible)
                report_json = report.to_deliverable_json()
                report_bytes = report_json.encode("utf-8")
                object_key = await client.upload_file(
                    f"trust_report_{request_id}.json",
                    report_bytes,
                )
                log.info(f"[{request_id}] Report uploaded: {object_key}")

                # Deliver — keccak256 hash goes on-chain via CAP
                await client.deliver_order(
                    e.order_id,
                    {
                        "deliverable_type": "schema",
                        "deliverable_schema": json.loads(report_json),
                    }
                )
                log.info(
                    f"[{request_id}] ✅ Delivered to buyer. "
                    f"Verdict={report.verdict.value} OnChainHash={report.report_hash}"
                )

            except Exception as ex:
                log.error(
                    f"[{request_id}] Master handler error for order {e.order_id}: {ex}",
                    exc_info=True,
                )
                try:
                    await client.reject_order(
                        e.order_id,
                        f"Trust check failed: {str(ex)[:200]}"
                    )
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
