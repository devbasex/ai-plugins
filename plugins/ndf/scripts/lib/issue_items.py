"""Issue の記載項目の定義を読み、本文が定義を満たすかを判定する（#2737）。GitHub を呼ばない。

定義は `data/issue-items.json`（種類ごとの必須・該当時・不要と水準）、置き場ごとの表し方は `data/issue-stores.json` にある。
プロジェクトの `.ndf/issue-items.json` があれば、既定の定義の代わりにそのまま読む（表し方のファイルは置き換えない）。
工程が足す節の見出しの語は、表し方のファイルの `record_headings` に、定義の `record_headings`（あれば）を足したものである。
種類のラベル名は、表し方のファイルの GitHub の `labels` を、定義の `kind_labels`（種類 → ラベル名。あれば）で種類ごとに置き換えたものである。

- `load_definition(root)`: 定義と表し方を読み、不変条件（各項目が 4 種類の区分と水準を持つ・該当時の項目は条件を持つ・
  3 つの置き場で表し方を 1 つに解決できる）を確かめる。破れていれば `DefinitionError`
- `judge_body(body, labels, context, definition)`: 本文とラベルから欠けを返す純粋な関数。`context` は機械で決まる
  該当時の条件のキー（`ai`・`origin`）の真偽を持つ

hook の経路（`hook_lib/issue_guard.py`）から呼ぶため、標準ライブラリと `decl`・`gh_sections` だけで書く。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import gh_sections
import limits

DATA = Path(__file__).resolve().parents[1] / "data"
DEFAULT_ITEMS = DATA / "issue-items.json"
STORES = DATA / "issue-stores.json"
SETTING = "issue-items.json"
NEEDS = ("必須", "該当時", "不要")
STORE_NAMES = ("github", "markdown", "redmine")
CONTEXT_KEYS = ("ai", "origin")
SECTION = "section"
EMPTY_MARKS = ("_No response_", "<!-- ndf:section-end -->")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
REPO_IN_URL = re.compile(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")


class DefinitionError(ValueError):
    """定義か表し方が不変条件を破る（読む側は終了コード 2 で止める）。"""


class Gap(NamedTuple):
    heading: str  # 項目名（種類の欠けは「種類」）
    result: str  # missing / empty / order / kind

    def to_dict(self) -> dict:
        return {"heading": self.heading, "result": self.result}


@dataclass
class Item:
    id: str
    name: str
    need: dict[str, str]
    level: str
    when: str | None = None
    key: str | None = None


@dataclass
class Definition:
    kinds: list[str]
    items: list[Item]
    last: str | None
    stores: dict[str, dict]
    record_headings: list[str]
    path: Path = DEFAULT_ITEMS
    own_kind_labels: dict[str, str] = field(default_factory=dict)
    by_id: dict[str, Item] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_id = {it.id: it for it in self.items}

    def rep(self, item: Item, store: str = "github") -> dict:
        """項目の表し方。識別子ごとの表し方が無ければ、その置き場の既定（ほかの項目）を使う。"""
        s = self.stores[store]
        return (s.get("items") or {}).get(item.id) or s["default"]

    def is_section(self, item: Item, store: str = "github") -> bool:
        return self.rep(item, store).get("type") == SECTION

    def labels_by_kind(self) -> dict[str, str]:
        """種類 → GitHub のラベル。定義の `kind_labels` があれば、その種類のラベル名を置き換える。"""
        labels = dict((self.rep(self.by_id["kind"]).get("labels") or {}) if "kind" in self.by_id else {})
        labels.update(self.own_kind_labels)
        return labels

    def kind_labels(self) -> dict[str, str]:
        """GitHub のラベル → 種類。"""
        return {lab: kind for kind, lab in self.labels_by_kind().items()}

    def required_sections(self, kind: str) -> list[Item]:
        return [it for it in self.items if it.need.get(kind) == "必須" and self.is_section(it)]

    def item_for_heading(self, text: str) -> Item | None:
        """見出しの語が当たる項目（項目名・「項目名: 補足」・「項目名（補足）」）。長い名前を先に当てる。"""
        for it in sorted(self.items, key=lambda i: -len(i.name)):
            if heading_matches(text, it.name):
                return it
        return None

    def is_record_heading(self, text: str) -> bool:
        return any(heading_matches(text, h) for h in self.record_headings)


def heading_matches(text: str, name: str) -> bool:
    text = text.strip()
    return text == name or text.startswith((f"{name}:", f"{name}：", f"{name}（", f"{name} ("))


# --- 読み込み ----------------------------------------------------------------------------


def _definition_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DefinitionError(f"{path} を読めない（{exc}）") from None
    if not isinstance(data, dict):
        raise DefinitionError(f"{path} の最上位がオブジェクトでない")
    return data


def _read_item(raw: dict, kinds: list[str], path: Path) -> Item:
    """1 項目を読み、区分と水準と条件を確かめる。"""
    iid, name = raw.get("id"), raw.get("name")
    if not isinstance(iid, str) or not iid or not isinstance(name, str) or not name:
        raise DefinitionError(f"{path}: id か name の無い項目がある: {raw!r}")
    need = raw.get("need") if isinstance(raw.get("need"), dict) else {}
    bad = [k for k in kinds if need.get(k) not in NEEDS]
    if bad:
        raise DefinitionError(f"{path}: 項目「{name}」の区分が {'・'.join(bad)} で必須・該当時・不要のどれでもない")
    level = raw.get("level")
    if not isinstance(level, str) or not level.strip():
        raise DefinitionError(f"{path}: 項目「{name}」に水準が無い")
    when = raw.get("when") if isinstance(raw.get("when"), dict) else {}
    text, key = when.get("text"), when.get("key")
    if "該当時" in need.values() and (not isinstance(text, str) or not text.strip()):
        raise DefinitionError(f"{path}: 該当時の項目「{name}」に該当する条件が無い")
    if key is not None and key not in CONTEXT_KEYS:
        raise DefinitionError(f"{path}: 項目「{name}」の条件のキー {key!r} は {'・'.join(CONTEXT_KEYS)} のどれでもない")
    return Item(iid, name, {k: need[k] for k in kinds}, level, text, key)


def _check_stores(stores: dict, items: list[Item]) -> None:
    for s in STORE_NAMES:
        store = stores.get(s)
        if not isinstance(store, dict):
            raise DefinitionError(f"{STORES}: 置き場 {s} の表し方が無い")
        own = store.get("items") if isinstance(store.get("items"), dict) else {}
        default = store.get("default")
        lost = [it.name for it in items if not isinstance(own.get(it.id) or default, dict)]
        if lost:
            raise DefinitionError(f"{STORES}: 置き場 {s} で表し方を決められない項目: {'・'.join(lost)}")


def items_path(root) -> Path:
    """読む定義のファイル（`.ndf/issue-items.json` があればそれ、無ければ既定）。"""
    if root is not None:
        try:
            import decl

            found = decl.setting_path(root, SETTING)
        except Exception:  # noqa: BLE001 — 設定の置き場を決められないときは既定を読む
            found = None
        if found is not None and Path(found).is_file():
            return Path(found)
    return DEFAULT_ITEMS


def _own_kind_labels(raw: dict, kinds: list[str], path: Path) -> dict[str, str]:
    """定義の `kind_labels`（任意）。種類 → 空でないラベル名で、種類は kinds のどれか、ラベル名は重ならない。"""
    own = raw.get("kind_labels")
    if own is None:
        return {}
    if not isinstance(own, dict) or not all(isinstance(v, str) and v.strip() for v in own.values()):
        raise DefinitionError(f"{path}: kind_labels が種類 → ラベル名のオブジェクトでない")
    unknown = [k for k in own if k not in kinds]
    if unknown:
        raise DefinitionError(f"{path}: kind_labels の種類 {'・'.join(unknown)} は kinds に無い")
    if len(set(own.values())) != len(own):
        raise DefinitionError(f"{path}: kind_labels で同じラベル名を 2 つ以上の種類に付けている")
    return {k: v.strip() for k, v in own.items()}


def _read_kinds(raw: dict, path: Path) -> list[str]:
    """定義の `kinds`。空でない文字列の並び。"""
    kinds = raw.get("kinds")
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) and k for k in kinds):
        raise DefinitionError(f"{path}: kinds が文字列の並びでない")
    return kinds


def _record_headings(raw: dict, st: dict, path: Path) -> list[str]:
    """表し方の `record_headings` に、定義の `record_headings`（任意）のうち無いものを足した並び。"""
    own = raw.get("record_headings")
    if own is not None and (not isinstance(own, list) or not all(isinstance(h, str) and h.strip() for h in own)):
        raise DefinitionError(f"{path}: record_headings が文字列の並びでない")
    heads = [h for h in st.get("record_headings") or [] if isinstance(h, str)]
    heads += [h for h in own or [] if h not in heads]
    return heads


def _check_merged_labels(definition: Definition, path: Path) -> None:
    """kind_labels で置き換えた後のラベル名が種類どうしで重ならないこと。"""
    merged = definition.labels_by_kind()
    if len(set(merged.values())) != len(merged):
        raise DefinitionError(f"{path}: kind_labels で置き換えたラベル名が、ほかの種類の既定のラベル名と重なる")


def load_definition(root=None) -> Definition:
    path = items_path(root)
    raw = _definition_json(path)
    kinds = _read_kinds(raw, path)
    rows = raw.get("items")
    if not isinstance(rows, list) or not rows:
        raise DefinitionError(f"{path}: items が無い")
    items = [_read_item(r if isinstance(r, dict) else {}, kinds, path) for r in rows]
    st = _definition_json(STORES)
    stores = st.get("stores") if isinstance(st.get("stores"), dict) else {}
    _check_stores(stores, items)
    heads = _record_headings(raw, st, path)
    last = raw.get("last") if isinstance(raw.get("last"), str) else None
    definition = Definition(kinds, items, last, stores, heads, path, own_kind_labels=_own_kind_labels(raw, kinds, path))
    _check_merged_labels(definition, path)
    return definition


def origin_repo(cwd) -> str | None:
    """`cwd` のリポジトリの `git remote get-url origin` の `<所有者>/<リポジトリ>`。読めなければ `None`。GitHub は呼ばない。"""
    try:
        p = subprocess.run(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=limits.seconds("hook.git-remote"),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = REPO_IN_URL.search(p.stdout.strip()) if p.returncode == 0 else None
    return m.group(1) if m else None


def repo_differs(here: str | None, want: str) -> bool:
    """`want` が `here` と別のリポジトリか（大文字小文字は区別しない）。`here` を読めない（`None`）ときは別とみなす（C5 の側へ倒す）。"""
    return here is None or here.lower() != want.lower()


def other_repo(repo: str | None, cwd) -> bool:
    """起票先 `repo` が `cwd` のリポジトリ（origin）と別か。`repo` が無ければ同じ、origin を読めなければ別とみなす。"""
    if not repo:
        return False
    return repo_differs(origin_repo(cwd), repo.split("github.com/")[-1].removesuffix(".git").strip("/"))


def definition_for(root, repo: str | None) -> Definition:
    """起票先 `repo` を判定する定義。`root` のリポジトリと別の起票先には `root` の宣言を当てず既定を読む（PR 2856）。"""
    if root is not None and items_path(root) != DEFAULT_ITEMS and other_repo(repo, root):
        root = None
    return load_definition(root)


# --- 本文の判定 --------------------------------------------------------------------------


class Heading(NamedTuple):
    line: int
    level: int
    text: str


def body_headings(lines: list[str]) -> list[Heading]:
    """コードブロックの外にある見出し。"""
    out = []
    for i, line in gh_sections.unfenced_lines(lines):
        h = _HEADING.match(line)
        if h:
            out.append(Heading(i, len(h.group(1)), h.group(2).strip()))
    return out


def _empty(lines: list[str], heads: list[Heading], n: int) -> bool:
    """`heads[n]` の節（同じか上の段の次の見出しまで）が空か。HTML のコメントと `_No response_` は中身に数えない。"""
    h = heads[n]
    end = next((x.line for x in heads[n + 1 :] if x.level <= h.level), len(lines))
    text = _COMMENT.sub("", "\n".join(lines[h.line + 1 : end]))
    for mark in EMPTY_MARKS:
        text = text.replace(mark, "")
    return not text.strip()


def kind_of(labels, definition: Definition) -> str | None:
    """ラベルから決まる種類。0 個か 2 個以上なら `None`。"""
    table = definition.kind_labels()
    found = {table[lab] for lab in labels or [] if lab in table}
    return found.pop() if len(found) == 1 else None


def _needed(definition: Definition, kind: str, context: dict) -> list[Item]:
    """この本文で欠けてはいけない項目（必須と、条件が機械で決まって当たる該当時の項目）。"""
    out = []
    for it in definition.items:
        if not definition.is_section(it):
            continue
        need = it.need.get(kind)
        if need == "必須" or (need == "該当時" and it.key and context.get(it.key)):
            out.append(it)
    return out


def _order_gap(heads: list[Heading], definition: Definition) -> Gap | None:
    """書き手が書く節の最後の項目（依頼（原文））の後ろに、工程が足す節でない節があるか。"""
    last = definition.by_id.get(definition.last or "")
    if last is None:
        return None
    at = next((n for n, h in enumerate(heads) if heading_matches(h.text, last.name)), None)
    if at is None:
        return None
    level = heads[at].level
    for h in heads[at + 1 :]:
        if h.level <= level and not heading_matches(h.text, last.name) and not definition.is_record_heading(h.text):
            return Gap(last.name, "order")
    return None


def _item_gap(item: Item, lines: list[str], heads: list[Heading], definition: Definition) -> Gap | None:
    """1 項目の欠け。節が無ければ missing、どの節も空なら empty、そうでなければ None。"""
    at = [n for n, h in enumerate(heads) if h.level >= 2 and definition.item_for_heading(h.text) is item]
    if not at:
        return Gap(item.name, "missing")
    if all(_empty(lines, heads, n) for n in at):
        return Gap(item.name, "empty")
    return None


def heading_line(body: str, definition: Definition, item_id: str) -> str | None:
    """本文で項目 `item_id` の節と判定される最初の見出しの行（`judge_body` と同じ規則）。無ければ `None`。"""
    item = definition.by_id.get(item_id)
    lines = (body or "").replace("\r\n", "\n").split("\n")
    for h in body_headings(lines):
        if item is not None and h.level >= 2 and definition.item_for_heading(h.text) is item:
            return lines[h.line].rstrip()
    return None


def judge_body(body: str, labels, context: dict | None, definition: Definition) -> tuple[str | None, list[Gap]]:
    """`(決まった種類か None, 欠け)`。種類が決まらなければ必須の項目は見ない（種類の欠けと順序だけを返す）。"""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    heads = body_headings(lines)
    kind = kind_of(labels, definition)
    gaps: list[Gap] = []
    if kind is None:
        gaps.append(Gap(definition.by_id["kind"].name if "kind" in definition.by_id else "種類", "kind"))
    else:
        for it in _needed(definition, kind, context or {}):
            gap = _item_gap(it, lines, heads, definition)
            if gap is not None:
                gaps.append(gap)
    order = _order_gap(heads, definition)
    if order is not None:
        gaps.append(order)
    return kind, gaps


def describe_gaps(gaps: list[Gap]) -> str:
    """欠けを 1 行で（`影響（無い）・種類（不明）`）。"""
    word = {"missing": "無い", "empty": "空", "order": "後ろに書き手の節がある", "kind": "不明"}
    return "・".join(f"{g.heading}（{word.get(g.result, g.result)}）" for g in gaps)
