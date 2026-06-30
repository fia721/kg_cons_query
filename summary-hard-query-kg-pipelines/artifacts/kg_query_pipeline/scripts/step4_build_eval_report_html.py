#!/usr/bin/env python3
"""Step 4: build an HTML report from query/rubric and evaluation result CSVs.

Input:
  --rubrics step2 query/rubric JSONL.
  --eval-csv step3 evaluation dataset CSV.
  --test-res-dir directory containing evaluation result CSVs.

Output:
  HTML report grouped by base case id. Each case shows query, positive/negative
  rubric, judge guidance, and each rollout answer/score/retrieval hit summary.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from pipeline_utils import as_list, contains_any, iter_jsonl, load_json_maybe, raise_csv_field_limit


def base_case_id(data_id: str) -> str:
    return str(data_id or "").split("__sample", 1)[0]


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def html_block(title: str, content: Any, cls: str = "") -> str:
    body = html.escape("" if content is None else str(content))
    return f"<section class='block {cls}'><h4>{html.escape(title)}</h4><pre>{body}</pre></section>"


def query_plan(record: Dict[str, Any]) -> Dict[str, Any]:
    return (load_json_maybe(record.get("query_synthesis")).get("query_plan") or {})


def collect_negative_terms(qp: Dict[str, Any]) -> List[str]:
    selected = qp.get("selected_contrast_set") or {}
    boundary = qp.get("answer_boundary") or {}
    rubric = qp.get("rubric") or {}
    terms = []
    terms += as_list(selected.get("hard_negative_entities"))
    terms += as_list(selected.get("negative_attribute_values"))
    terms += as_list(boundary.get("must_exclude"))
    terms += as_list(rubric.get("zero_if_answer_asserts"))
    terms += as_list(rubric.get("zero_if_mentions"))
    terms += as_list(rubric.get("zero_if_contains"))
    return list(dict.fromkeys(str(x) for x in terms if str(x).strip()))


def load_eval(path: Path) -> Dict[str, Dict[str, str]]:
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            out[row.get("dataID", "")] = row
    return out


def load_runs(test_res_dir: Path) -> Dict[str, List[Dict[str, str]]]:
    raise_csv_field_limit()
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for csv_path in sorted(test_res_dir.glob("*.csv")):
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("状态") == "成功" and row.get("dataID"):
                    row["_source_csv"] = csv_path.name
                    grouped[base_case_id(row["dataID"])].append(row)
    return grouped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rubrics", type=Path, required=True)
    parser.add_argument("--eval-csv", type=Path, required=True)
    parser.add_argument("--test-res-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rubrics = {str(obj.get("case_id")): obj for _, obj in iter_jsonl(args.rubrics)}
    eval_rows = load_eval(args.eval_csv)
    runs = load_runs(args.test_res_dir)
    css = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:24px;background:#f7f8fa;color:#202124}
.case{background:#fff;border:1px solid #ddd;border-radius:10px;margin:18px 0;padding:18px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.block{border:1px solid #e5e7eb;border-radius:8px;padding:10px;background:#fcfcfd;min-width:0}
.wide{grid-column:1/-1}.run{border-left:4px solid #3b82f6;margin-top:14px;padding-left:12px}.bad{border-left-color:#ef4444}
pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px;line-height:1.45;max-height:420px;overflow:auto}
@media(max-width:1000px){.grid{grid-template-columns:1fr}}
"""
    parts = ["<!doctype html><html><head><meta charset='utf-8'>", "<title>KG Query Pipeline Report</title>", f"<style>{css}</style></head><body>", "<h1>KG Query Pipeline Report</h1>"]
    for case_id in sorted(rubrics):
        qp = query_plan(rubrics[case_id])
        query = qp.get("query", "")
        negative_terms = collect_negative_terms(qp)
        example_eval = next((v for k, v in eval_rows.items() if base_case_id(k) == case_id), {})
        parts.append(f"<article class='case'><h2>{html.escape(case_id)} - {html.escape(query)}</h2><div class='grid'>")
        parts.append(html_block("原始 query", rubrics[case_id].get("original_user_query", "")))
        parts.append(html_block("改写 query", query))
        parts.append(html_block("query/rubric", compact_json(qp), "wide"))
        parts.append(html_block("机评引导", example_eval.get("预期答复（机评文本）", ""), "wide"))
        parts.append("</div>")
        for idx, row in enumerate(runs.get(case_id, []), 1):
            text = "\n".join([row.get("passage1", ""), row.get("passage2", ""), row.get("passage3", ""), row.get("agenticV0Info", "")])
            hits = contains_any(text, negative_terms)
            answer_hits = contains_any(row.get("output", ""), negative_terms)
            cls = "run bad" if str(row.get("可信分", "")).strip() in {"0", "0.0"} else "run"
            parts.append(f"<section class='{cls}'><h3>run {idx} / score {html.escape(row.get('可信分',''))}</h3>")
            parts.append(html_block("negative hits in retrieval", hits, "wide"))
            parts.append(html_block("negative hits in answer", answer_hits, "wide"))
            parts.append(html_block("answer", row.get("output", ""), "wide"))
            parts.append(html_block("llm_reason", row.get("llm_reason", ""), "wide"))
            parts.append("</section>")
        parts.append("</article>")
    parts.append("</body></html>")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(parts), encoding="utf-8")
    print(f"[step4] wrote={args.output}")


if __name__ == "__main__":
    main()
