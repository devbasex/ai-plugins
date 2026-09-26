"""提案の前に指標を測る（`measure`、#1319）。

`init` が作った `code_metrics`（`status = pending`）を埋める。**測るのは 1 実行に 1 回だけ、
提案を始める前だけ**（I1 I2）。測定の失敗（ツールが無い・落ちる・読めない・遅い・書けない）で
終了コードを変えない（I6）。失敗は記録に残して提案へ進む。

測定は書き込み用の作業ディレクトリ（`work/`）で行う。書くのは一時ディレクトリ（全件無視の
`.gitignore` の中）だけで、対象リポジトリの追跡されたファイルを変えない（I8）。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import time
from typing import Any, Callable, Optional

import statefile

from .. import clock, codemetrics as cm, codemetrics_read as read, codemetrics_view as view, info, timeline
from ..codemetrics_record import propose_started
from ..paths import git_out, load_state, work_dir
from ..process import run_capture

# 打ち切ったプロセスグループへ SIGKILL を送るまでの待ち（`timeline.FIXED_VALUES` の kill_grace）。
KILL_GRACE = 5.0


def metrics_path(tmp_dir: pathlib.Path, run_id: Any) -> pathlib.Path:
    """指標のファイルの置き場。"""
    return tmp_dir / f"code-metrics-rf{run_id}.md"


def cmd_measure(args: argparse.Namespace) -> None:
    """指標を測り、指標のファイルを書く。`CODE_METRICS` と `CODE_METRICS_FILE` を返す。

    | 状態 | 扱い |
    | --- | --- |
    | 記録が無い（旧い状態ファイルで提案を始めた後） | 何もせず空を返す |
    | `pending` 以外・提案を始めた後 | 測らずに記録を返す（I1 I2） |
    | `--no-code-metrics` | `disabled` にする |
    | それ以外 | 測る |
    """
    path, state = load_state(args.id)
    record = state.get("code_metrics")
    if isinstance(record, dict) and record.get("status") == cm.STATUS_PENDING \
            and not propose_started(state):
        if (record.get("config") or {}).get("enabled") is False:
            record["status"] = cm.STATUS_DISABLED
            info("ℹ 指標は測りません（--no-code-metrics）")
        else:
            Measurement(state, record).run()
        statefile.save(path, state)
    record = record if isinstance(record, dict) else {}
    statefile.emit(CODE_METRICS=record.get("status") or "",
                   CODE_METRICS_FILE=record.get("file") or "")


def tracked_files(work: str, scope: list[str]) -> list[str]:
    """`--scope` の中で git が追跡しているファイル（I3）。作業ディレクトリに無いものは除く。"""
    out = git_out(work, ["ls-files", "-z", "--", *scope], strip=False)
    if not out:
        return []
    return sorted(p for p in out.split("\0") if p and os.path.isfile(os.path.join(work, p)))


class Measurement:
    """1 回の測定。締め切り（`measure_deadline`）を言語ごとの測定と重複検出で分け合う。"""

    def __init__(self, state: dict[str, Any], record: dict[str, Any],
                 which: Optional[Callable[[str], Optional[str]]] = None,
                 kill_grace: float = KILL_GRACE) -> None:
        self.state = state
        self.record = record
        self.config = record.get("config") or cm.load_config(None)
        self.work = work_dir(state)
        self.roots = list(dict.fromkeys([str(self.work), os.path.realpath(self.work)]))
        self.tmp = pathlib.Path(state["tmp_dir"])
        self.run_id = state["id"]
        self.which = which or shutil.which
        self.kill_grace = kill_grace
        self.budget = 0
        self.end = 0.0
        self.sources: dict[str, str] = {}
        self.parsed: dict[str, Optional[list[dict[str, Any]]]] = {}

    # --- 時間と起動 ---
    def left(self) -> float:
        return self.end - time.monotonic()

    def call(self, argv: list[str], cwd: Optional[str] = None) -> tuple[Optional[int], str, str, bool]:
        left = self.left()
        if left <= 0:
            return None, "", "", True
        return run_capture(argv, cwd or self.work, left, self.kill_grace)

    def _version(self, command: str, runner: str) -> Optional[str]:
        if runner != "path":
            return cm.pinned_version(command)
        code, out, err, _ = self.call([command, "--version"])
        found = cm.version_from(out + "\n" + err) if code == 0 else None
        return f"{command} {found}" if found else None

    def _resolve(self, commands: tuple[str, ...], result: dict[str, Any]) -> Optional[dict[str, list[str]]]:
        """コマンドの起動の頭を決める。どれかが無ければ `tool_missing` にして `None`。"""
        resolved = {c: cm.resolve_runner(c, self.which) for c in commands}
        missing = [c for c, r in resolved.items() if r is None]
        if missing:
            runner = cm.COMMANDS[commands[0]][0]
            self._fail(result, cm.TOOL_MISSING, " / ".join([runner, *commands]))
            return None
        runners = {r[0] for r in resolved.values() if r}
        result["runner"] = runners.pop() if len(runners) == 1 else "path"
        versions = [self._version(c, r[0]) for c, r in resolved.items() if r]
        result["version"] = " / ".join(v for v in versions if v) or None
        return {c: r[1] for c, r in resolved.items() if r}

    def _fail(self, result: dict[str, Any], reason: str, detail: Optional[str]) -> None:
        result.update(status=cm.FAILED, reason=reason, detail=detail)

    def _outcome(self, result: dict[str, Any], code: Optional[int], err: str, timed_out: bool,
                 ok: tuple[int, ...] = (0,)) -> bool:
        """起動の結果が正常か。正常でなければ理由を書いて `False`。"""
        if timed_out:
            self._fail(result, cm.TIMEOUT, f"{self.budget} 秒")
            return False
        if code not in ok:
            tail = read.tail_lines(err)
            self._fail(result, cm.TOOL_FAILED, f"終了コード {code}" + (f": {tail}" if tail else ""))
            return False
        return True

    # --- 全体 ---
    def run(self) -> None:
        started = time.monotonic()
        record = self.record
        record["started_at"] = statefile.now()
        record["head"] = git_out(self.work, ["rev-parse", "HEAD"])
        limits = {**timeline.of_state(self.state), **(self.state.get("limits") or {})}
        self.budget = timeline.measure_deadline(clock.now(), limits)
        record["deadline_seconds"] = self.budget
        self.end = started + self.budget
        if record.get("config", {}).get("source") == cm.SOURCE_INVALID:
            info(f"⚠ {cm.DECLARATION_FILE} を使わず既定で測ります"
                 f"（{cm.DECLARATION_INVALID}: {self.config.get('error')}）")
        files = tracked_files(self.work, list(self.state.get("target_scope") or []))
        by_lang, ignored = cm.split_by_language(files)
        record["ignored_files"] = ignored
        if not by_lang:
            record.update(status=cm.STATUS_NO_LANGUAGE, languages=[], duplication=[],
                          seconds=round(time.monotonic() - started, 1))
            info("ℹ 測る言語がありません（対象範囲に言語を判定できるファイルが無い）")
            return
        self._read_sources(by_lang)
        languages, sections = [], {}
        for lang in sorted(by_lang):
            result, section = self.language(lang, by_lang[lang])
            languages.append(result)
            sections[lang] = section
            info(view.stderr_line(result))
        duplication, clones = [], []
        for tool, langs, targets in cm.duplication_targets(self.config, by_lang):
            result, found = self.duplicate(tool, langs, targets)
            duplication.append(result)
            clones += found
            info(view.stderr_line(result, duplication=True))
        record["languages"] = languages
        record["duplication"] = duplication
        text = view.metrics_markdown(
            run_id=self.run_id, head=record.get("head"),
            scope=list(self.state.get("target_scope") or []), config=self.config,
            ignored=ignored, languages=languages, sections=sections,
            duplication=duplication, clones=clones)
        target = metrics_path(self.tmp, self.run_id)
        try:
            self.tmp.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            record.update(status=cm.STATUS_WRITE_FAILED, file=None, error=str(exc))
            info(f"⚠ 指標のファイルを書けませんでした（{exc}）。指標なしで提案します")
        else:
            record.update(status=cm.STATUS_WRITTEN, file=str(target))
            info(f"📏 指標のファイル: {target}")
        record["seconds"] = round(time.monotonic() - started, 1)

    def _timed_out(self, result: dict[str, Any]) -> bool:
        """残りが無ければ `timeout` を書いて `True`。起動も読込もせずに次へ進むために使う。"""
        if self.left() > 0:
            return False
        self._fail(result, cm.TIMEOUT, f"{self.budget} 秒")
        return True

    def _read_sources(self, by_lang: dict[str, list[str]]) -> None:
        """ソースを読み、Python を解析する。締め切りを過ぎたらやめる（残りは言語ごとに `timeout`）。"""
        for files in by_lang.values():
            for rel in files:
                if self.left() <= 0:
                    return
                try:
                    data = pathlib.Path(self.work, rel).read_bytes()
                except OSError:
                    continue
                self.sources[rel] = data.decode("utf-8", errors="replace")
        for rel in by_lang.get("python", []):
            if self.left() <= 0:
                return
            text = self.sources.get(rel)
            self.parsed[rel] = read.python_functions(text) if text is not None else None

    # --- 言語ごと ---
    def language(self, lang: str, files: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        tool, reason = cm.language_tool(self.config, lang)
        result: dict[str, Any] = {
            "language": lang, "tool": tool, "version": None, "runner": None,
            "files": len(files), "seconds": 0.0, "status": cm.FAILED, "reason": reason,
            "detail": None, "unreadable_files": 0,
        }
        section: dict[str, Any] = {"functions": [], "files": []}
        if reason is not None or self._timed_out(result):
            return result, section
        prefixes = self._resolve(cm.TOOL_COMMANDS[tool], result)
        if prefixes is None:
            return result, section
        started = time.monotonic()
        if tool == cm.TOOL_RUFF_COMPLEXIPY:
            measured = self._python(files, prefixes, result)
        else:
            measured = self._lizard(files, prefixes, result)
        result["seconds"] = round(time.monotonic() - started, 1)
        if measured is None:
            return result, section
        functions, unreadable = measured
        result.update(status=cm.MEASURED, reason=None, detail=None,
                      unreadable_files=len(unreadable))
        counts = {p: cm.count_lines(self.sources.get(p, "")) for p in files}
        section["functions"] = functions
        section["files"] = read.file_metrics(files, counts, functions, unreadable)
        return result, section

    def _python(self, files: list[str], prefixes: dict[str, list[str]],
                result: dict[str, Any]) -> Optional[tuple[list[dict[str, Any]], set[str]]]:
        ruff_values: dict = {}
        cognitive: dict = {}
        invalid: set[str] = set()
        cache = self.tmp / "complexipy-cache"
        for n, batch in enumerate(cm.chunks(files)):
            code, out, err, timed_out = self.call(cm.ruff_argv(prefixes["ruff"], batch))
            if not self._outcome(result, code, err, timed_out):
                return None
            try:
                values, bad = read.parse_ruff(out, self.roots)
            except read.UnreadableOutput as exc:
                self._fail(result, cm.UNREADABLE_OUTPUT, str(exc))
                return None
            ruff_values.update(values)
            invalid |= bad
            output = self.tmp / f"complexipy-rf{self.run_id}-{n}.json"
            output.unlink(missing_ok=True)
            code, _, err, timed_out = self.call(
                cm.complexipy_argv(prefixes["complexipy"], batch, str(output), str(cache)))
            # 0 と、1 で `--output` が読めるとき（構文を読めないファイルがある）を正常とする。
            if not self._outcome(result, code, err, timed_out, ok=(0, 1)):
                return None
            try:
                text = output.read_text(encoding="utf-8")
                cognitive.update(read.parse_complexipy(text, self.roots))
            except (OSError, read.UnreadableOutput) as exc:
                if code == 1:
                    self._outcome(result, code, err, False)
                else:
                    self._fail(result, cm.UNREADABLE_OUTPUT, str(exc))
                return None
        unreadable = invalid | {p for p in files if self.parsed.get(p) is None}
        listed = {p: self.parsed.get(p) for p in files}
        return read.python_function_metrics(listed, ruff_values, cognitive, unreadable), unreadable

    def _lizard(self, files: list[str], prefixes: dict[str, list[str]],
                result: dict[str, Any]) -> Optional[tuple[list[dict[str, Any]], set[str]]]:
        functions: list[dict[str, Any]] = []
        for batch in cm.chunks(files):
            code, out, err, timed_out = self.call(cm.lizard_argv(prefixes["lizard"], batch))
            if not self._outcome(result, code, err, timed_out):
                return None
            try:
                functions += read.parse_lizard(out, self.roots)
            except read.UnreadableOutput as exc:
                self._fail(result, cm.UNREADABLE_OUTPUT, str(exc))
                return None
        return functions, set()

    # --- 重複検出 ---
    def duplicate(self, tool: str, langs: list[str],
                  files: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if tool == cm.TOOL_SYMILAR:
            # 1 本でも読めないファイルがあると symilar の全体が落ちる。`ast` が読めたものだけを渡す。
            files = [p for p in files if self.parsed.get(p) is not None]
        result: dict[str, Any] = {
            "tool": tool, "version": None, "runner": None, "languages": list(langs),
            "files": len(files), "seconds": 0.0, "status": cm.FAILED, "reason": None,
            "detail": None, "clones": 0, "duplicated_lines": 0, "total_lines": 0,
        }
        if self._timed_out(result):
            return result, []
        size = cm.arg_bytes(files if tool == cm.TOOL_SYMILAR
                            else [os.path.join(self.work, p) for p in files])
        if size > cm.DUPLICATE_ARG_BYTES:
            self._fail(result, cm.TOO_MANY_FILES, f"{len(files):,} ファイル・{size:,} バイト")
            return result, []
        prefixes = self._resolve((tool,), result)
        if prefixes is None:
            return result, []
        started = time.monotonic()
        parsed = self._symilar(files, prefixes[tool], result) if tool == cm.TOOL_SYMILAR \
            else self._jscpd(files, prefixes[tool], result)
        result["seconds"] = round(time.monotonic() - started, 1)
        if parsed is None:
            return result, []
        clones = parsed["clones"]
        result.update(status=cm.MEASURED, clones=len(clones),
                      duplicated_lines=parsed["duplicated_lines"], total_lines=parsed["total_lines"])
        if "sources" in parsed:
            result["sources"] = parsed["sources"]
        return result, clones

    def _symilar(self, files: list[str], prefix: list[str],
                 result: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not files:
            return {"clones": [], "total_lines": 0, "duplicated_lines": 0}
        code, out, err, timed_out = self.call(cm.symilar_argv(prefix, files))
        if not self._outcome(result, code, err, timed_out):
            return None
        try:
            return read.parse_symilar(out, self.roots)
        except read.UnreadableOutput as exc:
            self._fail(result, cm.UNREADABLE_OUTPUT, str(exc))
            return None

    def _jscpd(self, files: list[str], prefix: list[str],
               result: dict[str, Any]) -> Optional[dict[str, Any]]:
        # cwd を空のディレクトリにする。jscpd は cwd の `.jscpd.json` と `package.json` を読む。
        out_dir = self.tmp / f"jscpd-rf{self.run_id}"
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        abs_files = [os.path.join(self.work, p) for p in files]
        code, _, err, timed_out = self.call(cm.jscpd_argv(prefix, abs_files, str(out_dir)),
                                            cwd=str(out_dir))
        if not self._outcome(result, code, err, timed_out):
            return None
        report = out_dir / "jscpd-report.json"
        try:
            text: Optional[str] = report.read_text(encoding="utf-8") if report.is_file() else None
            return read.parse_jscpd(text, self.roots)
        except (OSError, read.UnreadableOutput) as exc:
            self._fail(result, cm.UNREADABLE_OUTPUT, str(exc))
            return None
