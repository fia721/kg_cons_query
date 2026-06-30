#!/usr/bin/env python3
"""Download and index open source ontology/classification files for v3.

输入：
  --output-dir: 原始公开资料和解析索引输出目录。

输出：
  raw/:
    schema_org_current_https.jsonld
    dbpedia_ontology.owl
    naics_2022_manual.pdf
    naics_2022_structure.xlsx
    iso_3166_1.json
    nist_rbac_project.html
    wikidata_*.json
  index/:
    schema_org_terms.jsonl
    dbpedia_terms.jsonl
    naics_terms.jsonl
    iso_3166_1_terms.jsonl
    wikidata_terms.jsonl
    nist_rbac_links.jsonl
  download_manifest.jsonl:
    每个来源的 url、状态、文件大小、sha256、错误信息。

说明：
  Wikidata 全量 dump 规模过大，不适合在本 pipeline step0 中直接下载。
  这里下载的是映射实际依赖的 Wikidata EntityData 原始 JSON：
  P31/P279/P452/P17/P131/P106/P108/P361/P749 等。
"""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET


USER_AGENT = "kg-build-data-open-source-downloader/1.0"


SOURCES: List[Dict[str, Any]] = [
    {
        "source_id": "schema_org",
        "kind": "full_original",
        "filename": "schema_org_current_https.jsonld",
        "urls": [
            "https://schema.org/version/latest/schemaorg-current-https.jsonld",
            "https://schema.org/docs/jsonldcontext.jsonld",
        ],
    },
    {
        "source_id": "dbpedia_ontology",
        "kind": "full_original",
        "filename": "dbpedia_ontology.owl",
        "urls": [
            "https://downloads.dbpedia.org/ontology/dbpedia_2022-12.owl",
            "https://ontology.dbpedia.org/dbpedia_2022-12.owl",
            "https://akswnc7.informatik.uni-leipzig.de/dstreitmatter/archivo/dbpedia.org/ontology--DEV/2024.06.23-082004/ontology--DEV_type=parsed.owl",
        ],
    },
    {
        "source_id": "naics_2022_manual",
        "kind": "full_original",
        "filename": "naics_2022_manual.pdf",
        "urls": [
            "https://www.census.gov/naics/reference_files_tools/2022_NAICS_Manual.pdf",
        ],
    },
    {
        "source_id": "naics_2022_structure",
        "kind": "full_original",
        "filename": "naics_2022_structure.xlsx",
        "urls": [
            "https://www.census.gov/naics/2022NAICS/2022_NAICS_Structure.xlsx",
            "https://www.census.gov/naics/2022NAICS/2022_NAICS_Structure.csv",
        ],
    },
    {
        "source_id": "iso_3166_1",
        "kind": "open_source_full_original",
        "filename": "iso_3166_1.json",
        "urls": [
            "https://salsa.debian.org/iso-codes-team/iso-codes/-/raw/main/data/iso_3166-1.json",
            "https://raw.githubusercontent.com/lukes/ISO-3166-Countries-with-Regional-Codes/master/all/all.json",
        ],
    },
    {
        "source_id": "nist_rbac",
        "kind": "project_page_original",
        "filename": "nist_rbac_project.html",
        "urls": [
            "https://csrc.nist.gov/projects/role-based-access-control",
        ],
    },
]


WIKIDATA_IDS = ["P31", "P279", "P452", "P17", "P131", "P106", "P108", "P361", "P749"]


def request_url(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_one(raw_dir: Path, spec: Dict[str, Any], retries: int = 2) -> Dict[str, Any]:
    path = raw_dir / spec["filename"]
    result = {
        "source_id": spec["source_id"],
        "kind": spec["kind"],
        "filename": str(path),
        "status": "failed",
        "url": None,
        "size_bytes": 0,
        "sha256": None,
        "error": None,
    }
    if path.exists() and path.stat().st_size > 0:
        data = path.read_bytes()
        result.update(
            {
                "status": "exists",
                "size_bytes": len(data),
                "sha256": sha256_bytes(data),
            }
        )
        return result
    errors = []
    for url in spec["urls"]:
        for attempt in range(retries + 1):
            try:
                data = request_url(url)
                path.write_bytes(data)
                result.update(
                    {
                        "status": "downloaded",
                        "url": url,
                        "size_bytes": len(data),
                        "sha256": sha256_bytes(data),
                        "error": None,
                    }
                )
                return result
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url} attempt={attempt + 1}: {exc}")
                time.sleep(min(2 * (attempt + 1), 5))
    result["error"] = " | ".join(errors[-5:])
    return result


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def text_label(value: Any) -> str:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        return str(value.get("@value") or value.get("value") or value.get("label") or "")
    return str(value or "")


def index_schema_org(raw_dir: Path, index_dir: Path) -> int:
    path = raw_dir / "schema_org_current_https.jsonld"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for item in data.get("@graph", []):
        term_id = str(item.get("@id", ""))
        if not term_id:
            continue
        types = item.get("@type", [])
        if isinstance(types, str):
            types = [types]
        rows.append(
            {
                "source_id": "schema_org",
                "term_id": term_id,
                "label": text_label(item.get("rdfs:label") or item.get("schema:name") or term_id),
                "types": types,
                "comment": text_label(item.get("rdfs:comment")),
                "subclass_of": [v.get("@id") for v in as_list(item.get("rdfs:subClassOf")) if isinstance(v, dict) and v.get("@id")],
                "subproperty_of": [v.get("@id") for v in as_list(item.get("rdfs:subPropertyOf")) if isinstance(v, dict) and v.get("@id")],
            }
        )
    write_jsonl(index_dir / "schema_org_terms.jsonl", rows)
    return len(rows)


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def rdf_attr(elem: ET.Element, suffix: str) -> Optional[str]:
    for key, value in elem.attrib.items():
        if key.endswith(suffix):
            return value
    return None


def index_dbpedia(raw_dir: Path, index_dir: Path) -> int:
    path = raw_dir / "dbpedia_ontology.owl"
    if not path.exists():
        return 0
    rows = []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return 0
    for elem in root.iter():
        local = strip_ns(elem.tag)
        if local not in {"Class", "ObjectProperty", "DatatypeProperty"}:
            continue
        about = rdf_attr(elem, "about") or rdf_attr(elem, "ID")
        if not about:
            continue
        labels = []
        comments = []
        parents = []
        for child in elem:
            child_local = strip_ns(child.tag)
            if child_local == "label" and child.text:
                labels.append(child.text.strip())
            elif child_local == "comment" and child.text:
                comments.append(child.text.strip())
            elif child_local in {"subClassOf", "subPropertyOf"}:
                res = rdf_attr(child, "resource")
                if res:
                    parents.append(res)
        rows.append(
            {
                "source_id": "dbpedia_ontology",
                "term_id": about,
                "kind": local,
                "label": labels[0] if labels else about.rsplit("/", 1)[-1],
                "comment": comments[0] if comments else "",
                "parents": parents,
            }
        )
    write_jsonl(index_dir / "dbpedia_terms.jsonl", rows)
    return len(rows)


def read_xlsx_rows(path: Path) -> List[List[str]]:
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in shared_root.iter():
                if strip_ns(si.tag) == "si":
                    texts = [t.text or "" for t in si.iter() if strip_ns(t.tag) == "t"]
                    shared.append("".join(texts))
        sheet_names = [n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n)]
        if not sheet_names:
            return []
        root = ET.fromstring(zf.read(sheet_names[0]))
        rows = []
        for row in root.iter():
            if strip_ns(row.tag) != "row":
                continue
            cells = []
            for cell in row:
                if strip_ns(cell.tag) != "c":
                    continue
                cell_type = cell.attrib.get("t")
                value = ""
                for child in cell:
                    if strip_ns(child.tag) == "v" and child.text is not None:
                        value = child.text
                        break
                if cell_type == "s" and value:
                    idx = int(value)
                    value = shared[idx] if idx < len(shared) else value
                cells.append(value)
            if any(cells):
                rows.append(cells)
        return rows


def index_naics(raw_dir: Path, index_dir: Path) -> int:
    xlsx = raw_dir / "naics_2022_structure.xlsx"
    rows = []
    if xlsx.exists() and xlsx.suffix == ".xlsx":
        for cells in read_xlsx_rows(xlsx):
            joined = " ".join(cells)
            code = next((c for c in cells if re.fullmatch(r"\d{2,6}", c.strip())), "")
            title = ""
            for c in cells:
                if c.strip() and c.strip() != code and not re.fullmatch(r"\d{2,6}", c.strip()):
                    title = c.strip()
                    break
            if code and title:
                rows.append({"source_id": "naics", "code": code, "title": title, "raw": cells})
    write_jsonl(index_dir / "naics_terms.jsonl", rows)
    return len(rows)


def index_iso(raw_dir: Path, index_dir: Path) -> int:
    path = raw_dir / "iso_3166_1.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    if isinstance(data, dict) and "3166-1" in data:
        for item in data["3166-1"]:
            rows.append(
                {
                    "source_id": "iso_3166_1",
                    "alpha_2": item.get("alpha_2"),
                    "alpha_3": item.get("alpha_3"),
                    "name": item.get("name"),
                    "official_name": item.get("official_name"),
                    "numeric": item.get("numeric"),
                }
            )
    elif isinstance(data, list):
        for item in data:
            rows.append({"source_id": "iso_3166_1", **item})
    write_jsonl(index_dir / "iso_3166_1_terms.jsonl", rows)
    return len(rows)


def index_wikidata(raw_dir: Path, index_dir: Path) -> int:
    rows = []
    for path in sorted(raw_dir.glob("wikidata_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        entities = data.get("entities", {})
        for entity_id, entity in entities.items():
            labels = entity.get("labels", {})
            descriptions = entity.get("descriptions", {})
            rows.append(
                {
                    "source_id": "wikidata_core",
                    "entity_id": entity_id,
                    "label_en": labels.get("en", {}).get("value"),
                    "label_zh": labels.get("zh", {}).get("value") or labels.get("zh-cn", {}).get("value"),
                    "description_en": descriptions.get("en", {}).get("value"),
                    "datatype": entity.get("datatype"),
                    "type": entity.get("type"),
                    "claims": sorted(entity.get("claims", {}).keys()),
                }
            )
    write_jsonl(index_dir / "wikidata_terms.jsonl", rows)
    return len(rows)


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            attrs_dict = dict(attrs)
            self._href = attrs_dict.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            self.links.append({"href": self._href, "text": text})
            self._href = None
            self._text = []


def index_nist(raw_dir: Path, index_dir: Path) -> int:
    path = raw_dir / "nist_rbac_project.html"
    if not path.exists():
        return 0
    parser = LinkParser()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    rows = [
        {"source_id": "nist_rbac", "href": link["href"], "text": link["text"]}
        for link in parser.links
        if "role" in link["text"].lower() or "rbac" in link["text"].lower() or ".pdf" in link["href"].lower()
    ]
    write_jsonl(index_dir / "nist_rbac_links.jsonl", rows)
    return len(rows)


def download_wikidata(raw_dir: Path) -> List[Dict[str, Any]]:
    rows = []
    for entity_id in WIKIDATA_IDS:
        rows.append(
            download_one(
                raw_dir,
                {
                    "source_id": f"wikidata_{entity_id}",
                    "kind": "selected_entitydata_original",
                    "filename": f"wikidata_{entity_id}.json",
                    "urls": [f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"],
                },
            )
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/code_table_pipeline_v3/open_sources"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_dir = args.output_dir / "raw"
    index_dir = args.output_dir / "index"
    raw_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = [download_one(raw_dir, spec) for spec in SOURCES]
    manifest_rows.extend(download_wikidata(raw_dir))
    write_jsonl(args.output_dir / "download_manifest.jsonl", manifest_rows)

    index_counts = {
        "schema_org_terms": index_schema_org(raw_dir, index_dir),
        "dbpedia_terms": index_dbpedia(raw_dir, index_dir),
        "naics_terms": index_naics(raw_dir, index_dir),
        "iso_3166_1_terms": index_iso(raw_dir, index_dir),
        "wikidata_terms": index_wikidata(raw_dir, index_dir),
        "nist_rbac_links": index_nist(raw_dir, index_dir),
    }
    (args.output_dir / "index_summary.json").write_text(json.dumps(index_counts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"download_manifest": str(args.output_dir / "download_manifest.jsonl"), "index_counts": index_counts}, ensure_ascii=False, indent=2))

    failed = [row for row in manifest_rows if row["status"] == "failed"]
    if failed:
        print(json.dumps({"failed": failed}, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
