import hashlib
import os
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

VERSION = "3.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "keys.db"))
USER_AGENT = "Mozilla/5.0 (compatible; WebSearchBot/3.0; +https://tool-tim-kiem.onrender.com)"
TIMEOUT = (3.05, 8)

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
app.config["JSON_SORT_KEYS"] = False
CORS(app, resources={r"/api/*": {"origins": "*"}})
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)

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
            "SELECT 1 FROM api_keys WHERE key_hash=? AND active=1",
            (hash_key(raw),)
        ).fetchone() is not None
    finally:
        conn.close()

@app.errorhandler(429)
def rate_limit_error(_):
    return jsonify({"error": "Quá nhiều yêu cầu. Vui lòng đợi rồi thử lại.", "code": "rate_limited"}), 429

@app.get("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")

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
        conn.execute(
            "INSERT INTO api_keys(name,key_hash,created_at) VALUES(?,?,?)",
            (name, hash_key(api_key), int(time.time()))
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({
        "api_key": api_key,
        "name": name,
        "warning": "Hãy lưu key ngay. Máy chủ chỉ lưu bản băm nên không thể xem lại key."
    }), 201

def clean_ddg_url(href):
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        return unquote(parse_qs(parsed.query).get("uddg", [""])[0])
    return href

def search_duckduckgo(query, limit):
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT
    )
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
        "https://vi.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": min(limit, 10), "format": "json", "utf8": 1
        },
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
    )
    response.raise_for_status()
    results = []
    for item in response.json().get("query", {}).get("search", []):
        title = BeautifulSoup(item.get("title", ""), "html.parser").get_text(" ", strip=True)
        snippet = BeautifulSoup(item.get("snippet", ""), "html.parser").get_text(" ", strip=True)
        page_url = "https://vi.wikipedia.org/wiki/" + requests.utils.quote(title.replace(" ", "_"), safe="_()")
        results.append({"title": title, "url": page_url, "snippet": snippet, "source": "Wikipedia tiếng Việt"})
    return results

def search_google_news(query, limit):
    response = requests.get(
        "https://news.google.com/rss/search",
        params={"q": query, "hl": "vi", "gl": "VN", "ceid": "VN:vi"},
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "xml")
    results = []
    for item in soup.find_all("item")[:limit]:
        title = item.title.get_text(" ", strip=True) if item.title else ""
        link = item.link.get_text(strip=True) if item.link else ""
        description = item.description.get_text(" ", strip=True) if item.description else ""
        snippet = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
        if title and link.startswith("https://"):
            results.append({"title": title, "url": link, "snippet": snippet[:700], "source": "Google News"})
    return results

PROVIDERS = {
    "DuckDuckGo": search_duckduckgo,
    "Wikipedia tiếng Việt": search_wikipedia,
    "Google News": search_google_news,
}

def normalize_url(url):
    parsed = urlparse(url)
    return ((parsed.netloc or "").lower(), parsed.path.rstrip("/"))

def rank_results(results, query):
    terms = {word.lower().strip(".,?!:;()[]{}\"'") for word in query.split() if len(word) > 1}
    def score(item):
        title = item.get("title", "").lower()
        snippet = item.get("snippet", "").lower()
        return sum(3 for term in terms if term in title) + sum(1 for term in terms if term in snippet)
    return sorted(results, key=score, reverse=True)

def run_search(query, limit):
    collected, provider_status = [], {}
    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
        futures = {pool.submit(fn, query, limit): name for name, fn in PROVIDERS.items()}
        try:
            for future in as_completed(futures, timeout=11):
                name = futures[future]
                try:
                    items = future.result()
                    provider_status[name] = {"ok": True, "count": len(items)}
                    collected.extend(items)
                except Exception as exc:
                    app.logger.warning("Search provider %s failed: %s", name, type(exc).__name__)
                    provider_status[name] = {"ok": False, "error": type(exc).__name__}
        except FuturesTimeoutError:
            for future, name in futures.items():
                if name not in provider_status:
                    provider_status[name] = {"ok": False, "error": "timeout"}
    unique, seen = [], set()
    for item in collected:
        key = normalize_url(item.get("url", ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return rank_results(unique, query)[:limit], provider_status

@app.get("/api/search")
@limiter.limit("30 per minute")
def search():
    if not valid_key():
        return jsonify({"error": "API key thiếu hoặc không hợp lệ. Gửi header X-API-Key.", "code": "invalid_api_key"}), 401
    query = request.args.get("q", "").strip()
    try:
        limit = int(request.args.get("limit", "8"))
    except ValueError:
        return jsonify({"error": "limit phải là số nguyên từ 1 đến 10."}), 400
    limit = max(1, min(10, limit))
    if not query:
        return jsonify({"error": "Thiếu từ khóa q."}), 400
    if len(query) > 300:
        return jsonify({"error": "Từ khóa quá dài (tối đa 300 ký tự)."}), 400
    try:
        results, providers = run_search(query, limit)
        return jsonify({
            "query": query, "count": len(results), "results": results,
            "providers": providers, "version": VERSION
        })
    except Exception:
        app.logger.exception("Search aggregation failed")
        return jsonify({"error": "Lỗi tổng hợp kết quả tìm kiếm.", "code": "search_failed"}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
