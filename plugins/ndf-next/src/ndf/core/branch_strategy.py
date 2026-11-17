"""ブランチ戦略の宣言を読む部品（#3672・#3681）。

宣言は `.ndf/project.json` のセクション `branch_strategy` の 1 か所にあり、解析の `ndf project branch-strategy` だけが
作る・更新する・移す・消す。工程の部品（worktree の用意・スプリント・リリース・マージ）はこの部品だけを読む。

- 旧い 4 か所にキーが残れば止まり、キーの位置と移行のコマンドを理由に出す（I4。`branch_strategy_legacy`）
- セクションが無い・スキーマ（`$defs/BranchStrategy`）に落ちるときは止まる。既定値へ倒さない（I3）
- 組み合わせの規則 R2〜R10 はスキーマの `allOf` が持つ。値の等しさを比べる R1 だけは JSON Schema で書けないので
  `validate` が同じ番号で確かめる

スキーマの正本は 11.0 の契約（`contracts/schemas/ndf-settings/project.schema.json`）で、10 系の
`scripts/lib/branch_strategy.py` は同じ責務を 10 系の置き場で持つ。
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator

from ndf import contracts
from ndf.adapters.git import Git, GitError
from ndf.core import branch_strategy_legacy as legacy
from ndf.core import json_pointer

SECTION = legacy.SECTION
DECL_DIR = legacy.DECL_DIR
PROJECT = f"{DECL_DIR}/project.json"
SCHEMA = contracts.schema_path("ndf-settings/project")
DEF = "BranchStrategy"
PR_KINDS = ("sprint", "doc", "release")
# 止まる理由の区分（`contracts/facts/exit-codes.json` の id）
PRECONDITION, UNREADABLE, VIOLATION = "precondition", "unreadable", "failed"
# 早送りの取り込みのコミットの trailer（書く側は release の land、読む側は project の測定。10 系の `release_lib/land_read.py` と同じ名前）
TRAILER_PR, TRAILER_HEAD, TRAILER_TIP = "NDF-Landed-PR", "NDF-Approved-Head", "NDF-Tip"


class StrategyError(contracts.ContractError):
    """宣言を読めない・使えない。`reason` は利用者へ出す文、`exit_id` は終了コードの区分の id。"""

    def __init__(self, reason: str, exit_id: str = PRECONDITION) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_id = exit_id


@dataclass(frozen=True)
class Strategy:
    """スキーマを通ったセクション（不変の値として扱う。`section` を書き換えない）。"""

    section: dict

    @property
    def release_strategy(self) -> str:
        return self.section["release_strategy"]

    @property
    def production(self) -> str:
        return self.section["branches"]["production"]

    @property
    def base(self) -> str:
        return self.section["branches"]["base"]

    @property
    def merge_actor(self) -> str:
        return self.section["merge"]["actor"]

    @property
    def merge_method(self) -> str:
        return self.section["merge"]["method"]

    @property
    def merge_commit(self) -> str:
        return self.section["merge"]["commit"]

    @property
    def merge_admin(self) -> Any:
        """`merge.admin` の値（真偽の判定は呼び手が `is True` で行う）。"""
        return self.section["merge"]["admin"]

    @property
    def approval_via(self) -> str:
        return self.section["approval"]["via"]

    @property
    def version_store(self) -> dict:
        """版数の置き場（`release.version_store`。書き換えない）。"""
        return self.section["release"]["version_store"]

    @property
    def release_plugin(self) -> Optional[str]:
        """版数を持つプラグインの名前（`release.plugin`。無ければ None）。"""
        return self.section["release"].get("plugin")

    def sprint_start_ref(self) -> str:
        """スプリントブランチを切る元のブランチの名前（スプリントの起点）。"""
        return self.base if self.section["sprint"]["start"] == "base" else self.production

    def pr_target(self, kind: str, sprint_branch: Optional[str] = None, integrated: bool = False) -> str:
        """PR の種類（`sprint`・`doc`・`release`）ごとの宛先のブランチ名。

        `sprint` はスプリントの PR（ベースブランチ。`trunk` ではベースブランチと本番ブランチが同じ）、`release` は
        版上げの PR（本番ブランチ）。`doc` は設計 PR と確定仕様化の PR で、`sprint.doc_prs` が `base` ならベースブランチ、
        `sprint` ならスプリントブランチ（`sprint_branch` に渡す。渡さなければ ValueError）。
        `integrated` はスプリントの取り込みを終えた後の PR（確定仕様化）。`trunk` ではスプリントブランチが本番へ入った後
        なので、スプリントの PR と同じ宛先にする。
        """
        if kind not in PR_KINDS:
            raise ValueError(f"PR の種類は {'・'.join(PR_KINDS)} のどれか: {kind!r}")
        if kind == "release":
            return self.production
        if integrated and self.release_strategy == "trunk":
            return self.base
        if kind == "doc" and self.section["sprint"]["doc_prs"] == "sprint":
            if not sprint_branch:
                raise ValueError("doc_prs が sprint の宣言では、設計 PR の宛先にスプリントブランチを渡す")
            return sprint_branch
        return self.base


@contracts.cached_reader
def _schema() -> tuple[dict, Draft202012Validator]:
    """project のスキーマの全体と、`$defs/BranchStrategy` の検証器（1 プロセスに 1 回だけ読む）。"""
    try:
        root = contracts.load_json(SCHEMA)
    except contracts.ContractError as exc:
        raise StrategyError(str(exc), UNREADABLE) from None
    if not isinstance(root.get("$defs"), dict) or DEF not in root["$defs"]:
        raise StrategyError(f"{SCHEMA}: $defs/{DEF} が無い", UNREADABLE)
    return root, Draft202012Validator({"$schema": root.get("$schema"), "$defs": root["$defs"], "$ref": f"#/$defs/{DEF}"})


def _rule_of(schema_path: list, root: dict) -> str:
    """jsonschema の誤りの位置から、落ちた規則（`allOf` の行の title）を引く。行の外なら `shape`。"""
    for i, tok in enumerate(schema_path):
        if tok == "allOf" and i + 1 < len(schema_path):
            row = root["$defs"][DEF]["allOf"][schema_path[i + 1]]
            return row["title"] if "title" in row else "shape"
    return "shape"


def _r1_failed(section: Any) -> bool:
    """R1: `trunk` のときだけベースブランチと本番ブランチが同じ。"""
    if not isinstance(section, dict) or "release_strategy" not in section:
        return False
    branches = section["branches"] if isinstance(section.get("branches"), dict) else {}
    same = branches.get("base") is not None and branches.get("base") == branches.get("production")
    return (section["release_strategy"] == "trunk") != same


def validate(section: Any) -> list[dict]:
    """セクションをスキーマで検査し、落ちた規則とキーの位置（`{"rule", "pointer"}`）を返す。空なら通った。"""
    root, validator = _schema()
    failed: list[dict] = []
    seen = set()
    for err in validator.iter_errors(section):
        key = (_rule_of(list(err.absolute_schema_path), root), json_pointer.join(err.absolute_path))
        if key not in seen:
            seen.add(key)
            failed.append({"rule": key[0], "pointer": key[1]})
    if _r1_failed(section):
        failed.append({"rule": "R1", "pointer": "/branches"})
    return sorted(failed, key=lambda f: (f["rule"], f["pointer"]))


def _read_files(read: Callable[[str], Optional[str]]) -> dict[str, dict]:
    files = {}
    for fid, name in legacy.FILES.items():
        text = read(name)
        if text is None:
            continue
        try:
            files[fid] = json.loads(text)
        except ValueError as exc:
            raise StrategyError(f"`{DECL_DIR}/{name}` を JSON として読めない（{exc}）", UNREADABLE) from None
    return files


def _check_files(files: dict[str, dict]) -> Strategy:
    found = legacy.find_legacy(files, legacy.load_moves())
    if found:
        raise StrategyError(legacy.legacy_reason(found, "11"))
    project = files.get("project")
    if not isinstance(project, dict) or SECTION not in project:
        command = legacy.COMMAND["11"]
        raise StrategyError(
            f"`{PROJECT}` にセクション `{SECTION}` がありません。`{command} init --kind <種類>`"
            f"（既存のリポジトリは `{command} measure`）で作ってください"
        )
    failed = validate(project[SECTION])
    if failed:
        where = "、".join(f"{f['rule']} `{f['pointer'] or '/'}`" for f in failed)
        raise StrategyError(f"`{PROJECT}` の `{SECTION}` がスキーマに落ちる: {where}", VIOLATION)
    return Strategy(project[SECTION])


def load(root: Path) -> Strategy:
    """メインディレクトリのセクションを読み、旧いキーとスキーマで検査して型にする（I1・I3・I4）。"""
    ndf = Path(root) / DECL_DIR

    def read(name: str) -> Optional[str]:
        path = ndf / name
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else None
        except (OSError, UnicodeDecodeError) as exc:
            raise StrategyError(f"`{DECL_DIR}/{name}` を読めない（{exc}）", UNREADABLE) from None

    return _check_files(_read_files(read))


def load_at_ref(root: Path, ref: str, git: Optional[Git] = None) -> Strategy:
    """指定したブランチのセクションを `git show <ref>:.ndf/<file>` で読み、`load` と同じ検査をする。"""
    g = git or Git(cwd=Path(root))
    try:
        if g.attempt("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
            raise StrategyError(f"ref `{ref}` がありません。取得してから打ち直してください")

        def read(name: str) -> Optional[str]:
            done = g.attempt("show", f"{ref}:{DECL_DIR}/{name}")
            return done.stdout if done.returncode == 0 else None

        return _check_files(_read_files(read))
    except GitError as exc:
        raise StrategyError(f"ref `{ref}` の宣言を読めない（{exc}）", UNREADABLE) from None


def get(strategy: Strategy, pointer: str) -> Any:
    """セクションの中の 1 つの値を JSON Pointer で引く。無ければ StrategyError。"""
    if pointer and not pointer.startswith("/"):
        raise StrategyError(f"`{pointer}` は JSON Pointer ではありません（`/` で始める）", UNREADABLE)
    found, value = json_pointer.resolve(strategy.section, pointer)
    if not found:
        raise StrategyError(f"`{pointer}` はセクションにありません", UNREADABLE)
    return value


def canonical_json(value: Any) -> str:
    """正規化した JSON（キーの昇順・区切りの空白なし）。ハッシュの入力。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(value: Any) -> str:
    """正規化した JSON の SHA-256（計画のハッシュとスナップショットのハッシュ）。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def snapshot(strategy: Strategy, source: str) -> dict:
    """スプリントの開始時に控えるセクションの全体（決定 9）。`digest` は正規化した JSON の SHA-256。"""
    section = copy.deepcopy(strategy.section)
    return {"source": source, "digest": digest_of(section), "section": section}


def _leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_leaves(v, f"{prefix}/{json_pointer.escape(k)}"))
        return out
    return {prefix: value}


def snapshot_diff(snap: dict, now: Strategy) -> list[str]:
    """スナップショットと今のセクションの違うキーの位置を昇順で並べる（I13）。同じなら空。"""
    a, b = _leaves(snap["section"]), _leaves(now.section)

    def differs(key: str) -> bool:
        return key not in a or key not in b or canonical_json(a[key]) != canonical_json(b[key])

    return sorted(k for k in set(a) | set(b) if differs(k))
