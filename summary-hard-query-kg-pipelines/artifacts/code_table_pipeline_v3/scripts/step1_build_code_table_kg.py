#!/usr/bin/env python3
"""Build the permanent ontology/code-table KG for pipeline v3.

这个脚本只构建“永久码表层”，不把训练材料里的实体写成永久节点。

输入：
  --sources: JSONL。公开资料来源清单。
  --types: JSONL。实体上位类型。
  --properties: JSONL。独立属性轴。

输出：
  --kg-output: 只包含 source/type/property/value 的 KG JSON。
  --html-output: 自包含 HTML 可视化。

设计约束：
  训练材料中的实体应该在 query 构造时临时抽取成 instance graph。
  instance graph 只引用这里的 type/property/value，不反向污染永久码表。
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def add_node(nodes: Dict[str, Dict[str, Any]], node_id: str, label: str, kind: str, **attrs: Any) -> None:
    nodes.setdefault(node_id, {"id": node_id, "label": label, "kind": kind, **attrs})


def add_edge(edges: Dict[Tuple[str, str, str], Dict[str, Any]], source: str, target: str, relation: str, **attrs: Any) -> None:
    edges.setdefault((source, target, relation), {"source": source, "target": target, "relation": relation, **attrs})


def validate(
    sources: Dict[str, Dict[str, Any]],
    types: Dict[str, Dict[str, Any]],
    properties: Dict[str, Dict[str, Any]],
) -> None:
    for row in types.values():
        parent = row.get("parent_type_id")
        if parent and parent not in types:
            raise ValueError(f"type {row['type_id']} references missing parent_type_id={parent}")
    for row in list(types.values()) + list(properties.values()):
        for ref in as_list(row.get("source_refs")):
            source_id = ref.get("source_id")
            if source_id and source_id not in sources and not str(source_id).startswith("private"):
                raise ValueError(f"missing source_id={source_id} in {row}")


def build_kg(
    sources: Dict[str, Dict[str, Any]],
    types: Dict[str, Dict[str, Any]],
    properties: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for sid, source in sources.items():
        add_node(
            nodes,
            f"source:{sid}",
            source.get("name", sid),
            "source",
            url=source.get("url"),
            source_kind=source.get("kind"),
            used_for=source.get("used_for", []),
            local_status=source.get("local_status"),
        )

    for type_id, typ in types.items():
        add_node(
            nodes,
            f"type:{type_id}",
            typ.get("label", type_id),
            "type",
            parent_type_id=typ.get("parent_type_id"),
            description=typ.get("description"),
            source_refs=typ.get("source_refs", []),
        )
        if typ.get("parent_type_id"):
            add_edge(edges, f"type:{type_id}", f"type:{typ['parent_type_id']}", "subtype_of")
        for ref in as_list(typ.get("source_refs")):
            if ref.get("source_id") in sources:
                add_edge(edges, f"type:{type_id}", f"source:{ref['source_id']}", "grounded_by", source_term=ref.get("source_term"))

    for property_id, prop in properties.items():
        add_node(
            nodes,
            f"property:{property_id}",
            prop.get("label", property_id),
            "property",
            value_type=prop.get("value_type"),
            is_cross_cutting=prop.get("is_cross_cutting"),
            description=prop.get("description"),
            source_refs=prop.get("source_refs", []),
        )
        for ref in as_list(prop.get("source_refs")):
            if ref.get("source_id") in sources:
                add_edge(edges, f"property:{property_id}", f"source:{ref['source_id']}", "grounded_by", source_term=ref.get("source_term"))
        for value in as_list(prop.get("values")):
            value_id = value.get("value_id")
            add_node(nodes, f"value:{property_id}:{value_id}", value.get("label", value_id), "value", property_id=property_id)
            add_edge(edges, f"value:{property_id}:{value_id}", f"property:{property_id}", "value_of")

    return {
        "meta": {
            "schema_version": "code_table_pipeline_v3_permanent_code_table",
            "source_count": len(sources),
            "type_count": len(types),
            "property_count": len(properties),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "entity_count": 0,
            "notes": "Permanent KG contains no material entities. Entities are ephemeral instance nodes during query synthesis.",
        },
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }


def render_html(kg: Dict[str, Any], path: Path) -> None:
    data = json.dumps(kg, ensure_ascii=False)
    text = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Pipeline V3 Permanent Code Table KG</title>
<style>
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1f2937;background:#f7f8fa}}
header{{padding:16px 22px;background:#fff;border-bottom:1px solid #dde1e7}}
h1{{font-size:20px;margin:0 0 8px}}.stat{{display:inline-block;margin-right:16px;color:#56606f;font-size:13px}}
main{{display:grid;grid-template-columns:minmax(0,1fr) 420px;height:calc(100vh - 92px)}}
#graph{{background:#fbfcfd;width:100%;height:100%}}aside{{background:#fff;border-left:1px solid #dde1e7;overflow:auto;padding:14px}}
pre{{white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid #e5e7eb;border-radius:6px;padding:8px;font-size:12px}}
.edge{{stroke:#cbd5e1;stroke-width:1;opacity:.7}}.node text{{font-size:11px;paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round}}
</style></head><body>
<header><h1>Pipeline V3 永久码表 KG</h1>
<span class="stat">sources: {kg['meta']['source_count']}</span><span class="stat">types: {kg['meta']['type_count']}</span><span class="stat">properties: {kg['meta']['property_count']}</span><span class="stat">entities: 0</span>
<div class="stat">实体不进入永久 KG，只在单条材料处理时作为临时 instance graph。</div></header>
<main><svg id="graph"></svg><aside><h3>节点详情</h3><div id="detail">点击节点查看详情。</div></aside></main>
<script>
const data={data};
const svg=document.getElementById('graph');
const colors={{source:'#8b9bb4',type:'#5aa9d6',property:'#78b66e',value:'#b494d4'}};
function esc(s){{return String(s).replace(/[&<>]/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[ch]))}}
function draw(){{
  const w=svg.clientWidth,h=svg.clientHeight; svg.setAttribute('viewBox',`0 0 ${{w}} ${{h}}`); svg.innerHTML='';
  const nodes=data.nodes, edges=data.edges, map=Object.fromEntries(nodes.map(n=>[n.id,n]));
  const groups={{source:[],type:[],property:[],value:[]}}; nodes.forEach(n=>(groups[n.kind]||groups.value).push(n));
  [['source',60],['type',w*.30],['property',w*.55],['value',w*.78]].forEach(([kind,x])=>{{groups[kind].forEach((n,i)=>{{n.x=x;n.y=38+i*Math.max(25,(h-76)/Math.max(1,groups[kind].length-1));}})}});
  const g=document.createElementNS('http://www.w3.org/2000/svg','g'); svg.appendChild(g);
  edges.forEach(e=>{{const s=map[e.source],t=map[e.target]; if(!s||!t)return; const line=document.createElementNS('http://www.w3.org/2000/svg','line'); line.setAttribute('x1',s.x);line.setAttribute('y1',s.y);line.setAttribute('x2',t.x);line.setAttribute('y2',t.y);line.setAttribute('class','edge'); g.appendChild(line);}});
  nodes.forEach(n=>{{const ng=document.createElementNS('http://www.w3.org/2000/svg','g'); ng.style.cursor='pointer'; ng.onclick=()=>document.getElementById('detail').innerHTML=`<h4>${{esc(n.label)}}</h4><pre>${{esc(JSON.stringify(n,null,2))}}</pre>`; const c=document.createElementNS('http://www.w3.org/2000/svg','circle'); c.setAttribute('cx',n.x);c.setAttribute('cy',n.y);c.setAttribute('r',7);c.setAttribute('fill',colors[n.kind]||'#aaa');c.setAttribute('stroke','#374151'); ng.appendChild(c); const tx=document.createElementNS('http://www.w3.org/2000/svg','text'); tx.setAttribute('x',n.x+9);tx.setAttribute('y',n.y+4);tx.textContent=n.label; ng.appendChild(tx); g.appendChild(ng);}});
}}
window.addEventListener('resize',draw); draw();
</script></body></html>"""
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument("--properties", type=Path, required=True)
    parser.add_argument("--kg-output", type=Path, required=True)
    parser.add_argument("--html-output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = {row["source_id"]: row for row in iter_jsonl(args.sources)}
    types = {row["type_id"]: row for row in iter_jsonl(args.types)}
    properties = {row["property_id"]: row for row in iter_jsonl(args.properties)}
    validate(sources, types, properties)
    kg = build_kg(sources, types, properties)
    args.kg_output.parent.mkdir(parents=True, exist_ok=True)
    args.html_output.parent.mkdir(parents=True, exist_ok=True)
    args.kg_output.write_text(json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(kg, args.html_output)
    print(json.dumps(kg["meta"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
