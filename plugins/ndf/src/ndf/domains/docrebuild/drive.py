"""`ndf document-rebuild run` の 1 回の実行（#4337 の決定 1・3・5・6、I1〜I7）。

起点を決め、差分を読み、`select_decide` で対象を選び、`ndf worker run` を 1 回打ち、worker の結果を確かめて
結果 JSON を組む。コミット・push・GitHub への書き込みを持たない（条件 13。コミットは worker が作業場所で行う）。
"""

from __future__ import annotations

import json
import logging
import os
import stat
import string
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf import cli, contracts, identity
from ndf.adapters import git
from ndf.contracts import result
from ndf.core import issue_docs
from ndf.domains.docrebuild import select_decide as sd
from ndf.domains.records import reading
from ndf.domains.docrebuild.select_decide import Inspection, Target

log = logging.getLogger(__name__)

TOOL = cli.tool_of(f"{__package__}.cli")
# `ndf worker run --kind` の値と保存先のファイル名の頭（サブコマンドの名前と同じ）
WORK_KIND = TOOL
RESULT_SCHEMA = contracts.schema_path("io/document-rebuild.result")
PROMPT_TEMPLATE = "contracts/worker/document-rebuild.md"
SKILL = "skills/document-rebuild/SKILL.md"
REQUIREMENTS_FILE = "issues/issue-{number}-requirements.md"
ADD_PREFIX, READER_PREFIX = contracts.schema_enum(RESULT_SCHEMA, "/$defs/id_prefix")
# `git diff A...B` で分岐点が無いときの終了コード（設計書の実測）
NO_MERGE_BASE = 128
WORKTREE_REMOTE = f"{git.WORKTREE_NDF_SETTINGS} の remote"
# 確かめの記録の verdict（すべて）と、読み手役の確かめの verdict
VERDICTS = contracts.schema_enum(RESULT_SCHEMA, "/$defs/reader/properties/verdict")
CHECKED = contracts.schema_enum(RESULT_SCHEMA, "/$defs/reader/properties/checks/items/properties/verdict")

# 読み手役を立てられなかった記録の verdict と、その欠けの理由の頭（#4446 の決定 5。ok で進めずに止める）
NOT_RUN = "not_run"
NOT_RUN_GAP = "読み手役を立てられなかった"

Runner = Callable[[list[str], Path], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)


@dataclass(frozen=True)
class RunArgs:
    """1 回の実行の引数。`state_dir` と `cwd` は絶対パス。"""

    base: str
    remote: str
    issues: tuple[int, ...]
    kinds: tuple[str, ...]
    state_dir: Path
    cwd: Path


class Stop(Exception):
    """停止理由を持つ例外の基底。`kind` は結果の `cause.kind`、`detail` は説明。"""

    kind = ""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class Precondition(Stop):
    """起点か差分を決められない（`stopped`（precondition））。"""

    kind = "precondition"


class BadReport(Stop):
    """worker の結果の形の誤り（`stopped`（bad_report））。"""

    kind = "bad_report"


def _resolve(g: git.Git, ref: str) -> Optional[str]:
    """参照のコミットの SHA。読めなければ None、git の失敗は `Precondition`。"""
    try:
        return g.commit_or_none(ref)
    except git.GitError as exc:
        raise Precondition(f"{ref} を読めない: {exc}") from None


def _head_sha(g: git.Git) -> str:
    """`HEAD` の SHA。読めなければ `Precondition`。"""
    try:
        return g.rev_parse("HEAD")
    except git.GitError as exc:
        raise Precondition(f"HEAD を読めない: {exc}") from None


def start_point(g: git.Git, base: str, remote: str) -> tuple[str, str]:
    """起点の参照の名前と SHA（設計書の「処理の流れ」の表）。

    リモート追跡ブランチが読めればそれを使う。読めないときは、求めた名前のリモートが登録済みか、リモートが 1 つも
    無いときだけ手元の参照を使う。ほかの名前のリモートだけがあるとき・どちらも読めないときは `Precondition`。
    """
    tracking = f"refs/remotes/{remote}/{base}"
    sha = _resolve(g, tracking)
    if sha is not None:
        return tracking, sha
    try:
        names = g.run("remote").split()
    except git.GitError as exc:
        raise Precondition(f"登録済みのリモートを読めない: {exc}") from None
    if names and remote not in names:
        raise Precondition(f"リモート {remote} が登録されていない（登録済み: {', '.join(names)}）。{WORKTREE_REMOTE} を直す")
    local = f"refs/heads/{base}"
    sha = _resolve(g, local)
    if sha is None:
        raise Precondition(f"起点の参照 {tracking} と {local} をどちらも読めない")
    return local, sha


def diff_paths(g: git.Git, start: str, head: str) -> list[str]:
    """3 ドット差分（分岐点から `head` まで）の、消していないパス。分岐点が無い・git の失敗は `Precondition`。"""
    done = g.attempt("diff", "--name-only", "-z", "--diff-filter=d", f"{start}...{head}", "--")
    if done.returncode == NO_MERGE_BASE:
        raise Precondition(f"分岐点が無い: {done.stderr.strip()}")
    if done.returncode != 0:
        raise Precondition(f"差分を読めない（git の終了コード {done.returncode}）: {done.stderr.strip()}")
    return [p for p in done.stdout.split("\0") if p]


def _stat_or_none(path: Path) -> Optional[os.stat_result]:
    """`lstat` の結果。作業場所に無ければ None。"""
    try:
        return os.lstat(path)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 作業場所に無いものは None で返し、呼び手が外す理由にする
        return None


def inspect(cwd: Path, found: Sequence[Target]) -> Inspection:
    """候補を `lstat` し、通常ファイル・作業場所にあるもの・実装計画の本文と、外す理由を返す（git を打たない）。"""
    regular: set[str] = set()
    present: set[str] = set()
    excluded: dict[str, str] = {}
    plans: dict[str, str] = {}
    unreadable: dict[str, str] = {}
    for t in found:
        path = cwd / t.path
        try:
            st = _stat_or_none(path)
        except OSError as exc:
            present.add(t.path)
            excluded[t.path] = f"確かめられない（{type(exc).__name__}: {exc.strerror or exc}）"
            continue
        if st is None:
            excluded[t.path] = "作業場所に無い"
            continue
        present.add(t.path)
        if not stat.S_ISREG(st.st_mode):
            excluded[t.path] = "通常ファイルでない"
            continue
        regular.add(t.path)
        if t.kind != "plan":
            continue
        try:
            plans[t.path] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            unreadable[t.path] = "UTF-8 で読めない"
        except OSError as exc:
            unreadable[t.path] = f"{type(exc).__name__}: {exc.strerror or exc}"
    return Inspection(frozenset(regular), frozenset(present), excluded, plans, unreadable)


def requirements_of(cwd: Path, targets: Sequence[Target]) -> dict[str, list[str]]:
    """設計書ごとの、組になる要求仕様のコピー（作業場所の通常ファイルのもの）。

    組になるのは、設計書の名前に含まれる課題の番号すべての要求仕様である（`issue-7-8-design.md` なら課題 7 と 8）。
    工程の `--issue` が課題 7 だけでも、課題 8 の要求仕様を落とさない。設計書でない対象と、コピーが 1 つも無い設計書は含めない。
    """
    out: dict[str, list[str]] = {}
    for t in targets:
        if t.kind != "design":
            continue
        paths = [REQUIREMENTS_FILE.format(number=n) for n in sorted(issue_docs.design_issues(t.path))]
        found = [rel for rel in paths if _is_regular(cwd / rel)]
        if found:
            out[t.path] = found
    return out


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:  # noqa: NDF401 ファイル: 無い・確かめられない要求仕様は起動指示に載せない
        return False


def _root() -> Path:
    return identity.load().root


def _target_line(t: Target, requirements: Mapping[str, Sequence[str]]) -> str:
    """対象 1 件の行。設計書には、その設計書と組になる要求仕様のコピーを次の行に添える（無ければ「無し」）。"""
    line = f"- `{t.path}`（{t.kind}）"
    if t.kind != "design":
        return line
    paired = "・".join(f"`{r}`" for r in requirements[t.path]) if t.path in requirements else "無し"
    return f"{line}\n  - 組になる要求仕様のコピー: {paired}"


def prompt_text(targets: Sequence[Target], requirements: Mapping[str, Sequence[str]], skill: Path) -> str:
    """起動指示のテンプレートへ対象（設計書ごとの要求仕様のコピーを添える）と手順書のパスを差し込む。読めなければ `Precondition`。"""
    path = _root() / PROMPT_TEMPLATE
    try:
        template = string.Template(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Precondition(f"起動指示のテンプレート {path} を読めない: {exc.strerror or exc}") from None
    return template.substitute(targets="\n".join(_target_line(t, requirements) for t in targets), skill=str(skill))


def _worker_result(done: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """`ndf worker run` の標準出力の最後の空でない行の結果 JSON。無いか形が違えば `BadReport`。"""
    tail = " / ".join(done.stderr.strip().splitlines()[-5:])
    detail = f"ndf worker run の標準出力に結果 JSON が無い（終了コード {done.returncode}）: {tail}"
    try:
        got = result.last_line(done.stdout)
    except ValueError:
        raise BadReport(detail) from None
    if got is None:
        raise BadReport(detail)
    return got


def run_worker(args: RunArgs, prompt: str, runner: Runner) -> tuple[dict[str, Any], Path]:
    """起動指示を保存先へ書き、`ndf worker run` を 1 回打ち、標準出力の結果 JSON と worker の結果ファイルのパスを返す。"""
    prompt_file = args.state_dir / f"{WORK_KIND}.prompt.md"
    result_file = args.state_dir / f"{WORK_KIND}.result.json"
    try:
        args.state_dir.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(prompt, encoding="utf-8")
    except OSError as exc:
        raise Precondition(f"起動指示を保存先 {prompt_file} へ書けない: {exc.strerror or exc}") from None
    argv = [
        str(identity.entrypoint(_root())),
        "worker",
        "run",
        "--kind",
        WORK_KIND,
        "--prompt-file",
        str(prompt_file),
        "--result-file",
        str(result_file),
        "--cwd",
        str(args.cwd),
        "--state-dir",
        str(args.state_dir),
    ]
    return _worker_result(runner(argv, args.cwd)), result_file


def added_items(raw: Any, targets: Sequence[Target]) -> list[dict[str, str]]:
    """worker の足すべき内容を確かめ、`id` を `add-<番号>` に振り直す（I3・I6）。形が違えば `BadReport`。"""
    if not isinstance(raw, list):
        raise BadReport("結果ファイルの items が並びでない")
    paths = {t.path for t in targets}
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise BadReport(f"items の {i} 番目が辞書でない")
        text, doc = item.get("text"), item.get("doc")
        if not isinstance(text, str) or not text.strip():
            raise BadReport(f"items の {i} 番目に text が無い")
        if not isinstance(doc, str) or doc not in paths:
            raise BadReport(f"items の {i} 番目の doc が対象の文書でない")
        out.append({"id": f"{ADD_PREFIX}-{len(out) + 1}", "text": text, "doc": doc})
    return out


def _filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_gap(check: Any) -> Optional[str]:
    if not isinstance(check, dict):
        return "辞書でない"
    if check.get("verdict") not in CHECKED:
        return "verdict が pass・fail でない"
    said = check.get("restatements")
    if not isinstance(said, list) or not said or not all(_filled(s) for s in said):
        return "restatements が空でない文字列の並びでない"
    if check["verdict"] == "fail" and not _filled(check.get("reason")):
        return "fail に reason が無い"
    return None


def _record_gap(rec: Mapping[str, Any], kind: str) -> Optional[str]:
    """記録 1 件の欠け（設計書の「確かめの記録」と詳細設計書の `reader_gaps` の表）。欠けが無ければ None。

    実装計画は `self` だけ、設計書は `self` 以外を使える。原稿はどれでもよい。
    """
    verdict, checks = rec.get("verdict"), rec.get("checks")
    if verdict not in VERDICTS:
        return f"verdict が {'・'.join(VERDICTS)} のどれでもない"
    if not isinstance(checks, list):
        return "checks が並びでない"
    # 詳細設計書の `reader_gaps` の表の「使える種類」の列。原稿はどれも、実装計画は `self` だけ、設計書は `self` 以外。
    usable_kind = kind == "manuscript" or (kind == "plan") == (verdict == "self")
    if not usable_kind:
        return f"{kind} に {verdict} は使えない"
    if verdict == NOT_RUN:
        why = rec.get("reason") if _filled(rec.get("reason")) else "理由なし"
        return f"{NOT_RUN_GAP}（{why}）"
    if verdict not in CHECKED:
        if checks:
            return f"{verdict} の checks が空でない"
        return None if _filled(rec.get("reason")) else f"{verdict} に reason が無い"
    return _checks_gap(rec, verdict, checks)


def _checks_gap(rec: Mapping[str, Any], verdict: str, checks: list[Any]) -> Optional[str]:
    """読み手役の確かめ（`pass`・`fail`）の記録の欠け。"""
    if len(checks) not in (1, 2):
        return "checks の数が 1 か 2 でない"
    for i, check in enumerate(checks):
        bad = _check_gap(check)
        if bad is not None:
            return f"checks の {i} 番目: {bad}"
    if checks[-1]["verdict"] != verdict:
        return "最後の checks の verdict と記録の verdict が合わない"
    if len(checks) == 2 and checks[0]["verdict"] != "fail":
        return "2 回目の確かめは 1 回目が fail のときだけ行う"
    if len(checks) == 1 and verdict == "fail" and not _filled(rec.get("reason")):
        return "2 回目を行えなかった理由（reason）が無い"
    return None


def _records_of(raw: Any, path: str) -> list[Mapping[str, Any]]:
    return [r for r in raw if isinstance(r, dict) and r.get("doc") == path]


def reader_gaps(raw: Any, targets: Sequence[Target]) -> list[dict[str, str]]:
    """確かめの記録（`metrics.readers`）の欠けを、対象の文書ごとの `gaps` の要素にする（I4）。対象の外の記録は見ない。"""
    gaps = []
    for t in targets:
        reason = _reader_gap(raw, t)
        if reason is not None:
            gaps.append({"kind": "reader", "name": t.path, "reason": reason})
    return gaps


def _reader_gap(raw: Any, target: Target) -> Optional[str]:
    """文書 1 件ぶんの確かめの記録の欠けの理由。欠けが無ければ None。"""
    if not isinstance(raw, list):
        return "記録（metrics.readers）が並びでない"
    recs = _records_of(raw, target.path)
    if not recs:
        return "記録が無い"
    if len(recs) > 1:
        return "記録が 2 件以上ある"
    return _record_gap(recs[0], target.kind)


def _clean(rec: Mapping[str, Any]) -> dict[str, Any]:
    """確かめた記録を、結果のスキーマが決めたキーだけにする。`reason` は不合格の理由なので、合格の確かめからは外す。"""
    out: dict[str, Any] = {"doc": rec["doc"], "verdict": rec["verdict"]}
    out["checks"] = [_clean_check(c) for c in rec["checks"]]
    if _filled(rec.get("reason")):
        out["reason"] = rec["reason"]
    return out


def _clean_check(check: Mapping[str, Any]) -> dict[str, Any]:
    out = {"verdict": check["verdict"], "restatements": check["restatements"]}
    if check["verdict"] == "fail":
        out["reason"] = check["reason"]
    return out


def reader_items(readers: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    """`fail` の文書を、パスの順に `reader-<番号>` の項目と `warnings` の文にする（I5）。"""
    items: list[dict[str, str]] = []
    warnings: list[str] = []
    for rec in sorted((r for r in readers if r["verdict"] == "fail"), key=lambda r: r["doc"]):
        why = "・".join(w for w in (rec["checks"][-1].get("reason"), rec.get("reason")) if isinstance(w, str) and w.strip())
        text = f"読み手役が {rec['doc']} を不合格とした: {why}"
        items.append({"id": f"{READER_PREFIX}-{len(items) + 1}", "text": text, "doc": rec["doc"]})
        warnings.append(text)
    return items, warnings


def _worker_stopped(out: Mapping[str, Any]) -> dict[str, Any]:
    """worker が `ok` で終えなかったときの結果（I7。結果ファイルは読まない）。`gate` も `stopped` にする。"""
    said = out.get("summary") or "（要約なし）"
    if out.get("status") == result.GATE:
        return result.stopped(TOOL, "work_stopped", f"組み直す worker が判断を求めた: {said}")
    cause = out.get("cause")
    kind = cause.get("kind") if isinstance(cause, dict) else None
    gaps = out.get("gaps") if kind == "incomplete" else None
    return result.stopped(TOOL, kind or "work_stopped", f"組み直す worker が止まった: {said}", gaps=gaps)


def _read_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadReport(f"worker の結果ファイル {path} を読めない: {exc}") from None
    if not isinstance(report, dict):
        raise BadReport(f"worker の結果ファイル {path} の中身がオブジェクトでない")
    return report


def _reader_summary(
    raw: Any, targets: Sequence[Target], skipped: Sequence[str]
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    """欠けの無い確かめの記録を整形し、(readers, items, warnings) を返す。"""
    records = raw if isinstance(raw, list) else []  # 欠けが無ければ並びである（reader_gaps が確かめた）
    readers = [_clean(_records_of(records, t.path)[0]) for t in targets]
    found, said = reader_items(readers)
    warnings = [*skipped, *said]
    outside = len(records) - len(readers)
    if outside:
        warnings.append(f"対象の外の文書の記録を {outside} 件外した")
    return readers, found, warnings


def _checked(report: Mapping[str, Any], targets: Sequence[Target], skipped: Sequence[str]) -> dict[str, Any]:
    """worker の結果ファイルを確かめて `ok` か `stopped` の結果にする（確かめの順は足すべき内容が先）。"""
    try:
        added = added_items(report.get("items"), targets)
    except BadReport as exc:
        return result.stopped(TOOL, "bad_report", exc.detail)
    metrics = report.get("metrics")
    raw = metrics.get("readers") if isinstance(metrics, dict) else None
    gaps = reader_gaps(raw, targets)
    not_run = [g["name"] for g in gaps if g["reason"].startswith(NOT_RUN_GAP)]
    if not_run:
        detail = f"{NOT_RUN_GAP}文書がある: {'・'.join(not_run)}。読み手役を立てられる環境に直してから打ち直す"
        return result.stopped(TOOL, "precondition", detail)
    if gaps:
        return result.stopped(TOOL, "incomplete", f"確かめの記録が欠けた文書が {len(gaps)} 件ある", gaps=gaps)
    readers, found, warnings = _reader_summary(raw, targets, skipped)
    paths = [t.path for t in targets]
    summary = f"{len(paths)} 件の文書を組み直した（足すべき内容 {len(added)} 件・読み手役の不合格 {len(found)} 件）"
    return result.result(TOOL, result.OK, summary, [*added, *found], {"targets": paths, "readers": readers}, warnings=warnings)


def new_check_id() -> str:
    """確認の id（1 回の実行ごと）。"""
    return uuid.uuid4().hex


def record_reads(g: git.Git, branch: str, report: Mapping[str, Any], targets: Sequence[Target], *, remote: str) -> None:
    """読み手役が読んだ文書を読ませたものの記録へ追記し、完了の行を書く（#4446 の Task 3。受け入れ条件 1）。

    文書は作業場所のブランチで引けるよう `branch` を付け、中身は `git hash-object` の blob の SHA で持つ。worker 自身が読み直した
    文書（`self`）は書かない。書けなければ `Precondition`（完了の行が無いので、次の gate は全体を読ませる）。
    """
    metrics = report.get("metrics")
    raw = metrics.get("readers") if isinstance(metrics, dict) else []
    read = [(t.path, _records_of(raw, t.path)[0]) for t in targets]
    read = [(path, rec) for path, rec in read if rec["verdict"] in CHECKED]
    try:
        repo = g.remote_repo(remote)
        blobs = g.run("hash-object", "--", *(path for path, _ in read)).split() if read else []
        check = new_check_id()
        base = {"repo": repo, "check": check, "branch": branch, "gate": None, "pr": None}
        for (path, rec), blob in zip(read, blobs, strict=True):
            for row in _read_rows(rec):
                reading.append_doc({"kind": "read", **base, "name": path, "hash": blob, "reader": reading.ENGINEER, **row})
        reading.append_doc(reading.done_row(base))
    except (git.GitError, OSError, ValueError, reading.ReadingInvalid) as exc:
        raise Precondition(f"読ませた文書を記録できない: {exc}") from None


def _read_rows(rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """確かめ 1 件の記録を、回ごとの行（回・合否・残った文の引用）にする。2 回目の不合格は `remained`。"""
    out: list[dict[str, Any]] = []
    for i, check in enumerate(rec["checks"], 1):
        if check["verdict"] == "pass":
            out.append({"round": i, "verdict": "passed"})
        elif i == 2:
            out.append({"round": 2, "verdict": "remained", "quotes": [{"quote": check["reason"], "why": ""}]})
        else:
            out.append({"round": i, "verdict": "failed"})
    return out


def checked_out(g: git.Git) -> str:
    """作業場所のブランチの名前。読めなければ `Precondition`。"""
    try:
        return g.run("branch", "--show-current").strip()
    except git.GitError as exc:
        raise Precondition(f"作業場所のブランチを読めない: {exc}") from None


def start_and_changes(g: git.Git, base: str, remote: str) -> tuple[str, list[str]]:
    """起点の参照の名前と、起点から `HEAD` までの差分のパス。決められなければ `Precondition`。

    差分の失敗には起点の参照の名前を頭に付け、包まれない `GitError` は「git を打てない」にする。
    """
    try:
        head = _head_sha(g)
        ref, start = start_point(g, base, remote)
        try:
            changed = diff_paths(g, start, head)
        except Precondition as exc:
            raise Precondition(f"起点 {ref} から差分を読めない: {exc.detail}") from None
    except git.GitError as exc:
        raise Precondition(f"git を打てない: {exc}") from None
    return ref, changed


def drive(args: RunArgs, *, git_of: Callable[[Path], git.Git] = git.Git, runner: Runner = _run) -> dict[str, Any]:
    """起点 → 差分 → 選択 → 起動 → 確かめの順に進め、結果 JSON を返す。"""
    g = git_of(args.cwd)
    try:
        ref, changed = start_and_changes(g, args.base, args.remote)
        found = sd.candidates(changed, args.issues, args.kinds)
        chosen = sd.select(found, inspect(args.cwd, found))
        log.info("組み直す文書を選んだ", extra={"start_ref": ref, "candidates": len(found), "targets": len(chosen.targets)})
        if not chosen.targets:
            summary = f"組み直す文書が無い（種類: {'・'.join(args.kinds)}・起点: {ref}）"
            return result.result(TOOL, result.OK, summary, [], {"targets": []}, warnings=chosen.skipped)
        prompt = prompt_text(chosen.targets, requirements_of(args.cwd, chosen.targets), _root() / SKILL)
        out, result_file = run_worker(args, prompt, runner)
        attempts = out["metrics"].get("attempts") if isinstance(out.get("metrics"), dict) else None
        log.info("組み直す worker が終えた", extra={"worker_status": out.get("status"), "attempts": attempts})
        if out.get("status") != result.OK:
            return _worker_stopped(out)
        report = _read_report(result_file)
        got = _checked(report, chosen.targets, chosen.skipped)
        if got["status"] == result.OK:
            record_reads(g, checked_out(g), report, chosen.targets, remote=args.remote)
    except Stop as exc:
        return result.stopped(TOOL, exc.kind, exc.detail)
    return got
