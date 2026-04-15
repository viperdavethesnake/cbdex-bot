"""
Phase 0.5 — Subgraph Sync Check

Verifies the Aerodrome subgraph is indexed to within 1,000 blocks of the
current Base chain tip before any Truth Path queries are attempted.

Usage:
    python ingestion/check_subgraph.py

Exit codes:
    0 — subgraph is synced, safe to proceed
    1 — subgraph is stale or has indexing errors, do not proceed
"""

import os
import sys

import httpx
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

SUBGRAPH_ID = "GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM"
STALE_BLOCK_THRESHOLD = 1_000


def main() -> int:
    api_key = os.environ.get("THEGRAPH_API_KEY")
    rpc_url = os.environ.get("BASE_RPC_URL")

    if not api_key:
        print("ERROR: THEGRAPH_API_KEY not set in environment / .env")
        return 1
    if not rpc_url:
        print("ERROR: BASE_RPC_URL not set in environment / .env")
        return 1

    url = f"https://gateway.thegraph.com/api/{api_key}/subgraphs/id/{SUBGRAPH_ID}"
    query = "{ _meta { block { number } hasIndexingErrors } }"

    print(f"Querying subgraph: {SUBGRAPH_ID}")
    try:
        resp = httpx.post(url, json={"query": query}, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"ERROR: Subgraph request failed — {exc}")
        return 1

    payload = resp.json()
    if "errors" in payload:
        print(f"ERROR: Subgraph returned GraphQL errors — {payload['errors']}")
        return 1

    meta = payload["data"]["_meta"]
    subgraph_block = meta["block"]["number"]
    has_errors = meta["hasIndexingErrors"]

    print(f"Connecting to Base RPC: {rpc_url[:40]}...")
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            print("ERROR: Could not connect to Base RPC")
            return 1
        chain_head = w3.eth.block_number
    except Exception as exc:
        print(f"ERROR: RPC call failed — {exc}")
        return 1

    delta = chain_head - subgraph_block
    minutes_behind = delta * 2 / 60  # Base ~2s block time

    print()
    print(f"  Chain head:       {chain_head:,}")
    print(f"  Subgraph block:   {subgraph_block:,}")
    print(f"  Delta:            {delta:,} blocks (~{minutes_behind:.1f} minutes)")
    print(f"  Indexing errors:  {has_errors}")
    print()

    if has_errors:
        print("STALE — subgraph reports indexing errors. Do not proceed with Truth Path queries.")
        print("Options: Bitquery Aerodrome API, newer community subgraph, or eth_getLogs fallback.")
        return 1

    if delta > STALE_BLOCK_THRESHOLD:
        print(f"STALE — delta {delta:,} blocks exceeds threshold of {STALE_BLOCK_THRESHOLD:,}.")
        print("Options: Bitquery Aerodrome API, newer community subgraph, or eth_getLogs fallback.")
        return 1

    print("SYNCED — safe to proceed with Truth Path queries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
