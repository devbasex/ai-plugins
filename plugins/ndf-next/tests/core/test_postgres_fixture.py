"""現状固定: durable URL の pg_url から共通化した PostgreSQL の起動契約。"""

import contextlib
import socket
from pathlib import Path

import postgres_server as postgres
import pytest


def test_pg_bin_prefers_path_then_last_installed_version(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    monkeypatch.setattr(postgres.shutil, "which", lambda name: str(bindir / name) if name == "pg_ctl" else None)
    monkeypatch.setattr(postgres.glob, "glob", lambda pattern: pytest.fail("PATH が優先される"))
    assert postgres._pg_bin() == bindir

    monkeypatch.setattr(postgres.shutil, "which", lambda name: None)

    def installed(pattern):
        assert pattern == "/usr/lib/postgresql/*/bin/pg_ctl"
        return ["/usr/lib/postgresql/18/bin/pg_ctl", "/usr/lib/postgresql/17/bin/pg_ctl"]

    monkeypatch.setattr(postgres.glob, "glob", installed)
    assert postgres._pg_bin() == Path("/usr/lib/postgresql/18/bin")
    monkeypatch.setattr(postgres.glob, "glob", lambda pattern: [])
    assert postgres._pg_bin() is None


@pytest.mark.parametrize("ci", ["", "1"])
@pytest.mark.parametrize("missing", ["PostgreSQL（pg_ctl）が無い", "root では initdb を打てない"])
def test_missing_postgres_skips_locally_and_fails_in_ci(monkeypatch, tmp_path_factory, ci, missing):
    monkeypatch.setenv("CI", ci)
    monkeypatch.setattr(postgres, "_pg_bin", lambda: None if missing == "PostgreSQL（pg_ctl）が無い" else Path("/bin"))
    monkeypatch.setattr(postgres.os, "geteuid", lambda: 0)
    error = pytest.fail.Exception if ci else pytest.skip.Exception
    message = f"CI で PostgreSQL を起こせない: {missing}" if ci else missing
    with pytest.raises(error) as caught, postgres.start_postgres(tmp_path_factory):
        pytest.fail("環境不足では起動しない")
    assert str(caught.value) == message


@pytest.mark.parametrize("abort", [False, True])
def test_pg_url_keeps_database_start_options_and_shutdown(monkeypatch, tmp_path_factory, tmp_path, abort):
    bindir = tmp_path / "bin"
    data = tmp_path_factory.mktemp("pgdata")
    port = 5432
    calls = []
    monkeypatch.setattr(postgres, "_pg_bin", lambda: bindir)
    monkeypatch.setattr(postgres, "_free_port", lambda: port)
    monkeypatch.setattr(postgres.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(postgres.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    def mktemp(name):
        assert name == "pgdata"
        return data

    monkeypatch.setattr(tmp_path_factory, "mktemp", mktemp)
    options = f"-c listen_addresses=127.0.0.1 -p {port} -k {data}"
    outcome = pytest.raises(RuntimeError) if abort else contextlib.nullcontext()
    with outcome, postgres.start_postgres(tmp_path_factory) as (_, base):
        # pg_url（core/conftest.py）は末尾に DB 名 /postgres を足す
        assert base + "/postgres" == f"postgresql://ndf@127.0.0.1:{port}/postgres"
        assert calls == [
            ([bindir / "initdb", "-D", data, "-U", "ndf", "--auth=trust", "-E", "UTF8"], {"check": True, "capture_output": True}),
            (
                [bindir / "pg_ctl", "-D", data, "-o", options, "-l", data / "log", "-w", "start"],
                {"check": True, "capture_output": True},
            ),
        ]
        if abort:
            raise RuntimeError("利用側の例外")
    assert calls[-1] == (
        [bindir / "pg_ctl", "-D", data, "-m", "immediate", "stop"],
        {"check": False, "capture_output": True},
    )


def test_free_port_can_be_bound_on_loopback():
    port = postgres._free_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
