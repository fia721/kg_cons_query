#!/usr/bin/env python3
"""Locate material entities against the permanent v3 code table.

输入：
  --case-spec: JSONL，每行指定一个 case：
    {
      "case_id": "wandoujia_step20_l80",
      "source_file": "...jsonl",
      "line_no": 80,
      "note": "豌豆荚"
    }
  --types: v3 entity_types.jsonl
  --properties: v3 property_axes.jsonl
  --output-jsonl: 实体定位结果 JSONL
  --output-md: 中文可读报告

输出：
  每个 case 的召回材料实体定位结果：
    - entity_name / aliases
    - evidence
    - linked_types
    - linked_properties
    - relations
    - ambiguity_notes

注意：
  这里构建的是“临时实例图”的实体结果，不会写回永久 KG。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List


ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_jsonl_line(path: Path, line_no: int) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            if idx == line_no:
                return json.loads(line)
    raise IndexError(f"{path} has no line {line_no}")


def strip_noise(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"<<url\d+>>", "", text)
    text = re.sub(r"<qa_image>.*?</qa_image>", "", text, flags=re.S)
    text = re.sub(r"\\n", "\n", text)
    return text


def try_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s or s[0] not in "[{":
        return value
    try:
        return json.loads(s)
    except Exception:
        return value


def collect_texts(obj: Any, texts: List[str], preferred_keys: set[str]) -> None:
    obj = try_json(obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in preferred_keys:
                if isinstance(value, str):
                    texts.append(value)
                else:
                    collect_texts(value, texts, preferred_keys)
            elif isinstance(value, (dict, list)):
                collect_texts(value, texts, preferred_keys)
    elif isinstance(obj, list):
        for item in obj:
            collect_texts(item, texts, preferred_keys)


def extract_materials(row: Dict[str, Any]) -> Dict[str, Any]:
    query = row.get("query") or ""
    texts: List[str] = []

    if row.get("retrieval_materials"):
        collect_texts(row["retrieval_materials"], texts, {"content", "search_result", "title"})

    for key in ["passage1", "passage2", "passage3"]:
        if row.get(key):
            collect_texts(row[key], texts, {"search_result", "content", "title"})

    for msg in row.get("messages", []) or []:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user" and not query and isinstance(content, str):
            query = content[:500]
        if role == "tool" and isinstance(content, str):
            texts.append(content)
            continue
        if role in {"tool", "assistant"}:
            collect_texts(content, texts, {"content", "search_result", "title", "author", "data_source"})

    joined = "\n\n".join(strip_noise(t) for t in texts if isinstance(t, str) and t.strip())
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return {"query": query, "materials": joined}


def schema_summary(types_path: Path, properties_path: Path) -> str:
    types = list(iter_jsonl(types_path))
    properties = list(iter_jsonl(properties_path))
    type_lines = [f"- {t['type_id']}: {t.get('label','')}" for t in types]
    prop_lines = []
    for prop in properties:
        values = "、".join(f"{v['value_id']}={v.get('label','')}" for v in prop.get("values", [])[:30])
        if prop.get("value_type") == "string":
            values = "string"
        prop_lines.append(f"- {prop['property_id']}({prop.get('label','')}): {values}")
    return "【可选上位类型】\n" + "\n".join(type_lines) + "\n\n【可选属性轴和值】\n" + "\n".join(prop_lines)


def build_prompt(case_id: str, query: str, materials: str, schema: str, max_chars: int) -> str:
    materials = materials[:max_chars]
    return f"""你要把召回材料中的实体定位到给定的永久码表。只基于材料，不要补充外部事实。

永久码表如下：
{schema}

任务要求：
1. 抽取材料中对回答问题可能有区分价值的实体，不需要抽无关泛词。
2. 每个实体输出 linked_types 和 linked_properties。属性必须从码表中选择，不能自造 property_id/value_id。
3. 如果实体之间存在材料内关系，用 relations 表达，例如 person -> org 的 works_for/reports_on/authored，building -> place 的 located_in。
4. 如果属性不确定，用 confidence 0.4-0.7，并在 ambiguity_notes 解释。
5. 特别关注同一主题下容易混淆的实体：同行业不同组织形态、同目标组织不同人物角色、同金融主题不同机构层级、同产品/模板不同支持状态。
6. 输出必须是严格 JSON，不要 markdown。

输出 schema：
{{
  "case_id": "{case_id}",
  "query": "...",
  "entities": [
    {{
      "local_entity_id": "e1",
      "entity_name": "...",
      "aliases": [],
      "evidence": "材料中的短证据",
      "linked_types": [{{"type_id": "...", "label": "...", "confidence": 0.0}}],
      "linked_properties": [{{"property_id": "...", "value_id": "...", "label": "...", "confidence": 0.0, "evidence": "..."}}],
      "relations": [{{"relation": "works_for/reports_on/authored/belongs_to/located_in/supports/does_not_support/converts_to", "target_entity_name": "...", "evidence": "...", "confidence": 0.0}}],
      "ambiguity_notes": "..."
    }}
  ],
  "material_boundary_observations": [
    "哪些实体共享同一锚点但属性不同"
  ]
}}

case_id: {case_id}
query: {query}

召回材料：
{materials}
"""


def call_ark(prompt: str, model: str, api_key: str, timeout: int = 300) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 12000,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ARK_BASE_URL,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except Exception as e:
            if "429" in str(e):
                sleep_seconds = int(os.environ.get("ARK_429_SLEEP_SECONDS", "60"))
                print(f"[call_ark] 429 rate limited, retrying after {sleep_seconds}s: {e}", flush=True)
                time.sleep(sleep_seconds)
                continue
            if attempt >= 3:
                raise
            time.sleep(5 * (attempt + 1))
            attempt += 1
    raise RuntimeError("unreachable")


def parse_model_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def render_md(rows: List[Dict[str, Any]], path: Path) -> None:
    lines = ["# 召回材料实体 KG 定位结果", ""]
    for row in rows:
        lines.extend(
            [
                f"## {row.get('case_id')}",
                "",
                f"- 来源：`{row.get('source_file')}:{row.get('line_no')}`",
                f"- Query：{row.get('query','')}",
                f"- 实体数：{len(row.get('entities', []))}",
                "",
            ]
        )
        for ent in row.get("entities", []):
            lines.append(f"### {ent.get('entity_name')}")
            if ent.get("evidence"):
                lines.append(f"- 证据：{ent.get('evidence')}")
            types = ent.get("linked_types", [])
            if types:
                lines.append("- 类型：" + "；".join(f"{t.get('type_id')}({t.get('label')}, {t.get('confidence')})" for t in types))
            props = ent.get("linked_properties", [])
            if props:
                lines.append("- 属性：" + "；".join(f"{p.get('property_id')}={p.get('value_id')}({p.get('label')}, {p.get('confidence')})" for p in props))
            rels = ent.get("relations", [])
            if rels:
                lines.append("- 关系：" + "；".join(f"{r.get('relation')} -> {r.get('target_entity_name')}({r.get('confidence')})" for r in rels))
            if ent.get("ambiguity_notes"):
                lines.append(f"- 歧义：{ent.get('ambiguity_notes')}")
            lines.append("")
        obs = row.get("material_boundary_observations", [])
        if obs:
            lines.append("### 材料内边界观察")
            for item in obs:
                lines.append(f"- {item}")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-spec", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument("--properties", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--model", default=os.environ.get("ARK_MODEL", "ep-20260225140859-njzr9"))
    parser.add_argument("--api-key", default=os.environ.get("ARK_API_KEY"))
    parser.add_argument("--max-material-chars", type=int, default=18000)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("ARK_API_KEY is required")
    schema = schema_summary(args.types, args.properties)
    specs = list(iter_jsonl(args.case_spec))
    if args.limit:
        specs = specs[: args.limit]
    outputs = []
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as out:
        for spec in specs:
            row = read_jsonl_line(Path(spec["source_file"]), int(spec["line_no"]))
            material_info = extract_materials(row)
            prompt = build_prompt(
                spec["case_id"],
                material_info["query"],
                material_info["materials"],
                schema,
                args.max_material_chars,
            )
            raw = call_ark(prompt, args.model, args.api_key)
            parsed = parse_model_json(raw)
            parsed["case_id"] = spec["case_id"]
            parsed["source_file"] = spec["source_file"]
            parsed["line_no"] = spec["line_no"]
            parsed["query"] = material_info["query"]
            parsed["material_chars_used"] = min(len(material_info["materials"]), args.max_material_chars)
            outputs.append(parsed)
            out.write(json.dumps(parsed, ensure_ascii=False) + "\n")
            out.flush()
            print(json.dumps({"case_id": spec["case_id"], "entities": len(parsed.get("entities", []))}, ensure_ascii=False))
    render_md(outputs, args.output_md)


if __name__ == "__main__":
    main()
