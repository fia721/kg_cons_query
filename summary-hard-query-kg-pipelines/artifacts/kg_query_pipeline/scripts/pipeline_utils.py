#!/usr/bin/env python3
"""Shared helpers for the KG query pipeline."""

from __future__ import annotations

import csv
import json
import re
import sys
import zipfile
from difflib import SequenceMatcher
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List


PIPELINE_DIR = Path(__file__).resolve().parents[1]
KG_ROOT = PIPELINE_DIR.parents[1]
LEGACY_SCRIPTS = KG_ROOT / "artifacts/scripts"

if str(LEGACY_SCRIPTS) not in sys.path:
    sys.path.append(str(LEGACY_SCRIPTS))


DEFAULT_TRAIN_JSONL = Path(
    "/mlx_devbox/users/yuantongfei/playground/rl_reward/data/rl_data/"
    "0520_rl_train/rl_training_data_v2_mix_grpo_clean_with_context.jsonl"
)

OUTPUT_FIELDS = [
    "dataID",
    "query",
    "企业内是否有知识",
    "预期答复（机评文本）",
    "ref图片文件名称",
    "机评忽略case",
]


def raise_csv_field_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def iter_jsonl(path: Path) -> Iterable[tuple[int, Dict[str, Any]]]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if line:
                yield line_no, json.loads(line)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def original_user_query(record: Dict[str, Any]) -> str:
    if record.get("query"):
        return str(record["query"])
    for msg in record.get("messages", []) or []:
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def load_json_maybe(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def format_numbered(items: List[Any]) -> List[str]:
    return [f"{idx}. {item}" for idx, item in enumerate(items, 1) if str(item).strip()]


def looks_like_optional_example(item: Any) -> bool:
    text = str(item or "")
    if not text.strip():
        return False
    if "定向邀约" in text:
        return True
    separators = ["、", "，", ",", "或"]
    return sum(text.count(sep) for sep in separators) >= 2


def split_legacy_required_items(items: List[Any]) -> tuple[List[Any], List[Any]]:
    required: List[Any] = []
    optional: List[Any] = []
    for item in items:
        if looks_like_optional_example(item):
            optional.append(item)
        else:
            required.append(item)
    return required, optional


def soften_legacy_negative_text(text: Any) -> str:
    value = str(text or "")
    replacements = {
        "出现任何hard negative实体即得0分": "把 hard negative 实体作为正确答案、并列答案或目标属性成员纳入时得0分",
        "出现任何 hard negative 实体即得0分": "把 hard negative 实体作为正确答案、并列答案或目标属性成员纳入时得0分",
        "出现任何hard negative实体即判0": "把 hard negative 实体作为正确答案、并列答案或目标属性成员纳入时判0",
        "出现任何 hard negative 实体即判0": "把 hard negative 实体作为正确答案、并列答案或目标属性成员纳入时判0",
        "出现即判0": "作为正确答案、并列答案或目标属性成员纳入时判0",
        "出现即得0分": "作为正确答案、并列答案或目标属性成员纳入时得0分",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def build_expected_answer(query_plan: Dict[str, Any]) -> str:
    answer_boundary = query_plan.get("answer_boundary") or {}
    rubric = query_plan.get("rubric") or {}
    selected = query_plan.get("selected_contrast_set") or {}

    lines: List[str] = []
    required_attributes = answer_boundary.get("required_attributes") or []
    optional_examples = answer_boundary.get("optional_examples") or []
    allowed_entities = answer_boundary.get("allowed_entities") or []
    legacy_include = answer_boundary.get("must_include_or_allow") or []
    if required_attributes:
        must_include = required_attributes
    else:
        must_include, legacy_optional = split_legacy_required_items(legacy_include)
        optional_examples = optional_examples or legacy_optional

    must_exclude = answer_boundary.get("must_exclude") or []
    boundary_rule = answer_boundary.get("boundary_rule") or ""
    zero_if_answer_asserts = rubric.get("zero_if_answer_asserts") or []
    zero_if_mentions = rubric.get("zero_if_mentions") or []
    non_zero_if_negated = rubric.get("non_zero_if_negated") or []
    zero_if_contains = rubric.get("zero_if_contains") or []

    if query_plan.get("classification_axis") or selected.get("target_attribute_value"):
        lines.append("【问题意图】")
        if query_plan.get("classification_axis"):
            lines.append(f"- 识别属性轴：{query_plan['classification_axis']}")
        if selected.get("target_attribute_value"):
            lines.append(f"- 只回答目标属性：{selected['target_attribute_value']}")
        if boundary_rule:
            lines.append(f"- 边界：{boundary_rule}")

    if must_include:
        if lines:
            lines.append("")
        lines.append("【必要关键信息】")
        lines.extend(format_numbered(must_include))

    if allowed_entities:
        if lines:
            lines.append("")
        lines.append("【允许答案】")
        lines.extend(format_numbered(allowed_entities))

    if optional_examples:
        if lines:
            lines.append("")
        lines.append("【可选补充，不作为必答】")
        lines.extend(format_numbered(optional_examples))

    scoring_lines: List[str] = []
    if rubric.get("score_1"):
        scoring_lines.append(f"可信无误：{rubric['score_1']}")
    if rubric.get("score_0"):
        scoring_lines.append(f"判0条件：{soften_legacy_negative_text(rubric['score_0'])}")
    if must_exclude:
        scoring_lines.append(
            "不得把以下内容作为正确答案、并列答案或目标属性成员纳入："
            + "；".join(str(x) for x in must_exclude)
        )
    if zero_if_answer_asserts or zero_if_contains:
        terms = zero_if_answer_asserts or zero_if_contains
        scoring_lines.append(
            "若答案将以下内容作为正确答案、并列答案或目标属性成员纳入，则判0："
            + "；".join(str(x) for x in terms)
        )
    if zero_if_mentions:
        scoring_lines.append("以下内容只要在最终答案中出现即判0：" + "；".join(str(x) for x in zero_if_mentions))
    if non_zero_if_negated:
        scoring_lines.append(
            "若以下内容仅在否定、排除、对比语境中出现，不因此判0："
            + "；".join(str(x) for x in non_zero_if_negated)
        )
    elif zero_if_contains and not zero_if_mentions:
        scoring_lines.append("注意：若上述负例仅在否定、排除、对比语境中出现，不应仅因字符串出现判0。")
    if boundary_rule:
        scoring_lines.append(f"边界规则：{boundary_rule}")
    if rubric.get("notes"):
        scoring_lines.append(f"备注：{soften_legacy_negative_text(rubric['notes'])}")

    if scoring_lines:
        if lines:
            lines.append("")
        lines.append("【打分备注】")
        lines.extend(scoring_lines)

    return "\n".join(lines)


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def cell_xml(row_idx: int, col_idx: int, value: str) -> str:
    ref = f"{column_name(col_idx)}{row_idx}"
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value or "")}</t></is></c>'


def worksheet_xml(rows: List[Dict[str, str]], fields: List[str]) -> str:
    sheet_rows = []
    header_cells = [cell_xml(1, col_idx, field) for col_idx, field in enumerate(fields, 1)]
    sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')
    for row_idx, row in enumerate(rows, 2):
        cells = [cell_xml(row_idx, col_idx, str(row.get(field, ""))) for col_idx, field in enumerate(fields, 1)]
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    last_col = column_name(len(fields))
    dimension = f"A1:{last_col}{len(rows) + 1}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols><col min="1" max="{len(fields)}" width="32" customWidth="1"/></cols>'
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        "</worksheet>"
    )


def write_xlsx(rows: List[Dict[str, str]], output_path: Path, fields: List[str] = OUTPUT_FIELDS) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="eval_dataset" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": worksheet_xml(rows, fields),
    }
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)


def normalize_term(term: Any) -> str:
    value = re.sub(r"\s+", "", str(term or "")).strip(" ，,。；;：:").lower()
    value = value.replace("（", "(").replace("）", ")")
    value = re.sub(r"[\[\]【】《》“”\"'`]", "", value)
    return value


def normalize_entity_alias(term: Any) -> str:
    """Normalize common Chinese entity wording variants for hit display only."""
    value = normalize_term(term)
    value = re.sub(r"(类型|适配|具备|具有|拥有|商家|主体|模式|资源|能力|经验|意向|跨境|供货|出海|的|型)+", "", value)
    value = re.sub(r"[、，,。；;：:|/\\()\-\s]+", "", value)
    return value


def term_aliases(term: Any) -> List[str]:
    text = normalize_term(term)
    aliases = [text]
    aliases.append(normalize_entity_alias(text))
    for part in re.split(r"[、，,；;|/\\]", text):
        part = part.strip()
        if len(part) >= 2:
            aliases.append(part)
            aliases.append(normalize_entity_alias(part))

    out = []
    seen = set()
    for alias in aliases:
        if len(alias) < 2 or alias in seen:
            continue
        seen.add(alias)
        out.append(alias)
    return out


def contains_any(text: str, terms: List[str]) -> List[str]:
    text_norm = normalize_term(text)
    text_alias_norm = normalize_entity_alias(text)
    hits = []
    for term in terms:
        term_norm = normalize_term(term)
        if term_norm and term_norm in text_norm:
            hits.append(term)
            continue
        aliases = [x for x in term_aliases(term) if len(x) >= 3]
        if any(alias in text_norm or alias in text_alias_norm for alias in aliases):
            hits.append(term)
    return hits


def distinct_nonempty(items: List[Any]) -> List[str]:
    seen = set()
    values = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def query_similarity(a: Any, b: Any) -> float:
    """Return a lightweight normalized string similarity for original vs synthetic query."""
    left = re.sub(r"\s+", "", str(a or "").lower()).strip("？?。.!！")
    right = re.sub(r"\s+", "", str(b or "").lower()).strip("？?。.!！")
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def is_open_enumeration_query(query: str) -> bool:
    return any(token in query for token in ["哪些", "有哪些", "列出", "包括哪些", "都有谁", "是什么"]) and not any(
        token in query for token in ["是否", "能否", "哪个"]
    )


BAD_STANDALONE_QUERY_TERMS = [
    "这份材料",
    "该材料",
    "材料中",
    "这份文档",
    "该文档",
    "文档中",
    "本文",
    "这个介绍",
    "这份介绍",
    "上表",
    "表格中",
    "栏目中",
    "这份海外电商模式介绍中",
    "领域",
    "语境",
]


OVER_EXPLICIT_QUERY_PATTERNS = [
    r"在[^？?，,。；;]{1,20}领域中",
    r"在[^？?，,。；;]{1,20}语境下",
    r"在[^？?，,。；;]{1,20}场景中",
    r"[^？?，,。；;]{1,20}领域中，",
    r"[^？?，,。；;]{1,20}语境下，",
    r"[^？?，,。；;]{1,20}场景中，",
    r"[^？?，,。；;]{1,20}领域的",
    r"[^？?，,。；;]{1,20}语境的",
]


def query_plan_is_synthesizable(query_plan: Dict[str, Any]) -> tuple[bool, str]:
    """Check if a query/rubric has enough contrast structure for GRPO data."""
    if query_plan.get("can_synthesize") is False:
        return False, str(query_plan.get("reject_reason") or "can_synthesize=false")

    query = str(query_plan.get("query") or "").strip()
    selected = query_plan.get("selected_contrast_set") or {}
    boundary = query_plan.get("answer_boundary") or {}
    rubric = query_plan.get("rubric") or {}
    quality = query_plan.get("quality_checks") or {}
    query_type = str(query_plan.get("query_type") or "")

    positives = as_list(selected.get("positive_entities"))
    positives += as_list(boundary.get("required_attributes"))
    positives += as_list(boundary.get("must_include_or_allow"))
    positives += as_list(boundary.get("allowed_entities"))

    negatives = as_list(selected.get("hard_negative_entities"))
    negatives += as_list(selected.get("negative_attribute_values"))
    negatives += as_list(boundary.get("must_exclude"))
    negatives += as_list(rubric.get("zero_if_answer_asserts"))
    negatives += as_list(rubric.get("zero_if_mentions"))
    negatives += as_list(rubric.get("zero_if_contains"))

    if not query:
        return False, "missing query"
    if any(term in query for term in BAD_STANDALONE_QUERY_TERMS):
        return False, "query depends on external material/document/table scope"
    if any(re.search(pattern, query) for pattern in OVER_EXPLICIT_QUERY_PATTERNS):
        return False, "query uses over-explicit domain/context hint"
    if not [x for x in positives if str(x).strip()]:
        return False, "missing positive boundary"
    if not [x for x in negatives if str(x).strip()]:
        return False, "missing negative boundary"

    retrieval_likelihood = str(selected.get("retrieval_contrast_likelihood") or "").lower()
    if retrieval_likelihood == "low":
        return False, "low retrieval_contrast_likelihood"

    pos_strength = str(selected.get("positive_evidence_strength") or "").lower()
    neg_strength = str(selected.get("negative_evidence_strength") or "").lower()
    if pos_strength == "weak":
        return False, "weak positive evidence"
    if neg_strength == "weak":
        return False, "weak negative evidence"

    taxonomy = query_plan.get("taxonomy") or {}
    mutual_exclusivity = str(taxonomy.get("mutual_exclusivity") or "").strip().lower()
    if mutual_exclusivity == "non_exclusive":
        return False, "non_exclusive taxonomy is not valid for hard negative boundary"
    if mutual_exclusivity == "scoped_relation":
        return False, "scoped_relation requires material/table scope and is not valid as standalone query"

    if quality:
        consistency = str(quality.get("query_target_consistency") or "").strip().lower()
        if consistency in {"fail", "failed"}:
            return False, "query_target_consistency=fail"
        if consistency == "uncertain":
            return False, "query_target_consistency=uncertain"

        if quality.get("query_disambiguation_sufficient") is False:
            return False, "query_disambiguation_sufficient=false"

        if quality.get("standalone_query") is False:
            return False, "standalone_query=false"

        if quality.get("over_explicit_domain_hint") is True:
            return False, "over_explicit_domain_hint=true"

        if quality.get("negative_in_query_scope") is False:
            return False, "negative_in_query_scope=false"

        expected_error = str(quality.get("expected_summary_error_likelihood") or "").strip().lower()
        if expected_error == "low":
            return False, "expected_summary_error_likelihood=low"

        completeness = str(quality.get("positive_set_completeness") or "").strip().lower()
        closed_required = quality.get("closed_set_required")
        if closed_required is True and completeness in {"partial", "unknown", "uncertain"}:
            return False, f"positive_set_completeness={completeness}"
        if is_open_enumeration_query(query) and completeness in {"partial", "unknown", "uncertain"}:
            return False, f"open enumeration query requires complete positive set, got {completeness}"
    elif query_type in {"fine_grained_attribute", "enumeration_filter", "direct_attribute"}:
        return False, "missing quality_checks"

    if query_type == "fine_grained_attribute":
        if mutual_exclusivity != "exclusive":
            if mutual_exclusivity == "non_exclusive":
                return False, "fine_grained_attribute requires mutually exclusive secondary categories; got non_exclusive"
            if mutual_exclusivity == "scoped_relation":
                return False, "fine_grained_attribute cannot use scoped_relation as intrinsic entity attribute"
            return False, "fine_grained_attribute requires taxonomy.mutual_exclusivity=exclusive"

        overlap_risk = str(taxonomy.get("overlap_risk") or "").strip().lower()
        if overlap_risk == "high":
            return False, "fine_grained_attribute rejected because taxonomy.overlap_risk=high"

        categories = taxonomy.get("categories") or []
        multi_entity_categories = []
        for category in categories:
            entities = category.get("entities") or []
            count = category.get("entity_count")
            if count is None:
                count = len([x for x in entities if str(x).strip()])
            if count >= 2:
                multi_entity_categories.append(category)
        if len(multi_entity_categories) < 2:
            return False, "fine_grained_attribute requires at least two secondary categories with >=2 entities each"

        positive_set = {str(x) for x in as_list(selected.get("positive_entities"))}
        negative_set = {str(x) for x in as_list(selected.get("hard_negative_entities"))}
        has_positive_multi_category = any(positive_set.intersection({str(x) for x in (c.get("entities") or [])}) for c in multi_entity_categories)
        has_negative_multi_category = any(negative_set.intersection({str(x) for x in (c.get("entities") or [])}) for c in multi_entity_categories)
        if not has_positive_multi_category or not has_negative_multi_category:
            return False, "fine_grained_attribute needs positive and negative entities from multi-entity categories"

    if query_type == "enumeration_filter":
        taxonomy = query_plan.get("taxonomy") or {}
        categories = taxonomy.get("categories") or []
        positive_set = {str(x) for x in as_list(selected.get("positive_entities"))}
        negative_set = {str(x) for x in as_list(selected.get("hard_negative_entities"))}
        if not negative_set:
            return False, "enumeration_filter requires hard negative entities"

        negative_category_counts = []
        for category in categories:
            entities = category.get("entities") or []
            entity_set = {str(x) for x in entities}
            count = category.get("entity_count")
            if count is None:
                count = len(distinct_nonempty(entities))
            if entity_set.intersection(negative_set):
                negative_category_counts.append(count)

        if categories and negative_category_counts and max(negative_category_counts) < 2:
            return False, "enumeration_filter requires at least one hard negative category with >=2 entities"

        concrete_negatives = distinct_nonempty(as_list(selected.get("hard_negative_entities")) + as_list(boundary.get("must_exclude")))
        if len(concrete_negatives) < 2 and not (negative_category_counts and max(negative_category_counts) >= 2):
            return False, "enumeration_filter requires at least two concrete hard negatives"

    return True, "ok"
