"""playwright_kit の pytest plugin (E2E シナリオテスト)。

CLI options:
- ``--pwk-config <path>``: scenario.config.yaml を指定
- ``--pwk-out-dir <path>``: 成果物 (HAR / trace / 動画 / report) の出力先
- ``--pwk-no-evidence``: evidence 収集を OFF
- ``--pwk-overlay``: overlay (赤丸カーソル + 字幕、旧名 HUD) を ON
- ``--pwk-drive-folder <id>``: Drive 連携

markers:
- ``page_role(*roles)``: accessibility / web vitals autouse の判定材料
- ``role(role_id)``: login する role を明示 (`pwk_role_<id>` fixture と並用可)
- ``phase(num)``: report.md のフェーズ集計用
- ``priority(level)``: report.md のソート用
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import pytest

from playwright_kit.pytest_report import PwkTestEntry, write_report

# 配下の fixture モジュールを pytest_plugins として読み込む
# (こうすると entry-point 経由で plugin がロードされた瞬間に fixture が
#  全 test に対して discover される)。
pytest_plugins = [
    "playwright_kit.fixtures.auth",
    "playwright_kit.fixtures.evidence",
    "playwright_kit.fixtures.accessibility",
    "playwright_kit.fixtures.web_vitals",
    "playwright_kit.fixtures.body_check",
]


# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("pwk", "playwright E2E scenario test (playwright_kit)")
    group.addoption(
        "--pwk-config",
        action="store",
        default=None,
        help="scenario.config.yaml へのパス (env PWK_CONFIG, または ./scenario.config.yaml も可)",
    )
    group.addoption(
        "--pwk-out-dir",
        action="store",
        default=None,
        help="成果物出力先ディレクトリ (default: ./reports/<run-id>/)",
    )
    group.addoption(
        "--pwk-no-evidence",
        action="store_true",
        default=False,
        help="HAR / trace / video の収集を OFF にする",
    )
    group.addoption(
        "--pwk-har-mode",
        action="store",
        default=None,
        choices=["minimal", "full", "none"],
        help=(
            "HAR 録画モード (Issue #62)。"
            "minimal=メタデータのみ (default; Basic 認証 + redirect race を回避), "
            "full=Playwright 既定の full HAR, "
            "none=HAR を出力しない。"
            "config の playwright.har_mode より優先。"
        ),
    )
    group.addoption(
        "--pwk-no-video",
        action="store_true",
        default=False,
        help="動画収集を明示的に OFF にする (デフォルトは全テストで動画 ON)",
    )
    group.addoption(
        "--pwk-overlay",
        action="store_true",
        default=False,
        help="overlay (赤丸カーソル + 字幕、旧名 HUD) を全 page に inject する",
    )
    group.addoption(
        "--pwk-drive-folder",
        action="store",
        default=None,
        help=(
            "Drive アップロード先フォルダ ID (terminal_summary 後に upload 実行)。"
            "trace.zip / *.har / 動画には機微情報 (URL / Cookie / localStorage / 操作履歴) "
            "が含まれる可能性があります。private folder + 信頼できる共有相手のみに限定してください。"
            " (Codex Minor 8)"
        ),
    )


# ---------------------------------------------------------------------------
# Markers / Config
# ---------------------------------------------------------------------------


_PWK_MARKERS: list[tuple[str, str]] = [
    ("page_role", "page_role(*roles): accessibility / web vitals autouse の判定 (例: form, list, dashboard)"),
    ("role", "role(role_id): test がどの login role を要求するか (`pwk_role_<id>` 経由でも可)"),
    ("phase", "phase(num): report.md のフェーズ集計用 (1〜N の整数)"),
    ("priority", "priority(level): report.md のソート用 (high/mid/low など任意文字列)"),
    ("no_body_check", "no_body_check: body_check autouse をこの test では skip する"),
]


def pytest_configure(config: pytest.Config) -> None:
    """marker 登録 + config の早期 load を試みる。

    config 読み込みは ``pwk_config`` fixture でも遅延ロードされるが、
    ``pwk_role_<id>`` fixture を *動的登録* するためには
    ``pytest_configure`` で 1 度 Config をロードしておく必要がある。
    failure は警告にとどめ、利用者が playwright_kit 機能を使わない場合に test 全体を
    潰さないようにする。
    """
    for name, doc in _PWK_MARKERS:
        config.addinivalue_line("markers", f"{name}: {doc}")

    # 動的 fixture 登録のため、可能なら Config を early load する。
    cfg = _try_load_config_silently(config)
    if cfg is not None:
        from playwright_kit.fixtures import auth as auth_module

        registered = auth_module.register_role_fixtures(auth_module, cfg)
        if registered:
            # plugin 自体にも公開しておく (ユーザが import 元を調整しなくて良いように)。
            import playwright_kit.pytest_plugin as plugin_self

            for name in registered:
                fn = getattr(auth_module, name, None)
                if fn is not None:
                    setattr(plugin_self, name, fn)
        # session 中で再利用するためにキャッシュする。
        config._pwk_config = cfg  # type: ignore[attr-defined]

    # 動画デフォルト ON (大原則: エビデンス動画を常に取得)
    # ユーザーが --video を CLI で明示指定した場合はそちらを優先する。
    # --pwk-no-video 指定時は video='off' に設定する。
    # --pwk-no-evidence 指定時も video='off' に設定する (全エビデンス OFF)。
    # pytest-playwright の --video デフォルト値は 'off' であるため、
    # getoption() の返り値では明示指定の有無を判別できない。
    # invocation_params.args を走査して明示指定を検出する。
    try:
        cli_args = list(config.invocation_params.args)
        video_explicitly_set = any(
            a == "--video" or a.startswith("--video=") for a in cli_args
        )
        no_video = config.getoption("pwk_no_video", default=False)
        no_evidence = config.getoption("pwk_no_evidence", default=False)
        if not video_explicitly_set:
            if no_video or no_evidence:
                config.option.video = "off"
            else:
                config.option.video = "on"
    except (ValueError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Reports / hooks
# ---------------------------------------------------------------------------


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """test の各 phase 終了時に ``pwk_evidence`` の状態をレポートに紐付ける。

    FAIL 時には evidence の trace/HAR path を log に追記し、
    成果物 path / marker を rep.user_properties に保存して
    ``pytest_terminal_summary`` で report.md に集約する。

    HAR lifecycle 修正 (Codex Major-1 完遂 / 3回目):
    Playwright は HAR を ``context.close()`` 時に flush する。
    ``pwk_evidence`` の finalizer は ``context`` の finalizer より先に動くため、
    call phase の時点では ``har_relpath`` / ``trace_relpath`` がまだ未確定の場合がある。

    teardown phase の makereport は pytest-playwright の ``context`` finalizer が
    teardown 中に完了した後に走るため、ここで再度 ``confirm_har()`` を呼んで
    HAR の存在を確認し直し、確定した path を teardown report の user_properties に
    積む。``_collect_entries()`` が teardown report の pwk_har/pwk_trace を call
    entry に merge することで、report.md に artifact path が反映される。
    """
    outcome = yield
    rep = outcome.get_result()

    ev = getattr(item, "_pwk_evidence", None)

    # teardown phase: context.close() 後に HAR が flush されるため confirm_har() 再呼び出し。
    # 確定した har_relpath / trace_relpath を teardown report の user_properties に積む。
    # body_check_violations もこの phase で確定する (autouse fixture finalizer が
    # pytest.fail 直前まで populate してから走る)。
    # _collect_entries() がこれらの値を call entry に merge する。
    if rep.when == "teardown" and ev is not None:
        ev.confirm_har()
        if ev.har_relpath:
            rep.user_properties.append(("pwk_har", str(ev.case_dir / ev.har_relpath)))
        if ev.trace_relpath:
            rep.user_properties.append(
                ("pwk_trace", str(ev.case_dir / ev.trace_relpath))
            )
        rep.user_properties.append(
            ("pwk_body_check_violations", len(ev.body_check_violations))
        )
        if ev.body_check_violations:
            rep.user_properties.append(
                ("pwk_body_check_detail", list(ev.body_check_violations))
            )
        return

    if rep.when != "call":
        return

    # pwk_evidence fixture が attach した状態を直接参照
    if ev is not None:
        if ev.har_relpath:
            rep.user_properties.append(("pwk_har", str(ev.case_dir / ev.har_relpath)))
        if ev.trace_relpath:
            rep.user_properties.append(
                ("pwk_trace", str(ev.case_dir / ev.trace_relpath))
            )
        rep.user_properties.append(("pwk_console_errors", len(ev.console_errors)))
        rep.user_properties.append(("pwk_page_errors", len(ev.page_errors)))

    # markers を user_properties に転写
    page_roles: list[str] = []
    for marker in item.iter_markers(name="page_role"):
        for arg in marker.args:
            if isinstance(arg, str):
                page_roles.append(arg)
            elif isinstance(arg, (list, tuple)):
                page_roles.extend(str(a) for a in arg)
    if page_roles:
        rep.user_properties.append(("pwk_page_role", page_roles))

    role_marker = item.get_closest_marker("role")
    if role_marker is not None and role_marker.args:
        rep.user_properties.append(("pwk_role", str(role_marker.args[0])))

    phase_marker = item.get_closest_marker("phase")
    if phase_marker is not None and phase_marker.args:
        try:
            rep.user_properties.append(("pwk_phase", int(phase_marker.args[0])))
        except (TypeError, ValueError):
            pass

    priority_marker = item.get_closest_marker("priority")
    if priority_marker is not None and priority_marker.args:
        rep.user_properties.append(("pwk_priority", str(priority_marker.args[0])))


# ---------------------------------------------------------------------------
# Terminal summary / session finish
# ---------------------------------------------------------------------------


def _collect_entries(terminalreporter) -> list[PwkTestEntry]:
    """terminalreporter から ``PwkTestEntry`` のリストを構築する。

    xfailed / xpassed も集約する (Codex Major 3)。
    pytest 内部では xfailed の rep は stats["xfailed"] に直接入るため、
    "xfailed" / "xpassed" キーを明示的に走査する。

    artifact 伝搬 (Codex Major-1 / 3回目):
    HAR は context.close() 時に flush されるため、call phase 時点では
    har_relpath / trace_relpath が未確定の場合がある。
    teardown phase の makereport で確定した pwk_har / pwk_trace を
    call entry に merge することで、report.md に artifact path を反映する。
    """
    # Step 1: call/setup phase の entry を nodeid でインデックス化
    call_entries: dict[str, PwkTestEntry] = {}
    for outcome_key in ("passed", "failed", "skipped", "error", "xfailed", "xpassed"):
        for rep in terminalreporter.stats.get(outcome_key, []):
            if getattr(rep, "when", "call") not in ("call", "setup"):
                continue
            props = dict(rep.user_properties or [])
            nodeid = getattr(rep, "nodeid", "?")
            entry = PwkTestEntry(
                nodeid=nodeid,
                name=getattr(rep, "head_line", nodeid),
                outcome=outcome_key,
                duration_s=float(getattr(rep, "duration", 0.0) or 0.0),
                page_role=list(props.get("pwk_page_role") or []),
                role=props.get("pwk_role"),
                phase=int(props.get("pwk_phase") or 0),
                priority=props.get("pwk_priority"),
                har_path=props.get("pwk_har"),
                trace_path=props.get("pwk_trace"),
                console_errors=int(props.get("pwk_console_errors") or 0),
                page_errors=int(props.get("pwk_page_errors") or 0),
                # Amazon Q Critical-3: skipped 時の longrepr は tuple 形式のため
                # failed / error のときのみ str() 化する。他 outcome は None のまま。
                error_message=(
                    str(rep.longrepr)
                    if outcome_key in ("failed", "error") and rep.longrepr
                    else None
                ),
            )
            call_entries[nodeid] = entry

    # Step 2: teardown report の pwk_har / pwk_trace / body_check を call entry に merge する。
    # teardown 時点で context.close() 後の確定値や body_check の violation 集計が
    # 積まれているため、call phase で未確定だった値をここで埋める。
    # pytest は setup/teardown の rep を stats[""] (空文字キー) に格納するため、
    # "" キーも含めて全キーを走査する。
    for outcome_key in terminalreporter.stats:
        for rep in terminalreporter.stats[outcome_key]:
            if getattr(rep, "when", None) != "teardown":
                continue
            nodeid = getattr(rep, "nodeid", "?")
            if nodeid not in call_entries:
                continue
            entry = call_entries[nodeid]
            props = dict(rep.user_properties or [])
            if not entry.har_path and props.get("pwk_har"):
                entry.har_path = props["pwk_har"]
            if not entry.trace_path and props.get("pwk_trace"):
                entry.trace_path = props["pwk_trace"]
            if "pwk_body_check_violations" in props:
                entry.body_check_violations = int(
                    props.get("pwk_body_check_violations") or 0
                )
            detail = props.get("pwk_body_check_detail")
            if detail:
                entry.body_check_detail = list(detail)
            # body_check が teardown で pytest.fail を起こした場合、call phase
            # は passed / xfailed / xpassed / skipped のまま teardown report
            # のみ failed/error になる。call phase の本物の failure は上書き
            # しないが、それ以外の outcome は teardown 失敗を反映させる
            # (xfail テストでも teardown の body_check fail は実バグ扱い)。
            teardown_outcome = getattr(rep, "outcome", None)
            if (
                teardown_outcome in ("failed", "error")
                and entry.outcome not in ("failed", "error")
            ):
                entry.outcome = (
                    "error" if teardown_outcome == "error" else "failed"
                )
                if rep.longrepr and not entry.error_message:
                    entry.error_message = str(rep.longrepr)

    return list(call_entries.values())


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """``reports/<run-id>/report.md`` を生成する。

    ``--pwk-out-dir`` 指定があればそこに、なければ ``pwk_out_dir`` fixture と
    同一の ``pytestconfig._pwk_out_dir`` キャッシュを参照する。
    キャッシュが無い場合 (pwk_out_dir fixture が一度も呼ばれていない) は
    ``_resolve_out_dir`` 経由でセットする。これにより evidence と report.md の
    出力先が秒またぎでズレる問題を防ぐ (新規 Major 対応)。
    """
    # session 中で 1 件も test を回していない (collect-only など) は先に entries で判断。
    # xfailed / xpassed のみの session でも report を生成するため、
    # early return は _collect_entries() の結果で判断する (新規 Minor 対応)。
    entries = _collect_entries(terminalreporter)
    if not entries:
        return

    cached_cfg = getattr(config, "_pwk_config", None)
    base_url = cached_cfg.base_url if cached_cfg is not None else None
    title = (
        cached_cfg.report.title
        if cached_cfg is not None
        else "シナリオ E2E テスト 実施報告書"
    )

    # evidence.py の _resolve_out_dir と同一キャッシュ (_pwk_out_dir) を参照する。
    # これにより両者が独立に datetime.now() を呼んで別ディレクトリを作る問題を解消。
    from playwright_kit.fixtures.evidence import _resolve_out_dir

    out_dir = _resolve_out_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Amazon Q Critical-4: xdist 並列実行時の session 開始時刻計算が不正確な問題を修正。
    # terminalreporter._sessionstarttime (pytest 内部 float) を優先利用し、
    # 無ければ従来の逐次実行前提の計算にフォールバックする。
    session_start_ts = getattr(terminalreporter, "_sessionstarttime", None)
    if session_start_ts is not None:
        started = _dt.datetime.fromtimestamp(session_start_ts)
    else:
        started = _dt.datetime.now() - _dt.timedelta(
            seconds=sum(e.duration_s for e in entries)
        )
    finished = _dt.datetime.now()
    path = write_report(
        entries,
        out_dir=out_dir,
        started_at=started,
        finished_at=finished,
        title=title,
        base_url=base_url,
    )
    terminalreporter.write_sep("-", "pwk report")
    terminalreporter.write_line(f"report.md generated: {path}")

    # session 後の Drive アップロードに使うため pickle 不要な情報を保存
    config._pwk_report_path = path  # type: ignore[attr-defined]
    config._pwk_out_dir = out_dir  # type: ignore[attr-defined]


def pytest_sessionfinish(session, exitstatus):
    """``--pwk-drive-folder`` 指定時、生成済 report.md と evidence を Drive アップ。

    ``upload_evidence.upload`` を直接呼ぶ。失敗時は警告のみで test 結果には影響しない。
    """
    folder_id: str | None = session.config.getoption(
        "pwk_drive_folder", default=None
    )
    if not folder_id:
        return

    report_path: Path | None = getattr(session.config, "_pwk_report_path", None)
    out_dir: Path | None = getattr(session.config, "_pwk_out_dir", None)
    if report_path is None or out_dir is None:
        return

    try:
        # Amazon Q Critical-5: sys.path への動的 inject を廃止し、
        # playwright_kit.uploaders パッケージ経由で安全に import する。
        # scripts/upload_evidence.py は CLI スタンドアロン用途として残す。
        from playwright_kit.uploaders import upload, detect_kind

        # report.md は kind=any でアップ
        if report_path.exists():
            upload(
                report_path, kind="any", parent_folder_id=folder_id, public=False
            )

        # trace.zip / *.har / *.mp4 / body_check.jsonl を 1 階層下から拾い上げる
        for sub in out_dir.iterdir():
            if not sub.is_dir():
                continue
            for f in sub.iterdir():
                suffix = f.suffix
                if suffix not in (".zip", ".har", ".mp4", ".webm", ".jsonl"):
                    continue
                # detect_kind は body_check.jsonl 等の任意ファイルを未知の kind
                # と扱うため、jsonl は ``any`` に固定する。
                kind = "any" if suffix == ".jsonl" else detect_kind(f)
                upload(
                    f, kind=kind, parent_folder_id=folder_id, public=False
                )
    except Exception as exc:  # pragma: no cover - depends on Drive auth
        import warnings

        warnings.warn(
            f"[pwk] Drive upload 失敗 (session continues): {exc}",
            stacklevel=1,
        )


def _try_load_config_silently(config: pytest.Config) -> Any | None:
    """``--pwk-config`` 等から Config を試行ロードする。失敗時は None。"""
    import os
    from pathlib import Path

    raw_path: str | None = config.getoption("pwk_config", default=None)
    if not raw_path:
        env = os.environ.get("PWK_CONFIG")
        if env:
            raw_path = env
    if not raw_path:
        candidate = Path.cwd() / "scenario.config.yaml"
        if candidate.exists():
            raw_path = str(candidate)
    if not raw_path:
        return None

    try:
        from playwright_kit.config import Config

        return Config.load(Path(raw_path).resolve())
    except Exception as exc:  # pragma: no cover - depends on user config
        import warnings

        warnings.warn(
            f"[pwk] config load 失敗 ({raw_path}): {exc}. "
            "pwk_role_<id> fixture は動的登録されません。",
            stacklevel=2,
        )
        return None
