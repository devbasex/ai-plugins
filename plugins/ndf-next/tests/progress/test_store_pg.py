"""PostgreSQL の URL を渡したときに行わないこと（P1-4。#3344 の決定 82）。Factory はこの形で開く。"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from ndf.progress import store
from ndf.progress import backup as progress_backup


def test_open_issues_no_pragma_and_takes_no_backup(pg_url, root):
    sent: list[str] = []

    def record(_conn, _cursor, statement, *_rest):
        sent.append(statement)

    sa.event.listen(sa.Engine, "before_cursor_execute", record)
    try:
        store.open_store(pg_url, root).close()
    finally:
        sa.event.remove(sa.Engine, "before_cursor_execute", record)
    assert sent and not [s for s in sent if "PRAGMA" in s.upper()]
    assert not (root / progress_backup.BACKUP_DIR).exists()
    assert not (root / store.CHECKED_MARK).exists()


def test_restore_is_unsupported_and_touches_nothing(pg_url, root, tmp_path):
    backup = tmp_path / "x.sqlite"
    backup.write_bytes(b"")
    with pytest.raises(store.RestoreUnsupported, match="DB の運用"):
        store.restore(pg_url, root, backup)
    assert store.RestoreUnsupported.kind == "restore_unsupported"


def test_state_changing_operations_take_an_advisory_lock(pg_url, root):
    with store.open_store(pg_url, root) as st, store.run_lock(st, "sprint:r:s1"):
        with st.engine.connect() as conn:
            held = conn.execute(sa.text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")).scalar()
        assert held == 1
