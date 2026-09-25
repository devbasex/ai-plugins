"""google-drive の `gdrive_fetch.py` の公開範囲と引数の扱い（#879）。

Google の API は呼ばない。認証とクライアントを差し替え、送られる要求だけを見る。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "gdrive_fetch.py"


class _Call:
    def __init__(self, log, name, kwargs):
        self._log, self._name, self._kwargs = log, name, kwargs

    def execute(self):
        self._log.append((self._name, self._kwargs))
        return {"id": "F1", "webViewLink": "https://drive/F1"}


class _Resource:
    def __init__(self, log, prefix):
        self._log, self._prefix = log, prefix

    def create(self, **kwargs):
        return _Call(self._log, f"{self._prefix}.create", kwargs)


class _Service:
    def __init__(self, log):
        self._log = log

    def files(self):
        return _Resource(self._log, "files")

    def permissions(self):
        return _Resource(self._log, "permissions")


@pytest.fixture
def gdrive(monkeypatch):
    log: list = []
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda *a, **k: _Service(log)
    http = types.ModuleType("googleapiclient.http")
    http.MediaFileUpload = lambda path: path
    http.MediaIoBaseDownload = object
    auth = types.ModuleType("google_auth")
    auth.get_credentials = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "googleapiclient", types.ModuleType("googleapiclient"))
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http)
    monkeypatch.setitem(sys.modules, "google_auth", auth)
    spec = importlib.util.spec_from_file_location("gdrive_fetch_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.calls = log
    return module


def _run(gdrive, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["gdrive_fetch.py", *argv])
    gdrive.main()


def test_an_upload_is_private_by_default(gdrive, monkeypatch, tmp_path):
    target = tmp_path / "a.png"
    target.write_bytes(b"x")
    _run(gdrive, monkeypatch, "--upload", str(target))
    names = [name for name, _ in gdrive.calls]
    assert names == ["files.create"]


def test_public_grants_reading_to_anyone_with_the_link(gdrive, monkeypatch, tmp_path):
    target = tmp_path / "a.png"
    target.write_bytes(b"x")
    _run(gdrive, monkeypatch, "--upload", str(target), "--public")
    perms = [kw for name, kw in gdrive.calls if name == "permissions.create"]
    assert perms == [{"fileId": "F1", "body": {"type": "anyone", "role": "reader"}}]


def test_missing_arguments_end_with_a_nonzero_status(gdrive, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(gdrive, monkeypatch)
    assert e.value.code == 2
