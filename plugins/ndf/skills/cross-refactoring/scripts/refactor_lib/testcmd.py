"""項目ごとの限ったテストの語の並びを組み立てる（#933 I8）。

**実装担当はコマンドを返さず、テストの対象（`test_targets`）だけを返す。** 担当が
書いたコマンドをそのまま走らせると、検証の中身を担当が決められてしまう。進行側が
`--round-test`（省けば `--baseline-test`）を差し替えの元にして組み立て、`shell=False`
で走らせる。

**差し替えるのは既知の実行器だけである。** 位置引数をテストのファイルのパスとして
受け取ると分かっている実行器でなければ、オプションの値（`make -C backend test` の
`backend`）を対象の語と読み違えるか、`cargo test` のように位置引数をパスとして
読まない実行器で、対象のテストを走らせずに通る。

対象の語の見分けは `scope.round_test_roots` と同じ規則を使う。範囲の関門と検証とで
同じコマンドを別々に読むと、関門を通したコマンドが検証で違う形に組み立てられる。
"""
from __future__ import annotations

import os
import pathlib
import shlex
from typing import Any, Iterable, Optional

from .gitfacts import is_test_path
from .scope import VALUE_OPTIONS, covered_by_roots, test_locations

# 既知の実行器。`python -m pytest` のように複数の語から成るものは語の並びで持つ。
KNOWN_RUNNERS: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("jest",),
    ("vitest",),
)

# 実行器の前に置く起動の前置き。**読み飛ばすだけで、実行器の判定には使わない。**
_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("uv", "run"),
    ("poetry", "run"),
    ("npx",),
)

# 対象の語に含まれてはならない文字。組み立てた語の並びは `shell=False` で走らせるが、
# 担当が書いた値を語として通す以上、シェルの構文に読める値は最初から受け取らない。
_SHELL_CHARS = frozenset(";&|$`<>()\n")


def _skip_options(words: list[str], i: int) -> int:
    """`i` から始まるオプションを読み飛ばし、最初のオプションでない語の位置を返す。

    値を取ると分かっているオプション（`VALUE_OPTIONS`）は次の語も飛ばす。
    `--opt=value` は 1 語に値を含むため 1 語だけ飛ばす。
    """
    while i < len(words) and words[i].startswith("-"):
        word = words[i]
        i += 2 if ("=" not in word and word in VALUE_OPTIONS) else 1
    return i


def runner_index(words: list[str]) -> Optional[int]:
    """既知の実行器の**最後の語**の位置。既知でなければ `None`。

    `python -m pytest` なら `pytest` の位置を返す。対象の語はこの位置より後ろにある。
    """
    i = 0
    progressed = True
    while progressed:
        progressed = False
        for prefix in _PREFIXES:
            if tuple(words[i:i + len(prefix)]) == prefix:
                i = _skip_options(words, i + len(prefix))
                progressed = True
                break
    for runner in KNOWN_RUNNERS:
        if tuple(words[i:i + len(runner)]) == runner:
            return i + len(runner) - 1
    return None


def _split(command: str) -> Optional[list[str]]:
    try:
        return shlex.split(str(command or ""))
    except ValueError:
        return None


def is_known(command: str) -> bool:
    """コマンドの実行器が既知か。語として読めないコマンドは既知でない。"""
    words = _split(command)
    return bool(words) and runner_index(words) is not None


def target_indices(words: list[str], runner_idx: int, work: str) -> list[int]:
    """実行器より後ろの語のうち、テストの対象の語の位置。

    規則は `scope.round_test_roots` と同じである。`-` で始まる語・値を取るオプションの
    直後の語・作業ディレクトリの根（`.`）・絶対パスは数えない。ノード ID は `::` より
    前で読み、実在するディレクトリか、テストの置き場所に当たる実在するファイルを数える。
    """
    found: list[int] = []
    for idx in range(runner_idx + 1, len(words)):
        word, previous = words[idx], words[idx - 1]
        if word.startswith("-") or os.path.isabs(word) or previous in VALUE_OPTIONS:
            continue
        path = word.split("::", 1)[0]
        normalized = os.path.normpath(path)
        if normalized == ".":
            continue
        target = pathlib.Path(work) / path
        if target.is_dir() or (target.is_file() and is_test_path(normalized)):
            found.append(idx)
    return found


def build(command: str, targets: list[str], work: str) -> Optional[list[str]]:
    """元のコマンドの対象の語を `targets` へ差し替えた語の並び。既知でなければ `None`。

    対象の語があれば取り除き、その最初の位置へ並べる。オプションの順序（`-q` を
    対象の後ろに置く書き方）を保つためである。無ければ末尾へ足す。
    """
    words = _split(command)
    if not words:
        return None
    idx = runner_index(words)
    if idx is None:
        return None
    hits = target_indices(words, idx, work)
    if not hits:
        return words + list(targets)
    kept = [word for i, word in enumerate(words) if i not in set(hits)]
    # 取り除いた語はすべて最初の位置以降にあるため、最初の位置は残した並びでも同じである。
    return kept[:hits[0]] + list(targets) + kept[hits[0]:]


def valid_targets(
    targets: list[str], work: str, scope: list[str], planned: Iterable[str] = (),
) -> bool:
    """`test_targets` が組み立てに使えるか。1 つでも満たさなければ偽。

    - シェルの構文の文字と空白を含まない
    - `::` より前のパスが作業ディレクトリの中に実在する（絶対パスと `..` で外へ出るものは不可）。
      **ただし、その項目が足すテスト（`planned` = 計画の `tests`）は実在しなくてよい**
      （計画の時点ではまだ無い。足さなければ項目ごと `not_done` で見送られる。実装計画 I11）
    - `--scope` のテストの置き場所の中にある
    """
    planned_paths = {os.path.normpath(p) for p in planned if isinstance(p, str) and p}
    if not targets:
        return False
    locations = [os.path.normpath(loc) for loc in test_locations(list(scope), work)]
    if not locations:
        return False
    for target in targets:
        if not isinstance(target, str) or not target:
            return False
        if any(ch in _SHELL_CHARS or ch.isspace() for ch in target):
            return False
        path = target.split("::", 1)[0]
        if not path or os.path.isabs(path):
            return False
        normalized = os.path.normpath(path)
        if normalized == ".." or normalized.startswith("../"):
            return False
        if not (pathlib.Path(work) / normalized).exists() and normalized not in planned_paths:
            return False
        # `covered_by_roots` は起点が空なら全てを入れるため、空の場合は上で弾いてある。
        if not covered_by_roots(normalized, locations):
            return False
    return True


def _command_of(value: Any) -> Optional[str]:
    """状態の `round_test` / `baseline_test`（`{"command": ...}`）か、コマンドの文字列から読む。"""
    if isinstance(value, dict):
        value = value.get("command")
    return str(value) if value else None


def limited_command(
    state_like: dict[str, Any], test_targets: list[str], work: str,
    planned: Iterable[str] = (),
) -> tuple[Optional[list[str]], str]:
    """項目の検証に使う語の並びと、その由来（`targets` / `round_test` / `none`）。

    **`--baseline-test` をそのまま使うことはない**（全体のテストは危険の印の 1 回だけ）。
    差し替えの元に使うだけで、組み立てられなければ `--round-test` へ、それも無ければ
    `none`（呼ぶ側が `no_target` で見送る）。
    """
    round_test = _command_of(state_like.get("round_test"))
    source = round_test or _command_of(state_like.get("baseline_test"))
    scope = state_like.get("target_scope") or state_like.get("scope") or []
    if source and is_known(source) and valid_targets(list(test_targets or []), work, scope, planned):
        built = build(source, list(test_targets), work)
        if built is not None:
            return built, "targets"
    if round_test:
        words = _split(round_test)
        if words:
            return words, "round_test"
    return None, "none"


# ---------- 落ちたテストの取り出しと走らせ直し（#933 決定 22 / I14） ----------

# pytest の末尾の要約（`-q` でも既定で出る）の行頭。`ERROR: file or directory not found`
# のように `:` が続く行は落ちたテストではないため、空白までを含めて見る。
_SUMMARY_PREFIXES = ("FAILED ", "ERROR ")


def _node_of(rest: str) -> str:
    """要約の 1 行から ID を切り出す。理由は ` - ` の後ろに付く。

    パラメータの値が ` - ` を含むこと（`test_p[c - d]`）があるため、角括弧の対が
    閉じた位置の ` - ` だけを区切りと読む。
    """
    start = 0
    while True:
        pos = rest.find(" - ", start)
        if pos < 0:
            return rest.strip()
        head = rest[:pos]
        if head.count("[") == head.count("]"):
            return head.strip()
        start = pos + 1


def failed_nodes(output: str) -> list[str]:
    """pytest の出力の要約から、落ちたテスト（`FAILED` / `ERROR`）の ID を出た順に返す。

    収集の失敗はファイルの単位（`ERROR tests/test_b.py`）で出る。見つからなければ空。
    """
    found: list[str] = []
    for line in str(output or "").splitlines():
        for prefix in _SUMMARY_PREFIXES:
            if line.startswith(prefix):
                node = _node_of(line[len(prefix):])
                if node and node not in found:
                    found.append(node)
    return found


def rerun_command(command: str, nodes: list[str], work: str) -> Optional[list[str]]:
    """全体のテストの対象を落ちたテストの ID へ差し替えた語の並び。pytest でなければ `None`。

    **取り出しは pytest の要約の形だけを確かめてある。** jest / vitest の出力の形は
    確かめていないため、ここで `None` を返し、呼ぶ側は見分けと直しを行わない。
    作業ディレクトリの根（`.`）は `build` が対象の語に数えないため先に除く（残すと
    全体を走らせ直す）。
    """
    words = _split(command)
    if not words:
        return None
    idx = runner_index(words)
    if idx is None or words[idx] != "pytest":
        return None
    kept = [word for i, word in enumerate(words)
            if not (i > idx and os.path.normpath(word) == "." and words[i - 1] not in VALUE_OPTIONS)]
    return build(shlex.join(kept), list(nodes), work)
