# 🎮 Eldorado.gg Marketplace Monitor Bot

Real-time Telegram alerts whenever new offers appear on [Eldorado.gg](https://www.eldorado.gg/) — covering **Boosting, Currency, Accounts, and Items**.

## How It Works

1. **Scrapes** Eldorado.gg listing pages every 60 seconds using a headless browser (Playwright)
2. **Tracks** all seen offers in a local SQLite database
3. **Alerts** you via Telegram whenever a brand-new listing appears
4. On first run, it silently indexes existing offers so you only get notified about **truly new** listings

---

## Quick Setup (5 minutes)

### Step 1 — Create your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** it gives you (looks like `7123456789:AAH...`)

### Step 2 — Get your Chat ID

1. Search for **@userinfobot** on Telegram
2. Send it any message
3. It replies with your **Chat ID** (a number like `123456789`)

### Step 3 — Install & Configure

```bash
# Clone / copy this folder
cd eldorado_monitor

# Install Python dependencies
pip install -r requirements.txt

# Install browser for Playwright
playwright install chromium

# Create your config
cp .env.example .env
```

Now edit `.env` and paste your bot token and chat ID:

```
TELEGRAM_BOT_TOKEN=7123456789:AAHxxx...
TELEGRAM_CHAT_ID=123456789
```

### Step 4 — Run

```bash
python monitor.py
```

You should get a Telegram message: **"✅ Monitor started!"**

---

## Configuration

### Change Check Interval

Edit `.env`:
```
CHECK_INTERVAL=60    # seconds (60 = every minute)
```

### Add / Remove Monitored Pages

Open `monitor.py` and edit the `MONITORED_PAGES` list. Each entry is a tuple:
```python
("https://www.eldorado.gg/some-page/b/123", "Category › Game Name"),
```

You can add **any** Eldorado.gg listing page URL. Examples:
- Boosting: `https://www.eldorado.gg/valorant-boosting-services/b/32-4`
- Currency: `https://www.eldorado.gg/osrs-gold/g/10-0-0`
- Accounts: `https://www.eldorado.gg/fortnite-accounts-for-sale/a/16-1-0`
- Items: `https://www.eldorado.gg/cs2-skins/i/20-2-0`

### Run in Background (Linux/Mac)

Using `nohup`:
```bash
nohup python monitor.py &
```

Using `screen`:
```bash
screen -S eldorado
python monitor.py
# Press Ctrl+A then D to detach
```

Using `systemd` (recommended for servers):
```ini
# /etc/systemd/system/eldorado-monitor.service
[Unit]
Description=Eldorado.gg Monitor Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/eldorado_monitor
ExecStart=/usr/bin/python3 monitor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable eldorado-monitor
sudo systemctl start eldorado-monitor
```

---

## What the Alerts Look Like

```
🆕 New Listing!
━━━━━━━━━━━━━━━━
📂 Category: Boosting › Valorant
🏷️ Title: Rank Boost Iron to Diamond
💰 Price: $29.99
👤 Seller: ProBooster
⭐ Rating: 4.9
━━━━━━━━━━━━━━━━
🔗 View on Eldorado.gg
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Telegram error 401` | Double-check your bot token in `.env` |
| `Telegram error 400` | Double-check your chat ID in `.env` |
| No offers detected | The site may have changed its layout. Check `monitor.log` |
| Browser crashes | Try `HEADLESS=false` in `.env` to debug visually |
| Rate limited by site | Increase `CHECK_INTERVAL` to `120` or higher |

---

## Files

```
eldorado_monitor/
├── monitor.py          # Main bot script
├── requirements.txt    # Python dependencies
├── .env.example        # Config template
├── .env                # Your config (create from .env.example)
├── eldorado_offers.db  # Auto-created SQLite database
└── monitor.log         # Auto-created log file
```

## Requirements

- Python 3.10+
- ~200MB disk for Chromium browser
- Stable internet connection
