"""テストの戦略（#1334）。宣言（`.ndf/project.json` の `test`）と引数から、範囲テストの走らせ方・全体テストの
置き場（手元か CI か）・時間の上限を決める。cross-refactoring と supervise が同じ関数を使う（I10）。

**純粋な処理だけを置く。** ファイルもプロセスも触らない（宣言の読み取りは `decl_of` だけで、`project_decl` を通す）。
戦略はコマンドの語・実行器の名前・前置きを見ずに決まる（I1）。コマンドへの加工は `{paths}` の 1 語を対象の語の並びへ
置き換えることだけである（I2・決定 14）。解けないときは `StrategyError` を上げ、終了コードは呼ぶ側が決める。
標準ライブラリだけで書く。
"""

from __future__ import annotations

import math
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

LOCAL_FULL = "local-full"
LOCAL_SCOPED_CI_WHOLE = "local-scoped-ci-whole"
ROUND_ONLY = "round-only"
STRATEGIES = (LOCAL_FULL, LOCAL_SCOPED_CI_WHOLE, ROUND_ONLY)

PATHS = "{paths}"
# 所要の出所を採る順（決定 2）。手元の実測 → CI の JUnit の直列の合計 → CI のテストの step の合計。
DURATION_SOURCES = ("ndf-record", "ci-junit", "ci-steps")
# 所要がこれを超え、宣言の CI が読めれば、全体テストを CI に任せる（決定 2。既定の予算 30 分の 1/3）。
CI_WHOLE_THRESHOLD_SECONDS = 600.0

# 時間の係数（決定 8）。B は予算（分）、w は全体テストの所要、x は着手前に手元で走らせたテストの実測、c は CI の壁時計。
INIT_TEST_SHARE = 0.10  # 着手前のテスト 1 回の上限 = max(0.10·B, 3·w)（CI に任せる戦略は 0.10·B）
TEST_FACTOR = 3.0  # テスト 1 回の上限 = max(3·x, 0.01·B)
TEST_FLOOR_SHARE = 0.01
CI_WAIT_SHARE = 0.05  # CI の待ちの上限 = max(3·c, 0.05·B)
CI_WAIT_UNKNOWN_SHARE = 0.20  # c が分からなければ 0.20·B
# 所要が宣言に無く予算も無い（supervise の計画）ときだけ使う値。計画に「所要が不明のときの値」と書く。
UNKNOWN_DURATION_LIMITS = {"test_timeout": 900, "whole_timeout": 1800, "ci_wait_timeout": 3600}

MISSING_TEST = ".ndf/project.json の test が無い（か不明: {reason}）。/ndf:development-workflow の手順 0 で解析するか、--round-test を渡す"


class StrategyError(Exception):
    """戦略を解けない。文は `init` と `new` がそのまま出す。"""


@dataclass
class Suite:
    """宣言の suite の写し。`scope_command` を持たない suite は範囲テストに使わない。"""

    name: str
    command: str
    scope_command: Optional[str] = None
    junit: Optional[str] = None
    paths: list[str] = field(default_factory=list)

    def as_state(self) -> dict[str, Any]:
        return {"name": self.name, "command": self.command, "scope_command": self.scope_command, "junit": self.junit, "paths": list(self.paths)}


@dataclass
class Strategy:
    """解いた戦略。`source` は根拠（`test.strategy` / `derived:test.suites` / `derived:test_duration` / `args`）。"""

    name: str
    source: str
    suites: list[Suite] = field(default_factory=list)
    round_command: Optional[str] = None
    ci: Optional[dict[str, Any]] = None
    notes: list[str] = field(default_factory=list)

    @property
    def whole_on_ci(self) -> bool:
        return self.name == LOCAL_SCOPED_CI_WHOLE

    def scoped_suites(self) -> list[Suite]:
        return [s for s in self.suites if s.scope_command]

    def whole_commands(self) -> list[str]:
        """手元で走らせる全体テストのコマンド（シェルで走らせる文字列）。suite が無い `round-only` はラウンドテストそのもの。"""
        commands = [s.command for s in self.suites if s.command]
        if commands:
            return commands
        return [self.round_command] if self.round_command else []

    def as_state(self) -> dict[str, Any]:
        """状態ファイルとプランへ写す形。以後は変えない（集約の不変条件）。"""
        return {
            "name": self.name,
            "source": self.source,
            "suites": [s.as_state() for s in self.suites],
            "round_command": self.round_command,
            "ci": dict(self.ci) if self.ci else None,
            "notes": list(self.notes),
        }

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> "Strategy":
        suites = [Suite(**{k: s.get(k) for k in ("name", "command", "scope_command", "junit")}, paths=list(s.get("paths") or [])) for s in data.get("suites") or []]
        return cls(str(data.get("name")), str(data.get("source") or ""), suites, data.get("round_command"), data.get("ci"), list(data.get("notes") or []))


# ---------- 雛形 ----------


def template_problem(template: str, key: str) -> Optional[str]:
    """範囲テストの雛形の不備。`{paths}` が無い・1 語として立っていない・語に分けられないなら理由、なければ `None`。"""
    text = str(template or "")
    if PATHS not in text:
        return f"{key} に {PATHS} が無い。範囲テストの雛形は {PATHS} を 1 語で含める（今: {text}）"
    try:
        words = shlex.split(text)
    except ValueError:
        return f"{key} を語に分けられない（今: {text}）"
    if PATHS not in words:
        return f"{key} の {PATHS} は空白で区切った 1 語で書く（今: {text}）"
    return None


def has_paths(command: Optional[str]) -> bool:
    return bool(command) and PATHS in str(command)


def scope_words(template: str, paths: list[str]) -> list[str]:
    """雛形の `{paths}` の語を対象の語の並びへ置き換えた語の並び（`shell=False` で走らせる）。"""
    words = shlex.split(str(template))
    out: list[str] = []
    for word in words:
        if word == PATHS:
            out.extend(str(p) for p in paths)
        else:
            out.append(word)
    return out


def whole_command_of(template: str) -> str:
    """雛形から全体テストのコマンド（`{paths}` を `.` にした文字列）を作る。"""
    return str(template).replace(PATHS, ".")


def suite_for(strategy: Strategy, path: str) -> Optional[Suite]:
    """パスを受け持つ suite（`paths` の最長の一致）。`scope_command` を持つ suite だけを見る。"""
    best, best_len = None, -1
    for suite in strategy.scoped_suites():
        for root in suite.paths or ["."]:
            r = str(root).rstrip("/")
            if r in (".", "") or path == r or path.startswith(r + "/"):
                if len(r) > best_len:
                    best, best_len = suite, len(r)
    return best


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
    walls = [(str(w.get("path") or ""), float(w["wall_seconds"])) for w in ci["workflows"] if isinstance(w.get("wall_seconds"), (int, float))]
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
    """宣言だけから戦略を導く（解析の答えの既定と、`test.strategy` が無い宣言の経路）。戻りは（戦略, 根拠）。"""
    test, reason = _test_of(decl)
    if test is None:
        return None, f"unknown:{reason}" if reason is not None else "missing"
    if test.get("strategy") in STRATEGIES:
        return str(test["strategy"]), "test.strategy"
    if not any(s.scope_command for s in _suites_of(test)):
        return ROUND_ONLY, "derived:test.suites"
    w, _ = whole_seconds(decl)
    if w is not None and w > CI_WHOLE_THRESHOLD_SECONDS and ci_of(decl) is not None:
        return LOCAL_SCOPED_CI_WHOLE, "derived:test_duration"
    return LOCAL_FULL, "derived:test_duration"


def _check_templates(suites: list[Suite]) -> None:
    for i, suite in enumerate(suites):
        if suite.scope_command:
            problem = template_problem(suite.scope_command, f"test.suites[{i}].scope_command")
            if problem:
                raise StrategyError(problem)


def _from_args(decl: dict[str, Any], baseline_test: Optional[str], round_test: Optional[str], ci_check: Optional[str]) -> Optional[Strategy]:
    """引数からの解き方（決定 2 の表の上 3 行）。引数が無ければ `None`。"""
    test, _ = _test_of(decl)
    declared = str((test or {}).get("strategy") or "") if test else ""
    if round_test and not has_paths(round_test):
        # 全体テストは `--baseline-test`（`{paths}` を含めば `.` にしたもの）→ 宣言の suite の `command`。
        # どちらも無ければラウンドテストが全体を兼ねる。
        if baseline_test:
            suites = [Suite("args", whole_command_of(baseline_test), baseline_test if has_paths(baseline_test) else None, paths=["."])]
        else:
            suites = [s for s in _suites_of(test or {}) if s.command]
        return Strategy(ROUND_ONLY, "args", suites, round_command=str(round_test), notes=["--round-test をそのまま走らせる"])
    template = round_test if has_paths(round_test) else (baseline_test if has_paths(baseline_test) else None)
    if template:
        problem = template_problem(template, "--round-test" if has_paths(round_test) else "--baseline-test")
        if problem:
            raise StrategyError(problem)
        name = declared if declared in STRATEGIES and declared != ROUND_ONLY else LOCAL_FULL
        suite = Suite("args", whole_command_of(template), template, paths=["."])
        ci = _ci_target(test or {}, decl, ci_check) if name == LOCAL_SCOPED_CI_WHOLE else None
        return Strategy(name, "args", [suite], ci=ci)
    if baseline_test:
        return Strategy(
            ROUND_ONLY,
            "args",
            round_command=str(baseline_test),
            notes=["--baseline-test に {paths} が無いため、項目ごとに全体テストを走らせる"],
        )
    return None


def resolve(
    decl: dict[str, Any],
    *,
    baseline_test: Optional[str] = None,
    round_test: Optional[str] = None,
    ci_check: Optional[str] = None,
) -> Strategy:
    """宣言と引数から戦略を解く（決定 2 の表を上から当てる）。解けなければ `StrategyError`（I3）。"""
    from_args = _from_args(decl, baseline_test, round_test, ci_check)
    if from_args is not None:
        return from_args
    test, reason = _test_of(decl)
    if test is None:
        raise StrategyError(MISSING_TEST.format(reason=reason if reason is not None else "test のキーが無い"))
    suites = _suites_of(test)
    _check_templates(suites)
    name, source = propose(decl)
    if name == ROUND_ONLY:
        commands = [s.command for s in suites if s.command]
        if not commands:
            raise StrategyError("test.suites に command を持つ suite が無い")
        return Strategy(ROUND_ONLY, source, suites, round_command=" && ".join(commands))
    if name == LOCAL_SCOPED_CI_WHOLE and not any(s.scope_command for s in suites):
        raise StrategyError("local-scoped-ci-whole には scope_command を持つ suite が要る")
    ci = _ci_target(test, decl, ci_check) if name == LOCAL_SCOPED_CI_WHOLE else None
    return Strategy(str(name), source, suites, ci=ci)


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
    decl["test"] = {"suites": [{"name": "supervise", "runner": "supervise", "command": whole_command_of(template), "scope_command": template, "paths": ["."]}]}
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
) -> dict[str, Any]:
    """時間の上限（秒）の表（決定 8）。`basis` に入力を並べる。

    予算が無い（supervise）ときは `3·w` と `3·c` を上限にし、所要も無ければ `UNKNOWN_DURATION_LIMITS` を使って
    `basis.unknown_duration` を真にする。
    """
    w, c, x = whole_seconds_value, ci_seconds, measured_seconds
    basis = {"budget_minutes": budget_minutes, "w": w, "w_source": whole_source, "c": c, "x": x, "strategy": strategy.name, "unknown_duration": False}
    if budget_minutes is None:
        if w is None and x is None:
            basis["unknown_duration"] = True
            test_timeout = UNKNOWN_DURATION_LIMITS["test_timeout"]
            whole_timeout = UNKNOWN_DURATION_LIMITS["whole_timeout"]
        else:
            test_timeout = _ceil(TEST_FACTOR * float(x if x is not None else w))
            whole_timeout = _ceil(TEST_FACTOR * float(w if w is not None else x))
        ci_wait = _ceil(TEST_FACTOR * c) if c is not None else UNKNOWN_DURATION_LIMITS["ci_wait_timeout"]
        return {"init_test_timeout": whole_timeout, "test_timeout": test_timeout, "whole_timeout": whole_timeout, "ci_wait_timeout": ci_wait, "basis": basis}
    seconds = float(budget_minutes) * 60
    if strategy.whole_on_ci or w is None:
        init_timeout = _ceil(seconds * INIT_TEST_SHARE)
    else:
        init_timeout = _ceil(max(seconds * INIT_TEST_SHARE, TEST_FACTOR * w))
    if x is None:
        test_timeout = init_timeout
    else:
        test_timeout = _ceil(max(TEST_FACTOR * float(x), seconds * TEST_FLOOR_SHARE))
    whole_timeout = _ceil(max(TEST_FACTOR * float(w), seconds * TEST_FLOOR_SHARE)) if w is not None else init_timeout
    ci_wait = _ceil(max(TEST_FACTOR * c, seconds * CI_WAIT_SHARE)) if c is not None else _ceil(seconds * CI_WAIT_UNKNOWN_SHARE)
    return {"init_test_timeout": init_timeout, "test_timeout": test_timeout, "whole_timeout": whole_timeout, "ci_wait_timeout": ci_wait, "basis": basis}


def reserve_seconds(strategy: Strategy, whole: Optional[float], ci: Optional[float], ci_gate: bool) -> tuple[float, float]:
    """バッファに入れる全体テストの秒（危険フラグ, 最終ゲート）。CI に任せる戦略は危険フラグ 0・最終ゲート c（決定 8）。"""
    w = float(whole or 0.0)
    c = float(ci or 0.0)
    if strategy.whole_on_ci:
        return 0.0, c
    return w, (c if ci_gate else w)
