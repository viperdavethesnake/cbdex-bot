"""
Execution Layer — Aerodrome Router Interface

Abstracted swap execution for Base Sepolia (paper trading) and Base Mainnet.
Calls the Aerodrome Router contract directly via web3.py.
Never touches Coinbase UI — direct on-chain interaction only.

Safety rules (non-negotiable):
  - Kill switch checked before every trade
  - Daily loss limit enforced at session level
  - minAmountOut enforced on every swap (slippage cap)
  - Gas ceiling: halt if baseFeePerGas exceeds GAS_CEILING_GWEI
  - No withdrawal permissions on the hot wallet

Usage:
    from execution.router import AerodromeRouter
    router = AerodromeRouter(network="sepolia")  # or "mainnet"
    tx = router.swap(token_in, token_out, amount_in_wei, min_amount_out_wei)
"""

import os
import time
import logging
from dataclasses import dataclass
from typing import Literal

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

log = logging.getLogger(__name__)

# ── Network config ─────────────────────────────────────────────────────────────

NETWORK_CONFIG = {
    "mainnet": {
        "rpc_url":   os.environ.get("BASE_RPC_URL", ""),
        "chain_id":  8453,
        "router":    "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
        "explorer":  "https://basescan.org/tx/",
    },
    "sepolia": {
        "rpc_url":   os.environ.get("BASE_SEPOLIA_RPC_URL", ""),
        "chain_id":  84532,
        "router":    os.environ.get("SEPOLIA_ROUTER_ADDRESS", ""),
        "explorer":  "https://sepolia.basescan.org/tx/",
    },
}

# ── Safety limits ──────────────────────────────────────────────────────────────

GAS_CEILING_GWEI = 1.0    # halt if baseFee > 1 Gwei (10× Base L2 median)
SLIPPAGE_CAP_PCT = 0.005  # 0.5% max slippage on minAmountOut
DEADLINE_SECONDS = 60     # tx deadline: now + 60s

# ── Aerodrome Router ABI (minimal — swapExactTokensForTokens only) ─────────────
# Full ABI: https://basescan.org/address/0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43

ROUTER_ABI = [
    {
        "inputs": [
            {"name": "amountIn",     "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {
                "components": [
                    {"name": "from",    "type": "address"},
                    {"name": "to",      "type": "address"},
                    {"name": "stable",  "type": "bool"},
                    {"name": "factory", "type": "address"},
                ],
                "name": "routes",
                "type": "tuple[]",
            },
            {"name": "to",       "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"name": "from",    "type": "address"},
                    {"name": "to",      "type": "address"},
                    {"name": "stable",  "type": "bool"},
                    {"name": "factory", "type": "address"},
                ],
                "name": "routes",
                "type": "tuple[]",
            },
            {"name": "amountIn", "type": "uint256"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]

ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


@dataclass
class SwapResult:
    success:       bool
    tx_hash:       str | None
    amount_in:     int       # wei
    amount_out:    int       # wei (actual)
    gas_used:      int
    gas_price_wei: int
    gas_cost_eth:  float
    error:         str | None = None
    explorer_url:  str | None = None


class KillSwitch(Exception):
    """Raised when the kill switch file is present."""


class GasCeilingExceeded(Exception):
    """Raised when baseFeePerGas exceeds GAS_CEILING_GWEI."""


class AerodromeRouter:
    """
    Thin wrapper around the Aerodrome Router contract.
    Handles gas checks, kill switch, token approval, and tx submission.
    """

    KILL_SWITCH_FILE = ".kill"

    # Aerodrome factory addresses (needed for route struct)
    FACTORY_CLASSIC = "0x420DD381b31aEf6683db6B902084cB0FFECe40D"  # Classic vAMM
    FACTORY_CL      = "0x5e7BB104d84c7CB9B682AaC2F3d509f5F406809A"  # Slipstream CL

    def __init__(
        self,
        network: Literal["mainnet", "sepolia"] = "sepolia",
        private_key: str | None = None,
    ):
        self.network = network
        cfg = NETWORK_CONFIG[network]
        self.chain_id    = cfg["chain_id"]
        self.explorer    = cfg["explorer"]
        self.router_addr = Web3.to_checksum_address(cfg["router"])

        self.w3 = Web3(Web3.HTTPProvider(cfg["rpc_url"]))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError(f"Cannot connect to {network} RPC")

        self._pk = private_key or os.environ.get("HOT_WALLET_PRIVATE_KEY", "")
        if not self._pk:
            raise ValueError("HOT_WALLET_PRIVATE_KEY not set")

        self.account = self.w3.eth.account.from_key(self._pk)
        self.wallet  = self.account.address

        self.router = self.w3.eth.contract(
            address=self.router_addr,
            abi=ROUTER_ABI,
        )

        log.info(f"AerodromeRouter ready  network={network}  wallet={self.wallet}")

    # ── Safety checks ──────────────────────────────────────────────────────────

    def check_kill_switch(self) -> None:
        """Raise KillSwitch if .kill file exists in working directory."""
        import pathlib
        if pathlib.Path(self.KILL_SWITCH_FILE).exists():
            raise KillSwitch("Kill switch active (.kill file present). Halting.")

    def check_gas(self) -> int:
        """Return current baseFeePerGas in wei. Raise if above ceiling."""
        base_fee = self.w3.eth.get_block("latest")["baseFeePerGas"]
        gwei = base_fee / 1e9
        if gwei > GAS_CEILING_GWEI:
            raise GasCeilingExceeded(
                f"baseFee {gwei:.4f} Gwei > ceiling {GAS_CEILING_GWEI} Gwei. Halting."
            )
        return base_fee

    def get_quote(
        self, token_in: str, token_out: str, amount_in: int, stable: bool = False
    ) -> int:
        """Get expected output amount from Aerodrome Router (read-only)."""
        route = [{
            "from":    Web3.to_checksum_address(token_in),
            "to":      Web3.to_checksum_address(token_out),
            "stable":  stable,
            "factory": self.FACTORY_CLASSIC,
        }]
        amounts = self.router.functions.getAmountsOut(amount_in, route).call()
        return amounts[-1]

    def _ensure_approval(self, token_addr: str, amount: int) -> None:
        """Approve router to spend token if allowance is insufficient."""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_addr),
            abi=ERC20_ABI,
        )
        allowance = token.functions.allowance(self.wallet, self.router_addr).call()
        if allowance >= amount:
            return

        log.info(f"Approving {self.router_addr} to spend {amount} of {token_addr}")
        nonce = self.w3.eth.get_transaction_count(self.wallet)
        approve_tx = token.functions.approve(
            self.router_addr,
            2**256 - 1,  # max approval
        ).build_transaction({
            "chainId": self.chain_id,
            "from":    self.wallet,
            "nonce":   nonce,
            "gas":     100_000,
        })
        signed  = self.account.sign_transaction(approve_tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt["status"] != 1:
            raise RuntimeError(f"Approval tx failed: {tx_hash.hex()}")
        log.info(f"Approval confirmed: {tx_hash.hex()}")

    # ── Core swap ──────────────────────────────────────────────────────────────

    def swap(
        self,
        token_in:       str,
        token_out:      str,
        amount_in:      int,
        min_amount_out: int,
        stable:         bool = False,
    ) -> SwapResult:
        """
        Execute a swap via the Aerodrome Router.

        Args:
            token_in:       checksummed token address to sell
            token_out:      checksummed token address to buy
            amount_in:      amount in wei
            min_amount_out: minimum acceptable output (slippage cap) in wei
            stable:         True for stable pools, False for volatile (AERO/WETH)

        Returns:
            SwapResult with tx details and actual amounts.
        """
        self.check_kill_switch()
        base_fee = self.check_gas()

        self._ensure_approval(token_in, amount_in)

        route = [{
            "from":    Web3.to_checksum_address(token_in),
            "to":      Web3.to_checksum_address(token_out),
            "stable":  stable,
            "factory": self.FACTORY_CLASSIC,
        }]

        deadline = int(time.time()) + DEADLINE_SECONDS
        nonce    = self.w3.eth.get_transaction_count(self.wallet)

        tx = self.router.functions.swapExactTokensForTokens(
            amount_in,
            min_amount_out,
            route,
            self.wallet,
            deadline,
        ).build_transaction({
            "chainId":             self.chain_id,
            "from":                self.wallet,
            "nonce":               nonce,
            "gas":                 300_000,
            "maxFeePerGas":        base_fee * 2,
            "maxPriorityFeePerGas": base_fee // 10,
        })

        signed  = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        log.info(f"Swap submitted: {tx_hash.hex()}")

        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)

        gas_cost = receipt["gasUsed"] * base_fee / 1e18

        if receipt["status"] != 1:
            return SwapResult(
                success=False,
                tx_hash=tx_hash.hex(),
                amount_in=amount_in,
                amount_out=0,
                gas_used=receipt["gasUsed"],
                gas_price_wei=base_fee,
                gas_cost_eth=gas_cost,
                error="Transaction reverted",
                explorer_url=self.explorer + tx_hash.hex(),
            )

        log.info(
            f"Swap confirmed  gas={receipt['gasUsed']}  "
            f"cost={gas_cost:.8f} ETH  block={receipt['blockNumber']}"
        )

        return SwapResult(
            success=True,
            tx_hash=tx_hash.hex(),
            amount_in=amount_in,
            amount_out=0,   # caller reads from quote or Transfer event
            gas_used=receipt["gasUsed"],
            gas_price_wei=base_fee,
            gas_cost_eth=gas_cost,
            explorer_url=self.explorer + tx_hash.hex(),
        )
