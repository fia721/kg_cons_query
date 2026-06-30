#!/usr/bin/env python3
"""Download and index domain vocabulary sources.

输入：
  --source-specs: domain_vocab_source_specs.jsonl
  --bootstrap-terms: domain_vocab_bootstrap_terms.jsonl
  --output-dir: open_sources/domain_vocab

输出：
  raw/: 每个来源下载到的原始文件或页面。
  index/domain_vocab_terms.jsonl: 统一 term index。
  domain_vocab_download_manifest.jsonl: 每个 URL 的下载状态。

说明：
  本脚本优先下载权威来源；如果 devbox 网络不可用，会继续写入
  bootstrap terms，并把状态标为 bootstrap_pending_download，保证下游
  grounding router 可运行但不会误认为已经完成全量下载。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html.parser
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET


USER_AGENT = "kg-build-data-domain-vocab-downloader/0.1"


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def safe_name(text: str) -> str:
    text = urllib.parse.urlparse(text).netloc + "_" + urllib.parse.urlparse(text).path.rsplit("/", 1)[-1]
    text = text.strip("_") or "index"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)[:180]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, timeout: int, ignore_proxy: bool) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if ignore_proxy else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def download_specs(specs: List[Dict[str, Any]], raw_dir: Path, timeout: int, retries: int, ignore_proxy: bool) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    raw_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        source_id = spec["source_id"]
        for url in spec.get("urls", []):
            filename = f"{source_id}__{safe_name(url)}"
            if not Path(filename).suffix:
                filename += ".html"
            path = raw_dir / filename
            row = {
                "source_id": source_id,
                "domain": spec.get("domain", ""),
                "kind": spec.get("kind", ""),
                "name": spec.get("name", ""),
                "url": url,
                "filename": str(path),
                "status": "failed",
                "size_bytes": 0,
                "sha256": "",
                "error": "",
            }
            if path.exists() and path.stat().st_size > 0:
                data = path.read_bytes()
                row.update({"status": "exists", "size_bytes": len(data), "sha256": sha256_bytes(data)})
                rows.append(row)
                continue
            errors = []
            for attempt in range(retries + 1):
                try:
                    data = fetch(url, timeout=timeout, ignore_proxy=ignore_proxy)
                    path.write_bytes(data)
                    row.update({"status": "downloaded", "size_bytes": len(data), "sha256": sha256_bytes(data)})
                    break
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"attempt={attempt + 1}: {exc}")
                    time.sleep(min(2 * (attempt + 1), 8))
            if row["status"] == "failed":
                row["error"] = " | ".join(errors[-3:])
            rows.append(row)
    return rows


class LinkTextParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self._href: Optional[str] = None
        self._text: List[str] = []
        self.text_chunks: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        s = " ".join(data.split())
        if s:
            self.text_chunks.append(s)
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            self.links.append({"href": self._href, "text": text})
            self._href = None
            self._text = []


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def attr_suffix(elem: ET.Element, suffix: str) -> str:
    for k, v in elem.attrib.items():
        if k.endswith(suffix):
            return v
    return ""


def index_json_terms(path: Path, source_id: str, domain: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    if isinstance(data, dict) and "@graph" in data:
        for item in data.get("@graph", []):
            term_id = str(item.get("@id", ""))
            label = item.get("rdfs:label") or item.get("schema:name") or term_id
            if isinstance(label, dict):
                label = label.get("@value", "")
            comment = item.get("rdfs:comment") or ""
            if isinstance(comment, dict):
                comment = comment.get("@value", "")
            if term_id:
                rows.append({"surface": str(label), "aliases": [term_id], "domain": domain, "source_id": source_id, "source_status": "downloaded", "parents": [], "properties": [], "notes": str(comment)[:500]})
    elif source_id == "spdx" and isinstance(data, dict):
        for lic in data.get("licenses", []):
            rows.append({"surface": lic.get("name", ""), "aliases": [lic.get("licenseId", "")], "domain": domain, "source_id": source_id, "source_status": "downloaded", "parents": ["software license"], "properties": [{"property_id": "software_artifact_property", "value_id": "license"}], "notes": lic.get("reference", "")})
    return [r for r in rows if r.get("surface")]


def index_xml_terms(path: Path, source_id: str, domain: str) -> List[Dict[str, Any]]:
    data = path.read_bytes()
    if path.suffix == ".gz":
        try:
            data = gzip.decompress(data)
        except Exception:
            return []
    try:
        root = ET.fromstring(data)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for elem in root.iter():
        local = strip_ns(elem.tag)
        if local not in {"Class", "Weakness", "Attack_Pattern", "cpe-item", "Term", "Code"}:
            continue
        term_id = attr_suffix(elem, "about") or attr_suffix(elem, "ID") or attr_suffix(elem, "name") or attr_suffix(elem, "Name")
        label = attr_suffix(elem, "Name") or attr_suffix(elem, "name") or term_id.rsplit("/", 1)[-1]
        desc = ""
        for child in elem:
            cl = strip_ns(child.tag)
            if cl in {"Description", "Summary", "comment", "title"} and child.text:
                desc = child.text.strip()
                break
        if label:
            rows.append({"surface": label, "aliases": [term_id] if term_id else [], "domain": domain, "source_id": source_id, "source_status": "downloaded", "parents": [], "properties": [], "notes": desc[:500]})
    return rows


def index_zip_terms(path: Path, source_id: str, domain: str, limit_files: int = 200) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")][:limit_files]
            for name in names:
                lower = name.lower()
                if not lower.endswith((".owl", ".rdf", ".xml", ".json", ".jsonld")):
                    continue
                tmp = Path(name)
                try:
                    data = zf.read(name)
                except Exception:
                    continue
                if len(data) > 8_000_000:
                    continue
                fake = path.parent / f".tmp_{path.stem}_{tmp.name}"
                fake.write_bytes(data)
                if lower.endswith((".json", ".jsonld")):
                    rows.extend(index_json_terms(fake, source_id, domain))
                else:
                    rows.extend(index_xml_terms(fake, source_id, domain))
                fake.unlink(missing_ok=True)
    except Exception:
        return rows
    return rows


def index_html_terms(path: Path, source_id: str, domain: str) -> List[Dict[str, Any]]:
    parser = LinkTextParser()
    try:
        parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    rows = []
    for link in parser.links:
        text = link.get("text", "").strip()
        href = link.get("href", "")
        if len(text) >= 3 and any(k in (text + " " + href).lower() for k in ["taxonomy", "ontology", "code", "download", "warehouse", "loan", "credit", "license", "cpe", "cwe", "capec", "locode"]):
            rows.append({"surface": text, "aliases": [href], "domain": domain, "source_id": source_id, "source_status": "downloaded_page_link", "parents": [], "properties": [], "notes": "link discovered from source page"})
    return rows[:200]


def index_downloads(manifest_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in manifest_rows:
        if item.get("status") not in {"downloaded", "exists"}:
            continue
        path = Path(item["filename"])
        source_id = item["source_id"]
        domain = item.get("domain", "")
        lower = path.name.lower()
        if lower.endswith((".json", ".jsonld")):
            rows.extend(index_json_terms(path, source_id, domain))
        elif lower.endswith((".xml", ".owl", ".rdf", ".gz")):
            rows.extend(index_xml_terms(path, source_id, domain))
        elif lower.endswith(".zip"):
            rows.extend(index_zip_terms(path, source_id, domain))
        elif lower.endswith((".html", ".htm")):
            rows.extend(index_html_terms(path, source_id, domain))
    return rows


def load_bootstrap(path: Path) -> List[Dict[str, Any]]:
    return list(iter_jsonl(path)) if path.exists() else []


def dedupe_terms(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        key = (row.get("source_id", ""), row.get("domain", ""), row.get("surface", ""), tuple(row.get("aliases", [])[:3]))
        if key in seen or not row.get("surface"):
            continue
        seen.add(key)
        out.append(row)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--source-specs", type=Path, default=root / "data/domain_vocab_source_specs.jsonl")
    parser.add_argument("--bootstrap-terms", type=Path, default=root / "data/domain_vocab_bootstrap_terms.jsonl")
    parser.add_argument("--output-dir", type=Path, default=root / "open_sources/domain_vocab")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--ignore-proxy", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    specs = list(iter_jsonl(args.source_specs))
    raw_dir = args.output_dir / "raw"
    index_dir = args.output_dir / "index"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: List[Dict[str, Any]]
    if args.skip_download:
        manifest_rows = []
        for path in raw_dir.glob("*"):
            if path.is_file():
                source_id = path.name.split("__", 1)[0]
                spec = next((s for s in specs if s["source_id"] == source_id), {})
                data = path.read_bytes()
                manifest_rows.append({"source_id": source_id, "domain": spec.get("domain", ""), "filename": str(path), "status": "exists", "size_bytes": len(data), "sha256": sha256_bytes(data), "url": ""})
    else:
        manifest_rows = download_specs(specs, raw_dir, args.timeout, args.retries, args.ignore_proxy)
    downloaded_terms = index_downloads(manifest_rows)
    bootstrap_terms = load_bootstrap(args.bootstrap_terms)
    all_terms = dedupe_terms(downloaded_terms + bootstrap_terms)
    write_jsonl(args.output_dir / "domain_vocab_download_manifest.jsonl", manifest_rows)
    write_jsonl(index_dir / "domain_vocab_terms.jsonl", all_terms)
    summary = {
        "manifest": str(args.output_dir / "domain_vocab_download_manifest.jsonl"),
        "index": str(index_dir / "domain_vocab_terms.jsonl"),
        "num_specs": len(specs),
        "num_downloaded_or_existing": sum(1 for r in manifest_rows if r.get("status") in {"downloaded", "exists"}),
        "num_failed": sum(1 for r in manifest_rows if r.get("status") == "failed"),
        "num_downloaded_terms": len(downloaded_terms),
        "num_bootstrap_terms": len(bootstrap_terms),
        "num_index_terms": len(all_terms),
    }
    (args.output_dir / "domain_vocab_index_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
