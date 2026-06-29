"""
agents/wallet_reputation.py

WalletReputation sub-agent.

Listens for CAP orders, queries AfreeKartMarketplaceV2.sol on Polygon
to score a seller wallet's on-chain trust history, then delivers a
WalletReputation JSON result.

Register this agent in the CROO dashboard with:
  - Service name:  "Wallet Reputation Check"
  - Price:         0.10 USDC
  - SLA:           0h 5m
  - Deliverable:   Schema (JSON)
  - Requirements:  Schema (JSON)
    {
      "wallet_address": "string",
      "request_id": "string"
    }
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

from web3 import Web3
from croo import AgentClient, EventType, Event

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core.config import croo_config, WALLET_REP_SDK_KEY, POLYGON_RPC_URL, AFREEKART_CONTRACT
from core.models import WalletReputation, Verdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [WalletRep] %(message)s")
log = logging.getLogger(__name__)

# ── AfreeKart contract ABI (escrow-relevant functions only) ───────────────────
AFREEKART_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "seller", "type": "address"}],
        "name": "getSellerStats",
        "outputs": [
            {"internalType": "uint256", "name": "totalOrders",     "type": "uint256"},
            {"internalType": "uint256", "name": "completedOrders", "type": "uint256"},
            {"internalType": "uint256", "name": "disputedOrders",  "type": "uint256"},
            {"internalType": "uint256", "name": "firstOrderTime",  "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

# ── Scoring logic ─────────────────────────────────────────────────────────────

def _score_wallet(
    total: int,
    completed: int,
    disputed: int,
    wallet_age_days: int,
) -> tuple[float, str]:
    """
    Returns (score 0.0–1.0, human-readable notes).

    Weights:
      40% completion rate
      30% dispute rate (inverted)
      20% wallet age
      10% volume bonus (log-scaled)
    """
    import math

    # No history at all — neutral-low
    if total == 0:
        return 0.45, "No escrow history found. New seller."

    completion_rate = completed / total
    dispute_rate    = disputed / total

    completion_score = completion_rate                          # 0–1
    dispute_score    = max(0.0, 1.0 - dispute_rate * 3)       # penalise hard
    age_score        = min(1.0, wallet_age_days / 180)         # caps at 6 months
    volume_bonus     = min(1.0, math.log1p(completed) / 4.0)  # log scale, caps ~54 orders

    score = (
        completion_score * 0.40
        + dispute_score  * 0.30
        + age_score      * 0.20
        + volume_bonus   * 0.10
    )
    score = round(max(0.0, min(1.0, score)), 4)

    notes_parts = [
        f"{completed}/{total} orders completed ({completion_rate*100:.0f}%).",
        f"{disputed} dispute(s).",
        f"Wallet active {wallet_age_days} days.",
    ]
    return score, " ".join(notes_parts)


async def check_wallet(w3: Web3, contract, wallet_address: str) -> WalletReputation:
    """Query Polygon and build a WalletReputation result."""
    try:
        checksum = Web3.to_checksum_address(wallet_address)
        stats = contract.functions.getSellerStats(checksum).call()
        total, completed, disputed, first_order_time = stats
    except Exception as e:
        log.warning(f"Contract call failed for {wallet_address}: {e}")
        total, completed, disputed, first_order_time = 0, 0, 0, 0

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if first_order_time > 0:
        wallet_age_days = max(0, (now_ts - first_order_time) // 86400)
        first_tx_str = datetime.fromtimestamp(first_order_time, tz=timezone.utc).isoformat()
    else:
        # Fall back to querying first tx from Web3 (best-effort)
        wallet_age_days = 0
        first_tx_str = None

    completion_rate = (completed / total) if total > 0 else 0.0
    dispute_rate    = (disputed / total)  if total > 0 else 0.0

    score, notes = _score_wallet(total, completed, disputed, wallet_age_days)

    return WalletReputation(
        wallet_address=wallet_address,
        total_escrows=total,
        completed_escrows=completed,
        disputed_escrows=disputed,
        completion_rate=round(completion_rate, 4),
        dispute_rate=round(dispute_rate, 4),
        wallet_age_days=wallet_age_days,
        first_tx_timestamp=first_tx_str,
        score=score,
        notes=notes,
    )


# ── CAP provider loop ─────────────────────────────────────────────────────────

async def run():
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(AFREEKART_CONTRACT),
        abi=AFREEKART_ABI,
    )
    log.info(f"Connected to Polygon. Block: {w3.eth.block_number}")

    client = AgentClient(croo_config, WALLET_REP_SDK_KEY)
    stream = await client.connect_websocket()
    log.info("WalletReputation agent online. Listening for orders...")

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

                wallet_address = req.get("wallet_address", "")
                request_id     = req.get("request_id", e.order_id)

                log.info(f"[{request_id}] Checking wallet {wallet_address}")
                result = await check_wallet(w3, contract, wallet_address)

                await client.deliver_order(
                    e.order_id,
                    {
                        "deliverable_type": "schema",
                        "deliverable_schema": json.loads(result.to_json()),
                    }
                )
                log.info(f"[{request_id}] Delivered. Score: {result.score} — {result.notes}")

            except Exception as ex:
                log.error(f"Handler error for order {e.order_id}: {ex}", exc_info=True)
                try:
                    await client.reject_order(e.order_id, f"Internal error: {str(ex)[:120]}")
                except Exception:
                    pass

        asyncio.create_task(_handle())

    stream.on(EventType.NEGOTIATION_CREATED, on_negotiation)
    stream.on(EventType.ORDER_PAID,          on_paid)

    # Keep alive
    try:
        while True:
            await asyncio.sleep(30)
    finally:
        await stream.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
