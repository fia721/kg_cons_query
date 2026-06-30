#!/usr/bin/env python3
"""Build an evaluation CSV/XLSX from v3 boundary-query JSONL.

输入：
  --input: step3_synthesize_boundary_queries_from_train.py 输出的 JSONL。
    需要字段：
      record_id
      synthesized_query
      boundary.positive_entities / boundary.negative_entities
      boundary.anchor_properties / target_property / negative_properties
      machine_eval_guidance.positive_rubric / negative_rubric / zero_score_conditions / boundary_note

输出：
  --output-csv / --output-xlsx:
    测评平台需要的字段：
      dataID, query, 企业内是否有知识, 预期答复（机评文本）, ref图片文件名称, 机评忽略case

逻辑：
  1. 将 v3 结构转换成 kg_query_pipeline 的通用 query_plan。
  2. 复用 pipeline_utils.build_expected_answer 生成机评引导。
  3. 每条 query 重复 samples-per-query 次，用于多次 rollout 采样。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


KG_QUERY_SCRIPTS = Path(__file__).resolve().parents[2] / "kg_query_pipeline" / "scripts"
if str(KG_QUERY_SCRIPTS) not in sys.path:
    sys.path.append(str(KG_QUERY_SCRIPTS))

from pipeline_utils import OUTPUT_FIELDS, build_expected_answer, iter_jsonl, write_xlsx  # noqa: E402


def compact_prop(prop: Dict[str, Any]) -> str:
    if not prop:
        return ""
    label = prop.get("label") or prop.get("value_id") or prop.get("type_id") or ""
    prop_id = prop.get("property_id")
    value_id = prop.get("value_id")
    if prop_id and value_id:
        return f"{label}（{prop_id}={value_id}）"
    if prop.get("type_id"):
        return f"{label}（type={prop.get('type_id')}）"
    return str(label)


def as_text_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return [str(value)]


def convert_record(row: Dict[str, Any]) -> Dict[str, Any]:
    boundary = row.get("boundary") or {}
    guidance = row.get("machine_eval_guidance") or {}
    target = compact_prop(boundary.get("target_property") or {})
    anchors = [compact_prop(x) for x in boundary.get("anchor_properties", [])]
    negatives = [compact_prop(x) for x in boundary.get("negative_properties", [])]
    positive_entities = as_text_list(boundary.get("positive_entities"))
    negative_entities = as_text_list(boundary.get("negative_entities"))

    required_attrs = as_text_list(guidance.get("positive_rubric"))
    if target:
        required_attrs.insert(0, f"目标属性边界正确：{target}")
    if anchors:
        required_attrs.insert(0, "回答需满足锚点范围：" + "；".join(x for x in anchors if x))

    boundary_rule_parts = []
    if guidance.get("boundary_note"):
        boundary_rule_parts.append(str(guidance["boundary_note"]))
    if target:
        boundary_rule_parts.append(f"只统计目标属性为 {target} 的对象。")
    if negatives:
        boundary_rule_parts.append("不得混入负向属性：" + "；".join(x for x in negatives if x))

    query_plan = {
        "query": row.get("synthesized_query", ""),
        "classification_axis": "；".join(x for x in [target] + anchors if x),
        "selected_contrast_set": {
            "target_attribute_value": target,
            "positive_entities": positive_entities,
            "negative_entities": negative_entities,
        },
        "answer_boundary": {
            "required_attributes": required_attrs,
            "optional_examples": positive_entities,
            "must_exclude": negative_entities + as_text_list(guidance.get("negative_rubric")),
            "boundary_rule": " ".join(boundary_rule_parts),
        },
        "rubric": {
            "score_1": "答案符合目标属性边界，未把负例实体或负向属性作为正确答案混入。",
            "score_0": "答案把 negative 实体、negative 属性或 zero_score_conditions 中的内容作为正确答案、并列答案或目标属性成员纳入。",
            "zero_if_answer_asserts": negative_entities,
            "zero_if_contains": as_text_list(guidance.get("zero_score_conditions")),
            "non_zero_if_negated": negative_entities,
            "notes": guidance.get("boundary_note", ""),
        },
    }
    return {
        "case_id": row.get("record_id") or row.get("case_id") or "",
        "query_plan": query_plan,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-xlsx", type=Path, required=True)
    parser.add_argument("--samples-per-query", type=int, default=4)
    parser.add_argument("--max-queries", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = []
    used = 0
    for _, row in iter_jsonl(args.input):
        if args.max_queries and used >= args.max_queries:
            break
        converted = convert_record(row)
        query = str(converted["query_plan"].get("query") or "").strip()
        if not query:
            continue
        expected = build_expected_answer(converted["query_plan"])
        case_id = converted["case_id"] or f"case{used + 1}"
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
    print(json.dumps({"queries": used, "rows": len(rows), "csv": str(args.output_csv), "xlsx": str(args.output_xlsx)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
