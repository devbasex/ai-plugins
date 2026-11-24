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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf import cli, contracts, identity
from ndf.adapters import git
from ndf.contracts import result, worker_paths
from ndf.core import issue_docs
from ndf.domains.docrebuild import select_decide as sd
from ndf.domains.docrebuild.select_decide import Inspection, Target

log = logging.getLogger(__name__)

TOOL = cli.tool_of(f"{__package__}.cli")
# `ndf worker run --kind` の値と保存先のファイル名の頭（サブコマンドの名前と同じ）
WORK_KIND = TOOL
RESULT_SCHEMA = contracts.schema_path("io/document-rebuild.result")
PROMPT_TEMPLATE = "contracts/worker/document-rebuild.md"
SKILL = "skills/document-rebuild/SKILL.md"
REQUIREMENTS_FILE = "issues/issue-{number}-requirements.md"
# 保存先の中の起動指示と worker の結果ファイルの名前
PROMPT_NAME = f"{WORK_KIND}.prompt.md"
RESULT_NAME = f"{WORK_KIND}.result.json"
ADD_PREFIX, READER_PREFIX = contracts.schema_enum(RESULT_SCHEMA, "/$defs/id_prefix")
# `git diff A...B` で分岐点が無いときの終了コード（設計書の実測）
NO_MERGE_BASE = 128
WORKTREE_REMOTE = f"{git.WORKTREE_NDF_SETTINGS} の remote"
# 確かめの記録の verdict（すべて）と、読み手役の確かめの verdict
VERDICTS = contracts.schema_enum(RESULT_SCHEMA, "/$defs/reader/properties/verdict")
CHECKED = contracts.schema_enum(RESULT_SCHEMA, "/$defs/reader/properties/checks/items/properties/verdict")

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


class Precondition(Exception):
    """起点か差分を決められない（`stopped`（precondition））。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class BadReport(Exception):
    """worker の結果の形の誤り（`stopped`（bad_report））。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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


class Outside(Exception):
    """パスの親ディレクトリのどれかがリンクかディレクトリでなく、パスが作業場所の中にあると言えない。"""


def _stat_or_none(cwd: Path, rel: str) -> Optional[os.stat_result]:
    """作業場所 `cwd` の中の `rel` の `lstat` の結果。作業場所に無ければ None。

    末尾の名前だけでなく、`cwd` から末尾の手前までの親ディレクトリも 1 つずつ `lstat` し、リンクかディレクトリでない
    要素があれば `Outside` を上げる。親ディレクトリがリンクだと、末尾が通常ファイルでも作業場所の外のファイルを指すためである。
    """
    parts = Path(rel).parts
    try:
        for depth in range(1, len(parts)):
            if not stat.S_ISDIR(os.lstat(cwd.joinpath(*parts[:depth])).st_mode):
                raise Outside(rel)
        return os.lstat(cwd / rel)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 作業場所に無いものは None で返し、呼び手が外す理由にする
        return None


def inspect(cwd: Path, found: Sequence[Target]) -> Inspection:
    """候補を親ディレクトリから `lstat` し、通常ファイル・作業場所にあるもの・実装計画の本文と、外す理由を返す（git を打たない）。"""
    regular: set[str] = set()
    present: set[str] = set()
    excluded: dict[str, str] = {}
    plans: dict[str, str] = {}
    unreadable: dict[str, str] = {}
    for t in found:
        path = cwd / t.path
        try:
            st = _stat_or_none(cwd, t.path)
        except Outside:
            present.add(t.path)
            excluded[t.path] = "親ディレクトリがリンクで、作業場所の中にあると確かめられない"
            continue
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
    """設計書ごとの、組になる要求仕様のコピー（作業場所の中の通常ファイルのもの）。

    組になるのは、設計書の名前に含まれる課題の番号すべての要求仕様である（`issue-7-8-design.md` なら課題 7 と 8）。
    工程の `--issue` が課題 7 だけでも、課題 8 の要求仕様を落とさない。設計書でない対象と、コピーが 1 つも無い設計書は含めない。
    """
    out: dict[str, list[str]] = {}
    for t in targets:
        if t.kind != "design":
            continue
        paths = [REQUIREMENTS_FILE.format(number=n) for n in sorted(issue_docs.design_issues(t.path))]
        found = [rel for rel in paths if _is_regular(cwd, rel)]
        if found:
            out[t.path] = found
    return out


def _is_regular(cwd: Path, rel: str) -> bool:
    try:
        st = _stat_or_none(cwd, rel)
    except (Outside, OSError):  # noqa: NDF401 ファイル: 作業場所の外を指す・確かめられない要求仕様は起動指示に載せない
        return False
    return st is not None and stat.S_ISREG(st.st_mode)


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
    lines = [ln for ln in done.stdout.splitlines() if ln.strip()]
    tail = " / ".join(done.stderr.strip().splitlines()[-5:])
    detail = f"ndf worker run の標準出力に結果 JSON が無い（終了コード {done.returncode}）: {tail}"
    try:
        got = result.received(json.loads(lines[-1])) if lines else None
    except ValueError:
        raise BadReport(detail) from None
    if got is None:
        raise BadReport(detail)
    return got


def _argv(wd: worker_paths.WorkDir, cwd: Path) -> list[str]:
    """`ndf worker run` の引数。保存先は開いて解いたパスで渡し、`--result-dir-id` で開いた保存先と突き合わせさせる。"""
    dev, ino = worker_paths.dir_id(wd)
    return [
        str(identity.entrypoint(_root())),
        "worker",
        "run",
        "--kind",
        WORK_KIND,
        "--prompt-file",
        str(wd.path / PROMPT_NAME),
        "--result-file",
        str(wd.path / RESULT_NAME),
        "--cwd",
        str(cwd),
        "--state-dir",
        str(wd.path),
        "--result-dir-id",
        f"{dev}:{ino}",
    ]


def run_worker(args: RunArgs, prompt: str, runner: Runner) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """起動指示を保存先へ書き、`ndf worker run` を 1 回打ち、標準出力の結果 JSON と、`ok` なら worker の結果ファイルの中身を返す。

    保存先は 1 回だけ開いた記述子を最後まで持つ（`worker_paths.open_or_create`）。起動指示は既存のファイルを開かずに
    私有の新しいファイルとして置き換え（`write_file(private=True)`）、既存のリンクをたどって別のファイルを上書きしない。
    `ndf worker run` は起動指示をパスで開き直すので、渡す前に名前と祖先をほかの利用者が差し替えられないことを確かめる。
    結果ファイルは、`--result-dir-id` で同じ保存先を確かめた `ndf worker run` が `ok` を返した後に、同じ記述子から
    呼んだ利用者の私有のファイルだけを 1 回読む（`read_file(owned=True)`）。名前を開き直さないので、別の場所や
    別の利用者が差し替えた結果を後ろの確かめへ渡さない。読めなければ `BadReport`、保存先を使えなければ `Precondition`。
    """
    try:
        with worker_paths.open_or_create(args.state_dir) as wd:
            worker_paths.write_file(wd, PROMPT_NAME, prompt, private=True)
            worker_paths.check_unshared(wd, PROMPT_NAME)
            out = _worker_result(runner(_argv(wd, args.cwd), args.cwd))
            report = _read_report(wd) if out.get("status") == result.OK else None
    except (worker_paths.EntryError, OSError) as exc:
        raise Precondition(f"起動指示と結果の保存先 {args.state_dir} を使えない: {exc}") from None
    return out, report


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
        if doc not in paths:
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
    if (kind == "plan") != (verdict == "self") and kind != "manuscript":
        return f"{kind} に {verdict} は使えない"
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
        if not isinstance(raw, list):
            reason: Optional[str] = "記録（metrics.readers）が並びでない"
        else:
            recs = _records_of(raw, t.path)
            reason = "記録が無い" if not recs else "記録が 2 件以上ある" if len(recs) > 1 else _record_gap(recs[0], t.kind)
        if reason is not None:
            gaps.append({"kind": "reader", "name": t.path, "reason": reason})
    return gaps


def _clean(rec: Mapping[str, Any]) -> dict[str, Any]:
    """確かめた記録を、結果のスキーマが決めたキーだけにする。"""
    out: dict[str, Any] = {"doc": rec["doc"], "verdict": rec["verdict"]}
    out["checks"] = [{k: c[k] for k in ("verdict", "restatements", "reason") if k in c} for c in rec["checks"]]
    if _filled(rec.get("reason")):
        out["reason"] = rec["reason"]
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


def _read_report(wd: worker_paths.WorkDir) -> dict[str, Any]:
    """開いた保存先 `wd` の結果ファイルを、呼んだ利用者の私有のファイルだけ 1 回読む。無い・読めない・形が違えば `BadReport`。"""
    path = wd.path / RESULT_NAME
    try:
        text = worker_paths.read_file(wd, RESULT_NAME, owned=True)
        report = json.loads(text) if text is not None else None
    except (worker_paths.EntryError, ValueError) as exc:
        raise BadReport(f"worker の結果ファイル {path} を読めない: {exc}") from None
    if text is None:
        raise BadReport(f"worker の結果ファイル {path} が無い")
    if not isinstance(report, dict):
        raise BadReport(f"worker の結果ファイル {path} の中身がオブジェクトでない")
    return report


def _checked(report: Mapping[str, Any], targets: Sequence[Target], skipped: Sequence[str]) -> dict[str, Any]:
    """worker の結果ファイルを確かめて `ok` か `stopped` の結果にする（確かめの順は足すべき内容が先）。"""
    try:
        added = added_items(report.get("items"), targets)
    except BadReport as exc:
        return result.stopped(TOOL, "bad_report", exc.detail)
    metrics = report.get("metrics")
    raw = metrics.get("readers") if isinstance(metrics, dict) else None
    gaps = reader_gaps(raw, targets)
    if gaps:
        return result.stopped(TOOL, "incomplete", f"確かめの記録が欠けた文書が {len(gaps)} 件ある", gaps=gaps)
    records = raw if isinstance(raw, list) else []  # 欠けが無ければ並びである（reader_gaps が確かめた）
    readers = [_clean(_records_of(records, t.path)[0]) for t in targets]
    found, said = reader_items(readers)
    warnings = [*skipped, *said]
    outside = len(records) - len(readers)
    if outside:
        warnings.append(f"対象の外の文書の記録を {outside} 件外した")
    paths = [t.path for t in targets]
    summary = f"{len(paths)} 件の文書を組み直した（足すべき内容 {len(added)} 件・読み手役の不合格 {len(found)} 件）"
    return result.result(TOOL, result.OK, summary, [*added, *found], {"targets": paths, "readers": readers}, warnings=warnings)


def drive(args: RunArgs, *, git_of: Callable[[Path], git.Git] = git.Git, runner: Runner = _run) -> dict[str, Any]:
    """起点 → 差分 → 選択 → 起動 → 確かめの順に進め、結果 JSON を返す。"""
    g = git_of(args.cwd)
    try:
        head = _head_sha(g)
        ref, start = start_point(g, args.base, args.remote)
        try:
            changed = diff_paths(g, start, head)
        except Precondition as exc:
            raise Precondition(f"起点 {ref} から差分を読めない: {exc.detail}") from None
    except Precondition as exc:
        return result.stopped(TOOL, "precondition", exc.detail)
    except git.GitError as exc:
        return result.stopped(TOOL, "precondition", f"git を打てない: {exc}")
    found = sd.candidates(changed, args.issues, args.kinds)
    chosen = sd.select(found, inspect(args.cwd, found))
    log.info("組み直す文書を選んだ", extra={"start_ref": ref, "candidates": len(found), "targets": len(chosen.targets)})
    if not chosen.targets:
        summary = f"組み直す文書が無い（種類: {'・'.join(args.kinds)}・起点: {ref}）"
        return result.result(TOOL, result.OK, summary, [], {"targets": []}, warnings=chosen.skipped)
    try:
        prompt = prompt_text(chosen.targets, requirements_of(args.cwd, chosen.targets), _root() / SKILL)
        out, report = run_worker(args, prompt, runner)
    except Precondition as exc:
        return result.stopped(TOOL, "precondition", exc.detail)
    except BadReport as exc:
        return result.stopped(TOOL, "bad_report", exc.detail)
    attempts = out["metrics"].get("attempts") if isinstance(out.get("metrics"), dict) else None
    log.info("組み直す worker が終えた", extra={"worker_status": out.get("status"), "attempts": attempts})
    if out.get("status") != result.OK or report is None:
        return _worker_stopped(out)
    return _checked(report, chosen.targets, chosen.skipped)
