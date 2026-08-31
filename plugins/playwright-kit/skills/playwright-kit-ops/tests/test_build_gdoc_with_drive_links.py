"""scripts/build_gdoc_with_drive_links.py の置換をテストする (Drive API は呼ばない)。

report.md の文面は ``playwright_kit.pytest_report.render_markdown`` を実際に呼んで得る。
文字列を書き写すと、report の書き方が変わったときにテストが追随せず、置換側と
噛み合わない状態が再び起きる (issue #81)。

Drive 上のファイル一覧は引数で差し替える。ここでの mapping は
``list_folder_files()`` が返す {run-id フォルダからの相対パス: file_id}。
"""

from __future__ import annotations

import datetime as _dt

from playwright_kit.pytest_report import PwkTestEntry, render_markdown

from build_gdoc_with_drive_links import rewrite_links

# case ディレクトリ名は nodeid 由来の slug + sha1[:6] で、``TC-`` 始まりではない
# (playwright_kit/fixtures/evidence.py の _safe_case_slug)。
CASE_DIR = "tests-test-login-py-test-ok-1a2b3c"
# report.md には評価環境の root からの絶対パスが入る (pytest_plugin.py が
# ``str(ev.case_dir / ev.trace_relpath)`` を user_properties へ積むため)。
ABS_CASE_DIR = f"/work/reports/20260426-120000/{CASE_DIR}"

_STARTED = _dt.datetime(2026, 4, 26, 12, 0, 0)
_FINISHED = _dt.datetime(2026, 4, 26, 12, 0, 5)


def _report_markdown(
    case_dir: str = ABS_CASE_DIR, error_message: str = "AssertionError: boom"
) -> str:
    """report.md の実出力を得る。証跡 2 件 (trace / HAR) を持つ FAIL 1 件。"""
    return render_markdown(
        [
            PwkTestEntry(
                nodeid="tests/test_login.py::test_ok",
                name="test_ok",
                outcome="failed",
                duration_s=1.0,
                error_message=error_message,
                trace_path=f"{case_dir}/trace.zip",
                har_path=f"{case_dir}/request.har",
            )
        ],
        started_at=_STARTED,
        finished_at=_FINISHED,
    )


def _mapping(case_dir_name: str = CASE_DIR) -> dict[str, str]:
    return {
        f"{case_dir_name}/trace.zip": "TRACEID",
        f"{case_dir_name}/request.har": "HARID",
    }


class TestReportRegression:
    """report.md の実出力を入力にした回帰検査。"""

    def test_code_span_evidence_is_rewritten(self):
        md = _report_markdown()
        out, replaced = rewrite_links(md, _mapping())
        assert replaced == 2
        assert "https://drive.google.com/file/d/TRACEID/view" in out
        assert "https://drive.google.com/file/d/HARID/view" in out

    def test_code_span_becomes_a_link_keeping_the_original_path(self):
        md = _report_markdown()
        out, _ = rewrite_links(md, _mapping())
        assert (
            f"- trace: [{ABS_CASE_DIR}/trace.zip]"
            "(https://drive.google.com/file/d/TRACEID/view)"
        ) in out
        # 置換後にコード表記が残らない
        assert f"`{ABS_CASE_DIR}/trace.zip`" not in out

    def test_case_dir_not_starting_with_tc_is_rewritten(self):
        """case ディレクトリ名は ``TC-`` 始まりに限定しない。"""
        assert not CASE_DIR.startswith("TC-")
        _, replaced = rewrite_links(_report_markdown(), _mapping())
        assert replaced == 2

    def test_absolute_path_matches_listing_key_by_suffix(self):
        """report は絶対パス、Drive の一覧は run-id フォルダからの相対パス。"""
        md = _report_markdown()
        assert f"`{ABS_CASE_DIR}/trace.zip`" in md
        assert f"{CASE_DIR}/trace.zip" in _mapping()
        _, replaced = rewrite_links(md, _mapping())
        assert replaced == 2

    def test_nodeid_code_span_is_left_untouched(self):
        """一覧に無い文字列 (テスト識別子) は書き換えない。"""
        md = _report_markdown()
        out, _ = rewrite_links(md, _mapping())
        assert "`tests/test_login.py::test_ok`" in out

    def test_report_is_unchanged_when_listing_is_empty(self):
        md = _report_markdown()
        out, replaced = rewrite_links(md, {})
        assert replaced == 0
        assert out == md


class TestExistingLinkNotation:
    """既存のリンク記法を壊さない。report が出す形ではないため文面は直に書く。"""

    def test_relative_link_is_rewritten(self):
        md = "証跡: [trace](./TC-01/trace.zip)\n"
        out, replaced = rewrite_links(md, {"TC-01/trace.zip": "FID"})
        assert replaced == 1
        assert out == "証跡: [trace](https://drive.google.com/file/d/FID/view)\n"

    def test_png_uses_the_direct_image_url(self):
        md = "![shot](./TC-01/shot.png)\n"
        out, replaced = rewrite_links(md, {"TC-01/shot.png": "IMGID"})
        assert replaced == 1
        assert out == "![shot](https://drive.google.com/uc?id=IMGID)\n"


class TestNonEvidenceIsPreserved:
    def test_external_url_in_code_span_is_left_untouched(self):
        md = "| 1 | `https://example.com/trace.zip` | fatal |\n"
        out, replaced = rewrite_links(md, {"trace.zip": "FID"})
        assert replaced == 0
        assert out == md

    def test_external_url_in_link_notation_is_left_untouched(self):
        md = "[手順](https://example.com/docs/trace.zip)\n"
        out, replaced = rewrite_links(md, {"docs/trace.zip": "FID"})
        assert replaced == 0
        assert out == md

    def test_angle_bracket_link_target_is_left_untouched(self):
        """リンク先を山括弧で囲む書き方は対象にしない (report は出さない書き方)。

        囲まれた候補は mapping のキーと一致しないため、壊れた URL を作らずに
        原文のまま残る。
        """
        md = "証跡: [trace](<./TC-01/trace.zip>)\n"
        out, replaced = rewrite_links(md, {"TC-01/trace.zip": "FID"})
        assert replaced == 0
        assert out == md

    def test_double_backtick_reference_is_left_untouched(self):
        """``x`` は入れ子のリンクになると読めなくなるため対象にしない。"""
        md = "詳細は ``body_check.jsonl`` を参照\n"
        out, replaced = rewrite_links(md, {"body_check.jsonl": "FID"})
        assert replaced == 0
        assert out == md


class TestSuffixMatching:
    def test_longest_matching_key_wins(self):
        """末尾がそろうキーが複数あるときは、要素数の多い方を採る。"""
        md = "- trace: `/root/run-1/case-a/trace.zip`\n"
        mapping = {"trace.zip": "SHORT", "case-a/trace.zip": "LONG"}
        out, replaced = rewrite_links(md, mapping)
        assert replaced == 1
        assert "https://drive.google.com/file/d/LONG/view" in out

    def test_partial_component_is_not_matched(self):
        """区切りの境界を無視した部分一致は採らない。"""
        md = "- trace: `/root/run-1/not-case-a/trace.zip`\n"
        out, replaced = rewrite_links(md, {"case-a/trace.zip": "FID"})
        assert replaced == 0
        assert out == md


class TestFailureMessageIsPreserved:
    """FAIL の詳細に載る失敗メッセージは書き換えない。

    メッセージはテストが出した文字列をそのまま載せる欄で、証跡フィールドではない。
    中のコード片やコード例が一覧のキーと一致しても置換すると、失敗の内容が読めなく
    なる (コードブロックの中ではリンク記法がそのまま文字として出る)。
    """

    # 証跡 2 件に加えて、失敗メッセージが触れる別のケースの証跡も一覧に載っている
    MAPPING = {
        f"{CASE_DIR}/trace.zip": "TRACEID",
        f"{CASE_DIR}/request.har": "HARID",
        "case-a/trace.zip": "OTHERID",
    }

    def test_code_span_in_failure_message_is_left_untouched(self):
        message = "AssertionError: `case-a/trace.zip` が見つからない"
        out, replaced = rewrite_links(
            _report_markdown(error_message=message), self.MAPPING
        )
        assert replaced == 2  # 証跡フィールドの trace / HAR だけ
        assert message in out
        assert "OTHERID" not in out

    def test_parenthesized_path_in_failure_message_is_left_untouched(self):
        """リンク記法ではない丸括弧の中身は候補にしない。"""
        message = "AssertionError: open(case-a/trace.zip) failed"
        out, replaced = rewrite_links(
            _report_markdown(error_message=message), self.MAPPING
        )
        assert replaced == 2
        assert message in out
        assert "OTHERID" not in out
