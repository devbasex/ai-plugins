"""起動の承認の記録（JSONL）の読み書きと、質問の中身のハッシュ（#1959）。

承認のコマンド（`launch_approval`）と hook `launch-answer` が同じ規則で読み書きするための共通の処理である。hook の import の表に
収めるため、標準ライブラリと依存の `filelock` だけを読み込み、`ndf.contracts` を読まない（錠の待ちの上限は呼び手が渡す）。
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping, Optional

# 中断した追記の末尾を閉じるバイト列。UTF-8 として復号できない 1 バイトを含むので、閉じた行は JSON を書き終えていても読まれない
TORN_END = b"\xff\n"


def canonical_sha(value: Any) -> str:
    """キーの順にそろえた JSON の SHA-256。字下げとキーの順の違いでは変わらない。"""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def option_labels(questions: Any) -> list[str]:
    """最初の質問が示した選択肢のラベル。hook はこれと同じラベルの回答だけを記録し、承認のコマンドはリクエストに残したこのラベルと照らす。"""
    first = questions[0] if isinstance(questions, list) and questions and isinstance(questions[0], dict) else {}
    options = first.get("options")
    return [o["label"] for o in options if isinstance(o, dict) and isinstance(o.get("label"), str)] if isinstance(options, list) else []


def private_to_me(fd: int) -> bool:
    """開いた記録のファイルが、このユーザーの持ち物の普通のファイルで、ほかのユーザーが読み書きできないか。

    `os.open` の `0o600` は作るときにしか効かないため、復元などで権限が広がった既存のファイルは開いた後に確かめる。
    """
    st = os.fstat(fd)
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077


def whole_rows(path: Path) -> list[dict[str, Any]]:
    """改行で終わる行だけを読み、壊れた行を飛ばす。読めないか所有者だけの形でなければ空（承認していないと扱う）。

    バイト列のまま改行で区切り、行ごとに復号する。1 行の書きかけ（多バイト文字の途中で切れた行など）でほかの行を捨てないためである。
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return []
    try:
        with os.fdopen(fd, "rb") as f:
            if not private_to_me(f.fileno()):
                return []
            data = f.read()
    except OSError:
        return []
    rows = (_parsed_row(line) for line in data.split(b"\n")[:-1])  # 最後の要素は改行で終わらない書きかけか空
    return [row for row in rows if row is not None]


def _parsed_row(line: bytes) -> Optional[dict[str, Any]]:
    """1 行の JSON のオブジェクト。書きかけや壊れた行は None（読む側はロックを取らないため、追記の途中の行がありうる）。"""
    try:
        row = json.loads(line.decode("utf-8"))
    except ValueError:  # noqa: NDF401 ファイル: 読む側はロックを取らないので、追記の途中の行・復号できない行・壊れた行は飛ばす
        return None
    return row if isinstance(row, dict) else None


def file_lock(path: Path, timeout: float) -> Any:
    """記録のファイルの錠（`<ファイル>.lock`）。読んでから書く操作（承認の行・リクエスト・候補の作成と掃除）が取る。"""
    from filelock import FileLock

    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path) + ".lock", timeout=timeout)


# 1 行を書ききれなかったときに、同じ行を書き直す回数の上限（書き直しも 1 回の write で行う）
WRITE_ATTEMPTS = 3


def write_row(path: Path, row: Mapping[str, Any], locked: bool = True) -> None:
    """1 行を追記する。ファイルは所有者だけが読める（0600）。どの書き込みも、1 行分を 1 回の `write` で書く。

    既存のファイルが所有者だけの形でなければ書かずに `PermissionError` を投げる（承認者名とリポジトリのパスをほかのユーザーへ見せない）。
    書きかけの行（中断か書ききれなかった追記）は、次の追記が先頭に付ける `TORN_END` で閉じる。つなげると読み手が両方を捨て、打ち直した
    操作が反映されない。改行だけで閉じると、JSON を書き終えたところで中断した行が、次の無関係な追記で有効になる。

    - `locked` が真（呼び手が錠を持つ。承認の行とリクエスト）: 末尾が改行で終わらないときだけ `TORN_END` を付ける
    - `locked` が偽（錠を取らない hook の回答）: 末尾を見ずに毎回 `TORN_END` を付ける。末尾を見てから書くまでの間に別の hook の
      書きかけが入っても、その行と自分の行がつながらない。増えるのは読まれない `TORN_END` の行だけである

    `O_APPEND` が追記を分ける単位は 1 回の `write` なので、書ききれなかった残りを続けて書かない（間に別の追記が入りうる）。
    書きかけを残したまま、同じ行を先頭から新しい 1 回の `write` で書き直す。`WRITE_ATTEMPTS` 回で書ききれなければ `OSError`。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if not private_to_me(fd):
            raise PermissionError(f"{path} が所有者だけの読み書きでない。所有者を自分にし、`chmod 600` にしてから打ち直す")
        for _ in range(WRITE_ATTEMPTS):
            size = os.fstat(fd).st_size
            torn = not locked or (size > 0 and os.pread(fd, 1, size - 1) != b"\n")
            data = (TORN_END if torn else b"") + line
            if os.write(fd, data) == len(data):
                return
        raise OSError(f"{path} へ 1 行を書ききれない（{WRITE_ATTEMPTS} 回）。空きを確かめてから打ち直す")
    finally:
        os.close(fd)
