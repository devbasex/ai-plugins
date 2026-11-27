"""承認ゲートの直前の MVV 判定（`ndf records mvv gate`。#3745・#3017・#3959）。

工程の定義で承認ゲート（設計・リリース）の直前に置く判定のステップの本体。進め方によらず、判定する PR のベースブランチの
承認済みの MVV があれば LLM のアダプター（`ndf.adapters.llm`）で判定を打ち（`mvv.mvv_validate_sprint`。同じ材料の「従う」は
記録を再利用する）、承認ゲートを省けるか（`ndf.progress.gate.checks.may_skip`）を出力の `mvv_gate` に書く。進め方（pace）と
承認ゲートの区分は省くかだけを決め、省けるのは fast・auto の設計承認ゲートだけである（リリース承認ゲートは #4105 まで省かない）。
承認ゲートのステップ（`ndf.progress.run`）はその結果だけを読み、省けるなら回答した者 `service:mvv` の承認で通す。

MVV とレッドラインのパスは PR のベースのコミットから 1 度だけ読み（worktree のファイルは読まない）、差分も同じコミットと head の組から読む。
差分を読んだ後の結果は、PR のコメントへ書く直前に PR を読み直し、head かマージ先が変わっていれば書かずに止める。コメントには判定した head を載せる。
判定の材料は、共通原則の本文（`contracts/data/ndf-common-principles.md`）・プロジェクト MVV の本文・PR の本文と差分。
レッドライン（必ず人の承認が要る操作）は共通原則とプロジェクト MVV の表から引き、材料の先に置く。
判定を打てないときは承認ゲートを省かず、理由を `why` に書いて人へ問う。失敗の原因は定型文と手元の診断の記録の識別子だけを
結果と PR に載せる（`failure_text`）。PR を読めた後の結果は、決めた終わり方でだけ判定の記録（`checks.note`）を PR のコメントへ載せる。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts, identity
from ndf.adapters import github, llm, publish, run_log
from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError, Pull, Unread
from ndf.contracts import internal, limits
from ndf.core import approval_words, issue_docs, merge_approval, production_merge
from ndf.domains.records import design_check_rules, files, jsonfile, jsonl, mvv
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress import definition
from ndf.progress.gate.checks import MvvFacts, judgeable, may_skip, note, refusals
from ndf.progress.model import MVV_GATE_STEPS, MVV_PACES, MVV_SKIP_GATES

# 判定の回答のスキーマ（contracts/schemas/mvv-verdict.schema.json）
VERDICT_SCHEMA = "mvv-verdict"
# 判定の 1 回の呼び出しの上限
JUDGE_LIMIT = "supervise.judge-step"
# 承認ゲートの区分（設計は作業場所のブランチの PR、リリースは文脈の pr を判定する）
DESIGN, RELEASE = "design", "release"
# 区分 → 判定のステップの id（`progress.model` の 1 か所の表）と、そのステップを持つ工程の文書の名前（`stages/<名前>.json`）
GATE_STEPS = MVV_GATE_STEPS
GATE_STAGES = {DESIGN: "design", RELEASE: "deploy"}
# 省けない区分（リリース）の判定が why に足す理由（#3959 の決定 3）
RELEASE_KEPT = "リリース承認ゲートは、保留を読む PR の範囲が決まるまで（#4105）MVV 判定で省かない"
# 手元のどの参照にも MVV の設定が無いときの理由（`mvv_declared_locally`。#3959）
NO_DECL = "MVV の設定が手元の HEAD にもリモートの追跡ブランチにも無いため、MVV 判定を打たない"
# 記録を読めないときの保留の頭（#3959）
RECORDS_UNREADABLE = "MVV の記録（mvv-validation.jsonl）を読めない: "
# 省ける判定のときに読む設計文書の「人へ残す判断」の表で、省かない理由の頭の文（#2912 の決定 7）
TABLE_KEPT = "設計文書の『人へ残す判断』の表に、レッドラインに当たる判断がある: "
TABLE_UNREADABLE = "設計文書の本文を読めないため、『人へ残す判断』の表を確かめられない: "
# 判定の 1 回の呼び出し（質問・材料・回答のスキーマ・上限の秒 → 回答。キーワード `before_retry` は打ち直しの起動の直前に呼ぶ
# 確かめ。`llm.ask` と同じ）。テストはスタブへ差し替える
Ask = Callable[..., llm.JudgeAnswer]
# 判定するリポジトリ（root）と解いたリモート名から、GitHub の接続先を開く。テストは偽物へ差し替える
OpenHub = Callable[[Path, str], GitHub]


class GateError(Exception):
    """判定を打てない（PR が無い・LLM が回答しない）。承認ゲートは省かずに人へ問う。"""


class _Moved(Exception):
    """LLM の打ち直しを起動する直前に PR が動いていた・読み直せなかった。`outcome` は止まった結果（コメントを書かない）。"""

    def __init__(self, outcome: dict[str, Any]) -> None:
        super().__init__(outcome["stopped_by"]["kind"])
        self.outcome = outcome


def boundary_paths(root: Path, ref: str) -> tuple[str, ...]:
    """コミット `ref` の進め方の設定のレッドラインのパス（設定かキーが無ければ空。形が壊れていれば `jsonfile.JsonFileError`）。

    worktree のファイルでなくベースのコミットから読む（PR が規則を緩めても、今回の変更にはベースの規則が効く。#3959）。
    """
    where = f"{ref}:{files.relpath('pace')}"
    (raw,) = Git(root).blobs(where)
    if raw is None:
        return ()
    try:
        decl = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise jsonfile.JsonFileError(f"{where} の進め方の設定を JSON として解けない: {exc}") from exc
    if not isinstance(decl, dict):
        raise jsonfile.JsonFileError(f"{where} の進め方の設定が JSON の object でない")
    if "boundary_paths" not in decl:
        return ()
    held = decl["boundary_paths"]
    # 書き損じた設定を「保護するパスが無い」と読むと、保護したパスを変える PR を人の承認なしに通しうる（壊れた設定は人へ問う）
    if not isinstance(held, list) or not all(isinstance(p, str) for p in held):
        raise jsonfile.JsonFileError(f"{where} の boundary_paths が文字列の配列でない")
    return tuple(held)


def mvv_declared_locally(root: Path, remote: str) -> bool:
    """手元の HEAD かリモート `remote` の追跡ブランチのどれかのコミットに、MVV の設定があるか（ネットワークに出ない。#3959）。

    どこにも無いと確かめられたときだけ偽にし、PR の取得とベースの取得を省く。手元が古くて見落としても、判定を打たずに人へ
    問うだけで、承認ゲートを省く側へは倒れない。どれも取得しない `Git.offline` で、blob を読まずにツリーだけでパスの有無を見る
    （部分クローンで未取得の blob を無いと読まない）。worktree の未追跡の下書きは数えない。
    - `git ls-tree HEAD -- <パス>` が行を返せば真（MVV を導入したリポジトリでは、git の起動はこの 1 回）
    - 無ければ、追跡ブランチの指すコミットのツリーをパスの階層ごとにたどる（`_in_trees`）
    - git が失敗した・ツリーが手元に無くて確かめられなければ真にし、今までどおり PR とベースを取得する経路へ進む
    """
    g = Git.offline(root)
    path = mvv.decl_path()
    try:
        if g.run("ls-tree", "HEAD", "--", path).strip():
            return True
        tips = g.run("rev-parse", f"--remotes={remote}").split()
        return _in_trees(g, tips, path) is not False
    except GitError:  # noqa: NDF401 外部コマンド: 手元で確かめられなければ、MVV があるものとして今までの経路（PR とベースの取得）へ進む
        return True


def _in_trees(g: Git, commits: list[str], path: str) -> Optional[bool]:
    """コミット `commits` のどれかのツリーに `path` があれば真、どれにも無いと確かめられれば偽、手元のツリーが欠けて
    確かめられないコミットがあれば None。階層ごとに `git cat-file --batch` を 1 回起動し、ツリーだけを読む。

    同じコミット・同じサブツリーを指す ID は 1 度だけ問い合わせる。次の階層のツリーが 1 つも見つからなければ、そこで起動を止める。
    """
    wanted = list(dict.fromkeys(f"{c}^{{tree}}" for c in commits))
    parts = path.split("/")
    unsure = False
    for depth, name in enumerate(parts):
        if not wanted:
            break
        raw = g.run_bytes("cat-file", "--batch", stdin="".join(f"{w}\n" for w in wanted).encode("utf-8"))
        missed, hits = _find_entries(raw, len(wanted), name.encode("utf-8"))
        unsure = unsure or missed
        if depth == len(parts) - 1:
            return True if hits else (None if unsure else False)
        wanted = list(dict.fromkeys(oid for oid, is_tree in hits if is_tree))
    return None if unsure else False


def _find_entries(raw: bytes, count: int, name: bytes) -> tuple[bool, list[tuple[str, bool]]]:
    """`git cat-file --batch` の `count` 件の応答から、各ツリーの `name` のエントリー（オブジェクト ID, ツリーか）を集める。

    最初の値は、手元に無い・ツリーでない応答があったか。応答はオフセットで走査し、本体や残りの応答をコピーしない。
    """
    missed, pos = False, 0
    hits: list[tuple[str, bool]] = []
    for _ in range(count):
        end = raw.find(b"\n", pos)
        if end < 0:  # 応答が途中で切れた
            return True, hits
        head = raw[pos:end].split(b" ")
        pos = end + 1
        if len(head) != 3:  # `<名前> missing` など、本体の続かない応答
            missed = True
            continue
        body_end = pos + int(head[2])
        if head[1] != b"tree":
            missed = True
        else:
            hit = _tree_entry(raw, pos, body_end, name, len(head[0]) // 2)
            if hit is not None:
                hits.append(hit)
        pos = body_end + 1
    return missed, hits


def _tree_entry(raw: bytes, start: int, end: int, name: bytes, width: int) -> Optional[tuple[str, bool]]:
    """`raw[start:end]` のツリーの本体で `name` のエントリーを探し、（オブジェクト ID, ツリーか）を返す。無ければ None。"""
    pos = start
    while pos < end:
        space = raw.index(b" ", pos, end)
        nul = raw.index(b"\0", space, end)
        if nul - space - 1 == len(name) and raw.startswith(name, space + 1):
            return raw[nul + 1 : nul + 1 + width].hex(), raw[pos:space] == b"40000"
        pos = nul + 1 + width
    return None


def snapshot_facts(root: Path, snap: mvv.Snapshot, inputs: Mapping[str, Any]) -> tuple[MvvFacts, str]:
    """機械の検査の事実（PR の本文と変えたファイルを除く）と、プロジェクト MVV の本文。

    どれもベースの MVV `snap` とそのコミットから作り、worktree の MVV の設定・本文・進め方の設定は読まない（#3959）。
    """
    ref = inputs["mvv_ref"] if isinstance(inputs.get("mvv_ref"), str) else None
    paths = boundary_paths(root, snap.commit)
    approved = snap.state == md.State.APPROVED and snap.path is not None
    body = snap.path.read_text(encoding="utf-8") if approved and snap.path is not None else ""
    sha = snap.sha256 if approved else None
    return MvvFacts(snap.state, sha, sha, ref, str(inputs.get("mode")), boundary_paths=paths), body


def _common_dir(where: Path) -> str:
    return Git(where).run("rev-parse", "--path-format=absolute", "--git-common-dir").strip()


def _is_worktree_of(where: Path, root: Path) -> bool:
    """作業場所 `where` が `root` と同じリポジトリか。同じパスなら Git を呼ばず、違うパスのときだけ共通ディレクトリを比べる。"""
    try:
        if where.resolve() == root.resolve():
            return True
        return _common_dir(where) == _common_dir(root)
    except (GitError, OSError):  # noqa: NDF401 外部コマンド: worktree でないパスは同じリポジトリでないとして呼ぶ側の GateError で止める
        return False


def release_pr(inputs: Mapping[str, Any]) -> int:
    """リリースの承認ゲートで判定する PR の番号（文脈の `pr`）。GitHub へつながない。無ければ `GateError`。"""
    if not isinstance(inputs.get("pr"), int):
        raise GateError("文脈にスプリントの PR の番号（pr）が無い")
    return int(inputs["pr"])


def pr_number(root: Path, gate: str, inputs: Mapping[str, Any], hub: GitHub) -> int:
    """判定する PR の番号。無い・決められなければ `GateError`。

    リリースは文脈の `pr`。設計は作業場所（入力の `worktree`。無ければ `root`）のブランチの開いた PR で、文脈の `design_pr` は
    読まない（プランの経路の入力に無いので、`NDF_STAGE_INPUT` と `--machine` の両方で同じ PR を選ぶため。#3959 の決定 5）。
    作業場所が判定するリポジトリの worktree でなければ、そのブランチ名で PR を探さない。マージ先が判定するリポジトリで、head も
    同じリポジトリのもの（フォークの同名ブランチを除く）を数え、1 件に決まらなければ止める（どれにもコメントを書かない）。
    """
    if gate == RELEASE:
        return release_pr(inputs)
    where = Path(inputs["worktree"]) if isinstance(inputs.get("worktree"), str) else root
    if not _is_worktree_of(where, root):
        raise GateError(f"作業場所 {where} が判定するリポジトリの worktree でない")
    branch = Git(where).run("rev-parse", "--abbrev-ref", "HEAD").strip()
    opened = hub.open_pulls_of_head(branch, enough=2)
    if not opened:
        raise GateError(f"ブランチ {branch} の開いた設計の PR が無い")
    if len(opened) > 1:
        raise GateError(f"ブランチ {branch} の開いた PR が複数あり、判定する PR を 1 つに決められない")
    return opened[0].number


def review_prs(gate_name: str, number: int) -> list[int]:
    """保留を読む PR。設計は判定する PR、リリースは #4105 まで空（読まない。省かないので人が回答する）。"""
    return [number] if gate_name == DESIGN else []


def _repo_of(where: Mapping[str, Any]) -> Optional[str]:
    root = where.get("root")
    if root is None:
        return None
    try:
        return mvv.repo_key(Path(root))
    except mvv.MvvError:  # noqa: NDF401 外部コマンド: メインディレクトリを解けなければ、診断の記録の repo を null にして書く
        return None


def failure_text(kind: str, exc: BaseException | str, where: Mapping[str, Any]) -> str:
    """失敗の原因を、PR のコメントと承認資料へ載せてよい文（「<定型文>（診断の記録 <id>）」）に変える（#3959）。

    例外の文は返す文に入れない。例外の文には手元のパス・社内の Git ホスト名・他のリポジトリへの参照が入りえ、伏せる処理は
    形の決まらない値を見分けないためである。全文は秘密の値だけを伏せて手元の診断の記録（`mvv.record_failure`）へ書く。
    `where` の `gate`・`pr`・`head` を記録に写し（無ければ null）、`root` があれば記録の `repo` を解く。記録に書けなくても例外を
    出さず「<定型文>（診断の記録に書けない）」を返す。GitHub も公開範囲の照会も呼ばない。
    """
    return f"{mm.STOP_REASONS[kind]}（{diagnosis(kind, exc, where)}）"


def diagnosis(kind: str, exc: BaseException | str, where: Mapping[str, Any]) -> str:
    """`failure_text` の括弧の中（「診断の記録 <id>」か「診断の記録に書けない」）。診断の記録を 1 件だけ書く。

    1 つの失敗を複数の理由に載せるときは、これを 1 回だけ呼び、どの理由からも同じ識別子を指す。
    """
    row = {
        "at": jsonl.now(),
        "repo": _repo_of(where),
        **{k: where.get(k) for k in ("gate", "pr", "head")},
        "kind": kind,
        "cause": run_log.redact(str(exc)),
    }
    try:
        ident = mvv.record_failure(row)
    except (OSError, internal.InternalError):  # noqa: NDF401 ファイル: 記録に書けないことを理由の文に残して呼ぶ側へ返す
        return "診断の記録に書けない"
    return f"診断の記録 {ident}"


def judge_with(ask: Ask, before_retry: Optional[Callable[[], None]] = None) -> mvv.Judge:
    """`mvv.mvv_validate_sprint` へ渡す判定の関数。回答の判定の語と、費用・判定の中身（理由・レッドライン・根拠）を返す。

    `before_retry` は、回答が形に合わず `ask` が LLM を起動し直す直前に呼ぶ確かめ（`ask` へそのまま渡す）。例外で打ち直しを止める。
    """
    schema = contracts.load_json(contracts.schema_path(VERDICT_SCHEMA))

    def judge(inputs: Mapping[str, str]) -> tuple[str, Mapping[str, Any]]:
        got = ask(inputs["question"], inputs["material"], schema, limits.seconds(JUDGE_LIMIT), before_retry=before_retry)
        if not got.ok:
            raise GateError(f"MVV 判定の回答が無い: {got.cause}")
        return str(got.answer["verdict"]), {"usd": got.cost, **{k: got.answer[k] for k in md.VERDICT_DETAILS}}

    return judge


def _verdict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"verdict": row["verdict"], **{k: row[k] for k in md.VERDICT_DETAILS if k in row}}


class _Judging:
    """PR を読んだ後の 1 回の判定（`_judged`）。失敗の原因は `failure_text` の定型文と診断の記録の識別子だけを結果に入れる。"""

    def __init__(self, root: Path, gate_name: str, hub: GitHub, number: int, pull: Pull) -> None:
        self.root, self.gate, self.hub, self.number, self.pull = root, gate_name, hub, number, pull
        self.where = {"root": root, "gate": gate_name, "pr": number, "head": pull.head_sha}

    def text(self, kind: str, exc: BaseException | str) -> str:
        return failure_text(kind, exc, self.where)

    def stopped(self, kind: str, why: list[str], noted: bool = True, holds: Optional[list[str]] = None) -> dict[str, Any]:
        """判定を持たずに終わる。`noted` なら判定の記録を PR のコメントへ書く（材料が今の PR に属さないときは書かない）。"""
        out = mm.stop_outcome(self.gate, kind, why, holds, pull=(self.number, self.pull.head_sha))
        return _recorded(self.hub, self.number, out, self.where) if noted else out

    def moved(self) -> Optional[dict[str, Any]]:
        """PR を読み直し、判定の材料にした head・マージ先・本文のどれかが (1) と違えば、止まった結果（コメントを書かない）を返す。

        本文も材料なので照らす（本文の編集では head が変わらない）。読み直せないときも、材料が今の PR と同じか確かめられないので止める。
        マージ先のブランチの HEAD が進んだかは照らさない（判定はベースのコミット `snap.commit` に固定し、その HEAD と照らすのは
        #4106 が判定を使う時点である）。
        """
        try:
            again = self.hub.pull(self.number)
        except GitHubError as exc:
            why = ["判定の間に PR を読み直せない: " + self.text("pr_reread_failed", exc)]
            return self.stopped("pr_reread_failed", why, noted=False)
        if (again.head_sha, again.base_ref, again.body) == (self.pull.head_sha, self.pull.base_ref, self.pull.body):
            return None
        moved = f"{self.pull.base_ref}@{self.pull.head_sha} → {again.base_ref}@{again.head_sha}"
        if again.body != self.pull.body:
            moved += "（本文が変わった）"
        return self.stopped("pr_moved", [self.text("pr_moved", moved)], noted=False)

    def still_current(self) -> None:
        """手順 (7): LLM の打ち直しを起動する直前に `moved` で確かめ、動いていれば `_Moved` で起動を止める。

        1 回目の起動の前は確かめない。差分を読み終えてから 1 回目を起動するまでは数秒で、その間に PR が動くことはまれである。
        確かめると LLM で判定するたびに PR の読み取りが 1 回増えるが、起動を省ける場面はほとんど無い。打ち直しは 1 回目の回答を
        待った後なので PR が動いていることがあり、読み取りが増えるのも打ち直すときだけである（#3959）。
        """
        stop = self.moved()
        if stop is not None:
            raise _Moved(stop)

    def posted_if_current(self, out: dict[str, Any]) -> dict[str, Any]:
        """手順 (9): `moved` で確かめ、材料が (1) のままなら結果 `out` を PR のコメントへ書く。

        LLM の回答を待つ間にも PR は更新されうるので、書く直前にも確かめる。動いていた・読めなければ結果を捨て、コメントを書かずに
        止める（更新前の材料への判定を今の変更への判定と読ませない。#3959）。
        """
        stop = self.moved()
        return stop if stop is not None else _recorded(self.hub, self.number, out, self.where)


def _base_snapshot(j: _Judging, remote: str, inputs: Mapping[str, Any]) -> tuple[mvv.Snapshot, MvvFacts, str]:
    """手順 (2): PR のマージ先をリモート `remote` から取得し、ベースの MVV を 1 度だけ読んで事実を作る（MVV・事実・MVV の本文）。"""
    Git(j.root).fetch_branch_or_raise(remote, j.pull.base_ref)
    snap = mvv.current(j.root, md.signals_ref(j.pull.base_ref, remote), allow_mismatch=True)
    facts, project = snapshot_facts(j.root, snap, inputs)
    return snap, facts, project


def _diff(j: _Judging, remote: str, snap: mvv.Snapshot) -> tuple[Optional[list[dict[str, Any]]], Optional[dict[str, Any]]]:
    """手順 (4): `snap.commit` と head の組の差分。読めなければ（None, 結果）。PR が動いたかは書く直前に確かめる（手順 (9)）。"""
    try:
        return mvv.compare_files(j.hub, Git(j.root), remote, snap.commit, j.pull.head_sha), None
    except (GitHubError, GitError, mm.DiffError) as exc:
        return None, j.stopped("diff_unreadable", ["変えたファイルを読めない: " + j.text("diff_unreadable", exc)])


class _Records(NamedTuple):
    """手順 (5) で読んだ記録（読めなければ `rows` が None）と、保留（読まない区分では None）。"""

    rows: Optional[list[dict[str, Any]]]
    broken: list[jsonl.Broken]
    holds: Optional[list[str]]


def _read_records(j: _Judging, snap: mvv.Snapshot, warnings: list[dict[str, Any]]) -> _Records:
    """手順 (5): 記録を 1 回だけ読み、保留を求める（同じ行を判定の再利用にも渡す）。

    読めなければ判定を止めず、保留を読めない理由の 1 件にして、判定は再利用を省いて LLM で打つ。"""
    prs = review_prs(j.gate, j.number)
    try:
        rows, broken = mvv.validation_rows(j.root)
        repo = mvv.repo_key(j.root) if prs else ""
    except (OSError, UnicodeDecodeError, internal.InternalError, mvv.MvvError, identity.IdentityError) as exc:
        return _Records(None, [], [RECORDS_UNREADABLE + j.text("records_unreadable", exc)] if prs else None)
    warnings += jsonl.broken_items(mvv.record_path("mvv-validation"), broken)
    if not prs:
        return _Records(rows, broken, None)
    found = md.review_holds(mvv.review_rows(rows), len(broken), repo, prs, (snap.version, snap.sha256), hidden=publish.WITHHELD)
    return _Records(rows, broken, found)


def _judged(
    where: tuple[Path, str], gate_name: str, inputs: Mapping[str, Any], open_hub: OpenHub, ask: Ask, warnings: list[dict[str, Any]]
) -> dict[str, Any]:
    # 手順 (1): 判定する PR と head を決める。ここまでの失敗は `gate` が受ける（書く先の PR が決まっていない）。
    # `where` は判定するリポジトリとリモート名。リモート名は `gate` が 1 度だけ解き、GitHub の接続先とベースの取得の両方に使う
    root, remote = where
    hub = open_hub(root, remote)
    number = pr_number(root, gate_name, inputs, hub)
    j = _Judging(root, gate_name, hub, number, hub.pull(number))
    try:
        snap, facts, project = _base_snapshot(j, remote, inputs)
    except (GitError, mvv.MvvError, jsonfile.JsonFileError, identity.IdentityError, OSError) as exc:
        return j.stopped("base_unreadable", ["ベースの MVV を読めない: " + j.text("base_unreadable", exc)])
    # 手順 (3): 検査 1 だけが判定を止める。承認済みの MVV が無いリポジトリの出力と PR を今と同じにする（コメントを書かない）
    unjudgeable = judgeable(facts)
    if unjudgeable is not None:
        return j.stopped("mvv_unapproved", [unjudgeable], noted=False)
    rows, stop = _diff(j, remote, snap)
    if rows is None:
        assert stop is not None
        return stop
    facts = mvv.pr_facts(hub, number, facts, rows, j.pull)
    rec = _read_records(j, snap, warnings)
    facts = replace(facts, review_holds=tuple(rec.holds) if rec.holds is not None else ())
    # 手順 (6): 差分の全体が材料に載らない PR は、省いた部分を判定できないため LLM を呼ばずに人へ問う
    cut = mm.incomplete_patches(rows)
    if cut:
        stop = j.stopped("incomplete_patches", [*refusals(facts, gate_name), *cut], noted=False, holds=rec.holds)
        return j.posted_if_current(stop)
    shared = mvv.principles().decode("utf-8")
    materials = {
        "NDF の共通原則": shared,
        "プロジェクト MVV": project,
        f"Pull Request #{number} の本文": facts.body if facts.body is not None else "",
        "変えたファイルと差分": mm.patch_material(rows),
    }
    asked = {"gate": gate_name, "question": mm.SYSTEM, "material": mm.prompt(mm.redlines(shared, project), materials)}
    # 手順 (7): 機械の検査 2〜6 が外れても判定は打つ（省かない理由として why に載る）。LLM の打ち直しを起動する直前に PR を
    # 照らし、動いていれば打ち直さない
    try:
        judge = judge_with(ask, before_retry=j.still_current)
        row, unrecorded = mvv.mvv_validate_sprint(root, asked, judge, reuse=rec.rows is not None, rows=rec.rows, broken=rec.broken)
    except _Moved as stop:
        return stop.outcome
    except GateError as exc:
        why = ["MVV 判定を打てない: " + j.text("llm_no_answer", exc), *refusals(facts, gate_name)]
        return j.posted_if_current(j.stopped("llm_no_answer", why, noted=False, holds=rec.holds))
    why = [] if unrecorded is None else ["判定を記録（mvv-validation.jsonl）に書けない: " + j.text("records_unwritable", unrecorded)]
    verdict = _verdict(row)
    skip, why = _skip_decision(facts, verdict, gate_name, inputs.get("pace"), recorded=unrecorded is None, why=why)
    outcome = _bound(j, rows, mm.gate_outcome(gate_name, verdict, skip, why, rec.holds, pull=(number, j.pull.head_sha)))
    return j.posted_if_current(outcome)


def _skip_decision(
    facts: MvvFacts, verdict: Any, gate_name: str, pace: Any, *, recorded: bool, why: Sequence[str]
) -> tuple[bool, list[str]]:
    """手順 (8): 承認ゲートを省くかと、省かない理由（`why` の後ろへ足したもの）を返す。副作用は無い。

    進め方 `pace` と区分 `gate_name` は省くかだけを決める。記録に行の無い判定（`recorded` が偽）では省かない。
    """
    ok, reasons = may_skip(facts, verdict, gate_name)
    skip = ok and recorded
    out = [*why, *reasons]
    if pace not in MVV_PACES:
        skip, out = False, [f"進め方 {pace} は承認ゲートを MVV 判定で省かない", *out]
    if gate_name not in MVV_SKIP_GATES:
        skip, out = False, [*out, RELEASE_KEPT]
    return skip, out


def _design_paths(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """差分の設計文書のうち、head に本文があるもの（削除したものは読まない）。"""
    return [str(r["filename"]) for r in rows if issue_docs.DESIGN_FILE.match(str(r["filename"])) and r.get("status") != "removed"]


def _bound(j: _Judging, rows: Sequence[Mapping[str, Any]], outcome: dict[str, Any]) -> dict[str, Any]:
    """承認ゲートを省く判定にだけ、承認の対象 `target` を持たせ、差分の設計文書の「人へ残す判断」の表を確かめる。

    最初の要求でマージ先の設定と、残りのスロットに収まる設計文書の head の本文を読み、残った文書は上限の件数ごとに分けて読む
    （#4409・#2912 の決定 7）。最初の要求が失敗すれば、設定とすべての文書を読めなかったとして省かない。後続の要求だけが
    失敗したときは、その要求に載せた文書だけを読めなかったとし、要求ごとに 1 件の診断の記録を指す。表に番号を持つ行か、
    本文の無い・切られた・読めなかった設計文書があれば、`skip` を偽にして `target` を外し、`why` にパスごとの理由を足す。
    前に人が承認した行かは問わない。省けない判定では何も読まない。
    """
    if not outcome["skip"]:
        return outcome
    paths = _design_paths(rows)
    try:
        target, found, unread = _target(j.hub, j.pull, paths)
    except (GitHubError, GitError) as exc:
        # 設定と本文は同じ要求なので、失敗の診断の記録は 1 件だけ書き、どの理由からも同じ識別子を指す
        ref = diagnosis("base_unreadable", exc, j.where)
        why = [*outcome["why"], f"マージ先の設定を読めない: {mm.STOP_REASONS['base_unreadable']}（{ref}）"]
        why += [_table_unreadable(p, ref) for p in paths]
        return {**outcome, "skip": False, "why": why}
    kept = _kept_reasons(found, _unread_refs(j, unread))
    if kept:
        return {**outcome, "skip": False, "why": [*outcome["why"], *kept]}
    return {**outcome, "target": target}


def _table_unreadable(path: str, ref: str) -> str:
    """設計文書 `path` の本文を読めなかったときの省かない理由。`ref` は診断の記録を指す括弧の中（`diagnosis`）。"""
    return f"{TABLE_UNREADABLE}{path}（{mm.STOP_REASONS['table_unreadable']}。{ref}）"


def _unread_refs(j: _Judging, unread: Sequence[Unread]) -> dict[str, str]:
    """失敗した後続の要求ごとに診断の記録を要求の順に 1 件だけ書き、その要求に載せた文書のパスから同じ識別子を引けるようにする。"""
    refs: dict[str, str] = {}
    for u in unread:
        ref = diagnosis("table_unreadable", u.error, j.where)
        for p in u.names:
            refs[p] = ref
    return refs


def _kept_reasons(found: Mapping[str, Optional[tuple[str, ...]]], refs: Mapping[str, str]) -> list[str]:
    """設計文書ごとの省かない理由を `found` の順に返す（読めなかった・本文が無い・表に番号を持つ行がある）。"""
    kept: list[str] = []
    for p, numbers in found.items():
        if p in refs:
            kept.append(_table_unreadable(p, refs[p]))
        elif numbers is None:
            kept.append(f"{TABLE_UNREADABLE}{p}（本文が無い）")
        elif numbers:
            kept.append(f"{TABLE_KEPT}{p}（{'・'.join(numbers)}）")
    return kept


def _table_numbers(got: Mapping[str, Any], paths: Sequence[str]) -> dict[str, Optional[tuple[str, ...]]]:
    """応答 `got` のパスごとの本文から、「人へ残す判断」の表の番号を出てくる順に重複なく返す。

    本文が無い（Blob でない・応答に無い）パスと、切り詰められた（`isTruncated`）パスは None。
    切られた本文では後ろにある表の行を見落とし、省く側へ倒れるので読まない。
    """
    out: dict[str, Optional[tuple[str, ...]]] = {}
    for p in paths:  # noqa: NDF201 上限: PR の差分の設計文書の数（数件）。応答の dict を読む純粋な関数で外部へは出ない
        text = github.whole_text(got.get(p))
        out[p] = None if text is None else design_check_rules.redline_numbers(text)
    return out


def _target(hub: GitHub, pull: Pull, paths: Sequence[str]) -> tuple[dict[str, Any], dict[str, Optional[tuple[str, ...]]], list[Unread]]:
    """判定した PR の承認の対象（出力 `mvv_gate.target`）、設計文書 `paths` の表の番号（`_table_numbers`）、読めなかった後続の要求。

    マージ先の設定と head の本文を `GitHub.branch_file_with` で読む（承認ゲートを省く判定でだけ打つ。設計の費用表の増分に記録する）。
    マージ先の設定を読めなければ本番へ反映され得るとして扱う。最初の要求の失敗は `GitHubError` を送出する。
    """
    selections = {p: github.whole_blob_at(pull.head_sha, p) for p in paths}  # noqa: NDF201 上限: PR の差分の設計文書の数（数件）。選択の文字列を組む純粋な関数で外部へは出ない
    found, got, unread = hub.branch_file_with(pull.base_ref, production_merge.PROJECT_FILE, selections)
    verdict = production_merge.judge_project_text(found.text, pull.base_ref, source_repo=found.repo, repo=hub.repo)
    production = production_merge.reaches_production(verdict)
    target = {"repo": hub.repo, "pr": pull.number, "base": pull.base_ref, "head": pull.head_sha, "production": production}
    return target, _table_numbers(got, paths), unread


def post_note(hub: GitHub, number: int, text: str) -> publish.Published:
    """判定の記録を PR のコメントへ書く（`publish.Publisher` を通す）。"""
    return publish.Publisher(hub).comment(number, text)


def _recorded(hub: GitHub, number: int, outcome: dict[str, Any], where: Mapping[str, Any]) -> dict[str, Any]:
    """判定の結果（省く・省かないの両方）を PR へ載せ、人が後から確かめられるようにする。

    承認ゲートを省くと決めたときだけ、判定の記録の本文の後ろに担い手 `mvv` のマージの承認の証跡の行を入れて 1 件で書く
    （#4409 の決定 7。投稿は増えない）。レッドラインや省かない判定では証跡の行を入れない。
    書けなければ判定を保ったまま省かない側へ倒し、`noted` を偽にする（原因は `failure_text` の定型文と識別子だけ）。
    """
    text = note(outcome["gate"], outcome["verdict"], outcome["why"], outcome["holds"], outcome.get("head"))
    held = outcome.get("target")
    if outcome["skip"] and isinstance(held, dict):
        t = merge_approval.MergeTarget(held["repo"], held["pr"], held["base"], held["head"])
        row = merge_approval.MergeRow(
            approval_words.APPROVED, merge_approval.MVV_BY, t, bool(held["production"]), f"mvv:{outcome['gate']}:{t.head}"
        )
        text = f"{text}\n{merge_approval.render_merge_row(row)}"
    try:
        made = post_note(hub, number, text)
    except GitHubError as exc:
        why = [*outcome["why"], "判定の記録を PR に書けない: " + failure_text("note_unwritten", exc, where)]
        kept = {k: v for k, v in outcome.items() if k != "target"}
        return {**kept, "skip": False, "why": why, "noted": False}
    return {**outcome, "noted": True, "note_url": made.url}


def has_gate(mode: Any, gate_name: str) -> bool:
    """モード `mode` の定義が承認ゲート `gate_name` のステップを持つか（定義の無いモードは持たない）。"""
    if mode not in definition.modes():
        return False
    return any(st.kind == "gate" and st.gate == gate_name for st in definition.load_definition(str(mode)).steps())


def gate(
    root: Path,
    gate_name: str,
    inputs: Mapping[str, Any],
    hub: OpenHub,
    ask: Ask = llm.ask,
    warnings: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """承認ゲートの直前の MVV 判定を打ち、出力 `mvv_gate` の値を返す（`mm.gate_outcome`）。進め方によらず判定を打つ（#3959）。

    判定のステップはモードを問わず流れ（工程の出力 `mvv_gate` をどのモードでもそろえる）、承認ゲートを持たないモードでは判定しない。
    PR を読む前の失敗だけをここで受け、判定を持たない結果にする（PR を読んだ後の失敗は `_judged` が受ける）。
    MVV 判定の記録に読めない行があれば `warnings` へ足す。
    """
    if not has_gate(inputs.get("mode"), gate_name):
        return mm.stop_outcome(gate_name, "no_gate", [f"モード {inputs.get('mode')} は承認ゲート {gate_name} を持たない"])
    try:
        remote = Git(root).remote()
        if not mvv_declared_locally(root, remote):
            # MVV を導入していないリポジトリは、GitHub にも取得にも出ずに検査 1 と同じ理由で止める（PR を読まないので pr・head は無い）
            return mm.stop_outcome(gate_name, "mvv_unapproved", [NO_DECL])
        return _judged((root, remote), gate_name, inputs, hub, ask, [] if warnings is None else warnings)
    except (GateError, GitError, GitHubError, identity.IdentityError, OSError) as exc:
        why = ["MVV 判定を打てない: " + failure_text("no_pr", exc, {"root": root, "gate": gate_name})]
        return mm.stop_outcome(gate_name, "no_pr", why)
