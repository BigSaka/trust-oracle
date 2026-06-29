#!/usr/bin/env bash
# scripts/run_all.sh
# Starts all 4 Trust Oracle agents with automatic restart on failure.
# Press Ctrl+C to stop all.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

source venv/bin/activate 2>/dev/null || true

# Colours
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pids=()

run_agent() {
    local name="$1"
    local module="$2"
    local logfile="logs/${name}.log"
    mkdir -p logs

    while true; do
        echo -e "${GREEN}[supervisor] Starting ${name}...${NC}"
        python -m "$module" >> "$logfile" 2>&1 &
        pid=$!
        pids+=($pid)
        wait $pid || true
        echo -e "${YELLOW}[supervisor] ${name} exited. Restarting in 5s...${NC}"
        sleep 5
    done
}

cleanup() {
    echo -e "\n[supervisor] Shutting down all agents..."
    for pid in "${pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    exit 0
}
trap cleanup SIGINT SIGTERM

# Start sub-agents first (they have no deps on each other)
run_agent "risk_scorer"       "agents.risk_scorer"       &
run_agent "wallet_reputation" "agents.wallet_reputation" &
run_agent "listing_verifier"  "agents.listing_verifier"  &

# Small delay so sub-agents are online before master starts taking orders
sleep 3
run_agent "master" "agents.master" &

echo -e "${GREEN}All agents running. Logs in ./logs/${NC}"
wait
