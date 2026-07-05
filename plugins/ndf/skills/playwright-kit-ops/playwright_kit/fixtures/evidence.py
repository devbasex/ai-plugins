"""evidence fixture: HAR / trace / console / pageerror の収集。

pytest-playwright が提供する ``browser_context_args`` / ``context`` / ``page``
fixture と組み合わせて、test 関数ごとに以下を自動収集する:

- HAR: ``browser_context_args`` に ``record_har_path`` を inject
- trace: ``context.tracing.start`` / ``stop`` (``--pwk-no-evidence`` で無効化)
- console.error / pageerror: page listener として attach
  (``tolerated_console_errors`` / ``tolerated_page_errors`` でフィルタ)

artifact の出力先は ``--pwk-out-dir`` (default: ``./reports/<run-id>/``)。
test 関数 ID から sub-dir を作って 1 test = 1 dir で隔離する。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import pytest

from playwright_kit.config import Config, PlaywrightConfig


# ---------------------------------------------------------------------------
# Output directory resolver (session scope)
# ---------------------------------------------------------------------------


def _resolve_out_dir(pytestconfig) -> Path:
    """``--pwk-out-dir`` が指定されればそれを、なければ ``reports/<run-id>/``。

    run_id は session 開始時に 1 度だけ決定し、``pytestconfig._pwk_out_dir`` に
    キャッシュする。これにより ``pwk_out_dir`` fixture と
    ``pytest_terminal_summary`` が別々に ``datetime.now()`` を呼んで
    秒またぎでディレクトリがズレる問題を防ぐ (新規 Major 対応)。

    ``--pwk-out-dir`` が明示指定されている場合はキャッシュ不要のため
    常にその値を返す（複数回呼ばれても同じ値）。
    """
    raw: str | None = pytestconfig.getoption("pwk_out_dir", default=None)
    if raw:
        return Path(raw).resolve()

    # --pwk-out-dir 未指定時のみキャッシュで run_id の秒またぎを防ぐ。
    # hasattr で厳密にチェックし、MagicMock 等が偽の属性を返さないようにする。
    if "_pwk_out_dir" in vars(pytestconfig):
        return pytestconfig._pwk_out_dir  # type: ignore[attr-defined]

    run_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = (Path.cwd() / "reports" / run_id).resolve()
    # session-scoped キャッシュとして保存
    pytestconfig._pwk_out_dir = out  # type: ignore[attr-defined]
    return out


@pytest.fixture(scope="session")
def pwk_out_dir(pytestconfig) -> Path:
    """session 全体で共有する成果物ルート。session 開始時に作成する。

    ``_resolve_out_dir`` を通じて ``pytestconfig._pwk_out_dir`` にキャッシュし、
    ``pytest_terminal_summary`` と同じ out_dir を参照する。
    """
    out = _resolve_out_dir(pytestconfig)
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Per-test evidence (function scope)
# ---------------------------------------------------------------------------


_FILENAME_SAFE_RE = re.compile(r"[^\w\-]+")


def _safe_slug(name: str, fallback: str = "test") -> str:
    """文字列から安全なファイル名スラグを生成する (後方互換維持)。"""
    s = _FILENAME_SAFE_RE.sub("-", name).strip("-").lower()
    return s[:80] or fallback


def _safe_case_slug(node: Any) -> str:
    """nodeid + xdist worker + sha1[:6] suffix で衝突しない slug を生成する (Codex Major 2)。

    - parametrize / 同名関数 / xdist 並列で trace.zip / request.har の上書きを防止。
    - 既存の _safe_slug(name, fallback) 仕様は変えず、evidence fixture 内のみ本関数を使う。
    """
    nodeid = getattr(node, "nodeid", getattr(node, "name", "test"))
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    raw = f"{nodeid}@{worker}" if worker else nodeid
    slug = _FILENAME_SAFE_RE.sub("-", raw).strip("-").lower()
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
    # 60 文字 + "-" + sha1[:6] = 最大 67 文字程度に圧縮
    return f"{slug[:60]}-{digest}".strip("-") or "test"


@dataclass
class PwkEvidence:
    """1 test 関数分の証跡コレクタ。"""

    case_dir: Path
    config: Config | None
    enabled: bool

    har_path: Path | None = None
    trace_path: Path | None = None
    har_relpath: str | None = None
    trace_relpath: str | None = None

    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    axe_violations: list[dict[str, Any]] = field(default_factory=list)
    web_vitals_metrics: dict[str, float] = field(default_factory=dict)
    web_vitals_passed: bool = True
    # PHP / SSR ページ本文エラー (body_check) 違反 (v0.4.0)。1 件 = 1 dict
    # ({url, category, pattern, snippet})。
    body_check_violations: list[dict[str, Any]] = field(default_factory=list)

    log_lines: list[str] = field(default_factory=list)

    _trace_started: bool = field(default=False, init=False, repr=False)
    _tolerated_console_re: list[re.Pattern[str]] = field(
        default_factory=list, init=False, repr=False
    )
    _tolerated_page_re: list[re.Pattern[str]] = field(
        default_factory=list, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.config is not None:
            self._tolerated_console_re = [
                re.compile(p) for p in self.config.tolerated_console_errors
            ]
            self._tolerated_page_re = [
                re.compile(p) for p in self.config.tolerated_page_errors
            ]

    # --- listener ------------------------------------------------------

    def attach_listeners(self, page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)

    def _on_console(self, msg) -> None:
        try:
            if msg.type != "error":
                return
            loc = getattr(msg, "location", None) or {}
            text = msg.text[:500]
            for rx in self._tolerated_console_re:
                if rx.search(text):
                    return
            self.console_errors.append(f"{loc.get('url', '?')}: {text}")
        except Exception as exc:  # pragma: no cover
            self.log_lines.append(f"[console listener] {exc}")

    def _on_pageerror(self, exc) -> None:
        try:
            text = str(exc)[:1000]
            for rx in self._tolerated_page_re:
                if rx.search(text):
                    return
            self.page_errors.append(text)
        except Exception as listener_exc:  # pragma: no cover
            self.log_lines.append(f"[pageerror listener] {listener_exc}")

    # --- trace lifecycle (context scope) -------------------------------

    def start_tracing(self, context) -> None:
        if not self.enabled or self.trace_path is None:
            return
        try:
            context.tracing.start(
                name=self.case_dir.name,
                title=self.case_dir.name,
                snapshots=True,
                screenshots=True,
                sources=False,
            )
            self._trace_started = True
        except Exception as exc:  # pragma: no cover
            self.log_lines.append(f"[trace] start 失敗: {exc}")

    def stop_tracing(self, context) -> None:
        if not self._trace_started or self.trace_path is None:
            return
        try:
            context.tracing.stop(path=str(self.trace_path))
            self.trace_relpath = self.trace_path.name
        except Exception as exc:  # pragma: no cover
            self.log_lines.append(f"[trace] stop 失敗: {exc}")

    def confirm_har(self) -> None:
        if self.har_path and self.har_path.exists():
            self.har_relpath = self.har_path.name

    # --- summary -------------------------------------------------------

    @property
    def has_runtime_errors(self) -> bool:
        return bool(self.console_errors or self.page_errors)

    def runtime_error_summary(self) -> str:
        parts: list[str] = []
        if self.page_errors:
            parts.append(f"pageerror {len(self.page_errors)} 件")
        if self.console_errors:
            parts.append(f"console.error {len(self.console_errors)} 件")
        return "Runtime errors detected: " + ", ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _pwk_config_optional(pytestconfig) -> Config | None:
    """``pwk_config`` を session に 1 度だけ load する (失敗時は None)。

    evidence fixture は pwk_config が無くても動くように optional にしてある。
    """
    cached = getattr(pytestconfig, "_pwk_config", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    return None


def _resolve_har_mode(pytestconfig, pwk_config) -> str:
    """``--pwk-har-mode`` CLI > ``playwright.har_mode`` config > ``PlaywrightConfig`` default。

    Issue #62 対策で default は ``minimal``。``--pwk-no-evidence`` が True の場合
    呼び出し側で HAR を一切 inject しない (本関数の戻り値は使われない)。

    default 値は ``PlaywrightConfig().har_mode`` から引くことで dataclass 側との
    二重管理を防ぐ。
    """
    cli = pytestconfig.getoption("pwk_har_mode", default=None)
    if cli:
        # ``pytest_plugin.py`` の argparse ``choices`` で値は担保済だが、
        # API 経由 (``--config`` 等) で大文字が来た場合に備える defensive normalize。
        return str(cli).lower()
    if pwk_config is not None:
        return str(pwk_config.playwright.har_mode).lower()
    return PlaywrightConfig().har_mode


@pytest.fixture()
def browser_context_args(
    browser_context_args, request, pytestconfig, pwk_out_dir, _pwk_config_optional
) -> dict[str, Any]:
    """pytest-playwright の ``browser_context_args`` を function scope で override し、
    1 test = 1 HAR を実現する (Codex Major 1)。

    - scope を function に変更し、``request.node`` ごとに ``case_dir/request.har``
      を ``record_har_path`` に inject する。
    - session 共通 HAR (``session.har``) は廃止。これにより
      ``PwkEvidence.confirm_har()`` が常に None を返す不整合を解消。
    - ``--pwk-no-evidence`` が True なら HAR 収集を OFF。
    - HAR mode (Issue #62):
      - ``--pwk-har-mode none`` (または config ``playwright.har_mode: none``):
        ``record_har_path`` を inject しない。
      - ``minimal`` (default): ``record_har_mode="minimal"`` でメタデータのみ
        記録。Basic 認証 + redirect 連続時の ``ERR_ABORTED`` race を回避する。
      - ``full``: Playwright 既定の full HAR (body + content) を記録。
    """
    no_evidence = bool(pytestconfig.getoption("pwk_no_evidence", default=False))
    args = dict(browser_context_args or {})
    if no_evidence:
        return args

    har_mode = _resolve_har_mode(pytestconfig, _pwk_config_optional)
    if har_mode == "none":
        return args

    case_dir = pwk_out_dir / _safe_case_slug(request.node)
    case_dir.mkdir(parents=True, exist_ok=True)
    args.setdefault("record_har_path", str(case_dir / "request.har"))
    if har_mode == "minimal":
        # Playwright の record_har_mode="minimal" (Playwright >= 1.30) は
        # request/response の主要メタデータのみ。content は記録されないので
        # omit 指定は不要。
        args.setdefault("record_har_mode", "minimal")
    else:  # "full"
        # 既存挙動 (body 含む) を維持しつつ content だけは省略する。
        args.setdefault("record_har_content", "omit")
    return args


@pytest.fixture()
def pwk_evidence(
    request,
    pytestconfig,
    pwk_out_dir: Path,
    _pwk_config_optional,
    context,
    page,
) -> Iterator[PwkEvidence]:
    """1 test 関数分の evidence collector を返す。

    - ``--pwk-no-evidence`` が True なら trace/HAR を OFF にし、listener のみ動かす
    - ``page`` fixture から console / pageerror listener を attach
    - ``context.tracing.start/stop`` を裏で実行 (有効時)
    - ``--pwk-overlay`` 指定時は ``overlay.OVERLAY_INIT_SCRIPT`` を ``context.add_init_script``
      で全 page に inject する
    - ``pytest_runtest_makereport`` から FAIL 時に ``pwk_evidence`` の状態を確認可能
    """
    enabled = not bool(pytestconfig.getoption("pwk_no_evidence", default=False))
    overlay_enabled = bool(pytestconfig.getoption("pwk_overlay", default=False))
    # _safe_case_slug で nodeid + xdist worker + sha1[:6] の衝突しない slug を使用 (Codex Major 2)
    case_dir = pwk_out_dir / _safe_case_slug(request.node)
    case_dir.mkdir(parents=True, exist_ok=True)

    # HAR mode が "none" のときは ``request.har`` を期待しないようにする
    # (browser_context_args で record_har_path 自体を inject していない: Issue #62)。
    har_mode = _resolve_har_mode(pytestconfig, _pwk_config_optional)
    har_enabled = enabled and har_mode != "none"

    ev = PwkEvidence(
        case_dir=case_dir,
        config=_pwk_config_optional,
        enabled=enabled,
        har_path=(case_dir / "request.har") if har_enabled else None,
        trace_path=(case_dir / "trace.zip") if enabled else None,
    )
    ev.attach_listeners(page)
    ev.start_tracing(context)

    # overlay (赤丸カーソル + 字幕、旧名 HUD) を init_script で inject。
    if overlay_enabled:
        try:
            from playwright_kit.overlay import OVERLAY_INIT_SCRIPT

            context.add_init_script(OVERLAY_INIT_SCRIPT)
        except Exception as exc:  # pragma: no cover
            ev.log_lines.append(f"[overlay] add_init_script 失敗: {exc}")

    # request.node に ev を保持して makereport hook から参照可能にする
    request.node._pwk_evidence = ev  # type: ignore[attr-defined]

    try:
        yield ev
    finally:
        try:
            ev.stop_tracing(context)
        finally:
            ev.confirm_har()
