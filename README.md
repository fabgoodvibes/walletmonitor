# Base USDc Wallet Monitor

Real-time CLI dashboard for your Base USDc balance. Polls Basescan directly — no platform dependency.

## Usage

```bash
git clone <your-repo-url>
cd <repo>
chmod +x run.sh
./run.sh --wallet 0xYourWalletAddress
```

That's it. The first run creates a virtual environment and installs dependencies automatically. Every subsequent run just launches the monitor.

## Options

```
--wallet        Base wallet address, 0x...  (required)
--interval      Poll interval in seconds    (default: 15, min: 5)
--basescan-key  Basescan API key            (optional — get one free at basescan.org
                                             if you hit rate limits)
```

## Requirements

- Python 3.6+
- Internet access to basescan.org
