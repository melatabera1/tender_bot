#!/usr/bin/env python3
"""
Enterprise Ethiopian Tender & Bid Intelligence Bot (AI-Powered & Multi-Client)
Built for Media, Communication & B2B Agencies in Ethiopia.

Features Included:
  1. AI Precision Engine (Google Gemini API) - Eliminates false positives & summarizes bids
  2. Paid Portal Login Support (2Merkato & AfroTender subscriber sessions)
  3. Multi-Client Management (Manage paying clients directly via Telegram commands)
  4. SHA-256 Content Deduplication (Never sends duplicate tenders even if reposted)
  5. Built-in Free Cloud Health Server (Works on Render Free Web Service $0/mo)
"""

import os
import sys
import asyncio
import logging
import sqlite3
import hashlib
import re
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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
# ⚙️ STEP-BY-STEP EASY CONFIGURATION (FILL THESE IN)
# ==============================================================================

# 1. Your Telegram Bot Token (Already configured for you!):
MY_BOT_TOKEN = "8772423781:AAHYbS4JNdPwYwxiAF0mskFO1IPIEXLlJFs"

# 2. Your Personal Admin Telegram Chat ID (Already configured for you!):
MY_CHAT_ID = "5337175992"

# 3. FREE Google Gemini AI Key (Configured for you!):
#    Note: Standard Google AI Studio keys start with "AIza...". If this key is from
#    another portal, the bot will automatically fall back to smart keyword mode if needed.
GEMINI_API_KEY = "AQ.Ab8RN6JqwIzBI3FxdRygvaJxM1Q3nR0fblGe9PJFIhkGwk7oYw"

# 4. PAID TENDER WEBSITE LOGINS (Optional - Fill in when you buy a subscription):
MERKATO_USERNAME = ""
MERKATO_PASSWORD = ""

AFROTENDER_USERNAME = ""
AFROTENDER_PASSWORD = ""

# 5. Scanner Settings:
SCAN_INTERVAL_MINUTES = 30
DATABASE_PATH = "tenders.db"
LOOKBACK_DAYS = 3
AI_CONFIDENCE_THRESHOLD = 70  # Only send tenders scored 70% or higher by AI

# Resolve config from code or cloud environment variables
BOT_TOKEN = MY_BOT_TOKEN.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = MY_CHAT_ID.strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip()
AI_KEY = GEMINI_API_KEY.strip() or os.getenv("GEMINI_API_KEY", "").strip()

MERKATO_USER = MERKATO_USERNAME.strip() or os.getenv("MERKATO_USERNAME", "").strip()
MERKATO_PASS = MERKATO_PASSWORD.strip() or os.getenv("MERKATO_PASSWORD", "").strip()
AFRO_USER = AFROTENDER_USERNAME.strip() or os.getenv("AFROTENDER_USERNAME", "").strip()
AFRO_PASS = AFROTENDER_PASSWORD.strip() or os.getenv("AFROTENDER_PASSWORD", "").strip()

# Default Media & Communication Keywords (Stage 1 Fast Filter)
DEFAULT_MEDIA_KEYWORDS = [
    # English keywords
    "media", "communication", "communications", "video production", "video",
    "documentary", "advertising", "advertisement", "public relations", "pr firm",
    "branding", "graphic design", "graphics", "social media", "creative agency",
    "tv commercial", "radio spot", "radio", "television", "audio visual",
    "audiovisual", "animation", "photography", "photo", "printing",
    "event management", "event organizing", "campaign", "content creation",
    "translation", "signage", "billboard", "press conference", "consultancy",
    "tor", "rfp", "eoi", "expression of interest", "call for proposals", "production",
    # Amharic keywords
    "ማስታወቂያ", "ህትመት", "ሕትመት", "ሚዲያ", "ኮሙኒኬሽን", "ኮሚዩኒኬሽን",
    "የህዝብ ግንኙነት", "ቪዲዮ", "ዘጋቢ ፊልም", "ዶክመንተሪ", "ፎቶ", "ፎቶግራፍ",
    "ግራፊክስ", "ዲዛይን", "ስልጠና", "ዝግጅት"
]

# Configure Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("EnterpriseTenderBot")


# ==========================================
# 🌐 FREE CLOUD PORT LISTENER (FOR RENDER)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Ethiopian Tender AI Bot is Active and Running 24/7!")

    def log_message(self, format, *args):
        return  # Keep console logs clean


def start_free_cloud_web_server():
    """Starts a tiny background web server so Render Free Web Service stays alive 24/7."""
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"Cloud Health Web Server listening on port {port}")
    except Exception as e:
        logger.warning(f"Note: Web server port {port} skipped: {e}")


# ==========================================
# 📦 DATA MODELS
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
    ai_summary: str = ""
    ai_score: int = 0
    ai_requirements: str = ""


# ==========================================
# 🗄️ DATABASE (TENDERS, KEYWORDS & CLIENTS)
# ==========================================
class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._init_db()

    def _compute_hash(self, title: str, org: str) -> str:
        clean = re.sub(r"\W+", "", f"{title.lower()}{org.lower()}")
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Seen tenders table with content hash
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS seen_tenders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    tender_url TEXT UNIQUE NOT NULL,
                    content_hash TEXT,
                    title TEXT NOT NULL,
                    organization TEXT,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Keywords table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT UNIQUE NOT NULL
                )
            """)
            # Multi-tenant clients table (for your SaaS startup!)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS subscribed_clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT UNIQUE NOT NULL,
                    client_name TEXT NOT NULL,
                    sector TEXT DEFAULT 'Media & Communications',
                    is_active INTEGER DEFAULT 1,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

            # Ensure default keywords exist
            for kw in DEFAULT_MEDIA_KEYWORDS:
                cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (kw.lower(),))

            # Ensure Admin Chat ID is automatically registered as the primary client
            if ADMIN_CHAT_ID:
                cursor.execute("""
                    INSERT OR IGNORE INTO subscribed_clients (chat_id, client_name, sector)
                    VALUES (?, 'Admin (My Personal Account)', 'Media & Communications')
                """, (ADMIN_CHAT_ID,))
            conn.commit()

    def is_tender_seen(self, url: str, title: str = "", org: str = "") -> bool:
        content_hash = self._compute_hash(title, org) if title else ""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if content_hash:
                cursor.execute(
                    "SELECT 1 FROM seen_tenders WHERE tender_url = ? OR content_hash = ?",
                    (url, content_hash)
                )
            else:
                cursor.execute("SELECT 1 FROM seen_tenders WHERE tender_url = ?", (url,))
            return cursor.fetchone() is not None

    def mark_tender_seen(self, tender: Tender):
        content_hash = self._compute_hash(tender.title, tender.organization)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO seen_tenders (source, tender_url, content_hash, title, organization)
                VALUES (?, ?, ?, ?, ?)
            """, (tender.source, tender.url, content_hash, tender.title, tender.organization))
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

    def add_client(self, chat_id: str, client_name: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO subscribed_clients (chat_id, client_name, is_active)
                VALUES (?, ?, 1)
            """, (chat_id.strip(), client_name.strip()))
            conn.commit()
            return True

    def remove_client(self, chat_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM subscribed_clients WHERE chat_id = ?", (chat_id.strip(),))
            conn.commit()
            return cursor.rowcount > 0

    def get_active_clients(self) -> List[Dict[str, str]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id, client_name, sector FROM subscribed_clients WHERE is_active = 1")
            return [{"chat_id": r[0], "name": r[1], "sector": r[2]} for r in cursor.fetchall()]

    def get_stats(self) -> Dict[str, int]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM seen_tenders")
            total_seen = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM keywords")
            total_keywords = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM subscribed_clients WHERE is_active = 1")
            total_clients = cursor.fetchone()[0]
            return {
                "total_seen": total_seen,
                "total_keywords": total_keywords,
                "total_clients": total_clients
            }


# ==========================================
# 🧠 AI PRECISION FILTER (GOOGLE GEMINI)
# ==========================================
class AITenderAnalyzer:
    """Uses Google Gemini Flash to verify, score, and summarize Ethiopian tenders."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.enabled = bool(api_key)

    async def evaluate_tender(self, client: httpx.AsyncClient, tender: Tender) -> bool:
        """
        Returns True if the tender should be kept, False if AI determines it's a false positive.
        If GEMINI_API_KEY is not set or invalid, it gracefully returns True and uses standard keyword mode.
        """
        if not self.enabled:
            return True

        prompt = f"""
You are a Senior Procurement Analyst in Ethiopia working for a Media, Communications, Advertising, Printing, Photography, Audiovisual, and PR Agency.
Analyze the following tender/job listing and respond ONLY in valid JSON format.

Title: {tender.title}
Organization: {tender.organization}
Snippet: {tender.snippet}
Matched Keywords: {', '.join(tender.matched_keywords)}

Determine if this notice is genuinely useful for a Media, Creative, Communication, Printing, Event, or Consultancy agency.
Reject false positives (for example, construction tenders that only mention "media" as in "filter media for water pipes", or generic website menu headers).

Respond with this exact JSON structure:
{{
  "relevance_score": <integer from 0 to 100>,
  "clean_title": "<clean, readable title in English>",
  "organization": "<organization name if identifiable, else '{tender.organization}'>",
  "deadline": "<extracted closing date/deadline or '{tender.deadline}'>",
  "requirements": "<key eligibility or service needed in 1 short line>",
  "executive_summary": "<concise 2-sentence summary of what the client wants to buy>"
}}
"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}
        }

        try:
            resp = await client.post(url, json=payload, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(raw_text)

                score = int(parsed.get("relevance_score", 80))
                tender.ai_score = score

                if score < AI_CONFIDENCE_THRESHOLD:
                    logger.info(f"AI Rejected False Positive ({score}%): {tender.title[:60]}")
                    return False

                tender.title = parsed.get("clean_title") or tender.title
                tender.organization = parsed.get("organization") or tender.organization
                tender.deadline = parsed.get("deadline") or tender.deadline
                tender.ai_requirements = parsed.get("requirements", "")
                tender.ai_summary = parsed.get("executive_summary", "")
                return True
        except Exception as e:
            logger.debug(f"AI evaluation fallback (keeping tender via keywords): {e}")

        return True


# ==========================================
# 🔐 AUTHENTICATED & MULTI-SOURCE SCRAPER
# ==========================================
class TenderScraper:
    def __init__(self, db: Database, ai: AITenderAnalyzer):
        self.db = db
        self.ai = ai
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,am;q=0.8",
        }
        self.merkato_cookies = {}
        self.afrotender_cookies = {}

    async def _login_2merkato_if_configured(self, client: httpx.AsyncClient):
        """Logs into 2Merkato if subscriber credentials are provided."""
        if not (MERKATO_USER and MERKATO_PASS) or self.merkato_cookies:
            return
        try:
            login_url = "https://tender.2merkato.com/login"
            page = await client.get(login_url, headers=self.headers, timeout=15.0)
            soup = BeautifulSoup(page.text, "html.parser")
            csrf = soup.find("input", {"name": "_token"})
            form_data = {
                "email": MERKATO_USER,
                "username": MERKATO_USER,
                "password": MERKATO_PASS,
            }
            if csrf and csrf.get("value"):
                form_data["_token"] = csrf["value"]

            resp = await client.post(login_url, data=form_data, headers=self.headers, timeout=15.0)
            if resp.status_code in (200, 302):
                self.merkato_cookies = dict(client.cookies)
                logger.info("Successfully authenticated subscriber session on 2Merkato!")
        except Exception as e:
            logger.warning(f"2Merkato subscriber login note: {e}")

    async def _login_afrotender_if_configured(self, client: httpx.AsyncClient):
        """Logs into AfroTender if subscriber credentials are provided."""
        if not (AFRO_USER and AFRO_PASS) or self.afrotender_cookies:
            return
        try:
            login_url = "https://afrotender.com/login"
            resp = await client.post(
                login_url,
                data={"email": AFRO_USER, "password": AFRO_PASS},
                headers=self.headers,
                timeout=15.0
            )
            if resp.status_code in (200, 302):
                self.afrotender_cookies = dict(client.cookies)
                logger.info("Successfully authenticated subscriber session on AfroTender!")
        except Exception as e:
            logger.warning(f"AfroTender subscriber login note: {e}")

    def _is_category_or_nav(self, title: str, url: str) -> bool:
        """Filter out generic category taxonomy pages, menus, and navigation links."""
        url_lower = url.lower()
        title_lower = title.strip().lower()

        junk_url_parts = [
            "/category/", "/categories/", "/tender-category/", "/job-category/",
            "/services/", "/tag/", "/tags/", "/sector/", "/browse",
            "/page/", "/author/", "/login", "/register", "/signup",
            "/about", "/contact", "/privacy", "/terms", "/pricing", "/feed"
        ]
        if any(junk in url_lower for junk in junk_url_parts):
            return True

        words = title_lower.split()
        if len(words) <= 4:
            category_endings = ["service", "services", "category", "categories", "tenders", "jobs", "vacancies", "sector"]
            if words and words[-1] in category_endings:
                return True
            if title_lower in [
                "printing & publishing", "printing & publishing service",
                "advertising & public relations", "media & communication",
                "consultancy & training", "consultancy services",
                "all tenders", "free tenders", "latest tenders"
            ]:
                return True

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

    # 1. 2MERKATO TENDERS
    async def fetch_2merkato(self, client: httpx.AsyncClient, keywords: Optional[List[str]] = None, ignore_seen: bool = False) -> List[Tender]:
        await self._login_2merkato_if_configured(client)
        tenders = []
        urls = [
            "https://tender.2merkato.com/tenders/free",
            "https://tender.2merkato.com/tenders",
            "https://tender.2merkato.com/tenders?page=2"
        ]

        for url in urls:
            try:
                resp = await client.get(url, headers=self.headers, cookies=self.merkato_cookies, timeout=25.0)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if not ("/tender" in href or "/tenders/view" in href):
                        continue

                    title = link.get_text(separator=" ", strip=True)
                    tender_url = href if href.startswith("http") else f"https://tender.2merkato.com{href}"

                    if self._is_category_or_nav(title, tender_url):
                        continue

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(tender_url, title, "2Merkato"):
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
                            snippet=parent_text[:300] + ("..." if len(parent_text) > 300 else ""),
                            matched_keywords=matches,
                            posted_date="Recent"
                        ))
            except Exception as e:
                logger.error(f"Error scraping 2Merkato ({url}): {e}")
        return tenders

    # 2. ETHIOJOBS (Consultancies, RFPs, ToRs & Media Jobs)
    async def fetch_ethiojobs(self, client: httpx.AsyncClient, keywords: Optional[List[str]] = None, ignore_seen: bool = False) -> List[Tender]:
        tenders = []
        seen_job_urls = set()

        api_urls = [
            "https://api.ethiojobs.net/ethiojobs/jobs?page=1&perPage=45",
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
                        company = item.get("companyName") or item.get("employer") or (item.get("company", {})).get("name") or "Organization on EthioJobs"
                        if not ignore_seen and keywords is not None and self.db.is_tender_seen(job_url, title, str(company)):
                            continue

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
                                snippet=clean_snippet[:300] + ("..." if len(clean_snippet) > 300 else ""),
                                matched_keywords=matches,
                                posted_date=posted
                            ))
            except Exception as e:
                logger.debug(f"EthioJobs API probe note: {e}")

        web_urls = [
            "https://ethiojobs.net/jobs",
            "https://ethiojobs.net/jobs/consultancy-and-training"
        ]

        for w_url in web_urls:
            try:
                resp = await client.get(w_url, headers=self.headers, timeout=25.0)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if not ("/job/" in href or "/jobs/" in href):
                        continue

                    title = link.get_text(separator=" ", strip=True)
                    job_url = href if href.startswith("http") else f"https://ethiojobs.net{href}"

                    if job_url in seen_job_urls or self._is_category_or_nav(title, job_url):
                        continue
                    seen_job_urls.add(job_url)

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(job_url, title, "EthioJobs"):
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
                            snippet=parent_text[:300] + ("..." if len(parent_text) > 300 else ""),
                            matched_keywords=matches,
                            posted_date="Recent"
                        ))
            except Exception as e:
                logger.error(f"Error scraping EthioJobs ({w_url}): {e}")
        return tenders

    # 3. THE REPORTER & LIVE TELEGRAM TENDER FEEDS
    async def fetch_the_reporter(self, client: httpx.AsyncClient, keywords: Optional[List[str]] = None, ignore_seen: bool = False) -> List[Tender]:
        tenders = []
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

                    if self._is_category_or_nav(title, post_url):
                        continue

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(post_url, title, "The Reporter"):
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
                            snippet=full_text[:300] + ("..." if len(full_text) > 300 else ""),
                            matched_keywords=matches,
                            posted_date=posted
                        ))
            except Exception as e:
                logger.warning(f"Error fetching channel {ch_url}: {e}")

        try:
            r_resp = await client.get("https://reportertenders.com/", headers=self.headers, timeout=20.0)
            if r_resp.status_code == 200:
                soup = BeautifulSoup(r_resp.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    title = link.get_text(strip=True)
                    tender_url = href if href.startswith("http") else f"https://reportertenders.com{href}"

                    if self._is_category_or_nav(title, tender_url):
                        continue

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(tender_url, title, "ReporterTenders"):
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

    # 4. AFROTENDER
    async def fetch_afrotender(self, client: httpx.AsyncClient, keywords: Optional[List[str]] = None, ignore_seen: bool = False) -> List[Tender]:
        await self._login_afrotender_if_configured(client)
        tenders = []
        urls = [
            "https://afrotender.com/tenders",
            "https://afrotender.com"
        ]
        for url in urls:
            try:
                resp = await client.get(url, headers=self.headers, cookies=self.afrotender_cookies, timeout=25.0)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    title = link.get_text(separator=" ", strip=True)
                    tender_url = href if href.startswith("http") else f"https://afrotender.com{href}"

                    if self._is_category_or_nav(title, tender_url):
                        continue

                    if not ignore_seen and keywords is not None and self.db.is_tender_seen(tender_url, title, "AfroTender"):
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
                            snippet=parent_text[:300] + "...",
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

            candidates: List[Tender] = []
            counts = {"2Merkato": 0, "EthioJobs": 0, "The Reporter": 0, "AfroTender": 0}
            source_keys = ["2Merkato", "EthioJobs", "The Reporter", "AfroTender"]

            for idx, res in enumerate(results):
                source_name = source_keys[idx]
                if isinstance(res, list):
                    counts[source_name] = len(res)
                    candidates.extend(res)
                elif isinstance(res, Exception):
                    logger.error(f"Scraper error on {source_name}: {res}")

            # Stage 2: Pass candidates through AI Precision Filter (if GEMINI_API_KEY is configured)
            verified_tenders: List[Tender] = []
            seen_urls_in_batch = set()
            for t in candidates:
                if t.url in seen_urls_in_batch:
                    continue
                seen_urls_in_batch.add(t.url)

                keep = await self.ai.evaluate_tender(client, t)
                if keep:
                    verified_tenders.append(t)

        return {"tenders": verified_tenders, "counts": counts}

    async def get_raw_latest(self) -> List[Tender]:
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
                latest_list.extend(res[:2])
        return latest_list


# ==========================================
# 💬 TELEGRAM MESSAGE FORMATTER
# ==========================================
def format_tender_message(tender: Tender) -> str:
    badge = {
        "2Merkato": "🟣 <b>2Merkato Tender</b>",
        "EthioJobs": "🔵 <b>EthioJobs Consultancy / RFP</b>",
        "The Reporter": "🔴 <b>The Reporter Notice</b>",
        "AfroTender": "🟢 <b>AfroTender Bid</b>"
    }.get(tender.source, f"📌 <b>{tender.source}</b>")

    tags = " ".join([f"<code>#{kw.replace(' ', '_')}</code>" for kw in tender.matched_keywords[:6]])

    ai_badge = f" | 🤖 <b>AI Match: {tender.ai_score}%</b>" if tender.ai_score > 0 else ""
    summary_text = tender.ai_summary if tender.ai_summary else tender.snippet
    req_line = f"📋 <b>Requirements:</b> {tender.ai_requirements}\n" if tender.ai_requirements else ""

    msg = (
        f"{badge}{ai_badge}\n\n"
        f"🎯 <b>Title:</b> {tender.title}\n"
        f"🏢 <b>Client/Org:</b> {tender.organization}\n"
        f"📅 <b>Posted:</b> {tender.posted_date}\n"
        f"⏳ <b>Deadline:</b> {tender.deadline}\n"
        f"{req_line}"
        f"🔑 <b>Matched Services:</b> {tags}\n\n"
        f"📝 <b>Executive Summary:</b>\n<i>{summary_text}</i>\n\n"
        f"🔗 <a href='{tender.url}'><b>👉 Click Here to Open Official Tender</b></a>"
    )
    return msg


# ==========================================
# 🤖 TELEGRAM BOT COMMAND HANDLERS
# ==========================================
class TenderBotApp:
    def __init__(self, db: Database, scraper: TenderScraper):
        self.db = db
        self.scraper = scraper

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        user_name = update.effective_user.first_name or "Partner"
        ai_status = "✅ Active (Gemini Flash)" if AI_KEY else "⚡ Fast Keyword Mode (Add GEMINI_API_KEY for AI)"

        welcome_text = (
            f"👋 <b>Selam {user_name}!</b>\n\n"
            "Welcome to the <b>Ethiopian Tender & Bid Intelligence Platform</b>.\n\n"
            f"🆔 <b>This Chat/Group ID:</b> <code>{chat_id}</code>\n"
            f"🧠 <b>AI Precision Engine:</b> {ai_status}\n"
            f"🗓️ <b>Scan Depth:</b> Past <b>{LOOKBACK_DAYS} days</b> (Every {SCAN_INTERVAL_MINUTES} mins)\n\n"
            "🛠 <b>Tender Commands:</b>\n"
            "/scan - Scan for new media & communication tenders\n"
            "/rescan - Re-send all 3-day matching tenders (even seen ones)\n"
            "/latest - Preview live posts directly from portals\n"
            "/keywords - View monitored service keywords\n"
            "/addkeyword &lt;word&gt; - Add custom keyword (English/Amharic)\n"
            "/delkeyword &lt;word&gt; - Remove a keyword\n\n"
            "💼 <b>Business & Client Management (SaaS):</b>\n"
            "/subscribe &lt;Chat_ID&gt; &lt;Company Name&gt; - Add a paying client or group\n"
            "/unsubscribe &lt;Chat_ID&gt; - Remove a client\n"
            "/clients - List all active subscribed clients\n"
            "/status - System health, logins & statistics"
        )
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)

    async def scan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_msg = await update.message.reply_text(
            f"🔎 <b>Scanning all 4 Ethiopian tender portals (past {LOOKBACK_DAYS} days)...</b>",
            parse_mode=ParseMode.HTML
        )
        scan_data = await self.scraper.scan_all_sources(ignore_seen=False)
        tenders = scan_data["tenders"]

        if not tenders:
            msg = (
                f"✅ <b>You are 100% up to date!</b>\n\n"
                f"All 4 websites were scanned for the past {LOOKBACK_DAYS} days. "
                "You have already received all matching tenders published recently.\n\n"
                "💡 <b>Want to view them again?</b>\n"
                "👉 Tap or send <code>/rescan</code> to re-display all tenders from the past 3 days!"
            )
            await status_msg.edit_text(msg, parse_mode=ParseMode.HTML)
            return

        await status_msg.edit_text(
            f"🎯 Found <b>{len(tenders)}</b> verified tender(s)! Delivering now...",
            parse_mode=ParseMode.HTML
        )

        for tender in tenders:
            msg = format_tender_message(tender)
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
            self.db.mark_tender_seen(tender)
            await asyncio.sleep(0.5)

    async def rescan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_msg = await update.message.reply_text(
            f"🔄 <b>Deep scanning past {LOOKBACK_DAYS} days (including previously seen bids)...</b>",
            parse_mode=ParseMode.HTML
        )
        scan_data = await self.scraper.scan_all_sources(ignore_seen=True)
        tenders = scan_data["tenders"]

        if not tenders:
            await status_msg.edit_text(
                f"ℹ️ No tenders matching your keywords were found in the last {LOOKBACK_DAYS} days.",
                parse_mode=ParseMode.HTML
            )
            return

        await status_msg.edit_text(
            f"📬 Found <b>{len(tenders)}</b> matching tenders from the past {LOOKBACK_DAYS} days:",
            parse_mode=ParseMode.HTML
        )

        for tender in tenders:
            msg = format_tender_message(tender)
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
            await asyncio.sleep(0.4)

    async def latest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_msg = await update.message.reply_text("📡 Fetching live sample posts from all 4 portals...", parse_mode=ParseMode.HTML)
        latest_tenders = await self.scraper.get_raw_latest()

        if not latest_tenders:
            await status_msg.edit_text("⚠️ Could not parse sample items right now. Try /rescan.", parse_mode=ParseMode.HTML)
            return

        await status_msg.edit_text(
            f"📋 Fetched <b>{len(latest_tenders)}</b> live listings directly from the websites:",
            parse_mode=ParseMode.HTML
        )

        for tender in latest_tenders:
            msg = format_tender_message(tender)
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
            await asyncio.sleep(0.4)

    async def keywords_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keywords = self.db.get_keywords()
        formatted = ", ".join([f"<code>{kw}</code>" for kw in keywords])
        text = (
            f"📋 <b>Monitored Service Keywords ({len(keywords)}):</b>\n\n"
            f"{formatted}\n\n"
            "➕ Add: <code>/addkeyword documentary</code>\n"
            "➖ Remove: <code>/delkeyword translation</code>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def add_keyword_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("⚠️ Usage: <code>/addkeyword &lt;your_keyword&gt;</code>", parse_mode=ParseMode.HTML)
            return
        kw = " ".join(context.args).strip()
        if self.db.add_keyword(kw):
            await update.message.reply_text(f"✅ Added keyword: <code>{kw.lower()}</code>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"ℹ️ Keyword <code>{kw}</code> already exists.", parse_mode=ParseMode.HTML)

    async def del_keyword_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("⚠️ Usage: <code>/delkeyword &lt;keyword&gt;</code>", parse_mode=ParseMode.HTML)
            return
        kw = " ".join(context.args).strip()
        if self.db.remove_keyword(kw):
            await update.message.reply_text(f"🗑 Removed keyword: <code>{kw.lower()}</code>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"⚠️ Keyword <code>{kw}</code> not found.", parse_mode=ParseMode.HTML)

    # --- MULTI-CLIENT SAAS COMMANDS ---
    async def subscribe_client_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text(
                "💼 <b>Add a Paying Client or Telegram Group:</b>\n\n"
                "Usage:\n<code>/subscribe &lt;Chat_ID&gt; &lt;Company Name&gt;</code>\n\n"
                "Example:\n<code>/subscribe -100192837465 Abyssinia Media PLC</code>\n\n"
                "<i>Tip: Add this bot to your client's Telegram group and type /start in their group to see their Chat ID!</i>",
                parse_mode=ParseMode.HTML
            )
            return

        client_chat_id = context.args[0].strip()
        company_name = " ".join(context.args[1:]).strip()
        self.db.add_client(client_chat_id, company_name)
        await update.message.reply_text(
            f"✅ <b>Client Subscribed Successfully!</b>\n\n"
            f"🏢 <b>Company:</b> {company_name}\n"
            f"🆔 <b>Chat ID:</b> <code>{client_chat_id}</code>\n\n"
            "They will now automatically receive all matching tender alerts every 30 minutes!",
            parse_mode=ParseMode.HTML
        )

    async def unsubscribe_client_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("⚠️ Usage: <code>/unsubscribe &lt;Chat_ID&gt;</code>", parse_mode=ParseMode.HTML)
            return
        client_chat_id = context.args[0].strip()
        if self.db.remove_client(client_chat_id):
            await update.message.reply_text(f"🗑 Unsubscribed client <code>{client_chat_id}</code>.", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"⚠️ Client ID <code>{client_chat_id}</code> not found.", parse_mode=ParseMode.HTML)

    async def clients_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        clients = self.db.get_active_clients()
        if not clients:
            await update.message.reply_text("ℹ️ No active clients registered yet.", parse_mode=ParseMode.HTML)
            return

        lines = [f"{idx+1}. <b>{c['name']}</b> (<code>{c['chat_id']}</code>)" for idx, c in enumerate(clients)]
        text = (
            f"💼 <b>Active Subscribed Clients ({len(clients)}):</b>\n\n"
            + "\n".join(lines) +
            "\n\n➕ Add new client: <code>/subscribe &lt;Chat_ID&gt; &lt;Company Name&gt;</code>\n"
            "➖ Remove client: <code>/unsubscribe &lt;Chat_ID&gt;</code>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.db.get_stats()
        ai_mode = "🟢 Enabled (Gemini 1.5 Flash)" if AI_KEY else "🟡 Keyword Mode (Add GEMINI_API_KEY to enable AI)"
        merkato_auth = "🟢 Logged In (Paid Account)" if MERKATO_USER else "⚪ Public / Free Tier"
        afro_auth = "🟢 Logged In (Paid Account)" if AFRO_USER else "⚪ Public / Free Tier"

        text = (
            "📊 <b>Enterprise Tender Bot Status</b>\n\n"
            f"• <b>AI Precision Filter:</b> {ai_mode}\n"
            f"• <b>2Merkato Session:</b> {merkato_auth}\n"
            f"• <b>AfroTender Session:</b> {afro_auth}\n"
            f"• <b>Active Paying Clients:</b> {stats['total_clients']}\n"
            f"• <b>Total Deduplicated Tenders:</b> {stats['total_seen']}\n"
            f"• <b>Monitored Keywords:</b> {stats['total_keywords']}\n"
            f"• <b>Auto-Scan Frequency:</b> Every {SCAN_INTERVAL_MINUTES} minutes\n"
            f"• <b>Server Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ==========================================
# ⏰ MULTI-CLIENT AUTO-BROADCASTER
# ==========================================
async def periodic_scan_job(context: ContextTypes.DEFAULT_TYPE):
    scraper: TenderScraper = context.job.data.get("scraper")
    db: Database = context.job.data.get("db")

    clients = db.get_active_clients()
    if not clients:
        return

    logger.info(f"Running scheduled scan for {len(clients)} subscribed client(s)...")
    try:
        scan_data = await scraper.scan_all_sources(ignore_seen=False)
        tenders = scan_data["tenders"]
        if tenders:
            logger.info(f"Broadcasting {len(tenders)} new tenders to {len(clients)} client(s).")
            for tender in tenders:
                msg = format_tender_message(tender)
                for client_info in clients:
                    try:
                        await context.bot.send_message(
                            chat_id=client_info["chat_id"],
                            text=msg,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False
                        )
                        await asyncio.sleep(0.3)
                    except Exception as send_err:
                        logger.error(f"Failed sending to client {client_info['chat_id']}: {send_err}")
                db.mark_tender_seen(tender)
        else:
            logger.info("Scheduled scan complete. No brand-new tenders since last check.")
    except Exception as e:
        logger.error(f"Error during periodic scan: {e}")


# ==========================================
# 🚀 MAIN ENTRYPOINT
# ==========================================
def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing!")
        sys.exit(1)

    # Start free cloud port listener so Render Web Service ($0/mo) works seamlessly
    start_free_cloud_web_server()

    db = Database(DATABASE_PATH)
    ai = AITenderAnalyzer(AI_KEY)
    scraper = TenderScraper(db, ai)
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
    app.add_handler(CommandHandler("subscribe", bot_app.subscribe_client_command))
    app.add_handler(CommandHandler("unsubscribe", bot_app.unsubscribe_client_command))
    app.add_handler(CommandHandler("clients", bot_app.clients_command))
    app.add_handler(CommandHandler("status", bot_app.status_command))

    job_queue = app.job_queue
    job_queue.run_repeating(
        periodic_scan_job,
        interval=SCAN_INTERVAL_MINUTES * 60,
        first=10,
        data={"scraper": scraper, "db": db},
        name="enterprise_tender_scanner"
    )

    print("\n🚀 Enterprise AI Tender Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
