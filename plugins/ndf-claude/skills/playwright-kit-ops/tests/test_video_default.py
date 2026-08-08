"""--pwk-no-video オプションと動画デフォルト ON の検証。

pytest-playwright の --video オプションが playwright_kit plugin 経由で
デフォルト 'on' に設定されること、および --pwk-no-video で 'off' に
切り替わることを pytester 経由で検証する。
"""

from __future__ import annotations

import textwrap


def test_pwk_no_video_option_registered(pytester):
    """--pwk-no-video が pytest -h に出ること。"""
    pytester.makepyfile("def test_dummy(): pass\n")
    res = pytester.runpytest("--help")
    out = res.stdout.str()
    assert "--pwk-no-video" in out


def test_video_default_on(pytester):
    """--video 未指定時、playwright_kit が video='on' をデフォルト設定すること。"""
    pytester.makepyfile(
        textwrap.dedent(
            """
            def test_video_config(pytestconfig):
                video = pytestconfig.getoption("video", default=None)
                assert video == "on", f"expected 'on', got {video!r}"
            """
        )
    )
    res = pytester.runpytest("-q")
    res.assert_outcomes(passed=1)


def test_pwk_no_video_sets_off(pytester):
    """--pwk-no-video 指定時、video='off' になること。"""
    pytester.makepyfile(
        textwrap.dedent(
            """
            def test_video_config(pytestconfig):
                video = pytestconfig.getoption("video", default=None)
                assert video == "off", f"expected 'off', got {video!r}"
            """
        )
    )
    res = pytester.runpytest("-q", "--pwk-no-video")
    res.assert_outcomes(passed=1)


def test_explicit_video_flag_takes_precedence(pytester):
    """--video=retain-on-failure を明示指定した場合、pwk が上書きしないこと。"""
    pytester.makepyfile(
        textwrap.dedent(
            """
            def test_video_config(pytestconfig):
                video = pytestconfig.getoption("video", default=None)
                assert video == "retain-on-failure", f"expected 'retain-on-failure', got {video!r}"
            """
        )
    )
    res = pytester.runpytest("-q", "--video=retain-on-failure")
    res.assert_outcomes(passed=1)
