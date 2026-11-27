"""収束ループの駆動（サブコマンド `ndf cross-review run`）。1 回の起動でラウンドを上限まで回す。

始める前に担当すべてを起動できるか（学習の設定のバックアップ）を確かめ、修正担当か名指し 1 者が断られれば `precondition` で止め、ほかの断られた相手役の席は claude で埋める（#3907・#3955）。席の起動と埋めは `seats` が持つ。

1 ラウンドは次の順に進む:

1. 範囲を決める（`scope_decide`。設計の PR は、詳細レビューを通らないセクションが残る間は全差分。#3181）。変わった
   Markdown が `cross-review.design-doc-lines` の行数を超えれば注意に載せる（止めない。#3941）。承認済みの MVV があれば、PR 全体で
   足した MVV の根拠の記述を機械で確かめ、誤りを NDF の指摘としてジャーナルへ入れる（`changes.citations`。解決も次の確かめだけが決める。#4076）
2. 相手役（`ndf.core.converge.participants`）を並べて起動し、最後の 1 行の `{"findings": [...]}` を読む。相手役へは
   指摘の基準と重要度（`criteria_decide`）と、PR に既にあるコメント（起動の始めに 1 回だけ読み、`prior_decide.others`・`relevant` で絞る。#3941）を渡す。サンドボックスが
   コマンドを打てなかった相手役（標準エラーの `bwrap:`）は読めなかったとして回答を指摘に入れない。読めなかった相手役は
   同じラウンドで 1 度だけ起動し直す。誰も読めなければ `precondition` で止める（#3893）。一部が読めなければ、その
   ラウンドを収束に数えず `incomplete_reviewers` で終える（#3941）。起動し直しても結果の無い席が claude 以外だけなら、その席を
   claude の読むだけの起動で埋め、2 者の指摘で判定する。埋めた席は以後のラウンドも座り続ける（`participants.fill_targets`。#3955）
3. 指摘をキー（`oscillation.finding_key`）で重ねずジャーナルへ入れる。見送り済みの指摘は出し直されても扱わない。このラウンドで
   新しく出たか戻った critical・major があれば、読むだけの claude（判定役。`verify`）を 1 回起動して起きるかを確かめ、
   成り立たないと分かった指摘を minor へ下げる（下げない指摘は `verify_decide`。#4363）
4. 収束の妨げになる重さ（`criteria_decide.blocking`）の未解決の指摘と、まだ決めていない足すべき内容（`--findings`。#2444）が
   無ければ収束。必ず人の承認が要る操作に当たる指摘（`mvv` が `P`・`C` の番号）は、修正担当へ渡さず、収束も止めず、
   人の承認を待つ指摘として残す（#4076）。前々ラウンドの指摘が戻っていれば振動で止める
5. 修正担当（claude）を起動し、修正の結果ファイル（`store.fix_result_path`）の指摘ごとの「直した」「見送った（理由）」を
   ジャーナルへ読み込む。修正のコミットを `push.push_fix` で送る（拒まれたら 1 回だけマージする。#2440）

相手役へ渡す資料と指示の組み立て、起動し直しと席の埋めは `reviewing` が持つ。判定役を起動するラウンドは、起動の直前に相手役の
結果を判定待ちのラウンド（`pending_review`）として保存し、ラウンドの記録を作る前に中断した打ち直しは、同じ対象なら相手役を
起動せずに 3 から続ける（#4363）。

状態ファイルへ rounds・reviewed_sections・ジャーナル（findings）をラウンドごとに書き、終わったら、最後のコミットの根拠の記述を
確かめてから `summary.json`（`finish_decide` が読む形）を書き、MVV に反するとした指摘を MVV の記録に残す（#4076）。始めに worktree の管理情報を確かめ（#3109）、origin の先頭へ合わせ（#2440）、
前の起動が残した修正の結果ファイルを読み込む（#2770）。別の保存先で始めた起動は、前の実行が PR に開いたまま残した
自分の指摘のスレッドと、最新の記録のコメントに残ったスレッドの無い未解決の指摘を引き継ぐ（`carry`。#3941）。最後のラウンドが収束で、その head と比較基点（`--base` と HEAD の
merge-base の SHA）と MVV とレビューの単位の指示が今と同じで、今回の相手役が全員そのラウンドを読んでいれば、相手役を起動せずに前回の結果を返す（#3941）。修正の結果は状態ファイルへ書いてから結果ファイルを
ラウンドの名前へ移し、送る（push が例外で止まっても、次の起動が記録から再開できる）。前の起動が送れなかった修正の
コミットが残っていれば、始めに送ってから進む。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import identity
from ndf.adapters import agents, git, publish, run_log
from ndf.contracts import internal, limits
from ndf.core.converge import oscillation, worktree
from ndf.domains.crossreview import (
    carry,
    changes,
    criteria_decide,
    fixer,
    fixgate_decide,
    prior_decide,
    report_decide,
    reviewed_pr,
    resume_decide,
    reviewing,
    round_scope,
    scope_decide,
    seats,
    settings,
    store,
    threads,
    verify,
    verify_decide,
)
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.fixer import Launcher
from ndf.domains.crossreview.journal import Checked, FindingJournal
from ndf.domains.crossreview.scope_decide import Filled
from ndf.domains.crossreview.settings import DriveStopped, RunConfig
from ndf.domains.records import jsonl, mvv
from ndf.domains.records import mvv_decide as md

# 終わったときの要約（`finish_decide` が読む）
SUMMARY_NAME = "summary.json"
# ラウンドの判定（指摘が無くなったラウンドは finish_decide.CONVERGED）
CHANGES_REQUIRED = fixgate_decide.CHANGES_REQUIRED
# PR に届いていないコミットの根拠の記述を確かめられなかった注意（PR へ載るので、原因は状態ファイルにだけ残す）
UNSENT_UNCHECKED = "PR に届いていない手元のコミットの根拠の記述を確かめられない（原因は状態ファイルの unsent_citations にある）"
# MVV の記録を書けなかった注意と、その原因を状態ファイルへ残せない（消せない）注意（PR へ載るので、保存先のパスを含む例外の文を入れない）
RECORDS_UNWRITTEN = f"MVV の記録を書けない（次の起動で書く。原因は状態ファイルの {store.MVV_RECORD_ERROR} にある）"
CAUSE_UNSAVED = f"状態ファイルの {store.MVV_RECORD_ERROR} を書き直せない（次の起動で書き直す）"
# 出た・消えたときに、指摘が同じでも PR の記録を書き直す注意
MVV_NOTICES = (RECORDS_UNWRITTEN, CAUSE_UNSAVED)
# 相手役へ渡す資料を置く、実行ごとのディレクトリの名前の頭（後ろは mkdtemp のランダムな文字列。#3955）
INPUT_PREFIX = "review-input-"
# 設計文書の行数の上限
DOC_LINES = "cross-review.design-doc-lines"


class Outcome(NamedTuple):
    """駆動の結果。`review` は `finish_decide.finish` へ渡す形、`process` は起動ごとの過程、`reviewed_head` は
    今回の起動で相手役の全員が読めた最後のラウンドの HEAD（最後のラウンドの修正のコミットは含まない。#3879。そのような
    ラウンドが無ければ None。一部の相手役が読めなかったラウンドの HEAD は出さない。PR 3944 の r2-3）。`reused` は
    相手役を起動せずに前回の収束を返したか（記録のコメントを書き直さない。PR 3944 の r2-10）。`mvv` は結果 JSON の `outputs.mvv`
    （承認済みのプロジェクト MVV が無ければ None。#4076）。`mvv_record_failed` は承認ゲートの判定に要る記録の行を書けなかったとき
    の、書けなかった行（`key`・`outcome`）と固定の注意（`finish_decide.finish` の `record_failed`。#3959）。`reviewed_pr` は
    `reviewed_head` を head に持つと確かめた、実際に読んだ PR（`<所有者>/<リポジトリ>#<番号>`。確かめられなければ None。#4369）。"""

    review: fin.Review
    summary_path: Path
    warnings: tuple[str, ...]
    process: tuple[dict[str, str], ...]
    reviewed_head: Optional[str] = None
    reused: bool = False
    mvv: Optional[dict[str, Any]] = None
    mvv_record_failed: Optional[dict[str, Any]] = None
    reviewed_pr: Optional[str] = None


@dataclass
class _Run:
    """1 回の起動の間に重ねる値（状態ファイルの中身・ジャーナル・注意・過程）。"""

    cfg: RunConfig
    g: git.Git
    ref: store.StateRef
    data: dict[str, Any]
    journal: FindingJournal
    warnings: list[str]
    process: list[dict[str, str]]
    history: list[frozenset[str]]
    # PR の既存のコメント（起動の始めに 1 回だけ読む。読めなければ空）
    remarks: Sequence[prior_decide.Remark] = ()
    # 起動の始めにベースブランチ（`--base` がコミットなら PR のマージ先）から読んだプロジェクト MVV（#4076）
    snapshot: Optional[mvv.Snapshot] = None
    # MVV を見分ける値と、MVV を読んだブランチ（ラウンドの記録に残す。#4076）
    mvv: Optional[str] = None
    mvv_branch: Optional[str] = None
    # PR に届いたと分かっている最後のコミット（始めの同期で取得した先頭か、この起動で送ったコミット）
    sent: str = ""
    # 始めに読めて、head ブランチとその先頭がレビューするブランチの取得した先頭と一致した PR
    read_pr: Optional[threads.ReadPr] = None
    # 取得と送信に使うリモート名・始めの同期で取得した先頭・この起動で送った（送り直した分を含む）コミット
    remote: str = ""
    synced_head: str = ""
    pushed: set[str] = field(default_factory=set)
    # PR が足した根拠の記述の確かめ（`changes.CitationRun`）
    checked_head: Optional[str] = None
    last_target: Optional[changes.Target] = None
    citations: dict[tuple[str, str, str], list[dict[str, Any]]] = field(default_factory=dict)
    # 結果と MVV の記録に残す自由記述を伏せる処理（PR に書く記録のコメントと同じもの）
    clean: report_decide.Cleaner = run_log.redact
    # HEAD の SHA から `--base` との merge-base の SHA（同じ head で求め直さない。PR 3944 の r3-5）
    merge_bases: dict[str, str] = field(default_factory=dict)
    # この起動で最後に起動した修正担当の記録と、1 ラウンドの文脈の増分の最大（状態ファイルへは書かない。#4013）
    fixer_last: Optional[resume_decide.Previous] = None
    fixer_growth: int = 0
    documents: bool = False  # PR の全差分に Markdown のファイルがあるか（文書の基準を指示に入れる。#4285）
    # 今の席・この実行で座っている埋めた席・この実行の資料のディレクトリ（#3955）
    seats: list[str] = field(default_factory=list)
    filled: list[Filled] = field(default_factory=list)
    input_dir: Optional[Path] = None

    @property
    def mvv_items(self) -> tuple[str, ...]:
        """承認済みのプロジェクト MVV の項目（MVV を読んでいないか、承認済みでなければ空）。"""
        return self.snapshot.items if self.snapshot is not None else ()

    def round_stem(self, round_no: int, suffix: str) -> Path:
        """状態の保存先のラウンドごとのファイルの名前（`round<n><suffix>`）。"""
        return self.cfg.state_dir / f"round{round_no}{suffix}"

    def input_path(self, round_no: int, suffix: str) -> Path:
        """相手役へ渡す資料のパス。資料のディレクトリは実行ごとに新しく作り、既存のものを使わない（#3955 の決定 2）。"""
        if self.input_dir is None:
            self.input_dir = Path(tempfile.mkdtemp(prefix=INPUT_PREFIX, dir=self.cfg.state_dir))
        return self.input_dir / f"round{round_no}{suffix}"

    def note(self, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None:
        """過程へ 1 行足す。`warning` があれば先に注意へも足す。"""
        if warning is not None:
            self.warnings.append(warning)
        self.process.append({"run": f"round {round_no} {who}", "result": result})


# --- 始め ------------------------------------------------------------------------------


def _ingest_pending(run: _Run) -> None:
    """前の起動が残した修正の結果ファイルを、修正の記録の無い最後のラウンドへマージする（#2770）。"""
    rounds = store.list_of(run.data, store.ROUNDS)
    last = store.last_round(run.data)
    pending = store.pending_fix_result(run.cfg.state_dir, run.ref)
    gate = fixgate_decide.require_fix_for_changes(len(rounds), last.get("verdict"), last.get("fix"), str(pending) if pending else None)
    if gate.kind == fixgate_decide.STOP:
        raise DriveStopped("work_stopped", str(gate.reason))
    if gate.kind == fixgate_decide.INGEST and pending is not None:
        try:
            last["fix"] = fixer.take_fix(run, pending, len(rounds))
        except store.FixResultMismatch as exc:
            raise DriveStopped("invalid", f"前の起動が残した修正の結果を読み込めない: {exc}") from exc
        _save(run)
        fixer.consume(pending, len(rounds))


def _resend(run: _Run, synced: worktree.SyncResult) -> None:
    """前の起動が送れなかった修正のコミット（載せ直した自分のコミット）があれば送る。送れなければ記録して止める。"""
    fix = store.last_round(run.data).get("fix")
    if synced.replayed == 0 or not isinstance(fix, dict) or "pushed" in fix:
        return
    round_no = len(store.list_of(run.data, store.ROUNDS))
    remote = run.g.rev_parse(f"refs/remotes/{run.g.remote()}/{run.cfg.branch}")
    # 載せ直しでコミットの SHA が変わるので、報告されたコミットとの検証はしない
    sent = fixer.send_fix(run, round_no, remote, fix, check_commit=False)
    if "pushed" in fix:
        run.pushed.add(run.sent)
    if sent:
        # 前の起動が送れずに未解決へ戻した指摘は、PR へ届いたので直したへ戻す（修正担当へ渡し直さない）
        run.journal.restore_fixed(store.list_of(fix, "fixed"))
    _save(run)
    if not sent:
        raise DriveStopped("work_stopped", f"前の起動の round {round_no} の修正を送れない: {run.warnings[-1]}")


def _merge_target(cfg: RunConfig, reader_for: Optional[carry.ReaderFor]) -> str:
    """PR のマージ先のブランチを GitHub から読む。読めなければ担当の起動前に止める。

    `--base` がコミット（SHA・タグなど）のときだけ使う（理由は `worktree.fetch_base` にある）。
    """
    pr = store.state_pr(cfg.state_dir, cfg.pr).current_pr
    if reader_for is None:
        raise DriveStopped("precondition", f"PR #{pr} のマージ先のブランチを読む口が無い（--base がコミットなので要る）")
    try:
        return reader_for(pr).base()
    except Exception as exc:
        # 認証・通信・応答のどの失敗でも、MVV を決められないので止める
        raise DriveStopped("precondition", f"PR #{pr} のマージ先のブランチを読めないので、MVV を決められない: {exc}") from exc


class _Based(NamedTuple):
    """起動の始めの同期の結果と、MVV を読むブランチ・差分の比較元のコミット・使ったリモート名。"""

    synced: worktree.SyncResult
    branch: str
    commit: str
    remote: str


def _sync(cfg: RunConfig, g: git.Git, reader_for: Optional[carry.ReaderFor]) -> _Based:
    """PR のブランチと MVV を読むブランチを 1 回で取得して同期する（ブランチの決め方は `worktree.fetch_base`）。

    `--base` がブランチなら、その先頭のコミットが MVV を読む先で、差分の比較元でもある。コミットなら、比較元はそのコミットの
    まま、MVV は PR のマージ先から読む（スプリントブランチへ単位の PR を集めた後のレビューは、集めた時点のコミットを渡す）。
    """
    remote = g.remote()

    def sync(branch: str) -> worktree.SyncResult:
        return worktree.sync_worktree(g, cfg.branch, remote, base_branch=branch)

    synced, base = worktree.fetch_base(g, cfg.base, remote, sync, lambda: _merge_target(cfg, reader_for))
    return _Based(synced, base.branch, base.commit if base.commit is not None else synced.base, remote)


def _snapshot(cfg: RunConfig, based: _Based) -> mvv.Snapshot:
    """同期で取得したコミットの MVV を読む。読めなければ、MVV を確かめないレビューにならないよう担当の起動前に止める（#4076）。"""
    try:
        return mvv.of_base(cfg.worktree, based.branch, based.synced.base, cfg.worktree)
    except mvv.MvvError as exc:
        raise DriveStopped("precondition", str(exc)) from exc


def _start(cfg: RunConfig, reader_for: Optional[carry.ReaderFor], clean: report_decide.Cleaner) -> _Run:
    worktree.registration(cfg.worktree)
    g = git.Git(cfg.worktree)
    based = _sync(cfg, g, reader_for)
    synced = based.synced
    if synced.conflicts:
        raise DriveStopped("precondition", worktree.conflict_detail(synced))
    snap = _snapshot(cfg, based)
    # 差分の比較元をコミットで持つ。起動の途中で別のセッションがベースを取得しても、比べる相手は変わらない（#4090）
    cfg = replace(cfg, base_commit=based.commit)
    warnings: list[str] = []
    # 重点は呼び手が同期の前に実行者のメインディレクトリから読んだもの。PR の head の設定は相手役と修正担当への指示に
    # しない（PR の作成者が承認を経ずに指摘の採否や直し方を誘導できる。PR 3944 の r3-1）。違えば注意に載せる
    if settings.proposed_differs(g, synced.remote, cfg.settings.raw):
        rel = settings.settings_path(Path(), "review").as_posix()
        warnings.append(settings.FOCUS_DIFFERS.format(pr_path=rel, trusted=settings.settings_path(cfg.main or cfg.worktree, "review")))
    ref = store.state_pr(cfg.state_dir, cfg.pr)
    data = store.read_state(ref)
    rebound = report_decide.bind_pr(data, ref.current_pr, ref.origin_pr)
    journal = FindingJournal(store.list_of(data, store.FINDINGS))
    run = _Run(
        cfg,
        g,
        ref,
        data,
        journal,
        warnings,
        [],
        [],
        snapshot=snap,
        mvv=snap.identity,
        mvv_branch=based.branch,
        sent=synced.remote,
        remote=based.remote,
        synced_head=synced.remote,
        clean=clean,
        seats=list(cfg.reviewers),
        filled=list(cfg.prefilled),
    )
    if rebound:
        _save(run)
    _ingest_pending(run)
    _resend(run, synced)
    return run


# --- 記録 -------------------------------------------------------------------------------


def _save(run: _Run) -> None:
    run.data[store.FINDINGS] = list(run.journal.rows.values())
    store.write_object(run.ref.path, run.data)


def _ingest_citations(run: _Run, errors: Optional[list[dict[str, Any]]], round_no: int, recorded: bool = True) -> bool:
    """根拠の記述の確かめの結果をジャーナルへ入れる。ジャーナルが変わったら真。"""
    wrote = run.journal.ingest_citations(errors, run.mvv, round_no, recorded=recorded)
    carry.detach_revived(run)
    return wrote


def _mvv_checked(run: _Run, head: str) -> bool:
    """`head` で根拠の記述を確かめ終えたか（承認済みの MVV が無ければ真）。PR に届いていない `head` は、確かめても偽にする
    （打ち直しが、手元だけのコミットの収束を使わない）。"""
    return criteria_decide.approved(run.snapshot) is None or run.checked_head == head == run.sent


def _final_head(run: _Run, final: str, rounds: int) -> str:
    """作業場所に残る最後のコミット。修正を送っていればそのコミット、送れなかったか修正担当が途中で終わったなら作業場所の HEAD。"""
    last = store.last_round(run.data)
    if rounds == 0 or final == fin.ERROR:
        return run.g.rev_parse("HEAD")
    fix = last.get("fix")
    return str(fix["pushed"] if isinstance(fix, dict) and "pushed" in fix else last["head"])


class _FinalCheck(NamedTuple):
    """終わり方を決める前の確かめの結果。確かめ終えたかと、PR に届いていないコミットで見つけた誤りの件数。"""

    checked: bool
    unsent: int = 0


def _check_final_head(run: _Run, final: str, rounds: int) -> _FinalCheck:
    """終わり方を決める前に根拠の記述を確かめる。最後の修正で増えた・直った誤りを、判定の前に状態ファイルへ残す（変わらなければ
    書かない）。指摘にするのは PR に届いたコミットの誤りだけで、手元だけに残ったコミットの誤りは別に確かめて件数だけを判定に使う。"""
    if criteria_decide.approved(run.snapshot) is None:
        return _FinalCheck(True)
    local = _final_head(run, final, rounds)
    checked, wrote = _check_sent(run, rounds)
    held = run.data.pop(fin.UNSENT_CITATIONS, None)
    unsent = _check_unsent(run, local) if local != run.sent else 0
    if wrote or held != run.data.get(fin.UNSENT_CITATIONS):
        _save(run)
    return _FinalCheck(False) if unsent is None else _FinalCheck(checked, unsent)


def _check_sent(run: _Run, rounds: int) -> tuple[bool, bool]:
    """PR に届いた最後のコミットを確かめ、結果を指摘にする。確かめ終えたかと、状態が変わったかを返す。"""
    last = store.last_round(run.data)
    n = len(store.list_of(run.data, store.ROUNDS))
    recorded = last.get("head") == run.sent
    # 記録の無いコミットで見つけた誤りは、次の起動の最初のラウンドの指摘にする
    wrote = _ingest_citations(run, changes.final_citations(run, run.sent), n if recorded else n + 1, recorded)
    checked = _mvv_checked(run, run.sent)
    if checked and recorded and rounds > 0 and last.get(fin.MVV_CHECKED) is not True:
        # ラウンドの中の確かめに失敗していた。同じ head を確かめ終えたので、打ち直しが収束を使えるようにする
        last[fin.MVV_CHECKED] = wrote = True
    return checked, wrote


def _check_unsent(run: _Run, head: str) -> Optional[int]:
    """PR に届いていないコミット `head` を確かめ、誤りの件数（確かめられなければ None）を返す。誤りと失敗の理由は未公開のファイルの
    名前や本文を含むので、PR へ載せる指摘と注意には入れず、手元の状態ファイルだけに残す。"""
    seen = len(run.warnings)
    errors = changes.final_citations(run, head)
    detail = run.warnings[seen:]
    run.data[fin.UNSENT_CITATIONS] = {"head": head, "errors": errors, "detail": detail}
    if errors is None:
        run.warnings[seen:] = [] if UNSENT_UNCHECKED in run.warnings[:seen] else [UNSENT_UNCHECKED]
        return None
    return len(errors)


def _fixes_checked(run: _Run) -> tuple[Optional[str], mvv.HoldIndex]:
    """今の PR で、直した通常の指摘を記録の保留と照らし終えた時刻（無ければ None）と、保留の候補（無ければ未確認）。"""
    pr = run.ref.current_pr
    held = run.data.get(store.MVV_FIXES_CHECKED)
    if not (isinstance(held, dict) and held.get("pr") == pr):
        return None, mvv.HoldIndex(pr)
    at = held["at"] if isinstance(held.get("at"), str) else None
    size, keys = held.get("size"), held.get("keys")
    if type(size) is not int or not isinstance(keys, list):
        return at, mvv.HoldIndex(pr)
    return at, mvv.HoldIndex(pr, size, {k for k in keys if isinstance(k, str)})


def _checked_state(pr: int, at: Optional[str], index: mvv.HoldIndex) -> dict[str, Any]:
    return {"pr": pr, "at": at, "size": index.size, "keys": sorted(index.keys)}


def _record_mvv(
    run: _Run, snap: mvv.Snapshot, rows: Sequence[Mapping[str, Any]], clean: Optional[report_decide.Cleaner]
) -> mvv.ReviewWrite:
    """MVV の記録を書き、結果を返す。書けなければ固定の注意を足し、伏せた原因（保存先の絶対パスを含む）は手元の状態ファイルだけに残す。"""
    step = "メインディレクトリの解決"
    since, index = _fixes_checked(run)
    before = _checked_state(run.ref.current_pr, since, index)
    # 前の起動より後に直した通常の指摘があれば、照らし終えた時刻を進めて状態ファイルに残す（同じ秒の中でも前後を見分ける）
    fresh = any(f.plain and f.outcome == fin.FIXED for f in md.judged_findings(rows, snap.identity, holds=True, since=since))
    checked = jsonl.now_exact()
    try:
        # 起動の始めに解決した主 worktree を使う（`git worktree list` を打ち直さない）
        repo = mvv.repo_key(run.cfg.worktree) if run.cfg.main is None else str(run.cfg.main)
        step = "記録の書き込み"
        got = mvv.record_review(mvv.ReviewTarget(repo, run.ref.current_pr, run.sent), snap, rows, clean, since, index)
        if got.cause is None:
            run.data[store.MVV_FIXES_CHECKED] = _checked_state(run.ref.current_pr, checked if fresh else since, index)
    except (mvv.MvvError, identity.IdentityError, internal.InternalError, OSError, UnicodeDecodeError) as exc:
        # 記録を書く前に止まった。どの行が記録にあるか分からないので、必須の行をすべて欠けとする
        got = mvv.unwritable(snap, rows, mvv.failure_cause(step, exc))
    if got.cause is not None:
        run.warnings.append(RECORDS_UNWRITTEN)
    held = run.data.pop(store.MVV_RECORD_ERROR, None)
    if got.cause is not None:
        run.data[store.MVV_RECORD_ERROR] = got.cause
    if got.cause != held or (store.MVV_FIXES_CHECKED in run.data and run.data[store.MVV_FIXES_CHECKED] != before):
        try:
            _save(run)
        except OSError:
            # 空き容量の不足では状態ファイルも書けない。レビューの結果は返す
            run.warnings.append(CAUSE_UNSAVED)
    return got


def _mvv_results(run: _Run) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """MVV に反するとした指摘と根拠の記述の誤りを MVV の記録に残し、結果 JSON の `outputs.mvv` と、承認ゲートの判定に要る行を
    書けなかったときの `Outcome.mvv_record_failed` を返す（承認済みの MVV が無ければどちらも None）。

    対象のコミットは、PR に届いた最後のコミット（指摘にする誤りは、このコミットで確かめたものだけである）。自由記述を伏せられない
    ときも、通常の行（`fixed`・`unresolved`）を書けないときも、レビューの終わり方は変えず、注意に載せる。
    """
    snap = criteria_decide.approved(run.snapshot)
    if snap is None:
        return None, None
    rows, seen = list(run.journal.rows.values()), publish.Once(run.clean)
    out = report_decide.mvv_outputs(rows, snap, run.sent, seen)
    if seen.failure is not None:
        run.warnings.append(f"MVV の結果の自由記述を伏せた（参照先の公開範囲を照会できない）: {seen.failure}")
    got = _record_mvv(run, snap, rows, seen if seen.failure is None else None)
    if got.cause is None and seen.failure is not None:
        later = "次の起動で書き直す。それまで設計承認ゲートは省かない"
        run.warnings.append(f"MVV の記録（mvv-validation.jsonl）の自由記述を伏せて書いた（{later}）: {seen.failure}")
    if not got.missing:
        return out, None
    return out, {"rows": [{"key": r["key"], "outcome": r["outcome"]} for r in got.missing], "cause": RECORDS_UNWRITTEN}


def _summary(run: _Run, final: str, rounds: int, reused: bool = False) -> Outcome:
    """要約を書いて結果を返す。`rounds` は今回の起動で終えたラウンドの数（前回の結果を返すときは状態ファイルのラウンドの数）。
    前回の結果は、根拠の記述を確かめ終えた head のものだけを返すので、確かめ直さない（`scope_decide.read_by_all_head`）。"""
    check = _FinalCheck(True) if reused else _check_final_head(run, final, rounds)
    path = run.cfg.state_dir / SUMMARY_NAME
    review = fin.Review(final, rounds, run.journal.findings(), check.checked, check.unsent)
    store.write_object(path, fin.summary_of(review, run.ref.origin_pr))
    results, failed = _mvv_results(run)
    read = reviewed_pr.read_by_all_this_run(run.data, rounds)
    # 注意を足すことがあるので、`run.warnings` を写す前に確かめる
    pr = reviewed_pr.confirmed(run, read)
    return Outcome(review, path, tuple(run.warnings), tuple(run.process), read, reused, results, failed, pr)


def _oversized_docs(run: _Run, docs: dict[str, str]) -> None:
    """変わった Markdown のうち `cross-review.design-doc-lines` の行数を超えるものを注意に載せる（止めない。1 つの文書は 1 回）。"""
    lengths = {path: len(text.splitlines()) for path, text in docs.items()}
    limit = int(limits.value(DOC_LINES, "count"))
    for path, n in scope_decide.oversized(lengths, limit):
        if not any(w.startswith(f"設計文書 {path} が ") for w in run.warnings):
            run.warnings.append(f"設計文書 {path} が {n} 行ある（{limit} 行を超える）。読み手とレビューが追えるよう分けることを考える")


def _prepare_round(run: _Run, round_no: int) -> reviewing.Prepared:
    """範囲を決めて差分のファイルを書き、PR 全体で足した根拠の記述を確かめる。head が PR に届いていなければ（前の起動の修正担当が
    コミットだけを残して終わった）、結果を指摘にせず手元の状態ファイルだけに残す。"""
    cfg = run.cfg
    changed = changes.changed(run.g, cfg.base_commit, cfg.worktree)
    _oversized_docs(run, changed.docs)
    run.documents = any(p.endswith(".md") for p in changed.files)
    reviewed = scope_decide.reviewed_of(run.data, run.mvv)
    sections = changes.sections(changed.docs)
    head = run.g.rev_parse("HEAD")
    merge_base = _merge_base(run, head)
    prev = round_scope.previous_head(run.g, run.data, _basis(run, head), head)
    scope = scope_decide.review_scope(cfg.every_section, round_no, prev, sections, reviewed)
    diff = since = reviewing.write_diff(run, round_no, ".diff", scope.base or merge_base, head)
    if prev is not None and scope.base is None:
        # 全差分を渡すラウンドでも、前の head からの差分はここで 1 回だけ作り、相手役の全員に渡す（相手役ごとに作らせない。#4285）
        since = reviewing.write_diff(run, round_no, "-since.diff", prev, head)
    previous = None if prev is None else criteria_decide.Previous(prev, since)
    full_diff = diff if scope.kind == scope_decide.FULL and scope.base is None else None
    if full_diff is None and _reads_full_diff(run, merge_base, head):
        # 根拠の確かめが全差分を読むので、ここで 1 回だけファイルへ書き、席を埋めるときにも使う（Git を 2 回起動しない。PR 4461 の r1-2）
        try:
            full_diff = reviewing.write_diff(run, round_no, reviewing.FULL_DIFF_SUFFIX, merge_base, head)
        except (git.GitError, OSError):  # 書けなければ確かめが git から読み直し、失敗を注意に載せる
            full_diff = None
    run.last_target = changes.Target(merge_base, head, changed.docs, full_diff)
    if head == run.sent or criteria_decide.approved(run.snapshot) is None:
        _ingest_citations(run, changes.citations(run, run.last_target), round_no)
    else:
        _check_unsent(run, head)
    return reviewing.Prepared(scope, sections, reviewed, head, diff, changed.files, merge_base, previous)


def _reads_full_diff(run: _Run, merge_base: str, head: str) -> bool:
    """このラウンドの根拠の確かめが `merge_base...head` の全差分を Git から読むか（承認済みの MVV があり、同じ対象をまだ確かめていない）。"""
    snap = criteria_decide.approved(run.snapshot)
    return snap is not None and snap.version is not None and (merge_base, head, snap.identity) not in run.citations


def _merge_base(run: _Run, head: str) -> str:
    """起動の始めに決めたベースのコミットと `head` の merge-base の SHA。`head` が同じなら 1 回の起動で 1 回だけ求める。"""
    if head not in run.merge_bases:
        run.merge_bases[head] = run.g.merge_base(run.cfg.base_commit, head)
    return run.merge_bases[head]


def _basis(run: _Run, head: str, stand_in: bool = False) -> scope_decide.Basis:
    """前のラウンドを差分の起点や収束の再利用に使える条件（今回の値。席は今の席）。"""
    unit = scope_decide.unit_id(run.cfg.unit)
    # 確認を求める起動は、読んだ PR が無ければラウンドの前に止まっている
    pr = run.read_pr.ident if run.cfg.check_pr and run.read_pr is not None else None
    return scope_decide.Basis(_merge_base(run, head), tuple(run.seats), run.cfg.settings.focus, run.mvv, stand_in, unit, pr)


def _confirm_heavy(
    run: _Run,
    round_no: int,
    diff: Path,
    launch: Launcher,
    use_tracks: bool = False,
    filled: Optional[reviewing.FilledRead] = None,
) -> Checked:
    """直前の `enter` で新しく出たか戻った critical・major を判定役に確かめ、回答をジャーナルへ当てる（#4363）。渡す指摘が
    無ければ起動しない。`use_tracks` は、回答の指摘の種類を使う処理があるラウンドで真にする。

    判定役の起動は 1 回で、指摘ごとに、それを出した席が読んだ差分を渡す。席を埋めたラウンド（`filled`）の埋めた席の指摘は
    全差分、ほかはラウンドの差分 `diff` である（一方の差分に載る変更が、他方に載るとは限らない）。"""
    heavy = [row for row in map(run.journal.rows.__getitem__, run.journal.fresh) if _heavy(row.get("severity"))]
    asked = verify_decide.select(heavy, use_tracks)
    if not asked:
        return Checked()
    diffs = {str(row["id"]): (diff,) if filled is None else filled.diffs(row.get("key"), diff) for row in asked}
    answers = verify.ask(run, round_no, asked, diffs, launch)
    run.warnings += [f"round {round_no}: {warning}" for warning in answers.warnings]
    return run.journal.apply_checks(list(diffs), answers.verdicts)._replace(record=answers.record)


def _heavy(severity: object) -> bool:
    """尺度にある収束の妨げになる重さ（critical・major）か。重要度の無い引き継いだ行は偽。"""
    known = criteria_decide.severity_of(severity)
    return known is not None and criteria_decide.blocking(known)


def _verdict(run: _Run, open_: Sequence[dict[str, Any]], handed: Sequence[fin.Handed]) -> str:
    """ラウンドの判定（収束・振動・修正が要る）。"""
    if not open_ and not handed:
        return fin.CONVERGED
    if oscillation.returned(run.history):
        return fin.OSCILLATING
    return CHANGES_REQUIRED


def _round_record(run: _Run, round_no: int, prep: reviewing.Prepared) -> dict[str, Any]:
    """状態ファイルに残すラウンドの記録。判定と修正の結果は、決まってから呼び手が足す。"""
    record = {
        "round": round_no,
        "head": prep.head,
        scope_decide.BASE: prep.base,
        scope_decide.REVIEWERS: list(run.seats),
        scope_decide.FOCUS: list(run.cfg.settings.focus),
        scope_decide.MVV: {scope_decide.MVV_ID: run.mvv, scope_decide.MVV_BRANCH: run.mvv_branch},
        fin.MVV_CHECKED: _mvv_checked(run, prep.head),
        "scope": prep.scope.kind,
    }
    if run.cfg.unit:
        record[scope_decide.UNIT] = scope_decide.unit_id(run.cfg.unit)
    if run.read_pr is not None:
        record[scope_decide.PR] = run.read_pr.ident
    if run.filled:
        # 埋めた後のすべてのラウンドに書く（#3955）
        record[scope_decide.FILLED] = [f.record() for f in run.filled]
    return record


def _reviewed(run: _Run, round_no: int, prep: reviewing.Prepared, launch: Launcher) -> reviewing.Reviewed:
    """相手役の結果。判定待ちで残った同じ対象のラウンドがあれば使い、無ければ起動して受け取る（#4363）。"""
    held = reviewing.kept(run, round_no, prep, _basis(run, prep.head, run.cfg.may_fill))
    if held is not None:
        return held
    got = reviewing.review_round(run, round_no, prep, reviewing.remarks_file(run, round_no, reviewing.remarks(run, prep)), launch)
    if got.silent:
        # 誰も差分を読めないまま収束と判定しない（#3893）。状態ファイルはこのラウンドを持たないので、直して打ち直せば続く。
        # precondition の詳細は手元にだけ返るので、標準エラーのログを絶対パスで示す（PR 4461 の r3-1）
        local = [u.local for u in got.unread.values()]
        raise DriveStopped("precondition", f"round {round_no} で相手役が誰も差分を読めなかった:\n" + "\n".join(local))
    return got


def _keeping(run: _Run, round_no: int, prep: reviewing.Prepared, got: reviewing.Reviewed, launch: Launcher) -> Launcher:
    """判定役の起動の直前に、判定待ちのラウンドを保存する起動（判定役の待ちで中断しても、打ち直しがレビューからやり直さない。
    起動しないラウンドは、状態を読みも書きも複製もしない。#4363）。足す先は状態ファイルの中身である。ラウンドの途中では
    状態ファイルを書かないので、このラウンドの指摘をジャーナルへ入れる前の状態が残り、打ち直しが同じ指摘を入れ直して同じように
    判定役へ渡す。"""

    def judging(spec: agents.Launch) -> agents.AgentStatus:
        data = store.read_state(run.ref)
        data[store.PENDING_REVIEW] = reviewing.pending(_round_record(run, round_no, prep), prep, got)
        store.write_object(run.ref.path, data)
        return launch(spec)

    return judging


def _round(run: _Run, round_no: int, launch: Launcher) -> Optional[str]:
    """1 ラウンドを回す。終わり方（`finish_decide` の FINALS）か、次へ進むなら None。"""
    rounds = run.data.setdefault(store.ROUNDS, [])
    prep = _prepare_round(run, round_no)
    got = _reviewed(run, round_no, prep, launch)
    raised, unread = got.raised, {s: u.hint for s, u in got.unread.items()}
    keys = run.journal.enter(round_no, raised, run.mvv_items, run.mvv)
    run.warnings += run.journal.warnings
    carry.detach_revived(run)
    # 確認で下げた指摘は、振動の判定のキーに数えない
    checked = _confirm_heavy(run, round_no, prep.diff, _keeping(run, round_no, prep, got, launch), filled=got.filled)
    run.history.append(keys - checked.lowered)
    record = _round_record(run, round_no, prep)
    if checked.record:  # 中断した起動の出力も保存先に残るので、このラウンドの起動の保存できた出力を名指す（回答の有無は表さない）
        record[verify_decide.ROUND_KEY] = checked.record
    rounds.append(record)
    run.data.pop(store.PENDING_REVIEW, None)
    if unread:
        # 読めなかった相手役の分を収束に数えない（#3941）。読めた相手役の指摘はジャーナルに残し、次の起動で続ける
        record["verdict"] = fin.INCOMPLETE_REVIEWERS
        names = "・".join(unread)
        run.warnings.append(
            f"round {round_no} は相手役 {names} が起動し直しても（claude の席は起動し直さない）差分を読めなかったので、収束に数えずに止める:\n"
            + "\n".join(unread.values())
        )
        return fin.INCOMPLETE_REVIEWERS
    if prep.scope.kind == scope_decide.FULL:
        run.data[store.REVIEWED] = sorted(prep.reviewed | set(prep.sections))
        run.data[store.REVIEWED_MVV] = run.mvv
    open_, handed = run.journal.pending(run.cfg.handed)
    record["verdict"] = _verdict(run, open_, handed)
    if record["verdict"] != CHANGES_REQUIRED:
        return record["verdict"]
    _save(run)
    fix = fixer.run_fixer(run, round_no, open_, handed, launch)
    if fix is None:
        # 修正必須のまま残すと、次の起動が修正の記録の欠けで止まり続ける。errored で残し、次の起動で修正担当をもう一度起動する
        record["verdict"] = fin.ERROR
        return fin.ERROR
    record["fix"] = fix
    _save(run)
    fixer.consume(store.fix_result_path(run.cfg.state_dir, run.ref), round_no)
    if not fixer.send_fix(run, round_no, prep.head, fix):
        return fin.ERROR
    if "pushed" in fix:
        run.pushed.add(run.sent)
    return None


def _reusable(run: _Run) -> bool:
    """前回の結果をそのまま返せるか（最後のラウンドが収束で、その head が今の HEAD、比較基点が今の `--base` と HEAD の
    merge-base の SHA と同じで、今回の相手役が全員そのラウンドを読んでいて、未解決の指摘も決めていない足すべき内容も無い）。
    比較基点が違えば PR の全差分が変わっている（PR 3944 の r2-1）。読んでいない相手役がいれば全差分から読ませる（r2-2）。
    そのラウンドと今の重点が違えば、足した重点でまだ読んでいないので回し直す（r4-3）。単位の指示の中身が違えば、今の範囲で
    まだ読んでいないので回し直す（#4369）。読んだ PR の確認を求める起動は、そのラウンドが読んだ PR が違えば回し直す（#4369）。MVV が違えば、今の MVV に反しないかを
    まだ確かめていないので回し直す（#4076）。"""
    if store.last_round(run.data).get("verdict") != fin.CONVERGED:
        return False
    head = run.g.rev_parse("HEAD")
    # 前回埋めた席を欠けた相手役の代わりにも数える（名指しが 1 者なら数えない。#3955）
    if scope_decide.read_by_all_head(run.data, _basis(run, head, run.cfg.may_fill)) != head:
        return False
    open_, handed = run.journal.pending(run.cfg.handed)
    return not open_ and not handed


def drive(
    cfg: RunConfig,
    launch: Launcher = agents.run_agent,
    reader_for: Optional[carry.ReaderFor] = None,
    clean: report_decide.Cleaner = run_log.redact,
) -> Outcome:
    """ラウンドを上限まで回し、要約を書いて返す。始められなければ `DriveStopped`・`worktree.WorktreeUnregistered`。
    `reader_for` は PR のコメントを読む口を作る（省けば読まない）。引き継ぎの確かめに要るもの（スレッドと PR コメント）は
    いつも読み、PR のマージ先は `--base` がコミットのときだけスレッドと同じ応答から読み、相手役へ渡す参考資料の残り（レビューコメントとレビュー本文）は前回の結果を返さないときだけ読む（PR 3944 の r2-10）。
    `clean` は、結果と MVV の記録に残す自由記述を伏せる処理（省けば秘密だけを伏せる）。"""
    cfg = settings.outside_checkout(cfg)
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # 既存の通常ファイル・書き込めない場所を渡されても、トレースバックでなく前提の欠けとして返す（PR 3944 の r13-1）
        raise DriveStopped("precondition", f"状態の保存先 {cfg.state_dir} を作れない: {exc}") from exc
    run = _start(cfg, reader_for, clean)
    try:
        return _drive_rounds(run, launch, reader_for)
    finally:
        # 止まったときも資料を消す。消せなくても結果は変えない（次の実行は新しいディレクトリを作る。#3955）
        if run.input_dir is not None:
            shutil.rmtree(run.input_dir, ignore_errors=True)


def _drive_rounds(run: _Run, launch: Launcher, reader_for: Optional[carry.ReaderFor]) -> Outcome:
    cfg = run.cfg
    prs = carry.reader(cfg, reader_for, run.warnings)
    prior = carry.read_prior(prs, run.warnings)
    run.read_pr = reviewed_pr.seen(run, prior.pr)
    reason = reviewed_pr.why_unread(run, prior.pr)
    if reason is not None:
        # 読めなかった原因は注意にある。結果へ載る文なので秘密を伏せる
        detail = [f"レビューを始める前に止めた。読んだ PR を確かめられない: {reason}", *run.warnings]
        raise DriveStopped("precondition", "\n".join(str(line) for line in run_log.redact(detail)))
    if carry.carry(run, prior):
        _save(run)
    if carry.restate(run):
        _save(run)
    if _reusable(run):
        # 変わっていない PR を回し直さない（相手役の起動の分の費用をかけない。#3941）
        n = len(store.list_of(run.data, store.ROUNDS))
        run.process.append({"run": f"round {n}", "result": "head が変わっていないので前回の結果（converged）を返した"})
        seats.reused_warnings(run)
        return _summary(run, fin.CONVERGED, n, reused=True)
    run.remarks = [*prior.comments, *carry.read_reviews(prs, run.warnings)]
    run.warnings += [seats.filled_warning(f, "起動の前") for f in cfg.prefilled]
    seats.fill_refused(run)
    final, done = fin.MAX_ROUNDS, 0
    first = len(store.list_of(run.data, store.ROUNDS)) + 1
    for round_no in range(first, first + cfg.max_rounds):
        ended = _round(run, round_no, launch)
        done = len(store.list_of(run.data, store.ROUNDS)) - first + 1
        _save(run)
        if ended is not None:
            final = ended
            break
    return _summary(run, final, done)
