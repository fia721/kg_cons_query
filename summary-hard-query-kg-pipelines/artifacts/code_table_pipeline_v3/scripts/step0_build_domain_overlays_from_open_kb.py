#!/usr/bin/env python3
"""Build domain overlay KG from open knowledge-base grounding.

This step does not add training-material entities to the permanent KG.
It enriches domain concepts with open KB references from:
  - Wikidata EntityData / Action API search
  - Schema.org term URLs
  - DBpedia ontology term URLs

Inputs:
  --seed-jsonl: domain seeds with concept search terms.

Outputs:
  --output-jsonl: open-KB-backed domain overlays consumed by step3.
  --cache-dir: raw Wikidata search/entity JSON cache.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


USER_AGENT = "kg-build-data-domain-overlay/1.0 (open-kb grounding)"

WIKIDATA_WALK_PROPS = {
    "P31": "instance_of",
    "P279": "subclass_of",
    "P452": "industry",
    "P1269": "facet_of",
    "P361": "part_of",
    "P749": "parent_organization",
}

OPEN_SOURCE_SPECS = [
    {
        "source_id": "schema_org",
        "url": "https://schema.org/version/latest/schemaorg-current-https.jsonld",
        "filename": "schemaorg-current-https.jsonld",
        "format": "jsonld",
    },
    {
        "source_id": "dbpedia_ontology",
        "url": "https://downloads.dbpedia.org/ontology/dbpedia_2016-10.owl",
        "filename": "dbpedia_2016-10.owl",
        "format": "rdfxml",
    },
    {
        "source_id": "naics_2022",
        "url": "https://www.census.gov/naics/2022NAICS/2022_NAICS_Structure.xlsx",
        "filename": "2022_NAICS_Structure.xlsx",
        "format": "binary_reference",
    },
    {
        "source_id": "nist_rbac",
        "url": "https://csrc.nist.gov/projects/role-based-access-control",
        "filename": "nist_rbac.html",
        "format": "html_reference",
    },
]


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def fetch_json(url: str, cache_path: Path, timeout: int = 60, ignore_proxy: bool = False) -> Dict[str, Any]:
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if ignore_proxy else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    time.sleep(0.2)
    return data


def fetch_bytes(url: str, cache_path: Path, timeout: int = 120) -> Tuple[bool, str]:
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return True, "cached"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            cache_path.write_bytes(resp.read())
        time.sleep(0.2)
        return True, "downloaded"
    except Exception as exc:
        return False, str(exc)


def load_source_terms(source_cache_dir: Path, download: bool) -> Dict[str, List[Dict[str, str]]]:
    source_terms: Dict[str, List[Dict[str, str]]] = {}
    for spec in OPEN_SOURCE_SPECS:
        path = source_cache_dir / spec["filename"]
        if download:
            ok, status = fetch_bytes(spec["url"], path)
        else:
            ok = path.exists() and path.stat().st_size > 0
            status = "cached" if ok else "not_downloaded"
        terms: List[Dict[str, str]] = []
        if ok and spec["format"] == "jsonld":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in data.get("@graph", []):
                    item_id = str(item.get("@id", ""))
                    label = item.get("rdfs:label") or item.get("schema:name") or ""
                    comment = item.get("rdfs:comment") or ""
                    if isinstance(label, dict):
                        label = label.get("@value", "")
                    if isinstance(comment, dict):
                        comment = comment.get("@value", "")
                    if item_id or label:
                        terms.append({"id": item_id, "label": str(label), "comment": str(comment), "url": f"https://schema.org/{item_id.split(':')[-1]}"})
            except Exception as exc:
                status = f"parse_error: {exc}"
        elif ok and spec["format"] == "rdfxml":
            try:
                root = ET.parse(path).getroot()
                for elem in root.iter():
                    about = elem.attrib.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
                    label = ""
                    comment = ""
                    for child in elem:
                        if child.tag.endswith("label") and not label:
                            label = child.text or ""
                        elif child.tag.endswith("comment") and not comment:
                            comment = child.text or ""
                    if about or label:
                        terms.append({"id": about.rsplit("/", 1)[-1], "label": label, "comment": comment, "url": about})
            except Exception as exc:
                status = f"parse_error: {exc}"
        source_terms[spec["source_id"]] = terms
        source_terms[f"{spec['source_id']}__meta"] = [{"url": spec["url"], "local_path": str(path), "status": status, "format": spec["format"]}]
    return source_terms


def token_set(text: str) -> set[str]:
    return {t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if len(t) >= 3}


def match_open_source_terms(concept: Dict[str, Any], source_terms: Dict[str, List[Dict[str, str]]]) -> List[Dict[str, str]]:
    queries = []
    queries.extend(concept.get("wikidata_search_terms", []))
    queries.extend(concept.get("schema_terms", []))
    queries.extend(concept.get("dbpedia_terms", []))
    query_tokens = token_set(" ".join(queries + [concept.get("label", ""), concept.get("description", "")]))
    matches: List[Dict[str, str]] = []
    for source_id, terms in source_terms.items():
        if source_id.endswith("__meta"):
            continue
        scored = []
        for term in terms:
            hay = " ".join([term.get("id", ""), term.get("label", ""), term.get("comment", "")])
            overlap = len(query_tokens & token_set(hay))
            if overlap:
                scored.append((overlap, term))
        for _, term in sorted(scored, key=lambda x: x[0], reverse=True)[:5]:
            matches.append(
                {
                    "source_id": source_id,
                    "id": term.get("id", ""),
                    "label": term.get("label", ""),
                    "url": term.get("url", ""),
                    "match_method": "token_overlap_from_downloaded_open_source",
                }
            )
    return matches


def wikidata_search(term: str, cache_dir: Path, limit: int, timeout: int, online: bool, ignore_proxy: bool) -> List[Dict[str, Any]]:
    q = urllib.parse.urlencode(
        {
            "action": "wbsearchentities",
            "search": term,
            "language": "en",
            "uselang": "en",
            "format": "json",
            "limit": str(limit),
        }
    )
    url = f"https://www.wikidata.org/w/api.php?{q}"
    safe = urllib.parse.quote(term, safe="")
    cache_path = cache_dir / "wikidata_search" / f"{safe}.json"
    if not online and not cache_path.exists():
        return []
    try:
        data = fetch_json(url, cache_path, timeout=timeout, ignore_proxy=ignore_proxy)
    except Exception as exc:
        return [{"id": "", "label": "", "description": f"wikidata_search_failed: {exc}"}]
    return data.get("search", [])


def wikidata_entity(entity_id: str, cache_dir: Path, timeout: int, online: bool, ignore_proxy: bool) -> Dict[str, Any]:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
    cache_path = cache_dir / "wikidata_entity" / f"{entity_id}.json"
    if not online and not cache_path.exists():
        return {}
    data = fetch_json(url, cache_path, timeout=timeout, ignore_proxy=ignore_proxy)
    return data.get("entities", {}).get(entity_id, {})


def label(entity: Dict[str, Any], lang: str = "en") -> str:
    return entity.get("labels", {}).get(lang, {}).get("value", "")


def desc(entity: Dict[str, Any], lang: str = "en") -> str:
    return entity.get("descriptions", {}).get(lang, {}).get("value", "")


def best_label(entity: Dict[str, Any]) -> str:
    return label(entity, "zh") or label(entity, "zh-hans") or label(entity, "en")


def best_desc(entity: Dict[str, Any]) -> str:
    return desc(entity, "zh") or desc(entity, "zh-hans") or desc(entity, "en")


def claim_ids(entity: Dict[str, Any], prop_id: str) -> List[str]:
    out = []
    for claim in entity.get("claims", {}).get(prop_id, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and value.get("id"):
            out.append(value["id"])
    return out


def wikidata_one_hop_walk(
    source_entity_id: str,
    source_entity: Dict[str, Any],
    cache_dir: Path,
    timeout: int,
    online: bool,
    ignore_proxy: bool,
    max_neighbors_per_prop: int,
    fetch_neighbor_labels: bool,
) -> List[Dict[str, Any]]:
    walks: List[Dict[str, Any]] = []
    for prop_id, relation in WIKIDATA_WALK_PROPS.items():
        for neighbor_id in claim_ids(source_entity, prop_id)[:max_neighbors_per_prop]:
            cache_path = cache_dir / "wikidata_entity" / f"{neighbor_id}.json"
            if not fetch_neighbor_labels and not cache_path.exists():
                walks.append(
                    {
                        "source_id": source_entity_id,
                        "property_id": prop_id,
                        "relation": relation,
                        "target_id": neighbor_id,
                        "target_label": "",
                        "target_description": "",
                        "target_url": f"https://www.wikidata.org/wiki/{neighbor_id}",
                        "label_status": "not_fetched",
                    }
                )
                continue
            try:
                neighbor = wikidata_entity(neighbor_id, cache_dir, timeout, online, ignore_proxy)
            except Exception as exc:
                walks.append(
                    {
                        "source_id": source_entity_id,
                        "property_id": prop_id,
                        "relation": relation,
                        "target_id": neighbor_id,
                        "target_label": "",
                        "target_description": f"neighbor_fetch_failed: {exc}",
                        "target_url": f"https://www.wikidata.org/wiki/{neighbor_id}",
                        "label_status": "fetch_failed",
                    }
                )
                continue
            walks.append(
                {
                    "source_id": source_entity_id,
                    "property_id": prop_id,
                    "relation": relation,
                    "target_id": neighbor_id,
                    "target_label": best_label(neighbor),
                    "target_description": best_desc(neighbor),
                    "target_url": f"https://www.wikidata.org/wiki/{neighbor_id}",
                    "label_status": "fetched" if fetch_neighbor_labels else "cached",
                }
            )
    return walks


def ground_concept(
    concept: Dict[str, Any],
    cache_dir: Path,
    search_limit: int,
    source_terms: Dict[str, List[Dict[str, str]]],
    wikidata_timeout: int,
    wikidata_online: bool,
    wikidata_ignore_proxy: bool,
    wikidata_max_neighbors_per_prop: int,
    wikidata_fetch_neighbor_labels: bool,
) -> Dict[str, Any]:
    wikidata_refs = []
    seen = set()
    for term in concept.get("wikidata_search_terms", []):
        for hit in wikidata_search(term, cache_dir, search_limit, wikidata_timeout, wikidata_online, wikidata_ignore_proxy):
            entity_id = hit.get("id")
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            try:
                entity = wikidata_entity(entity_id, cache_dir, wikidata_timeout, wikidata_online, wikidata_ignore_proxy)
            except Exception as exc:
                wikidata_refs.append(
                    {
                        "source_id": "wikidata",
                        "entity_id": entity_id,
                        "label": hit.get("label"),
                        "description": f"wikidata_entity_failed: {exc}",
                        "url": f"https://www.wikidata.org/wiki/{entity_id}",
                        "instance_of": [],
                        "subclass_of": [],
                        "matched_search_term": term,
                    }
                )
                continue
            wikidata_refs.append(
                {
                    "source_id": "wikidata",
                    "entity_id": entity_id,
                    "label": best_label(entity) or hit.get("label"),
                    "description": best_desc(entity) or hit.get("description"),
                    "url": f"https://www.wikidata.org/wiki/{entity_id}",
                    "instance_of": claim_ids(entity, "P31"),
                    "subclass_of": claim_ids(entity, "P279"),
                    "online_kg_walks": wikidata_one_hop_walk(
                        entity_id,
                        entity,
                        cache_dir,
                        wikidata_timeout,
                        wikidata_online,
                        wikidata_ignore_proxy,
                        wikidata_max_neighbors_per_prop,
                        wikidata_fetch_neighbor_labels,
                    ),
                    "matched_search_term": term,
                }
            )
    schema_refs = [
        {
            "source_id": "schema_org",
            "term": term,
            "url": f"https://schema.org/{term}",
        }
        for term in concept.get("schema_terms", [])
    ]
    dbpedia_refs = [
        {
            "source_id": "dbpedia_ontology",
            "term": term,
            "url": f"https://dbpedia.org/ontology/{term}",
        }
        for term in concept.get("dbpedia_terms", [])
    ]
    out = dict(concept)
    out.pop("wikidata_search_terms", None)
    out.pop("schema_terms", None)
    out.pop("dbpedia_terms", None)
    downloaded_source_matches = match_open_source_terms(concept, source_terms)
    out["open_kb_refs"] = wikidata_refs + schema_refs + dbpedia_refs + downloaded_source_matches
    out["open_kb_grounding_status"] = "grounded" if wikidata_refs or schema_refs or dbpedia_refs else "ungrounded"
    return out


def property_values(concept: Dict[str, Any]) -> List[Dict[str, str]]:
    values = []
    for item in concept.get("maps_to", []):
        property_id = str(item.get("property_id") or "").strip()
        value_id = str(item.get("value_id") or "").strip()
        if property_id and value_id:
            values.append(
                {
                    "property_id": property_id,
                    "value_id": value_id,
                    "label": str(item.get("label") or value_id),
                }
            )
    return values


def derive_sibling_value_walks(concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derive generic walks: concept -> shared property axis -> sibling value -> concept.

    The relation is intentionally schema-level and domain-agnostic. It avoids
    encoding case-specific pairs, while still exposing that two concepts share
    an anchor and differ along one reusable property axis.
    """

    walks: List[Dict[str, Any]] = []
    seen = set()
    for source in concepts:
        source_id = str(source.get("concept_id") or "")
        source_props = property_values(source)
        for target in concepts:
            target_id = str(target.get("concept_id") or "")
            if not source_id or not target_id or source_id == target_id:
                continue
            target_props = property_values(target)
            shared = [
                sp
                for sp in source_props
                for tp in target_props
                if sp["property_id"] == tp["property_id"] and sp["value_id"] == tp["value_id"]
            ]
            differing = [
                {"source": sp, "target": tp}
                for sp in source_props
                for tp in target_props
                if sp["property_id"] == tp["property_id"] and sp["value_id"] != tp["value_id"]
            ]
            if not shared or not differing:
                continue
            for diff in differing:
                axis = diff["source"]["property_id"]
                key = (source_id, target_id, axis, diff["source"]["value_id"], diff["target"]["value_id"])
                if key in seen:
                    continue
                seen.add(key)
                walks.append(
                    {
                        "walk_type": "shared_anchor_sibling_value",
                        "source_concept_id": source_id,
                        "source_label": source.get("label", ""),
                        "target_concept_id": target_id,
                        "target_label": target.get("label", ""),
                        "shared_anchor_properties": shared,
                        "axis_property_id": axis,
                        "source_value_id": diff["source"]["value_id"],
                        "source_value_label": diff["source"]["label"],
                        "target_value_id": diff["target"]["value_id"],
                        "target_value_label": diff["target"]["label"],
                        "walked_relations": [
                            f"{source_id} --has_value--> {axis}:{diff['source']['value_id']}",
                            f"{axis}:{diff['source']['value_id']} --value_of--> property:{axis}",
                            f"property:{axis} --sibling_value--> {axis}:{diff['target']['value_id']}",
                            f"{axis}:{diff['target']['value_id']} --value_of_inverse--> {target_id}",
                        ],
                        "why_confusing": "两个概念共享上位锚点，但在同一属性轴上取不同值；适合构造边界判别。",
                    }
                )
    return walks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-jsonl", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--source-cache-dir", type=Path, required=True)
    parser.add_argument("--wikidata-search-limit", type=int, default=3)
    parser.add_argument("--wikidata-timeout", type=int, default=8)
    parser.add_argument("--wikidata-max-neighbors-per-prop", type=int, default=3)
    parser.add_argument(
        "--wikidata-fetch-neighbor-labels",
        action="store_true",
        help="为一跳邻居继续请求 EntityData 补 label；默认只保留邻居 QID，避免主流程过慢。",
    )
    parser.add_argument(
        "--disable-wikidata-online",
        action="store_true",
        help="只读取已有 Wikidata 缓存，不访问在线 API；用于网络不可用时避免 pipeline 阻塞。",
    )
    parser.add_argument("--ignore-proxy-for-wikidata", action="store_true", help="访问 Wikidata 时忽略 http_proxy/https_proxy。")
    parser.add_argument(
        "--download-source-terms",
        action="store_true",
        help="下载 Schema.org/DBpedia/NAICS/NIST 原始文件并做 term 匹配；默认只使用本地已有缓存和 Wikidata 在线缓存。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    source_terms = load_source_terms(args.source_cache_dir, args.download_source_terms)
    rows = []
    for domain in iter_jsonl(args.seed_jsonl):
        out = dict(domain)
        out["open_kb_sources"] = [
            {"source_id": "wikidata", "url": "https://www.wikidata.org/wiki/Wikidata:Data_access", "local_status": "api_cached_per_entity"},
        ]
        for spec in OPEN_SOURCE_SPECS:
            meta = source_terms.get(f"{spec['source_id']}__meta", [{}])[0]
            out["open_kb_sources"].append(
                {
                    "source_id": spec["source_id"],
                    "url": spec["url"],
                    "local_path": meta.get("local_path", ""),
                    "local_status": meta.get("status", ""),
                    "format": spec["format"],
                }
            )
        out["concepts"] = [
            ground_concept(
                concept,
                args.cache_dir,
                args.wikidata_search_limit,
                source_terms,
                args.wikidata_timeout,
                not args.disable_wikidata_online,
                args.ignore_proxy_for_wikidata,
                args.wikidata_max_neighbors_per_prop,
                args.wikidata_fetch_neighbor_labels,
            )
            for concept in domain.get("concept_seeds", [])
        ]
        out["derived_walks"] = derive_sibling_value_walks(out["concepts"])
        out.pop("concept_seeds", None)
        rows.append(out)
    with args.output_jsonl.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"domains": len(rows), "output": str(args.output_jsonl)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
