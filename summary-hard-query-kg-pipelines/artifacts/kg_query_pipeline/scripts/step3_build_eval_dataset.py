#!/usr/bin/env python3
"""Step 3: build evaluation dataset from synthesized query/rubric JSONL.

Input JSONL fields:
  case_id
  query_synthesis.query_plan.query
  query_synthesis.query_plan.answer_boundary/rubric

Output CSV/XLSX fields:
  dataID: unique id for each sampling row, e.g. train_line1__sample01
  query: synthesized query
  企业内是否有知识: always 是
  预期答复（机评文本）: structured judge guidance
  ref图片文件名称: empty
  机评忽略case: empty

Sampling:
  --max-queries controls how many query records to export.
  --samples-per-query repeats each query N times for independent rollout samples.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from pipeline_utils import OUTPUT_FIELDS, build_expected_answer, iter_jsonl, load_json_maybe, write_xlsx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-xlsx", type=Path, required=True)
    parser.add_argument("--max-queries", type=int, default=3)
    parser.add_argument("--samples-per-query", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = []
    used = 0
    for _, record in iter_jsonl(args.input):
        if args.max_queries and used >= args.max_queries:
            break
        synthesis = load_json_maybe(record.get("query_synthesis"))
        query_plan = synthesis.get("query_plan") or {}
        query = str(query_plan.get("query") or "").strip()
        if not query:
            continue
        expected = build_expected_answer(query_plan)
        case_id = str(record.get("case_id") or f"case{used + 1}")
        for sample_idx in range(1, args.samples_per_query + 1):
            rows.append(
                {
                    "dataID": f"{case_id}__sample{sample_idx:02d}",
                    "query": query,
                    "企业内是否有知识": "是",
                    "预期答复（机评文本）": expected,
                    "ref图片文件名称": "",
                    "机评忽略case": "",
                }
            )
        used += 1

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    write_xlsx(rows, args.output_xlsx, fields=OUTPUT_FIELDS)
    print(f"[step3] wrote_csv={args.output_csv} wrote_xlsx={args.output_xlsx} rows={len(rows)} queries={used}")


if __name__ == "__main__":
    main()

