#!/usr/bin/env python3
"""
Eldorado.gg Marketplace Monitor Bot
====================================
Monitors all categories (Currency, Accounts, Items, Boosting)
on eldorado.gg and sends Telegram alerts for new listings.

Uses Playwright for JS-rendered page scraping.
Uses SQLite to track seen offers and avoid duplicate alerts.
"""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from playwright.async_api import async_playwright, Page, Browser
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration ───────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))       # seconds
MAX_ALERTS_PER_CHECK = int(os.getenv("MAX_ALERTS", "15"))     # prevent flood
DB_PATH = os.getenv("DB_PATH", "eldorado_offers.db")
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# ─── Pages to Monitor ────────────────────────────────────────────────────────
# Add or remove any URL + label pair. The bot will scrape each one every cycle.

MONITORED_PAGES = [
    # ── BOOSTING ──
    ("https://www.eldorado.gg/valorant-boosting-services/b/32-4",   "Boosting › Valorant"),
    ("https://www.eldorado.gg/league-of-legends-boosting/b/17-4",   "Boosting › LoL"),
    ("https://www.eldorado.gg/rocket-league-boosting/b/1-4",        "Boosting › Rocket League"),
    ("https://www.eldorado.gg/r6-boosting/b/48-4",                  "Boosting › R6 Siege"),
    ("https://www.eldorado.gg/apex-legends-boosting/b/33-4",        "Boosting › Apex Legends"),
    ("https://www.eldorado.gg/marvel-rivals-boosting/b/227",         "Boosting › Marvel Rivals"),
    ("https://www.eldorado.gg/overwatch-boosting-services/b/27-4",   "Boosting › Overwatch"),
    ("https://www.eldorado.gg/call-of-duty-boosting/b/35-4",        "Boosting › CoD"),
    ("https://www.eldorado.gg/brawl-stars-boosting/b/56-4",         "Boosting › Brawl Stars"),
    ("https://www.eldorado.gg/ea-fc-boosting/b/142-4",              "Boosting › EA FC"),

    # ── CURRENCY ──
    ("https://www.eldorado.gg/osrs-gold/g/10-0-0",                  "Currency › OSRS Gold"),
    ("https://www.eldorado.gg/buy-wow-gold/g/0-0-0",                "Currency › WoW Gold"),
    ("https://www.eldorado.gg/ea-fc-coins/g/142-0-0",               "Currency › EA FC Coins"),
    ("https://www.eldorado.gg/poe-currency/g/2-0-0",                "Currency › PoE Currency"),
    ("https://www.eldorado.gg/buy-robux/g/70-0-0",                  "Currency › Robux"),
    ("https://www.eldorado.gg/poe-2-currency/g/220",                "Currency › PoE 2"),
    ("https://www.eldorado.gg/wow-classic-gold/g/92-0-0",           "Currency › WoW Classic"),

    # ── ACCOUNTS ──
    ("https://www.eldorado.gg/fortnite-accounts-for-sale/a/16-1-0", "Accounts › Fortnite"),
    ("https://www.eldorado.gg/valorant-accounts/a/32-1-0",          "Accounts › Valorant"),
    ("https://www.eldorado.gg/gta-5-modded-accounts/a/25-1-0",      "Accounts › GTA 5"),
    ("https://www.eldorado.gg/league-of-legends-accounts-for-sale/a/17-1-0", "Accounts › LoL"),
    ("https://www.eldorado.gg/roblox-accounts-for-sale/a/70-1-0",   "Accounts › Roblox"),
    ("https://www.eldorado.gg/cs2-accounts/a/20-1-0",               "Accounts › CS2"),

    # ── ITEMS ──
    ("https://www.eldorado.gg/cs2-skins/i/20-2-0",                  "Items › CS2 Skins"),
    ("https://www.eldorado.gg/blox-fruits-shop/i/202-2-0",          "Items › Blox Fruits"),
    ("https://www.eldorado.gg/adopt-me-pets/i/201-2-0",             "Items › Adopt Me"),
    ("https://www.eldorado.gg/mm2-shop/i/204-2-0",                  "Items › MM2"),
    ("https://www.eldorado.gg/roblox-items/i/70-2-0",               "Items › Roblox"),
]


# ─── Data Model ──────────────────────────────────────────────────────────────

@dataclass
class Offer:
    offer_id: str
    title: str
    price: str
    seller: str
    category: str
    url: str
    rating: str = ""
    delivery_time: str = ""
    extra_info: str = ""
    first_seen: str = ""


# ─── Database ────────────────────────────────────────────────────────────────

class OffersDB:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_offers (
                offer_id   TEXT PRIMARY KEY,
                title      TEXT,
                price      TEXT,
                seller     TEXT,
                category   TEXT,
                url        TEXT,
                first_seen TEXT
            )
        """)
        self.conn.commit()

    def is_new(self, offer_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen_offers WHERE offer_id = ?", (offer_id,)
        )
        return cur.fetchone() is None

    def mark_seen(self, offer: Offer):
        self.conn.execute(
            """INSERT OR IGNORE INTO seen_offers
               (offer_id, title, price, seller, category, url, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (offer.offer_id, offer.title, offer.price,
             offer.seller, offer.category, offer.url, offer.first_seen),
        )
        self.conn.commit()

    def total_tracked(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM seen_offers").fetchone()[0]

    def close(self):
        self.conn.close()


# ─── Telegram ────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramNotifier:
    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    async def _send(self, text: str):
        url = self.API.format(token=self.token, method="sendMessage")
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        logging.error(f"Telegram error {r.status}: {await r.text()}")
        except Exception as e:
            logging.error(f"Telegram send failed: {e}")

    async def alert(self, offer: Offer):
        msg = (
            f"🆕 <b>New Listing!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📂 <b>Category:</b> {_esc(offer.category)}\n"
            f"🏷️ <b>Title:</b> {_esc(offer.title)}\n"
            f"💰 <b>Price:</b> {_esc(offer.price)}\n"
            f"👤 <b>Seller:</b> {_esc(offer.seller)}\n"
        )
        if offer.rating:
            msg += f"⭐ <b>Rating:</b> {_esc(offer.rating)}\n"
        if offer.delivery_time:
            msg += f"⏱️ <b>Delivery:</b> {_esc(offer.delivery_time)}\n"
        if offer.extra_info:
            msg += f"ℹ️ {_esc(offer.extra_info[:200])}\n"
        msg += f"━━━━━━━━━━━━━━━━\n🔗 <a href=\"{offer.url}\">View on Eldorado.gg</a>"
        await self._send(msg)

    async def status(self, text: str):
        await self._send(f"🤖 <b>Eldorado Monitor</b>\n{_esc(text)}")


# ─── Scraper ─────────────────────────────────────────────────────────────────

class EldoradoScraper:
    def __init__(self):
        self.browser: Optional[Browser] = None
        self._pw = None

    async def start(self):
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        logging.info("Browser launched")

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    async def scrape_page(self, url: str, category: str) -> list[Offer]:
        offers = []
        page = None
        try:
            page = await self.browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                )
            )
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Try common selectors for the offer cards
            for sel in [
                "[class*='OfferCard']", "[class*='offerCard']",
                "[class*='offer-card']", "[class*='listing-card']",
                "[class*='ProductCard']", "[class*='product-card']",
                "a[href*='/offer/']", "[class*='Card']",
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=4000)
                    break
                except Exception:
                    continue

            # Extract all offers using JS
            raw = await page.evaluate("""
                () => {
                    const results = [];
                    const seen = new Set();

                    // Find all links pointing to offers
                    const links = document.querySelectorAll('a[href]');
                    for (const a of links) {
                        const href = a.href || '';
                        // Skip nav, footer, tiny links
                        if (!href.includes('eldorado.gg')) continue;
                        const rect = a.getBoundingClientRect();
                        if (rect.height < 40 || rect.width < 100) continue;

                        const text = (a.innerText || '').trim();
                        if (text.length < 5) continue;

                        // De-dup by href
                        if (seen.has(href)) continue;
                        seen.add(href);

                        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);

                        // Extract price
                        const priceMatch = text.match(
                            /(?:USD|€|\\$)\\s*[\\d.,]+|[\\d.,]+\\s*(?:USD|€|\\$)/i
                        );

                        // Extract rating
                        const ratingMatch = text.match(/(\\d\\.\\d+)\\s*[★⭐/]/);

                        results.push({
                            title: lines[0] || '',
                            price: priceMatch ? priceMatch[0] : '',
                            seller: lines.find(l => l.match(/^[A-Za-z0-9_]{3,20}$/)) || '',
                            rating: ratingMatch ? ratingMatch[1] : '',
                            link: href,
                            info: lines.slice(0, 4).join(' · '),
                        });
                    }

                    // Also try: scrape table rows or card divs
                    if (results.length < 3) {
                        const cards = document.querySelectorAll(
                            '[class*="offer"], [class*="Offer"], [class*="card"], [class*="Card"]'
                        );
                        for (const c of cards) {
                            const text = (c.innerText || '').trim();
                            const rect = c.getBoundingClientRect();
                            if (rect.height < 40 || text.length < 10) continue;

                            const anchor = c.querySelector('a[href]');
                            const href = anchor ? anchor.href : '';
                            if (seen.has(href || text)) continue;
                            seen.add(href || text);

                            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                            const priceMatch = text.match(
                                /(?:USD|€|\\$)\\s*[\\d.,]+|[\\d.,]+\\s*(?:USD|€|\\$)/i
                            );

                            results.push({
                                title: lines[0] || '',
                                price: priceMatch ? priceMatch[0] : '',
                                seller: '',
                                rating: '',
                                link: href,
                                info: lines.slice(0, 4).join(' · '),
                            });
                        }
                    }

                    return results.slice(0, 50);
                }
            """)

            now = datetime.now(timezone.utc).isoformat()
            for item in raw:
                title = (item.get("title") or "Unknown")[:200]
                price = item.get("price") or "N/A"
                link = item.get("link") or url
                uid = hashlib.sha256(f"{link}|{title}|{price}".encode()).hexdigest()[:16]

                offers.append(Offer(
                    offer_id=uid,
                    title=title,
                    price=price,
                    seller=item.get("seller") or "—",
                    category=category,
                    url=link,
                    rating=item.get("rating") or "",
                    extra_info=(item.get("info") or "")[:300],
                    first_seen=now,
                ))

        except Exception as e:
            logging.error(f"Scrape error [{category}]: {e}")
        finally:
            if page:
                await page.close()
        return offers


# ─── Main Loop ───────────────────────────────────────────────────────────────

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("monitor.log"),
        ],
    )

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("=" * 55)
        print("  ERROR: Telegram credentials not set!")
        print("  1) Copy .env.example → .env")
        print("  2) Fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        print("  See README.md for step-by-step instructions.")
        print("=" * 55)
        sys.exit(1)

    db = OffersDB(DB_PATH)
    tg = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    scraper = EldoradoScraper()

    try:
        await scraper.start()
        await tg.status(
            f"✅ Monitor started!\n"
            f"Watching {len(MONITORED_PAGES)} pages\n"
            f"Interval: every {CHECK_INTERVAL}s"
        )

        first_run = db.total_tracked() == 0
        cycle = 0

        while True:
            cycle += 1
            logging.info(f"━━ Cycle #{cycle} ━━")
            new_total = 0

            for url, cat in MONITORED_PAGES:
                logging.info(f"  → {cat}")
                offers = await scraper.scrape_page(url, cat)
                logging.info(f"    {len(offers)} offers found")

                new = [o for o in offers if db.is_new(o.offer_id)]

                if first_run:
                    for o in offers:
                        db.mark_seen(o)
                else:
                    sent = 0
                    for o in new:
                        db.mark_seen(o)
                        if sent < MAX_ALERTS_PER_CHECK:
                            await tg.alert(o)
                            sent += 1
                            await asyncio.sleep(0.4)
                    new_total += len(new)
                    if sent:
                        logging.info(f"    📨 {sent} alerts sent")

                await asyncio.sleep(1.5)  # polite delay between pages

            if first_run:
                first_run = False
                t = db.total_tracked()
                await tg.status(f"📊 Initial scan done! Indexed {t} existing offers.\nNow alerting on NEW listings only.")
                logging.info(f"First run complete — {t} offers indexed")
            else:
                logging.info(f"━━ Cycle #{cycle} done — {new_total} new ━━")

            await asyncio.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logging.info("Stopped by user")
        await tg.status("🛑 Monitor stopped.")
    except Exception as e:
        logging.error(f"Fatal: {e}", exc_info=True)
        await tg.status(f"❌ Crashed: {e}")
    finally:
        await scraper.stop()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
