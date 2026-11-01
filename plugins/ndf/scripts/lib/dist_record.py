"""リリース記録（`## 配布の記録`）の形を 1 か所に持つ（#1273 の決定 3）。

形の正本は `skills/progress-tracking/SKILL.md` の「配布の記録」の表である。書く側（`release-steps.py record`）は
`format_record` で組み、読む側（`sprint-close.py`・`sprint_close_lib/record_find.py`）は `parse_record` で読む。見出しと行の名前をほかのモジュールに写さない。

使う側は `deps.require("md")` を先に呼ぶ。
"""

from __future__ import annotations

import re

import legacy_names
import md

DIST = "## 配布の記録"
VERIFY = "## リリース後テスト"
STAGE = "段階: "
VERSION = "版: "
SPRINT = "スプリント: "
VERIFY_VERSION = "対象の版: "


def format_record(prev: str | None, new: str, prs, *, stage_note: str, version_note: str) -> str:
    """本番のリリース記録を組む。

    prev は直前の正式版（無ければ None）、new は出した版（`v` を付けない）、prs はスプリントの PR の番号の並び。
    stage_note は `段階:` の括弧に書く配布の事実、version_note は `版:` の根拠の注記（どちらも parse_record は読まない）。"""
    return "\n".join(
        [
            DIST,
            "",
            f"{STAGE}本番（{stage_note}）",
            f"{VERSION}{prev or 'なし'} → {new}（{version_note}）",
            f"{SPRINT}PR " + " / ".join(f"#{n}" for n in prs),
            "",
        ]
    )


def released(stage) -> bool:
    """段階が本番か配布なしか（課題を閉じてよい段階。`sprint-close.py` と `sprint_close_lib/record_find.py` が使う）。"""
    stage = stage or ""
    return stage.startswith("本番") or stage.startswith("配布なし")


def _strip_note(text):
    """版の文字列から全角の括弧の注記を落とす。"""
    return re.sub(r"\s*（.*$", "", text).strip()


def _read_dist_block(block):
    """配布の記録の節から段階・スプリントの PR・本番の版を読む。"""
    out = {"stage": None, "version": None, "sprint_prs": []}
    for ln in block:
        if ln.startswith(STAGE) and out["stage"] is None:
            out["stage"] = ln[len(STAGE) :].strip()
        elif ln.startswith(legacy_names.record_labels(SPRINT)):  # 改名の前の記録の見出しも読む
            out["sprint_prs"] = [int(x) for x in re.findall(r"#(\d+)", ln)]
    if (out["stage"] or "").startswith("本番"):
        for ln in block:
            if ln.startswith(VERSION) and "→" in ln:
                out["version"] = _strip_note(ln.split("→", 1)[1])
                break
    return out


def _heading_sections(text, lines):
    """本文の節のうち、見出しの行が `#` で始まるもの（ブロックの切り出しと配布の記録の読み取りが共有する）。"""
    return [s for s in md.md_sections(text) if lines[s.heading.line].startswith("#")]


def _verify_blocks(lines, sections):
    """リリース後テストのブロックを切り出す。各ブロックは {"start": 開始行, "ver": 対象の版, "lines": 行の並び}。"""
    blocks = []
    verify_heads = {s.heading.line for s in sections if lines[s.heading.line].startswith(VERIFY)}
    cur = None
    for i, ln in enumerate(lines):
        if i in verify_heads:
            cur = {"start": i, "ver": None, "lines": [ln]}
            continue
        if cur is None:
            continue
        cur["lines"].append(ln)
        if ln.startswith(VERIFY_VERSION) and cur["ver"] is None:
            cur["ver"] = _strip_note(ln[len(VERIFY_VERSION) :])
        if ln.startswith("合否:"):
            blocks.append(cur)
            cur = None
    return blocks


_EXPLICIT_VERSION = re.compile(r"v?\d+\.\d+\.\d+")
_BASE_VERSION = re.compile(r"v?(\d+\.\d+\.\d+)")


def verify_block_for_base(text, version):
    """本文（text）のリリース後テストのブロックのうち、`対象の版:` の基底が version の基底と一致する最後のブロックの
    本文。無ければ None。parse_record の完全な版の一致とは別の規則で、受け入れ条件の版ごとの判定が使う。"""
    want = _BASE_VERSION.match(str(version))
    lines = text.splitlines()
    blocks = [
        b
        for b in _verify_blocks(lines, _heading_sections(text, lines))
        if want and (m := _BASE_VERSION.match(b["ver"] or "")) and m.group(1) == want.group(1)
    ]
    return "\n".join(blocks[-1]["lines"]) if blocks else None


def _is_explicit_version(ver):
    """`対象の版:` が版数（`10.17.69-dev.1` など）で書かれているか。コミットの表記（`main c427284`）は版数ではない。"""
    return bool(ver) and _EXPLICIT_VERSION.match(ver) is not None


def _pick_verify_block(blocks, stage, version, dist_start):
    """使うリリース後テストのブロック。本番は対象の版が一致する最後のブロック、無ければ（版数を上げない配布で
    `版:` と `対象の版:` の書き方が違うとき。#1789）最後の配布の記録より後で、対象の版が版数でない最後のブロック。
    版数の違うテスト（本番 10.17.68 の後の 10.17.69-dev.1 など）は本番の検証に使わない。配布なしは最後の配布の
    記録より後の最後のブロック。"""
    after = [b for b in blocks if b["start"] > dist_start]
    if stage.startswith("本番") and version:
        hit = [b for b in blocks if b["ver"] == version]
        if hit:
            return "\n".join(hit[-1]["lines"])
        after = [b for b in after if not _is_explicit_version(b["ver"])]
    elif not stage.startswith("配布なし"):
        return None
    return "\n".join(after[-1]["lines"]) if after else None


def _dist_comment_id(parts, dist_line):
    """最後の配布の記録の見出しの行を含む部分のコメントの識別子（振り返りの行を比べる基準。#2228 の決定 10）。

    parts は (先頭の行の番号, コメントの識別子) の並び（本文は識別子が None）。記録が本文にあれば、記録の PR の
    最初のコメントの識別子を使い、コメントが 1 件も無ければ None。parts が無ければ None。"""
    if not parts:
        return None
    hit = None
    for start, cid in parts:
        if start <= dist_line:
            hit = cid
    if hit is not None:
        return hit
    return next((cid for _, cid in parts if cid is not None), None)


def parse_record(text, parts=None):
    """最後の配布の記録と、使うリリース後テストのブロックを読む。

    parts は text を組んだ部分ごとの (先頭の行の番号, コメントの識別子) の並び（sprint-close.py の read_record が返す）。
    戻り値: {"found", "stage", "version", "sprint_prs", "verify_block", "dist_comment_id"}。
    verify_block は本番なら対象の版が一致する最後のブロック（無ければ最後の配布の記録より後で、対象の版が版数でない
    最後のブロック）、
    配布なしなら最後の配布の記録より後の最後のブロック。無ければ None。
    dist_comment_id は最後の配布の記録のコメントの識別子（_dist_comment_id。記録が無ければ None）。
    """
    lines = text.splitlines()
    sections = _heading_sections(text, lines)
    dists = [s for s in sections if lines[s.heading.line].startswith(DIST)]
    dist_start = dists[-1].heading.line if dists else None
    out = {"found": dist_start is not None, "stage": None, "version": None, "sprint_prs": [], "verify_block": None, "dist_comment_id": None}
    if dist_start is None:
        return out
    out.update(_read_dist_block(lines[dists[-1].start : dists[-1].end]))
    blocks = _verify_blocks(lines, sections)
    out["verify_block"] = _pick_verify_block(blocks, out["stage"] or "", out["version"], dist_start)
    out["dist_comment_id"] = _dist_comment_id(parts, dist_start)
    return out
