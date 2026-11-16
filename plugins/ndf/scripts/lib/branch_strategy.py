"""branch_strategy.py: ブランチ戦略の宣言を読む部品（#3672）。

宣言は `.ndf/project.json` のセクション `branch_strategy` の 1 か所にあり、解析の `project-decl.py branch-strategy`
だけが作る・更新する・移す・消す。工程の部品（worktree の用意・スプリント・リリース・マージ）はこの部品だけを読む。

- 旧い 4 か所にキーが残れば止まり、キーの位置と移行のコマンドを理由に出す（I4。`branch_strategy_legacy`）
- セクションが無い・スキーマ（`$defs/BranchStrategy`）に落ちるときは止まる。既定値へ倒さない（I3）
- 組み合わせの規則 R2〜R10 はスキーマの `allOf` が持つ。値の等しさを比べる R1 だけは JSON Schema で書けないので
  `validate` が同じ番号で確かめる

シェルからは `branch_strategy_cli.py get <pointer>`・`start-ref` で読む。使う側は `deps.require("jsonschema")` を先に呼ぶ
（hook の経路でも読むため、このモジュールは `deps.require` を呼ばない）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import branch_strategy_legacy as legacy  # noqa: E402
import strategy_rows as rows  # noqa: E402
from exit_codes import EXIT_PRECONDITION, EXIT_UNREADABLE, EXIT_VIOLATION  # noqa: E402

SECTION = legacy.SECTION
# スプリントの目録（`sprint.json`）とプランの引数に控える、開始時のスナップショットのキー（決定 9）
SNAPSHOT_KEY = "ブランチ戦略"
SCHEMA = Path(__file__).resolve().parents[2] / "skills" / "development-workflow" / "schemas" / "project.schema.json"
DEF = "BranchStrategy"
PR_KINDS = ("sprint", "doc", "release")
_schema_cache: dict = {}


class StrategyError(Exception):
    """宣言を読めない・使えない。`reason` は利用者へ出す文、`exit_code` は CLI の終了コード。"""

    def __init__(self, reason: str, exit_code: int = EXIT_PRECONDITION) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


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

    def sprint_start_ref(self) -> str:
        """スプリントブランチを切る元のブランチの名前（スプリントの起点）。"""
        return self.base if self.section["sprint"]["start"] == "base" else self.production

    def pr_target(self, kind: str, sprint_branch: str | None = None, integrated: bool = False) -> str:
        """PR の種類（`sprint`・`doc`・`release`）ごとの宛先のブランチ名。

        `sprint` はスプリントの PR（ベースブランチ。`trunk` ではベースブランチと本番ブランチが同じ）、`release` は
        版上げの PR（本番ブランチ）。`doc` は設計 PR と確定仕様化の PR で、`sprint.doc_prs` が `base` ならベースブランチ、
        `sprint` ならスプリントブランチ（`sprint_branch` に渡す。渡さなければ ValueError）。

        `integrated` はスプリントの取り込みを終えた後の PR（確定仕様化）。`trunk` ではスプリントブランチが本番へ入った後で、
        そこを宛先にすると本番へ届かない（消えていれば作業場も作れない）ため、スプリントの PR と同じ宛先（本番ブランチ）にする。
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

    @property
    def row(self) -> rows.Row:
        """取り込み方の表の行（行の名前は `release_strategy` の値）。"""
        return rows.ROWS[self.release_strategy]

    @property
    def reviews(self) -> rows.ReviewUnits:
        """レビューの単位（`reviews`。書いていない種類は既定値）。"""
        return rows.reviews_of(self.section.get("reviews"))

    @property
    def dev_workflow(self) -> str | None:
        """開発版のチャネル（`channels` の `dev`）のワークフローのファイル名。開発版が無ければ None。"""
        return next((c["workflow"] for c in self.section["channels"] if c["name"] == "dev"), None)

    def check_target(self) -> str:
        """検査の Pull Request の宛先（スプリントの PR と同じ。`trunk` ではベースブランチと本番ブランチが同じ）。"""
        return self.pr_target("sprint")


def _schema() -> dict:
    if "doc" not in _schema_cache:
        try:
            _schema_cache["doc"] = json.loads(SCHEMA.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise StrategyError(f"{SCHEMA}: スキーマを読めない（{e}）", EXIT_UNREADABLE) from e
        if DEF not in _schema_cache["doc"].get("$defs", {}):
            raise StrategyError(f"{SCHEMA}: $defs/{DEF} が無い", EXIT_UNREADABLE)
    return _schema_cache["doc"]


def _rule_of(error, root: dict) -> str:
    """jsonschema の誤りから、落ちた規則（`allOf` の行の title）を引く。行の外なら `shape`。"""
    path = list(error.absolute_schema_path)
    # 根の検査は {"$ref": ...} 越しなので、`$defs/BranchStrategy` の中の `allOf` の位置を探す
    for i, tok in enumerate(path):
        if tok == "allOf" and i + 1 < len(path):
            row = root["$defs"][DEF]["allOf"][path[i + 1]]
            return row.get("title", "shape")
    return "shape"


def escape_token(key) -> str:
    """JSON Pointer の参照 1 つを書き出す（`~` → `~0`・`/` → `~1`）。"""
    return str(key).replace("~", "~0").replace("/", "~1")


def _pointer(parts) -> str:
    return "".join("/" + escape_token(p) for p in parts)


def validate_section(section: Any) -> list[dict]:
    """セクションをスキーマで検査し、落ちた規則とキーの位置を返す。空なら通った。"""
    from jsonschema import Draft202012Validator

    root = _schema()
    validator = Draft202012Validator({"$schema": root.get("$schema"), "$defs": root["$defs"], "$ref": f"#/$defs/{DEF}"})
    failed: list[dict] = []
    seen = set()
    for err in validator.iter_errors(section):
        rule = _rule_of(err, root)
        pointer = _pointer(err.absolute_path)
        if (rule, pointer) not in seen:
            seen.add((rule, pointer))
            failed.append({"rule": rule, "pointer": pointer})
    if isinstance(section, dict):
        branches = section.get("branches") if isinstance(section.get("branches"), dict) else {}
        same = branches.get("base") is not None and branches.get("base") == branches.get("production")
        if ("release_strategy" in section) and ((section["release_strategy"] == "trunk") != same):
            failed.append({"rule": "R1", "pointer": "/branches"})
    if not failed and isinstance(section, dict) and section.get("reviews"):
        failed += [{"rule": "reviews", "pointer": "/reviews", "why": why} for why in rows.review_problems(section["reviews"])]
    return sorted(failed, key=lambda f: (f["rule"], f["pointer"]))


def _read_files(read) -> dict[str, dict]:
    files = {}
    for fid, name in legacy.FILES.items():
        text = read(name)
        if text is None:
            continue
        try:
            files[fid] = json.loads(text)
        except ValueError as e:
            raise StrategyError(f"`.ndf/{name}` を JSON として読めない（{e}）", EXIT_UNREADABLE) from e
    return files


def _check_files(files: dict[str, dict], system: str = "10") -> Strategy:
    found = legacy.find_legacy(files, legacy.load_moves())
    if found:
        raise StrategyError(legacy.legacy_reason(found, system))
    project = files.get("project")
    if not isinstance(project, dict) or SECTION not in project:
        raise StrategyError(
            f"`.ndf/project.json` にセクション `{SECTION}` がありません。`{legacy.COMMAND[system]} init --kind <種類>`"
            f"（既存のリポジトリは `{legacy.COMMAND[system]} measure`）で作ってください"
        )
    return _validated(project[SECTION], f"`.ndf/project.json` の `{SECTION}` ")


def _validated(section: Any, where_label: str) -> Strategy:
    """`section` を検査して型にする。落ちれば「<where_label>がスキーマに落ちる: 規則 `位置`、…」で止める（EXIT_VIOLATION）。"""
    failed = validate_section(section)
    if failed:
        where = "、".join(f"{f['rule']} `{f['pointer'] or '/'}`" for f in failed)
        raise StrategyError(f"{where_label}がスキーマに落ちる: {where}", EXIT_VIOLATION)
    return Strategy(section)


def load_strategy(root: Path) -> Strategy:
    """メインディレクトリのセクションを読み、旧いキーとスキーマで検査して型にする（I1・I3・I4）。"""
    ndf = Path(root) / ".ndf"

    def read(name: str):
        p = ndf / name
        return p.read_text(encoding="utf-8") if p.is_file() else None

    return _check_files(_read_files(read))


def load_at_ref(root: Path, ref: str) -> Strategy:
    """指定したブランチのセクションを `git show <ref>:.ndf/<file>` で読み、`load` と同じ検査をする。"""
    ok = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], capture_output=True, text=True)
    if ok.returncode != 0:
        raise StrategyError(f"ref `{ref}` がありません。取得してから打ち直してください")

    def read(name: str):
        r = subprocess.run(["git", "-C", str(root), "show", f"{ref}:.ndf/{name}"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    return _check_files(_read_files(read))


def get_value(strategy: Strategy, pointer: str) -> Any:
    """セクションの中の 1 つの値を JSON Pointer で引く。無ければ StrategyError。"""
    import json_pick

    value, found = json_pick.resolve_pointer(strategy.section, pointer)
    if not found:
        raise StrategyError(f"`{pointer}` はセクションにありません", EXIT_UNREADABLE)
    return value


def canonical_json(value: Any) -> str:
    """正規化した JSON（キーの昇順・区切りの空白なし）。ハッシュの入力。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def strategy_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def snapshot_section(strategy: Strategy, source: str) -> dict:
    """スプリントの開始時に控えるセクションの全体（決定 9）。"""
    section = json.loads(json.dumps(strategy.section))
    return {"source": source, "digest": strategy_digest(section), "section": section}


def from_snapshot(snap: Any) -> Strategy:
    """スプリントの開始時のスナップショット（`snapshot_section` の形）を読む（I13）。

    スナップショットはセクションの全体を持つので、今の宣言を読み直さない。形が違う・スキーマに落ちるときは止まる（I3）。"""
    section = snap.get("section") if isinstance(snap, dict) else None
    if not isinstance(section, dict):
        raise StrategyError(f"スプリントの開始時のスナップショット（`{SNAPSHOT_KEY}`）が無いか、形が違う。スプリントを始め直してください")
    return _validated(section, "スプリントの開始時のスナップショット")


def _section_leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_section_leaves(v, f"{prefix}/{escape_token(k)}"))
        return out
    return {prefix: value}


def snapshot_diff(snap: dict, now: Strategy) -> list[str]:
    """スナップショットと今のセクションの違うキーの位置を昇順で並べる（I13）。"""
    a, b = _section_leaves(snap["section"]), _section_leaves(now.section)
    return sorted(k for k in set(a) | set(b) if canonical_json(a.get(k, "\0missing")) != canonical_json(b.get(k, "\0missing")))
