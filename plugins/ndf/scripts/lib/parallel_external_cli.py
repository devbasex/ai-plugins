"""`parallel.py` の `--external` の副命令の本体（#2439。置き場のファイルは `parallel_external.py`）。

外のオーガナイザーのレーンとリリースの実行（環境変数 `NDF_ORGANIZER_DIR`）は、計画名の代わりに `--external` を付けて
置き場のファイルでやり取りする。

| 副命令 | 役割 |
| --- | --- |
| `request --external --lane <名> --state <パス> [--prs ...] [--issues ...]` | リリースの依頼を `requests/<id>.json` へ書く（10）か、すでに `main` にあれば書かない（0） |
| `await --external --from <パス>` | `answers/<id>.json` を読む（released 0・rejected 10・まだ無い 3） |
| `compose --external` | `batch.json` の手（`step`）ごとに流すもの（`pipeline`・`record`・`settle`）を返す |
| `present --external <連番> <提示> (--approval <承認資料> \\| --record)` | 範囲をレーンへ対応づける。`--record` はリリース承認ゲートで止まった提示を `presented/` へ書く |
| `settle --external <連番> --done <D>` | 届いた段を `progress/`、結果を `results/` へ書く |

サービスの実行ジョブ（環境変数 `NDF_FACTORY_RUN`）では `init`・`start`・`run` を断る（1 台の中のオーガナイザーを起動しない）。

どの関数も、結果を出して終える `reply`（`parallel.py` の同名の関数）を受け取り、それで終える。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

import parallel_external as pe
import parallel_lane as pl
import parallel_plan as pp
from parallel_repo import relay_unavailable


def manual_or_none(main: Path, plan, reply) -> None:
    """ラッパーの外・tmux が無いときは、レーンごとの手で起動する 1 行を返して 3 で終える（#2436 の受け入れ条件 9）。"""
    if why := relay_unavailable(main):
        lines = pp.manual_commands(plan, main)
        reply(
            "stopped",
            f"{why}ため、レーンを起動しない。手で起動する 1 行を items に並べた",
            [{"lane": ln.name, "command": c} for ln, c in zip(plan.lanes, lines)],
            3,
            manual=True,
        )


def refuse_in_factory_run(reply: Callable) -> None:
    """サービスの実行ジョブ（`NDF_FACTORY_RUN`）の中では 1 台の中のオーガナイザーを起動しない（#2439 の I68。2 つのオーガナイザーは排他）。"""
    if os.environ.get("NDF_FACTORY_RUN"):
        reply(
            "stopped",
            "サービスの実行ジョブの中では並列の計画を起動しない（外のオーガナイザーがリリースをまとめる）",
            code=1,
            factory_run=True,
        )


def external_dir(reply: Callable) -> Path:
    d = pe.organizer_dir()
    if d is None:
        reply("stopped", f"--external には環境変数 {pe.DIR_ENV} が要る", code=1)
    return d


def present_external(main: Path, a, reply: Callable) -> None:
    d = external_dir(reply)
    try:
        batch = pe.read_batch(d)
        if a.record:
            got = pe.record_presented(d, a.n, a.k, auto=False)
            reply("ok", f"提示 {a.n}-{a.k} を外のオーガナイザーへ書いた（{got['sha'][:12]} まで）", sha=got["sha"], prs=got["prs"])
        if not a.approval:
            reply("stopped", "--approval か --record が要る", code=1)
        got = pe.present_batch(main, d, batch, a.n, a.k, Path(a.approval))
    except ValueError as exc:
        reply("stopped", f"提示を作れない: {exc}", code=1)
    except OSError as exc:
        reply("stopped", f"読めない: {exc}", code=2)
    reply(
        "ok",
        f"提示 {a.n}-{a.k}: {got['sha'][:12]} まで（Pull Request {len(got['prs'])} 本）",
        [{"lane": n, **r} for n, r in got["lanes"].items()],
        sha=got["sha"],
        orphans=got["orphans"],
    )


def compose_external(main: Path, reply: Callable) -> None:
    """リリースの実行（`--parallel-helper --external`）が `batch.json` の手で流すもの（#2439）。"""
    from parallel_repo import dev_version
    from supervise_lib.paths import PARALLEL_PY, SELF

    d = external_dir(reply)
    try:
        task = pe.compose(main, d, pe.read_batch(d), f"python3 {SELF}", PARALLEL_PY, dev_version)
    except ValueError as exc:
        reply("stopped", f"まとめたリリースを組めない: {exc}", code=1)
    except OSError as exc:
        reply("stopped", f"書けない: {exc}", code=2)
    reply("ok", f"まとめたリリース {task['seq']} の手 {task['step']}（提示 {task['k']}）", **task)


def settle_external(main: Path, a, reply: Callable) -> None:
    d = external_dir(reply)
    if a.rejected or not a.done:
        reply("stopped", "--external の settle は --done が要る（差し戻しは外のオーガナイザーが決める）", code=1)
    try:
        done = json.loads(Path(a.done).read_text(encoding="utf-8"))
        got = pe.settle_batch(main, d, pe.read_batch(d), a.n, done, a.reason)
    except ValueError as exc:
        reply("stopped", f"結果を書かない: {exc}", code=4)
    except OSError as exc:
        reply("stopped", f"done を読めない: {exc}", code=2)
    reply("ok", f"リリース {a.n} の結果 {got['result']} を外のオーガナイザーへ書いた", result=got["result"], version=got["version"])


def request_external(main: Path, a, reply: Callable, parallel_py: Path) -> None:
    d = external_dir(reply)
    if a.prs and pl.merged_on_production(main, a.prs):
        version = pe.range_version(d, a.prs)
        pl.write_request(Path(a.state), {"result": "released", "version": version, "prs": a.prs})
        reply("ok", "自分の Pull Request はすでに本番チャネルにある。依頼を書かずに続きへ進む", result="released", version=version)
    rid = pl.lane_request_id(Path(a.state), a.lane)
    pe.write_request_file(d, rid, a.lane, a.prs, a.issues, Path(a.state))
    pl.write_request(Path(a.state), {"id": rid, "plan": pe.EXTERNAL, "lane": a.lane, "prs": a.prs})
    nxt = f"python3 {parallel_py} await --external --from {Path(a.state) / 'work' / 'request.txt'}"
    reply(
        "gate",
        f"リリースの依頼 {rid} を外のオーガナイザーへ書いた（リリース待ち）。答えは parallel.py await --external で読む",
        code=10,
        next_step=nxt,
        id=rid,
        result="requested",
    )
