"""レーンの conductor を起動する（#3097）: 起動するときの最初の入力を組み、名前つきのラッパー（`ndf relay spawn`）で起動する。

最初の入力はコマンドの並びで、起きた conductor がレーンのステートマシンを読んで行を実行中にする（E4）。

- 行を始める: `ndf progress new <スプリント名> --mode <モード> --issues <課題>... --parent <レーンのステートマシンの ID>` で
  スプリントのステートマシンを作り、返った ID で `ndf lanes row start <計画> <レーン> <行> --sprint <ID>` を打つ。
  `row start` は計画が記録した形の番号と自分のものを比べ、違えば `format_mismatch` で止まる（P3-1）。
  スプリント名は `<計画>-<レーン>-<行>-<要約>` にし、別の計画が同じレーン名を使っても、名前の区切りが曖昧な組
  （計画 `dev-api`／レーン `tests` と計画 `dev`／レーン `api-tests` など）でも重ならない
- 開発ループの行（`kind: loop`）も、レーンの子のステートマシンを作る（#3558）。`ndf progress new <スプリント名> --loop <レーン>
  --parent <レーンのステートマシンの ID>` で工程の無いステートマシンを作り、返った ID で `ndf lanes row start ... --sprint <ID>` を打ち、
  レーン名をループの名前にして `ndf loop start <レーン> --row <ID> --session <S>` で始める。`--session` は起きた
  conductor が自分のセッションで埋める。ループが止まると、ループの次の 1 手が `ndf progress next <ID>` になり、
  ステートマシンの完了と同じ口（`lane.end_row`）で行を終える。ほかの kind は起動する入力を組めないので `Unsupported` で断る
- 回答の後の next を打つ: `ndf lanes row resumed <スプリントの ID>` で回答の後の next のマーカーを書いてから、
  `ndf progress next <スプリントの ID>` で承認ゲートの続きを流す（3346-5）。開発ループの行は `progress next` の代わりに
  `ndf loop get <レーン>` で次の 1 手を読む（工程の無いステートマシンへ next を打つと行が終わる）
- 止めた計画で実行中のスプリントを中止する: `ndf progress cancel <スプリントの ID> --reason <理由>` の後に
  `ndf lanes row end <スプリントの ID> --outcome cancelled` で行を中止にする（`progress cancel` はレーンの行を書かない）。
  開発ループの行は、先に `ndf loop stop <レーン> --by organizer --now` でループをその場で止める
- 行を始める入力も再起動の入力も、先頭を `/goal /ndf:development-workflow <引数> -- <終わりの条件>` にする（#3858。`start_prompt`）。
  `/goal` が無いと conductor は並びを打って応答を終え、レーンが止まる。終わりの条件は行が `ndf lanes row end` で終わったとき。
  hook（`guards.unattended`）はこの 1 行目を conductor の起動と読む
- 認証の失敗で `/goal` が外れたレーンを再起動する: 入力の先頭に `/goal` を置き、`ndf progress next <スプリントの ID>` で
  止まった所から続ける（`goal_prompt`。#3243）。開発ループの行は `ndf loop get <レーン>` で続ける。アカウントが
  使えるかは `account_ok` が自分のサブコマンド（`ndf relay account show`）で読む

ラッパーの名前は `<計画>-<レーン>-<要約>` に固定し、同じレーンを 2 本起動しない（ラッパーが動いていれば `spawn` が断る）。
要約はリポジトリ・計画・レーンの 3 つから作る。ラッパーの登録簿は利用者単位で共通なので、別のリポジトリが同じ
計画名・レーン名を使っても、名前の区切りが曖昧な組（`a-b` と `a` / `b-c` など）でも重ならない。
ラッパーは子の conductor が終わるまで動き続けるので、起動する前に（行を始めるときも中止のときも）同じ名前の
ラッパーを `ndf relay stop` で止め、止まったのを確かめてから起動する（`stop`）。

打つサブコマンドのエントリポイントは打つたびに求める（`entry`）。耐久ワークフローを起動したバージョンの保存先がプラグインの更新の
片付けで消えていれば、同じ親の下の最も新しいバージョンの保存先のものを使い、切り替えた 1 行を標準エラー（切り離した
`ndf lanes run` の `run.log`）へ残す（#2583）。規則は中継の複製（`ndf.domains.relay.install`）と同じである。

ラッパーを起動する（`spawn`）ときだけは、レーンの作業ディレクトリで導入の一覧を読み、自分と同じキャッシュの親を指す有効な
NDF の導入がちょうど 1 件なら、その `bin/ndf` で起動する（`installed_entry`。#3909 の決定 4）。耐久ワークフローの外で
導入が上がっていても、ラッパーを今の導入のバージョンで動かすためである。選べなければ `entry` と同じ選び方へ落ち、
断念の理由と選んだバージョンを標準エラーへ 1 行残す。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from ndf import identity
from ndf.adapters import claude
from ndf.contracts import result
from ndf.progress import command
from ndf.progress.lane import CANCELLED, LaneRow

# 行の入力の args に --mode が無いときのモード
DEFAULT_MODE = "standard"
# レーンの conductor の役（ラッパーの登録簿の role）
ROLE = command.ACTOR
# 止めた計画で実行中のスプリントを中止する理由
STOP_REASON = "計画を止めた"
# ラッパーの名前の長さの上限（中継の名前の形 `relay.signals.NAME_RE`）と、末尾に付ける要約の桁数
WRAPPER_NAME_MAX = 64
WRAPPER_DIGEST_LENGTH = 12


def _usable(root: Path) -> bool:
    return os.access(identity.entrypoint(root), os.X_OK)


def _fallback_entry() -> tuple[Path, Optional[str]]:
    """一覧を読まない選び方。自分の根が使えれば自分、消えていれば同じ親の下の最も新しい根の `bin/ndf` と、切り替えの注記。

    切り替えなければ注記は None。使える根が無ければ自分のパスのまま返す（打つ側の失敗が起動の失敗になる）。何も書かない。
    """
    own = identity.ROOT
    if _usable(own):
        return identity.entrypoint(own), None
    parent = own.parent
    found = [p for p in parent.iterdir() if _usable(p)] if parent.is_dir() else []
    if not found:
        return identity.entrypoint(own), None
    newest = max(found, key=lambda p: identity.entrypoint(p).stat().st_mtime)
    return identity.entrypoint(newest), f"バージョンの保存先 {own} が無いので"


def entry() -> Path:
    """打つサブコマンドのエントリポイント。自分のバージョンの保存先が消えていれば、同じ親の下の最も新しいバージョンの保存先のものにする。

    切り替えたら 1 行を標準エラーへ書く。使える保存先が無ければ自分のパスのまま返す（打つ側の失敗が起動の失敗になる）。
    """
    path, note = _fallback_entry()
    if note is not None:
        print(f"ndf lanes: {note} {_root_of(path)} のバージョンで打つ", file=sys.stderr)
    return path


def _root_of(path: Path) -> Path:
    """`bin/ndf` のパスから、プラグインの根。"""
    return path.parent.parent


# 起動時の自分のバージョン（モジュールを読み込んだときに 1 回読む。読めなければ None）。自分の根が後で消えても、
# 一覧の導入を起動時の所属で選ぶ（`claude.same_cache_rows`）
OWN_VERSION: Optional[str] = identity.version_of(identity.ROOT)


def installed_entry(
    cwd: Path,
    listed: Callable[..., Optional[list[dict[str, Any]]]] = claude.list_plugins,
    version: Optional[str] = OWN_VERSION,
) -> Path:
    """ラッパーを起動する `bin/ndf`。`cwd` で読んだ一覧に、自分と同じキャッシュの親を指す有効な NDF の導入がちょうど 1 件あり、
    その `bin/ndf` が実行できればそれを返す（#3909 の決定 4）。

    ほかは `_fallback_entry` の選んだパスを返し、断念の理由と選んだバージョンを標準エラーへちょうど 1 行書く。
    """
    rows = listed(cwd=cwd)
    if rows is None:
        reason = "導入の一覧を読めない"
    else:
        hits = claude.same_cache_rows(rows, identity.ROOT, version, os.environ)
        if len(hits) == 1:
            root = Path(hits[0]["installPath"])
            if _usable(root):
                return identity.entrypoint(root)
            reason = f"一覧の導入の {identity.entrypoint(root)} を実行できない"
        else:
            reason = f"自分と同じキャッシュを指す有効な NDF の導入が {len(hits)} 件ある（1 件のときだけ選ぶ）"
    path, note = _fallback_entry()
    root = _root_of(path)
    chosen = identity.version_of(root) or str(root)
    head = f"{note} " if note is not None else ""
    print(f"ndf lanes: {reason}。{head}{chosen} のバージョンで打つ", file=sys.stderr)
    return path


def _digest(*parts: str) -> str:
    """区切りが曖昧にならないよう、値の並びを JSON にしてから要約する。"""
    return hashlib.sha256(json.dumps(list(parts)).encode()).hexdigest()[:WRAPPER_DIGEST_LENGTH]


def wrapper_name(repo: str, plan: str, lane: str) -> str:
    """レーンのラッパーの名前。読める部分（`<計画>-<レーン>`）に、リポジトリ・計画・レーンの要約を付ける。"""
    digest = _digest(repo, plan, lane)
    readable = f"{plan}-{lane}"[: WRAPPER_NAME_MAX - WRAPPER_DIGEST_LENGTH - 1]
    return f"{readable}-{digest}"


# 起動できる行の種類（行の入力の `kind`）: スプリントの行と開発ループの行
SPRINT_KIND, LOOP_KIND = "sprint", "loop"


class Unsupported(Exception):
    """起動する入力を組めない行（開発ループの行など）。打ち直しても起動できない。"""


def sprint_name(plan: str, lane: str, row: str) -> str:
    """行のスプリント名。読める部分（`<計画>-<レーン>-<行>`）に計画・レーン・行の要約を付け、区切りが曖昧な組でも重ならない。"""
    return f"{plan}-{lane}-{row}-{_digest(plan, lane, row)}"


def start_commands(plan: str, lane: str, lane_table: str, row: LaneRow) -> list[dict[str, Any]]:
    """行を始めるコマンドの並び（`result.command` の形）。`--sprint` と続ける `progress next` の ID は、打つ側が `new` の結果の ID で埋める。

    最後のコマンドは工程を続ける `ndf progress next`（#3858）。行を始めるだけで止まらないよう、最初の工程まで進める。

    開発ループの行は `loop_commands`。スプリントの行でも開発ループの行でもなければ `Unsupported`。
    """
    kind = row.input.get("kind")
    if kind == LOOP_KIND:
        return loop_commands(plan, lane, lane_table, row)
    if kind != SPRINT_KIND:
        raise Unsupported(
            f"行 {lane}/{row.key} は {kind} の行で、起動する入力を組めない（起動できるのは {SPRINT_KIND} と {LOOP_KIND} の行だけ）"
        )
    args = [str(a) for a in row.args]
    mode = [] if "--mode" in args else ["--mode", DEFAULT_MODE]
    issues = [str(i) for i in row.input["issues"]]
    name = sprint_name(plan, lane, row.key)
    new = result.command("progress", "new", name, *mode, "--issues", *issues, "--parent", lane_table, *args)
    start = result.command("lanes", "row", "start", plan, lane, row.key, "--sprint", ask=("sprint",))
    return [new, start, result.command("progress", "next", ask=("sprint",))]


def loop_commands(plan: str, lane: str, lane_table: str, row: LaneRow) -> list[dict[str, Any]]:
    """開発ループの行を始めるコマンドの並び。レーンの子のステートマシン（工程なし）を作り、行を実行中にしてからループを始める。

    ループの名前はレーン名。`--sprint`・`--row` は打つ側が `new` の結果の ID で、`--session` は自分のセッションで埋める。
    最後のコマンドはループの次の 1 手を読む `ndf loop get`（#3858）。
    """
    new = result.command("progress", "new", sprint_name(plan, lane, row.key), "--loop", lane, "--parent", lane_table)
    row_start = result.command("lanes", "row", "start", plan, lane, row.key, "--sprint", ask=("sprint",))
    start = result.command("loop", "start", lane, "--row", ask=("sprint", "session"))
    return [new, row_start, start, result.command("loop", "get", lane)]


def loop_of(lane: str, row: LaneRow) -> Optional[str]:
    """開発ループの行ならループの名前（レーン名）。ほかの行は None。"""
    return lane if row.input.get("kind") == LOOP_KIND else None


def _going_on(sprint_id: str, loop: Optional[str]) -> dict[str, Any]:
    """止まった所から続けるコマンド。開発ループの行はループの次の 1 手を読む（ステートマシンへ next を打つと行が終わる）。"""
    return result.command("loop", "get", loop) if loop is not None else result.command("progress", "next", sprint_id)


def cancel_commands(sprint_id: str, loop: Optional[str] = None) -> list[dict[str, Any]]:
    """止めた計画で実行中のスプリントを中止し、レーンの行を中止にするコマンドの並び。

    `loop` は開発ループの行のループの名前で、先にループをその場で止める。
    """
    halt = [result.command("loop", "stop", loop, "--by", "organizer", "--reason", STOP_REASON, "--now")] if loop is not None else []
    cancel = result.command("progress", "cancel", sprint_id, "--reason", STOP_REASON)
    end = result.command("lanes", "row", "end", sprint_id, "--outcome", CANCELLED)
    return [*halt, cancel, end]


def resume_commands(sprint_id: str, loop: Optional[str] = None) -> list[dict[str, Any]]:
    """回答が載った行で、回答の後の next のマーカーを書いてから続けるコマンドの並び（`loop` は開発ループの行のループの名前）。"""
    mark = result.command("lanes", "row", "resumed", sprint_id)
    return [mark, _going_on(sprint_id, loop)]


# 再起動の入力の先頭に置く、claude の目標を入れる命令（#3243）
GOAL = "/goal"


# 目標の終わりの条件（`/goal` の `--` の後）。行はスプリントのステートマシンの完了と同じ口（`ndf lanes row end`）で終わる
GOAL_END = "終わり: この行が `ndf lanes row end` で終わったとき（返った next を打ち続けて工程を進め、行の終わりまで止まらない）"


def _goal_line(args: list[str]) -> str:
    """目標の先頭。hook（`guards.unattended`）が 1 行目 `/goal /<前置き>development-workflow` を conductor の起動と読む。"""
    skill = f"/{identity.load().prefix}development-workflow"
    return " ".join([GOAL, skill, *args, "--"])


def _workflow_args(lane: str, row: LaneRow) -> list[str]:
    """Skill の引数。スプリントの行は課題（`#<番号>`）、開発ループの行は `--loop <レーン>`。"""
    if row.input.get("kind") == LOOP_KIND:
        return ["--loop", lane]
    return [f"#{i}" for i in row.input["issues"]]


def start_prompt(plan: str, lane: str, lane_table: str, row: LaneRow) -> str:
    """行を始めるレーンの最初の入力（#3858）。`/goal` を先頭に置き、行を始めてから工程を続ける。"""
    commands = start_commands(plan, lane, lane_table, row)
    return f"{_goal_line(_workflow_args(lane, row))} {GOAL_END}。{prompt(plan, lane, commands)}"


def goal_prompt(plan: str, lane: str, sprint_id: str, loop: Optional[str] = None) -> str:
    """認証の失敗で `/goal` が外れたレーンを再起動する入力。目標を入れ直し、スプリントを止まった所から続ける。"""
    args = ["--loop", loop] if loop is not None else []
    return f"{_goal_line(args)} {GOAL_END}。{prompt(plan, lane, [_going_on(sprint_id, loop)])}"


def prompt(plan: str, lane: str, commands: list[dict[str, Any]]) -> str:
    """conductor へ渡す最初の入力。コマンドの並びを JSON で持つ。"""
    body = json.dumps(commands, ensure_ascii=False)
    return f"計画 {plan} のレーン {lane} の conductor として、次のコマンドを順に打つ（ask のキーは前の結果で埋める）: {body}"


def spawn(repo: str, plan: str, lane: str, cwd: Path, first: str, run: Optional[Any] = None) -> dict[str, Any]:
    """名前つきのラッパーでレーンの conductor を起動する。結果 JSON の `status` が ok なら `ok` を真にする。

    ラッパーは `cwd` で読んだ今の有効な導入の `bin/ndf` で起動する（`installed_entry`）。
    """
    args = ["relay", "spawn", "--name", wrapper_name(repo, plan, lane), "--cwd", str(cwd), "--role", ROLE, "--plan", plan, first]
    argv = [str(installed_entry(cwd)), *args]
    return _relay(argv, run, "ラッパーを起動できない")


def stop(repo: str, plan: str, lane: str, run: Optional[Any] = None) -> dict[str, Any]:
    """レーンのラッパーを止め、止まったのを確かめる（`ndf relay stop` は錠が空くまで待つ）。動いていなければ ok。"""
    argv = _entry("relay", "stop", wrapper_name(repo, plan, lane))
    return _relay(argv, run, "ラッパーを止められない")


def _entry(*args: str) -> list[str]:
    return [str(entry()), *args]


def _last_line(stdout: str, empty: Any) -> Any:
    """標準出力の末尾の行を JSON として読む（行が無ければ `empty`）。読めなければ ValueError を呼び手へ伝える。"""
    lines = stdout.strip().splitlines()
    return json.loads(lines[-1]) if lines else empty


def _last_json(stdout: str) -> Optional[dict[str, Any]]:
    try:
        out = _last_line(stdout, None)
    except ValueError:  # noqa: NDF401 外部コマンド: 結果 JSON の無い出力は読めないとして呼び手が unknown にする
        return None
    return out if isinstance(out, dict) else None


def _run(argv: list[str], run: Optional[Any]) -> subprocess.CompletedProcess[str]:
    """サブコマンドを打つ（`run` が無ければ `subprocess.run`）。打てなければ OSError を呼び手へ伝える。"""
    runner = run if run is not None else subprocess.run
    return runner(argv, capture_output=True, text=True, check=False)


def _probe(argv: list[str], run: Optional[Any]) -> Optional[dict[str, Any]]:
    """自分のサブコマンドを打ち、結果 JSON（末尾の行）を返す。打てない・読めなければ None。"""
    try:
        done = _run(argv, run)
    except OSError:  # noqa: NDF401 外部コマンド: 打てなければ呼び手が「読めない」として生死を unknown にする
        return None
    return _last_json(done.stdout)


def wrappers(run: Optional[Any] = None) -> Optional[dict[str, dict[str, Any]]]:
    """ラッパーの一覧（`ndf relay ls`）を名前で引ける形で返す。一覧を読めなければ None（止まりと読まない）。"""
    out = _probe(_entry("relay", "ls"), run)
    if out is None or out.get("status") != result.OK or not isinstance(out.get("items"), list):
        return None
    return {str(r["name"]): r for r in out["items"] if isinstance(r, dict) and "name" in r}


def account_ok(run: Optional[Any] = None) -> bool:
    """子を起動するアカウントが使えるか（`ndf relay account show` が ok を返すか。指定が壊れていれば使えない）。
    打てない・読めなければ偽（再起動せずに止まりとして知らせる）。"""
    out = _probe(_entry("relay", "account", "show"), run)
    return out is not None and out.get("status") == result.OK


def sprint_wait(sprint_id: str, run: Optional[Any] = None) -> Optional[dict[str, Any]]:
    """スプリントのステートマシンの今の待ち（`ndf progress wait <ID> --timeout 0` の結果 JSON）。読めなければ None。"""
    return _probe(_entry("progress", "wait", sprint_id, "--timeout", "0"), run)


def _relay(argv: list[str], run: Optional[Any], failure: str) -> dict[str, Any]:
    try:
        done = _run(argv, run)
    except OSError as exc:
        return {"ok": False, "detail": f"{failure}: {exc}"}
    return read_spawn(done.stdout, done.stderr, done.returncode)


def read_spawn(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    """ラッパーの出力を読む。標準出力の末尾の行の結果 JSON の `status` が ok なら `ok` を真にする。

    結果 JSON が読めなければ、詳細は標準エラーの末尾（無ければ終了コード）にする。
    """
    try:
        out = _last_line(stdout, {})
    except ValueError:
        out = {}
    ok = out.get("status") == result.OK
    return {"ok": ok, "detail": out.get("summary") if out else (stderr.strip()[-300:] or f"終了コード {returncode}")}
