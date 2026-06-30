#!/usr/bin/env python3
"""Serve an online-Wikidata walk inspector.

Input:
  User text from HTTP query parameter `q`.

Output:
  A read-only HTML/API service that performs:
    1. Wikidata wbsearchentities for the input term.
    2. Special:EntityData fetch for matched entities.
    3. External-KG walks through selected Wikidata properties.
    4. Optional sibling lookup with SPARQL:
       source --property--> anchor <--same property-- sibling.

Routes:
  GET /: interactive HTML page.
  GET /api/search?q=...: online KG lookup and walks.
  GET /api/health: service configuration.

Notes:
  This service does not read the local code table/overlay. A small local cache
  is used only to avoid repeatedly requesting the same Wikidata URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


USER_AGENT = "kg-build-data-online-wikidata-walk/0.1"
SEARCH_URL = "https://www.wikidata.org/w/api.php"
ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
SPARQL_URL = "https://query.wikidata.org/sparql"

WALK_PROPS = {
    "P31": "instance_of",
    "P279": "subclass_of",
    "P452": "industry",
    "P361": "part_of",
    "P1269": "facet_of",
    "P749": "parent_organization",
}


def cache_name(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json"


def best_lang(values: Dict[str, Dict[str, str]], *langs: str) -> str:
    for lang in langs:
        if values.get(lang, {}).get("value"):
            return values[lang]["value"]
    for item in values.values():
        if item.get("value"):
            return item["value"]
    return ""


def entity_label(entity: Dict[str, Any]) -> str:
    return best_lang(entity.get("labels", {}), "zh", "zh-hans", "en")


def entity_desc(entity: Dict[str, Any]) -> str:
    return best_lang(entity.get("descriptions", {}), "zh", "zh-hans", "en")


def claim_entity_ids(entity: Dict[str, Any], prop_id: str) -> List[str]:
    out: List[str] = []
    for claim in entity.get("claims", {}).get(prop_id, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and value.get("id"):
            out.append(value["id"])
    return out


class WikidataClient:
    def __init__(self, cache_dir: Path, timeout: int, retries: int, ignore_proxy: bool):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.retries = retries
        self.ignore_proxy = ignore_proxy
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if ignore_proxy else urllib.request.build_opener()

    def fetch_json(self, url: str) -> Dict[str, Any]:
        path = self.cache_dir / cache_name(url)
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                time.sleep(0.15)
                return data
            except Exception as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise
                time.sleep(min(20, 2 ** attempt * 3))
        raise RuntimeError(f"request failed: {last_error}")

    def search(self, term: str, limit: int) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        seen = set()
        for lang in ["zh", "en"]:
            params = urllib.parse.urlencode(
                {
                    "action": "wbsearchentities",
                    "search": term,
                    "language": lang,
                    "uselang": "zh",
                    "format": "json",
                    "limit": str(limit),
                }
            )
            data = self.fetch_json(f"{SEARCH_URL}?{params}")
            for hit in data.get("search", []):
                qid = hit.get("id")
                if qid and qid not in seen:
                    seen.add(qid)
                    hit["search_language"] = lang
                    hits.append(hit)
            if hits:
                break
        return hits[:limit]

    def entity(self, qid: str) -> Dict[str, Any]:
        data = self.fetch_json(ENTITY_URL.format(qid=urllib.parse.quote(qid)))
        return data.get("entities", {}).get(qid, {})

    def sparql(self, query: str) -> Dict[str, Any]:
        params = urllib.parse.urlencode({"query": query, "format": "json"})
        return self.fetch_json(f"{SPARQL_URL}?{params}")


class OnlineWalkService:
    def __init__(self, client: WikidataClient, search_limit: int, anchor_limit: int, sibling_limit: int, enable_sparql: bool):
        self.client = client
        self.search_limit = search_limit
        self.anchor_limit = anchor_limit
        self.sibling_limit = sibling_limit
        self.enable_sparql = enable_sparql

    def label_for_qid(self, qid: str) -> Dict[str, str]:
        entity = self.client.entity(qid)
        return {
            "id": qid,
            "label": entity_label(entity),
            "description": entity_desc(entity),
            "url": f"https://www.wikidata.org/wiki/{qid}",
        }

    def sibling_entities(self, source_qid: str, prop_id: str, anchor_qid: str) -> List[Dict[str, str]]:
        if not self.enable_sparql:
            return []
        query = f"""
SELECT ?item ?itemLabel ?itemDescription WHERE {{
  ?item wdt:{prop_id} wd:{anchor_qid}.
  FILTER(?item != wd:{source_qid})
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,en". }}
}}
LIMIT {self.sibling_limit}
"""
        try:
            data = self.client.sparql(query)
        except Exception as exc:
            return [{"id": "", "label": "", "description": f"sparql_failed: {exc}", "url": ""}]
        rows = []
        for item in data.get("results", {}).get("bindings", []):
            uri = item.get("item", {}).get("value", "")
            qid = uri.rsplit("/", 1)[-1] if uri else ""
            if qid:
                rows.append(
                    {
                        "id": qid,
                        "label": item.get("itemLabel", {}).get("value", ""),
                        "description": item.get("itemDescription", {}).get("value", ""),
                        "url": f"https://www.wikidata.org/wiki/{qid}",
                    }
                )
        return rows

    def walks_for_entity(self, qid: str, entity: Dict[str, Any]) -> List[Dict[str, Any]]:
        walks: List[Dict[str, Any]] = []
        for prop_id, relation in WALK_PROPS.items():
            for anchor_qid in claim_entity_ids(entity, prop_id)[: self.anchor_limit]:
                try:
                    anchor = self.label_for_qid(anchor_qid)
                except Exception as exc:
                    anchor = {"id": anchor_qid, "label": "", "description": f"anchor_fetch_failed: {exc}", "url": f"https://www.wikidata.org/wiki/{anchor_qid}"}
                siblings = self.sibling_entities(qid, prop_id, anchor_qid)
                walks.append(
                    {
                        "walk_type": "external_kg_shared_anchor",
                        "source_id": qid,
                        "property_id": prop_id,
                        "property_label": relation,
                        "anchor": anchor,
                        "siblings": siblings,
                        "walked_relations": [
                            f"{qid} --{prop_id}/{relation}--> {anchor_qid}",
                            f"{anchor_qid} <--{prop_id}/{relation}-- sibling_entities",
                        ],
                        "source": "Wikidata EntityData + Wikidata SPARQL" if self.enable_sparql else "Wikidata EntityData",
                    }
                )
        return walks

    def search(self, term: str) -> Dict[str, Any]:
        results = []
        for hit in self.client.search(term, self.search_limit):
            qid = hit.get("id", "")
            try:
                entity = self.client.entity(qid)
                label = entity_label(entity) or hit.get("label", "")
                description = entity_desc(entity) or hit.get("description", "")
                walks = self.walks_for_entity(qid, entity)
                error = ""
            except Exception as exc:
                label = hit.get("label", "")
                description = hit.get("description", "")
                walks = []
                error = str(exc)
            results.append(
                {
                    "id": qid,
                    "label": label,
                    "description": description,
                    "search_language": hit.get("search_language", ""),
                    "url": f"https://www.wikidata.org/wiki/{qid}",
                    "error": error,
                    "walks": walks,
                }
            )
        return {"query": term, "source": "online_wikidata", "results": results}


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Online KG Walk Inspector</title>
<style>
body{margin:0;background:#f6f7f9;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}
header{background:#fff;border-bottom:1px solid #d9dee7;padding:18px 24px;position:sticky;top:0;z-index:2}
main{max-width:1180px;margin:0 auto;padding:18px} h1{font-size:22px;margin:0 0 10px} h2{font-size:18px;margin:0 0 8px} h3{font-size:15px;margin:10px 0 6px}
input{width:min(720px,calc(100% - 120px));padding:10px 12px;border:1px solid #cbd5e1;border-radius:6px;font-size:15px}
button{padding:10px 14px;border:1px solid #0f766e;background:#0f766e;color:white;border-radius:6px;font-size:15px;cursor:pointer}
.hint{color:#64748b}.card{background:#fff;border:1px solid #dce2ea;border-radius:8px;margin:0 0 14px;padding:16px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.badge{display:inline-block;border:1px solid #cbd5e1;border-radius:999px;padding:2px 8px;margin:2px;background:#f8fafc;font-size:12px}
.source{background:#eff6ff;border-color:#93c5fd;color:#1d4ed8}.prop{background:#fefce8;border-color:#fde68a;color:#854d0e}.sib{background:#ecfdf5;border-color:#86efac;color:#166534}
.walk{border-left:4px solid #0f766e;background:#f8fafc;padding:10px;margin:8px 0;border-radius:6px}.path{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
a{color:#0f766e;text-decoration:none} a:hover{text-decoration:underline}
</style></head><body>
<header><h1>Online KG Walk Inspector</h1>
<div><input id="q" placeholder="输入任意词，实时查询 Wikidata 外部 KG" autofocus>
<button onclick="search()">Search</button></div>
<div class="hint">输入词 -> Wikidata search -> EntityData 锚点 -> SPARQL sibling。该页面不读取本地码表。</div></header>
<main><div id="out" class="hint">请输入一个词开始。首次请求会较慢，结果会缓存。</div></main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function link(x){ return x.url ? `<a href="${esc(x.url)}" target="_blank">${esc(x.label || x.id)}</a>` : esc(x.label || x.id); }
function walkHtml(w){
  const sibs=(w.siblings||[]).map(s=>`<span class="badge sib">${link(s)}${s.description ? '：'+esc(s.description) : ''}</span>`).join('') || '<span class="hint">无 sibling 或 SPARQL 失败</span>';
  const path=(w.walked_relations||[]).map(x=>`<div class="path">${esc(x)}</div>`).join('');
  return `<div class="walk"><div><span class="badge prop">${esc(w.property_id)} / ${esc(w.property_label)}</span><span class="badge source">${esc(w.source)}</span></div>
  <div>anchor：<span class="badge">${link(w.anchor)}${w.anchor.description ? '：'+esc(w.anchor.description) : ''}</span></div>
  <h3>siblings</h3><div>${sibs}</div><h3>path</h3>${path}</div>`;
}
function entityHtml(r){
  const walks=(r.walks||[]).map(walkHtml).join('') || '<p class="hint">没有可用外部 KG walk。</p>';
  return `<section class="card"><h2>${link(r)} <span class="badge">${esc(r.id)}</span> <span class="badge">${esc(r.search_language)}</span></h2>
  <p>${esc(r.description)}</p>${r.error ? `<p class="hint">error: ${esc(r.error)}</p>` : ''}<h3>External KG walks</h3>${walks}</section>`;
}
async function search(){
  const q=document.getElementById('q').value.trim();
  if(!q){return}
  document.getElementById('out').innerHTML = '<p class="hint">查询在线 KG 中，请等待...</p>';
  const res=await fetch('/api/search?q='+encodeURIComponent(q));
  const data=await res.json();
  document.getElementById('out').innerHTML = data.results.length ? data.results.map(entityHtml).join('') : '<p class="hint">Wikidata 没有匹配结果。</p>';
}
document.getElementById('q').addEventListener('keydown', e => { if(e.key==='Enter') search(); });
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    service: OnlineWalkService
    config: Dict[str, Any]

    def send_json(self, data: Dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/health":
            self.send_json(self.config)
            return
        if parsed.path == "/api/search":
            q = urllib.parse.parse_qs(parsed.query).get("q", [""])[0].strip()
            if not q:
                self.send_json({"query": "", "source": "online_wikidata", "results": []})
                return
            try:
                self.send_json(self.service.search(q))
            except Exception as exc:
                self.send_json({"query": q, "source": "online_wikidata", "error": str(exc), "results": []})
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[online-kg-walk] %s %s\n" % (self.address_string(), fmt % args))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cache-dir", type=Path, default=Path("artifacts/code_table_pipeline_v3/outputs/online_kg_walk_service/cache"))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--search-limit", type=int, default=2)
    parser.add_argument("--anchor-limit", type=int, default=3)
    parser.add_argument("--sibling-limit", type=int, default=6)
    parser.add_argument("--disable-sparql", action="store_true")
    parser.add_argument("--ignore-proxy", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = WikidataClient(args.cache_dir, args.timeout, args.retries, args.ignore_proxy)
    Handler.service = OnlineWalkService(client, args.search_limit, args.anchor_limit, args.sibling_limit, not args.disable_sparql)
    Handler.config = {
        "source": "online_wikidata",
        "host": args.host,
        "port": args.port,
        "cache_dir": str(args.cache_dir),
        "timeout": args.timeout,
        "retries": args.retries,
        "search_limit": args.search_limit,
        "anchor_limit": args.anchor_limit,
        "sibling_limit": args.sibling_limit,
        "sparql_enabled": not args.disable_sparql,
        "ignore_proxy": args.ignore_proxy,
        "walk_properties": WALK_PROPS,
    }
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"online KG walk service: http://{args.host}:{args.port}", flush=True)
    print(json.dumps(Handler.config, ensure_ascii=False, indent=2), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
