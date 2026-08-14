"""pytester を使った統合テスト (Codex Major-5 完遂 / 3回目強化版)。

本物の pytest セッションを隔離環境で実行し、以下 4 件を検証する:

(a) auth cache hit/miss:
    同 role に対して pwk_role_<id> fixture を 2 つの test で使ったとき、
    _login_and_get_storage_state は 1 回だけ呼ばれること (2 回目は cache hit)。
    Playwright 実機不要: monkeypatch.setattr で fake に差し替え。
    call_count を stdout に出力して pytester から assert する。

(b) pytest_runtest_makereport 経由で artifact path が report.md に乗ること:
    pytester で本物の pytest セッションを走らせ、
    FAIL した test の report.md 詳細セクションに request.har / trace.zip の
    パスが含まれることを確認する (Codex Major-1 artifact 伝搬の直接検証)。
    本物の pwk_evidence fixture lifecycle を使用する。

(c) call phase FAIL 時でも teardown 後の artifact が report.md に反映されること:
    call phase で pytest.fail() する test を pytester で実行し、
    teardown 後に確定した HAR / trace path が report.md に含まれることを assert。

(d) _sessionstarttime の有無で report.md ヘッダ時刻が変わること:
    terminalreporter に _sessionstarttime を持たせた場合とない場合で
    write_report() に渡る started_at が異なることを確認 (AQ Critical-4)。
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# (a) auth cache hit/miss の統合テスト
# ---------------------------------------------------------------------------


def test_auth_cache_hit_miss_via_pytester(pytester, tmp_path: Path):
    """pwk_role_<id> fixture が session を跨いで cache hit し、
    _login_and_get_storage_state が 2 回目以降呼ばれないことを検証する。

    Playwright 実機不要: conftest.py で _login_and_get_storage_state を
    call_count を記録する fake に差し替え。
    call_count を stdout に print して pytester の出力から assert する。
    """
    cfg_path = tmp_path / "scenario.config.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            target:
              base_url: https://example.com
            roles:
              admin:
                label: 管理者
                login:
                  path: /login
                  requires_basic_auth: false
                  fail_if_url_contains: ""
                  fields:
                    email: admin@example.com
                    password: pass
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    # pytester 内の conftest: _login_and_get_storage_state を fake に差し替え、
    # context fixture も MagicMock に override して Playwright 実機を不要にする。
    pytester.makeconftest(
        textwrap.dedent(
            """
            from unittest.mock import MagicMock
            import pytest
            import playwright_kit.fixtures.auth as auth_mod

            _call_count = 0

            def _fake_login(**kwargs):
                global _call_count
                _call_count += 1
                return {"cookies": [{"name": "session", "value": "fake"}], "origins": []}

            @pytest.fixture(scope="session", autouse=True)
            def _patch_login():
                import playwright_kit.fixtures.auth as m
                orig = m._login_and_get_storage_state
                m._login_and_get_storage_state = _fake_login
                yield
                m._login_and_get_storage_state = orig

            @pytest.fixture()
            def context():
                ctx = MagicMock()
                ctx.add_cookies = MagicMock()
                ctx.new_page.return_value = MagicMock()
                return ctx
            """
        )
    )

    pytester.makepyfile(
        textwrap.dedent(
            """
            import playwright_kit.fixtures.auth as auth_mod
            import conftest as conftest_mod

            def test_first_call_cache_miss(pwk_role_admin, context):
                # 1 回目: _login_and_get_storage_state が呼ばれる (cache miss)
                context.add_cookies.assert_called()
                print(f"CALL_COUNT_AFTER_FIRST={conftest_mod._call_count}")

            def test_second_call_cache_hit(pwk_role_admin, context):
                # 2 回目: 同 role なので fake は再呼び出しされない (cache hit)
                # call_count は 1 のまま変わっていないはず
                print(f"CALL_COUNT_AFTER_SECOND={conftest_mod._call_count}")
                assert conftest_mod._call_count == 1, (
                    f"cache hit 失敗: _login_and_get_storage_state が "
                    f"{conftest_mod._call_count} 回呼ばれた (1 回のみ期待)"
                )
            """
        )
    )
    res = pytester.runpytest("-v", "-s", f"--pwk-config={cfg_path}")
    # 両方 passed であることを確認
    res.assert_outcomes(passed=2)
    # call_count が 1 であることを stdout から確認 (cache hit 証明)
    res.stdout.fnmatch_lines(["*CALL_COUNT_AFTER_FIRST=1*"])
    res.stdout.fnmatch_lines(["*CALL_COUNT_AFTER_SECOND=1*"])


# ---------------------------------------------------------------------------
# (b) artifact path が report.md に反映されること (本物の pwk_evidence lifecycle)
# ---------------------------------------------------------------------------


def test_makereport_populates_artifact_paths_in_report(pytester, tmp_path: Path):
    """本物の pwk_evidence fixture lifecycle を使い、
    FAIL した test の report.md 詳細セクションに
    request.har / trace.zip パスが含まれることを検証する。

    Codex Major-1 artifact 伝搬修正 (3回目) の直接検証:
    - call phase で pytest.fail() して FAIL test を作る
    - pwk_evidence fixture の context は MagicMock で Playwright 実機不要
    - teardown で confirm_har() が動き、har_path / trace_path が確定
    - _collect_entries() の teardown merge により report.md に artifact path が出る
    """
    out_dir = tmp_path / "out"

    pytester.makeconftest(
        textwrap.dedent(
            f"""
            import pytest
            from unittest.mock import MagicMock, patch
            from pathlib import Path

            DUMMY_CASE_DIR = Path({str(tmp_path)!r}) / "case"

            @pytest.fixture(scope="session", autouse=True)
            def _patch_browser_context_args(pytestconfig):
                \"\"\"browser_context_args が record_har_path を設定する際、
                ダミーの case_dir を使い HAR ファイルを事前に作成する。\"\"\"
                yield

            @pytest.fixture()
            def context():
                \"\"\"Playwright 実機不要: MagicMock context を返す。
                tracing.stop() 呼び出し時に trace.zip を実際に作成する。\"\"\"
                ctx = MagicMock()

                def fake_tracing_stop(path=None):
                    if path:
                        p = Path(path)
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(b"PK")  # ダミーの zip

                ctx.tracing.stop.side_effect = fake_tracing_stop
                return ctx

            @pytest.fixture()
            def page():
                return MagicMock()
            """
        )
    )

    # pwk_evidence fixture を本物のまま使い、call で pytest.fail() して FAIL させる。
    # HAR ファイルは browser_context_args の record_har_path で指定されるが、
    # MagicMock context では実際には作られないため、pwk_evidence の finalizer 前に
    # har_path を手動で作成する fixture を挟む。
    pytester.makepyfile(
        textwrap.dedent(
            f"""
            import pytest
            from pathlib import Path
            from unittest.mock import MagicMock

            @pytest.fixture(autouse=True)
            def _create_dummy_har(pwk_evidence):
                \"\"\"pwk_evidence の har_path に実体ファイルを作成し、
                confirm_har() が har_relpath を設定できるようにする。\"\"\"
                if pwk_evidence.har_path:
                    pwk_evidence.har_path.parent.mkdir(parents=True, exist_ok=True)
                    pwk_evidence.har_path.write_text("{{}}", encoding="utf-8")
                yield

            def test_fail_with_evidence(pwk_evidence):
                \"\"\"call phase で FAIL させ、teardown 後の artifact が
                report.md に乗ることを確認するためのダミー FAIL test。\"\"\"
                pytest.fail("intentional failure for artifact propagation test")
            """
        )
    )

    res = pytester.runpytest("-v", "--pwk-out-dir", str(out_dir))
    # 1 件 FAIL であることを確認
    res.assert_outcomes(failed=1)

    # report.md が生成されていること
    report_files = list(out_dir.glob("report.md"))
    assert report_files, f"report.md が見つかりません: {list(out_dir.iterdir())}"
    content = report_files[0].read_text(encoding="utf-8")

    # FAIL 詳細セクションに test 名が出ていること
    assert "test_fail_with_evidence" in content, "test 名が report.md に含まれていない"

    # artifact path (request.har) が report.md に含まれていること
    # Codex Major-1 修正: teardown merge により call phase FAIL でも HAR path が乗る
    assert "request.har" in content, (
        f"request.har が report.md に含まれていない。\n"
        f"report.md 内容:\n{content}"
    )


# ---------------------------------------------------------------------------
# (c) call phase FAIL 時でも teardown 後の artifact が report.md に反映されること
# ---------------------------------------------------------------------------


def test_artifact_propagation_call_to_teardown(pytester, tmp_path: Path):
    """call phase で FAIL した test の report.md に
    teardown 後に確定した HAR path が含まれることを直接 assert する。

    これは _collect_entries() の teardown merge ロジック (Codex Major-1 / 3回目)
    が正しく動いているかの直接検証テストである。
    """
    out_dir = tmp_path / "out"

    pytester.makeconftest(
        textwrap.dedent(
            """
            import pytest
            from unittest.mock import MagicMock
            from pathlib import Path

            @pytest.fixture()
            def context():
                ctx = MagicMock()
                def fake_tracing_stop(path=None):
                    if path:
                        p = Path(path)
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(b"PK")
                ctx.tracing.stop.side_effect = fake_tracing_stop
                return ctx

            @pytest.fixture()
            def page():
                return MagicMock()
            """
        )
    )

    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from pathlib import Path

            @pytest.fixture(autouse=True)
            def _ensure_har(pwk_evidence):
                # HAR ファイルを事前作成して confirm_har() が har_relpath をセットできるようにする
                if pwk_evidence.har_path:
                    pwk_evidence.har_path.parent.mkdir(parents=True, exist_ok=True)
                    pwk_evidence.har_path.write_text("{}", encoding="utf-8")
                yield

            def test_call_fail_artifact_propagation(pwk_evidence):
                # call phase で FAIL させる
                # teardown で pwk_evidence.confirm_har() が呼ばれ har_relpath が確定
                # _collect_entries の teardown merge で report.md に har path が乗るはず
                pytest.fail("call phase failure to test artifact propagation")
            """
        )
    )

    res = pytester.runpytest("-v", "--pwk-out-dir", str(out_dir))
    res.assert_outcomes(failed=1)

    report_files = list(out_dir.glob("report.md"))
    assert report_files, f"report.md が生成されていません: {list(out_dir.iterdir())}"
    content = report_files[0].read_text(encoding="utf-8")

    # FAIL 詳細セクションに request.har パスが含まれること
    assert "request.har" in content, (
        "Codex Major-1 (3回目) artifact 伝搬修正が機能していない: "
        "call phase FAIL の report.md に request.har パスが含まれていない\n"
        f"report.md 内容:\n{content}"
    )


# ---------------------------------------------------------------------------
# (d) _sessionstarttime の有無で write_report の started_at が変わること
# ---------------------------------------------------------------------------


def test_sessionstarttime_affects_report_header(tmp_path: Path):
    """terminalreporter._sessionstarttime がある場合とない場合で
    write_report に渡る started_at が異なることを確認する (AQ Critical-4)。

    pytester ではなく直接 pytest_terminal_summary を呼んで
    write_report の引数をモックで検証する。
    """
    import datetime as _dt
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from playwright_kit.pytest_plugin import pytest_terminal_summary

    def _make_rep(nodeid="t::ok", outcome="passed", when="call"):
        return SimpleNamespace(
            nodeid=nodeid,
            head_line=nodeid.split("::")[-1],
            outcome=outcome,
            when=when,
            duration=1.0,
            user_properties=[],
            longrepr=None,
        )

    # _sessionstarttime あり: fromtimestamp で started_at が決まる
    fixed_ts = _dt.datetime(2026, 1, 1, 10, 0, 0).timestamp()
    tr_with_ts = MagicMock()
    tr_with_ts.stats = {"passed": [_make_rep()]}
    tr_with_ts._sessionstarttime = fixed_ts

    config = MagicMock()
    config.getoption.return_value = str(tmp_path / "with_ts")
    config._pwk_config = None

    captured_started: list[_dt.datetime] = []

    orig_write = __import__(
        "playwright_kit.pytest_report", fromlist=["write_report"]
    ).write_report

    def _capture_write(entries, *, out_dir, started_at, finished_at, **kwargs):
        captured_started.append(started_at)
        return orig_write(
            entries,
            out_dir=out_dir,
            started_at=started_at,
            finished_at=finished_at,
            **kwargs,
        )

    with patch("playwright_kit.pytest_plugin.write_report", side_effect=_capture_write):
        pytest_terminal_summary(tr_with_ts, exitstatus=0, config=config)

    assert len(captured_started) == 1
    expected = _dt.datetime.fromtimestamp(fixed_ts)
    assert captured_started[0] == expected, (
        f"_sessionstarttime あり: started_at={captured_started[0]} != {expected}"
    )

    # _sessionstarttime なし: now() - sum(duration) で started_at が近似される
    tr_no_ts = MagicMock()
    tr_no_ts.stats = {"passed": [_make_rep()]}
    del tr_no_ts._sessionstarttime  # 属性を削除

    config2 = MagicMock()
    config2.getoption.return_value = str(tmp_path / "no_ts")
    config2._pwk_config = None

    captured_started2: list[_dt.datetime] = []

    def _capture_write2(entries, *, out_dir, started_at, finished_at, **kwargs):
        captured_started2.append(started_at)
        return orig_write(
            entries,
            out_dir=out_dir,
            started_at=started_at,
            finished_at=finished_at,
            **kwargs,
        )

    before = _dt.datetime.now()
    with patch("playwright_kit.pytest_plugin.write_report", side_effect=_capture_write2):
        pytest_terminal_summary(tr_no_ts, exitstatus=0, config=config2)
    after = _dt.datetime.now()

    assert len(captured_started2) == 1
    # fallback: now() - duration(1.0s) なので before - 2s < started_at < after
    assert before - _dt.timedelta(seconds=2) <= captured_started2[0] <= after, (
        f"_sessionstarttime なし fallback: started_at={captured_started2[0]} が範囲外"
    )
    # _sessionstarttime あり の結果 (2026-01-01) とは明確に異なる
    assert captured_started2[0] != expected
