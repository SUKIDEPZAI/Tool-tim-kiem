import hashlib
import os
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, quote, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

VERSION = "2.0.0"
DB_PATH = os.getenv("DATABASE_PATH", "keys.db")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 WebSearchBot/2.0"
TIMEOUT = (3.05, 9)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
CORS(app, resources={r"/api/*": {"origins": [x.strip() for x in FRONTEND_ORIGIN.split(",")] if FRONTEND_ORIGIN != "*" else "*"}})
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))


def connect_db():
    parent = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL DEFAULT '',
        key_hash TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    )""")
    conn.commit()
    return conn


def hash_key(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def valid_key():
    raw = request.headers.get("X-API-Key", "").strip()
    if not raw or len(raw) > 200:
        return False
    conn = connect_db()
    try:
        return conn.execute(
            "SELECT 1 FROM api_keys WHERE key_hash=? AND active=1", (hash_key(raw),)
        ).fetchone() is not None
    finally:
        conn.close()


@app.errorhandler(429)
def rate_limit_error(_):
    return jsonify({"error": "Quá nhiều yêu cầu. Vui lòng đợi rồi thử lại.", "code": "rate_limited"}), 429


@app.get("/")
def home():
    return jsonify({"service": "Web Search Bot API", "version": VERSION, "status": "ok",
                    "endpoints": ["/api/health", "/api/keys", "/api/search?q=...&limit=5"]})


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "web-search-bot", "version": VERSION})


@app.post("/api/keys")
@limiter.limit("5 per hour")
def create_key():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()[:80]
    api_key = "ws_" + secrets.token_urlsafe(32)
    conn = connect_db()
    try:
        conn.execute("INSERT INTO api_keys(name,key_hash,created_at) VALUES(?,?,?)",
                     (name, hash_key(api_key), int(time.time())))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"api_key": api_key, "name": name,
                    "warning": "Save this key now; it cannot be retrieved later."}), 201


def clean_ddg_url(href):
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    return href


def search_duckduckgo(query, limit):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for item in soup.select(".result"):
        link = item.select_one(".result__a")
        snippet = item.select_one(".result__snippet")
        if not link:
            continue
        href = clean_ddg_url(link.get("href", ""))
        title = link.get_text(" ", strip=True)
        desc = snippet.get_text(" ", strip=True) if snippet else ""
        if href.startswith(("https://", "http://")) and title:
            results.append({"title": title, "url": href, "snippet": desc, "source": "DuckDuckGo"})
        if len(results) >= limit:
            break
    return results


def search_wikipedia(query, limit):
    response = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query,
                "srlimit": min(limit, 10), "format": "json", "utf8": 1},
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
    )
    response.raise_for_status()
    data = response.json()
    results = []
    for item in data.get("query", {}).get("search", []):
        title = BeautifulSoup(item.get("title", ""), "html.parser").get_text(" ", strip=True)
        snippet = BeautifulSoup(item.get("snippet", ""), "html.parser").get_text(" ", strip=True)
        page_url = "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="_()")
        results.append({"title": title, "url": page_url, "snippet": snippet, "source": "Wikipedia"})
    return results


def search_google_news(query, limit):
    url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "xml")
    results = []
    for item in soup.find_all("item")[:limit]:
        title = item.title.get_text(" ", strip=True) if item.title else ""
        link = item.link.get_text(strip=True) if item.link else ""
        description = item.description.get_text(" ", strip=True) if item.description else ""
        snippet = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
        if title and link and link.startswith("https://"):
            results.append({"title": title, "url": link, "snippet": snippet[:700], "source": "Google News"})
    return results


PROVIDERS = {
    "DuckDuckGo": search_duckduckgo,
    "Wikipedia": search_wikipedia,
    "Google News": search_google_news,
}


def normalize_url(url):
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path.rstrip("/")
    return (host, path)


def rank_results(results, query):
    terms = {word.lower() for word in query.split() if len(word) > 1}
    def score(item):
        title = item.get("title", "").lower()
        snippet = item.get("snippet", "").lower()
        title_hits = sum(1 for term in terms if term in title)
        body_hits = sum(1 for term in terms if term in snippet)
        return title_hits * 3 + body_hits
    return sorted(results, key=score, reverse=True)


def run_search(query, limit):
    collected, provider_status = [], {}
    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
        futures = {pool.submit(fn, query, limit): name for name, fn in PROVIDERS.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                items = future.result()
                provider_status[name] = {"ok": True, "count": len(items)}
                collected.extend(items)
            except Exception as exc:
                app.logger.warning("Provider %s failed: %s", name, exc)
                provider_status[name] = {"ok": False, "error": type(exc).__name__}
    unique, seen = [], set()
    for item in collected:
        key = normalize_url(item.get("url", ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    ranked = rank_results(unique, query)
    return ranked[:limit], provider_status


@app.get("/api/search")
@limiter.limit("30 per minute")
def search():
    if not valid_key():
        return jsonify({"error": "API key thiếu hoặc không hợp lệ. Gửi header X-API-Key.", "code": "invalid_api_key"}), 401
    query = request.args.get("q", "").strip()
    try:
        limit = int(request.args.get("limit", "5"))
    except ValueError:
        return jsonify({"error": "limit phải là số nguyên từ 1 đến 10."}), 400
    limit = max(1, min(10, limit))
    if not query:
        return jsonify({"error": "Thiếu tham số q."}), 400
    if len(query) > 300:
        return jsonify({"error": "Từ khóa quá dài (tối đa 300 ký tự)."}), 400
    try:
        results, providers = run_search(query, limit)
        return jsonify({"query": query, "count": len(results), "results": results,
                        "providers": providers, "version": VERSION})
    except Exception:
        app.logger.exception("Search aggregation failed")
        return jsonify({"error": "Lỗi tổng hợp kết quả tìm kiếm.", "code": "search_failed"}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
