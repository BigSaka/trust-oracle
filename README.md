# AfreeKart Trust Oracle Network

> A multi-agent escrow intelligence network on CROO Agent Protocol (CAP).  
> Any agent — or human — can pay to underwrite the trustworthiness of a P2P commerce transaction before funds are committed.

**CROO Agent Hackathon submission** | Tracks: Research & Intelligence · Data & Verification  
Live on Base mainnet · Settled in USDC

---

## Architecture

```
Buyer / External Agent
        │
        │  pays CAP order
        ▼
┌─────────────────────────────┐
│   TrustOracle Master Agent  │  ← entry point, orchestrator
│   (agents/master.py)        │
└────────────┬────────────────┘
             │ fans out 3 simultaneous CAP sub-orders
     ┌───────┼──────────────┐
     ▼       ▼              ▼
┌─────────┐ ┌──────────┐ ┌──────────────┐
│ Wallet  │ │ Listing  │ │    Risk      │
│ Reputa- │ │ Verifier │ │   Scorer     │
│  tion   │ │  Agent   │ │   Agent      │
│(agents/ │ │(agents/  │ │ (agents/     │
│wallet_  │ │listing_  │ │ risk_scorer  │
│ rep.py) │ │verifier  │ │    .py)      │
│         │ │  .py)    │ │              │
└────┬────┘ └────┬─────┘ └──────┬───────┘
     │           │               │
     └───────────┴───────────────┘
                 │ results aggregate to master
                 ▼
        Signed Trust Report
        keccak256 hash on-chain
        Verdict: SAFE / CAUTION / REJECT
```

### Sub-agents

| Agent | What it checks | Data source |
|---|---|---|
| **WalletReputation** | On-chain history, escrow completion rate, dispute rate, wallet age | AfreeKartMarketplaceV2.sol (Polygon) + Web3 |
| **ListingVerifier** | IPFS image existence, price anomaly vs category median, metadata integrity | Pinata IPFS + Supabase listings table |
| **RiskScorer** | Weighted composite score → SAFE / CAUTION / REJECT | Results from other two agents + heuristics |

---

## Prerequisites

- Python 3.10+
- 4 CROO agents registered at [agent.croo.network](https://agent.croo.network)
- Small USDC balance on Base mainnet in the Master agent's AA wallet
- Polygon RPC access (free Alchemy or Infura tier)
- Pinata API key (you already have this for AfreeKart)
- Supabase connection string (your existing AfreeKart DB)

---

## Setup

```bash
git clone https://github.com/BigSaka/trust-oracle
cd trust-oracle
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in your keys
```

### .env variables

```env
# CROO SDK Keys (one per agent, obtained from agent.croo.network dashboard)
MASTER_SDK_KEY=croo_sk_...
WALLET_REP_SDK_KEY=croo_sk_...
LISTING_VERIFIER_SDK_KEY=croo_sk_...
RISK_SCORER_SDK_KEY=croo_sk_...

# CROO Service IDs (set after registering services in dashboard)
WALLET_REP_SERVICE_ID=
LISTING_VERIFIER_SERVICE_ID=
RISK_SCORER_SERVICE_ID=

# CROO endpoints
CROO_API_URL=https://api.croo.network
CROO_WS_URL=wss://api.croo.network/ws

# AfreeKart contract (Polygon mainnet)
AFREEKART_CONTRACT=0x...
POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY

# Pinata
PINATA_API_KEY=
PINATA_SECRET_KEY=
PINATA_GATEWAY=https://gateway.pinata.cloud/ipfs/

# Supabase
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=
```

---

## Running

Open 4 terminals (or use the `scripts/run_all.sh` helper):

```bash
# Terminal 1 - Risk Scorer (start first, no deps)
python -m agents.risk_scorer

# Terminal 2 - Wallet Reputation
python -m agents.wallet_reputation

# Terminal 3 - Listing Verifier
python -m agents.listing_verifier

# Terminal 4 - Master Orchestrator (starts last)
python -m agents.master
```

Or all at once with process supervision:

```bash
bash scripts/run_all.sh
```

---

## How a trust check works

1. Buyer pays Master agent via CROO CAP order with JSON input:
   ```json
   {
     "wallet_address": "0xSELLER_ADDRESS",
     "listing_id": "uuid-of-listing",
     "listing_ipfs_cid": "Qm...",
     "category": "electronics",
     "price_usdt": 45.00
   }
   ```

2. Master fans out 3 simultaneous CAP sub-orders to WalletReputation, ListingVerifier, and RiskScorer (RiskScorer waits for other two to complete first).

3. Each sub-agent delivers a JSON verdict within their SLA window.

4. Master composes the final Trust Report, uploads it via `upload_file`, delivers a signed JSON report, and the keccak256 hash is committed on-chain.

5. Buyer receives:
   ```json
   {
     "verdict": "SAFE",
     "confidence": 0.87,
     "wallet_score": 0.91,
     "listing_score": 0.82,
     "risk_score": 0.88,
     "report_hash": "0xabc...",
     "summary": "Seller has 23 completed escrows, 0 disputes. Listing image verified on IPFS. Price within 12% of category median.",
     "timestamp": "2026-06-29T10:00:00Z"
   }
   ```

---

## Judging criteria alignment

| Criterion | How we hit it |
|---|---|
| Technical execution (30%) | Full CAP lifecycle on all 4 agents. Async fan-out. Retry logic. File delivery with on-chain hash. |
| A2A composability (25%) | Master places 3 paid CAP orders simultaneously. Each sub-agent is independently callable. True economic graph. |
| Innovation (20%) | Only submission using a live escrow contract dataset. Only submission solving P2P trust for African commerce. |
| Usability (15%) | Single callable endpoint. AfreeKart integrates it directly. Real buyer wallets. |
| Presentation (10%) | Demo shows a real AfreeKart listing, real wallet, real verdict. Story: "0.3M Nigerians lose money to P2P fraud every month." |

---

## License

MIT
