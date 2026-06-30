#!/usr/bin/env python3
"""Synthesize boundary-style queries from training materials with v3 code table.

输入：
  --train-file: 训练数据 JSONL。
  --types / --properties: 永久码表中的上位类型和属性轴。
  --line-nos: 可选，逗号分隔的训练数据行号；不传则从 start-line 开始扫描。

输出：
  --output-jsonl:
    每条 accepted candidate 的结构化结果。
  --output-md:
    中文可读结果，方便人工看。

设计约束：
  1. 永久码表可以补充 type/property/value，但不能把材料实体写成永久节点。
  2. 训练材料实体只作为 temporary_instance_entities 输出。
  3. query 必须显式写出上位词/范围，例如“教育类企业”“金融机构中的支行/网点”“支持导出转换的模板”等，避免问题过宽。
  4. query 可以跨 property、跨 value 构造，例如：
     - anchor: industry=education，target: org_form=company
     - anchor: target_entity=豌豆荚，target: actor_role=employee
     - anchor: industry=finance，target: hierarchy_level=branch
"""

from __future__ import annotations

import argparse
import json
import os
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List

from step2_locate_entities_in_materials import (
    call_ark,
    extract_materials,
    iter_jsonl,
    parse_model_json,
    read_jsonl_line,
    schema_summary,
)


def domain_overlay_summary(path: Path | None) -> str:
    if not path or not path.exists():
        return "未提供专用领域 open-KB overlay。"
    lines: List[str] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            lines.append(f"- domain_id={row.get('domain_id')} label={row.get('label')}")
            sources = [s.get("url", "") for s in row.get("open_kb_sources", [])]
            if sources:
                lines.append(f"  open_kb_sources={sources[:5]}")
            concepts = []
            for concept in row.get("concepts", [])[:8]:
                maps = []
                for item in concept.get("maps_to", []):
                    if item.get("property_id"):
                        maps.append(f"{item.get('property_id')}={item.get('value_id')}")
                    elif item.get("type_id"):
                        maps.append(f"type={item.get('type_id')}")
                refs = []
                for ref in concept.get("open_kb_refs", [])[:4]:
                    name = ref.get("label") or ref.get("term") or ref.get("entity_id")
                    walks = []
                    for walk in ref.get("online_kg_walks", [])[:8]:
                        target = walk.get("target_label") or walk.get("target_id")
                        desc = walk.get("target_description") or ""
                        walks.append(f"{walk.get('relation')}->{target}({desc[:60]})")
                    if walks:
                        refs.append(f"{ref.get('source_id')}:{name} walks={walks}")
                    else:
                        refs.append(f"{ref.get('source_id')}:{name}")
                concepts.append(
                    {
                        "concept_id": concept.get("concept_id"),
                        "label": concept.get("label"),
                        "maps_to": maps,
                        "description": concept.get("description"),
                        "open_kb_refs": refs,
                    }
                )
            lines.append(f"  concepts={json.dumps(concepts, ensure_ascii=False)}")
            if row.get("derived_walks"):
                compact_walks = []
                for walk in row.get("derived_walks", [])[:10]:
                    compact_walks.append(
                        {
                            "walk_type": walk.get("walk_type"),
                            "source_concept_id": walk.get("source_concept_id"),
                            "target_concept_id": walk.get("target_concept_id"),
                            "shared_anchor_properties": walk.get("shared_anchor_properties", []),
                            "axis_property_id": walk.get("axis_property_id"),
                            "source_value_id": walk.get("source_value_id"),
                            "target_value_id": walk.get("target_value_id"),
                            "walked_relations": walk.get("walked_relations", []),
                        }
                    )
                lines.append(f"  derived_walks={json.dumps(compact_walks, ensure_ascii=False)}")
    return "\n".join(lines) if lines else "专用领域 open-KB overlay 为空。"


def build_prompt(
    record_id: str,
    materials: str,
    schema: str,
    domain_schema: str,
    max_chars: int,
) -> str:
    return f"""你要从一条训练数据的召回材料里，构造适合 summary GRPO 的“边界判别型 query”。

永久码表如下：
{schema}

专用领域 open-KB overlay KG 如下。
这些 overlay 来自 Wikidata / Schema.org / DBpedia / NIST 等公开知识库或标准的概念锚定，用来帮助识别材料中不显眼但容易混淆的边界。重点关注 derived_walks：它表示“共享上位锚点 -> 上走到共同属性轴 -> 下走到同轴相邻 value -> 相邻概念”的通用游走。
{domain_schema}

核心原则：
1. 只基于召回材料，不补充外部事实。
2. 你可以在当前材料里抽取临时实体 temporary_instance_entities，但不能把实体作为永久码表节点。
3. 如果现有码表表达不了材料中的稳定属性，可以提出 suggested_code_table_extensions；只允许新增 type/property/value，不允许新增 entity。
4. 只有当材料里同时出现 positive 实体和容易混淆的 negative 实体时才 accept。
5. query 必须清楚写出上位词/范围，不能过宽。坏例子：“有哪些？”、“哪些客户？”；好 query 应包含实体族/上位范围 + 目标属性值。
6. query 不要写“这份材料中/上文中/根据召回材料”等，因为真实 user query 没有附加材料。
7. 允许跨 property、跨 value 构造边界：
   - anchor_properties 可以是多个共享锚点属性。
   - target_property 应是目标属性轴上的一个 value。
   - negative_properties 优先来自同一属性轴上的相邻 value，尤其是 derived_walks 指出的 sibling value。
8. 如果召回材料命中某个专用领域 overlay，必须先使用 derived_walks 做 1-2 跳通用游走，再决定 query/rubric：
   - 先定位材料实体命中的 concept 或 maps_to 属性。
   - 上走到 shared_anchor_properties 或 axis_property_id。
   - 再下走到同一 axis_property_id 的 sibling value / target_concept。
   - 只有材料中同时出现目标 value 实体与 sibling value 实体时，才把 sibling value 作为 hard negative。
9. query 要自然像真实用户问题，不要写“某领域语境中”“这份材料中/上文中/根据召回材料”。但可以写清楚目标上位词和目标属性。
10. rubric 要能判 rollout：positive rubric 尽量要求“属性/边界正确”，不要强制召回不一定命中的实例；negative rubric 要列出材料中明确出现且容易混入的 negative 实体或属性。

输出必须是严格 JSON，不要 markdown：
{{
  "record_id": "{record_id}",
  "accept": true,
  "reject_reason": "",
  "original_query": "...",
  "temporary_instance_entities": [
    {{
      "local_entity_id": "e1",
      "entity_name": "...",
      "evidence": "...",
      "linked_types": [{{"type_id": "...", "label": "...", "confidence": 0.0}}],
      "linked_properties": [{{"property_id": "...", "value_id": "...", "label": "...", "confidence": 0.0, "evidence": "..."}}],
      "is_positive": true,
      "is_negative": false
    }}
  ],
  "boundary": {{
    "upper_type": {{"type_id": "...", "label": "..."}},
    "anchor_properties": [{{"property_id": "...", "value_id": "...", "label": "...", "why_anchor": "..."}}],
    "target_property": {{"property_id": "...", "value_id": "...", "label": "..."}},
    "negative_properties": [{{"property_id": "...", "value_id": "...", "label": "...", "why_confusing": "..."}}],
    "positive_entities": ["..."],
    "negative_entities": ["..."],
    "domain_walk": {{
      "domain_id": "",
      "matched_concepts": ["..."],
      "walked_relations": ["conceptA --relation--> conceptB"],
      "open_kb_support": ["..."],
      "why_domain_kg_needed": ""
    }},
    "why_summary_model_gets_confused": "..."
  }},
  "synthesized_query": "...",
  "machine_eval_guidance": {{
    "positive_rubric": ["..."],
    "negative_rubric": ["..."],
    "zero_score_conditions": ["..."],
    "boundary_note": "..."
  }},
  "suggested_code_table_extensions": [
    {{"extension_type": "type/property/value", "id": "...", "label": "...", "parent_or_property_id": "...", "reason": "..."}}
  ]
}}

record_id: {record_id}

召回材料：
{materials[:max_chars]}
"""


def render_md(rows: List[Dict[str, Any]], path: Path) -> None:
    lines = ["# 训练数据边界 Query 抽样结果", ""]
    for idx, row in enumerate(rows, 1):
        lines.extend(
            [
                f"## {idx}. {row.get('record_id')}",
                "",
                f"- 原始 query：{row.get('original_query','')}",
                f"- 合成 query：{row.get('synthesized_query','')}",
                f"- 上位词：{row.get('boundary',{}).get('upper_type',{})}",
                "",
            ]
        )
        boundary = row.get("boundary", {})
        if boundary:
            lines.append("### 边界")
            lines.append(f"- anchor：{boundary.get('anchor_properties', [])}")
            lines.append(f"- target：{boundary.get('target_property', {})}")
            lines.append(f"- negative properties：{boundary.get('negative_properties', [])}")
            lines.append(f"- positive entities：{boundary.get('positive_entities', [])}")
            lines.append(f"- negative entities：{boundary.get('negative_entities', [])}")
            if boundary.get("domain_walk"):
                lines.append(f"- 专用领域 KG 游走：{boundary.get('domain_walk')}")
            lines.append(f"- 混淆原因：{boundary.get('why_summary_model_gets_confused','')}")
            lines.append("")
        guidance = row.get("machine_eval_guidance", {})
        if guidance:
            lines.append("### 机评引导")
            lines.append(f"- positive：{guidance.get('positive_rubric', [])}")
            lines.append(f"- negative：{guidance.get('negative_rubric', [])}")
            lines.append(f"- 判 0：{guidance.get('zero_score_conditions', [])}")
            lines.append(f"- 备注：{guidance.get('boundary_note','')}")
            lines.append("")
        ents = row.get("temporary_instance_entities", [])
        if ents:
            lines.append("### 临时实体")
            for ent in ents[:12]:
                props = [f"{p.get('property_id')}={p.get('value_id')}" for p in ent.get("linked_properties", [])]
                types = [t.get("type_id") for t in ent.get("linked_types", [])]
                mark = "positive" if ent.get("is_positive") else "negative" if ent.get("is_negative") else "context"
                lines.append(f"- {ent.get('entity_name')} [{mark}] types={types} props={props}")
            lines.append("")
        exts = row.get("suggested_code_table_extensions", [])
        if exts:
            lines.append("### 建议补码表属性")
            for ext in exts:
                lines.append(f"- {ext}")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def bad_query_text(query: str) -> str:
    banned = ["这份材料", "召回材料", "上文", "根据材料", "根据召回", "本文中", "文档中"]
    for token in banned:
        if token in query:
            return f"query 包含不应出现在真实用户问题中的范围提示：{token}"
    return ""


def validate_candidate(parsed: Dict[str, Any], original_query: str, args: argparse.Namespace) -> str:
    query = str(parsed.get("synthesized_query") or "").strip()
    if not parsed.get("accept"):
        return str(parsed.get("reject_reason") or "模型拒绝")
    if not query:
        return "缺少 synthesized_query"
    reason = bad_query_text(query)
    if reason:
        return reason
    if original_query and SequenceMatcher(None, query, original_query).ratio() >= args.similarity_threshold:
        return f"合成 query 与原始 query 过于相似，similarity>={args.similarity_threshold}"
    boundary = parsed.get("boundary") or {}
    pos = boundary.get("positive_entities") or []
    neg = boundary.get("negative_entities") or []
    if len(pos) < args.min_positive_entities:
        return f"positive_entities 数量不足：{len(pos)} < {args.min_positive_entities}"
    if len(neg) < args.min_negative_entities:
        return f"negative_entities 数量不足：{len(neg)} < {args.min_negative_entities}"
    if not boundary.get("target_property"):
        return "缺少 target_property"
    if not boundary.get("anchor_properties"):
        return "缺少 anchor_properties"
    return ""


def selected_line_numbers(args: argparse.Namespace) -> List[int]:
    if args.line_nos:
        return [int(x) for x in args.line_nos.split(",") if x.strip()]
    return list(range(args.start_line, args.start_line + args.candidate_limit))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-file", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument("--properties", type=Path, required=True)
    parser.add_argument(
        "--domain-overlays",
        type=Path,
        default=Path("artifacts/code_table_pipeline_v3/outputs/open_kb_domain_overlays.jsonl"),
        help="Open-KB-backed domain overlay JSONL. Missing file is allowed.",
    )
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--rejected-jsonl", type=Path, default=None)
    parser.add_argument("--model", default=os.environ.get("ARK_MODEL", "ep-20260225140859-njzr9"))
    parser.add_argument("--api-key", default=os.environ.get("ARK_API_KEY"))
    parser.add_argument("--max-material-chars", type=int, default=9000)
    parser.add_argument("--target-count", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int, default=30)
    parser.add_argument("--start-line", type=int, default=1)
    parser.add_argument("--line-nos", default="")
    parser.add_argument("--min-positive-entities", type=int, default=1)
    parser.add_argument("--min-negative-entities", type=int, default=1)
    parser.add_argument("--similarity-threshold", type=float, default=0.92)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("ARK_API_KEY is required")
    schema = schema_summary(args.types, args.properties)
    domain_schema = domain_overlay_summary(args.domain_overlays)
    accepted: List[Dict[str, Any]] = []
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    rejected_out = None
    if args.rejected_jsonl:
        args.rejected_jsonl.parent.mkdir(parents=True, exist_ok=True)
        rejected_out = args.rejected_jsonl.open("w", encoding="utf-8")
    with args.output_jsonl.open("w", encoding="utf-8") as out:
        try:
            for line_no in selected_line_numbers(args):
                if len(accepted) >= args.target_count:
                    break
                row = read_jsonl_line(args.train_file, line_no)
                material_info = extract_materials(row)
                if len(material_info["materials"]) < 800:
                    continue
                record_id = f"train_l{line_no}"
                prompt = build_prompt(
                    record_id,
                    material_info["materials"],
                    schema,
                    domain_schema,
                    args.max_material_chars,
                )
                raw = call_ark(prompt, args.model, args.api_key)
                parsed = parse_model_json(raw)
                parsed["record_id"] = record_id
                parsed["line_no"] = line_no
                parsed["source_file"] = str(args.train_file)
                parsed["original_query"] = material_info["query"]
                parsed["material_chars_used"] = min(len(material_info["materials"]), args.max_material_chars)
                reject_reason = validate_candidate(parsed, material_info["query"], args)
                if not reject_reason:
                    accepted.append(parsed)
                    out.write(json.dumps(parsed, ensure_ascii=False) + "\n")
                    out.flush()
                    print(json.dumps({"accepted": len(accepted), "record_id": record_id, "query": parsed.get("synthesized_query")}, ensure_ascii=False))
                else:
                    parsed["pipeline_reject_reason"] = reject_reason
                    if rejected_out:
                        rejected_out.write(json.dumps(parsed, ensure_ascii=False) + "\n")
                        rejected_out.flush()
                    print(json.dumps({"rejected": record_id, "reason": reject_reason}, ensure_ascii=False))
        finally:
            if rejected_out:
                rejected_out.close()
    render_md(accepted, args.output_md)
    print(json.dumps({"accepted_count": len(accepted), "output_jsonl": str(args.output_jsonl), "output_md": str(args.output_md)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
