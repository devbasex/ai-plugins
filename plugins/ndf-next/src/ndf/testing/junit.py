"""JUnit の XML から落ちたテストを読む（#3616。入出力を持たない）。

落ちたテストは `<testcase>` の子に `<failure>` か `<error>` を持つもの（`<skipped>` だけのものは数えない）。
ファイルの書き場所はランナーごとに違う（testcase の `file`・testsuite の `file`・testcase の `classname`・
testsuite の `name`）ため、属性の候補を順に試し、作業ディレクトリが追跡するファイルと照らして決める（設計の決定 2）。
決められない testcase は一覧へ入れず、名前と試した属性を理由に返す。
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator, Sequence
from typing import NamedTuple, Optional

# ファイルの候補の順。(属性を読む要素, 属性の名前)。要素は testcase か、最も近い testsuite
CANDIDATE_ATTRS = (("testcase", "file"), ("testsuite", "file"), ("testcase", "classname"), ("testsuite", "name"))
# 成果物 1 本の ZIP の上限（取得の前に一覧の `size_in_bytes` と比べる）
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
# 展開した `.xml` 1 本の上限
MAX_XML_BYTES = 50 * 1024 * 1024
# 成果物 1 本から展開する `.xml` の合計の上限
MAX_EXTRACT_BYTES = 100 * 1024 * 1024


class JunitError(Exception):
    """JUnit の成果物を読めない（壊れた ZIP・XML、展開後の大きさの上限を超えた）。"""


class JunitFailures(NamedTuple):
    """XML 1 本の読み取りの結果。`nodes` は `<ファイル>::<name>`（重ねない・昇順）、`unresolved` はファイルを決められない testcase の理由。"""

    nodes: tuple[str, ...]
    unresolved: tuple[str, ...]


def _normalized(candidate: str) -> str:
    """候補のバックスラッシュを `/` にし、先頭の `./` を外す。"""
    text = candidate.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def resolve_file(candidates: Sequence[str], tracked: frozenset[str]) -> Optional[str]:
    """属性の候補を順に試し、追跡するファイルの 1 つに決める。どの候補でも決まらなければ None。

    | 候補と追跡するファイルの関係 | 使うファイル |
    | --- | --- |
    | 一致する | そのファイル |
    | 候補が絶対パスで `/<ファイル>` で終わる | 当たるうち最も長いファイル |
    | `/<候補>` で終わるファイルがちょうど 1 つ | そのファイル |
    """
    for raw in candidates:
        cand = _normalized(raw)
        if not cand:
            continue
        got = _match_one(cand, tracked)
        if got is not None:
            return got
    return None


def _is_absolute(cand: str) -> bool:
    """正規化した候補が絶対パス（`/` で始まるか、`C:/` のようにドライブ名で始まる）か。"""
    return cand.startswith("/") or (len(cand) > 2 and cand[0].isalpha() and cand[1:3] == ":/")


def _match_one(cand: str, tracked: frozenset[str]) -> Optional[str]:
    """候補 1 つを追跡するファイルへ照らす（`resolve_file` の表の 3 行を上から）。決まらなければ None。"""
    if cand in tracked:
        return cand
    suffixes = [t for t in tracked if cand.endswith("/" + t)] if _is_absolute(cand) else []
    if suffixes:
        return max(suffixes, key=len)
    longer = [t for t in tracked if t.endswith("/" + cand)]
    if len(longer) == 1:
        return longer[0]
    return None


def _cases(elem: ET.Element, suite: Optional[ET.Element]) -> Iterator[tuple[ET.Element, Optional[ET.Element]]]:
    """(testcase, 最も近い testsuite) を文書の順に並べる。testsuite の入れ子を辿る。"""
    for child in elem:
        if child.tag == "testsuite":
            yield from _cases(child, child)
        elif child.tag == "testcase":
            yield child, suite


def _failed_cases(root: ET.Element) -> Iterator[tuple[ET.Element, Optional[ET.Element]]]:
    """落ちた（`<failure>` か `<error>` を子に持つ）testcase と、最も近い testsuite。"""
    start = root if root.tag == "testsuite" else None
    for case, suite in _cases(root, start):
        if case.find("failure") is not None or case.find("error") is not None:
            yield case, suite


def _attr(elem: Optional[ET.Element], name: str) -> str:
    """要素の属性の値。要素か属性が無ければ空文字。"""
    value = elem.get(name) if elem is not None else None
    return value if value is not None else ""


def _candidates(case: ET.Element, suite: Optional[ET.Element]) -> list[str]:
    elements = {"testcase": case, "testsuite": suite}
    return [_attr(elements[where], attr) for where, attr in CANDIDATE_ATTRS]


def _tried() -> str:
    return "・".join(f"{where} の {attr}" for where, attr in CANDIDATE_ATTRS)


def junit_failures(xml: bytes, tracked: frozenset[str]) -> JunitFailures:
    """XML 1 本の落ちた testcase を `<ファイル>::<name>` と、ファイルを決められない testcase の理由へ分ける。

    読めない XML は `JunitError`。
    """
    try:
        root = ET.fromstring(xml)  # noqa: S314 CI の成果物の JUnit を読むだけで、外部実体は展開しない
    except ET.ParseError as exc:
        raise JunitError(f"JUnit の XML として読めない: {exc}") from exc
    nodes: set[str] = set()
    unresolved: list[str] = []
    for case, suite in _failed_cases(root):
        name = _attr(case, "name")
        found = resolve_file(_candidates(case, suite), tracked)
        if found is None:
            unresolved.append(f"testcase「{_attr(case, 'classname')} {name}」のファイルを決められない（試した属性: {_tried()}）")
            continue
        nodes.add(f"{found}::{name}")
    return JunitFailures(tuple(sorted(nodes)), tuple(unresolved))


def _checked_members(z: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """ZIP の `.xml` のメンバーを名前の順に並べ、展開後の大きさ（1 本と合計）を上限と比べる。超えれば `JunitError`。"""
    infos = sorted((i for i in z.infolist() if i.filename.lower().endswith(".xml")), key=lambda i: i.filename)
    for info in infos:
        if info.file_size > MAX_XML_BYTES:
            raise JunitError(f"{info.filename} の展開後が {info.file_size} バイトで上限 {MAX_XML_BYTES} を超える")
    if sum(i.file_size for i in infos) > MAX_EXTRACT_BYTES:
        raise JunitError(f"展開する .xml の合計が上限 {MAX_EXTRACT_BYTES} バイトを超える")
    return infos


def xml_members(archive: bytes) -> list[bytes]:
    """成果物の ZIP の中の `.xml`（大文字小文字を問わない）の中身を、ZIP の中の名前の順に返す。

    1 本の展開後が `MAX_XML_BYTES` を、合計が `MAX_EXTRACT_BYTES` を超えれば 1 本も返さず `JunitError`。大きさは展開の前に
    `ZipInfo.file_size` で比べる。設定より大きい中身は `zipfile` が CRC の不一致（`BadZipFile`）で断るため、上限を超えて展開しない。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            return [z.read(info) for info in _checked_members(z)]
    except zipfile.BadZipFile as exc:
        raise JunitError(f"成果物の ZIP を読めない: {exc}") from exc
