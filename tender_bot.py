#!/usr/bin/env python3
"""
Tender & Bid Telegram Monitor for Media & Communication Agencies
Monitors:
  1. 2Merkato Tenders (https://tender.2merkato.com/tenders)
  2. EthioJobs Consultancies/RFPs & Media Jobs (https://ethiojobs.net/jobs)
  3. The Reporter Tenders (https://reportertenders.com/all-tenders/ & Telegram feed)
  4. AfroTender (https://afrotender.com/tenders)
"""

import os
import sys
import asyncio
import logging
import sqlite3
import re
import json
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# Load environment variables if available
load_dotenv()

# ==============================================================================
# ⚙️ EASY CONFIGURATION
# ==============================================================================
# 1. Your Telegram Bot Token:
MY_BOT_TOKEN = "8772423781:AAHYbS4JNdPwYwxiAF0mskFO1IPIEXLlJFs"

# 2. Your Telegram Chat ID:
MY_CHAT_ID = "5337175992"

# Scan interval in minutes (30 minutes)
SCAN_INTERVAL_MINUTES = 30
DATABASE_PATH = "tenders.db"

# Lookback settings: Scan up to 3 days back across multiple pages
LOOKBACK_DAYS = 3
MAX_PAGES_TO_SCAN = 3

# Resolve config (prioritizes code variables, falls back to .env)
BOT_TOKEN = MY_BOT_TOKEN.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
AUTHORIZED_CHAT_ID = MY_CHAT_ID.strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Default Media & Communication Keywords
DEFAULT_MEDIA_KEYWORDS = [
    # English keywords
    "media",
    "communication",
    "communications",
    "video production",
    "video",
    "documentary",
    "advertising",
    "advertisement",
    "public relations",
    "pr firm",
    "branding",
    "graphic design",
    "graphics",
    "social media",
    "creative agency",
    "tv commercial",
    "radio spot",
    "radio",
    "television",
    "audio visual",
    "audiovisual",
    "animation",
    "photography",
    "photo",
    "printing",
    "event management",
    "event organizing",
    "campaign",
    "content creation",
    "translation",
    "signage",
    "billboard",
    "press conference",
    "consultancy",
    "tor",
    "rfp",
    # Amharic keywords (common on 2Merkato & Reporter)
    "ማስታወቂያ",
    "ህትመት",
    "ሕትመት",
    "ሚዲያ",
    "ኮሙኒኬሽን",
    "ኮሚዩኒኬሽን",
    "የህዝብ ግንኙነት",
    "ቪዲዮ",
    "ዘጋቢ ፊልም",
    "ዶክመንተሪ",
    "ፎቶ",
    "ፎቶግራፍ",
    "ግራፊክስ",
    "ዲዛይን",
    "ስልጠና",
    "ዝግጅት"
]

# Configure Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TenderBot")

# ==========================================
# DATA MODELS
# ==========================================
@dataclass
class Tender:
    source: str
    title: str
    url: str
    organization: str
    deadline: str
    category: str
    snippet: str
    matched_keywords: List[str]
    posted_date: str = "Recent"


# ==========================================
# DATABASE REPOSITORY (SQLite)
# ==========================================
class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS seen_tenders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    tender_url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    organization TEXT,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT UNIQUE NOT NULL
                )
            """)
            conn.commit()

            cursor.execute("SELECT COUNT(*) FROM keywords")
            count = cursor.fetchone()[0]
            if count == 0:
                cursor.executemany(
                    "INSERT OR IGNORE INTO keywords (keyword) VALUES (?)",
                    [(kw.lower(),) for kw in DEFAULT_MEDIA_KEYWORDS]
                )
                conn.commit()
            else:
                for kw in DEFAULT_MEDIA_KEYWORDS:
                    cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (kw.lower(),))
                conn.commit()

    def is_tender_seen(self, url: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM seen_tenders WHERE tender_url = ?", (url,))
            return cursor.fetchone() is not None

    def mark_tender_seen(self, tender: Tender):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO seen_tenders (source, tender_url, title, organization)
                VALUES (?, ?, ?, ?)
            """, (tender.source, tender.url, tender.title, tender.organization))
            conn.commit()

    def get_keywords(self) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT keyword FROM keywords ORDER BY keyword ASC")
            return [row[0] for row in cursor.fetchall()]

    def add_keyword(self, keyword: str) -> bool:
        kw = keyword.strip().lower()
        if not kw:
            return False
        with sqlite3.connect(self.db_path) as conn:
            try:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO keywords (keyword) VALUES (?)", (kw,))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_keyword(self, keyword: str) -> bool:
        kw = keyword.strip().lower()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM keywords WHERE keyword = ?", (kw,))
            conn.commit()
            return cursor.rowcount > 0

    def get_stats(self) -> Dict[str, int]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM seen_tenders")
            total_seen = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM keywords")
            total_keywords = cursor.fetchone()[0]
            return {"total_seen": total_seen, "total_keywords": total_keywords}


# ==========================================
# SCRAPERS & PARSERS
# ==========================================
class TenderScraper:
    def __init__(self, db: Database):
        self.db = db
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,am;q=0.8",
        }

    def _is_category_or_nav(self, title: str, url: str) -> bool:
        """Filter out generic category taxonomy pages, menus, and navigation links."""
        url_lower = url.lower()
        title_lower = title.strip().lower()

        # 1. URL patterns indicating archives, categories, tags, or system pages
        junk_url_parts = [
            "/category/", "/categories/", "/tender-category/", "/job-category/",
            "/services/", "/tag/", "/tags/", "/sector/", "/browse",
            "/page/", "/author/", "/login", "/register", "/signup",
            "/about", "/contact", "/privacy", "/terms", "/pricing", "/feed"
        ]
        if any(junk in url_lower for junk in junk_url_parts):
            return True

        # 2. Titles that are purely general taxonomy/category headers
        words = title_lower.split()
        if len(words) <= 4:
            category_endings = ["service", "services", "category", "categories", "tenders", "jobs", "vacancies", "sector"]
            if words and words[-1] in category_endings:
                return True
            if title_lower in [
                "printing & publishing",
                "printing & publishing service",
                "advertising & public relations",
                "media & communication",
                "consultancy & training",
                "consultancy services",
                "all tenders",
                "free tenders",
                "latest tenders"
            ]:
                return True

        # 3. Discard overly brief titles that lack organizational or procurement context
        if len(title.strip()) < 18:
            return True

        return False

    def _is_within_lookback(self, date_str: str, max_days: int = LOOKBACK_DAYS) -> bool:
        if not date_str:
            return True
        text = date_str.lower().strip()
        if any(term in text for term in ["today", "hour", "minute", "just now", "yesterday", "1 day", "2 day", "3 day", "recent"]):
            return True
        if any(f"{d} day" in text for d in range(4, 32)) or any(f"{w} week" in text for w in range(1, 12)) or "month" in text:
            return False

        try:
            date_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
            if date_match:
                year, month, day = map(int, date_match.groups())
                dt = datetime(year, month, day)
                diff = (datetime.now() - dt).days
                return -1 <= diff <= max_days
        except Exception:
            pass
        return True

    def _match_keywords(self, text: str, keywords: List[str]) -> List[str]:
        text_lower = text.lower()
        matches = []
        for kw in keywords:
            kw_clean = kw.strip().lower()
            if not kw_clean:
                continue
            if any(ord(char) > 127 for char in kw_clean):
                if kw_clean in text_lower:
                    matches.append(kw_clean)
            else:
                pattern = r"\b" + re.escape(kw_clean) + r"\b"
                if re.search(pattern, text_lower):
                    matches.append(kw_clean)
        return matches

    # 1. 2MERKATO TENDERS (Main + Free Tenders)
    async def fetch_2merkato(self, client: httpx.AsyncClient, keywords: Optional[List[str]] = None, ignore_seen: bool = False) -> List[Tender]:
        tenders = []
        urls = [
            "https://tender.2merkato.com/tenders/free",
            "https://tender.2merkato.com/tenders",
        ]
        
        for url in urls:
            try:
                resp = await client.get(url, headers=self.headers, timeout=25.0)
                if resp.status_code != 200:
                    logger.warning(f"2Merkato returned status {resp.status_code} for {url}")
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.find_all("a", href=True)
                for link in links:
                    href = link["href"]
                    if not ("/tender" in href or "/tenders/view" in href):
                        continue

                    title = link.get_text(separator=" ", strip=True)
                    tender_url = href if href.startswith("http") else f"https://tender.2merkato.com{href}"

                    if self._is_category_or_nav(title, tender_url):
                        continue

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(tender_url):
                        continue

                    parent = link.find_parent("tr") or link.find_parent("li") or link.find_parent("div")
                    parent_text = parent.get_text(separator=" ", strip=True) if parent else title

                    if keywords is not None and not self._is_within_lookback(parent_text):
                        continue

                    matches = self._match_keywords(f"{title} {parent_text}", keywords) if keywords else ["latest"]
                    if matches:
                        deadline_match = re.search(r"Bid closing date:\s*([^.\n\r]+)", parent_text, re.IGNORECASE)
                        deadline = deadline_match.group(1).strip() if deadline_match else "See tender notice"

                        tenders.append(Tender(
                            source="2Merkato",
                            title=title,
                            url=tender_url,
                            organization="2Merkato Listed Organization",
                            deadline=deadline,
                            category="Tender",
                            snippet=parent_text[:280] + ("..." if len(parent_text) > 280 else ""),
                            matched_keywords=matches,
                            posted_date="Recent"
                        ))
            except Exception as e:
                logger.error(f"Error scraping 2Merkato ({url}): {e}")
        return tenders

    # 2. ETHIOJOBS (Jobs, Consultancies, RFPs & ToRs)
    async def fetch_ethiojobs(self, client: httpx.AsyncClient, keywords: Optional[List[str]] = None, ignore_seen: bool = False) -> List[Tender]:
        tenders = []
        seen_job_urls = set()

        # A. Directly query EthioJobs REST API if available
        api_urls = [
            "https://api.ethiojobs.net/ethiojobs/jobs?page=1&perPage=40",
            "https://api.ethiojobs.net/ethiojobs/jobs"
        ]
        api_headers = {
            **self.headers,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://ethiojobs.net/"
        }

        for api_url in api_urls:
            try:
                resp = await client.get(api_url, headers=api_headers, timeout=20.0)
                if resp.status_code == 200:
                    payload = resp.json()
                    job_items = payload.get("data") or payload.get("jobs") or (payload if isinstance(payload, list) else [])
                    for item in job_items:
                        if not isinstance(item, dict):
                            continue
                        title = item.get("title") or item.get("jobTitle") or ""
                        slug = item.get("slug") or item.get("id") or ""
                        if not title or not slug:
                            continue

                        job_url = f"https://ethiojobs.net/job/{slug}"
                        if job_url in seen_job_urls:
                            continue
                        seen_job_urls.add(job_url)

                        if self._is_category_or_nav(title, job_url):
                            continue
                        if not ignore_seen and keywords is not None and self.db.is_tender_seen(job_url):
                            continue

                        company = item.get("companyName") or item.get("employer") or (item.get("company", {})).get("name") or "Organization on EthioJobs"
                        description = item.get("description") or item.get("summary") or title
                        deadline = item.get("deadline") or item.get("closingDate") or "Check posting"
                        posted = str(item.get("createdAt") or item.get("postedDate") or "Recent")[:10]

                        full_search_text = f"{title} {company} {description}"
                        matches = self._match_keywords(full_search_text, keywords) if keywords else ["latest"]
                        if matches:
                            clean_snippet = re.sub(r"<[^>]+>", " ", description).strip()
                            tenders.append(Tender(
                                source="EthioJobs",
                                title=title,
                                url=job_url,
                                organization=str(company),
                                deadline=str(deadline),
                                category="Consultancy / RFP / ToR",
                                snippet=clean_snippet[:280] + ("..." if len(clean_snippet) > 280 else ""),
                                matched_keywords=matches,
                                posted_date=posted
                            ))
            except Exception as e:
                logger.debug(f"EthioJobs API probe note: {e}")

        # B. HTML Scraper + Next.js __NEXT_DATA__ JSON extractor
        web_urls = [
            "https://ethiojobs.net/jobs",
            "https://ethiojobs.net/jobs/consultancy-and-training"
        ]

        for w_url in web_urls:
            try:
                resp = await client.get(w_url, headers=self.headers, timeout=25.0)
                if resp.status_code != 200:
                    logger.warning(f"EthioJobs HTML returned status {resp.status_code} for {w_url}")
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")

                # Check Next.js __NEXT_DATA__ embedded JSON
                next_script = soup.find("script", id="__NEXT_DATA__")
                if next_script and next_script.string:
                    try:
                        next_json = json.loads(next_script.string)
                        page_props = next_json.get("props", {}).get("pageProps", {})
                        embedded_jobs = page_props.get("jobs") or page_props.get("initialState", {}).get("jobs", [])
                        if isinstance(embedded_jobs, list):
                            for ej in embedded_jobs:
                                if not isinstance(ej, dict):
                                    continue
                                title = ej.get("title") or ej.get("jobTitle") or ""
                                slug = ej.get("slug") or ej.get("id") or ""
                                if not title or not slug:
                                    continue

                                job_url = f"https://ethiojobs.net/job/{slug}"
                                if job_url in seen_job_urls:
                                    continue
                                seen_job_urls.add(job_url)

                                if self._is_category_or_nav(title, job_url):
                                    continue
                                if not ignore_seen and keywords is not None and self.db.is_tender_seen(job_url):
                                    continue

                                company = ej.get("companyName") or (ej.get("company", {})).get("name") or "Organization on EthioJobs"
                                deadline = ej.get("deadline") or "Check posting"
                                matches = self._match_keywords(title, keywords) if keywords else ["latest"]
                                if matches:
                                    tenders.append(Tender(
                                        source="EthioJobs",
                                        title=title,
                                        url=job_url,
                                        organization=str(company),
                                        deadline=str(deadline),
                                        category="Consultancy / RFP / ToR",
                                        snippet=title,
                                        matched_keywords=matches,
                                        posted_date="Recent"
                                    ))
                    except Exception as err:
                        logger.debug(f"EthioJobs Next.js parse note: {err}")

                # Standard HTML <a> tag inspection (matches /job/<id>)
                links = soup.find_all("a", href=True)
                for link in links:
                    href = link["href"]
                    if not ("/job/" in href or "/jobs/" in href):
                        continue

                    title = link.get_text(separator=" ", strip=True)
                    job_url = href if href.startswith("http") else f"https://ethiojobs.net{href}"

                    if job_url in seen_job_urls or self._is_category_or_nav(title, job_url):
                        continue
                    seen_job_urls.add(job_url)

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(job_url):
                        continue

                    parent = link.find_parent("article") or link.find_parent("li") or link.find_parent("div")
                    parent_text = parent.get_text(separator=" ", strip=True) if parent else title

                    matches = self._match_keywords(f"{title} {parent_text}", keywords) if keywords else ["latest"]
                    if matches:
                        tenders.append(Tender(
                            source="EthioJobs",
                            title=title,
                            url=job_url,
                            organization="Organization on EthioJobs",
                            deadline="Check posting",
                            category="Consultancy / RFP / ToR",
                            snippet=parent_text[:280] + ("..." if len(parent_text) > 280 else ""),
                            matched_keywords=matches,
                            posted_date="Recent"
                        ))
            except Exception as e:
                logger.error(f"Error scraping EthioJobs ({w_url}): {e}")
        return tenders

    # 3. THE REPORTER & TELEGRAM TENDERS FEED
    async def fetch_the_reporter(self, client: httpx.AsyncClient, keywords: Optional[List[str]] = None, ignore_seen: bool = False) -> List[Tender]:
        tenders = []
        # Telegram Web feeds that mirror Reporter & Ethiopian tenders live:
        channels = [
            "https://t.me/s/reportertender",
            "https://t.me/s/tender_ethiopia"
        ]

        for ch_url in channels:
            try:
                resp = await client.get(ch_url, headers=self.headers, timeout=25.0)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                messages = soup.select(".tgme_widget_message_wrap")

                for msg_box in messages:
                    text_el = msg_box.select_one(".tgme_widget_message_text")
                    link_el = msg_box.select_one(".tgme_widget_message_date")
                    if not text_el or not link_el:
                        continue

                    time_tag = msg_box.select_one("time")
                    posted = "Recent"
                    if time_tag and time_tag.get("datetime"):
                        dt_str = time_tag["datetime"]
                        posted = dt_str[:10]
                        if keywords is not None and not self._is_within_lookback(dt_str):
                            continue

                    full_text = text_el.get_text(separator="\n", strip=True)
                    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
                    title = lines[0] if lines else "Tender Notice"
                    post_url = link_el.get("href", ch_url)

                    # Avoid category or channel navigation posts
                    if self._is_category_or_nav(title, post_url):
                        continue

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(post_url):
                        continue

                    matches = self._match_keywords(full_text, keywords) if keywords else ["latest"]
                    if matches:
                        tenders.append(Tender(
                            source="The Reporter",
                            title=title[:140],
                            url=post_url,
                            organization="The Reporter Classifieds",
                            deadline="Refer to notice",
                            category="Tender Announcement",
                            snippet=full_text[:280] + ("..." if len(full_text) > 280 else ""),
                            matched_keywords=matches,
                            posted_date=posted
                        ))
            except Exception as e:
                logger.warning(f"Error fetching channel {ch_url}: {e}")

        # Direct reportertenders.com inspection (filtering out category archives)
        try:
            r_resp = await client.get("https://reportertenders.com/", headers=self.headers, timeout=20.0)
            if r_resp.status_code == 200:
                soup = BeautifulSoup(r_resp.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    title = link.get_text(strip=True)
                    tender_url = href if href.startswith("http") else f"https://reportertenders.com{href}"

                    # Explicitly discard category taxonomy pages (e.g. Printing & Publishing Service)
                    if self._is_category_or_nav(title, tender_url):
                        continue

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(tender_url):
                        continue

                    matches = self._match_keywords(title, keywords) if keywords else ["latest"]
                    if matches:
                        tenders.append(Tender(
                            source="The Reporter",
                            title=title,
                            url=tender_url,
                            organization="ReporterTenders",
                            deadline="See link",
                            category="Tender Notice",
                            snippet=title,
                            matched_keywords=matches,
                            posted_date="Recent"
                        ))
        except Exception as e:
            logger.debug(f"Reporter direct check: {e}")

        return tenders

    # 4. AFROTENDER (https://afrotender.com)
    async def fetch_afrotender(self, client: httpx.AsyncClient, keywords: Optional[List[str]] = None, ignore_seen: bool = False) -> List[Tender]:
        tenders = []
        urls = [
            "https://afrotender.com/tenders",
            "https://afrotender.com"
        ]
        for url in urls:
            try:
                resp = await client.get(url, headers=self.headers, timeout=25.0)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.find_all("a", href=True)

                for link in links:
                    href = link["href"]
                    title = link.get_text(separator=" ", strip=True)
                    tender_url = href if href.startswith("http") else f"https://afrotender.com{href}"

                    if self._is_category_or_nav(title, tender_url):
                        continue

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(tender_url):
                        continue

                    parent = link.find_parent("tr") or link.find_parent("div")
                    parent_text = parent.get_text(separator=" ", strip=True) if parent else title

                    matches = self._match_keywords(f"{title} {parent_text}", keywords) if keywords else ["latest"]
                    if matches:
                        tenders.append(Tender(
                            source="AfroTender",
                            title=title,
                            url=tender_url,
                            organization="AfroTender Listing",
                            deadline="Check link",
                            category="Procurement",
                            snippet=parent_text[:280] + "...",
                            matched_keywords=matches,
                            posted_date="Recent"
                        ))
            except Exception as e:
                logger.error(f"Error scraping AfroTender ({url}): {e}")
        return tenders

    async def scan_all_sources(self, ignore_seen: bool = False) -> Dict[str, any]:
        keywords = self.db.get_keywords()
        async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
            tasks = [
                self.fetch_2merkato(client, keywords, ignore_seen=ignore_seen),
                self.fetch_ethiojobs(client, keywords, ignore_seen=ignore_seen),
                self.fetch_the_reporter(client, keywords, ignore_seen=ignore_seen),
                self.fetch_afrotender(client, keywords, ignore_seen=ignore_seen),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        found_tenders = []
        counts = {"2Merkato": 0, "EthioJobs": 0, "The Reporter": 0, "AfroTender": 0}
        source_keys = ["2Merkato", "EthioJobs", "The Reporter", "AfroTender"]

        for idx, res in enumerate(results):
            source_name = source_keys[idx]
            if isinstance(res, list):
                counts[source_name] = len(res)
                found_tenders.extend(res)
            elif isinstance(res, Exception):
                logger.error(f"Scraper error on {source_name}: {res}")

        return {"tenders": found_tenders, "counts": counts}

    async def get_raw_latest(self) -> List[Tender]:
        """Fetch raw tenders regardless of keywords to verify live connection."""
        async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
            tasks = [
                self.fetch_2merkato(client, keywords=None),
                self.fetch_ethiojobs(client, keywords=None),
                self.fetch_the_reporter(client, keywords=None),
                self.fetch_afrotender(client, keywords=None),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        latest_list = []
        for res in results:
            if isinstance(res, list):
                # Take up to 2 raw items from each responsive portal
                latest_list.extend(res[:2])
        return latest_list


# ==========================================
# TELEGRAM FORMATTER & BROADCASTER
# ==========================================
def format_tender_message(tender: Tender) -> str:
    badge = {
        "2Merkato": "🟣 <b>2Merkato Tender</b>",
        "EthioJobs": "🔵 <b>EthioJobs Opportunity</b>",
        "The Reporter": "🔴 <b>The Reporter Notice</b>",
        "AfroTender": "🟢 <b>AfroTender Bid</b>"
    }.get(tender.source, f"📌 <b>{tender.source}</b>")

    tags = " ".join([f"<code>#{kw.replace(' ', '_')}</code>" for kw in tender.matched_keywords])

    msg = (
        f"{badge}\n\n"
        f"🎯 <b>Title:</b> {tender.title}\n"
        f"🏢 <b>Client/Org:</b> {tender.organization}\n"
        f"📅 <b>Posted:</b> {tender.posted_date}\n"
        f"⏳ <b>Deadline:</b> {tender.deadline}\n"
        f"🏷️ <b>Category:</b> {tender.category}\n"
        f"🔑 <b>Matched Services:</b> {tags}\n\n"
        f"📝 <b>Summary:</b>\n<i>{tender.snippet}</i>\n\n"
        f"🔗 <a href='{tender.url}'><b>👉 Click Here to Open Bid</b></a>"
    )
    return msg


# ==========================================
# TELEGRAM BOT HANDLERS
# ==========================================
class TenderBotApp:
    def __init__(self, db: Database, scraper: TenderScraper):
        self.db = db
        self.scraper = scraper

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        user_name = update.effective_user.first_name

        welcome_text = (
            f"👋 <b>Hello {user_name}!</b>\n\n"
            "I am your <b>Business Development Tender Monitor Bot</b> for Media & Communication services in Ethiopia.\n\n"
            f"🆔 <b>Your Chat ID:</b> <code>{chat_id}</code>\n\n"
            f"🗓️ <b>Coverage:</b> Scanning past <b>{LOOKBACK_DAYS} days</b> across multiple pages\n"
            "🌐 <b>Active Web Sources:</b>\n"
            "• 2Merkato (tender.2merkato.com/tenders)\n"
            "• EthioJobs (ethiojobs.net/jobs - RFPs & ToRs)\n"
            "• The Reporter (reportertenders.com & t.me/s/reportertender)\n"
            "• AfroTender (afrotender.com/tenders)\n\n"
            "🛠 <b>Commands:</b>\n"
            "/scan - Scan for new media tenders (past 3 days)\n"
            "/rescan - Re-send all 3-day media tenders (even seen ones)\n"
            "/latest - Preview newest live posts directly from portals\n"
            "/keywords - View current filtered keywords\n"
            "/addkeyword &lt;word&gt; - Add custom keyword (English or Amharic)\n"
            "/delkeyword &lt;word&gt; - Remove a keyword\n"
            "/status - Bot health & statistics\n"
            "/help - Display this manual"
        )
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)

    async def scan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_msg = await update.message.reply_text(
            f"🔎 <b>Scanning past {LOOKBACK_DAYS} days across all 4 sites...</b> Please wait.",
            parse_mode=ParseMode.HTML
        )
        scan_data = await self.scraper.scan_all_sources(ignore_seen=False)
        tenders = scan_data["tenders"]

        if not tenders:
            msg = (
                f"✅ <b>Scan completed (Checked past {LOOKBACK_DAYS} days).</b>\n\n"
                "All 4 sites were reached and multiple pages were scanned, but <b>no new tenders</b> matched your media/communication keywords.\n\n"
                "💡 <i>Tips:</i>\n"
                "• Send <code>/rescan</code> to see all 3-day tenders even if previously viewed\n"
                "• Send <code>/latest</code> to inspect live listings\n"
                "• Send <code>/keywords</code> to check or add more terms!"
            )
            await status_msg.edit_text(msg, parse_mode=ParseMode.HTML)
            return

        await status_msg.edit_text(f"🎯 Found <b>{len(tenders)}</b> matching tender(s) from the past {LOOKBACK_DAYS} days! Sending now...", parse_mode=ParseMode.HTML)

        for tender in tenders:
            msg = format_tender_message(tender)
            await update.message.reply_text(
                msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            self.db.mark_tender_seen(tender)
            await asyncio.sleep(0.5)

    async def rescan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_msg = await update.message.reply_text(
            f"🔄 <b>Deep scanning past {LOOKBACK_DAYS} days (including already seen bids)...</b>",
            parse_mode=ParseMode.HTML
        )
        scan_data = await self.scraper.scan_all_sources(ignore_seen=True)
        tenders = scan_data["tenders"]

        if not tenders:
            await status_msg.edit_text(
                f"ℹ️ No tenders matching media & communication services were found in the last {LOOKBACK_DAYS} days.",
                parse_mode=ParseMode.HTML
            )
            return

        await status_msg.edit_text(f"📬 Found <b>{len(tenders)}</b> total media tenders from the past {LOOKBACK_DAYS} days:", parse_mode=ParseMode.HTML)

        for tender in tenders:
            msg = format_tender_message(tender)
            await update.message.reply_text(
                msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            await asyncio.sleep(0.4)

    async def latest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_msg = await update.message.reply_text("📡 Connecting to all 4 portals...", parse_mode=ParseMode.HTML)
        latest_tenders = await self.scraper.get_raw_latest()

        if not latest_tenders:
            await status_msg.edit_text(
                "⚠️ Portals responded, but no list elements were parsed. Please check console logs or send /rescan.",
                parse_mode=ParseMode.HTML
            )
            return

        await status_msg.edit_text(
            f"📋 Successfully fetched <b>{len(latest_tenders)}</b> live listings directly from the portals:",
            parse_mode=ParseMode.HTML
        )

        for tender in latest_tenders:
            msg = format_tender_message(tender)
            await update.message.reply_text(
                msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            await asyncio.sleep(0.4)

    async def keywords_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keywords = self.db.get_keywords()
        formatted = ", ".join([f"<code>{kw}</code>" for kw in keywords])
        text = (
            f"📋 <b>Monitored Service Keywords ({len(keywords)}):</b>\n\n"
            f"{formatted}\n\n"
            "To add: <code>/addkeyword video production</code> (or in Amharic <code>/addkeyword ዶክመንተሪ</code>)\n"
            "To delete: <code>/delkeyword translation</code>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def add_keyword_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("⚠️ Usage: <code>/addkeyword &lt;your_keyword&gt;</code>", parse_mode=ParseMode.HTML)
            return

        kw = " ".join(context.args).strip()
        success = self.db.add_keyword(kw)
        if success:
            await update.message.reply_text(f"✅ Added keyword: <code>{kw.lower()}</code>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"ℹ️ Keyword <code>{kw}</code> is already in the list or invalid.", parse_mode=ParseMode.HTML)

    async def del_keyword_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("⚠️ Usage: <code>/delkeyword &lt;keyword_to_remove&gt;</code>", parse_mode=ParseMode.HTML)
            return

        kw = " ".join(context.args).strip()
        success = self.db.remove_keyword(kw)
        if success:
            await update.message.reply_text(f"🗑 Removed keyword: <code>{kw.lower()}</code>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"⚠️ Keyword <code>{kw}</code> was not found.", parse_mode=ParseMode.HTML)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.db.get_stats()
        text = (
            "📊 <b>Tender Monitor Bot Status</b>\n\n"
            f"• <b>Total Seen Bids Recorded:</b> {stats['total_seen']}\n"
            f"• <b>Monitored Keywords:</b> {stats['total_keywords']}\n"
            f"• <b>Auto-Scan Frequency:</b> Every {SCAN_INTERVAL_MINUTES} minutes\n"
            f"• <b>Web Targets:</b>\n"
            "  1. tender.2merkato.com/tenders\n"
            "  2. ethiojobs.net/jobs\n"
            "  3. reportertenders.com & t.me/s/reportertender\n"
            "  4. afrotender.com/tenders\n"
            f"• <b>System Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ==========================================
# SCHEDULED BACKGROUND SCANNER
# ==========================================
async def periodic_scan_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data.get("chat_id")
    if not chat_id:
        return

    scraper: TenderScraper = context.job.data.get("scraper")
    db: Database = context.job.data.get("db")

    logger.info("Executing scheduled tender scan...")
    try:
        scan_data = await scraper.scan_all_sources()
        tenders = scan_data["tenders"]
        if tenders:
            logger.info(f"Discovered {len(tenders)} matching tenders during auto-scan.")
            for tender in tenders:
                msg = format_tender_message(tender)
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
                    db.mark_tender_seen(tender)
                    await asyncio.sleep(0.5)
                except Exception as send_err:
                    logger.error(f"Failed to deliver message to Telegram: {send_err}")
        else:
            logger.info("Scheduled scan completed. No new matching tenders found.")
    except Exception as e:
        logger.error(f"Error during periodic scan: {e}")


# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    if not BOT_TOKEN:
        print("\n" + "="*60)
        print("ERROR: TELEGRAM_BOT_TOKEN is not configured!")
        print("="*60 + "\n")
        sys.exit(1)

    db = Database(DATABASE_PATH)
    scraper = TenderScraper(db)
    bot_app = TenderBotApp(db, scraper)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", bot_app.start))
    app.add_handler(CommandHandler("help", bot_app.start))
    app.add_handler(CommandHandler("scan", bot_app.scan_command))
    app.add_handler(CommandHandler("rescan", bot_app.rescan_command))
    app.add_handler(CommandHandler("latest", bot_app.latest_command))
    app.add_handler(CommandHandler("keywords", bot_app.keywords_command))
    app.add_handler(CommandHandler("addkeyword", bot_app.add_keyword_command))
    app.add_handler(CommandHandler("delkeyword", bot_app.del_keyword_command))
    app.add_handler(CommandHandler("status", bot_app.status_command))

    if AUTHORIZED_CHAT_ID:
        job_queue = app.job_queue
        job_queue.run_repeating(
            periodic_scan_job,
            interval=SCAN_INTERVAL_MINUTES * 60,
            first=10,
            data={"chat_id": AUTHORIZED_CHAT_ID, "scraper": scraper, "db": db},
            name="tender_scanner_job"
        )
        logger.info(f"Scheduled scanner active for Chat ID: {AUTHORIZED_CHAT_ID} every {SCAN_INTERVAL_MINUTES} mins.")
    else:
        logger.warning("MY_CHAT_ID is not configured.")

    print("\n🚀 Tender Monitor Bot is running! Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()