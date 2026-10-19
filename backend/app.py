import os
import secrets
import sqlite3
import time
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
frontend_origin = os.getenv("FRONTEND_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": frontend_origin}})

DB_PATH = os.getenv("DATABASE_PATH", "keys.db")
USER_AGENT = "Mozilla/5.0 (compatible; WebSearchBot/1.0; +https://duckduckgo.com)"


def connect_db():
    conn = sqlite3.connect(DB_PATH)
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
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def valid_key():
    raw = request.headers.get("X-API-Key", "").strip()
    if not raw:
        return False
    conn = connect_db()
    try:
        row = conn.execute(
            "SELECT id FROM api_keys WHERE key_hash=? AND active=1",
            (hash_key(raw),)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


@app.get("/")
def home():
    return jsonify({
        "service": "Web Search Bot API",
        "status": "ok",
        "endpoints": ["/api/health", "/api/keys", "/api/search?q=...&limit=5"]
    })


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "web-search-bot"})


@app.post("/api/keys")
def create_key():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", ""))[:80]
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
    # Raw key is returned only once. Database stores only its hash.
    return jsonify({"api_key": api_key, "name": name, "warning": "Save this key now; it cannot be retrieved later."}), 201


def search_duckduckgo(query, limit):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for item in soup.select(".result"):
        link = item.select_one(".result__a")
        snippet = item.select_one(".result__snippet")
        if not link:
            continue
        href = link.get("href", "")
        title = link.get_text(" ", strip=True)
        desc = snippet.get_text(" ", strip=True) if snippet else ""
        if href and title:
            results.append({"title": title, "url": href, "snippet": desc})
        if len(results) >= limit:
            break
    return results


@app.get("/api/search")
def search():
    if not valid_key():
        return jsonify({"error": "API key thiếu hoặc không hợp lệ. Gửi header X-API-Key."}), 401
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
        results = search_duckduckgo(query, limit)
        return jsonify({"query": query, "results": results, "count": len(results)})
    except requests.Timeout:
        return jsonify({"error": "Nguồn tìm kiếm phản hồi quá chậm. Thử lại sau."}), 504
    except requests.RequestException:
        app.logger.exception("Search provider request failed")
        return jsonify({"error": "Không kết nối được nguồn tìm kiếm. Thử lại sau."}), 502
    except Exception:
        app.logger.exception("Search parsing failed")
        return jsonify({"error": "Không xử lý được kết quả tìm kiếm."}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
