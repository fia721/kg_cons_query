#!/usr/bin/env python3
"""Build a v3 HTML report for synthesized queries and optional rollout results.

输入：
  --queries-jsonl: step3 输出的 v3 query/rubric JSONL。
  --eval-csv: step4 输出的测评集 CSV，可选。
  --result-csv: 测评/rollout 结果 CSV，可选；脚本会尽量识别 dataID/query/output/score/reason/context 字段。

输出：
  --output-html: 中文 HTML 报告。

报告内容：
  1. 原始 query / 重构 query。
  2. 基础 KG 中的一/二级属性、positive/negative 边界。
  3. 专用领域 KG 的引用和游走 domain_walk。
  4. 机评引导。
  5. 如果提供 rollout 结果，展示每条 query 的采样答案、分数、理由、召回文本摘录。
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_csv(path: Path | None) -> List[Dict[str, str]]:
    if not path or not path.exists():
        return []
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            break
        except OverflowError:
            limit //= 10
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def base_case_id(data_id: str) -> str:
    return str(data_id or "").split("__sample", 1)[0]


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def html_list(items: Any, empty: str = "无") -> str:
    values = [str(x) for x in as_list(items) if str(x).strip()]
    if not values:
        return f"<p class='muted'>{esc(empty)}</p>"
    return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in values) + "</ul>"


def prop_label(prop: Dict[str, Any]) -> str:
    if not prop:
        return ""
    label = prop.get("label") or prop.get("value_id") or prop.get("type_id") or ""
    prop_id = prop.get("property_id")
    value_id = prop.get("value_id")
    if prop_id and value_id:
        return f"{label} ({prop_id}={value_id})"
    if prop.get("type_id"):
        return f"{label} (type={prop.get('type_id')})"
    return str(label)


def prop_hit_terms(prop: Dict[str, Any]) -> List[str]:
    if not prop:
        return []
    terms = [prop_label(prop)]
    for key in ["label", "value_id", "type_id"]:
        value = str(prop.get(key) or "").strip()
        if value:
            terms.append(value)
    return list(dict.fromkeys(terms))


def badge(text: str, cls: str = "") -> str:
    return f"<span class='badge {cls}'>{esc(text)}</span>"


def clip(text: Any, limit: int = 1200) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit // 2] + "\n...[中间省略]...\n" + value[-limit // 2 :]


def find_first(row: Dict[str, str], names: List[str]) -> str:
    lower = {k.lower(): k for k in row}
    for name in names:
        if name in row and row[name]:
            return row[name]
        key = lower.get(name.lower())
        if key and row.get(key):
            return row[key]
    return ""


def score_value(row: Dict[str, str]) -> str:
    return find_first(row, ["score", "Score", "FinalScore", "llm_score", "可信分", "评分", "result_score"])


def output_value(row: Dict[str, str]) -> str:
    return find_first(row, ["output", "answer", "答案", "模型答案", "response", "final_answer", "模型输出"])


def reason_value(row: Dict[str, str]) -> str:
    return find_first(row, ["llm_reason", "reason", "评测理由", "judge_reason", "llm_res"])


def retrieval_value(row: Dict[str, str]) -> str:
    combined_passages = "\n\n".join(
        row.get(name, "").strip()
        for name in ["passage1", "passage2", "passage3", "passage4", "passage5"]
        if row.get(name, "").strip()
    )
    if combined_passages:
        return combined_passages
    return find_first(row, ["context", "contexts", "retrieval_text", "召回材料", "检索结果", "materials"])


def detect_hits(text: str, terms: List[str]) -> List[str]:
    hits = []
    for term in terms:
        term = str(term or "").strip()
        if not term:
            continue
        candidates = [term]
        candidates.extend([x.strip() for x in re.split(r"[；;、,/，]", term) if len(x.strip()) >= 2])
        if any(c and c in text for c in candidates):
            hits.append(term)
    return hits[:20]


def is_exclusion_context(text: str, start: int, end: int) -> bool:
    left = text[max(0, start - 28) : start]
    right = text[end : min(len(text), end + 28)]
    window = left + text[start:end] + right
    exclusion_markers = [
        "不支持",
        "不包含",
        "不包括",
        "不得",
        "不能",
        "不可",
        "无法",
        "不应",
        "不是",
        "排除",
        "仅在否定",
        "仅在排除",
        "不适用",
        "不接受",
        "不得混入",
        "不得把",
        "不要把",
    ]
    if any(marker in window for marker in exclusion_markers):
        return True
    if re.search(r"(仅支持|只支持|只统计|仅统计).{0,24}(不|非|除外)", window):
        return True
    return False


def detect_asserted_hits(text: str, terms: List[str]) -> List[str]:
    hits = []
    for term in terms:
        term = str(term or "").strip()
        if not term:
            continue
        candidates = [term]
        candidates.extend([x.strip() for x in re.split(r"[；;、,/，]", term) if len(x.strip()) >= 2])
        asserted = False
        for candidate in candidates:
            if not candidate:
                continue
            start = text.find(candidate)
            while start >= 0:
                end = start + len(candidate)
                if not is_exclusion_context(text, start, end):
                    asserted = True
                    break
                start = text.find(candidate, end)
            if asserted:
                break
        if asserted:
            hits.append(term)
    return hits[:20]


def score_number(row: Dict[str, str]) -> float | None:
    value = score_value(row).strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        return float(match.group(0)) if match else None


def boundary_terms(row: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    boundary = row.get("boundary") or {}
    guidance = row.get("machine_eval_guidance") or {}
    positive_terms = [str(x) for x in as_list(boundary.get("positive_entities"))]
    positive_terms += prop_hit_terms(boundary.get("target_property") or {})
    positive_terms += [str(x) for x in as_list(guidance.get("positive_rubric"))]
    negative_terms = [str(x) for x in as_list(boundary.get("negative_entities"))]
    for prop in as_list(boundary.get("negative_properties")):
        negative_terms += prop_hit_terms(prop)
    negative_terms += [str(x) for x in as_list(guidance.get("negative_rubric"))]
    negative_terms += [str(x) for x in as_list(guidance.get("zero_score_conditions"))]
    return positive_terms, negative_terms


def answer_boundary_terms(row: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Terms used to detect what the model answer itself asserted.

    Full negative rubric sentences often contain exclusion wording such as
    "不得把 X 混入". If those sentences are used as answer-hit terms, a correct
    answer that says "不包括 X" is falsely counted as a negative hit. For answer
    boundary detection, prefer concrete entities/properties.
    """

    boundary = row.get("boundary") or {}
    guidance = row.get("machine_eval_guidance") or {}
    positive_terms = [str(x) for x in as_list(boundary.get("positive_entities"))]
    positive_terms += prop_hit_terms(boundary.get("target_property") or {})
    positive_terms += [str(x) for x in as_list(guidance.get("positive_rubric"))]
    negative_terms = [str(x) for x in as_list(boundary.get("negative_entities"))]
    for prop in as_list(boundary.get("negative_properties")):
        negative_terms += prop_hit_terms(prop)
    return positive_terms, negative_terms


def run_profile(run: Dict[str, str], positive_terms: List[str], negative_terms: List[str]) -> Dict[str, Any]:
    answer = output_value(run)
    reason = reason_value(run)
    retrieval = retrieval_value(run)
    return {
        "run": run,
        "score": score_number(run),
        "answer": answer,
        "reason": reason,
        "retrieval": retrieval,
        "ret_pos": detect_hits(retrieval, positive_terms),
        "ret_neg": detect_hits(retrieval, negative_terms),
        "ans_pos": detect_hits(answer, positive_terms),
        "ans_neg": detect_hits(answer, negative_terms),
    }


def good_case_pair(row: Dict[str, Any], result_rows: List[Dict[str, str]]) -> Dict[str, Any] | None:
    if not result_rows:
        return None
    retrieval_positive_terms, retrieval_negative_terms = boundary_terms(row)
    answer_positive_terms, answer_negative_terms = answer_boundary_terms(row)
    profiles = []
    for run in result_rows:
        profile = run_profile(run, retrieval_positive_terms, retrieval_negative_terms)
        profile["ans_pos"] = detect_hits(profile["answer"], answer_positive_terms)
        profile["ans_neg"] = detect_asserted_hits(profile["answer"], answer_negative_terms)
        profiles.append(profile)
    ret_pos = sorted({hit for profile in profiles for hit in profile["ret_pos"]})
    ret_neg = sorted({hit for profile in profiles for hit in profile["ret_neg"]})
    if not ret_pos or not ret_neg:
        return None

    def high_score(profile: Dict[str, Any]) -> bool:
        score = profile["score"]
        return score is not None and score >= 2

    def low_score(profile: Dict[str, Any]) -> bool:
        score = profile["score"]
        return score is not None and score <= 0

    boundary_profiles = [p for p in profiles if p["ret_pos"] and p["ret_neg"]]
    pure_good = [p for p in boundary_profiles if high_score(p) and p["ans_pos"] and not p["ans_neg"]]
    mixed_good = [p for p in boundary_profiles if high_score(p) and p["ans_pos"]]
    good = (pure_good or mixed_good)
    bad = [p for p in boundary_profiles if low_score(p) and p["ans_pos"] and p["ans_neg"]]
    if not good or not bad:
        return None
    good_profile = max(good, key=lambda p: (not p["ans_neg"], len(p["ans_pos"]), p["score"] or -999))
    bad_profile = max(bad, key=lambda p: (len(p["ans_neg"]), len(p["ans_pos"]), -(p["score"] or 0)))
    tier = "强边界 case" if pure_good else "可用边界 case"
    return {
        "tier": tier,
        "good": good_profile,
        "bad": bad_profile,
        "ret_pos": ret_pos,
        "ret_neg": ret_neg,
        "profiles": profiles,
    }


def render_case(row: Dict[str, Any], eval_rows: List[Dict[str, str]], result_rows: List[Dict[str, str]]) -> str:
    boundary = row.get("boundary") or {}
    guidance = row.get("machine_eval_guidance") or {}
    domain_walk = boundary.get("domain_walk") or {}
    positive_terms, negative_terms = boundary_terms(row)
    answer_positive_terms, answer_negative_terms = answer_boundary_terms(row)

    anchors = [prop_label(x) for x in as_list(boundary.get("anchor_properties"))]
    negatives = [prop_label(x) for x in as_list(boundary.get("negative_properties"))]
    target = prop_label(boundary.get("target_property") or {})
    upper = boundary.get("upper_type") or {}

    rollout_html = ""
    if result_rows:
        blocks = []
        for idx, run in enumerate(result_rows, 1):
            answer = output_value(run)
            retrieval = retrieval_value(run)
            reason = reason_value(run)
            ans_pos = detect_hits(answer, answer_positive_terms)
            raw_ans_neg = detect_hits(answer, answer_negative_terms)
            ans_neg = detect_asserted_hits(answer, answer_negative_terms)
            ret_pos = detect_hits(retrieval, positive_terms)
            ret_neg = detect_hits(retrieval, negative_terms)
            blocks.append(
                "<details class='rollout'><summary>"
                + f"采样 {idx} | dataID={esc(run.get('dataID') or run.get('data_id') or '')} | score={esc(score_value(run))} "
                + f"| 召回正例命中 {len(ret_pos)} | 召回负例命中 {len(ret_neg)} | 答案正例命中 {len(ans_pos)} | 答案断言式负例命中 {len(ans_neg)}"
                + "</summary>"
                + "<div class='hitrow'><b>召回材料 positive 命中：</b>" + html_list(ret_pos, "未检测到") + "</div>"
                + "<div class='hitrow'><b>召回材料 negative 命中：</b>" + html_list(ret_neg, "未检测到") + "</div>"
                + "<div class='hitrow'><b>答案 positive 命中：</b>" + html_list(ans_pos, "未检测到") + "</div>"
                + "<div class='hitrow'><b>答案断言式 negative 命中：</b>" + html_list(ans_neg, "未检测到") + "</div>"
                + "<div class='hitrow'><b>答案 raw negative 提及：</b>" + html_list(raw_ans_neg, "未检测到") + "</div>"
                + "<h4>回答内容</h4><pre>" + esc(clip(answer, 1600)) + "</pre>"
                + "<h4>评测理由</h4><pre>" + esc(clip(reason, 1200)) + "</pre>"
                + "<h4>召回材料摘录</h4><pre>" + esc(clip(retrieval, 1400)) + "</pre>"
                + "</details>"
            )
        rollout_html = "<section><h3>Rollout / 测评结果</h3>" + "".join(blocks) + "</section>"

    eval_note = ""
    if eval_rows:
        expected = eval_rows[0].get("预期答复（机评文本）", "")
        eval_note = "<section><h3>测评集机评文本</h3><pre>" + esc(clip(expected, 1800)) + "</pre></section>"

    return f"""
<article class="case">
  <h2>{esc(row.get('record_id'))} {badge(str(len(result_rows)) + " samples" if result_rows else "no rollout")}</h2>
  <section class="grid2">
    <div><h3>1. 原始 Query / 重构 Query</h3>
      <dl><dt>原始 query</dt><dd>{esc(row.get('original_query'))}</dd><dt>重构 query</dt><dd class="query">{esc(row.get('synthesized_query'))}</dd></dl>
    </div>
    <div><h3>2. 基础 KG 边界</h3>
      <dl>
        <dt>一级类型</dt><dd>{esc(upper.get('label') or upper.get('type_id'))}</dd>
        <dt>锚点属性</dt><dd>{''.join(badge(x, 'anchor') for x in anchors)}</dd>
        <dt>目标属性</dt><dd>{badge(target, 'positive')}</dd>
        <dt>负向属性</dt><dd>{''.join(badge(x, 'negative') for x in negatives) or '<span class="muted">无</span>'}</dd>
      </dl>
    </div>
  </section>
  <section class="grid2">
    <div><h3>Positive 边界</h3>{html_list(boundary.get('positive_entities'))}</div>
    <div><h3>Negative 边界</h3>{html_list(boundary.get('negative_entities'))}</div>
  </section>
  <section><h3>3. 专用领域 KG 引用与游走</h3><pre>{esc(compact_json(domain_walk))}</pre></section>
  <section><h3>4. 机评引导</h3>
    <div class="grid3"><div><h4>positive rubric</h4>{html_list(guidance.get('positive_rubric'))}</div><div><h4>negative rubric</h4>{html_list(guidance.get('negative_rubric'))}</div><div><h4>判 0 条件</h4>{html_list(guidance.get('zero_score_conditions'))}</div></div>
    <p class="note">{esc(guidance.get('boundary_note'))}</p>
  </section>
  {eval_note}
  {rollout_html}
</article>
"""


def render_answer_panel(title: str, profile: Dict[str, Any], cls: str) -> str:
    run = profile["run"]
    return f"""
<div class="answer {cls}">
  <h3>{esc(title)} {badge('score=' + score_value(run), cls)}</h3>
  <dl><dt>dataID</dt><dd>{esc(run.get('dataID') or run.get('data_id') or '')}</dd></dl>
  <div class="grid2 compact">
    <div><h4>答案 positive 命中</h4>{html_list(profile['ans_pos'], '未检测到')}</div>
    <div><h4>答案 negative 命中</h4>{html_list(profile['ans_neg'], '未检测到')}</div>
  </div>
  <h4>回答内容</h4><pre>{esc(clip(profile['answer'], 2200))}</pre>
  <h4>评测理由</h4><pre>{esc(clip(profile['reason'], 1400))}</pre>
</div>
"""


def render_good_case(row: Dict[str, Any], pair: Dict[str, Any]) -> str:
    boundary = row.get("boundary") or {}
    guidance = row.get("machine_eval_guidance") or {}
    domain_walk = boundary.get("domain_walk") or {}
    upper = boundary.get("upper_type") or {}
    anchors = [prop_label(x) for x in as_list(boundary.get("anchor_properties"))]
    negatives = [prop_label(x) for x in as_list(boundary.get("negative_properties"))]
    target = prop_label(boundary.get("target_property") or {})
    domain_id = domain_walk.get("domain_id") or row.get("domain_id") or ""
    walked = as_list(domain_walk.get("walked_relations"))
    matched = as_list(domain_walk.get("matched_concepts"))
    return f"""
<article class="case goodcase">
  <h2>{esc(row.get('record_id'))} {badge(pair['tier'], 'positive')} {badge(str(domain_id), 'anchor')}</h2>
  <section class="grid2">
    <div>
      <h3>Query</h3>
      <p class="query">{esc(row.get('synthesized_query'))}</p>
      <dl><dt>原始 query</dt><dd>{esc(row.get('original_query'))}</dd><dt>领域</dt><dd>{esc(domain_id)}</dd><dt>上位词</dt><dd>{esc(upper.get('label') or upper.get('type_id'))}</dd></dl>
    </div>
    <div>
      <h3>上位词与属性</h3>
      <dl>
        <dt>锚点属性</dt><dd>{''.join(badge(x, 'anchor') for x in anchors)}</dd>
        <dt>目标属性</dt><dd>{badge(target, 'positive')}</dd>
        <dt>负向属性</dt><dd>{''.join(badge(x, 'negative') for x in negatives) or '<span class="muted">无</span>'}</dd>
      </dl>
    </div>
  </section>
  <section class="grid2">
    <div><h3>召回 positive 命中</h3>{html_list(pair['ret_pos'])}</div>
    <div><h3>召回 negative 命中</h3>{html_list(pair['ret_neg'])}</div>
  </section>
  <section class="grid2">
    <div><h3>Positive rubric</h3>{html_list(guidance.get('positive_rubric'))}</div>
    <div><h3>Negative rubric / 判 0 条件</h3>{html_list(as_list(guidance.get('negative_rubric')) + as_list(guidance.get('zero_score_conditions')))}</div>
  </section>
  <section class="grid2">
    <div><h3>游走节点</h3>{html_list(matched, '无')}</div>
    <div><h3>游走关系</h3>{html_list(walked, '无')}</div>
  </section>
  <section class="grid2 answers">
    {render_answer_panel('好 answer', pair['good'], 'positive')}
    {render_answer_panel('坏 answer', pair['bad'], 'negative')}
  </section>
  <details class="rollout"><summary>好 answer 召回材料摘录</summary><pre>{esc(clip(pair['good']['retrieval'], 1800))}</pre></details>
  <details class="rollout"><summary>坏 answer 召回材料摘录</summary><pre>{esc(clip(pair['bad']['retrieval'], 1800))}</pre></details>
</article>
"""


def render_good_cases_html(
    queries: List[Dict[str, Any]],
    result_by_case: Dict[str, List[Dict[str, str]]],
    output_html: Path,
) -> Dict[str, Any]:
    cards = []
    tier_counter: Counter[str] = Counter()
    for row in queries:
        case_id = str(row.get("record_id") or row.get("case_id") or "")
        pair = good_case_pair(row, result_by_case.get(case_id, []))
        if not pair:
            continue
        tier_counter[pair["tier"]] += 1
        cards.append(render_good_case(row, pair))
    html_text = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Pipeline2 V3 Good Query Cases</title>
<style>
body{{margin:0;background:#f6f7f9;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}}
header{{background:#fff;border-bottom:1px solid #d9dee7;padding:18px 24px;position:sticky;top:0;z-index:2}}
h1{{font-size:22px;margin:0 0 8px}} h2{{font-size:20px;margin:0 0 14px}} h3{{font-size:15px;margin:0 0 8px}} h4{{font-size:13px;margin:10px 0 6px}}
main{{max-width:1280px;margin:0 auto;padding:18px}}
.case{{background:#fff;border:1px solid #dce2ea;border-radius:8px;margin:0 0 16px;padding:18px;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
.goodcase{{border-left:5px solid #0f766e}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .compact{{gap:10px}}
dl{{display:grid;grid-template-columns:110px 1fr;gap:8px 12px;margin:0}} dt{{font-weight:650;color:#4b5563}} dd{{margin:0}}
.query{{font-weight:650;color:#0f766e;font-size:16px}} .muted{{color:#7b8494}}
.badge{{display:inline-block;border:1px solid #cbd5e1;border-radius:999px;padding:2px 8px;margin:2px;background:#f8fafc;font-size:12px}}
.positive{{background:#ecfdf5;border-color:#86efac;color:#166534}} .negative{{background:#fff1f2;border-color:#fda4af;color:#9f1239}} .anchor{{background:#eff6ff;border-color:#93c5fd;color:#1d4ed8}}
.answer{{border:1px solid #e5e7eb;border-radius:8px;padding:12px;background:#fcfcfd}} .answer.positive{{background:#f7fefb}} .answer.negative{{background:#fff8f8}}
pre{{white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid #e5e7eb;border-radius:6px;padding:10px;font-size:12px;max-height:360px;overflow:auto}}
details.rollout{{border:1px solid #e5e7eb;border-radius:6px;margin:8px 0;padding:8px;background:#fcfcfd}} summary{{cursor:pointer;font-weight:650}}
ul{{margin:6px 0 0 20px;padding:0}}
@media(max-width:900px){{.grid2{{grid-template-columns:1fr}} dl{{grid-template-columns:1fr}}}}
</style></head><body>
<header><h1>Pipeline2 V3 Good Query Cases</h1>
<div>筛选规则：召回同时命中 positive/negative rubric；同一 query 下存在高分好 answer 和低分坏 answer。good case 数：{len(cards)}；分层：{esc(dict(tier_counter))}</div></header>
<main>{''.join(cards) if cards else '<p class="muted">没有筛到符合条件的 good query case。</p>'}</main></body></html>"""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html_text, encoding="utf-8")
    return {"good_cases": len(cards), "tiers": dict(tier_counter), "good_case_html": str(output_html)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries-jsonl", type=Path, required=True)
    parser.add_argument("--eval-csv", type=Path, default=None)
    parser.add_argument("--result-csv", type=Path, default=None)
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument("--good-case-html", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    queries = list(iter_jsonl(args.queries_jsonl))
    eval_rows = read_csv(args.eval_csv)
    result_rows = read_csv(args.result_csv)

    eval_by_case: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in eval_rows:
        eval_by_case[base_case_id(row.get("dataID", ""))].append(row)
    result_by_case: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in result_rows:
        did = row.get("dataID") or row.get("data_id") or row.get("id") or ""
        if did:
            result_by_case[base_case_id(did)].append(row)

    score_counter = Counter(score_value(row) for row in result_rows if score_value(row))
    cards = []
    for row in queries:
        case_id = str(row.get("record_id") or row.get("case_id") or "")
        cards.append(render_case(row, eval_by_case.get(case_id, []), result_by_case.get(case_id, [])))

    html_text = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Pipeline2 V3 边界 Query 报告</title>
<style>
body{{margin:0;background:#f6f7f9;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}}
header{{background:#fff;border-bottom:1px solid #d9dee7;padding:18px 24px;position:sticky;top:0;z-index:2}}
h1{{font-size:22px;margin:0 0 8px}} h2{{font-size:20px;margin:0 0 14px}} h3{{font-size:15px;margin:0 0 8px}} h4{{font-size:13px;margin:10px 0 6px}}
main{{max-width:1280px;margin:0 auto;padding:18px}}
.case{{background:#fff;border:1px solid #dce2ea;border-radius:8px;margin:0 0 16px;padding:18px;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}
dl{{display:grid;grid-template-columns:120px 1fr;gap:8px 12px;margin:0}} dt{{font-weight:650;color:#4b5563}} dd{{margin:0}}
.query{{font-weight:650;color:#0f766e}} .muted{{color:#7b8494}} .note{{background:#f8fafc;border-left:3px solid #94a3b8;padding:8px 10px}}
.badge{{display:inline-block;border:1px solid #cbd5e1;border-radius:999px;padding:2px 8px;margin:2px;background:#f8fafc;font-size:12px}}
.positive{{background:#ecfdf5;border-color:#86efac;color:#166534}} .negative{{background:#fff1f2;border-color:#fda4af;color:#9f1239}} .anchor{{background:#eff6ff;border-color:#93c5fd;color:#1d4ed8}}
pre{{white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid #e5e7eb;border-radius:6px;padding:10px;font-size:12px;max-height:360px;overflow:auto}}
details.rollout{{border:1px solid #e5e7eb;border-radius:6px;margin:8px 0;padding:8px;background:#fcfcfd}} summary{{cursor:pointer;font-weight:650}}
.hitrow{{border-top:1px solid #edf0f4;margin-top:8px;padding-top:8px}}
ul{{margin:6px 0 0 20px;padding:0}}
@media(max-width:900px){{.grid2,.grid3{{grid-template-columns:1fr}} dl{{grid-template-columns:1fr}}}}
</style></head><body>
<header><h1>Pipeline2 V3 边界 Query 报告</h1>
<div>query 数：{len(queries)}；测评集行数：{len(eval_rows)}；结果行数：{len(result_rows)}；分数分布：{esc(dict(score_counter))}</div></header>
<main>{''.join(cards)}</main></body></html>"""
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(html_text, encoding="utf-8")
    summary = {"queries": len(queries), "eval_rows": len(eval_rows), "result_rows": len(result_rows), "output_html": str(args.output_html)}
    if args.good_case_html:
        summary.update(render_good_cases_html(queries, result_by_case, args.good_case_html))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
