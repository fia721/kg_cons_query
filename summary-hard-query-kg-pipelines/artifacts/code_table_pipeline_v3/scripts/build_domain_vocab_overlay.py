#!/usr/bin/env python3
"""Convert domain vocabulary terms into an overlay graph consumed by S-Path-RAG.

输入：
  --terms-jsonl: domain_vocab_terms.jsonl

输出：
  --output-jsonl: domain vocab overlay JSONL

每个 term 会变成一个 concept，并按 properties 映射到属性轴和值。
同一 domain 内共享某个 property 但 value 不同的 concept，可由
spath_rag_component 的 property/value graph 自动形成 sibling_value 游走。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def safe_id(text: str) -> str:
    text = re.sub(r"\s+", "_", text.strip().lower())
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", text)
    return text.strip("_")[:80] or "term"


def main() -> None:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--terms-jsonl", type=Path, default=root / "open_sources/domain_vocab/index/domain_vocab_terms.jsonl")
    parser.add_argument("--output-jsonl", type=Path, required=True)
    args = parser.parse_args()

    by_domain: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in iter_jsonl(args.terms_jsonl):
        by_domain[row.get("domain") or "generic"].append(row)

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as f:
        for domain, rows in sorted(by_domain.items()):
            concepts = []
            for row in rows:
                cid = f"{domain}.{safe_id(row.get('surface', 'term'))}"
                concepts.append(
                    {
                        "concept_id": cid,
                        "label": row.get("surface", cid),
                        "maps_to": row.get("properties", []),
                        "description": row.get("notes", ""),
                        "aliases": row.get("aliases", []),
                        "parents": row.get("parents", []),
                        "source_id": row.get("source_id", ""),
                        "source_status": row.get("source_status", ""),
                        "open_kb_refs": [],
                    }
                )
            overlay = {
                "domain_id": domain,
                "label": domain,
                "source_policy": "domain_vocab_terms",
                "relations": [],
                "query_patterns": [],
                "open_kb_sources": [],
                "concepts": concepts,
            }
            f.write(json.dumps(overlay, ensure_ascii=False) + "\n")
    print(json.dumps({"output_jsonl": str(args.output_jsonl), "domains": {k: len(v) for k, v in by_domain.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
