"""scripts/ の標準出力が 1 つの JSON であることをテストする (Drive API は呼ばない)。

Drive へのアップロードそのものは差し替え、main() が stdout に書くものだけを見る。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import upload_evidence


def test_upload_evidence_prints_result_as_json(tmp_path: Path, monkeypatch, capsys):
    trace = tmp_path / "trace.zip"
    trace.write_bytes(b"PK")
    fake = {
        "kind": "trace",
        "file_id": "FID",
        "drive_view": "https://drive.google.com/file/d/FID/view",
        "direct_download": None,
        "playwright_trace_viewer": None,
    }
    monkeypatch.setattr(upload_evidence, "upload", lambda *a, **k: fake)
    monkeypatch.setattr(sys, "argv", ["upload_evidence.py", str(trace)])
    assert upload_evidence.main() == 0
    assert json.loads(capsys.readouterr().out) == fake


def test_upload_evidence_missing_file_is_json_error(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["upload_evidence.py", str(tmp_path / "none.zip")])
    assert upload_evidence.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
