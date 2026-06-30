#!/usr/bin/env python3
"""Ground extracted entity phrases against local and online domain vocabularies.

输入：
  --terms: 待 grounding 的实体短语，可重复。
  --index: step0_download_domain_vocab_sources.py 产出的 domain_vocab_terms.jsonl。

输出：
  JSON/Markdown grounding 结果。

路由：
  local index -> DBpedia Lookup -> QLever DBpedia/YAGO/OSM -> OSM taginfo
  -> OpenAlex -> ROR -> OLS4
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


USER_AGENT = "kg-build-data-domain-grounding-router/0.1"


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def compact(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def tokens(text: Any) -> set[str]:
    s = str(text or "").lower()
    out = {t for t in re.split(r"[^a-z0-9]+", s) if len(t) >= 2}
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", str(text or "")):
        out.add(chunk)
        for i in range(len(chunk) - 1):
            out.add(chunk[i : i + 2])
    return out


def score_text(query: str, row: Dict[str, Any]) -> float:
    q_compact = compact(query)
    surface = str(row.get("surface", ""))
    aliases = [str(x) for x in row.get("aliases", [])]
    hay = " ".join([surface] + aliases + row.get("parents", []) + [row.get("notes", "")])
    score = 0.0
    if q_compact and q_compact == compact(surface):
        score += 3.0
    elif q_compact and q_compact in compact(hay):
        score += 1.2
    q_tokens = tokens(query)
    h_tokens = tokens(hay)
    if q_tokens and h_tokens:
        score += len(q_tokens & h_tokens) / max(1, len(q_tokens | h_tokens))
    return score


def load_local_index(path: Path) -> List[Dict[str, Any]]:
    return list(iter_jsonl(path))


def local_lookup(term: str, rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    scored = []
    for row in rows:
        score = score_text(term, row)
        if score > 0:
            scored.append((score, row))
    out = []
    for score, row in sorted(scored, key=lambda x: -x[0])[:limit]:
        out.append({"source": "local_domain_vocab", "score": round(score, 4), **row})
    return out


class HttpClient:
    def __init__(self, timeout: int, retries: int, ignore_proxy: bool):
        self.timeout = timeout
        self.retries = retries
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if ignore_proxy else urllib.request.build_opener()

    def get_json(self, url: str, accept: str = "application/json") -> Dict[str, Any]:
        last: Optional[Exception] = None
        for i in range(self.retries + 1):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                last = exc
                if i < self.retries:
                    time.sleep(min(8, 2 * (i + 1)))
        raise RuntimeError(str(last))


def dbpedia_lookup(client: HttpClient, term: str, limit: int) -> Dict[str, Any]:
    url = "https://lookup.dbpedia.org/api/search?" + urllib.parse.urlencode({"query": term, "maxResults": str(limit)})
    try:
        data = client.get_json(url)
        docs = data.get("docs", []) if isinstance(data, dict) else []
        rows = []
        for doc in docs[:limit]:
            rows.append(
                {
                    "source": "dbpedia_lookup",
                    "id": (doc.get("resource") or [""])[0] if isinstance(doc.get("resource"), list) else doc.get("resource", ""),
                    "label": (doc.get("label") or [""])[0] if isinstance(doc.get("label"), list) else doc.get("label", ""),
                    "description": (doc.get("comment") or [""])[0] if isinstance(doc.get("comment"), list) else doc.get("comment", ""),
                    "types": doc.get("typeName", []),
                }
            )
        return {"source": "dbpedia_lookup", "success": True, "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"source": "dbpedia_lookup", "success": False, "error": str(exc), "rows": []}


def qlever_lookup(client: HttpClient, term: str, backend: str, limit: int) -> Dict[str, Any]:
    endpoint = f"https://qlever.dev/api/{backend}"
    # Full-text predicate names differ across backends; this generic label query is
    # intentionally simple and works when backend exposes rdfs:label.
    escaped = term.replace('"', '\\"')
    query = f"""
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?item ?label WHERE {{
  ?item rdfs:label ?label .
  FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{escaped}")))
}}
LIMIT {limit}
"""
    url = endpoint + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    try:
        data = client.get_json(url, accept="application/sparql-results+json")
        rows = []
        for item in data.get("results", {}).get("bindings", [])[:limit]:
            rows.append(
                {
                    "source": f"qlever_{backend}",
                    "id": item.get("item", {}).get("value", ""),
                    "label": item.get("label", {}).get("value", ""),
                    "description": "",
                }
            )
        return {"source": f"qlever_{backend}", "success": True, "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"source": f"qlever_{backend}", "success": False, "error": str(exc), "rows": []}


def osm_taginfo_lookup(client: HttpClient, term: str, limit: int) -> Dict[str, Any]:
    url = "https://taginfo.openstreetmap.org/api/4/search/by_keyword?" + urllib.parse.urlencode({"query": term, "page": "1", "rp": str(limit)})
    try:
        data = client.get_json(url)
        rows = []
        for item in data.get("data", [])[:limit]:
            rows.append(
                {
                    "source": "osm_taginfo",
                    "id": "=".join([item.get("key", ""), item.get("value", "")]).strip("="),
                    "label": item.get("description") or item.get("key") or item.get("value", ""),
                    "key": item.get("key", ""),
                    "value": item.get("value", ""),
                    "count_all": item.get("count_all", 0),
                }
            )
        return {"source": "osm_taginfo", "success": True, "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"source": "osm_taginfo", "success": False, "error": str(exc), "rows": []}


def openalex_lookup(client: HttpClient, term: str, limit: int) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    errors = []
    for kind in ["institutions", "topics"]:
        url = f"https://api.openalex.org/autocomplete/{kind}?" + urllib.parse.urlencode({"q": term})
        try:
            data = client.get_json(url)
            for item in data.get("results", [])[:limit]:
                rows.append({"source": f"openalex_{kind}", "id": item.get("id", ""), "label": item.get("display_name", ""), "description": item.get("hint", "")})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{kind}: {exc}")
    return {"source": "openalex", "success": bool(rows), "error": " | ".join(errors), "rows": rows[:limit]}


def ror_lookup(client: HttpClient, term: str, limit: int) -> Dict[str, Any]:
    url = "https://api.ror.org/organizations?" + urllib.parse.urlencode({"query": term})
    try:
        data = client.get_json(url)
        rows = []
        for item in data.get("items", [])[:limit]:
            rows.append({"source": "ror", "id": item.get("id", ""), "label": item.get("name", ""), "description": ", ".join(item.get("types", []))})
        return {"source": "ror", "success": True, "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"source": "ror", "success": False, "error": str(exc), "rows": []}


def ols4_lookup(client: HttpClient, term: str, limit: int) -> Dict[str, Any]:
    url = "https://www.ebi.ac.uk/ols4/api/search?" + urllib.parse.urlencode({"q": term, "rows": str(limit)})
    try:
        data = client.get_json(url)
        rows = []
        for item in data.get("response", {}).get("docs", [])[:limit]:
            rows.append({"source": "ols4", "id": item.get("iri", ""), "label": item.get("label", ""), "description": " ".join(item.get("description", [])[:1]) if isinstance(item.get("description"), list) else item.get("description", "")})
        return {"source": "ols4", "success": True, "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"source": "ols4", "success": False, "error": str(exc), "rows": []}


def classify_domain(term: str, local_hits: List[Dict[str, Any]]) -> str:
    if local_hits:
        return local_hits[0].get("domain", "")
    c = compact(term)
    if any(x in c for x in ["贷款", "额度", "授信", "银行卡", "账户"]):
        return "finance"
    if any(x in c for x in ["仓", "发货", "履约", "配送", "物流"]):
        return "ecommerce_logistics"
    if any(x in c for x in ["学校", "教育", "机构", "大学"]):
        return "education_scholarly"
    return "generic"


def ground_term(term: str, local_rows: List[Dict[str, Any]], client: HttpClient, limit: int, online: bool) -> Dict[str, Any]:
    local_hits = local_lookup(term, local_rows, limit)
    domain = classify_domain(term, local_hits)
    online_results = []
    if online:
        online_results.append(dbpedia_lookup(client, term, limit))
        if domain in {"ecommerce_logistics", "place_facility"}:
            online_results.append(osm_taginfo_lookup(client, term, limit))
            online_results.append(qlever_lookup(client, term, "osm-planet", limit))
        if domain in {"education_scholarly", "generic"}:
            online_results.append(openalex_lookup(client, term, limit))
            online_results.append(ror_lookup(client, term, limit))
        if domain in {"finance", "generic"}:
            online_results.append(qlever_lookup(client, term, "dbpedia", limit))
            online_results.append(qlever_lookup(client, term, "yago-4", limit))
        online_results.append(ols4_lookup(client, term, limit))
    return {"term": term, "domain": domain, "local_hits": local_hits, "online_results": online_results}


def write_markdown(results: List[Dict[str, Any]], path: Path) -> None:
    lines = ["# Domain Grounding Router 测试结果", ""]
    for result in results:
        lines.append(f"## {result['term']}")
        lines.append(f"- domain: `{result.get('domain','')}`")
        lines.append("")
        lines.append("### Local Hits")
        if not result.get("local_hits"):
            lines.append("- 无")
        for hit in result.get("local_hits", []):
            props = ", ".join(f"{p.get('property_id')}={p.get('value_id')}" for p in hit.get("properties", []))
            lines.append(f"- score={hit.get('score')} `{hit.get('surface')}` source={hit.get('source_id')} status={hit.get('source_status')} props=[{props}]")
        lines.append("")
        lines.append("### Online Results")
        if not result.get("online_results"):
            lines.append("- 未启用在线查询")
        for block in result.get("online_results", []):
            if not block.get("success"):
                lines.append(f"- `{block.get('source')}` failed: {block.get('error')}")
                continue
            lines.append(f"- `{block.get('source')}` success rows={len(block.get('rows', []))}")
            for row in block.get("rows", [])[:5]:
                lines.append(f"  - {row.get('label') or row.get('id')} ({row.get('id','')})")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--index", type=Path, default=root / "open_sources/domain_vocab/index/domain_vocab_terms.jsonl")
    parser.add_argument("--term", action="append", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--ignore-proxy", action="store_true")
    parser.add_argument("--disable-online", action="store_true")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    local_rows = load_local_index(args.index)
    client = HttpClient(timeout=args.timeout, retries=args.retries, ignore_proxy=args.ignore_proxy)
    results = [ground_term(term, local_rows, client, args.limit, online=not args.disable_online) for term in args.term]
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(results, args.output_md)
    print(json.dumps({"output_json": str(args.output_json), "output_md": str(args.output_md), "terms": args.term}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
