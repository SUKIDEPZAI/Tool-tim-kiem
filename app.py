import hashlib
import os
import secrets
import sqlite3
import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from urllib.parse import quote, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

VERSION = "5.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "keys.db"))
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 SearchPilot/5.0"
TIMEOUT = (3.05, 8)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"})
CACHE = {}
CACHE_LOCK = threading.Lock()
CACHE_TTL = 240

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
app.config["JSON_SORT_KEYS"] = False
CORS(app, resources={r"/api/*": {"origins": "*"}})
limiter = Limiter(get_remote_address, app=app, default_limits=[],
                  storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))

def connect_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '',
        key_hash TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1)""")
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
        return conn.execute("SELECT 1 FROM api_keys WHERE key_hash=? AND active=1",
                            (hash_key(raw),)).fetchone() is not None
    finally:
        conn.close()

@app.errorhandler(429)
def rate_limit_error(_):
    return jsonify({"error":"Quá nhiều yêu cầu. Hãy đợi rồi thử lại.","code":"rate_limited"}),429

@app.get("/")
def home():
    return send_from_directory(FRONTEND_DIR,"index.html")

@app.get("/api/health")
def health():
    return jsonify({"ok":True,"service":"SearchPilot AI","version":VERSION,
                    "github_deep_search":True,"providers":len(PROVIDERS)})

@app.post("/api/keys")
@limiter.limit("5 per hour")
def create_key():
    body=request.get_json(silent=True) or {}
    name=str(body.get("name","")).strip()[:80]
    api_key="ws_"+secrets.token_urlsafe(32)
    conn=connect_db()
    try:
        conn.execute("INSERT INTO api_keys(name,key_hash,created_at) VALUES(?,?,?)",
                     (name,hash_key(api_key),int(time.time())))
        conn.commit()
    finally: conn.close()
    return jsonify({"api_key":api_key,"name":name,
                    "warning":"Key chỉ hiển thị một lần; máy chủ lưu bản băm."}),201

def get(url, params=None, headers=None, timeout=TIMEOUT):
    response=SESSION.get(url,params=params,headers=headers,timeout=timeout)
    response.raise_for_status()
    return response

def cache_get(key):
    with CACHE_LOCK:
        item=CACHE.get(key)
        if item and time.time()-item[0] < CACHE_TTL:
            return item[1]
        if item: CACHE.pop(key,None)
    return None

def cache_put(key,value):
    with CACHE_LOCK:
        CACHE[key]=(time.time(),value)
        if len(CACHE)>250:
            cutoff=time.time()-CACHE_TTL
            for k,v in list(CACHE.items()):
                if v[0]<cutoff: CACHE.pop(k,None)

def ddg_url(href):
    if not href:return ""
    if href.startswith("//"):href="https:"+href
    p=urlparse(href)
    if p.netloc.endswith("duckduckgo.com") and p.path.startswith("/l/"):
        return unquote(parse_qs(p.query).get("uddg",[""])[0])
    return href

def search_duckduckgo(q,n):
    html=get("https://html.duckduckgo.com/html/",{"q":q}).text
    soup=BeautifulSoup(html,"html.parser");out=[]
    for item in soup.select(".result"):
        a=item.select_one(".result__a");sn=item.select_one(".result__snippet")
        if not a:continue
        url=ddg_url(a.get("href",""));title=a.get_text(" ",strip=True)
        if title and url.startswith(("http://","https://")):
            out.append({"title":title,"url":url,"snippet":sn.get_text(" ",strip=True) if sn else "",
                        "source":"DuckDuckGo","category":"web"})
        if len(out)>=n:break
    return out

def search_bing(q,n):
    soup=BeautifulSoup(get("https://www.bing.com/search",{"q":q,"count":min(n,20)}).text,"html.parser");out=[]
    for item in soup.select("li.b_algo"):
        a=item.select_one("h2 a");sn=item.select_one(".b_caption p") or item.select_one("p")
        if not a:continue
        url=a.get("href","");title=a.get_text(" ",strip=True)
        if title and url.startswith(("http://","https://")):
            out.append({"title":title,"url":url,"snippet":sn.get_text(" ",strip=True) if sn else "",
                        "source":"Bing","category":"web"})
        if len(out)>=n:break
    return out

def parse_rss(url,params,source,category,n):
    root=ET.fromstring(get(url,params).content);out=[]
    for item in root.findall(".//item")[:n]:
        title=(item.findtext("title") or "").strip();link=(item.findtext("link") or "").strip()
        desc=BeautifulSoup(item.findtext("description") or "","html.parser").get_text(" ",strip=True)
        if title and link.startswith(("http://","https://")):
            out.append({"title":title,"url":link,"snippet":desc[:800],"source":source,"category":category})
    return out

def search_google_news(q,n):
    return parse_rss("https://news.google.com/rss/search",{"q":q,"hl":"vi","gl":"VN","ceid":"VN:vi"},"Google News RSS","news",n)

def search_bing_news(q,n):
    return parse_rss("https://www.bing.com/news/search",{"q":q,"format":"rss","setlang":"vi"},"Bing News RSS","news",n)

def search_wikipedia(q,n):
    data=get("https://vi.wikipedia.org/w/api.php",{"action":"query","list":"search","srsearch":q,"srlimit":min(n,10),"format":"json","utf8":1}).json()
    out=[]
    for item in data.get("query",{}).get("search",[]):
        title=item.get("title","")
        out.append({"title":title,"url":"https://vi.wikipedia.org/wiki/"+quote(title.replace(" ","_"),safe="_()"),
                    "snippet":BeautifulSoup(item.get("snippet",""),"html.parser").get_text(" ",strip=True),
                    "source":"Wikipedia tiếng Việt","category":"knowledge"})
    return out

def search_wikidata(q,n):
    data=get("https://www.wikidata.org/w/api.php",{"action":"wbsearchentities","search":q,"language":"vi","format":"json","limit":min(n,10)}).json()
    return [{"title":x.get("label") or x.get("id",""),"url":"https://www.wikidata.org/wiki/"+x.get("id",""),
             "snippet":x.get("description",""),"source":"Wikidata","category":"knowledge"}
            for x in data.get("search",[]) if x.get("id")]

def search_stackexchange(q,n):
    data=get("https://api.stackexchange.com/2.3/search/advanced",{"site":"stackoverflow","q":q,"pagesize":min(n,20),"order":"desc","sort":"relevance"}).json()
    return [{"title":BeautifulSoup(x.get("title",""),"html.parser").get_text(" ",strip=True),
             "url":x.get("link",""),"snippet":f"Điểm: {x.get('score',0)} · Câu trả lời: {x.get('answer_count',0)}",
             "source":"Stack Overflow","category":"technical"} for x in data.get("items",[])]

def github_query_variants(q):
    # Multiple complementary queries deliberately include tiny/zero-star repositories.
    safe=" ".join(q.replace("\n"," ").split())[:180]
    return [
        ("broad", f"{safe} in:name,description,readme"),
        ("tiny", f"{safe} in:name,description,readme stars:0..5"),
        ("name", f"{safe} in:name"),
    ]

def search_github_repositories(q,n):
    cache_key=("github-repos",q.lower(),n)
    cached=cache_get(cache_key)
    if cached is not None:return cached
    found=[];seen=set();status=[]
    # Search API has a strict anonymous search quota. Use three varied queries and cache briefly.
    for label,query in github_query_variants(q):
        try:
            data=get("https://api.github.com/search/repositories",
                     {"q":query,"per_page":min(30,max(10,n)),"sort":"updated" if label!="tiny" else "stars",
                      "order":"asc" if label=="tiny" else "desc"},
                     headers={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"})
            for repo in data.json().get("items",[]):
                url=repo.get("html_url","")
                if not url or url in seen:continue
                seen.add(url)
                topics=repo.get("topics",[]) or []
                description=repo.get("description") or "Không có mô tả; xem README và mã nguồn trong repository."
                language=repo.get("language") or "Chưa xác định"
                meta=f"Ngôn ngữ: {language} · ⭐ {repo.get('stargazers_count',0)} · Fork: {repo.get('forks_count',0)} · Cập nhật: {(repo.get('updated_at') or '')[:10]}"
                if repo.get("archived"):meta+=" · Đã lưu trữ"
                out={"title":repo.get("full_name") or repo.get("name") or "GitHub repository",
                     "url":url,"snippet":description+" | "+meta,
                     "source":"GitHub Repositories","category":"code",
                     "repository":{"full_name":repo.get("full_name"),"language":language,
                                   "stars":repo.get("stargazers_count",0),"forks":repo.get("forks_count",0),
                                   "size_kb":repo.get("size",0),"topics":topics,
                                   "default_branch":repo.get("default_branch","main"),
                                   "created_at":repo.get("created_at"),"updated_at":repo.get("updated_at"),
                                   "archived":repo.get("archived",False),
                                   "license":(repo.get("license") or {}).get("spdx_id")}}
                found.append(out)
            status.append({"query_type":label,"ok":True,"count":len(data.json().get("items",[]))})
        except Exception as exc:
            status.append({"query_type":label,"ok":False,"error":type(exc).__name__})
    # Prioritize close name/description matches, then smaller repos are not discarded.
    terms={w.lower() for w in q.split() if len(w)>1}
    def score(item):
        title=item["title"].lower();desc=item["snippet"].lower()
        return sum(5 for t in terms if t in title)+sum(2 for t in terms if t in desc)
    found.sort(key=lambda x:(score(x),-int(x.get("repository",{}).get("stars",0))),reverse=True)
    result={"items":found[:max(n,30)],"queries":status}
    cache_put(cache_key,result)
    return result

def search_sourcegraph(q,n):
    cache_key=("sourcegraph",q.lower(),n)
    cached=cache_get(cache_key)
    if cached is not None:return cached
    # Public indexed-code search; it may be unavailable or have its own service limits.
    query=f"context:global {q} count:{min(max(n,10),50)}"
    response=get("https://sourcegraph.com/.api/search/stream",{"q":query,"v":"V3"},
                 headers={"Accept":"text/event-stream"},timeout=(4,10))
    out=[];seen=set()
    for line in response.text.splitlines():
        if not line.startswith("data:"):continue
        raw=line[5:].strip()
        try:data=__import__("json").loads(raw)
        except Exception:continue
        if data.get("type")!="matches":continue
        for match in data.get("matches",[]):
            repo=match.get("repository","")
            path=match.get("path","")
            url=""
            commit=match.get("commit","")
            if repo.startswith("github.com/"):
                parts=repo.split("/",2)
                if len(parts)>=3:
                    url="https://github.com/"+parts[1]+"/"+parts[2]+"/blob/"+(commit or "HEAD")+"/"+path
            if not url:
                url="https://sourcegraph.com/"+repo+"/-/blob/"+path
            key=(repo,path)
            if key in seen:continue
            seen.add(key)
            previews=[]
            for lm in match.get("lineMatches",[])[:2]:
                previews.append((lm.get("line") or "").strip()[:220])
            out.append({"title":f"{repo} · {path}","url":url,"snippet":" | ".join(previews) or "Khớp truy vấn mã nguồn công khai.",
                        "source":"Sourcegraph Code Search","category":"code",
                        "code_match":{"repository":repo,"path":path,"commit":commit}})
            if len(out)>=n:break
        if len(out)>=n:break
    cache_put(cache_key,out)
    return out

def search_github(q,n):
    repos=search_github_repositories(q,n)
    code=[]
    try:code=search_sourcegraph(q,min(n,15))
    except Exception as exc:app.logger.info("Sourcegraph unavailable: %s",type(exc).__name__)
    # Include repo search diagnostics in source status as synthetic field.
    return repos["items"][:n]+code[:n],{"GitHub Repositories":{"ok":bool(repos["items"]),"count":len(repos["items"]),"query_variants":repos["queries"]},
                                       "Sourcegraph Code Search":{"ok":bool(code),"count":len(code)}}

def search_internet_archive(q,n):
    data=get("https://archive.org/advancedsearch.php",{"q":q,"fl[]":["identifier","title","description","mediatype"],"rows":min(n,15),"page":1,"output":"json"}).json()
    out=[]
    for x in data.get("response",{}).get("docs",[]):
        ident=x.get("identifier","")
        if ident:
            desc=x.get("description","")
            if isinstance(desc,list):desc=" ".join(desc)
            out.append({"title":x.get("title",ident),"url":"https://archive.org/details/"+ident,
                        "snippet":str(desc)[:500],"source":"Internet Archive","category":"archive"})
    return out

PROVIDERS={
 "DuckDuckGo":(search_duckduckgo,{"web"}),
 "Bing":(search_bing,{"web"}),
 "Google News RSS":(search_google_news,{"news"}),
 "Bing News RSS":(search_bing_news,{"news"}),
 "Wikipedia tiếng Việt":(search_wikipedia,{"knowledge"}),
 "Wikidata":(search_wikidata,{"knowledge"}),
 "Stack Overflow":(search_stackexchange,{"technical","code"}),
 "GitHub + Sourcegraph":(search_github,{"github","code","technical"}),
 "Internet Archive":(search_internet_archive,{"archive","knowledge"}),
}
MODES={
 "all":set(PROVIDERS),
 "web":{"DuckDuckGo","Bing","Wikipedia tiếng Việt","Wikidata"},
 "news":{"Google News RSS","Bing News RSS","DuckDuckGo","Bing"},
 "technical":{"Stack Overflow","GitHub + Sourcegraph","DuckDuckGo","Bing"},
 "code":{"GitHub + Sourcegraph","Stack Overflow"},
 "github":{"GitHub + Sourcegraph"},
 "academic":{"Wikipedia tiếng Việt","Wikidata","Internet Archive","DuckDuckGo","Bing"},
 "knowledge":{"Wikipedia tiếng Việt","Wikidata","Internet Archive","DuckDuckGo","Bing"},
}

def url_key(url):
    p=urlparse(url)
    return ((p.netloc or "").lower().removeprefix("www."),p.path.rstrip("/"))

def rank_results(results,q):
    terms={w.lower().strip(".,?!:;()[]{}\"'") for w in q.split() if len(w)>1}
    def score(x):
        title=x.get("title","").lower();snippet=x.get("snippet","").lower()
        return sum(4 for t in terms if t in title)+sum(1 for t in terms if t in snippet)
    return sorted(results,key=score,reverse=True)

def run_search(q,limit,mode):
    selected=MODES.get(mode,MODES["all"])
    chosen={name:fn for name,(fn,cats) in PROVIDERS.items() if name in selected}
    gathered=[];status={}
    with ThreadPoolExecutor(max_workers=max(1,min(len(chosen),9))) as pool:
        futures={pool.submit(fn,q,min(limit,20)):name for name,fn in chosen.items()}
        try:
            for future in as_completed(futures,timeout=12):
                name=futures[future]
                try:
                    value=future.result()
                    if name=="GitHub + Sourcegraph":
                        items,diag=value;gathered.extend(items);status.update(diag)
                    else:
                        gathered.extend(value);status[name]={"ok":True,"count":len(value)}
                except Exception as exc:
                    status[name]={"ok":False,"error":type(exc).__name__}
                    app.logger.info("Provider %s failed: %s",name,type(exc).__name__)
        except FuturesTimeoutError:
            for future,name in futures.items():
                if name not in status:status[name]={"ok":False,"error":"timeout"}
    deduped=[];seen=set()
    for item in gathered:
        key=url_key(item.get("url",""))
        if not key[0] or key in seen:continue
        seen.add(key);deduped.append(item)
    return rank_results(deduped,q)[:limit],status

@app.get("/api/search")
@limiter.limit("20 per minute")
def search():
    if not valid_key():return jsonify({"error":"API key thiếu hoặc không hợp lệ. Gửi header X-API-Key.","code":"invalid_api_key"}),401
    q=request.args.get("q","").strip();mode=request.args.get("mode","all").strip().lower()
    try:limit=int(request.args.get("limit","20"))
    except ValueError:return jsonify({"error":"limit phải là số nguyên từ 1 đến 30."}),400
    limit=max(1,min(30,limit))
    if not q:return jsonify({"error":"Thiếu từ khóa q."}),400
    if len(q)>250:return jsonify({"error":"Từ khóa tối đa 250 ký tự."}),400
    if mode not in MODES:mode="all"
    results,status=run_search(q,limit,mode)
    return jsonify({"query":q,"mode":mode,"count":len(results),"results":results,
                    "providers":status,"version":VERSION,
                    "notice":"Không có công cụ nào bao phủ toàn bộ GitHub/Internet. Kết quả nhỏ/ít sao được tìm qua truy vấn riêng; nguồn có thể giới hạn hoặc không khả dụng."})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
