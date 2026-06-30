#!/usr/bin/env python3
"""Step 2c: rewrite synthetic query surface forms without changing rubrics.

Purpose:
  Pipeline1's step2 has already been validated for producing useful
  positive/negative boundaries, but many generated queries share the same
  "有哪些" surface form. This step only rewrites the query wording to improve
  query-form diversity. It must not change the answer target, rubric,
  positive boundary, or negative boundary.

Input JSONL fields:
  case_id
  query_synthesis.query_plan.query
  query_synthesis.query_plan.query_type
  query_synthesis.query_plan.answer_boundary
  query_synthesis.query_plan.rubric

Output JSONL fields:
  Same as input, plus:
  query_synthesis.query_plan.original_query_before_surface_rewrite
  query_synthesis.query_plan.surface_rewrite

Notes:
  This is intentionally deterministic and conservative. It avoids LLM calls so
  full-data postprocessing is cheap and reproducible.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

from pipeline_utils import iter_jsonl, load_json_maybe


QUESTION_SUFFIX_RE = re.compile(r"[？?。\\s]+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def stable_choice(items: Iterable[str], key: str) -> str:
    choices = list(items)
    if not choices:
        return ""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return choices[int(digest[:8], 16) % len(choices)]


def strip_question_suffix(query: str) -> str:
    return QUESTION_SUFFIX_RE.sub("", query.strip())


def rewrite_enumeration_query(query: str, key: str) -> tuple[str, str]:
    q = strip_question_suffix(query)

    patterns = [
        (r"^(?P<head>.+?)有哪些$", ["请列出{head}。", "{head}包括哪些？", "哪些属于{head}？", "{head}分别是什么？"]),
        (r"^(?P<head>.+?)都有谁$", ["{head}分别是谁？", "请列出{head}。", "{head}包括哪些人？"]),
        (r"^(?P<head>.+?)包括哪些$", ["{head}有哪些？", "请列出{head}。", "哪些属于{head}？"]),
        (r"^哪些(?P<body>.+)$", ["请列出{body}。", "有哪些{body}？"]),
    ]
    for pattern, templates in patterns:
        match = re.match(pattern, q)
        if not match:
            continue
        values = {k: v.strip(" ，,。") for k, v in match.groupdict().items()}
        template = stable_choice(templates, key + q + pattern)
        rewritten = template.format(**values)
        if rewritten != query:
            return rewritten, "enumeration_template"

    if "有哪些" in q:
        rewritten = q.replace("有哪些", "包括哪些", 1) + "？"
        return rewritten, "enumeration_replace"
    return query, "unchanged"


def rewrite_definition_query(query: str, key: str) -> tuple[str, str]:
    q = strip_question_suffix(query)
    patterns = [
        (r"^(?P<head>.+?)是什么$", ["{head}指什么？", "{head}如何定义？", "怎么理解{head}？"]),
        (r"^(?P<head>.+?)的定义是什么$", ["{head}怎么定义？", "{head}指什么？", "如何理解{head}？"]),
    ]
    for pattern, templates in patterns:
        match = re.match(pattern, q)
        if not match:
            continue
        values = {k: v.strip(" ，,。") for k, v in match.groupdict().items()}
        rewritten = stable_choice(templates, key + q + pattern).format(**values)
        if rewritten != query:
            return rewritten, "definition_template"
    return query, "unchanged"


def rewrite_query(query: str, query_type: str, key: str) -> tuple[str, str]:
    if not query.strip():
        return query, "empty"
    if query_type in {"direct_attribute"} or "是什么" in query:
        rewritten, reason = rewrite_definition_query(query, key)
        if reason != "unchanged":
            return rewritten, reason
    if query_type in {"enumeration_filter", "fine_grained_attribute"} or any(token in query for token in ["有哪些", "哪些", "都有谁", "包括哪些"]):
        return rewrite_enumeration_query(query, key)
    return query, "unchanged"


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    total = rewritten_count = 0

    with args.output.open("w", encoding="utf-8") as out:
        for _, record in iter_jsonl(args.input):
            total += 1
            row: Dict[str, Any] = copy.deepcopy(record)
            synthesis = load_json_maybe(row.get("query_synthesis"))
            query_plan = synthesis.get("query_plan") or {}
            query = str(query_plan.get("query") or "").strip()
            query_type = str(query_plan.get("query_type") or "").strip()
            key = str(row.get("case_id") or total)
            rewritten, reason = rewrite_query(query, query_type, key)
            if rewritten and rewritten != query:
                query_plan["original_query_before_surface_rewrite"] = query
                query_plan["query"] = rewritten
                query_plan["surface_rewrite"] = {
                    "method": "deterministic_template",
                    "reason": reason,
                    "scope": "query_only_rubric_unchanged",
                }
                rewritten_count += 1
            synthesis["query_plan"] = query_plan
            row["query_synthesis"] = synthesis
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"[step2c] input={args.input} output={args.output} rows={total} rewritten={rewritten_count}")


if __name__ == "__main__":
    main()
