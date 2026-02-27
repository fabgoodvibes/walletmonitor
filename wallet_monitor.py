#!/usr/bin/env python3
"""
Base USDc Wallet Monitor — streaming log style (like tail -f)
Uses the Base public RPC directly. No API key, no account, completely free.

Requirements:
    pip install rich requests

Usage:
    python wallet_monitor.py --wallet 0xYourAddress               # plain b&w, quiet (default)
    python wallet_monitor.py --wallet 0xYourAddress -c            # colors on
    python wallet_monitor.py --wallet 0xYourAddress --hide-wallet # hide wallet address
    python wallet_monitor.py --wallet 0xYourAddress --debug       # verbose poll info
"""

import argparse
import time
import sys
from datetime import datetime

try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    from rich.rule import Rule
    from rich import box
    import requests
except ImportError:
    print("Missing dependencies. Run: pip install rich requests")
    sys.exit(1)


# ── Constants ─────────────────────────────────────────────────────────────────

BASE_RPCS = [
    "https://mainnet.base.org",
    "https://base-rpc.publicnode.com",
    "https://1rpc.io/base",
    "https://base.llamarpc.com",
]

USDC_CONTRACT  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS  = 6
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WARN_THRESHOLD = 5.0
LOW_THRESHOLD  = 1.0
MAX_BLOCKS     = 1000

console     = None
COLOR       = False
HIDE_WALLET = False
DEBUG       = False
rpc_id          = 0
block_ts_cache  = {}  # block_number -> datetime str, avoids repeat calls


# ── RPC ───────────────────────────────────────────────────────────────────────

def rpc(method: str, params: list) -> dict:
    global rpc_id
    rpc_id += 1
    last_err = None
    for url in BASE_RPCS:
        try:
            r = requests.post(url, json={
                "jsonrpc": "2.0",
                "id":      rpc_id,
                "method":  method,
                "params":  params,
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
            # Also rotate on JSON-RPC errors so fallback nodes are tried
            if "error" in data:
                last_err = data["error"].get("message", str(data["error"]))
                pdebug(f"RPC {url} returned error: {last_err}, trying next node")
                continue
            return data
        except Exception as e:
            last_err = e
            continue
    # All nodes failed — return the last error response so caller can handle it
    return {"error": {"message": str(last_err)}}


def fetch_balance(wallet: str) -> tuple:
    selector = "0x70a08231"
    padded   = wallet[2:].lower().zfill(64)
    try:
        result = rpc("eth_call", [
            {"to": USDC_CONTRACT, "data": selector + padded},
            "latest",
        ])
        if "error" in result:
            return None, result["error"].get("message", str(result["error"]))
        balance = int(result["result"], 16) / 10 ** USDC_DECIMALS
        return balance, ""
    except Exception as e:
        return None, str(e)


def fetch_transfers(wallet: str) -> tuple:
    try:
        latest       = int(rpc("eth_blockNumber", [])["result"], 16)
        wallet_topic = "0x" + wallet[2:].lower().zfill(64)

        # Retry with increasing offset if node says block range exceeds head
        for offset in [2, 5, 10, 20]:
            to_int     = max(0, latest - offset)
            from_int   = max(0, to_int - MAX_BLOCKS)
            to_block   = hex(to_int)
            from_block = hex(from_int)

            pdebug(f"getLogs attempt offset={offset} from={from_int}({from_block}) to={to_int}({to_block})")

            incoming = rpc("eth_getLogs", [{"fromBlock": from_block, "toBlock": to_block,
                "address": USDC_CONTRACT, "topics": [TRANSFER_TOPIC, None, wallet_topic]}])
            outgoing = rpc("eth_getLogs", [{"fromBlock": from_block, "toBlock": to_block,
                "address": USDC_CONTRACT, "topics": [TRANSFER_TOPIC, wallet_topic, None]}])

            in_err  = incoming.get("error", {}).get("message", "")
            out_err = outgoing.get("error", {}).get("message", "")

            pdebug(f"getLogs in_err={repr(in_err)} out_err={repr(out_err)}")

            if any(e in in_err + out_err for e in ["beyond current head", "invalid block range"]):
                continue  # retry with larger offset

            if "error" in incoming:
                return [], incoming["error"].get("message", str(incoming["error"]))
            if "error" in outgoing:
                return [], outgoing["error"].get("message", str(outgoing["error"]))

            break  # success
        else:
            return [], "block range error after retries — node may be syncing"

        logs = incoming.get("result", []) + outgoing.get("result", [])
        transfers = []
        for log in logs:
            transfers.append({
                "blockNumber": int(log["blockNumber"], 16),
                "hash":        log["transactionHash"],
                "from":        "0x" + log["topics"][1][-40:],
                "to":          "0x" + log["topics"][2][-40:],
                "amount":      int(log["data"], 16) / 10 ** USDC_DECIMALS,
            })
        transfers.sort(key=lambda x: x["blockNumber"], reverse=True)
        return transfers[:50], ""
    except Exception as e:
        return [], str(e)


# ── Block timestamp ──────────────────────────────────────────────────────────

def block_timestamp(block_number: int) -> str:
    """Return human-readable timestamp for a block, cached to avoid repeat RPC calls."""
    if block_number in block_ts_cache:
        return block_ts_cache[block_number]
    try:
        result = rpc("eth_getBlockByNumber", [hex(block_number), False])
        ts = int(result["result"]["timestamp"], 16)
        formatted = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        formatted = now()  # fallback to current time on error
    block_ts_cache[block_number] = formatted
    return formatted


# ── Helpers ───────────────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 12 else addr

def p(text: str):
    """Plain print — no markup, no highlight."""
    console.print(text, markup=False, highlight=False)

def pdebug(text: str):
    """Print only when --debug is on."""
    if not DEBUG:
        return
    if COLOR:
        console.print(f"[dim]  [debug] {text}[/]", highlight=False)
    else:
        p(f"  [debug] {text}")


# ── Print ─────────────────────────────────────────────────────────────────────

def print_balance(balance: float):
    if balance <= LOW_THRESHOLD:
        flag, style = "[CRITICAL]", "bold red"
    elif balance <= WARN_THRESHOLD:
        flag, style = "[WARNING]", "bold yellow"
    else:
        flag, style = "", None

    line = f"{now()}  ${balance:,.6f} USDc  {flag}".rstrip()

    if COLOR and style:
        console.print(Text(line, style=style), markup=False, highlight=False)
    else:
        p(line)


def print_transfer_line(tx: dict, wallet: str, balance: float):
    """Print a single transfer as one log line."""
    wallet_lower = wallet.lower()
    is_in  = tx["to"].lower() == wallet_lower
    sign   = "+" if is_in else "-"
    line   = (f"{block_timestamp(tx['blockNumber'])}  block:{tx['blockNumber']}  "
              f"{sign}{tx['amount']:,.4f} USDc  |  Balance ${balance:,.6f}")
    p(line)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(wallet: str, interval: int):
    # Header
    if COLOR:
        console.print(Rule("[bold cyan]Base USDc Wallet Monitor[/]", style="cyan"))
    else:
        p("--- Base USDc Wallet Monitor ---")
    if not HIDE_WALLET:
        p(f"  wallet    {wallet}")
    p(f"  rpc       {BASE_RPCS[0]}  (+ {len(BASE_RPCS)-1} fallbacks)")
    p(f"  token     USDc")
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

    # Print initial balance label once
    p(f"\n{now()}  starting up, fetching initial balance...")

    while True:
        poll_n += 1
        pdebug(f"poll #{poll_n}  {now()}")

        # ── Balance
        balance, err = fetch_balance(wallet)
        if err:
            p(f"{now()}  error: {err}")
        else:
            if last_balance is None:
                # First reading — always print
                print_balance(balance)
                last_balance = balance
            elif balance != last_balance:
                # Balance changed — only print standalone line if no transfers caught it
                last_balance = balance
            else:
                pdebug(f"balance unchanged  ${balance:,.6f} USDc")

        # ── Transfers
        transfers, err = fetch_transfers(wallet)
        if err:
            p(f"{now()}  transfers error: {err}")
        else:
            pdebug(f"{len(transfers)} transfer(s) found in last {MAX_BLOCKS} blocks")
            if transfers:
                # Find transfers we have not printed yet
                new_txs = [tx for tx in transfers if tx["hash"] not in seen_hashes]
                if new_txs:
                    # Calculate running balance at each transfer:
                    # current balance already reflects all new txs, so work backwards
                    # then replay oldest-to-newest to get balance after each one
                    wallet_lower = wallet.lower()
                    sorted_new = sorted(new_txs, key=lambda x: x["blockNumber"])
                    total_signed = sum(
                        tx["amount"] if tx["to"].lower() == wallet_lower else -tx["amount"]
                        for tx in sorted_new
                    )
                    running = (balance if balance is not None else 0.0) - total_signed
                    for tx in sorted_new:
                        is_in   = tx["to"].lower() == wallet_lower
                        signed  = tx["amount"] if is_in else -tx["amount"]
                        running += signed
                        print_transfer_line(tx, wallet, running)
                        seen_hashes.add(tx["hash"])
                    # Keep seen_hashes from growing forever — drop oldest beyond 200
                    if len(seen_hashes) > 200:
                        seen_hashes.clear()
                        for tx in transfers:
                            seen_hashes.add(tx["hash"])
                else:
                    pdebug("no new transfers")
            else:
                pdebug(f"no transfers in last {MAX_BLOCKS} blocks")

        pdebug(f"sleeping {interval}s")
        time.sleep(interval)


def main():
    global COLOR, HIDE_WALLET, DEBUG, console

    parser = argparse.ArgumentParser(
        description="Base USDc wallet monitor via public Base RPC. No API key needed.",
    )
    parser.add_argument("--wallet",      required=True,        help="Base wallet address (0x...)")
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

    console = Console(no_color=not COLOR, highlight=False)

    if not args.wallet.startswith("0x") or len(args.wallet) != 42:
        p("Error: must be a valid 42-char Ethereum address starting with 0x")
        sys.exit(1)

    if args.interval < 5:
        p("Warning: using minimum interval of 5s.")
        args.interval = 5

    try:
        run(args.wallet, args.interval)
    except KeyboardInterrupt:
        console.print("\nStopped.", markup=False)


if __name__ == "__main__":
    main()
