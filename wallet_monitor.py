#!/usr/bin/env python3
"""
Base USDc Wallet Monitor — streaming log style (like tail -f)
Uses the Base public RPC directly. No API key, no account, completely free.

Requirements:
    pip install rich requests

Usage:
    python wallet_monitor.py --wallet 0xYourAddress               # plain b&w (default)
    python wallet_monitor.py --wallet 0xYourAddress -c            # colors on
    python wallet_monitor.py --wallet 0xYourAddress --hide-wallet # hide wallet address
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
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
    "https://1rpc.io/base",
]

USDC_CONTRACT  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS  = 6
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WARN_THRESHOLD = 5.0
LOW_THRESHOLD  = 1.0
MAX_BLOCKS     = 1000

# Initialized in main() based on -c flag
console     = None
COLOR       = False
HIDE_WALLET = False
rpc_id      = 0


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
            return r.json()
        except Exception as e:
            last_err = e
            continue
    raise Exception(f"All RPCs failed. Last error: {last_err}")


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
        from_block   = hex(max(0, latest - MAX_BLOCKS))
        to_block     = hex(latest)
        wallet_topic = "0x" + wallet[2:].lower().zfill(64)

        incoming = rpc("eth_getLogs", [{"fromBlock": from_block, "toBlock": to_block,
            "address": USDC_CONTRACT, "topics": [TRANSFER_TOPIC, None, wallet_topic]}])
        outgoing = rpc("eth_getLogs", [{"fromBlock": from_block, "toBlock": to_block,
            "address": USDC_CONTRACT, "topics": [TRANSFER_TOPIC, wallet_topic, None]}])

        if "error" in incoming:
            return [], incoming["error"].get("message", str(incoming["error"]))
        if "error" in outgoing:
            return [], outgoing["error"].get("message", str(outgoing["error"]))

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
        return transfers[:10], ""
    except Exception as e:
        return [], str(e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 12 else addr

def p(text: str):
    """Plain print — bypasses rich markup entirely."""
    console.print(text, markup=False, highlight=False)


# ── Print ─────────────────────────────────────────────────────────────────────

def print_balance(balance: float):
    if balance <= LOW_THRESHOLD:
        flag = "[CRITICAL]"
    elif balance <= WARN_THRESHOLD:
        flag = "[WARNING]"
    else:
        flag = ""

    if COLOR and flag:
        style = "bold red" if balance <= LOW_THRESHOLD else "bold yellow"
        console.print(
            Text(f"  balance   ${balance:,.6f} USDc  {flag}", style=style),
            markup=False, highlight=False,
        )
    else:
        p(f"  balance   ${balance:,.6f} USDc  {flag}".rstrip())


def print_transfers(transfers: list, wallet: str):
    if not transfers:
        p(f"  no transfers found in last {MAX_BLOCKS} blocks")
        return

    wallet_lower = wallet.lower()

    table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1),
                  highlight=False, header_style="" )
    table.add_column("Block",   width=10)
    table.add_column("Tx Hash", width=14)
    table.add_column("From",    width=14)
    table.add_column("",        width=2, justify="center")
    table.add_column("To",      width=14)
    table.add_column("Amount",  justify="right")

    for tx in transfers:
        is_in = tx["to"].lower() == wallet_lower
        if COLOR:
            arrow = Text("<-" if is_in else "->", style="bold green" if is_in else "bold red")
            amt_t = Text(f"{tx['amount']:,.4f} USDc", style="bold green" if is_in else "bold red")
        else:
            arrow = Text("<-" if is_in else "->")
            amt_t = Text(f"{tx['amount']:,.4f} USDc")
        table.add_row(
            str(tx["blockNumber"]),
            short(tx["hash"]),
            short(tx["from"]),
            arrow,
            short(tx["to"]),
            amt_t,
        )
    console.print(table, highlight=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(wallet: str, interval: int):
    if COLOR:
        console.print(Rule("[bold cyan]Base USDc Wallet Monitor[/]", style="cyan"))
    else:
        p("--- Base USDc Wallet Monitor ---")
    if not HIDE_WALLET:
        p(f"  wallet    {wallet}")
    p(f"  rpc       {BASE_RPCS[0]}  (+ {len(BASE_RPCS)-1} fallbacks)")
    p(f"  token     USDc")
    p(f"  interval  {interval}s")
    if COLOR:
        console.print(Rule(style="cyan dim"))
    else:
        p("--------------------------------")

    poll_n = 0

    while True:
        poll_n += 1
        if COLOR:
            console.print(f"\n[cyan]-- poll #{poll_n}  {now()} --[/]")
        else:
            p(f"\n-- poll #{poll_n}  {now()} --")

        balance, err = fetch_balance(wallet)
        if err:
            p(f"  balance error: {err}")
        else:
            print_balance(balance)

        transfers, err = fetch_transfers(wallet)
        if err:
            p(f"  transfers error: {err}")
        else:
            p(f"  transfers  ({len(transfers)} found in last {MAX_BLOCKS} blocks)")
            print_transfers(transfers, wallet)

        p(f"  next poll in {interval}s -- Ctrl-C to quit")
        time.sleep(interval)


def main():
    global COLOR, HIDE_WALLET, console

    parser = argparse.ArgumentParser(
        description="Base USDc wallet monitor via public Base RPC. No API key needed.",
    )
    parser.add_argument("--wallet",      required=True,        help="Base wallet address (0x...)")
    parser.add_argument("--interval",    type=int, default=15, help="Poll interval in seconds (default: 15)")
    parser.add_argument("-c",            dest="color",         action="store_true", default=False,
                                         help="Enable colors (default: plain b&w)")
    parser.add_argument("--hide-wallet", dest="hide_wallet",   action="store_true", default=False,
                                         help="Don't print wallet address in output")
    args = parser.parse_args()

    COLOR       = args.color
    HIDE_WALLET = args.hide_wallet

    # Key fix: when no color, create a Console with no_color=True which strips
    # ALL rich styling including auto-highlighting of URLs, hex strings, etc.
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
