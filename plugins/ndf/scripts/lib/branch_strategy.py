"""branch_strategy.py: ブランチ戦略の宣言を読む部品（#3672）。

宣言は `.ndf/project.json` のセクション `branch_strategy` の 1 か所にあり、解析の `project-decl.py branch-strategy`
だけが作る・更新する・移す・消す。工程の部品（worktree の用意・スプリント・リリース・マージ）はこの部品だけを読む。

- 旧い 4 か所にキーが残れば止まり、キーの位置と移行のコマンドを理由に出す（I4。`branch_strategy_legacy`）
- セクションが無い・スキーマ（`$defs/BranchStrategy`）に落ちるときは止まる。既定値へ倒さない（I3）
- 組み合わせの規則 R2〜R10 はスキーマの `allOf` が持つ。値の等しさを比べる R1 だけは JSON Schema で書けないので
  `validate` が同じ番号で確かめる

シェルからは `branch_strategy.py get <pointer>`・`start-ref` で読む。使う側は `deps.require("jsonschema")` を先に呼ぶ
（`main` は自分で呼ぶ）。
"""

from __future__ import annotations

import argparse
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
from exit_codes import EXIT_PRECONDITION, EXIT_UNREADABLE, EXIT_VIOLATION  # noqa: E402

SECTION = legacy.SECTION
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

    def pr_target(self, kind: str, sprint_branch: str | None = None) -> str:
        """PR の種類（`sprint`・`doc`・`release`）ごとの宛先のブランチ名。

        `sprint` はスプリントの PR（ベースブランチ。`trunk` ではベースブランチと本番ブランチが同じ）、`release` は
        版上げの PR（本番ブランチ）。`doc` は設計 PR と確定仕様化の PR で、`sprint.doc_prs` が `base` ならベースブランチ、
        `sprint` ならスプリントブランチ（`sprint_branch` に渡す。渡さなければ ValueError）。
        """
        if kind not in PR_KINDS:
            raise ValueError(f"PR の種類は {'・'.join(PR_KINDS)} のどれか: {kind!r}")
        if kind == "release":
            return self.production
        if kind == "doc" and self.section["sprint"]["doc_prs"] == "sprint":
            if not sprint_branch:
                raise ValueError("doc_prs が sprint の宣言では、設計 PR の宛先にスプリントブランチを渡す")
            return sprint_branch
        return self.base


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


def _pointer(parts) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


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
    failed = validate_section(project[SECTION])
    if failed:
        where = "、".join(f"{f['rule']} `{f['pointer'] or '/'}`" for f in failed)
        raise StrategyError(f"`.ndf/project.json` の `{SECTION}` がスキーマに落ちる: {where}", EXIT_VIOLATION)
    return Strategy(project[SECTION])


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


def _section_leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_section_leaves(v, f"{prefix}/{str(k).replace('~', '~0').replace('/', '~1')}"))
        return out
    return {prefix: value}


def snapshot_diff(snap: dict, now: Strategy) -> list[str]:
    """スナップショットと今のセクションの違うキーの位置を昇順で並べる（I13）。"""
    a, b = _section_leaves(snap["section"]), _section_leaves(now.section)
    return sorted(k for k in set(a) | set(b) if canonical_json(a.get(k, "\0missing")) != canonical_json(b.get(k, "\0missing")))


def _print_value(v: Any) -> None:
    sys.stdout.write((v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) + "\n")


def main(argv: list[str] | None = None) -> int:
    import deps

    deps.require("jsonschema")
    ap = argparse.ArgumentParser(prog="branch_strategy.py", description="ブランチ戦略の宣言の値を 1 つ読む")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get", help="セクションの値を JSON Pointer で 1 つ読む")
    g.add_argument("pointer")
    g.add_argument("--ref", help="このブランチ（origin/<名前>）のセクションを読む")
    g.add_argument("--root", default=".")
    s = sub.add_parser("start-ref", help="スプリントの起点のブランチ名を出す")
    s.add_argument("--root", default=".")
    t = sub.add_parser("pr-target", help="PR の種類ごとの宛先のブランチ名を出す")
    t.add_argument("kind", choices=PR_KINDS)
    t.add_argument("--sprint-branch", help="doc_prs が sprint の宣言で、設計 PR の宛先にするスプリントブランチ")
    t.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "get":
            if not a.pointer.startswith("/"):
                raise StrategyError(f"pointer は / で始める: {a.pointer!r}", EXIT_UNREADABLE)
            strategy = load_at_ref(Path(a.root), a.ref) if a.ref else load_strategy(Path(a.root))
            _print_value(get_value(strategy, a.pointer))
        elif a.cmd == "start-ref":
            _print_value(load_strategy(Path(a.root)).sprint_start_ref())
        else:
            _print_value(load_strategy(Path(a.root)).pr_target(a.kind, a.sprint_branch))
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return EXIT_UNREADABLE
    except StrategyError as e:
        sys.stderr.write(e.reason + "\n")
        return e.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
