#!/usr/bin/env python3
"""
USDC Wallet Monitor — streaming log style (like tail -f)
Supports Base (EVM) and Solana. Chain auto-detected from wallet address format.
Uses public RPC nodes directly. No API key, no account, completely free.

Requirements:
    pip install rich requests

Usage:
    python wallet_monitor.py --wallet 0xYourBaseAddress         # Base (auto-detected)
    python wallet_monitor.py --wallet YourSolanaAddress         # Solana (auto-detected)
    python wallet_monitor.py --wallet 0xYourAddress -c          # colors on
    python wallet_monitor.py --wallet 0xYourAddress --hide-wallet
    python wallet_monitor.py --wallet 0xYourAddress --debug
"""

import argparse
import time
import sys
import re
from datetime import datetime

try:
    from rich.console import Console
    from rich.text import Text
    from rich.rule import Rule
    import requests
except ImportError:
    print("Missing dependencies. Run: pip install rich requests")
    sys.exit(1)


# ── Constants — Base ──────────────────────────────────────────────────────────

BASE_RPCS = [
    "https://mainnet.base.org",
    "https://base-rpc.publicnode.com",
    "https://1rpc.io/base",
    "https://base.llamarpc.com",
]
BASE_USDC_CONTRACT  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_USDC_DECIMALS  = 6
BASE_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BASE_MAX_BLOCKS     = 1000


# ── Constants — Solana ────────────────────────────────────────────────────────

SOL_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://1rpc.io/sol",
]
# Both native USDC and bridged USDc (Wormhole) mints
SOL_USDC_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr": "USDc",
}
SOL_TX_LIMIT = 25  # signatures to fetch per poll


# ── Shared constants ──────────────────────────────────────────────────────────

WARN_THRESHOLD  = 5.0
LOW_THRESHOLD   = 1.0

console         = None
COLOR           = False
HIDE_WALLET     = False
DEBUG           = False
rpc_id          = 0
block_ts_cache  = {}  # Base block_number -> datetime str


# ── Helpers ───────────────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def ts_from_unix(unix: int) -> str:
    return datetime.fromtimestamp(unix).strftime("%Y-%m-%d %H:%M:%S")

def short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 12 else addr

def p(text: str):
    console.print(text, markup=False, highlight=False)

def pdebug(text: str):
    if not DEBUG:
        return
    if COLOR:
        console.print(f"[dim]  [debug] {text}[/]", highlight=False)
    else:
        p(f"  [debug] {text}")

def detect_chain(wallet: str) -> str:
    """Return 'base' or 'solana' based on address format."""
    if wallet.startswith("0x") and len(wallet) == 42:
        return "base"
    # Solana: base58, 32-44 chars
    if re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", wallet):
        return "solana"
    return "unknown"

def print_balance(balance: float, token: str = "USDC"):
    if balance <= LOW_THRESHOLD:
        flag, style = "[CRITICAL]", "bold red"
    elif balance <= WARN_THRESHOLD:
        flag, style = "[WARNING]", "bold yellow"
    else:
        flag, style = "", None
    line = f"{now()}  ${balance:,.6f} {token}  {flag}".rstrip()
    if COLOR and style:
        console.print(Text(line, style=style), markup=False, highlight=False)
    else:
        p(line)

def print_transfer_line(ts: str, ref: str, sign: str, amount: float, balance: float, token: str = "USDC"):
    line = f"{ts}  {ref}  {sign}{amount:,.4f} {token}  |  Balance ${balance:,.6f}"
    p(line)


# ── Base RPC ──────────────────────────────────────────────────────────────────

def base_rpc(method: str, params: list) -> dict:
    global rpc_id
    rpc_id += 1
    last_err = None
    for url in BASE_RPCS:
        try:
            r = requests.post(url, json={
                "jsonrpc": "2.0", "id": rpc_id,
                "method": method, "params": params,
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                last_err = data["error"].get("message", str(data["error"]))
                pdebug(f"Base RPC {url} error: {last_err}, trying next")
                continue
            return data
        except Exception as e:
            last_err = e
            continue
    return {"error": {"message": str(last_err)}}


def base_block_timestamp(block_number: int) -> str:
    if block_number in block_ts_cache:
        return block_ts_cache[block_number]
    try:
        result = base_rpc("eth_getBlockByNumber", [hex(block_number), False])
        ts = int(result["result"]["timestamp"], 16)
        formatted = ts_from_unix(ts)
    except Exception:
        formatted = now()
    block_ts_cache[block_number] = formatted
    return formatted


def base_fetch_balance(wallet: str) -> tuple:
    selector = "0x70a08231"
    padded   = wallet[2:].lower().zfill(64)
    try:
        result = base_rpc("eth_call", [
            {"to": BASE_USDC_CONTRACT, "data": selector + padded}, "latest",
        ])
        if "error" in result:
            return None, result["error"].get("message", str(result["error"]))
        return int(result["result"], 16) / 10 ** BASE_USDC_DECIMALS, ""
    except Exception as e:
        return None, str(e)


def base_fetch_transfers(wallet: str) -> tuple:
    try:
        latest       = int(base_rpc("eth_blockNumber", [])["result"], 16)
        wallet_topic = "0x" + wallet[2:].lower().zfill(64)

        for offset in [2, 5, 10, 20]:
            to_int     = max(0, latest - offset)
            from_int   = max(0, to_int - BASE_MAX_BLOCKS)
            to_block   = hex(to_int)
            from_block = hex(from_int)
            pdebug(f"getLogs offset={offset} from={from_int} to={to_int}")

            incoming = base_rpc("eth_getLogs", [{"fromBlock": from_block, "toBlock": to_block,
                "address": BASE_USDC_CONTRACT,
                "topics": [BASE_TRANSFER_TOPIC, None, wallet_topic]}])
            outgoing = base_rpc("eth_getLogs", [{"fromBlock": from_block, "toBlock": to_block,
                "address": BASE_USDC_CONTRACT,
                "topics": [BASE_TRANSFER_TOPIC, wallet_topic, None]}])

            in_err  = incoming.get("error", {}).get("message", "")
            out_err = outgoing.get("error", {}).get("message", "")
            pdebug(f"getLogs in_err={repr(in_err)} out_err={repr(out_err)}")

            if any(e in in_err + out_err for e in ["beyond current head", "invalid block range"]):
                continue
            if "error" in incoming:
                return [], incoming["error"].get("message", str(incoming["error"]))
            if "error" in outgoing:
                return [], outgoing["error"].get("message", str(outgoing["error"]))
            break
        else:
            return [], "block range error after retries"

        logs = incoming.get("result", []) + outgoing.get("result", [])
        transfers = []
        for log in logs:
            transfers.append({
                "blockNumber": int(log["blockNumber"], 16),
                "hash":        log["transactionHash"],
                "from":        "0x" + log["topics"][1][-40:],
                "to":          "0x" + log["topics"][2][-40:],
                "amount":      int(log["data"], 16) / 10 ** BASE_USDC_DECIMALS,
                "token":       "USDc",
            })
        transfers.sort(key=lambda x: x["blockNumber"], reverse=True)
        return transfers[:50], ""
    except Exception as e:
        return [], str(e)


# ── Solana RPC ────────────────────────────────────────────────────────────────

def sol_rpc(method: str, params: list) -> dict:
    global rpc_id
    rpc_id += 1
    last_err = None
    for url in SOL_RPCS:
        try:
            r = requests.post(url, json={
                "jsonrpc": "2.0", "id": rpc_id,
                "method": method, "params": params,
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                last_err = data["error"].get("message", str(data["error"]))
                pdebug(f"Solana RPC {url} error: {last_err}, trying next")
                continue
            return data
        except Exception as e:
            last_err = e
            continue
    return {"error": {"message": str(last_err)}}


def sol_fetch_balance(wallet: str) -> tuple:
    """Fetch combined USDC + USDc balance across all token accounts."""
    try:
        total = 0.0
        for mint in SOL_USDC_MINTS:
            result = sol_rpc("getTokenAccountsByOwner", [
                wallet,
                {"mint": mint},
                {"encoding": "jsonParsed"},
            ])
            if "error" in result:
                return None, result["error"].get("message", str(result["error"]))
            accounts = result.get("result", {}).get("value", [])
            for acc in accounts:
                info = acc["account"]["data"]["parsed"]["info"]
                total += float(info["tokenAmount"]["uiAmount"] or 0)
        return total, ""
    except Exception as e:
        return None, str(e)


def sol_fetch_transfers(wallet: str) -> tuple:
    """
    Fetch recent USDC/USDc transfers by:
    1. Getting recent signatures for the wallet
    2. Fetching each transaction and parsing token balance changes
    """
    try:
        sig_result = sol_rpc("getSignaturesForAddress", [
            wallet,
            {"limit": SOL_TX_LIMIT},
        ])
        if "error" in sig_result:
            return [], sig_result["error"].get("message", str(sig_result["error"]))

        signatures = sig_result.get("result", [])
        transfers  = []

        for sig_info in signatures:
            sig = sig_info.get("signature")
            if not sig:
                continue

            tx_result = sol_rpc("getTransaction", [
                sig,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
            ])
            if "error" in tx_result or not tx_result.get("result"):
                continue

            tx   = tx_result["result"]
            meta = tx.get("meta", {})
            if not meta or meta.get("err"):
                continue  # skip failed transactions

            block_time = tx.get("blockTime")
            pre_balances  = {b["accountIndex"]: b for b in (meta.get("preTokenBalances")  or [])}
            post_balances = {b["accountIndex"]: b for b in (meta.get("postTokenBalances") or [])}

            all_indices = set(pre_balances) | set(post_balances)
            for idx in all_indices:
                pre  = pre_balances.get(idx,  {})
                post = post_balances.get(idx, {})

                mint = post.get("mint") or pre.get("mint")
                if mint not in SOL_USDC_MINTS:
                    continue

                owner = post.get("owner") or pre.get("owner")
                if owner != wallet:
                    continue

                pre_amt  = float((pre.get("uiTokenAmount")  or {}).get("uiAmount") or 0)
                post_amt = float((post.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                diff     = post_amt - pre_amt

                if diff == 0:
                    continue

                transfers.append({
                    "hash":      sig,
                    "timestamp": block_time,
                    "amount":    abs(diff),
                    "direction": "in" if diff > 0 else "out",
                    "token":     SOL_USDC_MINTS[mint],
                })

        transfers.sort(key=lambda x: (x["timestamp"] or 0), reverse=True)
        return transfers[:50], ""
    except Exception as e:
        return [], str(e)


# ── Unified run loop ──────────────────────────────────────────────────────────

def run(wallet: str, interval: int, chain: str):
    chain_label = "Base" if chain == "base" else "Solana"
    rpcs        = BASE_RPCS if chain == "base" else SOL_RPCS
    token_label = "USDc" if chain == "base" else "USDC/USDc"

    if COLOR:
        console.print(Rule(f"[bold cyan]{chain_label} {token_label} Wallet Monitor[/]", style="cyan"))
    else:
        p(f"--- {chain_label} {token_label} Wallet Monitor ---")

    if not HIDE_WALLET:
        p(f"  wallet    {wallet}")
    p(f"  chain     {chain_label}")
    p(f"  rpc       {rpcs[0]}  (+ {len(rpcs)-1} fallbacks)")
    p(f"  token     {token_label}")
    p(f"  interval  {interval}s")
    if DEBUG:
        p(f"  debug     on")
    if COLOR:
        console.print(Rule(style="cyan dim"))
    else:
        p("--------------------------------")

    poll_n       = 0
    last_balance = None
    seen_hashes  = set()

    p(f"\n{now()}  starting up, fetching initial balance...")

    while True:
        poll_n += 1
        pdebug(f"poll #{poll_n}  {now()}")

        # ── Balance
        if chain == "base":
            balance, err = base_fetch_balance(wallet)
        else:
            balance, err = sol_fetch_balance(wallet)

        if err:
            p(f"{now()}  error: {err}")
        else:
            if last_balance is None:
                print_balance(balance, token_label)
                last_balance = balance
            elif balance != last_balance:
                last_balance = balance
            else:
                pdebug(f"balance unchanged  ${balance:,.6f}")

        # ── Transfers
        if chain == "base":
            transfers, err = base_fetch_transfers(wallet)
        else:
            transfers, err = sol_fetch_transfers(wallet)

        if err:
            p(f"{now()}  transfers error: {err}")
        else:
            pdebug(f"{len(transfers)} transfer(s) found")
            if transfers:
                new_txs = [tx for tx in transfers if tx["hash"] not in seen_hashes]
                if new_txs:
                    wallet_lower = wallet.lower() if chain == "base" else wallet
                    sorted_new   = sorted(new_txs, key=lambda x: (
                        x.get("blockNumber", 0) if chain == "base" else (x.get("timestamp") or 0)
                    ))

                    # Calculate running balance oldest-to-newest
                    total_signed = sum(
                        (tx["amount"] if (
                            (chain == "base" and tx["to"].lower() == wallet_lower) or
                            (chain == "solana" and tx["direction"] == "in")
                        ) else -tx["amount"])
                        for tx in sorted_new
                    )
                    running = (balance if balance is not None else 0.0) - total_signed

                    for tx in sorted_new:
                        if chain == "base":
                            is_in  = tx["to"].lower() == wallet_lower
                            ts     = base_block_timestamp(tx["blockNumber"])
                            ref    = f"block:{tx['blockNumber']}"
                        else:
                            is_in  = tx["direction"] == "in"
                            ts     = ts_from_unix(tx["timestamp"]) if tx["timestamp"] else now()
                            ref    = f"tx:{short(tx['hash'])}"

                        sign    = "+" if is_in else "-"
                        signed  = tx["amount"] if is_in else -tx["amount"]
                        running += signed
                        print_transfer_line(ts, ref, sign, tx["amount"], running, tx["token"])
                        seen_hashes.add(tx["hash"])

                    if len(seen_hashes) > 200:
                        seen_hashes.clear()
                        for tx in transfers:
                            seen_hashes.add(tx["hash"])
                else:
                    pdebug("no new transfers")
            else:
                pdebug("no transfers found")

        pdebug(f"sleeping {interval}s")
        time.sleep(interval)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global COLOR, HIDE_WALLET, DEBUG, console

    parser = argparse.ArgumentParser(
        description="USDC wallet monitor — Base and Solana, auto-detected. No API key needed.",
    )
    parser.add_argument("--wallet",      required=True,        help="Wallet address (0x... for Base, base58 for Solana)")
    parser.add_argument("--interval",    type=int, default=15, help="Poll interval in seconds (default: 15)")
    parser.add_argument("-c",            dest="color",         action="store_true", default=False,
                                         help="Enable colors (default: plain b&w)")
    parser.add_argument("--hide-wallet", dest="hide_wallet",   action="store_true", default=False,
                                         help="Don't print wallet address in output")
    parser.add_argument("--debug",       dest="debug",         action="store_true", default=False,
                                         help="Show poll activity even when nothing changes")
    args = parser.parse_args()

    COLOR       = args.color
    HIDE_WALLET = args.hide_wallet
    DEBUG       = args.debug
    console     = Console(no_color=not COLOR, highlight=False)

    chain = detect_chain(args.wallet)
    if chain == "unknown":
        p("Error: unrecognized wallet format. Use 0x... for Base or base58 for Solana.")
        sys.exit(1)

    p(f"  detected  {chain} wallet")

    if args.interval < 5:
        p("Warning: using minimum interval of 5s.")
        args.interval = 5

    try:
        run(args.wallet, args.interval, chain)
    except KeyboardInterrupt:
        console.print("\nStopped.", markup=False)


if __name__ == "__main__":
    main()
