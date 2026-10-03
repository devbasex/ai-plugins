"""JUnit XML の読み取り（#1334 決定 4・5）。落ちたテストの ID と所要を、実行器の出力の文字列でなく JUnit から読む（I6）。

ID は `file::classname::name`。`file` が絶対パス（PHPUnit は CI とコンテナで絶対パスを書く）なら、追跡ファイルの
末尾に一致する最短の部分へ直し、一致しなければその ID は読めないものとして扱う。CI の成果物は `gh api` の読む要求だけで
落とす。標準ライブラリだけで書く。
"""

from __future__ import annotations

import fnmatch
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Callable, Iterable, Optional

# 成果物の名前が JUnit を持つと読む形（解析と同じ規則）。宣言の `test.ci.junit_artifacts` が無いときに使う。
JUNIT_NAME = re.compile(r"junit|test-?result|test-?report|phpunit|pytest|jest|vitest", re.I)
ARTIFACT_BYTES = 50 * 1024 * 1024
SEP = "::"


def total_seconds(xml_bytes: bytes) -> Optional[float]:
    """JUnit の XML 1 本の直列の所要（根の `time`、無ければ直下の `testsuite` の `time` の合計）。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if root.get("time"):
        try:
            return float(root.get("time"))
        except ValueError:
            return None
    total = 0.0
    for child in root.findall("testsuite"):
        try:
            total += float(child.get("time") or 0)
        except ValueError:
            continue
    return total if root.findall("testsuite") else None


def _cases(root: ET.Element) -> Iterable[tuple[ET.Element, Optional[str]]]:
    """`testcase` と、それを含む `testsuite` の `file`（testcase に `file` が無いときの補い）。"""
    for suite in root.iter("testsuite"):
        for case in suite.findall("testcase"):
            yield case, suite.get("file")
    if root.tag == "testcase":
        yield root, None


def failed_cases(xml_bytes: bytes) -> Optional[list[dict[str, str]]]:
    """`failure` / `error` を持つ `testcase` の `{file, classname, name}`。読めなければ `None`。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    out: list[dict[str, str]] = []
    seen = set()
    for case, suite_file in _cases(root):
        if case.find("failure") is None and case.find("error") is None:
            continue
        row = {
            "file": str(case.get("file") or suite_file or ""),
            "classname": str(case.get("classname") or ""),
            "name": str(case.get("name") or ""),
        }
        key = (row["file"], row["classname"], row["name"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def relative_file(file: str, tracked: Iterable[str]) -> Optional[str]:
    """`file` を作業ディレクトリからの相対パスへ直す。追跡ファイルの末尾に一致する最短の部分。一致しなければ `None`。"""
    text = str(file or "").replace("\\", "/")
    if not text:
        return None
    files = set(tracked)
    if not text.startswith("/"):
        norm = text[2:] if text.startswith("./") else text
        return norm if norm in files else None
    parts = text.lstrip("/").split("/")
    for i in range(len(parts) - 1, -1, -1):
        candidate = "/".join(parts[i:])
        if candidate in files:
            return candidate
    return None


def test_id(case: dict[str, str], file: str) -> str:
    return SEP.join((file, case.get("classname") or "", case.get("name") or ""))


def file_of(test_id_value: str) -> str:
    return str(test_id_value).split(SEP, 1)[0]


def failed_ids(xml_bytes: bytes, tracked: Iterable[str]) -> Optional[list[str]]:
    """落ちたテストの ID（`file::classname::name`）。`file` を相対へ直せない ID は落とす。読めなければ `None`。"""
    cases = failed_cases(xml_bytes)
    if cases is None:
        return None
    files = list(tracked)
    out: list[str] = []
    for case in cases:
        rel = relative_file(case["file"], files)
        if rel is None:
            continue
        value = test_id(case, rel)
        if value not in out:
            out.append(value)
    return out


TEXT_CHARS = 8000  # 1 件の本文に残す文字数。超えたら先頭と末尾を半分ずつ残す（状態ファイルを膨らませない）


def _trimmed(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n…\n" + text[-half:]


def failure_texts(xml_bytes: bytes, tracked: Iterable[str], limit: int = TEXT_CHARS) -> Optional[dict[str, str]]:
    """落ちたテストの ID ごとの本文（`failure` / `error` の `message` と本文）。読めなければ `None`（#1649）。

    ID は `failed_ids` と同じ形で、相対へ直せない ID は落とす。本文は `limit` 文字に縮める。
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    files = list(tracked)
    out: dict[str, str] = {}
    for case, suite_file in _cases(root):
        parts = [
            "\n".join(p for p in (str(el.get("message") or ""), str(el.text or "")) if p)
            for el in (case.findall("failure") + case.findall("error"))
        ]
        if not parts:
            continue
        row = {"classname": str(case.get("classname") or ""), "name": str(case.get("name") or "")}
        rel = relative_file(str(case.get("file") or suite_file or ""), files)
        if rel is None:
            continue
        key = test_id(row, rel)
        out[key] = _trimmed("\n".join(filter(None, [out.get(key, ""), *parts])), limit)
    return out


def read_failure_texts(path, tracked: Iterable[str]) -> Optional[dict[str, str]]:
    """置き場のファイルから落ちたテストの本文を読む。無い・読めなければ `None`。"""
    try:
        with open(path, "rb") as fh:
            return failure_texts(fh.read(), tracked)
    except OSError:
        return None


def read_failed_ids(path, tracked: Iterable[str]) -> Optional[list[str]]:
    """置き場のファイルから落ちた ID を読む。無い・読めなければ `None`。"""
    try:
        with open(path, "rb") as fh:
            return failed_ids(fh.read(), tracked)
    except OSError:
        return None


# ---------- CI の成果物 ----------


def _picked(artifacts: list[dict[str, Any]], name_glob: Optional[str]) -> list[dict[str, Any]]:
    out = []
    for a in artifacts:
        name = str(a.get("name") or "")
        if a.get("expired"):
            continue
        if name_glob and fnmatch.fnmatch(name, name_glob) or (not name_glob and JUNIT_NAME.search(name)):
            out.append(a)
    return out


def artifact_xmls(
    get: Callable[[str], Any], raw: Callable[[str], bytes], repo: str, run_id: Any, name_glob: Optional[str] = None
) -> list[bytes]:
    """run の成果物のうち名前が当たるものを落とし、中の XML の本文を返す。`get` は JSON を、`raw` はバイト列を返す。"""
    arts = (get(f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100") or {}).get("artifacts") or []
    picked = _picked(arts, name_glob)
    out: list[bytes] = []
    size = 0
    for a in picked:
        size += int(a.get("size_in_bytes") or 0)
        if size > ARTIFACT_BYTES:
            break
        data = raw(f"repos/{repo}/actions/artifacts/{a['id']}/zip")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for name in z.namelist():
                    if name.lower().endswith(".xml"):
                        out.append(z.read(name))
        except zipfile.BadZipFile:
            continue
    return out
