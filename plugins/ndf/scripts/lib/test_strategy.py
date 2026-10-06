"""テストの戦略（#1334・#1483）。宣言（`.ndf/project.json` の `test`）と引数から、suite の種別（テスト / 静的解析）・
範囲テストの組み立て・全体テストとその置き場（手元か CI か）・時間の上限・起動の失敗の判別を決める。
cross-refactoring・supervise・`test-run.py`・cross-review が同じ関数を使う（I10）。

**純粋な処理だけを置く。** ファイルもプロセスも触らない（宣言の読み取りは `decl_of` だけで、`project_decl` を通す）。
戦略と種別はコマンドの語・実行器の名前・前置きを見ずに決まる（I1。種別は宣言の `kind` か引数 `--test-kind` だけ）。
コマンドへの加工は `{paths}` の字句をシェルの引用で守った対象の並びへ置き換えることだけである（I2・`fill`）。
コマンドはどれもシェル経由で 1 つずつ走らせる文字列である（I5・I6）。解けないときは `StrategyError` を上げ、
終了コードは呼ぶ側が決める。標準ライブラリだけで書く。
"""

from __future__ import annotations

import fnmatch
import math
import posixpath
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

LOCAL_FULL = "local-full"
LOCAL_SCOPED_CI_WHOLE = "local-scoped-ci-whole"
ROUND_ONLY = "round-only"
STRATEGIES = (LOCAL_FULL, LOCAL_SCOPED_CI_WHOLE, ROUND_ONLY)

# suite の種別（#1483 決定 1）。書かなければテスト（I3）。
TEST = "test"
LINT = "lint"
KINDS = (TEST, LINT)

# 実行の結果（`outcome`。I7）。
PASSED = "passed"
FAILED = "failed"
LAUNCH_FAILED = "launch_failed"
TIMED_OUT = "timed_out"

PATHS = "{paths}"
_GLOB_CHARS = ("*", "?", "[")
# 所要の出所を採る順（決定 2）。手元の実測 → CI の JUnit の直列の合計 → CI のテストの step の合計。
DURATION_SOURCES = ("ndf-record", "ci-junit", "ci-steps")
# 所要がこれを超え、宣言の CI が読めれば、全体テストを CI に任せる（決定 2。既定の予算 30 分の 1/3）。
CI_WHOLE_THRESHOLD_SECONDS = 600.0

# 時間の係数（決定 8）。B は予算（分）、w は全体テストの所要、x は着手前に手元で走らせたテストの実測、c は CI の壁時計。
INIT_TEST_SHARE = 0.10  # 着手前のテスト 1 回の上限 = max(0.10·B, 3·w)（CI に任せる戦略は max(0.10·B, 3·s)。s が分からなければ 0.10·B）
TEST_FACTOR = 3.0  # テスト 1 回の上限 = max(3·x, 0.01·B)
TEST_FLOOR_SHARE = 0.01
CI_WAIT_SHARE = 0.05  # CI の待ちの上限 = max(3·c, 0.05·B)
CI_WAIT_UNKNOWN_SHARE = 0.20  # c が分からなければ 0.20·B
# 所要が宣言に無く予算も無い（supervise の計画）ときだけ使う値。計画に「所要が不明のときの値」と書く。
UNKNOWN_DURATION_LIMITS = {"test_timeout": 900, "whole_timeout": 1800, "ci_wait_timeout": 3600}

MISSING_TEST = ".ndf/project.json の test が無い（か不明: {reason}）。/ndf:development-workflow の手順 0 で解析するか、--round-test を渡す"
NO_LINT_WHOLE = "静的解析の雛形に {paths} を埋める範囲のパスが無いため、静的解析の全体テストを組まない（{paths} を . にしない）"
WHOLE_FROM_TEMPLATE = (
    "全体テストは宣言の test.suites[].command が無いため、雛形の {paths} を . にしたコマンドで組んだ。"
    "静的解析のコマンドなら --test-kind lint か、宣言の test.suites（kind: lint）を使う"
)


class StrategyError(Exception):
    """戦略を解けない。文は `init` と `new` がそのまま出す。"""


@dataclass
class Suite:
    """宣言の suite の写し。`scope_command` を持たない suite は範囲テストに使わない。`kind` は `test` か `lint`。
    `ci_jobs` はこの suite が受け持つ継続的統合のジョブの識別子（#464）。"""

    name: str
    command: str
    scope_command: Optional[str] = None
    junit: Optional[str] = None
    paths: list[str] = field(default_factory=list)
    kind: str = TEST
    ci_jobs: list[str] = field(default_factory=list)

    def covers(self, path: str) -> bool:
        """`path`（`::` 付きの対象も可）をこの suite が受け持つか。glob の要素は `fnmatchcase`、ほかは接頭辞の一致。"""
        return _match_len(self, path) is not None

    def as_state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "scope_command": self.scope_command,
            "junit": self.junit,
            "paths": list(self.paths),
            "kind": self.kind,
            "ci_jobs": list(self.ci_jobs),
        }


def _match_len(suite: Suite, path: str) -> Optional[int]:
    """`suite.paths` のうち `path` に当たる要素の長さ（最長）。当たらなければ `None`。"""
    target = str(path).split("::", 1)[0]
    best: Optional[int] = None
    for root in suite.paths or ["."]:
        r = str(root)
        if any(c in r for c in _GLOB_CHARS):
            name = target.rsplit("/", 1)[-1]
            hit = fnmatch.fnmatchcase(target, r) or ("/" not in r and fnmatch.fnmatchcase(name, r))
        else:
            r = r.rstrip("/")
            hit = r in (".", "") or target == r or target.startswith(r + "/")
            r = "" if r == "." else r
        if hit and (best is None or len(r) > best):
            best = len(r)
    return best


@dataclass
class ScopeRun:
    """範囲テストの 1 本（どの suite の、どの種別の、どのコマンドか）。状態ファイルの `items[].scope_commands` の 1 要素。"""

    suite: str
    kind: str
    command: str

    def as_state(self) -> dict[str, str]:
        return {"suite": self.suite, "kind": self.kind, "command": self.command}

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> "ScopeRun":
        return cls(str(data.get("suite") or ""), str(data.get("kind") or TEST), str(data.get("command") or ""))


@dataclass
class Outcome:
    """1 本のコマンドの結果（`outcome`）。`status` は `passed` / `failed` / `launch_failed` / `timed_out`。"""

    status: str
    code: Optional[int]
    reason: str

    @property
    def launch_failed(self) -> bool:
        return self.status == LAUNCH_FAILED


def outcome(code: Optional[int], timed_out: bool) -> Outcome:
    """終了コードと打ち切りから結果を決める唯一の関数（I7）。起動の例外は実行器が 127 に置き換えて渡す（決定 6）。"""
    if timed_out:
        return Outcome(TIMED_OUT, code, "上限で打ち切った")
    if code == 0:
        return Outcome(PASSED, 0, "")
    if code == 127:
        return Outcome(LAUNCH_FAILED, 127, "終了コード 127（コマンドが見つからないか、起動できない）")
    if code == 126:
        return Outcome(LAUNCH_FAILED, 126, "終了コード 126（実行できない）")
    return Outcome(FAILED, code, f"終了コード {code}")


@dataclass
class Strategy:
    """解いた戦略。`source` は根拠（`test.strategy` / `derived:test.suites` / `derived:test_duration` / `args`）。

    `round_command` は引数（`--round-test` など）のラウンドテストか、旧形の状態ファイルの ` && ` でつないだもの。
    宣言から導いた `round-only` では `None` で、ラウンドテストは suite ごとの `command` から組む（I6）。
    `round_kind` は `round_command` の種別。
    """

    name: str
    source: str
    suites: list[Suite] = field(default_factory=list)
    round_command: Optional[str] = None
    ci: Optional[dict[str, Any]] = None
    notes: list[str] = field(default_factory=list)
    round_kind: str = TEST

    @property
    def whole_on_ci(self) -> bool:
        return self.name == LOCAL_SCOPED_CI_WHOLE

    def scoped_suites(self, kind: Optional[str] = None) -> list[Suite]:
        return [s for s in self.suites if s.scope_command and (kind is None or s.kind == kind)]

    def has_kind(self, kind: str) -> bool:
        """その種別の suite（か、その種別のラウンドテスト）があるか。"""
        return any(s.kind == kind for s in self.suites) or bool(self.round_command and self.round_kind == kind)

    def whole_commands(self, kind: Optional[str] = None) -> list[str]:
        """手元で走らせる全体テストのコマンド（シェルで 1 つずつ走らせる文字列）。`kind` が `None` なら両方の種別。

        suite に `command` が 1 つも無ければラウンドテストが全体を兼ねる（今と同じ）。
        """
        commands = [s.command for s in self.suites if s.command and (kind is None or s.kind == kind)]
        if commands or any(s.command for s in self.suites):
            return commands
        if self.round_command and (kind is None or kind == self.round_kind):
            return [self.round_command]
        return []

    def round_commands(self) -> list[str]:
        """ラウンドテストのコマンドの並び。引数（か旧形の状態）なら `[round_command]`、宣言からなら suite ごとに 1 つ（I6）。"""
        if self.round_command:
            return [self.round_command]
        return [s.command for s in self.suites if s.command]

    def round_runs(self) -> list[ScopeRun]:
        """ラウンドテストを種別つきで並べる。引数由来の `round_command` は `round_kind`、宣言からなら suite ごとの `kind`。"""
        if self.round_command:
            return [ScopeRun("round", self.round_kind, self.round_command)]
        return [ScopeRun(s.name, s.kind, s.command) for s in self.suites if s.command]

    def as_state(self) -> dict[str, Any]:
        """状態ファイルとプランへ写す形。以後は変えない（集約の不変条件）。"""
        out = {
            "name": self.name,
            "source": self.source,
            "suites": [s.as_state() for s in self.suites],
            "round_command": self.round_command,
            "ci": dict(self.ci) if self.ci else None,
            "notes": list(self.notes),
        }
        if self.round_command:
            out["round_kind"] = self.round_kind
        return out

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> "Strategy":
        """状態ファイルの戦略。`kind` を持たない suite（旧形）はテストとして読む（I3）。`ci_jobs` が無ければ空。"""
        suites = [
            Suite(
                **{k: s.get(k) for k in ("name", "command", "scope_command", "junit")},
                paths=list(s.get("paths") or []),
                kind=str(s.get("kind") or TEST),
                ci_jobs=list(s.get("ci_jobs") or []),
            )
            for s in data.get("suites") or []
        ]
        return cls(
            str(data.get("name")),
            str(data.get("source") or ""),
            suites,
            data.get("round_command"),
            data.get("ci"),
            list(data.get("notes") or []),
            str(data.get("round_kind") or TEST),
        )


# ---------- 雛形 ----------


def _tokens(text: str) -> list[str]:
    """シェルの字句（括弧・`&&`・`;` を別の字句にし、引用を字句に残す）。`{paths}` は 1 字句になる。"""
    lexer = shlex.shlex(text, posix=False, punctuation_chars=True)
    lexer.wordchars += "{}$"
    return list(lexer)


def template_problem(template: str, key: str) -> Optional[str]:
    """範囲テストの雛形の不備。`{paths}` が無い・引用の外の 1 字句として立っていない・字句に分けられないなら理由、なければ `None`。"""
    text = str(template or "")
    if PATHS not in text:
        return f"{key} に {PATHS} が無い。範囲テストの雛形は {PATHS} を 1 語で含める（今: {text}）"
    try:
        words = _tokens(text)
    except ValueError:
        return f"{key} を語に分けられない（今: {text}）"
    if PATHS not in words or any(PATHS in w and w != PATHS for w in words):
        return f"{key} の {PATHS} は引用の外に、空白で区切った 1 語で書く（今: {text}）"
    return None


def has_paths(command: Optional[str]) -> bool:
    return bool(command) and PATHS in str(command)


def fill(template: str, paths: list[str]) -> str:
    """雛形の `{paths}` の字句を、シェルの引用で守った対象の並び（`shlex.join`）へ置き換えた文字列（I2・AC22）。"""
    return str(template).replace(PATHS, shlex.join([str(p) for p in paths]))


def whole_command_of(template: str) -> str:
    """テストの種別の雛形から全体テストのコマンド（`{paths}` を `.` にした文字列）を作る。静的解析には使わない（I4）。"""
    return str(template).replace(PATHS, ".")


def suite_for(strategy: Strategy, path: str, kind: str = TEST) -> Optional[Suite]:
    """パスを受け持つ `kind` の suite（`paths` の最長の一致）。`scope_command` を持つ suite だけを見る。"""
    best, best_len = None, -1
    for suite in strategy.scoped_suites(kind):
        n = _match_len(suite, path)
        if n is not None and n > best_len:
            best, best_len = suite, n
    return best


def covers_whole(strategy: Strategy, locations: list[str], kind: str = TEST) -> bool:
    """テストの置き場所（`locations`）が、`kind` の suite の `paths` の全要素を覆うか（#1555 の前提 3）。

    要素は置き場所のどれかが同じか祖先なら覆う。glob の要素と `.` は置き場所 `.` だけが覆う。`kind` の suite が
    無いか、置き場所が無ければ覆わない。"""
    suites = [s for s in strategy.suites if s.kind == kind]
    locs = [posixpath.normpath(str(p).split("::", 1)[0]) for p in locations or [] if str(p).strip()]
    if not suites or not locs:
        return False
    whole = "." in locs
    for suite in suites:
        for root in suite.paths or ["."]:
            r = posixpath.normpath(str(root)) if str(root).strip() else "."
            if whole:
                continue
            if r == "." or any(c in r for c in _GLOB_CHARS):
                return False
            if not any(r == loc or r.startswith(loc + "/") for loc in locs):
                return False
    return True


def suite_groups(strategy: Strategy, targets: list[str]) -> list[tuple[Suite, list[str]]]:
    """テストの対象を受け持つテストの suite ごとに分ける。受け持つ suite の無いものは最初のテストの suite へ。

    雛形の `{paths}` の約束はパスなので、対象は `::` より前のパスへ直し、重なりを 1 つにまとめ、最初に現れた順を
    保つ（#1793 の R5）。`::` を受け付けるかはランナーごとに違い、宣言の `runner` からは見分けられない。"""
    groups: dict[str, tuple[Suite, list[str]]] = {}
    scoped = strategy.scoped_suites(TEST)
    for path in dict.fromkeys(target_path(t) for t in targets):
        if not path:
            continue
        suite = suite_for(strategy, path, TEST) or (scoped[0] if scoped else None)
        if suite is None:
            continue
        groups.setdefault(suite.name, (suite, []))[1].append(path)
    return list(groups.values())


def target_path(target: object) -> str:
    """テストの対象（`tests/x.py::Check` の形も可）の `::` より前のパス。"""
    return str(target).split("::", 1)[0].strip()


def scope_runs(strategy: Strategy, targets: list[str], changed: list[str]) -> list[ScopeRun]:
    """範囲テストの並び。テストの suite には対象（`targets`）を、静的解析の suite には変更したファイル（`changed`）の
    うち `covers` に当たるものを入れる（I10）。入れるものが無い suite は組まない。"""
    runs = [ScopeRun(s.name, TEST, fill(str(s.scope_command), paths)) for s, paths in suite_groups(strategy, list(targets))]
    for suite in strategy.scoped_suites(LINT):
        mine = [str(f) for f in changed if suite.covers(str(f))]
        if mine:
            runs.append(ScopeRun(suite.name, LINT, fill(str(suite.scope_command), mine)))
    return runs


# ---------- 宣言の読み取り ----------


def _test_of(decl: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """宣言の `test`。無ければ `(None, None)`、不明なら `(None, 理由)`。"""
    test = (decl or {}).get("test")
    if not isinstance(test, dict):
        return None, None
    if "unknown" in test:
        return None, str(test.get("unknown") or "")
    return test, None


def _suites_of(test: dict[str, Any]) -> list[Suite]:
    out = []
    for s in test.get("suites") or []:
        if not isinstance(s, dict):
            continue
        out.append(
            Suite(
                name=str(s.get("name") or "suite"),
                command=str(s.get("command") or ""),
                scope_command=str(s["scope_command"]) if s.get("scope_command") else None,
                junit=str(s["junit"]) if s.get("junit") else None,
                paths=[str(p) for p in s.get("paths") or []],
                kind=str(s.get("kind") or TEST),
                ci_jobs=[str(j) for j in s.get("ci_jobs") or []],
            )
        )
    return out


def whole_seconds(decl: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """宣言の全体テストの所要 w と出所。`ndf-record` → `ci-junit` → `ci-steps` の順に採る（決定 2）。"""
    td = (decl or {}).get("test_duration")
    if not isinstance(td, dict) or "unknown" in td:
        return None, None
    rows = [r for r in td.get("measured") or [] if isinstance(r, dict) and isinstance(r.get("seconds"), (int, float))]
    for source in DURATION_SOURCES:
        for row in rows:
            if row.get("source") == source:
                return float(row["seconds"]), source
    return None, None


def ci_of(decl: dict[str, Any]) -> Optional[dict[str, Any]]:
    """宣言の `ci`（読めなければ `None`）。"""
    ci = (decl or {}).get("ci")
    if not isinstance(ci, dict) or "unknown" in ci or not ci.get("workflows"):
        return None
    return ci


def ci_wall_seconds(decl: dict[str, Any], check: Optional[str]) -> Optional[float]:
    """CI の壁時計 c。`check` の名前を持つワークフローの値、分からなければ最大の値。どれも無ければ `None`。"""
    ci = ci_of(decl)
    if not ci:
        return None
    walls = [
        (str(w.get("path") or ""), float(w["wall_seconds"])) for w in ci["workflows"] if isinstance(w.get("wall_seconds"), (int, float))
    ]
    if not walls:
        return None
    if check:
        for path, wall in walls:
            if check in path:
                return wall
    return max(w for _, w in walls)


def _ci_target(test: dict[str, Any], decl: dict[str, Any], ci_check: Optional[str]) -> dict[str, Any]:
    """CI で見るチェックと成果物の名前（決定 6）。引数の `--ci-check` が宣言より先に効く。"""
    ci = test.get("ci") if isinstance(test.get("ci"), dict) else {}
    check = ci_check or ci.get("check") or None
    required = list((ci_of(decl) or {}).get("required_checks") or [])
    return {"check": check, "checks": [check] if check else required, "junit_artifacts": ci.get("junit_artifacts") or None}


def propose(decl: dict[str, Any]) -> tuple[Optional[str], str]:
    """宣言だけから戦略を導く（解析の答えの既定と、`test.strategy` が無い宣言の経路）。戻りは（戦略, 根拠）。

    `scope_command` の有無は、テストの suite があればテストの suite だけで、無ければ静的解析の suite で見る（決定 5）。
    """
    test, reason = _test_of(decl)
    if test is None:
        return None, f"unknown:{reason}" if reason is not None else "missing"
    if test.get("strategy") in STRATEGIES:
        return str(test["strategy"]), "test.strategy"
    suites = _suites_of(test)
    basis = [s for s in suites if s.kind == TEST] or suites
    if not any(s.scope_command for s in basis):
        return ROUND_ONLY, "derived:test.suites"
    w, _ = whole_seconds(decl)
    if w is not None and w > CI_WHOLE_THRESHOLD_SECONDS and ci_of(decl) is not None:
        return LOCAL_SCOPED_CI_WHOLE, "derived:test_duration"
    return LOCAL_FULL, "derived:test_duration"


def _check_kind(kind: str, key: str) -> None:
    if kind not in KINDS:
        raise StrategyError(f"{key} は {' か '.join(KINDS)} で書く（今: {kind}）")


def _check_templates(suites: list[Suite]) -> None:
    for i, suite in enumerate(suites):
        _check_kind(suite.kind, f"test.suites[{i}].kind")
        if suite.scope_command:
            problem = template_problem(suite.scope_command, f"test.suites[{i}].scope_command")
            if problem:
                raise StrategyError(problem)


def _args_suites(template: str, kind: str, declared: list[Suite], scope_paths: Optional[list[str]], notes: list[str]) -> list[Suite]:
    """引数の雛形（`{paths}` を含む）を種別 `kind` の範囲テストの雛形にした suite の並び（I11）。

    宣言の同じ種別の suite は `scope_command` を外して残し、全体テストは宣言の `command` が先に効く。宣言に
    `command` が無ければ、テストは `{paths}` を `.` にしたもの（その旨を
    `WHOLE_FROM_TEMPLATE` の注記で残す）、静的解析は `{paths}` を範囲のパスで埋めたもの（I4）。
    宣言のほかの種別の suite はそのまま残す。
    """
    same = [Suite(s.name, s.command, None, s.junit, list(s.paths), s.kind) for s in declared if s.kind == kind]
    others = [s for s in declared if s.kind != kind]
    paths = [str(p) for p in scope_paths or [] if str(p)]
    if any(s.command for s in same):
        whole = ""
    elif kind == TEST:
        whole = whole_command_of(template)
        notes.append(WHOLE_FROM_TEMPLATE)
    elif paths:
        whole = fill(template, paths)
    else:
        whole = ""
        notes.append(NO_LINT_WHOLE)
    arg = Suite("args", whole, template, paths=(paths or ["."]) if kind == LINT else ["."], kind=kind)
    return [*same, arg, *others]


def _round_only_from_args(
    baseline_test: Optional[str],
    round_test: str,
    kind: str,
    declared: list[Suite],
    others: list[Suite],
    scope_paths: Optional[list[str]],
) -> Strategy:
    """`--round-test` に `{paths}` が無い経路（そのまま走らせる）。"""
    notes: list[str] = []
    # 全体テストは `--baseline-test`（`{paths}` を含めば種別の規則で組む）→ 宣言の suite の `command`。
    # どちらも無ければラウンドテストが全体を兼ねる。
    if baseline_test and has_paths(baseline_test):
        suites = _args_suites(baseline_test, kind, declared, scope_paths, notes)
    elif baseline_test:
        suites = [Suite("args", str(baseline_test), None, paths=["."], kind=kind), *others]
    else:
        suites = [s for s in declared if s.command]
    notes.insert(0, "--round-test をそのまま走らせる")
    return Strategy(ROUND_ONLY, "args", suites, round_command=str(round_test), notes=notes, round_kind=kind)


def _template_from_args(
    decl: dict[str, Any],
    test: Optional[dict[str, Any]],
    template: str,
    flag: str,
    declared_name: str,
    ci_check: Optional[str],
    kind: str,
    declared: list[Suite],
    scope_paths: Optional[list[str]],
) -> Strategy:
    """雛形（`--round-test` か `--baseline-test` が `{paths}` を含む）の経路。"""
    problem = template_problem(template, flag)
    if problem:
        raise StrategyError(problem)
    notes: list[str] = []
    name = declared_name if declared_name in STRATEGIES and declared_name != ROUND_ONLY else LOCAL_FULL
    suites = _args_suites(template, kind, declared, scope_paths, notes)
    ci = _ci_target(test or {}, decl, ci_check) if name == LOCAL_SCOPED_CI_WHOLE else None
    return Strategy(name, "args", suites, ci=ci, notes=notes)


def _baseline_only_from_args(baseline_test: str, kind: str, others: list[Suite]) -> Strategy:
    """`--baseline-test` だけ（`{paths}` 無し）の経路。"""
    return Strategy(
        ROUND_ONLY,
        "args",
        others,
        round_command=str(baseline_test),
        notes=["--baseline-test に {paths} が無いため、項目ごとに全体テストを走らせる"],
        round_kind=kind,
    )


def _from_args(
    decl: dict[str, Any],
    baseline_test: Optional[str],
    round_test: Optional[str],
    ci_check: Optional[str],
    kind: str,
    scope_paths: Optional[list[str]],
) -> Optional[Strategy]:
    """引数からの解き方（決定 2 の表の上 3 行）。引数が無ければ `None`。雛形の種別は `kind`（I1）。"""
    test, _ = _test_of(decl)
    declared_name = str((test or {}).get("strategy") or "") if test else ""
    declared = _suites_of(test or {})
    for i, suite in enumerate(declared):
        _check_kind(suite.kind, f"test.suites[{i}].kind")
    others = [s for s in declared if s.kind != kind]
    _check_templates(others)
    if round_test and not has_paths(round_test):
        return _round_only_from_args(baseline_test, round_test, kind, declared, others, scope_paths)
    if has_paths(round_test):
        return _template_from_args(decl, test, str(round_test), "--round-test", declared_name, ci_check, kind, declared, scope_paths)
    if has_paths(baseline_test):
        return _template_from_args(decl, test, str(baseline_test), "--baseline-test", declared_name, ci_check, kind, declared, scope_paths)
    if baseline_test:
        return _baseline_only_from_args(baseline_test, kind, others)
    return None


def resolve(
    decl: dict[str, Any],
    *,
    baseline_test: Optional[str] = None,
    round_test: Optional[str] = None,
    ci_check: Optional[str] = None,
    template_kind: str = TEST,
    scope_paths: Optional[list[str]] = None,
) -> Strategy:
    """宣言と引数から戦略を解く（決定 2 の表を上から当てる）。解けなければ `StrategyError`（I3）。

    `template_kind` は引数の雛形（`--baseline-test` / `--round-test` / `--test-cmd`）の種別、`scope_paths` は
    静的解析の雛形の全体テストで `{paths}` に入れる範囲のパス（cross-refactoring の `--scope`・supervise の `--tests`）。
    """
    _check_kind(str(template_kind), "--test-kind")
    from_args = _from_args(decl, baseline_test, round_test, ci_check, str(template_kind), scope_paths)
    if from_args is not None:
        return from_args
    test, reason = _test_of(decl)
    if test is None:
        raise StrategyError(MISSING_TEST.format(reason=reason if reason is not None else "test のキーが無い"))
    suites = _suites_of(test)
    _check_templates(suites)
    name, source = propose(decl)
    if name == ROUND_ONLY:
        if not any(s.command for s in suites):
            raise StrategyError("test.suites に command を持つ suite が無い")
        return Strategy(ROUND_ONLY, source, suites)
    if name == LOCAL_SCOPED_CI_WHOLE and not any(s.scope_command for s in suites):
        raise StrategyError("local-scoped-ci-whole には scope_command を持つ suite が要る")
    ci = _ci_target(test, decl, ci_check) if name == LOCAL_SCOPED_CI_WHOLE else None
    return Strategy(str(name), source, suites, ci=ci)


def verify_commands(decl: dict[str, Any]) -> Optional[list[str]]:
    """cross-review の最終スイープに名指しする全体テスト（両方の種別。I13）。宣言の `test` が無い・不明なら `None`。

    `test` があって解けない（不正な `kind` など）ときは `StrategyError` をそのまま上げる。宣言が無いときと
    分けないと、不正な宣言が最終スイープの既定の探し方へ黙って流れる。
    """
    test, _ = _test_of(decl)
    if test is None:
        return None
    return resolve(decl).whole_commands() or None


def decl_of(root, supervise_decl: Optional[dict[str, Any]] = None) -> tuple[dict[str, Any], Optional[str]]:
    """`.ndf/project.json` の宣言。`test` が無く `supervise.json` の `test.command` があれば 1 つの suite として読み、
    読んだことを 2 つ目で知らせる（決定 3。移行性）。"""
    import project_decl

    decl = dict(project_decl.read_project_decl(root))
    test, _ = _test_of(decl)
    if test is not None:
        return decl, None
    sv = (supervise_decl or {}).get("test") if isinstance((supervise_decl or {}).get("test"), dict) else None
    command = str((sv or {}).get("command") or "")
    if not command:
        return decl, None
    template = command if has_paths(command) else f"{command} {PATHS}"
    decl["test"] = {
        "suites": [
            {"name": "supervise", "runner": "supervise", "command": whole_command_of(template), "scope_command": template, "paths": ["."]}
        ]
    }
    return decl, ".ndf/supervise.json の test.command を読んだ（.ndf/project.json に test が無い）。宣言は project.json へ移す"


# ---------- 時間の上限 ----------


def _ceil(value: float) -> int:
    return int(math.ceil(value))


def limits(
    strategy: Strategy,
    budget_minutes: Optional[int],
    *,
    whole_seconds_value: Optional[float] = None,
    whole_source: Optional[str] = None,
    ci_seconds: Optional[float] = None,
    measured_seconds: Optional[float] = None,
    scope_seconds: Optional[float] = None,
    scope_source: Optional[str] = None,
) -> dict[str, Any]:
    """時間の上限（秒）の表（決定 8）。`basis` に入力を並べる。

    `scope_seconds` は CI に任せる戦略で着手前に走らせる範囲テストの所要の見込み s（`scope_source` はその出所。
    `history` / `whole`）。分かれば着手前の上限を `max(0.10·B, 3·s)` にする（#1555）。ほかの戦略と予算なしでは使わない。

    予算が無い（supervise）ときは `3·w` と `3·c` を上限にし、所要も無ければ `UNKNOWN_DURATION_LIMITS` を使って
    `basis.unknown_duration` を真にする。
    """
    w, c, x = whole_seconds_value, ci_seconds, measured_seconds
    basis = {
        "budget_minutes": budget_minutes,
        "w": w,
        "w_source": whole_source,
        "c": c,
        "x": x,
        "strategy": strategy.name,
        "unknown_duration": False,
    }
    if budget_minutes is None:
        if w is None and x is None:
            basis["unknown_duration"] = True
            test_timeout = UNKNOWN_DURATION_LIMITS["test_timeout"]
            whole_timeout = UNKNOWN_DURATION_LIMITS["whole_timeout"]
        else:
            test_timeout = _ceil(TEST_FACTOR * float(x if x is not None else w))
            whole_timeout = _ceil(TEST_FACTOR * float(w if w is not None else x))
        ci_wait = _ceil(TEST_FACTOR * c) if c is not None else UNKNOWN_DURATION_LIMITS["ci_wait_timeout"]
        return {
            "init_test_timeout": whole_timeout,
            "test_timeout": test_timeout,
            "whole_timeout": whole_timeout,
            "ci_wait_timeout": ci_wait,
            "basis": basis,
        }
    seconds = float(budget_minutes) * 60
    # 範囲テストの所要は予算を持つ呼び出し（cross-refactoring）だけが渡す。supervise の表の形は変えない（AC12）。
    basis["scope_seconds"] = scope_seconds
    basis["scope_source"] = scope_source if scope_seconds is not None else None
    if strategy.whole_on_ci or w is None:
        base_timeout = _ceil(seconds * INIT_TEST_SHARE)
    else:
        base_timeout = _ceil(max(seconds * INIT_TEST_SHARE, TEST_FACTOR * w))
    init_timeout = base_timeout
    if strategy.whole_on_ci and scope_seconds is not None:
        init_timeout = _ceil(max(seconds * INIT_TEST_SHARE, TEST_FACTOR * float(scope_seconds)))
    if x is None:
        test_timeout = base_timeout
    else:
        test_timeout = _ceil(max(TEST_FACTOR * float(x), seconds * TEST_FLOOR_SHARE))
    whole_timeout = _ceil(max(TEST_FACTOR * float(w), seconds * TEST_FLOOR_SHARE)) if w is not None else base_timeout
    ci_wait = _ceil(max(TEST_FACTOR * c, seconds * CI_WAIT_SHARE)) if c is not None else _ceil(seconds * CI_WAIT_UNKNOWN_SHARE)
    return {
        "init_test_timeout": init_timeout,
        "test_timeout": test_timeout,
        "whole_timeout": whole_timeout,
        "ci_wait_timeout": ci_wait,
        "basis": basis,
    }


def reserve_seconds(strategy: Strategy, whole: Optional[float], ci: Optional[float], ci_gate: bool) -> tuple[float, float]:
    """バッファに入れる全体テストの秒（危険フラグ, 最終ゲート）。CI に任せる戦略は危険フラグ 0・最終ゲート c（決定 8）。"""
    w = float(whole or 0.0)
    c = float(ci or 0.0)
    if strategy.whole_on_ci:
        return 0.0, c
    return w, (c if ci_gate else w)
