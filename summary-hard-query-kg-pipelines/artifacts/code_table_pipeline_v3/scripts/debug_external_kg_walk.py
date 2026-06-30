#!/usr/bin/env python3
"""Debug external-KG walks from a free-text term.

This script does not use the local code table or domain overlay. It uses
Wikidata online APIs only:
  1. wbsearchentities: term -> candidate Wikidata entities.
  2. Special:EntityData: candidate -> direct KG anchors.
  3. SPARQL: anchor -> sibling / adjacent entities.

Walk types:
  - shared_anchor: source --P31/P279/P361/P1269/P452--> anchor
                   sibling --same property--> anchor
  - domain_business: source -> education/domain anchor
                     sibling has P452=that anchor and is a business/company

The second walk is meant to capture cases like:
  school -> education domain -> education-industry companies

Inputs:
  positional terms.

Outputs:
  JSON summary to stdout, optional JSON/Markdown files.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


USER_AGENT = "kg-build-data-external-kg-walk-debug/0.1"
SEARCH_URL = "https://www.wikidata.org/w/api.php"
ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
SPARQL_URL = "https://query.wikidata.org/sparql"

DIRECT_ANCHOR_PROPS = {
    "P31": "instance_of",
    "P279": "subclass_of",
    "P452": "industry",
    "P361": "part_of",
    "P1269": "facet_of",
    "P749": "parent_organization",
}

DOMAIN_HINT_PROPS = {
    "P1269": "facet_of",
    "P2579": "studied_by",
    "P366": "use",
    "P921": "main_subject",
    "P452": "industry",
}

BUSINESS_QIDS = [
    "Q4830453",  # business
    "Q6881511",  # enterprise
    "Q783794",  # company
    "Q43229",  # organization
]


def cache_name(url: str) -> str:
    import hashlib

    return hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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


def claim_entity_ids(entity: Dict[str, Any], prop_id: str, limit: int = 20) -> List[str]:
    out: List[str] = []
    for claim in entity.get("claims", {}).get(prop_id, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and value.get("id"):
            out.append(value["id"])
    return out[:limit]


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def domain_terms_from_text(text: str) -> List[str]:
    """Extract broad domain terms from KG labels/descriptions.

    This is intentionally generic: remove common organization/object suffixes
    from labels such as "教育机构" -> "教育", "financial service" -> "finance".
    The extracted term is then grounded back to Wikidata search.
    """

    text = compact_text(text)
    out: List[str] = []
    zh_suffixes = [
        "机构",
        "组织",
        "公司",
        "企业",
        "行业",
        "产业",
        "服务",
        "业务",
        "产品",
        "系统",
        "设施",
        "地点",
        "场所",
        "建筑物",
    ]
    for suffix in zh_suffixes:
        if text.endswith(suffix) and len(text) > len(suffix):
            stem = text[: -len(suffix)]
            if 1 < len(stem) <= 8:
                out.append(stem)
    en = re.sub(r"[^A-Za-z ]+", " ", text).lower()
    replacements = {
        "educational": "education",
        "financial": "finance",
        "banking": "banking",
        "logistical": "logistics",
        "shipping": "shipping",
        "branding": "brand",
        "licensing": "licensing",
    }
    for src, dst in replacements.items():
        if src in en:
            out.append(dst)
    for pattern in [
        r"([a-z]+)\s+institution",
        r"([a-z]+)\s+organization",
        r"([a-z]+)\s+service",
        r"([a-z]+)\s+industry",
        r"([a-z]+)\s+company",
    ]:
        for m in re.finditer(pattern, en):
            out.append(m.group(1))
    deduped = []
    seen = set()
    for item in out:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped[:5]


class Wikidata:
    def __init__(self, cache_dir: Path, timeout: int, retries: int, ignore_proxy: bool):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.retries = retries
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if ignore_proxy else urllib.request.build_opener()
        ensure_dir(cache_dir)

    def fetch_json(self, url: str, accept: str = "application/json") -> Dict[str, Any]:
        cache_path = self.cache_dir / cache_name(url)
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        last: Optional[Exception] = None
        for i in range(self.retries + 1):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                time.sleep(0.1)
                return data
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 429 and i < self.retries:
                    retry_after = exc.headers.get("Retry-After")
                    sleep_s = int(retry_after) if retry_after and retry_after.isdigit() else 65
                    time.sleep(sleep_s)
                    continue
                raise
            except Exception as exc:
                last = exc
                if i >= self.retries:
                    raise
                time.sleep(min(20, 2 ** i * 3))
        raise RuntimeError(f"request failed: {last}")

    def search(self, term: str, limit: int) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        seen = set()
        for lang in ["zh", "en"]:
            query = urllib.parse.urlencode(
                {
                    "action": "wbsearchentities",
                    "search": term,
                    "language": lang,
                    "uselang": "zh",
                    "format": "json",
                    "limit": str(limit),
                }
            )
            data = self.fetch_json(f"{SEARCH_URL}?{query}")
            for hit in data.get("search", []):
                qid = hit.get("id")
                if qid and qid not in seen:
                    hit["search_language"] = lang
                    hits.append(hit)
                    seen.add(qid)
            if hits:
                break
        return hits[:limit]

    def entity(self, qid: str) -> Dict[str, Any]:
        data = self.fetch_json(ENTITY_URL.format(qid=urllib.parse.quote(qid)))
        return data.get("entities", {}).get(qid, {})

    def entity_brief(self, qid: str) -> Dict[str, str]:
        entity = self.entity(qid)
        return {
            "id": qid,
            "label": entity_label(entity),
            "description": entity_desc(entity),
            "url": f"https://www.wikidata.org/wiki/{qid}",
        }

    def sparql(self, query: str) -> Dict[str, Any]:
        params = urllib.parse.urlencode({"query": query, "format": "json"})
        return self.fetch_json(f"{SPARQL_URL}?{params}", accept="application/sparql-results+json")


def sparql_rows(data: Dict[str, Any]) -> List[Dict[str, str]]:
    rows = []
    for row in data.get("results", {}).get("bindings", []):
        item_uri = row.get("item", {}).get("value", "")
        qid = item_uri.rsplit("/", 1)[-1] if item_uri else ""
        if not qid:
            continue
        rows.append(
            {
                "id": qid,
                "label": row.get("itemLabel", {}).get("value", ""),
                "description": row.get("itemDescription", {}).get("value", ""),
                "url": f"https://www.wikidata.org/wiki/{qid}",
            }
        )
    return rows


def shared_anchor_siblings(wd: Wikidata, source_qid: str, prop_id: str, anchor_qid: str, limit: int) -> List[Dict[str, str]]:
    query = f"""
SELECT ?item ?itemLabel ?itemDescription WHERE {{
  ?item wdt:{prop_id} wd:{anchor_qid}.
  FILTER(?item != wd:{source_qid})
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,en". }}
}}
LIMIT {limit}
"""
    return sparql_rows(wd.sparql(query))


def domain_business_siblings(wd: Wikidata, source_qid: str, domain_qid: str, limit: int) -> List[Dict[str, str]]:
    business_values = " ".join(f"wd:{qid}" for qid in BUSINESS_QIDS)
    query = f"""
SELECT ?item ?itemLabel ?itemDescription WHERE {{
  VALUES ?businessClass {{ {business_values} }}
  ?item wdt:P452 wd:{domain_qid}.
  ?item (wdt:P31/wdt:P279*) ?businessClass.
  FILTER(?item != wd:{source_qid})
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,en". }}
}}
LIMIT {limit}
"""
    return sparql_rows(wd.sparql(query))


def domain_anchor_candidates(wd: Wikidata, entity: Dict[str, Any], direct_anchors: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for prop_id, relation in DOMAIN_HINT_PROPS.items():
        for qid in claim_entity_ids(entity, prop_id, limit=limit):
            if qid in seen:
                continue
            seen.add(qid)
            brief = wd.entity_brief(qid)
            candidates.append({"property_id": prop_id, "property_label": relation, **brief})
    for anchor in direct_anchors:
        text = f"{anchor.get('label', '')} {anchor.get('description', '')}".lower()
        if any(tok in text for tok in ["education", "finance", "bank", "payment", "logistics", "shipping", "brand", "license", "licensing", "commerce"]):
            qid = anchor["id"]
            if qid not in seen:
                seen.add(qid)
                candidates.append({"property_id": anchor["property_id"], "property_label": anchor["property_label"], **{k: anchor[k] for k in ["id", "label", "description", "url"]}})
        for term in domain_terms_from_text(f"{anchor.get('label', '')} {anchor.get('description', '')}"):
            try:
                hits = wd.search(term, 1)
            except Exception:
                hits = []
            for hit in hits:
                qid = hit.get("id")
                if not qid or qid in seen:
                    continue
                seen.add(qid)
                brief = wd.entity_brief(qid)
                candidates.append(
                    {
                        "property_id": "derived_domain_search",
                        "property_label": f"domain_term:{term}",
                        **brief,
                    }
                )
    return candidates[:limit]


def walk_term(wd: Wikidata, term: str, search_limit: int, anchor_limit: int, sibling_limit: int) -> Dict[str, Any]:
    out = {"term": term, "source": "Wikidata online API + Wikidata SPARQL", "entities": []}
    for hit in wd.search(term, search_limit):
        qid = hit["id"]
        entity = wd.entity(qid)
        entity_item: Dict[str, Any] = {
            "id": qid,
            "label": entity_label(entity) or hit.get("label", ""),
            "description": entity_desc(entity) or hit.get("description", ""),
            "url": f"https://www.wikidata.org/wiki/{qid}",
            "search_language": hit.get("search_language", ""),
            "direct_anchors": [],
            "walks": [],
        }
        direct_anchors: List[Dict[str, Any]] = []
        for prop_id, prop_label in DIRECT_ANCHOR_PROPS.items():
            for anchor_qid in claim_entity_ids(entity, prop_id, limit=anchor_limit):
                anchor = {"property_id": prop_id, "property_label": prop_label, **wd.entity_brief(anchor_qid)}
                direct_anchors.append(anchor)
                entity_item["direct_anchors"].append(anchor)
                try:
                    siblings = shared_anchor_siblings(wd, qid, prop_id, anchor_qid, sibling_limit)
                except Exception as exc:
                    siblings = [{"id": "", "label": "", "description": f"sparql_failed: {exc}", "url": ""}]
                entity_item["walks"].append(
                    {
                        "walk_type": "shared_anchor",
                        "source": {"id": qid, "label": entity_item["label"]},
                        "property_id": prop_id,
                        "property_label": prop_label,
                        "anchor": anchor,
                        "siblings": siblings,
                        "path": [
                            f"{qid} --{prop_id}/{prop_label}--> {anchor_qid}",
                            f"{anchor_qid} <--{prop_id}/{prop_label}-- sibling_entities",
                        ],
                    }
                )
        for domain_anchor in domain_anchor_candidates(wd, entity, direct_anchors, anchor_limit):
            try:
                siblings = domain_business_siblings(wd, qid, domain_anchor["id"], sibling_limit)
            except Exception as exc:
                siblings = [{"id": "", "label": "", "description": f"sparql_failed: {exc}", "url": ""}]
            entity_item["walks"].append(
                {
                    "walk_type": "domain_business",
                    "source": {"id": qid, "label": entity_item["label"]},
                    "domain_anchor": domain_anchor,
                    "target_constraint": "P452=domain_anchor and P31/P279*=business|enterprise|company|organization",
                    "siblings": siblings,
                    "path": [
                        f"{qid} --domain_hint--> {domain_anchor['id']} ({domain_anchor['label']})",
                        f"sibling --P452/industry--> {domain_anchor['id']}",
                        "sibling --P31/P279*--> business/company/enterprise/organization",
                    ],
                }
            )
        out["entities"].append(entity_item)
    return out


def write_markdown(rows: List[Dict[str, Any]], path: Path) -> None:
    lines = ["# 外部 KG 游走调试结果", ""]
    for row in rows:
        lines += [f"## {row['term']}", ""]
        for ent in row.get("entities", []):
            lines += [f"### {ent.get('label')} ({ent.get('id')})", "", ent.get("description", ""), ""]
            lines += ["**直接 anchor**", ""]
            for a in ent.get("direct_anchors", [])[:12]:
                lines.append(f"- `{a['property_id']}/{a['property_label']}` -> {a.get('label')} ({a.get('id')})：{a.get('description', '')}")
            lines += ["", "**游走候选**", ""]
            for w in ent.get("walks", [])[:20]:
                if not w.get("siblings"):
                    continue
                if all(not s.get("id") for s in w.get("siblings", [])):
                    continue
                anchor = w.get("anchor") or w.get("domain_anchor") or {}
                lines.append(f"- `{w['walk_type']}` via {anchor.get('label')} ({anchor.get('id')})")
                for s in w.get("siblings", [])[:8]:
                    if s.get("id"):
                        lines.append(f"  - {s.get('label')} ({s.get('id')})：{s.get('description', '')}")
                lines.append("")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("terms", nargs="+")
    parser.add_argument("--cache-dir", type=Path, default=Path("artifacts/code_table_pipeline_v3/outputs/external_kg_walk_debug/cache"))
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--search-limit", type=int, default=1)
    parser.add_argument("--anchor-limit", type=int, default=4)
    parser.add_argument("--sibling-limit", type=int, default=8)
    parser.add_argument("--ignore-proxy", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    wd = Wikidata(args.cache_dir, args.timeout, args.retries, args.ignore_proxy)
    rows = [walk_term(wd, term, args.search_limit, args.anchor_limit, args.sibling_limit) for term in args.terms]
    if args.output_json:
        ensure_dir(args.output_json.parent)
        args.output_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output_md:
        ensure_dir(args.output_md.parent)
        write_markdown(rows, args.output_md)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
