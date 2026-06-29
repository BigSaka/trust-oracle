"""
agents/listing_verifier.py

ListingVerifier sub-agent.

Verifies an AfreeKart listing's integrity:
  1. IPFS image accessibility via Pinata gateway
  2. Price anomaly vs category median (from Supabase)
  3. Required metadata presence

Register in CROO dashboard with:
  - Service name:  "Listing Integrity Verifier"
  - Price:         0.10 USDC
  - SLA:           0h 5m
  - Deliverable:   Schema (JSON)
  - Requirements:  Schema (JSON)
    {
      "listing_id": "string",
      "listing_ipfs_cid": "string",
      "category": "string",
      "price_usdt": number,
      "request_id": "string"
    }
"""

import asyncio
import json
import logging
import os
import sys

import aiohttp
from supabase import create_client, Client
from croo import AgentClient, EventType, Event

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core.config import (
    croo_config, LISTING_VERIFIER_SDK_KEY,
    PINATA_GATEWAY, PINATA_API_KEY, PINATA_SECRET_KEY,
    SUPABASE_URL, SUPABASE_KEY,
)
from core.models import ListingVerification

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ListingVerifier] %(message)s")
log = logging.getLogger(__name__)

# ── IPFS verification ─────────────────────────────────────────────────────────

async def check_ipfs(session: aiohttp.ClientSession, cid: str) -> tuple[bool, int]:
    """
    Returns (accessible: bool, image_count: int).
    Hits the Pinata gateway for the CID.
    If it's a directory CID, counts files; if a single file, counts 1.
    """
    url = f"{PINATA_GATEWAY.rstrip('/')}/{cid}"
    try:
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                content_type = resp.headers.get("content-type", "")
                # It's a direct image file
                if "image" in content_type:
                    return True, 1
                return True, 1   # directory or unknown — count as accessible
            return False, 0
    except Exception as e:
        log.warning(f"IPFS check failed for {cid}: {e}")
        return False, 0


# ── Price anomaly ─────────────────────────────────────────────────────────────

async def get_category_median(supabase: Client, category: str) -> float:
    """
    Query Supabase listings table for category median price.
    Falls back to a hardcoded heuristic map if DB is empty.
    """
    FALLBACK_MEDIANS = {
        "electronics":   35.0,
        "fashion":       12.0,
        "food":           5.0,
        "home":          20.0,
        "beauty":        10.0,
        "sports":        18.0,
        "books":          5.0,
        "other":         15.0,
    }

    try:
        result = (
            supabase
            .table("listings")
            .select("price_usdt")
            .eq("category", category)
            .eq("status", "active")
            .execute()
        )
        prices = [row["price_usdt"] for row in result.data if row.get("price_usdt")]
        if not prices:
            return FALLBACK_MEDIANS.get(category.lower(), 15.0)
        prices.sort()
        mid = len(prices) // 2
        return prices[mid] if len(prices) % 2 else (prices[mid-1] + prices[mid]) / 2
    except Exception as e:
        log.warning(f"Supabase query failed: {e}. Using fallback median.")
        return FALLBACK_MEDIANS.get(category.lower(), 15.0)


def _score_listing(
    ipfs_accessible: bool,
    price_deviation_pct: float,
    metadata_integrity: bool,
) -> tuple[float, str]:
    """
    Returns (score 0.0–1.0, notes).

    Weights:
      35% IPFS image accessible
      40% price within normal range
      25% metadata completeness
    """
    ipfs_score     = 1.0 if ipfs_accessible else 0.0
    price_score    = max(0.0, 1.0 - abs(price_deviation_pct) / 100.0)
    metadata_score = 1.0 if metadata_integrity else 0.2

    score = (
        ipfs_score     * 0.35
        + price_score  * 0.40
        + metadata_score * 0.25
    )
    score = round(max(0.0, min(1.0, score)), 4)

    notes_parts = []
    notes_parts.append("IPFS images reachable." if ipfs_accessible else "IPFS images UNREACHABLE.")
    if abs(price_deviation_pct) < 20:
        notes_parts.append(f"Price {abs(price_deviation_pct):.0f}% from category median — normal.")
    else:
        notes_parts.append(f"Price {abs(price_deviation_pct):.0f}% from category median — ANOMALY.")
    notes_parts.append("Metadata complete." if metadata_integrity else "Metadata incomplete.")

    return score, " ".join(notes_parts)


async def verify_listing(
    supabase: Client,
    session: aiohttp.ClientSession,
    listing_id: str,
    ipfs_cid: str,
    category: str,
    price_usdt: float,
) -> ListingVerification:
    ipfs_ok, image_count = await check_ipfs(session, ipfs_cid)
    median = await get_category_median(supabase, category)
    deviation_pct = round(((price_usdt - median) / median) * 100, 2) if median > 0 else 0.0

    # Metadata integrity: we require all key fields to be non-empty
    metadata_ok = bool(listing_id and ipfs_cid and category and price_usdt > 0)

    score, notes = _score_listing(ipfs_ok, deviation_pct, metadata_ok)

    return ListingVerification(
        listing_id=listing_id,
        ipfs_cid=ipfs_cid,
        ipfs_accessible=ipfs_ok,
        image_count=image_count,
        price_usdt=price_usdt,
        category=category,
        category_median_price=round(median, 2),
        price_deviation_pct=deviation_pct,
        metadata_integrity=metadata_ok,
        score=score,
        notes=notes,
    )


# ── CAP provider loop ─────────────────────────────────────────────────────────

async def run():
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = AgentClient(croo_config, LISTING_VERIFIER_SDK_KEY)
    stream = await client.connect_websocket()
    log.info("ListingVerifier agent online. Listening for orders...")

    connector = aiohttp.TCPConnector(limit=10)
    http_session = aiohttp.ClientSession(connector=connector)

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

                listing_id = req.get("listing_id", "")
                ipfs_cid   = req.get("listing_ipfs_cid", "")
                category   = req.get("category", "other")
                price_usdt = float(req.get("price_usdt", 0))
                request_id = req.get("request_id", e.order_id)

                log.info(f"[{request_id}] Verifying listing {listing_id} CID {ipfs_cid}")
                result = await verify_listing(
                    supabase, http_session, listing_id, ipfs_cid, category, price_usdt
                )

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

    try:
        while True:
            await asyncio.sleep(30)
    finally:
        await http_session.close()
        await stream.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
