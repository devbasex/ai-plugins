"""進行表の DB（#3092 の決定 2・15・16・17・18・20・I2・I14）。

進行表・定義の改訂・進行の事象の 3 つの表と表の版の 1 行を、SQLAlchemy Core で SQLite と PostgreSQL の両方へ
同じコードで読み書きする。置き場は URL（`NDF_PROGRESS_DB`）で受け、無ければチェックアウトの状態の置き場
（`<git の共通ディレクトリ>/ndf/state`）の下の SQLite のファイルを使う。どの worktree から打っても同じファイルになる。

事象を書くのは `append_events` だけで、書く直前に進行表のモデルの書き手の表（I6）と遷移の判定を呼ぶ。事象と
定義の改訂を更新・削除する関数は作らない（I2・I15）。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, Text, create_engine, event, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

import clock
import limits
import progress_lanes as plr
import progress_model as pm

SCHEMA_VERSION = 1
ENV_DB = "NDF_PROGRESS_DB"
DEFAULT_DB = "progress/ndf-progress.sqlite"
BUSY_TIMEOUT_SECONDS = limits.seconds("progress.busy-timeout")  # 決定 18

md = MetaData()
TABLES = Table(
    "ndf_progress_tables",
    md,
    Column("id", String(512), primary_key=True),
    Column("level", String(16), nullable=False),
    Column("repo", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("parent_id", String(512), nullable=True),
    Column("created_at", String(40), nullable=False),
)
DEFINITIONS = Table(
    "ndf_progress_definitions",
    md,
    Column("table_id", String(512), primary_key=True),
    Column("rev", Integer, primary_key=True, autoincrement=False),
    Column("definition", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)
EVENTS = Table(
    "ndf_progress_events",
    md,
    Column("table_id", String(512), primary_key=True),
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("rev", Integer, nullable=True),
    Column("row_key", Text, nullable=True),
    Column("kind", String(32), nullable=False),
    Column("state", String(16), nullable=True),
    Column("actor", String(16), nullable=False),
    Column("reason", Text, nullable=True),
    Column("payload", JSON, nullable=True),
    Column("dedupe_key", String(512), nullable=True, unique=True),
    Column("at", String(40), nullable=False),
)
_LEVEL_WORD = {"sprint": "スプリント", "lane": "レーン", "plan": "計画"}
META = Table("ndf_progress_meta", md, Column("key", String(64), primary_key=True), Column("value", Text, nullable=False))


class Unreadable(Exception):
    """DB を開けない・置き場が決まらない・表の版が新しい（終了コード 2）。"""


class Conflict(Exception):
    """同時の追記で番号がぶつかった（I2）。読み直して判定し直す。"""


@dataclass
class Store:
    engine: Engine
    url: str
    shown: str  # パスワードを隠した URL

    @property
    def is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    @property
    def sqlite_path(self) -> Optional[Path]:
        return Path(self.engine.url.database) if self.is_sqlite and self.engine.url.database else None


def default_url(root: Path) -> str:
    """`NDF_PROGRESS_DB` か、チェックアウトの状態の置き場の既定の SQLite の URL（決定 2）。"""
    got = os.environ.get(ENV_DB)
    if got:
        return got
    import state_home

    if state_home.explicit_home() is None and state_home.find_git_dir(Path(root)) is None:
        raise Unreadable(f"git の外で打った。{ENV_DB} を渡すか git の中で打つ")
    return f"sqlite:///{state_home.checkout_home(Path(root)) / DEFAULT_DB}"


def progress_repo_key(root: Path) -> str:
    """進行表の ID に入れるリポジトリの識別（origin の URL。無ければメインディレクトリの絶対パス）。どの worktree でも同じ値。"""
    import subprocess

    import state_home

    p = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip()
    got = state_home.find_git_dir(Path(root))
    return str(got[1].parent if got else Path(root).resolve())


def _hide(url: str) -> str:
    return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", url)


def connect(url: str) -> Store:
    """進行表の DB へつなぎ、表が無ければ作り、表の版を確かめる。"""
    shown = _hide(url)
    try:
        if url.startswith("sqlite:///"):
            Path(url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
    except Exception as e:  # noqa: BLE001  URL の誤り・ドライバが無い（psycopg）
        raise Unreadable(f"{shown} へつなげない（{type(e).__name__}: {e}）") from None
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _rec):  # WAL と書き込みの待ち（決定 18）
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
            cur.close()

    try:
        md.create_all(engine)
        with engine.begin() as conn:
            got = conn.execute(select(META.c.value).where(META.c.key == "schema_version")).scalar()
            if got is None:
                conn.execute(META.insert().values(key="schema_version", value=str(SCHEMA_VERSION)))
            elif int(got) > SCHEMA_VERSION:
                raise Unreadable(f"表の版 {got} がこの NDF の版 {SCHEMA_VERSION} より新しい。新しい NDF で打つ")
    except Unreadable:
        raise
    except Exception as e:  # noqa: BLE001
        raise Unreadable(f"{shown} の表を作れない（{type(e).__name__}）") from None
    return Store(engine, url, shown)


@contextlib.contextmanager
def transaction(store: Store) -> Iterator[Connection]:
    """1 つのトランザクション。例外で抜ければ書いたものは何も残らない。"""
    with store.engine.begin() as conn:
        yield conn


# --- 読み込み -----------------------------------------------------------------


def _event(row) -> pm.Event:
    return pm.Event(
        kind=row.kind,
        actor=row.actor,
        row_key=row.row_key,
        rev=row.rev,
        state=row.state,
        reason=row.reason,
        payload=row.payload,
        dedupe_key=row.dedupe_key,
        table_id=row.table_id,
        seq=row.seq,
        at=row.at,
    )


def read_progress_events(conn: Connection, table_id: str, after: int = 0) -> list[pm.Event]:
    """1 つの進行表の事象を番号の順に読む。"""
    q = select(EVENTS).where(EVENTS.c.table_id == table_id, EVENTS.c.seq > after).order_by(EVENTS.c.seq)
    return [_event(r) for r in conn.execute(q)]


def progress_table(conn: Connection, table_id: str):
    return conn.execute(select(TABLES).where(TABLES.c.id == table_id)).first()


def children(conn: Connection, parent_id: str) -> list:
    return list(conn.execute(select(TABLES).where(TABLES.c.parent_id == parent_id).order_by(TABLES.c.created_at, TABLES.c.id)))


def definition(conn: Connection, table_id: str, rev: Optional[int] = None) -> tuple[int, dict]:
    """（改訂, 定義のスナップショット）。`rev` を省けば最新。"""
    q = select(DEFINITIONS).where(DEFINITIONS.c.table_id == table_id)
    q = q.where(DEFINITIONS.c.rev == rev) if rev is not None else q.order_by(DEFINITIONS.c.rev.desc())
    row = conn.execute(q.limit(1)).first()
    if row is None:
        raise Unreadable(f"{table_id} の定義の改訂が無い")
    return row.rev, row.definition


def last_seq(conn: Connection, table_id: str) -> int:
    from sqlalchemy import func

    return conn.execute(select(func.coalesce(func.max(EVENTS.c.seq), 0)).where(EVENTS.c.table_id == table_id)).scalar()


# --- 書き込み -----------------------------------------------------------------


def append_events(
    conn: Connection,
    table_id: str,
    drafts: list[pm.EventDraft],
    expect_seq: Optional[int] = None,
    heights: Optional[dict] = None,
    *,
    _row_ended: bool = False,
) -> list[int]:
    """事象を追記する唯一の関数。書く前に書き手の表と遷移を判定する（I2・I6）。

    `expect_seq` は呼ぶ側が読んだ最後の番号（省けば今の最後の番号）。下書きをその次から連番で書き、
    番号がぶつかれば `Conflict` を上げる。重複の鍵が既にある下書きは書かずに、その事象の番号を返す。"""
    level = pm.level_of(table_id)
    events = read_progress_events(conn, table_id)
    seq = last_seq(conn, table_id) if expect_seq is None else expect_seq
    out: list[int] = []
    for d in drafts:
        if d.kind == "row_ended" and not _row_ended:
            raise pm.Refused("row_ended はレーンの行を終える関数だけが書く（決定 17）")
        if d.dedupe_key:
            got = conn.execute(select(EVENTS.c.seq).where(EVENTS.c.dedupe_key == d.dedupe_key)).scalar()
            if got is not None:
                out.append(got)
                continue
        pm.check_writer(level, d.kind, d.actor)
        rev = d.rev if level == "sprint" else None
        rows = pm.fold(events, rev if rev is not None else (pm.latest_rev(events) if level == "sprint" else None))
        pm.check_event(level, rows, d, heights)
        seq += 1
        values = dict(
            table_id=table_id,
            seq=seq,
            rev=d.rev,
            row_key=d.row_key,
            kind=d.kind,
            state=d.resolved_state(),
            actor=d.actor,
            reason=d.reason,
            payload=d.payload,
            dedupe_key=d.dedupe_key,
            at=clock.now_iso("utc"),
        )
        try:
            conn.execute(EVENTS.insert().values(**values))
        except IntegrityError:
            raise Conflict(f"{table_id} の番号 {seq} がぶつかった（I2）") from None
        events.append(
            pm.Event(**{k: v for k, v in values.items() if k not in ("table_id", "seq", "at")}, table_id=table_id, seq=seq, at=values["at"])
        )
        out.append(seq)
    return out


def create_table(
    conn: Connection,
    level: str,
    repo: str,
    name: str,
    parent_id: Optional[str],
    definition: Optional[dict],
    drafts: list[pm.EventDraft],
) -> str:
    """進行表を作り、スプリントなら改訂 1 のスナップショットと最初の事象を書く。同じ ID があれば `Refused`（I1）。"""
    tid = pm.table_id(level, repo, name)
    if progress_table(conn, tid) is not None:
        raise pm.Refused(f"同じ{_LEVEL_WORD[level]}の進行表がある（I1）", current=tid)
    try:
        conn.execute(
            TABLES.insert().values(id=tid, level=level, repo=repo, name=name, parent_id=parent_id, created_at=clock.now_iso("utc"))
        )
    except IntegrityError:
        raise pm.Refused(f"同じ{_LEVEL_WORD[level]}の進行表がある（I1）", current=tid) from None
    if definition is not None:
        conn.execute(DEFINITIONS.insert().values(table_id=tid, rev=1, definition=definition, created_at=clock.now_iso("utc")))
    append_events(conn, tid, drafts, 0)
    return tid


def add_revision(conn: Connection, table_id: str, definition_: dict, drafts: list[pm.EventDraft], heights: Optional[dict] = None) -> int:
    """新しい改訂のスナップショットと、その改訂の事象を書く（決定 16）。前の改訂の行と事象は変わらない。"""
    rev, _ = definition(conn, table_id)
    new = rev + 1
    try:
        conn.execute(DEFINITIONS.insert().values(table_id=table_id, rev=new, definition=definition_, created_at=clock.now_iso("utc")))
    except IntegrityError:
        raise Conflict(f"{table_id} の改訂 {new} がぶつかった") from None
    append_events(conn, table_id, pm.with_rev(drafts, new), None, heights)
    return new


# --- 計画とレーン ---------------------------------------------------------------


def lane_name(row) -> str:
    return row.name.split("/", 1)[1] if "/" in row.name else row.name


def plan_and_lanes(conn: Connection, plan_id: str) -> tuple[dict, dict, dict]:
    """（計画の畳み込み, レーン名 → 順番表, レーン名 → レーンの進行表の ID）。"""
    plan = plr.plan_view(read_progress_events(conn, plan_id))
    lanes, ids = {}, {}
    for row in children(conn, plan_id):
        if row.level != "lane":
            continue
        ln = lane_name(row)
        lanes[ln] = plr.lane_order_rows(read_progress_events(conn, row.id))
        ids[ln] = row.id
    for ln in plan["lanes"]:
        lanes.setdefault(ln, [])
    return plan, lanes, ids


def plan_view_of(conn: Connection, plan_id: str) -> Optional[dict]:
    """計画の進行表の畳み込み。進行表が無ければ None。"""
    return plr.plan_view(read_progress_events(conn, plan_id)) if progress_table(conn, plan_id) is not None else None


def lane_write(conn: Connection, lane_id: str, draft: pm.EventDraft, *, _row_ended: bool = False) -> list[int]:
    """レーンの進行表へ 1 件書く。計画の決まり（I12a・I13・I18〜I21）を同じトランザクションで読み直して判定する。"""
    lane = progress_table(conn, lane_id)
    if lane is None or lane.level != "lane":
        raise pm.Refused(f"レーンの進行表 {lane_id} が無い")
    ln = lane_name(lane)
    payload = {**(draft.payload or {}), "lane": ln}
    draft = pm.EventDraft(**{**draft.__dict__, "payload": payload})
    if lane.parent_id:
        plan, lanes, _ = plan_and_lanes(conn, lane.parent_id)
        errs = plr.check_lane_change(plan, lanes, draft)
        if errs:
            raise pm.Refused("; ".join(errs))
    return append_events(conn, lane_id, [draft], _row_ended=_row_ended)


def sprint_link(conn: Connection, sprint_id: str) -> Optional[tuple[str, int]]:
    """スプリントの進行表が指すレーンの進行表の ID と行の番号。計画の外なら None。"""
    row = progress_table(conn, sprint_id)
    if row is None or not row.parent_id:
        return None
    first = next((e for e in read_progress_events(conn, sprint_id) if e.kind == "created" and e.row_key is None), None)
    n = (first.payload or {}).get("lane_row") if first else None
    return (row.parent_id, int(n)) if n is not None else None


def end_lane_row(conn: Connection, sprint_id: str, outcome: str, actor: str) -> Optional[dict]:
    """スプリントの終わりをレーンの進行表の行へ写す唯一の関数（決定 17）。冪等。

    戻り値は `{"next_row": ...}`（`progress_model.next_row` の結果）。計画の外なら None。"""
    link = sprint_link(conn, sprint_id)
    if link is None:
        return None
    lane_id, n = link
    done = any(e.kind == "row_ended" and (e.payload or {}).get("sprint") == sprint_id for e in read_progress_events(conn, lane_id))
    if not done:
        state = pm.COMPLETED if outcome == "completed" else pm.CANCELLED
        lane_write(
            conn,
            lane_id,
            pm.EventDraft("row_ended", actor, str(n), payload={"row": n, "state": state, "sprint": sprint_id}),
            _row_ended=True,
        )
    lane = progress_table(conn, lane_id)
    if not lane.parent_id:
        return {"next_row": None}
    plan, lanes, _ = plan_and_lanes(conn, lane.parent_id)
    return {"next_row": plr.next_row(lane_name(lane), plan, lanes)}


def carry_dropped(store: Store, sprint_id: str, drop: list[int], reason: str):
    """外した課題を、同じレーンの順番表の次の行へ足す（受け入れ条件 15）。断られたら依頼を開く。"""
    with transaction(store) as conn:
        link = sprint_link(conn, sprint_id)
        if link is None:
            return None, None
        lane_id, n = link
        rows = plr.lane_order_rows(read_progress_events(conn, lane_id))
        pos = next((i for i, r in enumerate(rows) if r["row"] == n), len(rows) - 1) + 1
        new = max(r["row"] for r in rows) + 1
        try:
            lane_write(
                conn,
                lane_id,
                pm.EventDraft("row_added", "conductor", str(new), reason=reason, payload={"row": new, "issues": drop, "position": pos}),
            )
            return None, None
        except pm.Refused as e:
            why = e.reason
    with transaction(store) as conn:
        lane = progress_table(conn, lane_id)
        rid = (
            open_request(conn, lane.parent_id, {"carry": drop, "from": sprint_id}, f"{reason}（{why}）", "workflow")
            if lane.parent_id
            else None
        )
    return why, rid


def open_request(conn: Connection, plan_id: str, body: dict, reason: str, actor: str) -> str:
    """計画の進行表へ依頼を開く（`request_opened`）。依頼の ID は事象の番号。"""
    seqs = append_events(conn, plan_id, [pm.EventDraft("request_opened", actor, None, reason=reason, payload=body)])
    return str(seqs[0])


# --- 実行の鍵の排他（I14・決定 20） -------------------------------------------------


def _advisory_key(table_id: str) -> int:
    return int.from_bytes(hashlib.sha256(table_id.encode()).digest()[:8], "big", signed=True)


@contextlib.contextmanager
def run_lock(store: Store, table_id: str) -> Iterator[None]:
    """同じ進行表の実行の鍵の排他を、DB と同じ範囲に効く形で待たずに取る。取れなければ `Refused`。"""
    if store.is_sqlite:
        import locks

        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", table_id)
        target = Path(f"{store.sqlite_path}.{safe}.lock")
        got = locks.try_exclusive(target)
        if got is None:
            raise pm.Refused("同じ進行表の実行が動いている（I14）")
        with got:
            yield
        return
    conn = store.engine.connect()
    try:
        key = _advisory_key(table_id)
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
            raise pm.Refused("同じ進行表の実行が動いている（I14）")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    finally:
        conn.close()
