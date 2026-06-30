#!/usr/bin/env python3
"""Serve a small UI/API for inspecting derived sibling-value walks.

Input:
  --overlay-jsonl: domain overlay JSONL produced by
    step0_build_domain_overlays_from_open_kb.py. It should contain
    concepts and derived_walks.
  --host / --port: HTTP bind address.

Routes:
  GET /: interactive HTML page.
  GET /api/search?q=...: fuzzy concept lookup and derived walk results.
  GET /api/domains: compact domain/concept inventory.

This service is intentionally read-only and does not call LLMs or online KGs.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import parse_qs, urlparse


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def prop_text(item: Dict[str, Any]) -> str:
    if item.get("property_id"):
        return f"{item.get('property_id')}={item.get('value_id')}"
    if item.get("type_id"):
        return f"type={item.get('type_id')}"
    return json.dumps(item, ensure_ascii=False)


def normalize(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def latin_tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 2}


def cjk_chars(text: str) -> set[str]:
    return {ch for ch in text if "\u4e00" <= ch <= "\u9fff"}


def score_text(query: str, haystack: str) -> float:
    qn = normalize(query)
    hn = normalize(haystack)
    if not qn or not hn:
        return 0.0
    score = 0.0
    if qn == hn:
        score += 100.0
    if qn in hn:
        score += 60.0 + min(20.0, len(qn))
    if hn in qn:
        score += 35.0
    q_tokens = latin_tokens(query)
    h_tokens = latin_tokens(haystack)
    if q_tokens and h_tokens:
        score += 20.0 * len(q_tokens & h_tokens) / max(1, len(q_tokens))
    q_chars = cjk_chars(query)
    h_chars = cjk_chars(haystack)
    if q_chars and h_chars:
        score += 25.0 * len(q_chars & h_chars) / max(1, len(q_chars))
    return score


class WalkIndex:
    def __init__(self, overlay_jsonl: Path):
        self.overlay_jsonl = overlay_jsonl
        self.domains = list(iter_jsonl(overlay_jsonl))
        self.concepts: List[Dict[str, Any]] = []
        self.walks: List[Dict[str, Any]] = []
        for domain in self.domains:
            domain_id = domain.get("domain_id", "")
            domain_label = domain.get("label", "")
            for concept in domain.get("concepts", []):
                item = dict(concept)
                item["domain_id"] = domain_id
                item["domain_label"] = domain_label
                self.concepts.append(item)
            for walk in domain.get("derived_walks", []):
                item = dict(walk)
                item["domain_id"] = domain_id
                item["domain_label"] = domain_label
                self.walks.append(item)

    def concept_haystack(self, concept: Dict[str, Any]) -> str:
        refs = []
        for ref in concept.get("open_kb_refs", [])[:8]:
            refs.append(str(ref.get("label") or ref.get("term") or ref.get("entity_id") or ""))
            refs.append(str(ref.get("description") or ""))
        maps = [prop_text(x) for x in concept.get("maps_to", [])]
        return " ".join(
            [
                str(concept.get("concept_id", "")),
                str(concept.get("label", "")),
                str(concept.get("description", "")),
                str(concept.get("domain_label", "")),
                " ".join(maps),
                " ".join(refs),
            ]
        )

    def search(self, query: str, limit: int = 8) -> Dict[str, Any]:
        scored = []
        for concept in self.concepts:
            score = score_text(query, self.concept_haystack(concept))
            if score > 0:
                scored.append((score, concept))
        scored.sort(key=lambda x: x[0], reverse=True)
        matches = []
        for score, concept in scored[:limit]:
            concept_id = concept.get("concept_id", "")
            outgoing = [w for w in self.walks if w.get("source_concept_id") == concept_id]
            incoming = [w for w in self.walks if w.get("target_concept_id") == concept_id]
            matches.append(
                {
                    "score": round(score, 3),
                    "domain_id": concept.get("domain_id", ""),
                    "domain_label": concept.get("domain_label", ""),
                    "concept_id": concept_id,
                    "label": concept.get("label", ""),
                    "description": concept.get("description", ""),
                    "maps_to": concept.get("maps_to", []),
                    "outgoing_walks": outgoing[:12],
                    "incoming_walks": incoming[:12],
                }
            )
        return {"query": query, "matches": matches}

    def inventory(self) -> Dict[str, Any]:
        rows = []
        for domain in self.domains:
            rows.append(
                {
                    "domain_id": domain.get("domain_id"),
                    "label": domain.get("label"),
                    "concept_count": len(domain.get("concepts", [])),
                    "derived_walk_count": len(domain.get("derived_walks", [])),
                    "concepts": [
                        {
                            "concept_id": c.get("concept_id"),
                            "label": c.get("label"),
                            "maps_to": c.get("maps_to", []),
                        }
                        for c in domain.get("concepts", [])
                    ],
                }
            )
        return {"overlay_jsonl": str(self.overlay_jsonl), "domains": rows}


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Sibling Value Walk Inspector</title>
<style>
body{margin:0;background:#f6f7f9;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}
header{background:#fff;border-bottom:1px solid #d9dee7;padding:18px 24px;position:sticky;top:0;z-index:2}
main{max-width:1180px;margin:0 auto;padding:18px} h1{font-size:22px;margin:0 0 10px} h2{font-size:18px;margin:0 0 8px} h3{font-size:15px;margin:10px 0 6px}
input{width:min(720px,calc(100% - 120px));padding:10px 12px;border:1px solid #cbd5e1;border-radius:6px;font-size:15px}
button{padding:10px 14px;border:1px solid #0f766e;background:#0f766e;color:white;border-radius:6px;font-size:15px;cursor:pointer}
.hint{color:#64748b}.card{background:#fff;border:1px solid #dce2ea;border-radius:8px;margin:0 0 14px;padding:16px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.badge{display:inline-block;border:1px solid #cbd5e1;border-radius:999px;padding:2px 8px;margin:2px;background:#f8fafc;font-size:12px}
.domain{background:#eff6ff;border-color:#93c5fd;color:#1d4ed8}.axis{background:#fefce8;border-color:#fde68a;color:#854d0e}.target{background:#ecfdf5;border-color:#86efac;color:#166534}
.walk{border-left:4px solid #0f766e;background:#f8fafc;padding:10px;margin:8px 0;border-radius:6px} pre{white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid #e5e7eb;border-radius:6px;padding:10px;font-size:12px}
dl{display:grid;grid-template-columns:130px 1fr;gap:6px 12px}dt{font-weight:650;color:#4b5563}dd{margin:0}
</style></head><body>
<header><h1>Sibling Value Walk Inspector</h1>
<div><input id="q" placeholder="输入词，例如：教育企业、学校、地方银行、支行、权限、绑卡" autofocus>
<button onclick="search()">Search</button></div>
<div class="hint">展示通用游走：共享锚点 -> 属性轴 -> sibling value -> 相邻概念。服务只读，不调用 LLM。</div></header>
<main><div id="out" class="hint">请输入一个词开始。</div></main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function prop(p){ if(p.property_id) return `${p.property_id}=${p.value_id}`; if(p.type_id) return `type=${p.type_id}`; return JSON.stringify(p); }
function walkHtml(w){
  const shared=(w.shared_anchor_properties||[]).map(p=>`<span class="badge target">${esc(prop(p))}</span>`).join('');
  const path=(w.walked_relations||[]).map(x=>`<div>${esc(x)}</div>`).join('');
  return `<div class="walk"><div><span class="badge">${esc(w.walk_type)}</span><span class="badge axis">${esc(w.axis_property_id)}</span>${shared}</div>
  <dl><dt>source</dt><dd>${esc(w.source_concept_id)} / ${esc(w.source_value_id)}</dd><dt>target</dt><dd>${esc(w.target_concept_id)} / ${esc(w.target_value_id)}</dd></dl>
  <h3>path</h3>${path}</div>`;
}
function conceptHtml(m){
  const maps=(m.maps_to||[]).map(p=>`<span class="badge">${esc(prop(p))}</span>`).join('');
  const outgoing=(m.outgoing_walks||[]).map(walkHtml).join('') || '<p class="hint">无 outgoing sibling walk</p>';
  const incoming=(m.incoming_walks||[]).map(walkHtml).join('') || '<p class="hint">无 incoming sibling walk</p>';
  return `<section class="card"><h2>${esc(m.label)} <span class="badge domain">${esc(m.domain_label)}</span> <span class="badge">score=${esc(m.score)}</span></h2>
  <dl><dt>concept_id</dt><dd>${esc(m.concept_id)}</dd><dt>description</dt><dd>${esc(m.description)}</dd><dt>maps_to</dt><dd>${maps}</dd></dl>
  <h3>Outgoing walks</h3>${outgoing}<h3>Incoming walks</h3>${incoming}</section>`;
}
async function search(){
  const q=document.getElementById('q').value.trim();
  if(!q){return}
  const res=await fetch('/api/search?q='+encodeURIComponent(q));
  const data=await res.json();
  document.getElementById('out').innerHTML = data.matches.length ? data.matches.map(conceptHtml).join('') : '<p class="hint">没有匹配到 concept。</p>';
}
document.getElementById('q').addEventListener('keydown', e => { if(e.key==='Enter') search(); });
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    index: WalkIndex

    def send_json(self, data: Dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self.send_json(self.index.search(query))
            return
        if parsed.path == "/api/domains":
            self.send_json(self.index.inventory())
            return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"not found")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[walk-service] {self.address_string()} {fmt % args}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--overlay-jsonl",
        type=Path,
        default=Path("artifacts/code_table_pipeline_v3/outputs/walk_mapping_service/open_kb_domain_overlays.with_derived_walks.jsonl"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.overlay_jsonl.exists():
        raise SystemExit(f"overlay jsonl not found: {args.overlay_jsonl}")
    Handler.index = WalkIndex(args.overlay_jsonl)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"walk mapping service: http://{args.host}:{args.port}", flush=True)
    print(f"overlay: {args.overlay_jsonl}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
