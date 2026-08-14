"""scripts/upload_evidence.py の純粋関数をテストする (Drive API は呼ばない)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from upload_evidence import ALLOWED_KINDS, detect_kind, detect_mime, upload


class TestDetectKind:
    def test_zip_is_trace(self):
        assert detect_kind(Path("foo.trace.zip")) == "trace"
        assert detect_kind(Path("simple.zip")) == "trace"

    def test_har(self):
        assert detect_kind(Path("session.har")) == "har"

    def test_video(self):
        assert detect_kind(Path("recording.mp4")) == "video"
        assert detect_kind(Path("recording.webm")) == "video"

    def test_unknown_extension_falls_back_to_any(self):
        assert detect_kind(Path("unknown.bin")) == "any"
        assert detect_kind(Path("README.md")) == "any"


class TestUploadKindValidation:
    """Min-7: Python API として呼ばれた場合の kind 値検査。"""

    def test_invalid_kind_raises_before_drive_call(self, tmp_path):
        # Drive API には触らずに上流で ValueError が出ることを確認
        f = tmp_path / "x.bin"
        f.write_bytes(b"")
        with pytest.raises(ValueError, match="未対応の kind"):
            upload(f, kind="bogus")

    def test_allowed_kinds_set(self):
        assert ALLOWED_KINDS == frozenset({"trace", "har", "video", "any"})


class TestDetectMime:
    """codex Min-3: kind=video でも .webm は video/webm を返す。"""

    def test_webm_returns_video_webm(self):
        assert detect_mime(Path("recording.webm"), "video") == "video/webm"

    def test_mp4_returns_video_mp4(self):
        assert detect_mime(Path("recording.mp4"), "video") == "video/mp4"

    def test_har_returns_application_json(self):
        assert detect_mime(Path("session.har"), "har") == "application/json"

    def test_unknown_extension_falls_back_to_kind_default(self):
        assert detect_mime(Path("evidence.bin"), "any") == "application/octet-stream"
        assert detect_mime(Path("evidence.bin"), "trace") == "application/zip"
