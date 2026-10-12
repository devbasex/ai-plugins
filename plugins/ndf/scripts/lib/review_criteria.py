#!/usr/bin/env python3
"""review_criteria.py: 指摘の基準の正本と、レビューの宣言（`.ndf/review.json`）の読み取り（#1287・#1910・#1942）。

レビュー担当への指示（cross-review の `launch-reviewer.sh`）・修正担当の文脈のファイル（`fix-steps.py context`）・
見送りの返信（`fix-steps.py finalize`）は、どれもここの定数と、宣言のプロファイルから組む。プロファイルの形の検査は
`review_criteria_decl.py` にある。標準ライブラリだけで書く。

    review_criteria.py reviewer [--root <worktree>] [--changed-file <パス> ...]   レビュー担当への節を標準出力へ
    review_criteria.py fixer    [--root <worktree>] [--changed-file <パス> ...]   修正担当への節を標準出力へ

`--root` を省くと宣言を読まない既定の節（基準 3 の無い形）を出す。重点やプロファイルが読めないときは理由を標準エラーへ出し、
終了コードは 0 のまま既定の節で続ける。`--root` を渡すと、節の後ろへ MVV の節（NDF の共通原則 → プロジェクト MVV →
判断の決まり。`project_mvv.block`・#1366）を足す。`--changed-file` は `version: 3` でプロファイルを選ぶパス（省けば空の列）。

宣言の形（`version: 1` はほかの鍵を無視する。`version: 2` では `focus` も `profile` も任意）:

    {"version": 1, "focus": ["<重点の名前>", ...]}
    {"version": 2, "focus": [...], "profile": {"criteria": {"1": "..."}, "not_written": ["..."],
     "waive_kinds": {"<種類>": "<名前>"}, "reply": "...{inner}...",
     "categories": {"<名前>": {"paths": ["<glob>"], "instructions": "..."}}, "replace_auto_categories": false}}
    {"version": 3, "focus": [...], "criteria": {...}, "not_written": [...],
     "profiles": [{"name": "<名前>", "paths": ["<glob>"], <profile と同じ鍵>}, ...]}（`select_profile`。最上位は全プロファイルに足す）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import pace  # noqa: E402
import project_mvv  # noqa: E402
from review_criteria_decl import FIXED_CRITERION, FOCUS_CRITERION, INNER, NO_PROFILE, PROFILE_KEYS, Profile  # noqa: E402,F401
from review_criteria_decl import _parse_profile, _parse_profiles, _text  # noqa: E402

DECL_PATH = (".ndf", "review.json")

# 基準の番号は、重点の宣言が無くても詰めない（設計の決定 4）。3 は宣言があるときだけ現れる
CRITERIA = {
    1: "利用者が普通に使う経路で、誤動作する・止まる・データを壊す",
    2: "秘密・認証認可・利用者のデータ・戻せない操作に触れる（起きる確率によらず書く）",
    4: "設計の文書で、実装する人が違うものを作ってしまう食い違い",
}
# プロファイルが基準を書かないときの組（外せない基準 2 を除く）
DEFAULT_CRITERIA = {n: t for n, t in CRITERIA.items() if n != FIXED_CRITERION}
FOCUS_LABEL = "このプロジェクトのレビューの重点に当たる"
CRITERION_NUMBERS = tuple(sorted({*CRITERIA, FOCUS_CRITERION}))

# 見送りの種類（値 → 返信の括弧に入る名前）。「書かないもの」と 1 対 1（設計の決定 6）
WAIVE_KINDS = {
    "wording": "字句や言い回しの修正",
    "unlikely": "まず起きない条件での異常処理",
    "doc_mismatch": "実装に影響しない文書の食い違い",
    "alignment": "番号や表記の揃え",
    "preference": "好みの設計",
}
NOT_WRITTEN = "字句や言い回し・まず起きない条件での異常処理・実装に影響しない文書どうしの食い違い・番号や表記の揃え・好みの設計"
REPLY = "直しません。この指摘は、利用者が実際に使って困る不具合ではないためです（{inner}）。使って困る場面が出たら、そのときに直します。"
REPLY_FOCUS = "。レビューの重点（{names}）にも当たりません"


class Focus(NamedTuple):
    """重点の宣言の読み取り結果。`none` と `unreadable` では `names` が空。"""

    status: str  # declared | none | unreadable
    names: tuple[str, ...] = ()
    error: str | None = None


NO_FOCUS = Focus("none")


def _parse_focus(focus) -> Focus | None:
    """`focus` の配列を読む。形が違えば None。"""
    if not isinstance(focus, list) or not all(_text(x) for x in focus):
        return None
    return Focus("declared", tuple(x.strip() for x in focus)) if focus else NO_FOCUS


class ReviewDecl(NamedTuple):
    """宣言の読み取り結果。重点とプロファイルは独立に検査する（#1910 の決定 4）。`profiles` は `version: 3` の候補、
    `shared` は最上位の `criteria`・`not_written`、`others` は選ばなかったが当たった名前（#1942）。"""

    focus: Focus
    profile: Profile
    profiles: tuple[Profile, ...] | None = None
    shared: Profile | None = None
    others: tuple[str, ...] = ()


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
    if isinstance(version, bool) or version not in (1, 2, 3):
        return ReviewDecl(Focus("unreadable", (), shape), NO_PROFILE)
    if version == 1:
        focus = _parse_focus(data.get("focus"))
        return ReviewDecl(focus if focus is not None else Focus("unreadable", (), shape), NO_PROFILE)
    focus = _parse_focus(data["focus"]) if "focus" in data else NO_FOCUS
    if focus is None:
        focus = Focus("unreadable", (), f"{path} の focus の形が違う（空でない文字列の配列で書く）")
    if version == 3:
        profiles, err = (None, "profile は書かず、profiles に移す") if "profile" in data else _parse_profiles(data.get("profiles"))
        shared, err = _parse_shared(data, profiles) if profiles is not None else (None, err)
        broken = Profile("unreadable", name="", error=f"{path} の {err}")
        return ReviewDecl(focus, broken, ()) if err else ReviewDecl(focus, Profile("none", name=""), profiles, shared)
    profile = _parse_profile(data.get("profile"))
    if profile.status == "unreadable":
        profile = profile._replace(error=f"{path} の profile: {profile.error}")
    return ReviewDecl(focus, profile)


def _parse_shared(data: dict, profiles) -> tuple[Profile | None, str | None]:
    """`version: 3` の最上位の `criteria`・`not_written`（宣言の側で外せない基準。#1942 の決定 7）。無ければ (None, None)。
    `criteria` の番号が、選びうる組（各プロファイルの `criteria`、無ければ既定の 1・4）と重なれば壊れている。"""
    shared = _parse_profile({k: data[k] for k in ("criteria", "not_written") if k in data} or None)
    if shared.status == "unreadable":
        return None, f"最上位の {shared.error}"
    default = (None,) if not profiles or profiles[-1].paths is not None else ()
    taken = {n for p in profiles for n in (p.criteria or {})} | {
        n for p in (*profiles, *default) if not p or p.criteria is None for n in DEFAULT_CRITERIA
    }
    overlap = sorted(set(shared.criteria or ()) & taken)
    if overlap:
        return None, f"最上位の criteria の番号がプロファイルか既定の基準と重なる: {overlap}"
    return (shared if shared.status == "declared" else None), None


def _with_shared(profile: Profile, shared: Profile | None) -> Profile:
    """選んだプロファイル（選ばなかったときは既定の組）へ最上位の基準を足す。`not_written` は最上位 → プロファイルの順の和。"""
    if shared is None:
        return profile
    criteria = profile.criteria if profile.criteria is not None else DEFAULT_CRITERIA
    own = profile.not_written if profile.not_written is not None else tuple(NOT_WRITTEN.split("・"))
    nw = profile.not_written if shared.not_written is None else tuple(dict.fromkeys((*shared.not_written, *own)))
    merged = dict(sorted({**criteria, **shared.criteria}.items())) if shared.criteria else profile.criteria
    return profile._replace(status="declared", criteria=merged, not_written=nw)


def select_profile(decl: ReviewDecl, paths) -> tuple[ReviewDecl, tuple[str, ...]]:
    """(選んだプロファイルを `profile` に置いた宣言, 選ばなかったが `paths` が当たったプロファイルの名前)（#1942 の I2）。
    `profiles` の先頭から見て `paths` が当たる最初のもの（`paths` が無ければ当たる）に最上位の基準を足す。
    名前は宣言の `others` にも置く。`version: 1`・`2` と壊れた宣言はそのまま返す。"""
    if decl.profiles is None or decl.profile.status == "unreadable":
        return decl, ()
    paths = [p for p in paths or () if isinstance(p, str)]
    hit = lambda p: p.paths is None or any(pace.matches(x, p.paths) for x in paths)  # noqa: E731
    chosen = next((p for p in decl.profiles if hit(p)), None)
    rest = decl.profiles[decl.profiles.index(chosen) + 1 :] if chosen else ()
    others = tuple(p.name for p in rest if p.paths is not None and hit(p))
    return decl._replace(profile=_with_shared(chosen or Profile("none", name=""), decl.shared), others=others), others


def multi_profile_notice(profile: Profile, others) -> str:
    """選ばなかったプロファイル（`others`）にも当たったときの知らせの文。"""
    return f"⚠️ 変更ファイルが複数のプロファイル（{', '.join((profile.name or '', *others))}）に当たるため、先に書いた {profile.name} を使う"


def select_and_notify(decl: ReviewDecl, paths, notify) -> ReviewDecl:
    """`select_profile` で選んだ宣言を返す。選ばなかったプロファイルにも当たったときは `notify` へ知らせの文を渡す。"""
    decl, others = select_profile(decl, paths)
    if others:
        notify(multi_profile_notice(decl.profile, others))
    return decl


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
    paths = [p for p in paths or () if isinstance(p, str)]
    return [(name, text) for name, (globs, text) in profile.categories.items() if any(pace.matches(p, globs) for p in paths)]


def _criteria_lines(focus: Focus, profile: Profile = NO_PROFILE) -> list[str]:
    items = dict(profile.criteria if profile.criteria is not None else DEFAULT_CRITERIA)
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


def context_decl(state_path, root_of, paths_of=None) -> ReviewDecl:
    """重点とプロファイル。cross-review の状態ファイル（`state_path`）の `review_criteria` があればその写し（I8・#1910）、
    無ければ `root_of()` の作業ツリーの宣言を読み、`version: 3` のときだけ `paths_of()`（変更ファイルのパス）で選ぶ（#1942 の決定 6）。
    プロファイルが読めなければ理由を、複数に当たれば知らせを標準エラーへ出す。`fix-steps.py context` が使う。"""
    try:
        crit = json.loads(Path(state_path).read_text(encoding="utf-8")).get("review_criteria") if state_path else None
    except (OSError, ValueError, AttributeError):
        crit = None
    if isinstance(crit, dict):
        return ReviewDecl(focus_from(crit.get("status"), crit.get("focus"), crit.get("error")), state_profile(crit))
    decl = load_review_decl(root_of())
    if decl.profile.status == "unreadable":
        print(PROFILE_WARNING.format(error=decl.profile.error), file=sys.stderr)
    elif decl.profiles is not None:
        decl = select_and_notify(decl, paths_of() if paths_of else (), lambda m: print(m, file=sys.stderr))
    return decl


def apply_basis(decisions: dict):
    """振り分けの各指摘の `mvv_basis` を正規化し（I7。返されなければ「根拠なし」か「MVV なし」で、止めない）、MVV を返す。"""
    mvv = project_mvv.from_record(decisions.get("project_mvv"))
    for e in decisions.get("decisions") or []:
        if isinstance(e, dict):
            e["mvv_basis"] = project_mvv.basis(e.get("mvv_basis"), mvv)
    return mvv


def as_state(focus: Focus, mvv=None, profile: Profile = NO_PROFILE, others=()) -> dict:
    """状態ファイルの `review_criteria` の形。`mvv`（`project_mvv.ProjectMvv`）を渡すと、参照と MVV の節も写す。
    プロファイルが無い（`none`）ときは、プロファイルの項目を置かない（決定 9）。`profile_name` は `version: 3` だけに置き、
    ほかにも当たった名前（`others`）があるときだけ `profile_others` を置く（#1942）。"""
    out = {"status": focus.status, "focus": list(focus.names), "error": focus.error, "reviewer_block": reviewer_block(focus, mvv, profile)}
    if mvv is not None:
        out.update(project_mvv=project_mvv.record(mvv), mvv_block=project_mvv.block(mvv))
    if profile.status == "declared":
        out.update(profile_status="declared", profile=profile.record())
    elif profile.status == "unreadable":
        out.update(profile_status="unreadable", profile_error=profile.error)
    if profile.name is not None:
        out["profile_name"] = profile.name
    if others:
        out["profile_others"] = list(others)
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
    ap.add_argument("--changed-file", action="append", default=[], help="version 3 の宣言でプロファイルを選ぶ変更ファイル（繰り返し可）")
    a = ap.parse_args(argv)
    decl = load_review_decl(a.root) if a.root else ReviewDecl(NO_FOCUS, NO_PROFILE)
    decl = select_and_notify(decl, a.changed_file, lambda m: print(m, file=sys.stderr))
    focus, profile = decl.focus, decl.profile
    if focus.status == "unreadable":
        print(f"レビューの重点の宣言を読めないため、基準 1・2・4 だけで続ける: {focus.error}", file=sys.stderr)
    if profile.status == "unreadable":
        print(PROFILE_WARNING.format(error=profile.error), file=sys.stderr)
    mvv = project_mvv.load_mvv(a.root) if a.root else None
    print(reviewer_block(focus, mvv, profile) if a.block == "reviewer" else fixer_block(focus, mvv, profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
