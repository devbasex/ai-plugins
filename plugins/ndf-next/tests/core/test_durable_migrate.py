"""DBOS の版を変えたときの表の移行（B12）。前の版で作った鍵のファイルを今の版で開き、途中の実行が回復する。

見本 `fixtures/dbos-<前の版>-pending.sqlite` は `_durable_old_make.py` で作る（ステップ 1 を記録し、ステップ 2 で落ちた）。
DBOS の版を上げる PR は、上げる前の版で見本を作り直す。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ndf.core import durable

from ._durable_helpers import SRC

FIXTURE = Path(__file__).with_name("fixtures") / "dbos-3.3.0-pending.sqlite"
OPEN_SCRIPT = Path(__file__).with_name("_durable_old_open.py")
OLD_RUN_KEY = "old-dbos-fixture"


def _schema(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT version FROM dbos_migrations").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def old_key_file(root: Path) -> Path:
    durable.prepare_store(root)
    body = root / durable.DBOS_DIR / f"{durable.run_name(OLD_RUN_KEY)}{durable.DB_SUFFIX}"
    body.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE, body)
    return body


def test_the_fixture_is_older_than_the_installed_dbos():
    assert _schema(FIXTURE) < durable.DBOS_SCHEMA


def test_an_old_key_file_is_backed_up_migrated_and_recovered(root, old_key_file, tmp_path):
    old = _schema(old_key_file)
    log = tmp_path / "steps.log"
    done = subprocess.run(
        [sys.executable, str(OPEN_SCRIPT), str(root), OLD_RUN_KEY, str(log)],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.splitlines()[-1]) == [10, 20, 30]
    # 記録したステップ 1 は流し直さない
    assert log.read_text().split() == ["2", "3"]
    backups = list((root / durable.DBOS_DIR / durable.BACKUP_DIR).glob(f"*-schema{old}{durable.DB_SUFFIX}"))
    assert len(backups) == 1
    assert _schema(backups[0]) == old
    recorded = json.loads((root / "format.json").read_text())
    assert recorded[durable.FORMAT_DBOS_KEY] == {"version": durable.DBOS_VERSION, "schema": durable.DBOS_SCHEMA}


def test_a_key_file_newer_than_the_installed_dbos_is_refused(old_key_file):
    conn = sqlite3.connect(old_key_file)
    conn.execute("UPDATE dbos_migrations SET version = ?", (durable.DBOS_SCHEMA + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(durable.StoreFormatNewer):
        durable._backup_before_migration(old_key_file, datetime.now(UTC))
    assert not (old_key_file.parent / durable.BACKUP_DIR).exists()


def test_a_current_key_file_takes_no_backup(old_key_file):
    conn = sqlite3.connect(old_key_file)
    conn.execute("UPDATE dbos_migrations SET version = ?", (durable.DBOS_SCHEMA,))
    conn.commit()
    conn.close()
    assert durable._backup_before_migration(old_key_file, datetime.now(UTC)) is None
