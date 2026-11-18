#!/usr/bin/env python3
"""check-plugin-refs.py: 外からの参照（`plugins/ndf/` の外から `plugins/ndf/` の中を名指すパス）を確かめ、書き換えの表を当てる。

    python3 scripts/check-plugin-refs.py check --tree current [--root DIR]
    python3 scripts/check-plugin-refs.py check --tree swapped [--strict] [--root DIR] [--table FILE]
    python3 scripts/check-plugin-refs.py apply [--root DIR] [--table FILE]

入れ替え（#3304。`plugins/ndf-next/` を `plugins/ndf/` へ置き換える）で消えるパスを、workflow・宣言・指示書・根の検査が
指したまま残らないようにする（#3341 の決定 1。設計は issues/issue-3341-design-detail.md の R0）。

- `check --tree current`: 参照先が今の `plugins/ndf/` に実在するか。表を読まない
- `check --tree swapped`: `plugins/ndf-next/` を `plugins/ndf/` と読み替え、書き換えの表（scripts/plugin-refs-swap.json）を
  当てた参照先が実在するか。表に無く入れ替えの後に実在しない参照・行き先の無い「向け直す」の行・行き先ができた
  「未作成」の行・参照を持たない行で落ちる。行き先の無い「未作成」の行は既知の失敗として数えるだけで、`--strict` のときだけ落ちる
- `apply`: 「向け直す」の行の文字列を置き換え、「ファイルを消す」の行のファイルを消し、「消す」の行を手で直す一覧として返す。
  「未作成」の行が残る・表とファイルが合わないときは 1 つも書かない（入れ替えの PR が 1 回だけ打つ）

結果は標準出力へ 1 行の JSON で出す。終了コード: 0 = 指摘なし / 1 = 指摘あり / 2 = 表か git かエントリポイントを読めない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = Path("scripts/plugin-refs-swap.json")
TOOL = "check-plugin-refs"
PLUGIN = "plugins/ndf"
NEXT = "plugins/ndf-next"
# 実行する参照を探す範囲（issues/issue-3341-design-refs.md の「数え方」のコマンドはこの並びを写したもの）
SCAN_PATHSPEC = (
    ".github",
    ".ndf",
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    "KIRO.md",
    "CONTRIBUTING.md",
    "SUPPORT.md",
    "pyproject.toml",
    "conftest.py",
    "plugins/mcp",
    "tests",
    "scripts/check-*",
    "scripts/lib",
    "scripts/contracts_lib",
    "scripts/structure_checks",
    "scripts/token-usage.py",
    "scripts/validate-runtime-plugins.sh",
    ":!.ndf/project.json",
)
PATH_CHARS = "A-Za-z0-9_./*{},-"
REF_PATTERN = re.compile(rf"{PLUGIN}/[{PATH_CHARS}]*")
# 参照の終わり: 続く文字がパスの文字でない（末尾の `.` と `,` は、続く文字がパスの文字でなければ文の句読点として読む）
_REF_END = re.compile(r"(?![A-Za-z0-9_/*{}-])(?![.,][A-Za-z0-9_/*{}-])")
SELF_FILES = frozenset({"scripts/check-plugin-refs.py", "scripts/tests/test_check_plugin_refs.py"})
TABLE_VERDICTS = ("redirect", "unbuilt", "drop", "delete_file")
GLOB_CHARS = "*?[{"
# 行き先の副命令の有無を確かめる `bin/ndf <語> --help` の上限（初回は依存の環境を作るため長めにとる）
PROBE_TIMEOUT = 600
USAGE_EXIT = 2


class Stop(Exception):
    """表・git・エントリポイントを読めない（終了コード 2）。"""


@dataclass(frozen=True)
class Ref:
    file: str
    path: str


@dataclass(frozen=True)
class Row:
    id: str
    file: str
    verdict: str
    frm: str
    to: str | None = None
    target: dict | None = None
    feature: str | None = None

    def covers(self, ref: Ref) -> bool:
        """参照の組がこの行に当たるか（同じファイルで、`from` の中の参照に組のパスがある。行番号では当てない）。"""
        return ref.file == self.file and ref.path in refs_in(self.frm)


@dataclass(frozen=True)
class Finding:
    rule: str
    file: str
    ref: str
    row: str | None = None

    def as_item(self) -> dict:
        return {"rule": self.rule, "file": self.file, "ref": self.ref, "row": self.row}


@dataclass
class Outcome:
    findings: list[Finding] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def refs_in(text: str) -> list[str]:
    """文字列の中の `plugins/ndf/` で始まるパス（末尾の `.` と `,` を除く）。"""
    return [m.group(0).rstrip(".,") for m in REF_PATTERN.finditer(text)]


def occurrences(text: str, frm: str) -> list[int]:
    """`frm` が参照の途中で切れずに現れる位置（`plugins/ndf/scripts/` は `plugins/ndf/scripts/lib/x` に当たらない）。"""
    pat = re.compile(re.escape(frm) + _REF_END.pattern)
    return [m.start() for m in pat.finditer(text)]


def _git_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", *SCAN_PATHSPEC],
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", b"") or b""
        raise Stop(f"git ls-files を打てない: {detail.decode(errors='replace').strip() or e}") from e
    return sorted(p for p in out.decode().split("\0") if p and p not in SELF_FILES)


def collect_refs(root: Path) -> tuple[list[Ref], list[str]]:
    """追跡されたファイルから参照の組を集める。戻り値は（ファイル・パスの順に並べた組, 読めなかったファイル）。"""
    refs: set[Ref] = set()
    warnings: list[str] = []
    for rel in _git_files(root):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            warnings.append(rel)
            continue
        refs.update(Ref(rel, p) for p in refs_in(text))
    return sorted(refs, key=lambda r: (r.file, r.path)), warnings


def _row_problem(r: object) -> str | None:
    if not isinstance(r, dict):
        return "行が object でない"
    for key in ("id", "file", "verdict", "from"):
        if not isinstance(r.get(key), str) or not r[key]:
            return f"{key} が無い"
    v = r["verdict"]
    if v not in TABLE_VERDICTS:
        return f"判定 {v!r} は {'・'.join(TABLE_VERDICTS)} のどれでもない"
    if not refs_in(r["from"]):
        return "from が plugins/ndf/ のパスを含まない"
    if v == "redirect" and not isinstance(r.get("to"), str):
        return "redirect に to が無い"
    if v in ("redirect", "unbuilt"):
        t = r.get("target")
        ok = isinstance(t, dict) and (
            isinstance(t.get("path"), str)
            or (isinstance(t.get("command"), list) and t["command"] and all(isinstance(w, str) for w in t["command"]))
        )
        if not ok:
            return f"{v} に target（path か command）が無い"
    if v == "unbuilt" and not isinstance(r.get("feature"), str):
        return "unbuilt に feature が無い"
    return None


def load_table(path: Path) -> list[Row]:
    """書き換えの表を読み、行の形を確かめて返す。"""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Stop(f"{path}: 表を読めない（{e}）") from e
    if not isinstance(rows, list):
        raise Stop(f"{path}: rows が配列でない")
    out = []
    for i, r in enumerate(rows):
        problem = _row_problem(r)
        if problem:
            raise Stop(f"{path}: rows[{i}]（{r.get('id') if isinstance(r, dict) else '?'}）: {problem}")
        out.append(Row(r["id"], r["file"], r["verdict"], r["from"], r.get("to"), r.get("target"), r.get("feature")))
    return out


def target_exists(base: Path, rel: str) -> bool:
    """`plugins/ndf/` からの相対パスが `base` の下に実在するか。glob と `{}` を含むときは、最初の glob の文字より前の
    最も深いディレクトリの実在で判定する。"""
    cut = min((i for i, c in enumerate(rel) if c in GLOB_CHARS), default=None)
    if cut is None:
        return (base / rel).exists()
    head = rel[:cut]
    return (base / (head.rsplit("/", 1)[0] if "/" in head else "")).is_dir()


class CommandProbe:
    """プラグインのエントリポイントがその副命令を知っているかを `--help` で確かめる（同じ語の並びは 1 度だけ打つ）。"""

    def __init__(self, plugin: Path) -> None:
        self.plugin = plugin
        self.cache: dict[tuple[str, ...], bool] = {}
        self.calls = 0

    def __call__(self, words: tuple[str, ...]) -> bool:
        if words not in self.cache:
            self.cache[words] = self._probe(words)
        return self.cache[words]

    def _probe(self, words: tuple[str, ...]) -> bool:
        self.calls += 1
        with tempfile.TemporaryDirectory(prefix="check-plugin-refs-") as state:
            # 利用者の状態の置き場に触れない
            env = {**os.environ, "NDF_STATE_HOME": state}
            try:
                p = subprocess.run(
                    [str(self.plugin / "bin" / "ndf"), *words, "--help"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=PROBE_TIMEOUT,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                raise Stop(f"{self.plugin}/bin/ndf を起動できない: {e}") from e
        if p.returncode == USAGE_EXIT:
            return False
        if p.returncode != 0:
            last = (p.stderr.strip().splitlines() or [""])[-1]
            raise Stop(f"{self.plugin}/bin/ndf {' '.join(words)} --help が終了コード {p.returncode}: {last}")
        return usage_names(p.stdout, words)


def usage_names(help_text: str, words: tuple[str, ...]) -> bool:
    """使い方の 1 行目が `words` の副命令のものか。argparse は知らない語の後の `--help` でも手前の使い方を出すため、
    終了コードだけでは決めない。"""
    for line in help_text.splitlines():
        if line.startswith("usage:"):
            names = []
            for tok in line[len("usage:") :].split():
                if tok[0] in "[{":
                    break
                names.append(tok)
            return names[-len(words) :] == list(words)
    return False


def target_ready(next_root: Path, target: dict, probe: CommandProbe) -> bool:
    if "command" in target:
        return probe(tuple(target["command"]))
    return target_exists(next_root, target["path"])


def _read(root: Path, rel: str, cache: dict[str, str | None]) -> str | None:
    if rel not in cache:
        try:
            cache[rel] = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            cache[rel] = None
    return cache[rel]


def _check_row(row: Row, text: str | None, next_root: Path, strict: bool, probe: CommandProbe, out: Outcome) -> None:
    ref = refs_in(row.frm)[0]
    if text is None or not occurrences(text, row.frm):
        out.findings.append(Finding("stale_row", row.file, ref, row.id))
        return
    if row.verdict == "redirect":
        if target_ready(next_root, row.target, probe):
            return
        if row.target.get("placed_by") == "swap":
            # 入れ替えの PR が置く行き先（S2 が新しい根へ置く README など）。置き忘れは入れ替えの後の current が落とす
            out.metrics["placed_by_swap"] += 1
            return
        out.findings.append(Finding("target_missing", row.file, ref, row.id))
    elif row.verdict == "unbuilt":
        if target_ready(next_root, row.target, probe):
            out.findings.append(Finding("unbuilt_exists", row.file, ref, row.id))
        elif strict:
            out.findings.append(Finding("unbuilt", row.file, ref, row.id))
        else:
            out.metrics["unbuilt"] += 1


def check_tree(
    root: Path, refs: list[Ref], rows: list[Row], tree: str, strict: bool, probe: CommandProbe | None = None
) -> Outcome:
    """1 つのモードで参照先の実在と表の行の状態を判定する。`current` は表を読まない。"""
    out = Outcome(metrics={"refs": len(refs)})
    if tree == "current":
        base = root / PLUGIN
        out.findings = [Finding("missing", r.file, r.path) for r in refs if not target_exists(base, r.path[len(PLUGIN) + 1 :])]
        return out
    next_root = root / NEXT
    probe = probe or CommandProbe(next_root)
    out.metrics.update(rows=len(rows), unbuilt=0, placed_by_swap=0)
    for r in refs:
        if not any(row.covers(r) for row in rows) and not target_exists(next_root, r.path[len(PLUGIN) + 1 :]):
            out.findings.append(Finding("unlisted", r.file, r.path))
    texts: dict[str, str | None] = {}
    for row in rows:
        _check_row(row, _read(root, row.file, texts), next_root, strict, probe, out)
    out.metrics["probes"] = probe.calls
    return out


def _replace(text: str, frm: str, to: str) -> str:
    for start in reversed(occurrences(text, frm)):
        text = text[:start] + to + text[start + len(frm) :]
    return text


def apply_table(root: Path, rows: list[Row]) -> tuple[list[str], list[dict], list[Finding]]:
    """表を当てる。戻り値は（書き換えたか消したファイル, 手で直す行, 指摘）。指摘が 1 件でもあれば何も変えていない。"""
    texts: dict[str, str | None] = {}
    findings = []
    for row in rows:
        ref = refs_in(row.frm)[0]
        if row.verdict == "unbuilt":
            findings.append(Finding("unbuilt", row.file, ref, row.id))
            continue
        text = _read(root, row.file, texts)
        if text is None or (row.verdict != "delete_file" and not occurrences(text, row.frm)):
            findings.append(Finding("stale_row", row.file, ref, row.id))
    if findings:
        return [], [], findings
    deleted = sorted({row.file for row in rows if row.verdict == "delete_file"})
    new: dict[str, str] = {}
    for row in rows:
        if row.verdict == "redirect" and row.file not in deleted:
            new[row.file] = _replace(new.get(row.file, texts[row.file]), row.frm, row.to)
    manual = [{"row": row.id, "file": row.file, "from": row.frm} for row in rows if row.verdict == "drop" and row.file not in deleted]
    done: list[str] = []
    try:
        for rel, text in sorted(new.items()):
            if text != texts[rel]:
                (root / rel).write_text(text, encoding="utf-8")
                done.append(rel)
        for rel in deleted:
            (root / rel).unlink()
            done.append(rel)
    except OSError as e:
        raise Stop(f"書き込みの途中で失敗した（{e}）。書き終えたファイル: {', '.join(done) or '無し'}") from e
    return done, manual, []


def emit(status: str, summary: str, items: list | None = None, metrics: dict | None = None, warnings: list | None = None) -> None:
    out = {"tool": TOOL, "status": status, "summary": summary, "items": items or [], "metrics": metrics or {}}
    if warnings:
        out["warnings"] = warnings
    print(json.dumps(out, ensure_ascii=False))


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--table", default=None, help="書き換えの表（既定は <root>/scripts/plugin-refs-swap.json）")
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("check")
    c.add_argument("--tree", choices=("current", "swapped"), required=True)
    c.add_argument("--strict", action="store_true", help="「未作成」の行を既知の失敗として数えず落とす（入れ替えの前提 S2-0）")
    sub.add_parser("apply")
    return ap


def _run(a: argparse.Namespace) -> int:
    root = Path(a.root)
    table = Path(a.table) if a.table else root / TABLE
    if a.action == "apply":
        done, manual, findings = apply_table(root, load_table(table))
        if findings:
            emit("failed", f"表を当てられない（{len(findings)} 件）。何も変えていない", [f.as_item() for f in findings])
            return 1
        items = [{"file": f, "rule": "applied"} for f in done] + [{**m, "rule": "manual"} for m in manual]
        emit("ok", f"{len(done)} ファイルを書き換えか削除した。手で直す行 {len(manual)} 件", items)
        return 0
    refs, warnings = collect_refs(root)
    rows = load_table(table) if a.tree == "swapped" else []
    out = check_tree(root, refs, rows, a.tree, a.strict)
    items = [f.as_item() for f in out.findings]
    mode = f"--tree {a.tree}{' --strict' if a.strict else ''}"
    if items:
        emit("failed", f"{mode}: 外からの参照の指摘 {len(items)} 件", items, out.metrics, warnings)
        return 1
    known = f"。既知の失敗（未作成）{out.metrics['unbuilt']} 件" if out.metrics.get("unbuilt") else ""
    emit("ok", f"{mode}: 指摘なし{known}", [], out.metrics, warnings)
    return 0


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        return _run(a)
    except Stop as e:
        emit("stopped", str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
