#!/usr/bin/env python3
"""Build a cross-property entity KG for code-table pipeline v3.

设计目标：
  v3 不再把码表建成“一个属性 label 拥有多个实体”，而是建成
  “一个实体拥有多个上位类型和多个独立属性”。

输入：
  --sources: JSONL。公开资料来源清单，字段包括 source_id/name/url/used_for。
  --types: JSONL。实体上位类型，字段包括 type_id/parent_type_id/source_refs。
  --properties: JSONL。独立属性轴，字段包括 property_id/value_type/values/source_refs。
  --entities: JSONL。实体断言，字段包括 entity_id/types/properties/source_refs。

输出：
  --kg-output: KG JSON，包含 source/type/property/value/entity 节点和关系边。
  --candidate-output: JSONL。根据“共享锚点 + 目标差异属性”挖出的 query 候选。
  --html-output: 自包含 KG 可视化 HTML。

候选挖掘逻辑：
  对每个目标属性值，找拥有该属性值的 positive entities。
  negative entities 必须：
    1. 没有目标属性值；
    2. 与 positive 共享至少一个非目标属性，或共享具体上位类型；
    3. 若自身也拥有目标属性值，则绝不作为 negative。
  这样 query 能围绕“同一主题/锚点下的属性边界”构造，而不是开放泛问。
"""

from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} is not valid JSONL: {exc}") from exc


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def node(nodes: Dict[str, Dict[str, Any]], node_id: str, label: str, kind: str, **attrs: Any) -> None:
    nodes.setdefault(node_id, {"id": node_id, "label": label, "kind": kind, **attrs})


def edge(edges: Dict[Tuple[str, str, str], Dict[str, Any]], source: str, target: str, relation: str, **attrs: Any) -> None:
    edges.setdefault((source, target, relation), {"source": source, "target": target, "relation": relation, **attrs})


def prop_keys(entity: Dict[str, Any]) -> Set[Tuple[str, str]]:
    keys = set()
    for prop in as_list(entity.get("properties")):
        if float(prop.get("confidence", 1.0) or 0.0) <= 0:
            continue
        pid = prop.get("property_id")
        vid = prop.get("value_id")
        if pid and vid:
            keys.add((str(pid), str(vid)))
    return keys


def concrete_types(entity: Dict[str, Any]) -> Set[str]:
    return {str(t) for t in as_list(entity.get("types")) if t and str(t) != "entity"}


def validate(
    sources: Dict[str, Dict[str, Any]],
    types: Dict[str, Dict[str, Any]],
    properties: Dict[str, Dict[str, Any]],
    entities: List[Dict[str, Any]],
) -> None:
    for row in types.values():
        parent = row.get("parent_type_id")
        if parent and parent not in types:
            raise ValueError(f"type {row['type_id']} references missing parent_type_id={parent}")
    value_index = {
        (pid, value["value_id"])
        for pid, prop in properties.items()
        for value in as_list(prop.get("values"))
        if value.get("value_id")
    }
    entity_ids = {row["entity_id"] for row in entities}
    for ent in entities:
        for type_id in as_list(ent.get("types")):
            if type_id not in types:
                raise ValueError(f"entity {ent['entity_id']} references missing type={type_id}")
        for prop in as_list(ent.get("properties")):
            if float(prop.get("confidence", 1.0) or 0.0) <= 0:
                continue
            pid = prop.get("property_id")
            vid = prop.get("value_id")
            if pid not in properties:
                raise ValueError(f"entity {ent['entity_id']} references missing property={pid}")
            value_type = properties[pid].get("value_type")
            if value_type == "enum" and vid and (pid, vid) not in value_index:
                raise ValueError(f"entity {ent['entity_id']} references missing value={pid}:{vid}")
            target = prop.get("target_entity_id")
            if target and target not in entity_ids:
                raise ValueError(f"entity {ent['entity_id']} property {pid}:{vid} references missing target_entity_id={target}")
    for container_name, rows in [("types", types), ("properties", properties)]:
        for row in rows.values():
            for ref in as_list(row.get("source_refs")):
                sid = ref.get("source_id")
                if sid and sid not in sources and not str(sid).startswith("private"):
                    raise ValueError(f"{container_name} {row} references missing source_id={sid}")


def build_kg(
    sources: Dict[str, Dict[str, Any]],
    types: Dict[str, Dict[str, Any]],
    properties: Dict[str, Dict[str, Any]],
    entities: List[Dict[str, Any]],
) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for sid, src in sources.items():
        node(nodes, f"source:{sid}", src.get("name", sid), "source", url=src.get("url"), used_for=src.get("used_for", []))

    for tid, typ in types.items():
        node(nodes, f"type:{tid}", typ.get("label", tid), "type", description=typ.get("description"), source_refs=typ.get("source_refs", []))
        if typ.get("parent_type_id"):
            edge(edges, f"type:{tid}", f"type:{typ['parent_type_id']}", "subtype_of")
        for ref in as_list(typ.get("source_refs")):
            if ref.get("source_id") in sources:
                edge(edges, f"type:{tid}", f"source:{ref['source_id']}", "grounded_by", source_term=ref.get("source_term"))

    for pid, prop in properties.items():
        node(nodes, f"property:{pid}", prop.get("label", pid), "property", value_type=prop.get("value_type"), description=prop.get("description"), source_refs=prop.get("source_refs", []))
        for ref in as_list(prop.get("source_refs")):
            if ref.get("source_id") in sources:
                edge(edges, f"property:{pid}", f"source:{ref['source_id']}", "grounded_by", source_term=ref.get("source_term"))
        for value in as_list(prop.get("values")):
            vid = value.get("value_id")
            node(nodes, f"value:{pid}:{vid}", value.get("label", vid), "value", property_id=pid)
            edge(edges, f"value:{pid}:{vid}", f"property:{pid}", "value_of")

    for ent in entities:
        eid = ent["entity_id"]
        node(nodes, f"entity:{eid}", ent.get("canonical_name", eid), "entity", aliases=ent.get("aliases", []), notes=ent.get("notes", ""), source_refs=ent.get("source_refs", []))
        for tid in as_list(ent.get("types")):
            edge(edges, f"entity:{eid}", f"type:{tid}", "has_type")
        for prop in as_list(ent.get("properties")):
            pid = prop.get("property_id")
            vid = prop.get("value_id")
            if vid:
                edge(edges, f"entity:{eid}", f"value:{pid}:{vid}", "has_property", confidence=prop.get("confidence"), evidence=prop.get("evidence"))
            else:
                edge(edges, f"entity:{eid}", f"property:{pid}", "has_property", confidence=prop.get("confidence"), value_text=prop.get("value_text"))
            if prop.get("target_entity_id"):
                edge(edges, f"entity:{eid}", f"entity:{prop['target_entity_id']}", "property_targets", property_id=pid, value_id=vid, confidence=prop.get("confidence"))

    kg = {
        "meta": {
            "source_count": len(sources),
            "type_count": len(types),
            "property_count": len(properties),
            "entity_count": len(entities),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "schema_version": "code_table_pipeline_v3_cross_property",
        },
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }
    return kg


def value_label(properties: Dict[str, Dict[str, Any]], pid: str, vid: str) -> str:
    for value in as_list(properties.get(pid, {}).get("values")):
        if value.get("value_id") == vid:
            return str(value.get("label", vid))
    return vid


def mine_candidates(
    types: Dict[str, Dict[str, Any]],
    properties: Dict[str, Dict[str, Any]],
    entities: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for ent in entities:
        for key in prop_keys(ent):
            by_key[key].append(ent)

    candidates: List[Dict[str, Any]] = []
    for target_key, positives in sorted(by_key.items()):
        target_pid, target_vid = target_key
        target_positive_ids = {p["entity_id"] for p in positives}
        target_owner_ids = {e["entity_id"] for e in entities if target_key in prop_keys(e)}
        for anchor_key, anchor_entities in sorted(by_key.items()):
            if anchor_key[0] == target_pid:
                continue
            anchored_positive = [e for e in anchor_entities if e["entity_id"] in target_positive_ids]
            anchored_negative = [e for e in anchor_entities if e["entity_id"] not in target_owner_ids]
            if len(anchored_positive) < 1 or len(anchored_negative) < 1:
                continue
            shared_types = sorted(set.intersection(*(concrete_types(e) for e in anchored_positive + anchored_negative))) if anchored_positive and anchored_negative else []
            risk = "low" if len(anchored_positive) >= 2 and len(anchored_negative) >= 2 else "medium"
            if not shared_types and len(anchored_negative) < 2:
                risk = "high"
            candidates.append(
                {
                    "candidate_id": f"{anchor_key[0]}:{anchor_key[1]}__{target_pid}:{target_vid}",
                    "anchor": {
                        "property_id": anchor_key[0],
                        "value_id": anchor_key[1],
                        "label": value_label(properties, anchor_key[0], anchor_key[1]),
                    },
                    "target": {
                        "property_id": target_pid,
                        "value_id": target_vid,
                        "label": value_label(properties, target_pid, target_vid),
                    },
                    "shared_types": [{"type_id": t, "label": types[t].get("label", t)} for t in shared_types if t in types],
                    "positive_entities": [
                        {"entity_id": e["entity_id"], "canonical_name": e.get("canonical_name"), "types": e.get("types", [])}
                        for e in anchored_positive
                    ],
                    "negative_entities": [
                        {"entity_id": e["entity_id"], "canonical_name": e.get("canonical_name"), "types": e.get("types", [])}
                        for e in anchored_negative
                    ],
                    "query_hint": f"围绕“{value_label(properties, anchor_key[0], anchor_key[1])}”锚点询问“{value_label(properties, target_pid, target_vid)}”边界",
                    "negative_rule_hint": "negative 只是不具备目标属性值，不代表业务上永远不能具备；rubric 应按材料显式归属判断。",
                    "risk": risk,
                }
            )

    candidates.sort(
        key=lambda c: (
            {"low": 0, "medium": 1, "high": 2}.get(c["risk"], 9),
            -len(c["positive_entities"]),
            -len(c["negative_entities"]),
            c["candidate_id"],
        )
    )
    return candidates


def mine_target_context_candidates(
    types: Dict[str, Dict[str, Any]],
    properties: Dict[str, Dict[str, Any]],
    entities: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Mine candidates where several entities point to the same target entity.

    This captures cases like:
      张雨忻 -- employee/works_for --> 豌豆荚
      阳光溪水 -- author/reports_on --> 豌豆荚
    The natural query anchor is not a generic property label, but the shared
    target entity "豌豆荚".
    """
    entity_by_id = {row["entity_id"]: row for row in entities}
    target_groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = defaultdict(list)
    for ent in entities:
        for prop in as_list(ent.get("properties")):
            if float(prop.get("confidence", 1.0) or 0.0) <= 0:
                continue
            target = prop.get("target_entity_id")
            if target:
                target_groups[str(target)].append((ent, prop))

    candidates: List[Dict[str, Any]] = []
    for target_id, assertions in sorted(target_groups.items()):
        if target_id not in entity_by_id:
            continue
        target_entity = entity_by_id[target_id]
        by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for ent, prop in assertions:
            if prop.get("property_id") and prop.get("value_id"):
                by_key[(str(prop["property_id"]), str(prop["value_id"]))].append(ent)
        if len(by_key) < 2:
            continue
        all_context_entity_ids = {ent["entity_id"] for ent, _ in assertions}
        for target_key, positives in sorted(by_key.items()):
            target_pid, target_vid = target_key
            positive_ids = {e["entity_id"] for e in positives}
            owners = {e["entity_id"] for e in positives}
            negatives = [
                entity_by_id[eid]
                for eid in sorted(all_context_entity_ids - owners)
                if entity_by_id[eid]["entity_id"] not in positive_ids
            ]
            if not positives or not negatives:
                continue
            shared_types = sorted(set.intersection(*(concrete_types(e) for e in positives + negatives)))
            candidates.append(
                {
                    "candidate_id": f"target_entity:{target_id}__{target_pid}:{target_vid}",
                    "anchor": {
                        "property_id": "target_entity",
                        "value_id": target_id,
                        "label": target_entity.get("canonical_name", target_id),
                    },
                    "target": {
                        "property_id": target_pid,
                        "value_id": target_vid,
                        "label": value_label(properties, target_pid, target_vid),
                    },
                    "shared_types": [{"type_id": t, "label": types[t].get("label", t)} for t in shared_types if t in types],
                    "positive_entities": [
                        {"entity_id": e["entity_id"], "canonical_name": e.get("canonical_name"), "types": e.get("types", [])}
                        for e in positives
                    ],
                    "negative_entities": [
                        {"entity_id": e["entity_id"], "canonical_name": e.get("canonical_name"), "types": e.get("types", [])}
                        for e in negatives
                    ],
                    "query_hint": f"围绕目标实体“{target_entity.get('canonical_name', target_id)}”询问“{value_label(properties, target_pid, target_vid)}”边界",
                    "negative_rule_hint": "negative 与 positive 指向同一目标实体，但不具备目标角色/关系；rubric 应按材料中显式关系判断。",
                    "risk": "medium" if len(positives) == 1 or len(negatives) == 1 else "low",
                }
            )
    return candidates


def render_html(kg: Dict[str, Any], candidates: List[Dict[str, Any]], path: Path) -> None:
    data = json.dumps({"kg": kg, "candidates": candidates[:80]}, ensure_ascii=False)
    text = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Code Table Pipeline V3 Cross-property KG</title>
<style>
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1f2937;background:#f6f7f9}}
header{{padding:16px 22px;background:#fff;border-bottom:1px solid #dde1e7}}
h1{{font-size:20px;margin:0 0 8px}}.stat{{display:inline-block;margin-right:16px;color:#56606f;font-size:13px}}
main{{display:grid;grid-template-columns:minmax(0,1fr) 420px;height:calc(100vh - 92px)}}
#graph{{background:#fbfcfd;width:100%;height:100%}}aside{{background:#fff;border-left:1px solid #dde1e7;overflow:auto;padding:14px}}
button{{border:1px solid #cdd3dd;background:#fff;border-radius:6px;padding:5px 8px;margin:0 6px 8px 0;cursor:pointer}}
pre{{white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid #e5e7eb;border-radius:6px;padding:8px;font-size:12px}}
.edge{{stroke:#ccd4df;stroke-width:1;opacity:.62}}.edge.property_targets{{stroke:#d97706;stroke-dasharray:4 3;opacity:.9}}
.node text{{font-size:11px;paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round}}
.cand{{border:1px solid #e1e5ec;border-radius:6px;padding:8px;margin:8px 0;background:#fbfcfe}}
.tag{{display:inline-block;background:#eef2f7;border-radius:4px;padding:1px 5px;margin:2px;font-size:12px}}
</style></head><body>
<header><h1>Code Table Pipeline V3: 实体-类型-独立属性 KG</h1>
<span class="stat">sources: {kg['meta']['source_count']}</span><span class="stat">types: {kg['meta']['type_count']}</span><span class="stat">properties: {kg['meta']['property_count']}</span><span class="stat">entities: {kg['meta']['entity_count']}</span><span class="stat">candidates: {len(candidates)}</span>
</header><main><svg id="graph"></svg><aside>
<button onclick="showCandidates()">边界候选</button><button onclick="showMeta()">KG Meta</button>
<div id="detail">点击节点查看详情，或查看边界候选。</div>
</aside></main>
<script>
const data={data};
const colors={{source:'#8b9bb4',type:'#5aa9d6',property:'#78b66e',value:'#b494d4',entity:'#edae61'}};
const svg=document.getElementById('graph');
function esc(s){{return String(s).replace(/[&<>]/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[ch]))}}
function showMeta(){{document.getElementById('detail').innerHTML='<pre>'+esc(JSON.stringify(data.kg.meta,null,2))+'</pre>';}}
function showCandidates(){{
  document.getElementById('detail').innerHTML=data.candidates.map(c=>`<div class="cand"><b>${{esc(c.query_hint)}}</b><br><span class="tag">anchor: ${{esc(c.anchor.property_id)}}=${{esc(c.anchor.label)}}</span><span class="tag">target: ${{esc(c.target.property_id)}}=${{esc(c.target.label)}}</span><span class="tag">risk: ${{esc(c.risk)}}</span><div>positive: ${{esc(c.positive_entities.map(e=>e.canonical_name).join('、'))}}</div><div>negative: ${{esc(c.negative_entities.map(e=>e.canonical_name).join('、'))}}</div></div>`).join('');
}}
function draw(){{
  const w=svg.clientWidth,h=svg.clientHeight; svg.setAttribute('viewBox',`0 0 ${{w}} ${{h}}`); svg.innerHTML='';
  const nodes=data.kg.nodes, edges=data.kg.edges, map=Object.fromEntries(nodes.map(n=>[n.id,n]));
  const groups={{source:[],type:[],property:[],value:[],entity:[]}}; nodes.forEach(n=>(groups[n.kind]||groups.entity).push(n));
  const layout=[['source',45],['type',w*.20],['property',w*.42],['value',w*.62],['entity',w*.80]];
  layout.forEach(([kind,x])=>{{groups[kind].forEach((n,i)=>{{n.x=x;n.y=36+i*Math.max(24,(h-72)/Math.max(1,groups[kind].length-1));}})}});
  const g=document.createElementNS('http://www.w3.org/2000/svg','g'); svg.appendChild(g);
  edges.forEach(e=>{{const s=map[e.source],t=map[e.target]; if(!s||!t)return; const line=document.createElementNS('http://www.w3.org/2000/svg','line'); line.setAttribute('x1',s.x);line.setAttribute('y1',s.y);line.setAttribute('x2',t.x);line.setAttribute('y2',t.y);line.setAttribute('class','edge '+e.relation); g.appendChild(line);}});
  nodes.forEach(n=>{{const ng=document.createElementNS('http://www.w3.org/2000/svg','g'); ng.classList.add('node'); ng.style.cursor='pointer'; ng.onclick=()=>document.getElementById('detail').innerHTML=`<h3>${{esc(n.label)}}</h3><pre>${{esc(JSON.stringify(n,null,2))}}</pre>`; const c=document.createElementNS('http://www.w3.org/2000/svg','circle'); c.setAttribute('cx',n.x);c.setAttribute('cy',n.y);c.setAttribute('r',n.kind==='entity'?8:6);c.setAttribute('fill',colors[n.kind]||'#aaa');c.setAttribute('stroke','#374151'); ng.appendChild(c); const tx=document.createElementNS('http://www.w3.org/2000/svg','text'); tx.setAttribute('x',n.x+9);tx.setAttribute('y',n.y+4);tx.textContent=n.label; ng.appendChild(tx); g.appendChild(ng);}});
}}
window.addEventListener('resize',draw); draw();
</script></body></html>"""
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument("--properties", type=Path, required=True)
    parser.add_argument("--entities", type=Path, required=True)
    parser.add_argument("--kg-output", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--html-output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = {row["source_id"]: row for row in iter_jsonl(args.sources)}
    types = {row["type_id"]: row for row in iter_jsonl(args.types)}
    properties = {row["property_id"]: row for row in iter_jsonl(args.properties)}
    entities = list(iter_jsonl(args.entities))
    validate(sources, types, properties, entities)
    kg = build_kg(sources, types, properties, entities)
    candidates = mine_candidates(types, properties, entities) + mine_target_context_candidates(types, properties, entities)
    candidates.sort(
        key=lambda c: (
            {"low": 0, "medium": 1, "high": 2}.get(c["risk"], 9),
            -len(c["positive_entities"]),
            -len(c["negative_entities"]),
            c["candidate_id"],
        )
    )
    args.kg_output.parent.mkdir(parents=True, exist_ok=True)
    args.candidate_output.parent.mkdir(parents=True, exist_ok=True)
    args.html_output.parent.mkdir(parents=True, exist_ok=True)
    args.kg_output.write_text(json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8")
    with args.candidate_output.open("w", encoding="utf-8") as f:
        for row in candidates:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    render_html(kg, candidates, args.html_output)
    print(json.dumps({"kg": kg["meta"], "candidate_count": len(candidates)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
