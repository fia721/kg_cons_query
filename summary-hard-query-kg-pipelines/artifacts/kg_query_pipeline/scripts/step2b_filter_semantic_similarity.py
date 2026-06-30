#!/usr/bin/env python3
"""Step 2b: filter query/rubric rows by LLM semantic similarity.

Input JSONL fields:
  Rows produced by step2_synthesize_query_rubric.py, especially:
    original_user_query
    query_synthesis.query_plan.query

Output JSONL fields:
  Same row schema as step2, with added:
    semantic_similarity:
      score: 0.0-1.0
      relation: same_intent|near_duplicate|overlap_different_target|different
      should_reject: bool
      reason: str

Rejected output:
  Rows rejected by semantic similarity, plus semantic_similarity details.

Purpose:
  String similarity can miss paraphrases. This step asks an LLM whether the
  synthesized query is essentially the same task as the original user query.
  The pipeline keeps only rows whose synthesized query has a meaningfully
  different target, scope, or attribute boundary.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
LEGACY_SCRIPTS = SCRIPT_DIR.parents[1] / "scripts"
if str(LEGACY_SCRIPTS) not in sys.path:
    sys.path.append(str(LEGACY_SCRIPTS))

from pipeline_utils import iter_jsonl  # noqa: E402
from synthesize_query_joint import (  # noqa: E402
    call_llm,
    get_api_key,
    resolve_provider_defaults,
)


SEMANTIC_SIMILARITY_PROMPT = """你是 query 语义相似度审核器。

任务：
- 判断“合成 query”是否和“原始 user query”本质上是同一个检索/问答任务。
- 这是训练数据构造过滤步骤；如果合成 query 只是原始 query 的改写、同义转述、范围极接近，应拒绝。
- 如果两者共享主题词，但目标属性、答案集合、过滤条件、考察边界明显不同，可以保留。

判定标准：
- same_intent：答案目标基本一致，只是措辞不同。
- near_duplicate：目标高度重合，可能有轻微范围变化，但训练价值不足。
- overlap_different_target：主题相关，但问的属性/集合/边界不同，可以保留。
- different：基本不同，可以保留。

注意：
- 不要只看字面相似度；要判断答案集合是否高度重合。
- 如果合成 query 更宽/更窄但仍主要要求同一答案，也应拒绝。
- 如果合成 query 从原始事实问法改成了 hard negative 边界问法，且答案集合不同，可以保留。

只输出严格 JSON：
{
  "score": 0.0,
  "relation": "same_intent|near_duplicate|overlap_different_target|different",
  "should_reject": true,
  "reason": "一句话说明"
}
"""


def query_plan_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    query_synthesis = row.get("query_synthesis") or {}
    query_plan = query_synthesis.get("query_plan") or {}
    return query_plan if isinstance(query_plan, dict) else {}


def semantic_similarity_input(row: Dict[str, Any]) -> str:
    query_plan = query_plan_from_row(row)
    payload = {
        "original_user_query": row.get("original_user_query", ""),
        "synthetic_query": query_plan.get("query", ""),
        "query_type": query_plan.get("query_type", ""),
        "broad_topic": query_plan.get("broad_topic", ""),
        "classification_axis": query_plan.get("classification_axis", ""),
        "target_attribute_value": (query_plan.get("selected_contrast_set") or {}).get("target_attribute_value", ""),
        "answer_boundary": query_plan.get("answer_boundary", {}),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rejected-output", type=Path, default=None)
    parser.add_argument("--provider", choices=["ark", "modelhub"], default="ark")
    parser.add_argument("--model", default="ep-20260225140859-njzr9")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--endpoint", default="https://aidp.bytedance.net/api/modelhub/online/responses")
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--target-count", type=int, default=0)
    parser.add_argument("--max-output-tokens", type=int, default=1200)
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


def process_row(row: Dict[str, Any], args: argparse.Namespace, api_key: str) -> Tuple[Dict[str, Any], bool]:
    try:
        result = call_llm(
            [
                {"role": "system", "content": SEMANTIC_SIMILARITY_PROMPT},
                {"role": "user", "content": semantic_similarity_input(row)},
            ],
            provider=args.provider,
            model=args.model,
            api_key=api_key,
            endpoint=args.endpoint,
            max_output_tokens=args.max_output_tokens,
        )
        score = float(result.get("score", 0.0))
        relation = str(result.get("relation", ""))
        should_reject = bool(result.get("should_reject", False))
        if score >= args.threshold and relation in {"same_intent", "near_duplicate"}:
            should_reject = True
        semantic = {
            "status": "ok",
            "score": score,
            "relation": relation,
            "should_reject": should_reject,
            "reason": str(result.get("reason", "")),
        }
    except Exception as e:
        semantic = {
            "status": "error",
            "score": 1.0,
            "relation": "error",
            "should_reject": True,
            "reason": str(e),
        }

    row["semantic_similarity"] = semantic
    keep = not semantic["should_reject"]
    if not keep:
        row["accepted"] = False
        row["filter_reason"] = "semantic similarity rejected: " + semantic.get("reason", "")
    return row, keep


def main() -> None:
    args = parse_args()
    resolve_provider_defaults(args)
    api_key = get_api_key(args)
    rejected_output = args.rejected_output or args.output.with_suffix(args.output.suffix + ".semantic_rejected")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rejected_output.parent.mkdir(parents=True, exist_ok=True)

    processed = 0
    accepted = count_jsonl(args.output) if args.resume else 0
    rejected = count_jsonl(rejected_output) if args.resume else 0
    seen_case_ids = existing_case_ids(args.output, rejected_output) if args.resume else set()
    rows = []
    for _, row in iter_jsonl(args.input):
        case_id = row.get("case_id")
        if args.resume and case_id is not None and str(case_id) in seen_case_ids:
            continue
        rows.append(row)
    max_workers = max(1, args.concurrency)
    mode = "a" if args.resume else "w"
    with args.output.open(mode, encoding="utf-8") as out, rejected_output.open(mode, encoding="utf-8") as rej:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_row, row, args, api_key) for row in rows]
            for future in as_completed(futures):
                if args.target_count and accepted >= args.target_count:
                    break
                row, keep = future.result()
                semantic = row["semantic_similarity"]
                if keep:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    accepted += 1
                else:
                    rej.write(json.dumps(row, ensure_ascii=False) + "\n")
                    rejected += 1
                processed += 1
                query_plan = query_plan_from_row(row)
                print(
                    f"[step2b] processed={processed} accepted={accepted} rejected={rejected} "
                    f"case={row.get('case_id')} keep={keep} score={semantic['score']:.3f} "
                    f"relation={semantic['relation']} query={str(query_plan.get('query', ''))[:80]}"
                )

    print(f"[step2b] wrote={args.output} accepted={accepted} rejected={rejected} rejected_output={rejected_output}")


if __name__ == "__main__":
    main()
