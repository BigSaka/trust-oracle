"""
scripts/test_requester.py

Simulates a buyer (or external agent) calling the TrustOracle Master.

Usage:
  python scripts/test_requester.py \
    --wallet 0xSELLER_ADDRESS \
    --listing "your-listing-uuid" \
    --cid "QmYourIPFSCID" \
    --category electronics \
    --price 45.00

Or use default test values (AfreeKart test data):
  python scripts/test_requester.py
"""

import asyncio
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from croo import AgentClient

from core.config import croo_config, MASTER_SDK_KEY
# Import a separate requester SDK key if you want a different agent as buyer.
# For testing, we can reuse the master key as requester against itself.
REQUESTER_SDK_KEY = os.getenv("REQUESTER_SDK_KEY", MASTER_SDK_KEY)
MASTER_SERVICE_ID = os.getenv("MASTER_SERVICE_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Requester] %(message)s")
log = logging.getLogger(__name__)


async def run(
    wallet_address: str,
    listing_id: str,
    listing_ipfs_cid: str,
    category: str,
    price_usdt: float,
):
    if not MASTER_SERVICE_ID:
        raise EnvironmentError("Set MASTER_SERVICE_ID in .env (from CROO dashboard)")

    client = AgentClient(croo_config, REQUESTER_SDK_KEY)
    log.info(f"Placing trust check order for wallet={wallet_address}")

    # Step 1: negotiate
    neg = await client.negotiate_order({
        "service_id": MASTER_SERVICE_ID,
        "requirements_text": json.dumps({
            "wallet_address":   wallet_address,
            "listing_id":       listing_id,
            "listing_ipfs_cid": listing_ipfs_cid,
            "category":         category,
            "price_usdt":       price_usdt,
        }),
    })
    log.info(f"Negotiation created: {neg.id}")

    # Step 2: wait for order
    elapsed = 0
    order_id = None
    while elapsed < 60:
        n = await client.get_negotiation(neg.id)
        if n.order_id:
            order_id = n.order_id
            break
        if n.status in ("rejected", "expired"):
            raise RuntimeError(f"Negotiation {n.status}")
        await asyncio.sleep(3)
        elapsed += 3

    if not order_id:
        raise TimeoutError("Master did not accept negotiation within 60s")
    log.info(f"Order created on-chain: {order_id}")

    # Step 3: pay
    await client.pay_order(order_id)
    log.info("Payment sent. Waiting for trust check to complete (up to 15 min)...")

    # Step 4: poll for completion
    elapsed = 0
    while elapsed < 900:  # 15 min
        order = await client.get_order(order_id)
        if order.status == "completed":
            break
        if order.status in ("rejected", "expired"):
            raise RuntimeError(f"Order {order.status}")
        await asyncio.sleep(10)
        elapsed += 10
        log.info(f"  Still waiting... ({elapsed}s elapsed)")

    # Step 5: get delivery
    delivery = await client.get_delivery(order_id)
    report = delivery.deliverable_schema or {}

    print("\n" + "="*60)
    print("TRUST ORACLE REPORT")
    print("="*60)
    print(json.dumps(report, indent=2))
    print("="*60)
    print(f"\nVerdict:    {report.get('verdict')}")
    print(f"Confidence: {report.get('confidence')}")
    print(f"Summary:    {report.get('summary')}")
    print(f"Hash:       {report.get('report_hash')}")
    print()

    await client.close()


def main():
    parser = argparse.ArgumentParser(description="Test the TrustOracle master agent")
    parser.add_argument("--wallet",   default="0x0000000000000000000000000000000000000001")
    parser.add_argument("--listing",  default="test-listing-001")
    parser.add_argument("--cid",      default="QmTest000000000000000000000000000000000001")
    parser.add_argument("--category", default="electronics")
    parser.add_argument("--price",    type=float, default=45.00)
    args = parser.parse_args()

    asyncio.run(run(
        wallet_address   = args.wallet,
        listing_id       = args.listing,
        listing_ipfs_cid = args.cid,
        category         = args.category,
        price_usdt       = args.price,
    ))


if __name__ == "__main__":
    main()
