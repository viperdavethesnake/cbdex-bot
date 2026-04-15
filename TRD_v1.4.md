# SPECIFICATION: Base DEX Data Acquisition Layer (v1.4)

**Status:** SUPERSEDED by v1.5 — do not use as reference. Retained for audit trail only.  
**Updated:** 2026-04-14  
**Router address corrected 2026-04-14:** Original contained a truncated (invalid) address. Correct address verified on BaseScan.

---

> ⚠️ This document is superseded by TRD_v1.5.md. The authoritative specification is v1.5. Key differences: v1.4 includes Pearson correlation in the audit gate (removed in v1.5 as inappropriate for DEX data validation). The Aerodrome Router address below has been corrected from the original truncated value.

---

## 1. Objective
To construct a high-fidelity, 90-day historical dataset of 1-minute OHLCV, Liquidity (TVL), and Network Gas data for specific Base Network pairs, verified against on-chain ground truth to ensure ML model integrity.

---

## 2. System Requirements
- **Language:** Python 3.12
- **Libraries:**
  - `web3.py` (RPC interaction for Truth Path, Gas data, and production trade execution)
  - `polars` (High-performance aggregation and binning)
  - `httpx` (API requests)
- **Infrastructure:** Local disk (SSD/NVMe recommended for high-frequency Parquet I/O).
- **Format:** Apache Parquet (`float64` for all numerical values).

---

## 3. Target Pairs — LOCKED

| Pair | Pool Address | Pool Type | Swap Fee | TVL (approx.) | 24h Vol (approx.) |
|---|---|---|---|---|---|
| **WETH/USDC** | `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` | CL (Slipstream, tick 100) | 0.05% | ~$15–30M | ~$82–185M |
| **AERO/WETH** | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | Classic Volatile (vAMM) | 0.30% | ~$9.8M | ~$110K |

---

## 4. Trading Parameters — LOCKED

| Parameter | Specification | Note |
|---|---|---|
| Network | Base Mainnet (Chain ID: 8453) | Solely focused on Base |
| Pairs | WETH/USDC, AERO/WETH | Benchmark and Alpha pairs |
| Min Signal (WETH/USDC) | >0.12% | 0.10% round-trip + 0.02% buffer |
| Min Signal (AERO/WETH) | >0.65% | 0.60% round-trip + 0.05% buffer |
| Execution | Direct Aerodrome Router via web3.py | Coinbase UI must not be used for production |

### 4.1 Execution Path

**Aerodrome Router (verified on BaseScan):** `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43`

Coinbase routes through 0x + 1inch and adds ~1% service fee per swap. Effective round-trip costs ~2.1–2.6%. Coinbase UI acceptable for manual observation only.

---

*See TRD_v1.5.md for the complete, current specification.*
