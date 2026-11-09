"""計画の台帳（#2647）: 並列の計画を続けるのに要る状態を、計画の置き場 `.ndf/parallel/<計画名>/` に持つ。

| ファイル | 中身 | 書き手 |
| --- | --- | --- |
| `ledger.jsonl` | 外から届いた行（依頼・問い・答え・止める指示・リリースの結果・開いた／閉じた・作り直し・役割）。足すだけ | 送り手の CLI・耐久ステップ |
| `checkpoint.json` | 耐久ワークフローが導いた状態と、読み終えた `ledger.jsonl` のバイト位置。書くたびに全体を置き換える | 耐久ステップ `parallel.checkpoint` |

送り手は台帳へ書いてから耐久ワークフローへ知らせる（I1）。耐久ワークフローは台帳の行だけを入力にし、作り直すときは
チェックポイントとその後の行から状態を戻す。行の形は `contracts/forms/parallel-ledger.json` にある。DBOS を import しない。
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import NamedTuple, Optional

import jsonio
import limits
import locks
import parallel_plan as pp
from secret_redact import redact_text
from usage_ledger import ndf_version, now_utc

VERSION = 1
LEDGER = "ledger.jsonl"
CHECKPOINT = "checkpoint.json"
TAKE = ("request", "gate1", "gate2", "ask", "unmatched", "answer", "stop", "left_stopped", "lanes_changed")  # `_Run.take` へ渡す
QUESTIONS = ("gate1", "gate2", "ask", "unmatched")
RECORD = ("opened", "closed", "rebuilt", "role")  # 耐久ワークフローの状態へ反映しない行
KINDS = (*TAKE, "resume", *RECORD)
REBUILD_SEQ = 1_000_000  # 作り直し 1 回ごとの `seq` の基準（決定 5）
RUNNING = ("PENDING", "ENQUEUED")
LOST = ("ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED")
# チェックポイントが持つ `_Run` の値（`seq`・観測の値は持たない。データ構造の `checkpoint.json`）
FIELDS = (
    "lanes", "pending", "questions", "releases", "launch", "held", "answered", "received", "refusals", "stall_until",
    "helper_asked", "held_spawned", "wake", "revive", "revive_at", "stop", "force", "left_stopped", "superseded", "holds",
)  # fmt: skip
SETS = ("answered", "left_stopped", "superseded")


def plan_ledger_path(main: Path, name: str) -> Path:
    return pp.plan_dir(Path(main), name) / LEDGER


def checkpoint_path(main: Path, name: str) -> Path:
    return pp.plan_dir(Path(main), name) / CHECKPOINT


def _redact(value):
    """文字列の値から認証情報を伏せる（I12。`secret_redact` と同じ規則）。"""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    return value


def message_key(msg: dict, topic: str, fallback: str = "") -> tuple[str, str]:
    """送るメッセージの (行の種類, 重複の判定のキー)。送り手と、台帳を書かない古い版のメッセージを足す耐久ステップが
    同じ規則で決める。id の無い知らせ（`left_stopped`・`lanes_changed`）は `fallback`（送り手の冪等の鍵）か乱数で決める。"""
    if topic == "resume":
        n, phase = msg.get("n"), msg.get("phase")
        return "resume", f"resume:confirm:{n}:{msg.get('take') or '-'}" if phase == "confirm" else f"resume:{phase}:{n}"
    kind = str(msg.get("kind") or "")
    if kind == "stop":
        return kind, "stop:force" if msg.get("force") else "stop"
    return kind, f"{kind}:{msg['id']}" if msg.get("id") else f"{kind}:{fallback or uuid.uuid4().hex}"


def append_ledger_row(
    main: Path, name: str, kind: str, key: str, msg: Optional[dict] = None, by: Optional[str] = None, rid: str = ""
) -> bool:
    """行を 1 行足す。同じキーの行があれば何も足さずに False（I2）。書けなければ `OSError`・`LockTimeout`。

    排他の中で重複のキーと末尾の改行を確かめてから `O_APPEND` で書く（I3・決定 2）。改行で終わらない末尾（落ちた書き手の
    切れた行）には改行を足して閉じ、次の行と混ざらないようにする。"""
    p = plan_ledger_path(main, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    msg = _redact(msg) if msg is not None else None
    row = {"v": VERSION, "key": key, "kind": kind, "id": rid or str((msg or {}).get("id") or ""), "at": now_utc(),
           "by": by or "", "ver": ndf_version(), "msg": msg}  # fmt: skip
    with locks.exclusive(p, timeout=limits.seconds("locks.append")):
        data = p.read_bytes() if p.exists() else b""
        if any(isinstance(r, dict) and r.get("key") == key for _, r in _ledger_lines(data, 0)):  # 読めない行は飛ばす（I4）
            return False
        line = (b"\n" if data and not data.endswith(b"\n") else b"") + json.dumps(row, ensure_ascii=False).encode() + b"\n"
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            view = memoryview(line)
            while view:
                view = view[os.write(fd, view) :]
        finally:
            os.close(fd)
    return True


def known(row) -> bool:
    return isinstance(row, dict) and row.get("v") == VERSION and row.get("kind") in KINDS and isinstance(row.get("key"), str)


def _ledger_lines(data: bytes, base: int):
    """`data`（ファイルの `base` バイト目から）の改行で終わる行を (位置, 値) で返す。読めない行の値は None。"""
    pos = 0
    while (end := data.find(b"\n", pos)) >= 0:
        line, at, pos = data[pos:end], base + pos, end + 1
        if line.strip():
            try:
                yield at, json.loads(line)
            except ValueError:
                yield at, None


class Read(NamedTuple):
    rows: list[dict]  # 読めて形の分かる行。`_pos` に位置を持つ
    next: int  # 次に読む位置（改行で終わらない末尾の行の先頭で止まる）
    skipped: list[int]  # 知らない形の行の位置（I5）
    broken: int  # 読めない行の数（I4）


def read_ledger_rows(main: Path, name: str, offset: int = 0) -> Read:
    """`offset` から改行で終わる行を読む。改行で終わらない末尾の行は追記の途中として数えない（排他を取らずに読むため）。"""
    try:
        with open(plan_ledger_path(main, name), "rb") as f:
            size = f.seek(0, os.SEEK_END)
            offset = offset if 0 <= offset <= size else 0
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return Read([], offset, [], 0)
    rows, skipped, broken = [], [], 0
    for at, row in _ledger_lines(data, offset):
        if row is None:
            broken += 1
        elif known(row):
            rows.append({**row, "_pos": at})
        else:
            skipped.append(at)
    return Read(rows, offset + data.rfind(b"\n") + 1 if b"\n" in data else offset, skipped, broken)


def read_at(main: Path, name: str, positions: list[int]) -> tuple[list[dict], list[int]]:
    """位置の行を読み直す。(読める版で分かった行, まだ分からない位置)。"""
    want, got, still = set(positions), [], []
    for row in read_ledger_rows(main, name, 0).rows if want else []:
        if row["_pos"] in want:
            got.append(row)
            want.discard(row["_pos"])
    still = sorted(want)
    return got, still


def load_checkpoint(main: Path, name: str) -> tuple[Optional[dict], Optional[str]]:
    """チェックポイントと警告。読めないものは無いものとして扱い、警告を返す（I6）。"""
    p = checkpoint_path(main, name)
    if not p.exists():
        return None, None
    got = jsonio.read(p, missing=None, broken=None)
    if not isinstance(got, dict) or got.get("v") != VERSION or not isinstance(got.get("offset"), int):
        return None, f"台帳のチェックポイント {p} を読めないため、台帳の全行から戻した"
    return got, None


def save_checkpoint(main: Path, name: str, state: dict) -> None:
    jsonio.write_atomic(pp.plan_dir(Path(main), name) / "checkpoint.json", {"v": VERSION, **state})


def restore(main: Path, name: str) -> dict:
    """作り直しの材料: チェックポイントの状態・その後の行（と読める版で分かった `unread` の行）・作り直しの回数・壊れた行数。"""
    cp, warning = load_checkpoint(main, name)
    every = read_ledger_rows(main, name, 0)
    offset = int((cp or {}).get("offset") or 0)
    late = [r for r in every.rows if r["_pos"] >= offset]
    found, still = read_at(main, name, list((cp or {}).get("unread") or []))
    rows = sorted([*found, *late], key=lambda r: r["_pos"])
    return {
        "state": {k: cp[k] for k in FIELDS if k in cp} if cp else None,
        "published": dict((cp or {}).get("published") or {}),
        "rows": [r for r in rows if r["kind"] not in RECORD],
        "offset": every.next,
        "unread": sorted({*still, *(p for p in every.skipped if p >= offset)}),
        "rebuilds": sum(1 for r in every.rows if r["kind"] == "rebuilt"),
        "broken": every.broken,
        "warning": warning,
    }


def restored_counts(got: dict) -> dict:
    """`start` の結果の `restored`: 戻す答えていない依頼と問いの数・外した行数。"""
    st = got.get("state") or {}
    answered = {*(st.get("answered") or []), *(r["id"] for r in got["rows"] if r["kind"] == "answer")}
    reqs = {r.get("id") for r in st.get("pending") or []} | {r["id"] for r in got["rows"] if r["kind"] == "request"}
    qs = {q.get("id") for q in st.get("questions") or []} | {r["id"] for r in got["rows"] if r["kind"] in QUESTIONS}
    return {"requests": len(reqs - {None, ""}), "questions": len(qs - answered - {None, ""}), "broken": got["broken"]}


def kinds(main: Path, name: str) -> set[str]:
    return {r["kind"] for r in read_ledger_rows(main, name, 0).rows}


def plan_state(main: Path, name: str, status: Optional[str]) -> str:
    """計画の状態: `running`（動いている）・`lost`（失われた）・`closed`（閉じた）・`legacy`（台帳の無い ERROR）・`fresh`（未開始）。"""
    if status in RUNNING:
        return "running"
    if status == "SUCCESS":
        return "closed"
    seen = kinds(main, name)
    if "closed" in seen:
        return "closed"
    if status is not None:
        return "lost" if "opened" in seen else "legacy"
    progress = seen - {"opened", "role"} or checkpoint_path(main, name).exists()
    return "lost" if "opened" in seen and progress else "fresh"


def accepts(state: str) -> bool:
    """依頼・問い・答えを受け付ける計画か（動いているか失われた。決定 8）。"""
    return state in ("running", "lost")


def published(main: Path, name: str, key: str):
    """チェックポイントに控えた答え・知らせ・範囲のイベント（作り直しの前に立てたもの。決定 6）。無ければ None。"""
    cp, _ = load_checkpoint(main, name)
    return ((cp or {}).get("published") or {}).get(key)


def pending_questions(main: Path, name: str) -> list[dict]:
    """台帳から読む答えていない問い（チェックポイントの問いと、その後に届いた問い）。失われた計画の問いの一覧に使う。
    台帳を開いていない計画（台帳を読まない途中の記録）は、耐久ワークフローが外した問いを区別できないため空を返す。"""
    if "opened" not in kinds(main, name):
        return []
    got = restore(main, name)
    st = got["state"] or {}
    answered = {*(st.get("answered") or []), *(r["id"] for r in got["rows"] if r["kind"] == "answer")}
    out = {q.get("id"): q for q in st.get("questions") or []}
    for r in got["rows"]:
        if r["kind"] in QUESTIONS and r["id"] not in out:
            out[r["id"]] = r.get("msg") or {"id": r["id"], "kind": r["kind"]}
    return [q for rid, q in out.items() if rid not in answered]


def ledger_report(main: Path, name: str, waiting: list[tuple[str, str]]) -> dict:
    """`wait` の `metrics` へ足す項目: 版の違い（I5 と条件 13）・知らない形の行・壊れた行・台帳に無い待ち（条件 14）。"""
    try:
        got = read_ledger_rows(main, name, 0)
        raw = Path(plan_ledger_path(main, name)).read_bytes() if got.skipped else b""
    except OSError:
        return {}
    out: dict = {}
    lanes = {r["by"]: r["ver"] for r in got.rows if r.get("by") and r["by"] != "organizer" and r.get("ver")}
    if any(v != ndf_version() for v in lanes.values()):
        out["versions"] = {"organizer": ndf_version(), "lanes": lanes}
    if got.skipped:
        ids = [str(row.get("id") or "") for at, row in _ledger_lines(raw, 0) if at in set(got.skipped) and isinstance(row, dict)]
        out.update(unknown=len(got.skipped), unknown_ids=[i for i in ids if i])
    if got.broken:
        out["broken"] = got.broken
    have = {r["id"] for r in got.rows}
    if orphans := sorted({rid for _, rid in waiting if rid not in have}):
        out["orphans"] = orphans
    return out


class RunState:
    """`_Run` が継ぐ: 耐久ワークフローの値をチェックポイントの形へ出し（`state`）、作り直しで戻す（`adopt`・`apply`）。

    `_Run` は `take`・`resume`・`publish_questions`・`pause` と、`FIELDS` の値・`order`・`lanes`・`seq`・`current` を持つ。"""

    def init_run_state(self, ledgered: bool) -> None:
        self.ledgered = ledgered  # 先頭の `patched("parallel-ledger")`。偽なら台帳を読まず書かない（決定 7）
        self.offset = 0  # 読み終えた ledger.jsonl のバイト位置
        self.unread: list[int] = []  # 知らない形として飛ばした行の位置
        self.published: dict = {}  # 立てた答え・知らせ・範囲のイベント（決定 6）
        self.saved: Optional[str] = None  # 最後に書いたチェックポイント（比べるための JSON）

    def state(self) -> dict:
        out = {k: sorted(getattr(self, k)) if k in SETS else getattr(self, k) for k in FIELDS}
        return {**out, "offset": self.offset, "unread": list(self.unread), "published": dict(self.published)}

    def adopt(self, got: Optional[dict]) -> None:
        """`restore` の値を戻し（設計の「作り直す」の順）、`running` と戻した問い・流れているリリースの知らせを立てる。
        `got` が None（台帳を読まない途中の記録）なら `running` を立てるだけにする。"""
        if got is None:
            self.pause("running")
            return
        st = got.get("state")
        if st:
            self._adopt_state(st)
        elif got.get("rebuilds"):  # チェックポイントの無い作り直し: 一覧に居るレーンは起動済みと読む（I10）
            self._adopt_listed_lanes(got.get("ls") or [])
        self._adopt_releases(got.get("release_dirs") or [0])
        self.seq = int(got.get("rebuilds") or 0) * REBUILD_SEQ  # 作り直しをまたいで増え続ける（I8・決定 5）
        self.offset, self.unread, self.published = (
            int(got.get("offset") or 0),
            list(got.get("unread") or []),
            dict(got.get("published") or {}),
        )
        self.pause("running")
        for row in got.get("rows") or []:
            self.apply(row)
        if self.questions:
            self.publish_questions()
        if self.current is not None:
            self.pause("notice", lanes=[], attach=[])

    def _adopt_state(self, st: dict) -> None:
        """チェックポイントの `FIELDS` の値とレーンを戻す。"""
        for k in FIELDS:
            if k in st:
                setattr(self, k, set(st[k]) if k in SETS else st[k])
        self.lanes = {n: dict((st.get("lanes") or {}).get(n) or {}) for n in self.order}

    def _adopt_listed_lanes(self, names: list) -> None:
        """一覧に居るレーンを起動済みと読む（I10）。"""
        for n in names:
            if n in self.lanes:
                self.lanes[n].setdefault("started", "restored")

    def _adopt_releases(self, release_dirs: list) -> None:
        """流れているリリースを決め、置き場のある連番の欠番を `lost` で埋める（I9）。"""
        self.current = next((r for r in self.releases if r.get("state") in ("presented", "confirmed")), None)
        have = {r.get("n") for r in self.releases}
        self.releases += [{"n": n, "state": "lost"} for n in range(1, max(release_dirs) + 1) if n not in have]
        self.releases.sort(key=lambda r: int(r.get("n") or 0))  # 置き場のある連番を使い回さない（I9）

    def receive(self, msg, topic: str) -> None:
        """受け取り。台帳の計画は台帳の行だけを入力にし、メッセージは読みを早める知らせとして扱う（決定 3）。"""
        if not self.ledgered:
            if topic == "inbox":
                self.take(msg)
            elif isinstance(msg, dict):
                self.resume(msg)
            return
        got = self.read_ledger(self.main, self.name, self.offset, msg, topic)
        self.offset, self.unread = got["next"], sorted({*self.unread, *got["skipped"]})
        for row in got["rows"]:
            self.apply(row)

    def save(self) -> None:
        """ループの 1 周の終わり: 状態が前に書いたものと違えばチェックポイントを書く（耐久ステップ `parallel.checkpoint`）。"""
        if self.ledgered and self.fingerprint() is not None:
            self.write_checkpoint(self.main, self.name, self.state())

    def apply(self, row: dict) -> None:
        msg = row.get("msg")
        if not isinstance(msg, dict):
            return
        if row.get("kind") == "resume":
            self.resume(msg)
        else:
            self.take(msg)

    def fingerprint(self) -> Optional[str]:
        """書くべきチェックポイント（前に書いたものと同じなら None）。"""
        blob = json.dumps(self.state(), ensure_ascii=False, sort_keys=True, default=str)
        if blob == self.saved:
            return None
        self.saved = blob
        return blob


# ---- 耐久ワークフローの外の入口と耐久ステップの中身（`parallel_run` の行数を上限に収めるためここに置く） ----


def open_run(main: Path, name: str, retired: Optional[Path]) -> None:
    """耐久ワークフローを始める直前: 失われた計画なら作り直しの行（`rebuilt:<回>`）を足し、開いた行を足す（E1・E7）。"""
    if retired is not None or plan_state(main, name, None) == "lost":
        n = sum(1 for r in read_ledger_rows(main, name).rows if r["kind"] == "rebuilt") + 1
        append_ledger_row(main, name, "rebuilt", f"rebuilt:{n}", {"retired": str(retired or "")}, by="organizer")
        print(f"parallel: 耐久ワークフローを作り直す（{n} 回目。退避: {retired or '記録が無い'}）", file=sys.stderr, flush=True)
    append_ledger_row(main, name, "opened", "opened", by="organizer")


def close_ledger(main: Path, name: str) -> None:
    """計画の終わりに閉じた行を足す（I11）。書けなくても計画の終わりは止めない（耐久ワークフローの状態が SUCCESS になる）。"""
    try:
        append_ledger_row(main, name, "closed", "closed", by="organizer")
    except OSError as exc:
        print(f"parallel: 台帳へ閉じた行を書けない: {exc}", file=sys.stderr, flush=True)


def restore_run(main: Path, name: str, ls: list[str]) -> dict:
    """耐久ステップ `parallel.restore` の中身: `restore` に一覧に居るレーンと置き場のあるリリースの連番を足し、run.log へ 1 行出す。"""
    got = restore(main, name)
    rel = pp.plan_dir(main, name) / "releases"
    got["release_dirs"] = sorted(int(d.name) for d in rel.iterdir() if d.is_dir() and d.name.isdigit()) if rel.is_dir() else []
    got["ls"] = ls
    if got["rebuilds"] or got["state"] or got["rows"]:
        n = restored_counts(got)
        line = f"作り直し {got['rebuilds']} 回・依頼 {n['requests']}・問い {n['questions']}・外した行 {n['broken']}"
        print(f"parallel: 台帳から戻した（{line}）{'。' + got['warning'] if got['warning'] else ''}", file=sys.stderr, flush=True)
    return got


def read_run(main: Path, name: str, offset: int, msg, topic: str) -> dict:
    """耐久ステップ `parallel.ledger-read` の中身。受け取ったメッセージが台帳に無ければ（台帳を書かない古い版の送り手）先に足す（決定 3）。"""
    if isinstance(msg, dict):
        kind, key = message_key(msg, topic)
        try:
            append_ledger_row(main, name, kind, key, msg)
        except OSError as exc:  # 足せないメッセージは行として渡し、受け取りを落とさない
            print(f"parallel: 受けたメッセージを台帳へ足せない: {exc}", file=sys.stderr, flush=True)
            return {"rows": [{"kind": kind, "msg": msg}], "next": offset, "skipped": [], "broken": 0}
    got = read_ledger_rows(main, name, offset)
    return {"rows": [r for r in got.rows if r["kind"] not in RECORD], "next": got.next, "skipped": got.skipped, "broken": got.broken}
