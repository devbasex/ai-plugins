"""使い捨ての PostgreSQL を `initdb` と `pg_ctl` で起こすテストの支援（fixture `start_postgres` の実体）。

GitHub Actions の ubuntu の Runner は PostgreSQL を入れてある（サービスは止まっている）。手元に無ければ飛ばし、
CI（環境変数 `CI`）では落とす。`pyproject.toml` の pytest の `pythonpath` がこのディレクトリを持つので、
テストは `import postgres_server` で読む。
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest


def find_bin() -> Path | None:
    """`pg_ctl` のあるディレクトリ。PATH を先に見て、無ければ入っている版の最後のもの。"""
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    candidates = sorted(glob.glob("/usr/lib/postgresql/*/bin/pg_ctl"))
    return Path(candidates[-1]).parent if candidates else None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def start_postgres(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, str]]:
    """PostgreSQL を起こし、（bin のディレクトリ, 末尾の DB 名を付けない URL）を渡して、抜けたら止める。"""
    bindir = find_bin()
    # initdb は root では動かない
    missing = "PostgreSQL（pg_ctl）が無い" if bindir is None else "root では initdb を打てない" if os.geteuid() == 0 else ""
    if missing:
        if os.environ.get("CI"):
            pytest.fail(f"CI で PostgreSQL を起こせない: {missing}")
        pytest.skip(missing)
    data = tmp_path_factory.mktemp("pgdata")
    port = free_port()
    subprocess.run([bindir / "initdb", "-D", data, "-U", "ndf", "--auth=trust", "-E", "UTF8"], check=True, capture_output=True)
    options = f"-c listen_addresses=127.0.0.1 -p {port} -k {data}"
    subprocess.run([bindir / "pg_ctl", "-D", data, "-o", options, "-l", data / "log", "-w", "start"], check=True, capture_output=True)
    try:
        yield bindir, f"postgresql://ndf@127.0.0.1:{port}"
    finally:
        subprocess.run([bindir / "pg_ctl", "-D", data, "-m", "immediate", "stop"], check=False, capture_output=True)
