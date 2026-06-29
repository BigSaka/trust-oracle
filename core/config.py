"""
core/config.py
Centralised environment config for all Trust Oracle agents.
"""
import os
from dotenv import load_dotenv
from croo import Config

load_dotenv()

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val

# ── CROO endpoints ────────────────────────────────────────────────────────────
CROO_API_URL = os.getenv("CROO_API_URL", "https://api.croo.network")
CROO_WS_URL  = os.getenv("CROO_WS_URL",  "wss://api.croo.network/ws")

croo_config = Config(
    base_url=CROO_API_URL,
    ws_url=CROO_WS_URL,
)

# ── CROO SDK keys (one per agent) ─────────────────────────────────────────────
MASTER_SDK_KEY           = _require("MASTER_SDK_KEY")
WALLET_REP_SDK_KEY       = _require("WALLET_REP_SDK_KEY")
LISTING_VERIFIER_SDK_KEY = _require("LISTING_VERIFIER_SDK_KEY")
RISK_SCORER_SDK_KEY      = _require("RISK_SCORER_SDK_KEY")

# ── Service IDs (set after dashboard registration) ────────────────────────────
WALLET_REP_SERVICE_ID       = _require("WALLET_REP_SERVICE_ID")
LISTING_VERIFIER_SERVICE_ID = _require("LISTING_VERIFIER_SERVICE_ID")
RISK_SCORER_SERVICE_ID      = _require("RISK_SCORER_SERVICE_ID")

# ── AfreeKart / Polygon ───────────────────────────────────────────────────────
AFREEKART_CONTRACT = _require("AFREEKART_CONTRACT")
POLYGON_RPC_URL    = _require("POLYGON_RPC_URL")

# ── Pinata ────────────────────────────────────────────────────────────────────
PINATA_API_KEY    = _require("PINATA_API_KEY")
PINATA_SECRET_KEY = _require("PINATA_SECRET_KEY")
PINATA_GATEWAY    = os.getenv("PINATA_GATEWAY", "https://gateway.pinata.cloud/ipfs/")

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_KEY = _require("SUPABASE_KEY")

# ── Scoring weights ───────────────────────────────────────────────────────────
WALLET_WEIGHT  = 0.45   # on-chain history matters most
LISTING_WEIGHT = 0.30   # listing integrity
RISK_WEIGHT    = 0.25   # composite heuristics

SAFE_THRESHOLD    = 0.70   # ≥ 0.70 → SAFE
CAUTION_THRESHOLD = 0.45   # 0.45–0.69 → CAUTION
                            # < 0.45 → REJECT

# ── SLA budgets (minutes) ─────────────────────────────────────────────────────
SUB_AGENT_SLA_MINUTES   = 5    # each sub-agent must deliver within 5 min
MASTER_SLA_MINUTES      = 15   # master must deliver within 15 min
