#!/usr/bin/env python3
"""check-plugin-refs.py: 外からの参照（`plugins/ndf/` の外から `plugins/ndf/` の中を名指すパス）を確かめ、書き換えの表を当てる。

    python3 scripts/check-plugin-refs.py check --tree current [--root DIR]
    python3 scripts/check-plugin-refs.py check --tree swapped [--strict] [--root DIR] [--table FILE]
    python3 scripts/check-plugin-refs.py apply [--root DIR] [--table FILE]

入れ替え（#3304。`plugins/ndf-next/` を `plugins/ndf/` へ置き換える）で消えるパスを、workflow・宣言・指示書・根の検査が
指したまま残らないようにする（#3341 の決定 1。設計は issues/issue-3341-design-detail.md の R0）。

- `check --tree current`: 参照先が今の `plugins/ndf/` に実在するか。表を読まない
- `check --tree swapped`: `plugins/ndf-next/` を `plugins/ndf/` と読み替え、書き換えの表（scripts/plugin-refs-swap.json）を
  当てた参照先が実在するか。表に無く入れ替えの後に実在しない参照（同じファイルの行の `from` の出現の外にある出現）・
  行き先の無い「向け直す」の行・`to` が `target` を名指さない「向け直す」の行・行き先ができた「未作成」の行・参照を持たない行で
  落ちる。副命令の行き先は、入れ替え先の `bin/ndf` が実行できるファイルであることも要る。行き先の無い「未作成」の行は既知の失敗として
  数えるだけで、`--strict` のときだけ落ちる。「11.x へ送る」（`deferred`）の行は、行き先を作るのを入れ替えの後へ送った行で、
  移しの課題（`issue`）を名指し、`--strict` でも落とさずに数える（#3304 の S2-0 の判断 (B)。移しは #3791）。行き先ができたら落ちる
- `apply`: 「向け直す」の行の文字列を置き換え、「ファイルを消す」の行のファイルを消し、「消す」と「11.x へ送る」の行を手で直す一覧として返す。
  「未作成」の行が残る・表とファイルが合わない・`to` が `target` を名指さない・書き換えるか消すファイルが HEAD に追跡されて
  いないか未コミットの変更があるときは 1 つも書かない（Git で戻せないファイルを変えない）。
  表のファイルが正規化したリポジトリ相対のパスでない・worktree の外を指す・シンボリックリンクなら止まる。書き換えは同じディレクトリの一時ファイルへ元のモードで
  書いてから置き換える（入れ替えの PR が 1 回だけ打つ）

結果は標準出力へ 1 行の JSON で出す。終了コード: 0 = 指摘なし / 1 = 指摘あり / 2 = 表か git かエントリポイントを読めない。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import posixpath
import re
import shutil
import stat
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
# プラグインの根からの公開のエントリポイント（副命令の行き先はこれを通して呼ばれる）
ENTRYPOINT = "bin/ndf"
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
TABLE_VERDICTS = ("redirect", "unbuilt", "deferred", "drop", "delete_file")
TARGET_VERDICTS = ("redirect", "unbuilt", "deferred")  # 行き先（target）を使う判定
FEATURE_VERDICTS = ("unbuilt", "deferred")  # 移す機能（feature）を名指す判定
ISSUE_REF = re.compile(r"#\d+")
GLOB_CHARS = "*?[{"
# 行き先の副命令の有無をまとめて確かめる 1 回の `uv run` の上限（初回は依存の環境を作るため長めにとる）
PROBE_TIMEOUT = 600
USAGE_EXIT = 2
# プラグインの環境で打つ確かめ。語の並びごとに、サブコマンドの表（cli.COMMANDS）のモジュールの main を `--help` 付きで
# 呼び、終了コードと使い方を集めて JSON 1 行で出す（副命令ごとにプラグインを起動しない）
PROBE_SCRIPT = r"""
import contextlib, importlib, io, json, sys
from ndf import cli
out = []
for words in json.loads(sys.argv[1]):
    module = cli.COMMANDS.get(words[0])
    if module is None:
        out.append({"code": 2, "help": "", "error": ""})
        continue
    usage, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(usage), contextlib.redirect_stderr(err):
            code = importlib.import_module(module).main([*words[1:], "--help"])
    except SystemExit as e:
        code = e.code
    except Exception as e:
        code = 1
        err.write(f"{type(e).__name__}: {e}")
    if not isinstance(code, int):
        code = 0 if code is None else 1
    out.append({"code": code, "help": usage.getvalue(), "error": err.getvalue()})
print(json.dumps(out))
"""


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
    issue: str | None = None

    @property
    def ref(self) -> str:
        """この行が名指す参照（`from` の中の最初の `plugins/ndf/` のパス）。"""
        return refs_in(self.frm)[0]

    def finding(self, rule: str) -> Finding:
        """この行についての指摘。"""
        return Finding(rule, self.file, self.ref, self.id)


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


def _has_target_selector(target: object) -> bool:
    """行き先が path（文字列）か command（空でない文字列の配列）を持つ object か。path を先に見る。"""
    if not isinstance(target, dict):
        return False
    if isinstance(target.get("path"), str):
        return True
    command = target.get("command")
    return isinstance(command, list) and bool(command) and all(isinstance(w, str) for w in command)


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
    if v in TARGET_VERDICTS and not _has_target_selector(r.get("target")):
        return f"{v} に target（path か command）が無い"
    if v in FEATURE_VERDICTS and not isinstance(r.get("feature"), str):
        return f"{v} に feature が無い"
    if v == "deferred" and not (isinstance(r.get("issue"), str) and ISSUE_REF.fullmatch(r["issue"])):
        return "deferred に移しの課題（issue: #<番号>）が無い"
    return None


def _checked_target(verdict: str, target: dict) -> dict | None:
    """行き先を使う判定（向け直す・未作成）の行だけ、選んだ 1 つの行き先（path を先に見る）と `placed_by` を返す。
    ほかの判定の行は None。経路の検査と副命令の一括収集が、同じ確かめ済みの値を読む。"""
    if verdict not in TARGET_VERDICTS:
        return None
    key = "path" if isinstance(target.get("path"), str) else "command"
    return {key: target[key], **({"placed_by": target["placed_by"]} if "placed_by" in target else {})}


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
        target = _checked_target(r["verdict"], r.get("target"))
        out.append(Row(r["id"], r["file"], r["verdict"], r["from"], r.get("to"), target, r.get("feature"), r.get("issue")))
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
    """プラグインがその副命令を知っているかを `--help` の使い方で確かめる。まだ確かめていない語の並びを 1 回の
    `uv run` にまとめて打つ（同じ語の並びは 1 度だけ）。"""

    def __init__(self, plugin: Path) -> None:
        self.plugin = plugin
        self.cache: dict[tuple[str, ...], bool] = {}
        self.calls = 0

    def prefetch(self, commands: list[tuple[str, ...]]) -> None:
        """まだ確かめていない語の並びを 1 回の起動で確かめる。"""
        todo = sorted({tuple(w) for w in commands} - self.cache.keys())
        if todo:
            self.cache.update(zip(todo, self._probe(todo)))

    def __call__(self, words: tuple[str, ...]) -> bool:
        self.prefetch([words])
        return self.cache[words]

    def _probe(self, todo: list[tuple[str, ...]]) -> list[bool]:
        uv = shutil.which("uv")
        if uv is None:
            raise Stop("uv が無く、行き先の副命令を確かめられない")
        self.calls += 1
        with tempfile.TemporaryDirectory(prefix="check-plugin-refs-") as state:
            # 利用者の状態の置き場に触れず、依存の環境もプラグインの中に作らない（uv のキャッシュから組むので速い）。
            # プラグインはパッケージにしない（package = false）ため src を読み込み先に足す
            env = {
                **os.environ,
                "NDF_STATE_HOME": state,
                "UV_PROJECT_ENVIRONMENT": os.path.join(state, "venv"),
                "PYTHONPATH": str(self.plugin / "src"),
            }
            argv = [uv, "run", "--frozen", "--no-dev", "--quiet", "--project", str(self.plugin), "python", "-P", "-c", PROBE_SCRIPT]
            try:
                p = subprocess.run(
                    [*argv, json.dumps([list(w) for w in todo])], capture_output=True, text=True, env=env, timeout=PROBE_TIMEOUT
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                raise Stop(f"{self.plugin} の副命令を確かめられない: {e}") from e
        if p.returncode != 0:
            last = (p.stderr.strip().splitlines() or [""])[-1]
            raise Stop(f"{self.plugin} の副命令を確かめる uv run が終了コード {p.returncode}: {last}")
        try:
            answers = json.loads((p.stdout.strip().splitlines() or [""])[-1])
        except ValueError as e:
            raise Stop(f"{self.plugin} の副命令の確かめの出力を読めない（{e}）") from e
        if not isinstance(answers, list) or len(answers) != len(todo):
            raise Stop(f"{self.plugin} の副命令の確かめの出力が {len(todo)} 件の答えでない")
        return [self._known(words, a) for words, a in zip(todo, answers)]

    def _known(self, words: tuple[str, ...], answer: dict) -> bool:
        if answer["code"] == USAGE_EXIT:
            return False
        if answer["code"] != 0:
            last = (answer["error"].strip().splitlines() or [""])[-1]
            raise Stop(f"{self.plugin} の {' '.join(words)} --help が終了コード {answer['code']}: {last}")
        return usage_names(answer["help"], words)


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
    """行き先が入れ替え先にあるか。副命令は、公開のエントリポイント（`bin/ndf`）が実行できるファイルであり、
    プラグインがその副命令を知っているときにある（起動の誤りはプラグインのテストが `bin/ndf` を起動して確かめる）。"""
    if "command" in target:
        entry = next_root / ENTRYPOINT
        return entry.is_file() and os.access(entry, os.X_OK) and probe(tuple(target["command"]))
    return target_exists(next_root, target["path"])


def to_names_target(row: Row) -> bool:
    """「向け直す」の行の `to` が `target` の行き先を名指すか（`to` を打ち間違えると、検査が通っても `apply` が
    壊れた参照を書く）。副命令は `plugins/ndf/bin/ndf <語の並び>`、パスは `to` の中の `plugins/ndf/<path>` で当てる。"""
    if "command" in row.target:
        call = re.compile(re.escape(" ".join([f"{PLUGIN}/{ENTRYPOINT}", *row.target["command"]])) + r"(?![^\s\"'`])")
        return bool(call.search(row.to))
    return f"{PLUGIN}/{row.target['path']}" in refs_in(row.to)


def _read(root: Path, rel: str, cache: dict[str, str | None]) -> str | None:
    if rel not in cache:
        try:
            cache[rel] = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            cache[rel] = None
    return cache[rel]


def _check_row(row: Row, text: str | None, next_root: Path, strict: bool, probe: CommandProbe, out: Outcome) -> None:
    if text is None or not occurrences(text, row.frm):
        out.findings.append(row.finding("stale_row"))
        return
    if row.verdict == "redirect":
        if not to_names_target(row):
            out.findings.append(row.finding("to_mismatch"))
            return
        if target_ready(next_root, row.target, probe):
            return
        if row.target.get("placed_by") == "swap":
            # 入れ替えの PR が置く行き先（S2 が新しい根へ置く README など）。置き忘れは入れ替えの後の current が落とす
            out.metrics["placed_by_swap"] += 1
            return
        out.findings.append(row.finding("target_missing"))
    elif row.verdict == "unbuilt":
        if target_ready(next_root, row.target, probe):
            out.findings.append(row.finding("unbuilt_exists"))
        elif strict:
            out.findings.append(row.finding("unbuilt"))
        else:
            out.metrics["unbuilt"] += 1
    elif row.verdict == "deferred":
        if target_ready(next_root, row.target, probe):
            out.findings.append(row.finding("deferred_exists"))
        else:
            out.metrics["deferred"] += 1


def _rel(path: str) -> str:
    """`plugins/ndf/` で始まるパスを、`plugins/ndf/` からの相対へ直す。"""
    return path[len(PLUGIN) + 1 :]


def check_current(root: Path, refs: list[Ref]) -> Outcome:
    """参照先が今の `plugins/ndf/` に実在するか。表を読まない。"""
    base = root / PLUGIN
    return Outcome([Finding("missing", r.file, r.path) for r in refs if not target_exists(base, _rel(r.path))], {"refs": len(refs)})


def _uncovered(text: str, path: str, frms: list[str]) -> bool:
    """`text` の中の `path` の出現に、表の行の `from` の出現の外にあるものがあるか（行番号では当てない）。

    同じパスを別の引数で呼ぶ行は、表の行の `from` と一致しないため表に無い参照として数える。"""
    spans = [(start, start + len(frm)) for frm in frms for start in occurrences(text, frm)]
    return any(m.group(0).rstrip(".,") == path and not any(a <= m.start() < b for a, b in spans) for m in REF_PATTERN.finditer(text))


def _command_targets(rows: list[Row]) -> list[tuple[str, ...]]:
    return [tuple(row.target["command"]) for row in rows if row.target and "command" in row.target]


def check_swapped(root: Path, refs: list[Ref], rows: list[Row], strict: bool, probe: CommandProbe | None = None) -> Outcome:
    """`plugins/ndf-next/` を `plugins/ndf/` と読み替え、表を当てた参照先の実在と表の行の状態を判定する。"""
    out = Outcome(metrics={"refs": len(refs)})
    next_root = root / NEXT
    probe = probe or CommandProbe(next_root)
    out.metrics.update(rows=len(rows), unbuilt=0, deferred=0, placed_by_swap=0)
    texts: dict[str, str | None] = {}
    for r in refs:
        if target_exists(next_root, _rel(r.path)):
            continue
        text = _read(root, r.file, texts)
        if text is None or _uncovered(text, r.path, [row.frm for row in rows if row.file == r.file]):
            out.findings.append(Finding("unlisted", r.file, r.path))
    probe.prefetch(_command_targets(rows))
    for row in rows:
        _check_row(row, _read(root, row.file, texts), next_root, strict, probe, out)
    out.metrics["probes"] = probe.calls
    return out


def _replace(text: str, frm: str, to: str) -> str:
    for start in reversed(occurrences(text, frm)):
        text = text[:start] + to + text[start + len(frm) :]
    return text


def _worktree_file(root: Path, rel: str) -> None:
    """表の `file` が正規化したリポジトリ相対のパスで、worktree の中の通常のファイル（か、まだ無いファイル）でなければ止まる。"""
    path = root / rel
    if (
        rel != posixpath.normpath(rel)
        or posixpath.isabs(rel)
        or rel.startswith("../")
        or path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve())
        or (path.exists() and not path.is_file())
    ):
        raise Stop(f"{rel}: worktree の中の通常のファイルでないため書き換えない")


def _git_out(root: Path, *args: str) -> bytes:
    """git を打って標準出力を返す。パスは glob でなく文字どおりに当てる。打てなければ止まる。"""
    try:
        return subprocess.run(["git", "--literal-pathspecs", "-C", str(root), *args], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", b"") or b""
        raise Stop(f"git {args[0]} を打てない: {detail.decode(errors='replace').strip() or e}") from e


def _untracked(root: Path, rels: list[str]) -> set[str]:
    """`rels` のうち HEAD に追跡されていないもの（無視するファイルを含む。git status に出ないため HEAD で確かめる）。"""
    if not rels:
        return set()
    out = _git_out(root, "ls-tree", "-r", "-z", "--name-only", "--full-tree", "HEAD", "--", *rels)
    return set(rels) - set(out.decode().split("\0"))


def _dirty(root: Path, rels: list[str]) -> set[str]:
    """`rels` のうち未コミットの変更（追跡していないファイルを含む）があるか、変更を git status から隠す印
    （`assume-unchanged`・`skip-worktree`）が index に立つもの。git status と git ls-files を 1 回ずつ打つ。"""
    if not rels:
        return set()
    return (_status_dirty(root, rels) | _index_hidden(root, rels)) & set(rels)


def _index_hidden(root: Path, rels: list[str]) -> set[str]:
    """`rels` のうち index に `assume-unchanged`（`git ls-files -v` の小文字の印）か `skip-worktree`（`S`）が立つもの。
    git status はこれらの作業ツリーの変更を報告しないため、変更が無いと確かめられない。"""
    out = _git_out(root, "ls-files", "-v", "-z", "--", *rels)
    return {entry[2:] for entry in out.decode().split("\0") if len(entry) > 2 and (entry[0].islower() or entry[0] == "S")}


def _status_dirty(root: Path, rels: list[str]) -> set[str]:
    """`rels` のうち git status が変更を報告するもの（追跡していないファイルと、名前の変更・複製の元を含む）。"""
    out = _git_out(root, "status", "--porcelain", "-z", "--untracked-files=all", "--", *rels)
    # 1 件は `XY <パス>`。名前の変更と複製（X か Y が R・C）は、続く 1 件に元のパスを持つ
    found: set[str] = set()
    entries = iter(out.decode().split("\0"))
    for entry in entries:
        if len(entry) > 3:
            found.add(entry[3:])
            if "R" in entry[:2] or "C" in entry[:2]:
                found.add(next(entries, ""))
    return found


def _apply_findings(root: Path, rows: list[Row], texts: dict[str, str | None]) -> list[Finding]:
    """表を当てられない行の指摘（「未作成」の行・ファイルか `from` が見つからない行（消す行も、表を作った時点の参照が
    消えていれば古い行とする）・`to` が行き先を名指さない行・HEAD に追跡されていないか未コミットの変更のある、
    書き換える先か消すファイル）。読んだ本文を `texts` へ残す。書き込む前に全行を確かめる。

    書き換える先も確かめるのは、途中で失敗したときに書き終えたファイルを `git checkout -- <パス>` で戻しても、
    利用者の変更を消さないためである。"""
    findings = []
    touched: list[Row] = []
    for row in rows:
        if row.verdict == "unbuilt":
            findings.append(row.finding("unbuilt"))
            continue
        _worktree_file(root, row.file)
        text = _read(root, row.file, texts)
        if text is None or not occurrences(text, row.frm):
            findings.append(row.finding("stale_row"))
        elif row.verdict == "redirect" and not to_names_target(row):
            findings.append(row.finding("to_mismatch"))
        elif row.verdict in ("redirect", "delete_file"):
            touched.append(row)
    files = sorted({row.file for row in touched})
    untracked = _untracked(root, files)
    dirty = _dirty(root, files)
    findings += [row.finding("untracked" if row.file in untracked else "uncommitted") for row in touched if row.file in untracked | dirty]
    return findings


def _rewritten(rows: list[Row], texts: dict[str, str | None], deleted: list[str]) -> dict[str, str]:
    """「向け直す」の行を当てた後の本文（消すファイルは除く）。"""
    new: dict[str, str] = {}
    for row in rows:
        if row.verdict == "redirect" and row.file not in deleted:
            new[row.file] = _replace(new.get(row.file, texts[row.file]), row.frm, row.to)
    return new


def _replace_file(path: Path, text: str) -> None:
    """同じディレクトリの一時ファイルへ元のモードで書き終えてから置き換える（途中で失敗しても元のファイルは切り詰めない）。"""
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _write_all(root: Path, new: dict[str, str], texts: dict[str, str | None], deleted: list[str]) -> list[str]:
    """変わった本文を書き、消すファイルを消す。戻り値は書き換えたか消したファイル。"""
    done: list[str] = []
    try:
        for rel, text in sorted(new.items()):
            if text != texts[rel]:
                _replace_file(root / rel, text)
                done.append(rel)
        for rel in deleted:
            (root / rel).unlink()
            done.append(rel)
    except OSError as e:
        raise Stop(f"書き込みの途中で失敗した（{e}）。書き終えたファイル: {', '.join(done) or '無し'}") from e
    return done


def apply_table(root: Path, rows: list[Row]) -> tuple[list[str], list[dict], list[Finding]]:
    """表を当てる。戻り値は（書き換えたか消したファイル, 手で直す行, 指摘）。指摘が 1 件でもあれば何も変えていない。"""
    texts: dict[str, str | None] = {}
    findings = _apply_findings(root, rows, texts)
    if findings:
        return [], [], findings
    deleted = sorted({row.file for row in rows if row.verdict == "delete_file"})
    new = _rewritten(rows, texts, deleted)
    manual = [
        {"row": row.id, "file": row.file, "from": row.frm, **({"issue": row.issue} if row.issue else {})}
        for row in rows
        if row.verdict in ("drop", "deferred") and row.file not in deleted
    ]
    return _write_all(root, new, texts, deleted), manual, []


def emit(status: str, summary: str, items: list | None = None, metrics: dict | None = None, warnings: list | None = None) -> None:
    out = {"tool": TOOL, "status": status, "summary": summary, "items": items or [], "metrics": metrics or {}}
    if warnings:
        out["warnings"] = warnings
    print(json.dumps(out, ensure_ascii=False))


def _parser() -> argparse.ArgumentParser:
    # --root と --table は副命令の前でも後でも受ける（後に書いた値が先に効く。副命令の側は書かなければ値を置かない）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")
    common.add_argument("--table", default=argparse.SUPPRESS, help="書き換えの表（既定は <root>/scripts/plugin-refs-swap.json）")
    # 既定値は _run で埋める（set_defaults は parents で共有した action の既定を書き換え、副命令の側が前の値を上書きする）
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("check", parents=[common])
    c.add_argument("--tree", choices=("current", "swapped"), required=True)
    c.add_argument("--strict", action="store_true", help="「未作成」の行を既知の失敗として数えず落とす（入れ替えの前提 S2-0）")
    sub.add_parser("apply", parents=[common])
    return ap


def _run_apply(root: Path, table: Path) -> int:
    done, manual, findings = apply_table(root, load_table(table))
    if findings:
        emit("failed", f"表を当てられない（{len(findings)} 件）。何も変えていない", [f.as_item() for f in findings])
        return 1
    items = [{"file": f, "rule": "applied"} for f in done] + [{**m, "rule": "manual"} for m in manual]
    emit("ok", f"{len(done)} ファイルを書き換えか削除した。手で直す行 {len(manual)} 件", items)
    return 0


def _run_check(a: argparse.Namespace, root: Path, table: Path) -> int:
    refs, warnings = collect_refs(root)
    out = check_swapped(root, refs, load_table(table), a.strict) if a.tree == "swapped" else check_current(root, refs)
    items = [f.as_item() for f in out.findings]
    mode = f"--tree {a.tree}{' --strict' if a.strict else ''}"
    if items:
        emit("failed", f"{mode}: 外からの参照の指摘 {len(items)} 件", items, out.metrics, warnings)
        return 1
    known = f"。既知の失敗（未作成）{out.metrics['unbuilt']} 件" if out.metrics.get("unbuilt") else ""
    known += f"。11.x へ送った行 {out.metrics['deferred']} 件" if out.metrics.get("deferred") else ""
    emit("ok", f"{mode}: 指摘なし{known}", [], out.metrics, warnings)
    return 0


def _run(a: argparse.Namespace) -> int:
    root = Path(getattr(a, "root", None) or ROOT)
    table = Path(a.table) if getattr(a, "table", None) else root / TABLE
    if a.action == "apply":
        return _run_apply(root, table)
    return _run_check(a, root, table)


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        return _run(a)
    except Stop as e:
        emit("stopped", str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
