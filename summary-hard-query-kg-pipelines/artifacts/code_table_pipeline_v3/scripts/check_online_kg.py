#!/usr/bin/env python3
"""Check whether online KG endpoints are reachable.

当前主要验证 Wikidata：
  1. Action API: wbsearchentities
  2. EntityData: Special:EntityData/{QID}.json
  3. SPARQL Query Service
  4. curl 访问路径

输出严格 JSON，包含每一步耗时、HTTP 状态、命中的 QID、P31(instance of)、P279(subclass of)。
这个脚本只做连通性和最小字段验证，不参与 pipeline 产物构造。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


USER_AGENT = "kg-build-data-online-kg-check/1.0"


def fetch_json_urllib(url: str, timeout: int, accept: str = "application/json", ignore_proxy: bool = False) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
    )
    start = time.time()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if ignore_proxy else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return {
            "_meta": {
                "url": url,
                "http_status": resp.status,
                "content_type": resp.headers.get("content-type", ""),
                "elapsed_sec": round(time.time() - start, 3),
                "bytes": len(body.encode("utf-8")),
            },
            "json": json.loads(body),
        }


def fetch_json_requests(url: str, timeout: int, accept: str = "application/json", ignore_proxy: bool = False) -> Dict[str, Any]:
    start = time.time()
    try:
        import requests  # type: ignore
    except Exception as exc:
        return {
            "success": False,
            "error": f"requests_import_failed: {exc}",
            "elapsed_sec": round(time.time() - start, 3),
        }
    if ignore_proxy:
        session = requests.Session()
        session.trust_env = False
        resp = session.get(url, headers={"User-Agent": USER_AGENT, "Accept": accept}, timeout=timeout)
    else:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": accept}, timeout=timeout)
    body = resp.text
    return {
        "_meta": {
            "url": url,
            "http_status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "elapsed_sec": round(time.time() - start, 3),
            "bytes": len(body.encode("utf-8")),
        },
        "json": resp.json(),
    }


def fetch_json_curl(url: str, timeout: int, accept: str = "application/json", ignore_proxy: bool = False) -> Dict[str, Any]:
    start = time.time()
    cmd = [
        "curl",
        "-L",
        "--max-time",
        str(timeout),
        "-A",
        USER_AGENT,
        "-H",
        f"Accept: {accept}",
        "-w",
        "\n__CURL_HTTP_CODE__:%{http_code}\n__CURL_TIME_TOTAL__:%{time_total}\n",
        url,
    ]
    if ignore_proxy:
        cmd.insert(1, "--noproxy")
        cmd.insert(2, "*")
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 5)
    stdout = proc.stdout
    marker = "\n__CURL_HTTP_CODE__:"
    body = stdout
    http_code = ""
    time_total = ""
    if marker in stdout:
        body, tail = stdout.rsplit(marker, 1)
        lines = tail.strip().splitlines()
        if lines:
            http_code = lines[0].strip()
        for line in lines[1:]:
            if line.startswith("__CURL_TIME_TOTAL__:"):
                time_total = line.split(":", 1)[1].strip()
    if proc.returncode != 0:
        return {
            "success": False,
            "error": proc.stderr.strip() or f"curl_returncode={proc.returncode}",
            "elapsed_sec": round(time.time() - start, 3),
            "http_status": http_code,
            "curl_time_total": time_total,
        }
    return {
        "_meta": {
            "url": url,
            "http_status": int(http_code) if http_code.isdigit() else http_code,
            "content_type": "",
            "elapsed_sec": round(time.time() - start, 3),
            "curl_time_total": time_total,
            "bytes": len(body.encode("utf-8")),
        },
        "json": json.loads(body),
    }


def try_fetch(name: str, url: str, timeout: int, method: str, accept: str = "application/json", ignore_proxy: bool = False) -> Dict[str, Any]:
    start = time.time()
    try:
        if method == "urllib":
            resp = fetch_json_urllib(url, timeout, accept=accept, ignore_proxy=ignore_proxy)
        elif method == "requests":
            resp = fetch_json_requests(url, timeout, accept=accept, ignore_proxy=ignore_proxy)
        elif method == "curl":
            resp = fetch_json_curl(url, timeout, accept=accept, ignore_proxy=ignore_proxy)
        else:
            raise ValueError(f"unknown method={method}")
        return {"name": name, "method": method, "success": True, **resp}
    except Exception as exc:
        return {
            "name": name,
            "method": method,
            "success": False,
            "error": repr(exc),
            "elapsed_sec": round(time.time() - start, 3),
            "url": url,
        }


def claim_ids(entity: Dict[str, Any], prop_id: str) -> List[str]:
    ids: List[str] = []
    for claim in entity.get("claims", {}).get(prop_id, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and value.get("id"):
            ids.append(value["id"])
    return ids


def label(entity: Dict[str, Any], lang: str = "en") -> str:
    return entity.get("labels", {}).get(lang, {}).get("value", "")


def description(entity: Dict[str, Any], lang: str = "en") -> str:
    return entity.get("descriptions", {}).get(lang, {}).get("value", "")


def first_success(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for result in results:
        if result.get("success"):
            return result
    return None


def check_wikidata(term: str, limit: int, timeout: int, methods: List[str], ignore_proxy: bool) -> Dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "action": "wbsearchentities",
            "search": term,
            "language": "en",
            "uselang": "en",
            "format": "json",
            "limit": str(limit),
        }
    )
    search_url = f"https://www.wikidata.org/w/api.php?{query}"
    search_attempts = [try_fetch("wikidata_search", search_url, timeout, method, ignore_proxy=ignore_proxy) for method in methods]
    search_resp = first_success(search_attempts)
    hits = search_resp["json"].get("search", []) if search_resp else []
    out: Dict[str, Any] = {
        "endpoint": "wikidata",
        "term": term,
        "search_url": search_url,
        "search_attempts": search_attempts,
        "search_meta": search_resp.get("_meta") if search_resp else None,
        "hits": [],
    }
    for hit in hits:
        qid = hit.get("id")
        item = {
            "id": qid,
            "label": hit.get("label", ""),
            "description": hit.get("description", ""),
            "concepturi": hit.get("concepturi", ""),
        }
        if qid:
            entity_url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
            entity_attempts = [try_fetch(f"wikidata_entity_{qid}", entity_url, timeout, method, ignore_proxy=ignore_proxy) for method in methods]
            entity_resp = first_success(entity_attempts)
            entity = entity_resp["json"].get("entities", {}).get(qid, {}) if entity_resp else {}
            item.update(
                {
                    "entity_attempts": entity_attempts,
                    "entity_meta": entity_resp.get("_meta") if entity_resp else None,
                    "entity_label": label(entity) or hit.get("label", ""),
                    "entity_description": description(entity) or hit.get("description", ""),
                    "instance_of_P31": claim_ids(entity, "P31")[:20],
                    "subclass_of_P279": claim_ids(entity, "P279")[:20],
                }
            )
        out["hits"].append(item)
    sparql = urllib.parse.urlencode(
        {
            "query": f'SELECT ?item ?itemLabel WHERE {{ ?item rdfs:label "{term}"@en. SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }} LIMIT {limit}',
            "format": "json",
        }
    )
    sparql_url = f"https://query.wikidata.org/sparql?{sparql}"
    out["sparql_attempts"] = [
        try_fetch("wikidata_sparql", sparql_url, timeout, method, accept="application/sparql-results+json", ignore_proxy=ignore_proxy)
        for method in methods
    ]
    out["success"] = bool(out["hits"])
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--term", default="bank branch")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--methods", default="urllib,requests,curl", help="comma-separated: urllib,requests,curl")
    parser.add_argument("--fail-on-unavailable", action="store_true")
    parser.add_argument("--ignore-proxy", action="store_true", help="Ignore http_proxy/https_proxy for Wikidata requests.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = time.time()
    try:
        methods = [x.strip() for x in args.methods.split(",") if x.strip()]
        result = check_wikidata(args.term, args.limit, args.timeout, methods, args.ignore_proxy)
        result["total_elapsed_sec"] = round(time.time() - start, 3)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.fail_on_unavailable and not result.get("success"):
            raise SystemExit(2)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "endpoint": "wikidata",
                    "term": args.term,
                    "error": repr(exc),
                    "total_elapsed_sec": round(time.time() - start, 3),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
