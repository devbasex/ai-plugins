"""参加者の決め方と、参加者ごとの結果の読み方（#1142 の C2）。"""
from __future__ import annotations

import argparse
import functools
from typing import Any

import review_lib  # noqa: E402
import assignment  # noqa: E402
import auth  # noqa: E402
import statefile  # noqa: E402
from review_lib import posts  # noqa: E402


# 再開で渡した引数の反映の表（#727 / #648 の決定 13）。**状態ファイルに載る引数は、
# この表のどちらかに必ず載る。** 載らないのは状態に載らない 3 つ（作業ツリー・観点・
# 追加指示のファイル）だけである。`replace` は状態へ書いて記録へ積み、`notify` は
# 状態と違うときだけ「反映しない」と知らせる。
REVIEW_RESUME_FIELDS = (
    statefile.ResumeField("max_rounds", "max_rounds", "replace"),
    statefile.ResumeField("rotate_after", "rotate_after", "replace"),
    statefile.ResumeField("only", "only", "replace"),
    statefile.ResumeField("verify_command", "verify_commands", "replace"),
    statefile.ResumeField("verify_exit_code", "verify_exit_codes", "replace"),
    statefile.ResumeField("host", "host", "notify"),
)

# 参加者を作り直す引数（決定 14）。どれかを渡した再開だけが確認をやり直す。
PARTICIPANT_ARGS = ("only", "include", "exclude", "require_all")


def _apply_resume_args_block(st: dict[str, Any], args: argparse.Namespace) -> bool:
    """再開で渡した引数を状態へ反映し、何か変えたら True を返す（#727 / #648）。

    担当に関わる引数（`--only` / `--include` / `--exclude` / `--require-all`）を渡した
    ときだけ、**渡さなかった引数を状態ファイルの値で補って**使える者の解決をやり直す
    （決定 14）。作り直しの失敗は状態を書き換える前に起きる（`_resolve_reviewers` を
    先に呼び、通ってから `st` を書く）。
    """
    only, include, exclude = _normalize_participant_args(args)
    before = len(st.get("resume_changes") or [])

    # **`--only none` はここで処理する。** 正規化した `None` を表へ渡すと「未指定」と
    # 区別できず、指定を外す操作が黙って捨てられる（決定 15）。
    args_copy = argparse.Namespace(**vars(args))
    args_copy.only = only
    if getattr(args, "only", None) == NONE_WORD:
        args_copy.only = None
        if st.get("only") is not None:
            old = st.get("only")
            st["only"] = None
            st.setdefault("resume_changes", []).append(
                {"at": statefile.now(), "field": "only", "from": old, "to": None})
            review_lib.info(f"↻ only: {old} → None")

    for line in statefile.apply_resume_args(st, args_copy, REVIEW_RESUME_FIELDS):
        review_lib.info(line)

    if any(getattr(args, name, None) is not None for name in PARTICIPANT_ARGS):
        old_participants = st.get("participants")
        recorded = old_participants or {}
        try:
            host = st.get("host") or assignment.detect_host(getattr(args, "host", None))[0]
        except assignment.AssignmentError as e:
            review_lib.die(str(e), code=1)
            raise
        include_eff = include if include is not None else list(recorded.get("included") or [])
        rebuild = argparse.Namespace(
            only=st.get("only"),
            include=include_eff,
            exclude=(exclude if exclude is not None
                     else assignment.recorded_exclusions(recorded, include_eff, st.get("only"))),
            require_all=(args.require_all if getattr(args, "require_all", None) is not None
                         else bool(recorded.get("require_all"))),
        )
        participants = _resolve_reviewers(host, rebuild)
        st["participants"] = participants
        st.setdefault("resume_changes", []).append(
            {"at": statefile.now(), "field": "participants",
             "from": old_participants, "to": participants})

    return len(st.get("resume_changes") or []) > before


# **母集合を広げる前からある 2 者。** `host` を持たない状態ファイル（このリポジトリの
# 版が上がる前に始めた実行）は、この 2 者を担当として読む。中断した実行を再開したときに
# 担当が入れ替わると、前のラウンドの記録と突き合わせられなくなる。
LEGACY_AGENTS = ("codex", "agy")
# 互換のための別名。既存の呼び出し側はこの名前を使い続けてよい。
AGENTS = LEGACY_AGENTS


def _round_reviewers(st: dict[str, Any], round_no: int) -> list[str]:
    """そのラウンドのレビュー担当（席の名前）を返す。

    **先に当たったものを採る**（設計の決定 11）。ラウンドの記録を 1 者指定より先に
    見るのは、再開で 1 者指定を変えても過去のラウンドの担当が変わらないようにする
    ためである。

    | 順 | 状態 | 返る担当 |
    | ---: | --- | --- |
    | 1 | ラウンドに `reviewers` がある | その値 |
    | 2 | `only` がある | `[only]` |
    | 3 | `participants` がある | `assignment.review_seats(round_no, available, fallback)` |
    | 4 | `host` がある | `assignment.review_seats(round_no, 全ランタイム − ホスト, [])`（#892 の前の母集合。変更前の輪番と同じ値） |
    | 5 | どれも無い（古い状態ファイル） | `LEGACY_AGENTS` |
    """
    for entry in st.get("rounds") or []:
        if entry.get("round") == round_no and entry.get("reviewers"):
            return list(entry["reviewers"])
    # **`--only` は担当そのものを絞る。** 輪番が返す 2 者を担当のまま残すと、指定した
    # 1 者が含まれないラウンドで誰も起動されない。そのとき全員が「指定によるスキップ」
    # として扱われ、レビューが行われていないのに収束する。
    only = st.get("only")
    if only:
        return [only]
    participants = st.get("participants")
    if participants:
        return assignment.review_seats(
            max(round_no, 1),
            list(participants.get("available") or []),
            list(participants.get("fallback") or []),
        )
    host = st.get("host")
    if host:
        # **`default_pool(host)` を呼ばない。** `host` だけを持つ状態ファイルの担当は、
        # ホストを除く全ランタイムから選んでいた。その担当を保つため、この母集合を式で持つ。
        return assignment.review_seats(
            max(round_no, 1), [r for r in assignment.ALL_RUNTIMES if r != host], [])
    return list(LEGACY_AGENTS)


# ---------- 参加者の引数と使える者の解決（#727） ----------
#
# 名前のチェックは 2 段に分かれる。綴り（4 つの名前か `none`）は argparse の型が弾き
# （終了コード 2）、母集合との関係は共通層の `resolve_participants` が弾く（終了コード 1）。

NONE_WORD = "none"


def _normalize_participant_args(
    args: argparse.Namespace,
) -> tuple[str | None, list[str] | None, list[str] | None]:
    """`--only` / `--include` / `--exclude` を読み手の形へ直し `(only, include, exclude)` を返す。

    `only` の `none` は `None`。`include` / `exclude` は `action="append"` の入れ子を
    平らにし（`--exclude agy --exclude kiro` と `--exclude agy,kiro` が同じになる）、
    `none` を含めば `[]`。未指定は `None` のまま返す（再開の経路が「渡さなかった」と
    読むため）。`none` と名前の混在は終了コード 1。
    """
    only = getattr(args, "only", None)
    if only == NONE_WORD:
        only = None

    def _flatten(option: str) -> list[str] | None:
        raw = getattr(args, option, None)
        if raw is None:
            return None
        names: list[str] = []
        for group in raw:
            names.extend(group if isinstance(group, list) else [group])
        if NONE_WORD in names:
            if len(names) > 1:
                review_lib.die(f"--{option} に {NONE_WORD} と名前を同時に指定できません: {', '.join(names)}")
            return []
        return names

    return only, _flatten("include"), _flatten("exclude")


def _resolve_reviewers(host: str, args: argparse.Namespace) -> dict[str, Any]:
    """使える者を決め、状態ファイルの `participants`（`fallback` を含む 9 項目）を返す。

    母集合は `default_pool(host)`（claude / codex / kiro とホスト）。母集合に無い者の
    除外は止めずに無視し、`ℹ` の 1 行を出す（決定 2）。確認は止めない確認
    （`auth.probe_auth`）で、通らない者は外して続ける。**ホストを別に確かめて埋め合わせに
    使うことはしない**（ホストは既に母集合で確かめている）。`fallback` は常に空で、使える者が
    1 者なら `review_seats` が `<その者>-2` で席を埋める。名前の矛盾・`--require-all` で
    欠け・使える者が 0 者・1 者指定が確認を通らない、は終了コード 1（状態ファイルはこの
    関数の後に書かれるため作られない）。
    """
    only, include, exclude = _normalize_participant_args(args)
    probe = functools.partial(auth.probe_auth, info=review_lib.info)
    try:
        pool = assignment.default_pool(host)
        resolved = assignment.resolve_participants(
            pool, host=host, include=include or [], exclude=exclude or [], only=only,
            probe=probe, require_all=bool(getattr(args, "require_all", None)),
        )
    except assignment.AssignmentError as e:
        review_lib.die(str(e), code=1)
        raise
    available = resolved.available
    review_lib.info(f"ホスト: {host} / 母集合: {' / '.join(pool)}"
         f" / 使える者: {' / '.join(available) or 'なし'}")
    if resolved.ignored_exclude:
        review_lib.info(f"ℹ --exclude {','.join(resolved.ignored_exclude)} は既定の母集合に無いため"
             f"無視しました（母集合: {', '.join(pool)}）")
    for name, reason in resolved.unavailable.items():
        review_lib.info(f"⚠ {name} を担当から外しました（{reason}）")

    fallback: list[str] = []
    # **1 者指定でも 0 者は通さない。** 指定した 1 者が確認を通らないと使える者が空に
    # なるが、席は 1 者指定をそのまま返す（`_round_reviewers` の順 2）。確認を通らない
    # 担当が席に座ると、レビューが行われないまま収束する。埋め合わせは 1 者指定では
    # 行わないため（決定 9）、ここで止めるほかにない。
    if only is not None and not available:
        review_lib.die(f"1 者指定の {only} が確認を通りません"
            f"（{resolved.unavailable.get(only, '')}）。"
            f"{only} で認証し直すか、1 者指定を外して再実行してください", code=1)
    if only is None and not available:
        review_lib.die(f"使える者がいません: 母集合 {' / '.join(pool)} の全員が確認を通りません", code=1)
    if only is None and len(available) == 1:
        review_lib.info("⚠ 使える者が 1 者のため、席を同じランタイムの 2 つ目で埋めます（観点が減ります）")

    state = resolved.to_state()
    state["fallback"] = fallback
    return state


def _is_pass(intent: str | None, severity: dict[str, int] | None) -> bool:
    """1 レビュアー分の判定が pass かどうか。

    pass は `APPROVE` と、重大な指摘が無い `COMMENT` だけである。指定によるスキップ
    （`--only`）は `_round_passes` が短絡して扱うため、ここへは届かない。結果を残さな
    かったレビュアー（`NO_RESULT`）は pass にしない。レビューが行われていないのに、
    行われて承認されたのと同じ出口へ進むためである。
    """
    if intent == "APPROVE":
        return True
    if intent == "COMMENT":
        sev = severity or {}
        return sev.get("critical", 0) == 0 and sev.get("major", 0) == 0
    return False


def _skipped_by_only(agent: str, only: str | None) -> bool:
    """`--only` の指定でそのレビュアーを起動しなかったかどうか。"""
    return bool(only) and only != agent


def _agent_intent(round_entry: dict[str, Any], agent: str, only: str | None) -> str:
    """そのラウンドに記録されたレビュアーの判定を読む。

    記録が無いときの既定値は、指定によるスキップなら `SKIP`、そうでなければ
    `NO_RESULT` になる。**この 2 つは別の事象である。**
    """
    entry = round_entry.get(agent) or {}
    default = "SKIP" if _skipped_by_only(agent, only) else posts.NO_RESULT
    return entry.get("intent") or default


def _no_result_agents(
    round_entry: dict[str, Any], only: str | None,
    reviewers: list[str] | None = None,
) -> list[str]:
    """そのラウンドで、起動したのに使える結果が残らなかったレビュアーを返す。"""
    return [
        a
        for a in (reviewers or AGENTS)
        if not _skipped_by_only(a, only) and _agent_intent(round_entry, a, only) == posts.NO_RESULT
    ]


def _round_passes(
    round_entry: dict[str, Any], only: str | None,
    reviewers: list[str] | None = None,
) -> bool:
    """そのラウンドで新しく投稿された指摘だけを見た pass 判定。

    引き継いだ指摘はここでは見ない（`cmd_judge` が別に扱う）。
    """
    for agent in (reviewers or AGENTS):
        if _skipped_by_only(agent, only):
            continue
        entry = round_entry.get(agent) or {}
        if not _is_pass(_agent_intent(round_entry, agent, only), entry.get("by_severity")):
            return False
    return True
