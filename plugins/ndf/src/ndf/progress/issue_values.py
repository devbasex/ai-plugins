"""工程の差し込みのうち課題から写す値（`title`・`body_file`・`issue`）と、その写しの後始末（`ndf.progress.invoke` から分けた）。

課題の題と本文は試行ごとにプランの実行の保存先へ初回に 1 度だけ写し、同じ試行の以後の呼び出しは写しを使う。
新しい試行の始まりに `clear_values` が写しと差し込みの値（単位ごとの保存先の分と単位の並びを含む）を消す。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import git, github
from ndf.contracts import worker_paths
from ndf.contracts.worker_kinds import VALUES_FILE
from ndf.core import route

# 課題の本文を書き出す保存先のファイルと、題の写し（プランの実行の保存先の下）
ISSUE_BODY_FILE = "issue-{number}-body.md"
ISSUE_TITLE_FILE = "issue-{number}-title.json"
# 試行の始まりに消す、前の試行が保存先に残した課題の題・本文のコピー（差し込みの値 `VALUES_FILE` と一緒に消す）
ISSUE_COPIES = (ISSUE_BODY_FILE.format(number="*"), ISSUE_TITLE_FILE.format(number="*"))
# 工程を単位ごとに流すときの、単位の並びのファイルと、単位ごとの実行の保存先を置くディレクトリ（工程の保存先の下。#4369）
UNITS_FILE = "units.json"
UNITS_DIR = "units"


def issue_text(location: Path, number: int, repo: Optional[str] = None) -> Optional[tuple[str, str]]:
    """課題の題と本文（`repo`、省けば作業場所のリモートの GitHub から読み、書き手の権限もそこで確かめる）。テストが差し替える。

    書き手を信用しない（`GitHub.trusts` が偽。#3364）なら None。本文は作業の入力として指示に読まれるためである。
    """
    hub = github.GitHub(repo if repo is not None else git.Git(location).remote_repo(), github.client())
    title, authored = hub.issue_text(number)
    return (title, authored.body) if hub.trusts(authored) else None


def issue_numbers(inputs: Mapping[str, Any]) -> list[str]:
    """工程の入力の課題の番号（小さい順。先頭が代表の課題）。入力に課題が無ければ空。"""
    issues = inputs["issues"] if "issues" in inputs else None
    return [str(n) for n in sorted(int(n) for n in issues)] if isinstance(issues, list) else []


def issue_values(
    inputs: Mapping[str, Any], work_dir: Path, location: Path, *, number: Optional[int] = None, repo: Optional[str] = None
) -> Optional[dict[str, route.Value]]:
    """代表の課題の題（title）・本文を書いたファイル（body_file）・番号（issue）。課題が 1 つも無ければ空。

    `number`・`repo` を渡せば、その課題をそのリポジトリから読む（#4369）。題のコピーは読んだ `repo` も持ち、`repo` の違う
    コピーは使わずに読み直す。読み直すときは題のコピーを消してから本文、題のコピーの順に書き、途中で止まっても前の題と
    新しい本文を組にしない。

    課題が 2 件以上なら、番号の最も小さい課題を代表にする（#3908 の決定 7。実装のフェーズの単位の決まりと同じ選び方で、
    PR の題と本文は代表の課題のものになる）。受け入れ条件の確かめはすべての課題を検証する（`route.launch_values`）。
    題と本文は試行ごとに保存先へ初回に 1 度だけコピーし（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE`）、同じ試行の以後の呼び出し
    （承認の回答・続き・プランの打ち直し）はそのコピーを使う。工程の新しい試行は `clear_values` がコピーを消し、課題と作成者の
    権限を読み直す。同じ試行の中で読み直すと GitHub を毎回呼び、途中で題が直されるとプランが変わって、終わっていない回へ
    届かず開始点から流れ直すためである（PR 3469 の指摘）。
    書き手を信用しない課題は、本文を書き出さずに None（足りない値の出所を `invoke.FROM_UNTRUSTED_ISSUE` にする）。
    読めない失敗（`GitError`・`github.GitHubError`）は捕まえずに `invoke._call_plan` へ上げる。空にすると、打ち直せば通る
    失敗が打ち直せない「値が無い」（invalid）に変わり、本当の理由も消えるためである。

    コピーは `values.json` と同じく保存先を 1 回だけ開いた記述子から読み書きする。読むコピーは呼んだ利用者が
    持ち、ほかの利用者が書き込めないものだけで、ほかは `worker_paths.EntryUnsafe`（`foreign` など。`invoke.plan_failure` が
    precondition にする）。作成者の権限を確かめる前に、共有の保存先へ別の利用者が置いた題と本文を worker へ渡さない。
    書くときもグループとほかの利用者の書き込み権限を外して公開し（`write_file(private=True)`）、`body_file` は開いて解いた
    保存先のパスで返す（共有の領域のリンクを経ない）。PR を作るステップ（`gh pr create --body-file`）はこの名前を開き直すので、
    返す前に、コピーの名前と保存先から根までのどの項目もほかの利用者が差し替えられないことを確かめる
    （`worker_paths.check_unshared`。差し替えられれば `EntryUnsafe`（`shared`）で止め、確かめたコピーと別の本文を送らない）。
    """
    if number is None:
        issues = issue_numbers(inputs)
        if not issues:
            return {}
        number = int(issues[0])
    out_name, copy_name = ISSUE_BODY_FILE.format(number=number), ISSUE_TITLE_FILE.format(number=number)
    with worker_paths.open_or_create(work_dir) as wd:
        out = wd.path / out_name
        title = _held_title(wd, copy_name, out_name, repo)
        if title is None:
            found = issue_text(location, number, repo)
            if found is None:
                return None
            title, body = found
            worker_paths.remove_file(wd, copy_name)
            worker_paths.write_file(wd, out_name, body, private=True)
            worker_paths.write_file(wd, copy_name, json.dumps({"title": title, "repo": repo}, ensure_ascii=False), private=True)
        worker_paths.check_unshared(wd, out_name)
    return {"title": title, "body_file": str(out), "issue": str(number)}


def _held_title(wd: worker_paths.WorkDir, copy_name: str, body_name: str, repo: Optional[str]) -> Optional[str]:
    """同じ試行が `repo` からコピーした題。題のコピーと本文のコピーがそろい、どちらも呼んだ利用者の私有のファイルなら題、
    そろわないか、別のリポジトリのコピーなら None。

    私有でないコピー（別の利用者が持つ・書き込める・リンク）は `worker_paths.EntryUnsafe` を上げ、読み直して上書きしない。
    """
    text = worker_paths.read_file(wd, copy_name, owned=True)
    if text is None or worker_paths.read_file(wd, body_name, owned=True) is None:
        return None
    try:
        held = json.loads(text)
    except json.JSONDecodeError:  # noqa: NDF401 ファイル: 書きかけ・壊れたコピーは無いものとして読み直す
        return None
    if not isinstance(held, dict) or held.get("repo") != repo:
        return None
    return held["title"] if isinstance(held.get("title"), str) else None


def clear_values(state_dirs: Iterable[Path]) -> None:
    """渡された保存先から前の試行の値を消す（無ければ何もしない）。保存先は NDF の状態の保存先の下である。

    差し込みの値（`VALUES_FILE`）と課題のコピー（`ISSUE_COPIES`）を消す。コピーは差し込みの値の有無によらず消す。残すと
    `issue_values` がコピーを優先し、新しい試行が GitHub で直した題・本文を使わず、作成者の権限も確かめ直さない
    （前の試行の後で write 権限を失った作成者の本文を worker に読ませる。PR 4289 の指摘）。
    マージの方式を決められずに止まった目印（`merge_pending.PENDING_FILE`）は消さない（打ち直しが方式を先に問うため）。
    工程を単位ごとに流した保存先は、単位の並び（`UNITS_FILE`）と、`UNITS_DIR` の下のすべての単位の差し込みの値と課題の
    コピーも消す。単位の回の記録とマージの結果は残す（打ち直しが再開の位置を決める。#4369）。
    消せなければ `OSError` をそのまま上げる（呼び手の `progress.run._prepare` が precondition にする）。
    """
    for state_dir in state_dirs:
        units = state_dir / UNITS_DIR
        for pattern in (*ISSUE_COPIES, VALUES_FILE):
            for found in (*state_dir.glob(pattern), *units.rglob(pattern)):
                found.unlink(missing_ok=True)
        (state_dir / UNITS_FILE).unlink(missing_ok=True)
