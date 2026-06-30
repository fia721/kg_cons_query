#!/usr/bin/env python3
"""Run the local S-Path-RAG-style path selector on existing KG artifacts.

Inputs:
  --graph: overlay JSONL or permanent KG JSON.
  --case: source::target::context. Target/context can be empty.

Outputs:
  JSON with ranked paths and a Markdown summary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from spath_rag_component import load_graph, ranked_paths_for_terms


def parse_case(text: str) -> Dict[str, str]:
    parts = text.split("::")
    while len(parts) < 3:
        parts.append("")
    return {"source": parts[0].strip(), "target": parts[1].strip(), "context": parts[2].strip()}


def path_line(path: Dict[str, Any]) -> str:
    chunks = []
    nodes = path.get("nodes", [])
    edges = path.get("edges", [])
    for i, node in enumerate(nodes):
        label = node.get("label") or node.get("id")
        chunks.append(label)
        if i < len(edges):
            chunks.append(f"--{edges[i].get('relation')}-->")
    return " ".join(chunks)


def write_markdown(results: List[Dict[str, Any]], output_md: Path) -> None:
    lines = ["# S-Path-RAG 组件 Demo", ""]
    for result in results:
        lines.append(f"## {result.get('source_term')} -> {result.get('target_term') or '(top paths)'}")
        if result.get("error"):
            lines.append(f"- error: `{result['error']}`")
            lines.append("")
            continue
        lines.append("")
        lines.append("### Source Matches")
        for item in result.get("source_matches", []):
            lines.append(f"- `{item['id']}` {item.get('label','')} ({item.get('kind','')}) score={item.get('score')}")
        if result.get("target_matches"):
            lines.append("")
            lines.append("### Target Matches")
            for item in result.get("target_matches", []):
                lines.append(f"- `{item['id']}` {item.get('label','')} ({item.get('kind','')}) score={item.get('score')}")
        lines.append("")
        lines.append("### Ranked Paths")
        for i, path in enumerate(result.get("paths", []), 1):
            status = "REJECT" if path.get("rejected") else "ACCEPT"
            lines.append(
                f"{i}. **{status}** score={path.get('score')} sem={path.get('semantic_score')} "
                f"generic_penalty={path.get('generic_penalty')}"
            )
            lines.append(f"   - {path_line(path)}")
            if path.get("reject_reasons"):
                lines.append(f"   - reject_reasons: {', '.join(path['reject_reasons'])}")
        lines.append("")
    output_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--case", action="append", required=True, help="source::target::context")
    parser.add_argument("--max-hops", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    args = parser.parse_args()

    graph = load_graph(args.graph)
    results = []
    for raw_case in args.case:
        case = parse_case(raw_case)
        results.append(
            ranked_paths_for_terms(
                graph,
                source_term=case["source"],
                target_term=case["target"],
                query_context=case["context"],
                max_hops=args.max_hops,
                top_k=args.top_k,
            )
        )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(results, args.output_md)
    print(json.dumps({"output_json": str(args.output_json), "output_md": str(args.output_md), "num_cases": len(results)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

