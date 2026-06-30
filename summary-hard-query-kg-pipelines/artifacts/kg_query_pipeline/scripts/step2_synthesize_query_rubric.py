#!/usr/bin/env python3
"""Step 2: synthesize hard query and structured rubric from retrieval materials.

Input JSONL fields:
  case_id, source_file, source_line_no, original_user_query, context_materials

Output JSONL fields:
  case_id, source_file, source_line_no, original_user_query, material_count
  query_synthesis.query_plan:
    broad_topic, entity_family, classification_axis
    selected_contrast_set: positive/negative entities and retrieval likelihood
    retrieval_design: topic/attribute anchors and risks
    answer_boundary: required_attributes, optional_examples, must_exclude
    rubric: score_1, score_0, zero_if_answer_asserts, zero_if_mentions
    query
  context_materials: copied from input for traceability
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Tuple

from pipeline_utils import iter_jsonl, query_plan_is_synthesizable, query_similarity
from synthesize_query_joint import get_api_key, resolve_provider_defaults, synthesize_joint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=["ark", "modelhub"], default="ark")
    parser.add_argument("--model", default="ep-20260225140859-njzr9")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--endpoint", default="https://aidp.bytedance.net/api/modelhub/online/responses")
    parser.add_argument("--max-docs", type=int, default=4)
    parser.add_argument("--max-chars-per-doc", type=int, default=900)
    parser.add_argument("--max-output-tokens", type=int, default=5000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--target-count", type=int, default=0, help="Stop after this many accepted query/rubric rows. 0 means no accepted target.")
    parser.add_argument("--similarity-threshold", type=float, default=0.85, help="Drop synthetic query if similarity with original query is >= threshold.")
    parser.add_argument("--rejected-output", type=Path, default=None, help="Optional JSONL path for rejected/error rows.")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent LLM requests.")
    parser.add_argument("--resume", action="store_true", help="Append output and skip case_id values already present in output/rejected JSONL.")
    return parser.parse_args()


def existing_case_ids(*paths: Path) -> set[str]:
    ids: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for _, row in iter_jsonl(path):
            case_id = row.get("case_id")
            if case_id is not None:
                ids.add(str(case_id))
    return ids


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def process_item(item: Dict[str, Any], args: argparse.Namespace, api_key: str) -> Tuple[Dict[str, Any], bool, str]:
    materials = item.get("context_materials") or []
    try:
        result = synthesize_joint(
            materials,
            provider=args.provider,
            model=args.model,
            api_key=api_key,
            endpoint=args.endpoint,
            max_docs=args.max_docs,
            max_chars_per_doc=args.max_chars_per_doc,
            max_output_tokens=args.max_output_tokens,
        )
        status = "ok"
        error = ""
    except Exception as e:
        result = {"strategy": "joint", "query_plan": {}}
        status = "error"
        error = str(e)

    query_plan = result.get("query_plan", {}) if isinstance(result, dict) else {}
    synth_query = query_plan.get("query", "") if isinstance(query_plan, dict) else ""
    similarity = query_similarity(item.get("original_user_query", ""), synth_query)
    synthesizable, filter_reason = query_plan_is_synthesizable(query_plan if isinstance(query_plan, dict) else {})
    if status != "ok":
        keep = False
        filter_reason = error or "llm error"
    elif similarity >= args.similarity_threshold:
        keep = False
        filter_reason = f"query too similar to original: {similarity:.3f}"
    elif not synthesizable:
        keep = False
    else:
        keep = True

    row = {
        "case_id": item.get("case_id"),
        "source_file": item.get("source_file"),
        "source_line_no": item.get("source_line_no"),
        "status": status,
        "error": error,
        "accepted": keep,
        "filter_reason": "accepted" if keep else filter_reason,
        "original_query_similarity": similarity,
        "original_user_query": item.get("original_user_query", ""),
        "material_count": len(materials),
        "query_synthesis": result,
        "context_materials": materials,
    }
    return row, keep, str(synth_query)


def main() -> None:
    args = parse_args()
    resolve_provider_defaults(args)
    api_key = get_api_key(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rejected_output = args.rejected_output or args.output.with_suffix(args.output.suffix + ".rejected")
    rejected_output.parent.mkdir(parents=True, exist_ok=True)

    processed = 0
    accepted = count_jsonl(args.output) if args.resume else 0
    rejected = count_jsonl(rejected_output) if args.resume else 0
    seen_case_ids = existing_case_ids(args.output, rejected_output) if args.resume else set()
    items = []
    for _, item in iter_jsonl(args.input):
        if args.limit and len(items) >= args.limit:
            break
        case_id = item.get("case_id")
        if args.resume and case_id is not None and str(case_id) in seen_case_ids:
            continue
        items.append(item)

    max_workers = max(1, args.concurrency)
    mode = "a" if args.resume else "w"
    with args.output.open(mode, encoding="utf-8") as out, rejected_output.open(mode, encoding="utf-8") as rej:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_item, item, args, api_key) for item in items]
            for future in as_completed(futures):
                if args.target_count and accepted >= args.target_count:
                    break
                row, keep, synth_query = future.result()
                similarity = float(row.get("original_query_similarity", 0.0))
                if keep:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    accepted += 1
                else:
                    rej.write(json.dumps(row, ensure_ascii=False) + "\n")
                    rejected += 1
                processed += 1
                print(
                    f"[step2] processed={processed} accepted={accepted} rejected={rejected} "
                    f"case={row['case_id']} status={row['status']} keep={keep} sim={similarity:.3f} "
                    f"reason={row['filter_reason']} query={str(synth_query)[:80]}"
                )

    print(f"[step2] wrote={args.output} accepted={accepted} rejected={rejected} rejected_output={rejected_output}")


if __name__ == "__main__":
    main()
