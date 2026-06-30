#!/usr/bin/env python3
"""Step 5: audit whether evaluation rollouts retrieved positive/negative evidence.

Input:
  --rubrics query/rubric JSONL from step2.
  --test-res-dir directory containing evaluation result CSV files.

Output Markdown:
  Per case table with:
    score
    positive hits in retrieval
    negative hits in retrieval
    negative hits in final answer
    answer preview

This is a lightweight string-match audit. It is meant to catch obvious drift
between synthesized rubric and real from-scratch retrieval, not to replace
manual semantic judgment.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from pipeline_utils import as_list, contains_any, iter_jsonl, load_json_maybe, raise_csv_field_limit


def query_plan(record: Dict[str, Any]) -> Dict[str, Any]:
    synthesis = load_json_maybe(record.get("query_synthesis"))
    return synthesis.get("query_plan") or {}


def collect_terms(qp: Dict[str, Any]) -> Dict[str, List[str]]:
    selected = qp.get("selected_contrast_set") or {}
    boundary = qp.get("answer_boundary") or {}
    rubric = qp.get("rubric") or {}
    positive = []
    positive += as_list(selected.get("positive_entities"))
    positive += as_list(boundary.get("required_attributes"))
    positive += as_list(boundary.get("must_include_or_allow"))
    positive += as_list(boundary.get("allowed_entities"))
    negative = []
    negative += as_list(selected.get("hard_negative_entities"))
    negative += as_list(selected.get("negative_attribute_values"))
    negative += as_list(boundary.get("must_exclude"))
    negative += as_list(rubric.get("zero_if_answer_asserts"))
    negative += as_list(rubric.get("zero_if_mentions"))
    negative += as_list(rubric.get("zero_if_contains"))
    return {
        "positive": list(dict.fromkeys(str(x) for x in positive if str(x).strip())),
        "negative": list(dict.fromkeys(str(x) for x in negative if str(x).strip())),
    }


def load_runs(test_res_dir: Path) -> Dict[str, List[Dict[str, str]]]:
    raise_csv_field_limit()
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for csv_path in sorted(test_res_dir.glob("*.csv")):
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                case_id = str(row.get("dataID") or "").split("__sample", 1)[0]
                if row.get("状态") == "成功" and case_id:
                    row["_source_csv"] = csv_path.name
                    grouped[case_id].append(row)
    return grouped


def run_search_text(row: Dict[str, str]) -> str:
    return "\n".join([row.get("passage1", ""), row.get("passage2", ""), row.get("passage3", ""), row.get("agenticV0Info", "")])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rubrics", type=Path, required=True)
    parser.add_argument("--test-res-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rubrics = {str(obj.get("case_id")): obj for _, obj in iter_jsonl(args.rubrics)}
    runs = load_runs(args.test_res_dir)
    parts = [
        "# Query/Rubric 与从头召回对齐审计",
        "",
        "该报告用字符串命中做轻量审计，只用于发现明显的正负例召回缺口，不替代人工语义判断。",
        "",
    ]
    for case_id in sorted(runs):
        if case_id not in rubrics:
            continue
        qp = query_plan(rubrics[case_id])
        terms = collect_terms(qp)
        parts += [
            f"## {case_id}",
            "",
            f"- query: `{qp.get('query', '')}`",
            f"- axis: `{qp.get('classification_axis', '')}`",
            f"- positive terms: {terms['positive']}",
            f"- negative terms: {terms['negative']}",
            "",
            "| run | score | positive hit in retrieval | negative hit in retrieval | negative hit in answer | answer head |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for idx, row in enumerate(runs[case_id], 1):
            search_text = run_search_text(row)
            answer = row.get("output") or ""
            answer_head = re.sub(r"\s+", " ", answer)[:140].replace("|", "\\|")
            parts.append(
                f"| {idx} | {row.get('可信分', '')} | {contains_any(search_text, terms['positive'])} | "
                f"{contains_any(search_text, terms['negative'])} | {contains_any(answer, terms['negative'])} | {answer_head} |"
            )
        parts.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(parts), encoding="utf-8")
    print(f"[step5] wrote={args.output}")


if __name__ == "__main__":
    main()

