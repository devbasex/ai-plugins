"""CI テストの宣言のあるリポジトリで、手元のテストの起動を止める PreToolUse の guard（#3056 の I9）。Claude Code と Codex のシェル。

作業場所から上へ辿って最初の `.ndf/project.json` が `test.ci.only: true` を持つときだけ判定する。コマンドの位置で起動する
コマンド（`&&`・`||`・`;`・`|` で区切った各部分。構文木は `lib/shparse.py`）の語の並びを、`kind: test` の suite の起動の形と
照らす。

- 起動の形: suite の `command` の語のうち、`runner`（語そのものか、パスの最後の名前）と同じ語まで。`runner` が語に無ければ
  `scope_command` の `{paths}` の前まで。前置きが 1 語だけになり、`command` の 2 語目がサブコマンド（`go test`・`npm test`・
  `make test`）のときは 2 語目まで含める。2 語目が `-` で始まる語か、作業場所のパス（`/` を含む語・`.`・宣言のあるディレクトリから
  見て在るファイルやディレクトリ）なら広げない（`pytest tests -q` の形はランナー 1 語で照らす）
- 例外: サブコマンドを取るツール（`DISPATCHERS`）の 2 語目は同じ名前のパスがあってもサブコマンドとして読み、2 語目が `run`・`exec` なら
  3 語目（スクリプトの名前）まで含める
- 当たり: コマンドの語（包み `env`・`timeout` などと変数の代入を飛ばした後）が、起動の形の前置きで始まる。または
  起動の形のうちランナー（suite の `runner` の語。起動の形に無ければ起動の形の全体）から後の語の並びが語にあり、それより前の語が
  すべて起動の形の語かオプションである（ランナーの前のオプションを省いた・足した言い換え）。`python -m <ランナー>` はランナーの
  直接の起動と同じに読む。`go test` のように起動の形を 2 語目まで広げた suite は `go test` の並びで当て、`test -f` は当てない

`kind: lint` の suite のコマンド・`test-run.py`（自分で静的解析だけを走らせる）・宣言の無いリポジトリは止めない。
人が自分の端末で打つコマンドは hook の対象外のため、例外の手段は設けない。
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

import project_decl
import shparse as sp

from . import payload as pl
from .shell_checks import ASSIGN, MAX_DEPTH, SHELLS, _shell_script, _skip_wrappers
from .token_guard import deny

SCRIPTS = Path(__file__).resolve().parents[1]
REASON = (
    "CI テストの宣言（.ndf/project.json の test.ci.only）があるため、手元でテストを走らせない（{suite} は CI で走る）。"
    "結果は `gh pr checks <PR>` か `python3 {scripts}/merged-steps.py probe --pr <PR>` で読む。"
    "静的解析は `python3 {scripts}/test-run.py scope --paths <パス>` で走らせる"
)


def find_root(start: str) -> Path | None:
    """`start` から上へ辿って最初の `.ndf/project.json` のあるディレクトリ。無ければ None。"""
    here = Path(start or os.getcwd()).resolve()
    return next((d for d in (here, *here.parents) if (d / project_decl.DECL).is_file()), None)


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _words_of(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


# サブコマンドを取るツール。2 語目は作業場所に同じ名前のパスがあってもサブコマンドとして読む（`test/` を持つ Go・Node）
DISPATCHERS = frozenset({"go", "npm", "pnpm", "yarn", "bun", "deno", "make", "cargo", "dotnet", "mvn", "gradle", "gradlew", "mix"})
# 続く語がスクリプトの名前になるサブコマンド（`npm run test` は `npm run` で止めず `test` まで含める）
RUN_WORDS = frozenset({"run", "run-script", "exec"})


def _is_subcommand_word(word: str, root: Path | None, tool: str = "") -> bool:
    """`command` の 2 語目がサブコマンドか（オプション・作業場所のパスでない）。`tool` がサブコマンドを取るツールならパスかを見ない。"""
    if not word or word.startswith("-") or "/" in word or word in (".", ".."):
        return False
    if _base(tool) in DISPATCHERS:
        return True
    return not (root is not None and (root / word).exists())


def launch_form(suite: dict, root: Path | None = None) -> list[str]:
    """suite の起動の形（前置きの語の並び）。組めなければ空。`root` は宣言のあるディレクトリ（2 語目がパスかを見る）。"""
    words = _words_of(str(suite.get("command") or ""))
    runner = str(suite.get("runner") or "")
    at = next((i for i, w in enumerate(words) if runner and _base(w) == runner), None)
    if at is not None:
        form = words[: at + 1]
    else:
        scope = str(suite.get("scope_command") or "")
        form = _words_of(scope.split("{paths}", 1)[0]) if "{paths}" in scope else []
    if len(form) == 1 and len(words) > 1 and words[0] == form[0] and _is_subcommand_word(words[1], root, words[0]):
        form = words[:2]
        if words[1] in RUN_WORDS and len(words) > 2 and not words[2].startswith("-"):
            form = words[:3]
    return form


def runner_tail(suite: dict, form: list[str]) -> list[str]:
    """起動の形のうち、ランナー（suite の `runner` と同じ語。パスの最後の名前でも当てる）から後の語。無ければ起動の形の全体。"""
    runner = str(suite.get("runner") or "")
    at = next((i for i, w in enumerate(form) if runner and _base(w) == runner), 0)
    return form[at:]


def test_forms(decl: dict, root: Path | None = None) -> list[tuple[str, list[str], list[str]]]:
    """(suite の名前, 起動の形, ランナーから後の語) の並び。宣言が無い・`test.ci.only` が真でなければ空。"""
    test = decl.get("test")
    if not isinstance(test, dict) or not isinstance(test.get("ci"), dict) or test["ci"].get("only") is not True:
        return []
    out = []
    for suite in test.get("suites") or []:
        if isinstance(suite, dict) and (suite.get("kind") or "test") == "test":
            form = launch_form(suite, root)
            if form:
                out.append((str(suite.get("name") or "test"), form, runner_tail(suite, form)))
    return out


_PYTHON = re.compile(r"python[0-9.]*$")


def hits(words: list[str], form: list[str], tail: list[str] | None = None) -> bool:
    """コマンドの語の並びが起動の形に当たるか。`tail` は起動の形のうちランナーから後の語（省けば起動の形の全体）。

    ランナーより前の語は、起動の形の語かオプション（`-` で始まる語。`uv run --no-sync` など）だけを許す。
    `python -m <ランナー>`（`python3 -m pytest` など）はランナーの直接の起動と同じに読む。
    """
    if not words or not form:
        return False
    if words[: len(form)] == form:
        return True
    tail = tail or form
    runner, rest = _base(tail[0]), tail[1:]
    at = next(
        (i for i, w in enumerate(words) if _base(w) == runner and words[i + 1 : i + 1 + len(rest)] == rest),
        None,
    )
    if at is None:
        return False
    before = words[:at]
    if len(before) >= 2 and before[-1] == "-m" and _PYTHON.match(_base(before[-2])):
        before = before[:-2]
    return all(w in form or w.startswith("-") for w in before)


def _launched_commands(cmd: str, depth: int = 0) -> list[list[str]]:
    """コマンドの位置で起動するコマンドの語の並び（包みと変数の代入を飛ばした後）。ヒアドキュメントの本文は見ない。
    `bash -c` / `sh -c` の中も読む。"""
    if depth > MAX_DEPTH:
        return []
    out, stack = [], [sp.parse_bash(cmd)]
    while stack:
        n = stack.pop()
        if n.type == "heredoc_body":
            continue
        if n.type == "command":
            words = [str(sp.unquote(c)) for c in n.children if sp.field_of(n, c) in ("name", "argument")]
            i = 0
            while i < len(words) and ASSIGN.match(words[i]):
                i += 1
            words = words[i:]
            words = words[_skip_wrappers(words) :]
            script = _shell_script(words[1:]) if words and _base(words[0]) in SHELLS else None
            out.extend(_launched_commands(script, depth + 1) if script else [words])
        stack.extend(c for c in n.children if c.child_count)
    return out


def ci_test_decision(ev: pl.Event) -> dict | None:
    """宣言のあるリポジトリで、テストの suite の起動に当たるシェルのコマンドを拒む。当たらなければ None。"""
    raw = ev.tool_input.get("command")
    if isinstance(raw, list) and len(raw) >= 3 and _base(str(raw[0])) in SHELLS and _shell_script([str(w) for w in raw[1:]]):
        cmd = _shell_script([str(w) for w in raw[1:]])  # Codex の `["bash", "-lc", "<コマンド>"]` は中身を読む
    else:
        cmd = ev.command_text()
    if not cmd:
        return None
    root = find_root(ev.command_cwd() or ev.cwd)
    forms = test_forms(project_decl.read_project_decl(root), root) if root else []
    if not forms:
        return None
    for words in _launched_commands(cmd):
        for name, form, tail in forms:
            if hits(words, form, tail):
                return deny(REASON.format(suite=name, scripts=SCRIPTS))
    return None
