#!/usr/bin/env python3
"""review_criteria.py: 指摘の基準の正本と、レビューの宣言（`.ndf/review.json`）の読み取り（#1287・#1910）。

レビュー担当への指示（cross-review の `launch-reviewer.sh`）・修正担当の文脈のファイル（`fix-steps.py context`）・
見送りの返信（`fix-steps.py finalize`）は、どれもここの定数と、宣言のプロファイルから組む。標準ライブラリだけで書く。

    review_criteria.py reviewer [--root <worktree>]   レビュー担当への節を標準出力へ
    review_criteria.py fixer    [--root <worktree>]   修正担当への節を標準出力へ

`--root` を省くと宣言を読まない既定の節（基準 3 の無い形）を出す。重点やプロファイルが読めないときは理由を標準エラーへ出し、
終了コードは 0 のまま既定の節で続ける。`--root` を渡すと、節の後ろへ MVV の節（NDF の共通原則 → プロジェクト MVV →
判断の決まり。`project_mvv.block`・#1366）を足す。

宣言の形（`version: 1` はほかの鍵を無視する。`version: 2` では `focus` も `profile` も任意）:

    {"version": 1, "focus": ["<重点の名前>", ...]}
    {"version": 2, "focus": [...], "profile": {"criteria": {"1": "..."}, "not_written": ["..."],
     "waive_kinds": {"<種類>": "<名前>"}, "reply": "...{inner}...",
     "categories": {"<名前>": {"paths": ["<glob>"], "instructions": "..."}}, "replace_auto_categories": false}}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_mvv  # noqa: E402

DECL_PATH = (".ndf", "review.json")

# 基準の番号は、重点の宣言が無くても詰めない（設計の決定 4）。3 は宣言があるときだけ現れる
CRITERIA = {
    1: "利用者が普通に使う経路で、誤動作する・止まる・データを壊す",
    2: "秘密・認証認可・利用者のデータ・戻せない操作に触れる（起きる確率によらず書く）",
    4: "設計の文書で、実装する人が違うものを作ってしまう食い違い",
}
FOCUS_LABEL = "このプロジェクトのレビューの重点に当たる"
FOCUS_CRITERION = 3
# 外せない基準（#1910 の決定 1）。プロファイルがあっても番号 2 のまま残り、プロファイルは 2 と 3 を使えない
FIXED_CRITERION = 2
CRITERION_NUMBERS = tuple(sorted({*CRITERIA, FOCUS_CRITERION}))

# 見送りの種類（値 → 返信の括弧に入る名前）。「書かないもの」と 1 対 1（設計の決定 6）
WAIVE_KINDS = {
    "wording": "字句や言い回しの修正",
    "unlikely": "まず起きない条件での異常処理",
    "doc_mismatch": "実装に影響しない文書の食い違い",
    "alignment": "番号や表記の揃え",
    "preference": "好みの設計",
}
NOT_WRITTEN = "・".join(
    ("字句や言い回し", "まず起きない条件での異常処理", "実装に影響しない文書どうしの食い違い", "番号や表記の揃え", "好みの設計")
)
REPLY = "直しません。この指摘は、利用者が実際に使って困る不具合ではないためです（{inner}）。使って困る場面が出たら、そのときに直します。"
REPLY_FOCUS = "。レビューの重点（{names}）にも当たりません"
INNER = "{inner}"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_NUMBER_RE = re.compile(r"^[1-9][0-9]*$")
PROFILE_KEYS = ("criteria", "not_written", "waive_kinds", "reply", "categories", "replace_auto_categories")


class Focus(NamedTuple):
    """重点の宣言の読み取り結果。`none` と `unreadable` では `names` が空。"""

    status: str  # declared | none | unreadable
    names: tuple[str, ...] = ()
    error: str | None = None


NO_FOCUS = Focus("none")


class Profile(NamedTuple):
    """レビューのプロファイル（#1910）。書かなかった鍵は `None` で既定を使う。`none` と `unreadable` では鍵がすべて既定。"""

    status: str  # declared | none | unreadable
    criteria: dict | None = None  # 番号（int）→ 文
    not_written: tuple[str, ...] | None = None
    waive_kinds: dict | None = None  # 種類 → 名前
    reply: str | None = None
    categories: dict | None = None  # 名前 → (glob の組, 観点の文)
    replace_auto_categories: bool = False
    error: str | None = None

    def record(self) -> dict:
        """状態ファイル・振り分けの JSON へ写す形（宣言の `profile` と同じ形。書いた鍵だけ）。"""
        out: dict = {}
        if self.criteria is not None:
            out["criteria"] = {str(n): t for n, t in self.criteria.items()}
        if self.not_written is not None:
            out["not_written"] = list(self.not_written)
        if self.waive_kinds is not None:
            out["waive_kinds"] = dict(self.waive_kinds)
        if self.reply is not None:
            out["reply"] = self.reply
        if self.categories is not None:
            out["categories"] = {n: {"paths": list(g), "instructions": i} for n, (g, i) in self.categories.items()}
        if self.replace_auto_categories:
            out["replace_auto_categories"] = True
        return out


NO_PROFILE = Profile("none")


class ReviewDecl(NamedTuple):
    """宣言の読み取り結果。重点とプロファイルは独立に検査する（#1910 の決定 4）。"""

    focus: Focus
    profile: Profile


def _text(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _parse_focus(focus) -> Focus | None:
    """`focus` の配列を読む。形が違えば None。"""
    if not isinstance(focus, list) or not all(_text(x) for x in focus):
        return None
    return Focus("declared", tuple(x.strip() for x in focus)) if focus else NO_FOCUS


def _parse_criteria(v):
    if not isinstance(v, dict) or not v:
        return None, "criteria は番号の文字列 → 文のオブジェクトで、1 件以上書く"
    bad = [k for k in v if not (isinstance(k, str) and _NUMBER_RE.match(k)) or not _text(v[k])]
    if bad:
        return None, f"criteria の鍵は 1 以上の整数、値は空でない文で書く: {', '.join(map(str, bad))}"
    taken = sorted({int(k) for k in v} & {FIXED_CRITERION, FOCUS_CRITERION})
    if taken:
        return None, f"criteria は番号 {FIXED_CRITERION}（外せない基準）と {FOCUS_CRITERION}（重点）を使えない: {taken}"
    return {int(k): v[k].strip() for k in sorted(v, key=int)}, None


def _parse_not_written(v):
    if not isinstance(v, list) or not all(_text(x) for x in v):
        return None, "not_written は空でない文字列の配列で書く"
    return tuple(x.strip() for x in v), None


def _parse_waive_kinds(v):
    if not isinstance(v, dict) or not v or not all(isinstance(k, str) and _NAME_RE.match(k) and _text(n) for k, n in v.items()):
        return None, "waive_kinds は種類（英小文字・数字・_）→ 空でない名前のオブジェクトで、1 件以上書く"
    return {k: n.strip() for k, n in v.items()}, None


def _parse_reply(v):
    if not isinstance(v, str) or v.count(INNER) != 1:
        return None, f"reply は {INNER} をちょうど 1 回含む文字列で書く"
    return v, None


def _parse_categories(v):
    if not isinstance(v, dict):
        return None, "categories は名前 → {paths, instructions} のオブジェクトで書く"
    out = {}
    for name, c in v.items():
        paths = c.get("paths") if isinstance(c, dict) else None
        if (
            not (isinstance(name, str) and _NAME_RE.match(name))
            or not isinstance(paths, list)
            or not paths
            or not all(_text(p) for p in paths)
            or not _text(c.get("instructions"))
        ):
            return None, f"categories の {name!r} は英小文字の名前で、paths（空でない glob の配列）と instructions（空でない文）を持つ"
        out[name] = (tuple(p.strip() for p in paths), c["instructions"].strip())
    return out, None


def _parse_replace(v):
    if not isinstance(v, bool):
        return None, "replace_auto_categories は真偽値で書く"
    return v, None


_PARSERS = {
    "criteria": _parse_criteria,
    "not_written": _parse_not_written,
    "waive_kinds": _parse_waive_kinds,
    "reply": _parse_reply,
    "categories": _parse_categories,
    "replace_auto_categories": _parse_replace,
}


def _parse_profile(raw) -> Profile:
    """`profile` の値を検査する。どれか 1 つでも形が違えば丸ごと `unreadable`（I4）。"""
    if raw is None or raw == {}:
        return NO_PROFILE
    if not isinstance(raw, dict):
        return Profile("unreadable", error="profile はオブジェクトで書く")
    unknown = [k for k in raw if k not in _PARSERS]
    if unknown:
        return Profile("unreadable", error=f"profile に知らない鍵がある: {', '.join(map(str, unknown))}（{'/'.join(PROFILE_KEYS)}）")
    values = {}
    for key, value in raw.items():
        parsed, err = _PARSERS[key](value)
        if err:
            return Profile("unreadable", error=err)
        values[key] = parsed
    return Profile("declared", **values)


def load_review_decl(root) -> ReviewDecl:
    """`<root>/.ndf/review.json` を 1 回読み、重点とプロファイルを返す。例外を上げない。"""
    path = Path(root).joinpath(*DECL_PATH)
    try:
        if not path.exists():
            return ReviewDecl(NO_FOCUS, NO_PROFILE)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return ReviewDecl(Focus("unreadable", (), f"{path} を読めない: {e}"), NO_PROFILE)
    version = data.get("version") if isinstance(data, dict) else None
    shape = f'{path} の形が違う（{{"version": 1, "focus": ["..."]}} を書く）'
    if isinstance(version, bool) or version not in (1, 2):
        return ReviewDecl(Focus("unreadable", (), shape), NO_PROFILE)
    if version == 1:
        focus = _parse_focus(data.get("focus"))
        return ReviewDecl(focus if focus is not None else Focus("unreadable", (), shape), NO_PROFILE)
    focus = _parse_focus(data["focus"]) if "focus" in data else NO_FOCUS
    if focus is None:
        focus = Focus("unreadable", (), f"{path} の focus の形が違う（空でない文字列の配列で書く）")
    profile = _parse_profile(data.get("profile"))
    if profile.status == "unreadable":
        profile = profile._replace(error=f"{path} の profile: {profile.error}")
    return ReviewDecl(focus, profile)


def load_focus(root) -> Focus:
    """`<root>/.ndf/review.json` の重点だけを読む。例外を上げない。"""
    return load_review_decl(root).focus


def focus_from(status, names, error=None) -> Focus:
    """状態ファイル・振り分けの JSON に写した値から `Focus` を組み直す（形が違えば宣言なし）。"""
    names = tuple(x for x in (names or []) if isinstance(x, str) and x.strip())
    if status == "declared" and names:
        return Focus("declared", names)
    if status == "unreadable":
        return Focus("unreadable", (), error if isinstance(error, str) else None)
    return NO_FOCUS


def profile_from(record) -> Profile:
    """状態ファイル・振り分けの JSON に写したプロファイル（`Profile.record` の形）から組み直す。形が違えば `NO_PROFILE`。"""
    if not isinstance(record, dict) or not record:
        return NO_PROFILE
    profile = _parse_profile(record)
    return profile if profile.status == "declared" else NO_PROFILE


def waive_kinds(profile: Profile = NO_PROFILE) -> dict:
    """見送りの種類 → 名前。"""
    return profile.waive_kinds if profile.waive_kinds is not None else WAIVE_KINDS


def criterion_numbers(profile: Profile = NO_PROFILE) -> tuple[int, ...]:
    """振り分けの `criterion` に書ける番号（昇順）。3 は重点の基準として常に予約し、使えるかは重点の有無で別に見る。"""
    if profile.criteria is None:
        return CRITERION_NUMBERS
    return tuple(sorted({*profile.criteria, FIXED_CRITERION, FOCUS_CRITERION}))


def profile_categories(profile: Profile, paths) -> list[tuple[str, str]]:
    """変更ファイルのパスに当たったプロファイルの分類の (名前, 観点の文)。宣言の順。glob は `.ndf/pace.json` と同じ規則（決定 2）。"""
    if not profile.categories:
        return []
    import pace

    paths = [p for p in paths or () if isinstance(p, str)]
    return [(name, text) for name, (globs, text) in profile.categories.items() if any(pace.matches(p, globs) for p in paths)]


def _criteria_lines(focus: Focus, profile: Profile = NO_PROFILE) -> list[str]:
    items = dict(profile.criteria) if profile.criteria is not None else {n: CRITERIA[n] for n in (1, 4)}
    items[FIXED_CRITERION] = CRITERIA[FIXED_CRITERION]
    if focus.status == "declared" and focus.names:
        items[FOCUS_CRITERION] = f"{FOCUS_LABEL}: {' / '.join(focus.names)}"
    return [f"{n}. {items[n]}" for n in sorted(items)]


def _not_written(profile: Profile) -> str | None:
    """「書かないもの」の文。プロファイルの `not_written` が `[]` なら None（行を出さない）。"""
    if profile.not_written is None:
        return NOT_WRITTEN
    return "・".join(profile.not_written) or None


def _mvv_text(mvv) -> str:
    """MVV の節（`project_mvv.ProjectMvv` か、状態に写した節の文字列）。省けば空。"""
    if mvv is None:
        return ""
    return mvv if isinstance(mvv, str) else project_mvv.block(mvv)


def reviewer_block(focus: Focus = NO_FOCUS, mvv=None, profile: Profile = NO_PROFILE) -> str:
    """レビュー担当への「指摘の基準」の節。宣言があるときだけ基準 3 の行を持つ。`mvv` を渡すと MVV の節を後ろへ足す。"""
    not_written = _not_written(profile)
    base = "\n".join(
        [
            "## 指摘の基準",
            "次のどれかに当たるものだけを、重要度 `critical` か `major` で書く（`minor` / `nit` は使わない）。"
            "上のレビュー観点は探す場所で、書く基準はここである。"
            "見つけたものはこのラウンドですべて書く。",
            *_criteria_lines(focus, profile),
            *([f"書かないもの: {not_written}"] if not_written else []),
        ]
    )
    extra = _mvv_text(mvv)
    return f"{base}\n\n{extra}".rstrip() if extra else base


MVV_FIXER = (
    "振り分けの各指摘の `mvv_basis` に、直す・見送る判断の根拠にした MVV の項目の番号（`Value 3` / `C4` / `P1` など）を並べる。"
    "返信の末尾の根拠の句は `finalize` が足す。"
)


def fixer_block(focus: Focus = NO_FOCUS, mvv=None, profile: Profile = NO_PROFILE) -> str:
    """修正担当への「指摘の基準」の節。基準・書かないもの・見送りの種類・返信の雛形。`mvv` を渡すと MVV の節を後ろへ足す。
    プロファイルがあるときは、見送りの句をコードに限らない語（「対象のファイル」）で書く（#1910 の決定 3）。"""
    kinds = waive_kinds(profile)
    example = "doc_mismatch" if "doc_mismatch" in kinds else next(iter(kinds))
    target = "対象のファイル" if profile.status == "declared" else "コード"
    not_written = _not_written(profile)
    base = "\n".join(
        [
            "## 指摘の基準",
            "",
            "届いた重要度のラベルによらず、指摘ごとに次のどれかに当たるかを判定し直す。",
            "当たるものは `critical` / `major` とし、`criterion` に番号を書いて直す（`fixed`）。",
            f'当たらないものは `minor` / `nit` とし、{target}を変えずに `decision: "waived"` と `waive_kind` を書く'
            "（`criterion` は書かない）。",
            "",
            *_criteria_lines(focus, profile),
            "",
            *([f"書かないもの（見送る）: {not_written}", ""] if not_written else []),
            "見送りの種類（`waive_kind`）:",
            *[f"- `{k}`: {v}" for k, v in kinds.items()],
            "",
            "見送りの返信は `finalize` が雛形から組む:",
            "",
            "> " + waiver_reply(example, focus.names if focus.status == "declared" else (), profile=profile),
        ]
    )
    extra = _mvv_text(mvv)
    return f"{base}\n\n{extra.rstrip()}\n\n{MVV_FIXER}" if extra else base


def waiver_reply(kind: str, names=(), basis: str | None = None, profile: Profile = NO_PROFILE) -> str:
    """見送りの返信の本文。既定の雛形では、重点の名前があるときだけ重点の句を足す（プロファイルの雛形には足さない。決定 8）。
    `basis`（`project_mvv.basis_phrase` の句。例: 「根拠: Value 1（MVV 版 1）」）を渡すと末尾に足す。不明な種類は ValueError。"""
    kinds = waive_kinds(profile)
    if kind not in kinds:
        raise ValueError(f"見送りの種類が {'/'.join(kinds)} のどれでもない: {kind!r}")
    inner = kinds[kind]
    if profile.reply is not None:
        reply = profile.reply.replace(INNER, inner)
    else:
        names = [n for n in (names or ()) if isinstance(n, str) and n.strip()]
        if names:
            inner += REPLY_FOCUS.format(names=" / ".join(names))
        reply = REPLY.format(inner=inner)
    return f"{reply}{basis}" if basis else reply


def mvv_context(state_path, root_of) -> tuple[dict, str]:
    """(プロジェクト MVV の参照, MVV の節)。cross-review の状態ファイル（`state_path`）に写したものがあればそれ、無ければ
    `root_of()` の作業ツリーの宣言を読む（#1366）。修正担当の文脈（`fix-steps.py context`）が使う。"""
    try:
        crit = json.loads(Path(state_path).read_text(encoding="utf-8")).get("review_criteria") if state_path else None
    except (OSError, ValueError, AttributeError):
        crit = None
    if isinstance(crit, dict) and isinstance(crit.get("project_mvv"), dict) and crit.get("mvv_block"):
        return crit["project_mvv"], crit["mvv_block"]
    mvv = project_mvv.load_mvv(root_of())
    return project_mvv.record(mvv), project_mvv.block(mvv)


def context_decl(state_path, root_of) -> ReviewDecl:
    """(重点, プロファイル)。cross-review の状態ファイル（`state_path`）の `review_criteria` があればその写し（I8・#1910）、
    無ければ `root_of()` の作業ツリーの宣言を読み、プロファイルが読めなければ理由を標準エラーへ出す。`fix-steps.py context` が使う。"""
    try:
        crit = json.loads(Path(state_path).read_text(encoding="utf-8")).get("review_criteria") if state_path else None
    except (OSError, ValueError, AttributeError):
        crit = None
    if isinstance(crit, dict):
        return ReviewDecl(focus_from(crit.get("status"), crit.get("focus"), crit.get("error")), state_profile(crit))
    decl = load_review_decl(root_of())
    if decl.profile.status == "unreadable":
        print(PROFILE_WARNING.format(error=decl.profile.error), file=sys.stderr)
    return decl


def apply_basis(decisions: dict):
    """振り分けの各指摘の `mvv_basis` を正規化し（I7。返されなければ「根拠なし」か「MVV なし」で、止めない）、MVV を返す。"""
    mvv = project_mvv.from_record(decisions.get("project_mvv"))
    for e in decisions.get("decisions") or []:
        if isinstance(e, dict):
            e["mvv_basis"] = project_mvv.basis(e.get("mvv_basis"), mvv)
    return mvv


def as_state(focus: Focus, mvv=None, profile: Profile = NO_PROFILE) -> dict:
    """状態ファイルの `review_criteria` の形。`mvv`（`project_mvv.ProjectMvv`）を渡すと、参照と MVV の節も写す。
    プロファイルが無い（`none`）ときは、プロファイルの項目を置かない（決定 9）。"""
    out = {
        "status": focus.status,
        "focus": list(focus.names),
        "error": focus.error,
        "reviewer_block": reviewer_block(focus, mvv, profile),
    }
    if mvv is not None:
        out.update(project_mvv=project_mvv.record(mvv), mvv_block=project_mvv.block(mvv))
    if profile.status == "declared":
        out.update(profile_status="declared", profile=profile.record())
    elif profile.status == "unreadable":
        out.update(profile_status="unreadable", profile_error=profile.error)
    return out


def state_profile(crit) -> Profile:
    """状態ファイルの `review_criteria` からプロファイルを組み直す（無い・読めなかったときは `NO_PROFILE`）。"""
    if not isinstance(crit, dict) or crit.get("profile_status") != "declared":
        return NO_PROFILE
    return profile_from(crit.get("profile"))


PROFILE_WARNING = "レビューのプロファイルを読めないため、既定の基準で続ける: {error}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("block", choices=("reviewer", "fixer"))
    ap.add_argument("--root", help="宣言を読む worktree の根（省くと宣言を読まない）")
    a = ap.parse_args(argv)
    focus, profile = load_review_decl(a.root) if a.root else ReviewDecl(NO_FOCUS, NO_PROFILE)
    if focus.status == "unreadable":
        print(f"レビューの重点の宣言を読めないため、基準 1・2・4 だけで続ける: {focus.error}", file=sys.stderr)
    if profile.status == "unreadable":
        print(PROFILE_WARNING.format(error=profile.error), file=sys.stderr)
    mvv = project_mvv.load_mvv(a.root) if a.root else None
    print(reviewer_block(focus, mvv, profile) if a.block == "reviewer" else fixer_block(focus, mvv, profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
