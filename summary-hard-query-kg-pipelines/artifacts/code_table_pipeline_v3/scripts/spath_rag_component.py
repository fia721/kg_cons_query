#!/usr/bin/env python3
"""S-Path-RAG style semantic path selector.

This is a local component inspired by S-Path-RAG's practical retrieval shape:

  candidate path generation -> semantic path weighting -> beam/k-shortest
  search -> lightweight verifier-friendly explanations.

It is not copied from an official S-Path-RAG repository. No official code was
found during the 2026-06-30 search; this module implements the component shape
needed by the current pipeline using the existing local KG/overlay artifacts.

Supported inputs:
  1. permanent KG JSON with {nodes, edges}
  2. domain overlay JSONL produced by step0_build_domain_overlays_from_open_kb.py

Main output:
  ranked paths with per-edge cost, generic-anchor penalties, semantic scores,
  and rejection reasons.
"""

from __future__ import annotations

import heapq
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


GENERIC_ANCHOR_TERMS = {
    "实体",
    "对象",
    "物体",
    "东西",
    "thing",
    "entity",
    "object",
    "模棱两可",
    "ambiguous",
    "地理对象",
    "geographic object",
    "建筑物",
    "building",
    "place",
    "地点",
    "organization",
    "organisation",
}

GOOD_RELATION_BONUS = {
    "has_value": -1.25,
    "sibling_value": -1.15,
    "value_of_inverse": -1.0,
    "value_of": -0.4,
    "contrast_with": -0.9,
    "subunit_of": -0.6,
    "industry": -0.35,
    "facet_of": -0.2,
    "subclass_of": 0.0,
    "instance_of": 0.1,
    "part_of": 0.15,
    "grounded_by": 0.35,
}

RELATION_BASE_COST = {
    "has_value": 0.65,
    "sibling_value": 0.7,
    "value_of_inverse": 0.65,
    "value_of": 0.8,
    "contrast_with": 0.8,
    "subunit_of": 0.9,
    "industry": 1.15,
    "facet_of": 1.25,
    "subclass_of": 1.25,
    "instance_of": 1.3,
    "part_of": 1.35,
    "grounded_by": 1.55,
}


def norm_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def compact(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def text_tokens(text: Any) -> set[str]:
    s = str(text or "").lower()
    tokens = {t for t in re.split(r"[^a-z0-9]+", s) if len(t) >= 2}
    zh = re.findall(r"[\u4e00-\u9fff]{2,}", str(text or ""))
    for chunk in zh:
        tokens.add(chunk)
        for i in range(len(chunk) - 1):
            tokens.add(chunk[i : i + 2])
    return tokens


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def contains_any(text: str, terms: Iterable[str]) -> bool:
    c = compact(text)
    return any(compact(term) and compact(term) in c for term in terms)


@dataclass
class Node:
    id: str
    label: str = ""
    kind: str = ""
    description: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join([self.id, self.label, self.kind, self.description])


@dataclass
class Edge:
    source: str
    target: str
    relation: str
    label: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join([self.relation, self.label, json.dumps(self.meta, ensure_ascii=False)[:500]])


@dataclass
class RankedPath:
    nodes: List[str]
    edges: List[Edge]
    score: float
    semantic_score: float
    generic_penalty: float
    rejected: bool
    reject_reasons: List[str]


class SemanticPathGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Node] = {}
        self.adj: Dict[str, List[Edge]] = {}

    def add_node(self, node_id: str, label: str = "", kind: str = "", description: str = "", **meta: Any) -> None:
        old = self.nodes.get(node_id)
        if old:
            old.label = old.label or label
            old.kind = old.kind or kind
            old.description = old.description or description
            old.meta.update({k: v for k, v in meta.items() if v not in (None, "", [], {})})
        else:
            self.nodes[node_id] = Node(node_id, label, kind, description, meta)
        self.adj.setdefault(node_id, [])

    def add_edge(self, source: str, target: str, relation: str, label: str = "", **meta: Any) -> None:
        if source not in self.nodes:
            self.add_node(source)
        if target not in self.nodes:
            self.add_node(target)
        self.adj.setdefault(source, []).append(Edge(source, target, relation, label, meta))

    def add_bidirectional_edge(self, source: str, target: str, relation: str, label: str = "", **meta: Any) -> None:
        self.add_edge(source, target, relation, label, **meta)
        self.add_edge(target, source, f"{relation}_inverse", label, **meta)

    def search_nodes(self, term: str, limit: int = 10) -> List[Tuple[float, Node]]:
        q_tokens = text_tokens(term)
        q_compact = compact(term)
        scored: List[Tuple[float, Node]] = []
        for node in self.nodes.values():
            n_text = node.text
            score = jaccard(q_tokens, text_tokens(n_text))
            matched = score > 0
            if q_compact and q_compact in compact(n_text):
                score += 1.0
                matched = True
            if q_compact and q_compact == compact(node.label):
                score += 2.0
                matched = True
            if node.kind == "concept":
                score += 0.15
            elif node.kind == "wikidata_anchor":
                score -= 0.08
            if matched and score > 0:
                scored.append((score, node))
        return sorted(scored, key=lambda x: (-x[0], x[1].id))[:limit]

    def edge_cost(self, edge: Edge, query_context: str = "") -> Tuple[float, float, float, List[str]]:
        relation = edge.relation.replace("_inverse", "")
        src = self.nodes.get(edge.source, Node(edge.source))
        dst = self.nodes.get(edge.target, Node(edge.target))
        base = RELATION_BASE_COST.get(relation, 1.6)
        base += GOOD_RELATION_BONUS.get(relation, 0.0)

        context_tokens = text_tokens(query_context)
        sem = max(
            jaccard(context_tokens, text_tokens(src.text)),
            jaccard(context_tokens, text_tokens(dst.text)),
            jaccard(context_tokens, text_tokens(edge.text)),
        )
        semantic_discount = min(0.8, sem * 2.5)

        generic_penalty = 0.0
        reasons: List[str] = []
        target_text = dst.text
        if dst.kind in {"wikidata_anchor", "wikidata_entity", "domain"} and contains_any(target_text, GENERIC_ANCHOR_TERMS):
            generic_penalty += 2.5
            reasons.append(f"generic_anchor:{dst.label or dst.id}")
        if relation in {"grounded_by"}:
            generic_penalty += 0.4

        cost = max(0.05, base - semantic_discount + generic_penalty)
        return cost, sem, generic_penalty, reasons

    def beam_paths(
        self,
        source_id: str,
        query_context: str = "",
        target_ids: Optional[set[str]] = None,
        max_hops: int = 4,
        beam_width: int = 30,
        top_k: int = 10,
    ) -> List[RankedPath]:
        heap: List[Tuple[float, int, List[str], List[Edge], float, float, List[str]]] = []
        counter = 0
        heapq.heappush(heap, (0.0, counter, [source_id], [], 0.0, 0.0, []))
        complete: List[RankedPath] = []

        while heap and len(complete) < top_k * 8:
            score, _, nodes, edges, sem_sum, generic_sum, reasons = heapq.heappop(heap)
            current = nodes[-1]
            if edges and (target_ids is None or current in target_ids):
                rejected = bool(reasons)
                complete.append(
                    RankedPath(
                        nodes=nodes,
                        edges=edges,
                        score=score,
                        semantic_score=sem_sum / max(1, len(edges)),
                        generic_penalty=generic_sum,
                        rejected=rejected,
                        reject_reasons=sorted(set(reasons)),
                    )
                )
            if len(edges) >= max_hops:
                continue

            next_edges = self.adj.get(current, [])
            candidates = []
            for edge in next_edges:
                if edge.target in nodes:
                    continue
                cost, sem, generic, edge_reasons = self.edge_cost(edge, query_context)
                candidates.append((cost, sem, generic, edge, edge_reasons))
            candidates.sort(key=lambda x: x[0])
            for cost, sem, generic, edge, edge_reasons in candidates[:beam_width]:
                counter += 1
                heapq.heappush(
                    heap,
                    (
                        score + cost,
                        counter,
                        nodes + [edge.target],
                        edges + [edge],
                        sem_sum + sem,
                        generic_sum + generic,
                        reasons + edge_reasons,
                    ),
                )

        complete.sort(key=lambda p: (p.rejected, p.score, -p.semantic_score))
        return complete[:top_k]

    def path_to_dict(self, path: RankedPath) -> Dict[str, Any]:
        return {
            "score": round(path.score, 4),
            "semantic_score": round(path.semantic_score, 4),
            "generic_penalty": round(path.generic_penalty, 4),
            "rejected": path.rejected,
            "reject_reasons": path.reject_reasons,
            "nodes": [
                {
                    "id": node_id,
                    "label": self.nodes.get(node_id, Node(node_id)).label,
                    "kind": self.nodes.get(node_id, Node(node_id)).kind,
                    "description": self.nodes.get(node_id, Node(node_id)).description,
                }
                for node_id in path.nodes
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "relation": edge.relation,
                    "label": edge.label,
                    "meta": edge.meta,
                }
                for edge in path.edges
            ],
        }


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def add_property_value_nodes(g: SemanticPathGraph, concept: Dict[str, Any]) -> None:
    concept_id = concept.get("concept_id", "")
    for item in concept.get("maps_to", []):
        prop = item.get("property_id", "")
        value = item.get("value_id", "")
        if not prop or not value:
            continue
        prop_id = f"property:{prop}"
        value_id = f"value:{prop}:{value}"
        g.add_node(prop_id, label=prop, kind="property_axis")
        g.add_node(value_id, label=f"{prop}={value}", kind="property_value")
        g.add_bidirectional_edge(concept_id, value_id, "has_value", property_id=prop, value_id=value)
        g.add_bidirectional_edge(value_id, prop_id, "value_of", property_id=prop)


def load_overlay_graph(path: Path) -> SemanticPathGraph:
    g = SemanticPathGraph()
    property_to_values: Dict[str, set[str]] = {}
    value_to_concepts: Dict[str, set[str]] = {}

    for domain in iter_jsonl(path):
        domain_id = domain.get("domain_id", "")
        g.add_node(f"domain:{domain_id}", label=domain.get("label", domain_id), kind="domain")
        for rel in domain.get("relations", []):
            src = rel.get("source", "")
            tgt = rel.get("target", "")
            if src and tgt:
                g.add_bidirectional_edge(src, tgt, rel.get("relation", "related_to"), note=rel.get("note", ""))
        for concept in domain.get("concepts", []):
            cid = concept.get("concept_id", "")
            if not cid:
                continue
            g.add_node(cid, label=concept.get("label", cid), kind="concept", description=concept.get("description", ""))
            g.add_bidirectional_edge(cid, f"domain:{domain_id}", "in_domain")
            add_property_value_nodes(g, concept)
            for item in concept.get("maps_to", []):
                prop = item.get("property_id", "")
                value = item.get("value_id", "")
                if prop and value:
                    value_node = f"value:{prop}:{value}"
                    property_to_values.setdefault(prop, set()).add(value_node)
                    value_to_concepts.setdefault(value_node, set()).add(cid)
            for ref in concept.get("open_kb_refs", []):
                if ref.get("source_id") != "wikidata" or not ref.get("entity_id"):
                    continue
                rid = f"wikidata:{ref['entity_id']}"
                g.add_node(rid, label=ref.get("label", ""), kind="wikidata_entity", description=ref.get("description", "") or "")
                g.add_bidirectional_edge(cid, rid, "grounded_by", matched_search_term=ref.get("matched_search_term", ""))
                for walk in ref.get("online_kg_walks", []):
                    target_id = walk.get("target_id", "")
                    if not target_id:
                        continue
                    wid = f"wikidata:{target_id}"
                    g.add_node(wid, label=walk.get("target_label", ""), kind="wikidata_anchor", description=walk.get("target_description", "") or "")
                    g.add_bidirectional_edge(
                        rid,
                        wid,
                        walk.get("relation", walk.get("property_id", "wikidata_relation")),
                        property_id=walk.get("property_id", ""),
                        target_url=walk.get("target_url", ""),
                    )

    for prop, values in property_to_values.items():
        values_sorted = sorted(values)
        for i, left in enumerate(values_sorted):
            for right in values_sorted[i + 1 :]:
                g.add_bidirectional_edge(left, right, "sibling_value", property_id=prop)
        for value_node, concepts in value_to_concepts.items():
            if value_node not in values:
                continue
            for concept_id in concepts:
                g.add_bidirectional_edge(value_node, concept_id, "value_of_inverse", property_id=prop)
    return g


def load_kg_json_graph(path: Path) -> SemanticPathGraph:
    g = SemanticPathGraph()
    data = json.loads(path.read_text(encoding="utf-8"))
    for node in data.get("nodes", []):
        g.add_node(
            node.get("id", ""),
            label=node.get("label", ""),
            kind=node.get("kind", ""),
            description=node.get("description", ""),
            **{k: v for k, v in node.items() if k not in {"id", "label", "kind", "description"}},
        )
    for edge in data.get("edges", []):
        g.add_bidirectional_edge(
            edge.get("source", ""),
            edge.get("target", ""),
            edge.get("relation", "related_to"),
            **{k: v for k, v in edge.items() if k not in {"source", "target", "relation"}},
        )
    return g


def load_graph(path: Path) -> SemanticPathGraph:
    if path.suffix == ".jsonl":
        return load_overlay_graph(path)
    return load_kg_json_graph(path)


def ranked_paths_for_terms(
    graph: SemanticPathGraph,
    source_term: str,
    target_term: str = "",
    query_context: str = "",
    max_hops: int = 4,
    top_k: int = 10,
) -> Dict[str, Any]:
    source_matches = graph.search_nodes(source_term, limit=5)
    raw_target_matches = graph.search_nodes(target_term, limit=12) if target_term else []
    target_matches = raw_target_matches
    if raw_target_matches:
        target_compact = compact(target_term)
        best = raw_target_matches[0][0]
        exact_or_strong = [
            (score, node)
            for score, node in raw_target_matches
            if target_compact == compact(node.label)
            or target_compact == compact(node.id)
            or (target_compact and target_compact in compact(node.label) and score >= max(0.8, best * 0.65))
            or (node.kind == "concept" and score >= best * 0.82)
        ]
        target_matches = exact_or_strong or raw_target_matches[:1]
    target_ids = {node.id for _, node in target_matches} if target_matches else None
    source_node = source_matches[0][1] if source_matches else None
    if not source_node:
        return {
            "source_term": source_term,
            "target_term": target_term,
            "error": "source_not_found",
            "source_matches": [],
            "target_matches": [],
            "paths": [],
        }
    paths = graph.beam_paths(
        source_node.id,
        query_context=query_context or " ".join([source_term, target_term]),
        target_ids=target_ids,
        max_hops=max_hops,
        top_k=top_k,
    )
    return {
        "source_term": source_term,
        "target_term": target_term,
        "source_matches": [{"score": round(score, 4), "id": node.id, "label": node.label, "kind": node.kind} for score, node in source_matches],
        "target_matches": [{"score": round(score, 4), "id": node.id, "label": node.label, "kind": node.kind} for score, node in target_matches],
        "paths": [graph.path_to_dict(path) for path in paths],
    }
