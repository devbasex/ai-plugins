#!/usr/bin/env python3
"""#1142（#3152 から統合）の洗い出し: 開発のワークフローで AI が分岐と例外の行き先を決めた回を、実際の記録から数える。

3 つの記録を読み、回数を種類ごとに数えて JSON で出す。数は「AI が出力を読んで次の手を決めた跡」の件数で、すべてが
誤りではない。数え方を変えずに、作り直しの前と後を同じコマンドで打つ。

    python3 scripts/measure/ai-branching.py [--since 2026-10-14] [--ndf .ndf] [--transcripts <根>]... [--task-outputs <根>]... [--jobs 6]

- プランの記録（`progress.jsonl`）: `--ndf` の下と `--state`（既定 `~/.local/state/ndf`）の下のすべて。
  `run` の行の `from`（途中からの再開）を、直前の実行の終わり方（承認ゲート / 失敗 / それ以外）で分ける。
  `attention` の行（`wait` が終了コード 20 で AI に読ませる知らせ）を理由ごとに、`judge` のステップを決定ごとに数える。
  実行が「止まった」で終わった回を、最後のステップと終了コードの組で数える
- 並列の計画の台帳（`<ndf>/parallel/*/ledger.jsonl`）: `ask` を `source` ごとに、`resume` の `take`・`settle` の `result` を数える
- 会話の記録（`--transcripts`。既定 `~/.claude/ndf/accounts/*/projects/-work-ai-plugins*`）: `assistant` の行の `tool_use`
  を id で 1 度だけ数え、Bash のコマンドを規則の表（`ai-branching-rules.json` の `kind: bash`）の正規表現で分ける（1 つの
  コマンドが複数の規則に当たれば、それぞれに数える）。Write のうち `"steps"` を含む `.json`（手で組んだプラン）と、規則の表の
  `kind: tool` の Tool と、tool_result のうち `kind: phrase` の文を含むもの（AI が読んだ止まりの文）も数える。`kind: cause` は、
  `command` の正規表現に当たる Bash の tool_result が結果 JSON で、その `cause.kind` が `cause` の値の回を数える。
  バックグラウンドで起動した回は、起動応答のタスクの識別子で、後から出力を読んだ Tool の結果と完了通知を起動した回へ対応づける。
  完了通知が結果を載せなければ `<output-file>` の保存済み出力を読む（`--task-outputs`。既定 `/tmp/claude-<uid>` の下の
  `tasks/<識別子>.output` だけ）。結果 JSON は `tool: ci` と状態・停止理由の形で見分ける。1 つの呼び出しが複数のタスクの出力を読んだ結果は数えない。
  結果 JSON を 1 度も読めなかったバックグラウンドの回は `results_without_cause` に分けて数える
- 規則の行の `aside: true` は補助の件数（R09 の `gh run rerun` など）。旧い指標に対応する件数と同じ物差しへ足さない
- 規則の表の各行は規則の番号 `rule`（#1142 の洗い出しの R01〜R27）と形 `form`（入れ替えの前 `before` / 後 `after`）を持つ。
  分類の表（`ai-branching-measures.json`）は番号ごとに物差し（G1〜G3）と形ごとの出所を持つ。表の形が誤っていれば終了コード 2
"""

import argparse
import collections
import datetime
import glob
import json
import os
import pathlib
import re
import stat
import sys
from concurrent.futures import ProcessPoolExecutor

# 数える規則（規則の番号・形・種類と、Bash の正規表現・Tool の名前・tool_result の文）。実在のエントリポイントの名前を持つため、
# コードではなくデータに置く。規則の番号を G1〜G3 へ寄せる分類の表と形ごとの出所は ai-branching-measures.json が持つ
RULES_FILE = pathlib.Path(__file__).with_name("ai-branching-rules.json")
MEASURES_FILE = pathlib.Path(__file__).with_name("ai-branching-measures.json")
FORMS = ("before", "after")
# 種類ごとに要る項目。bash は正規表現、tool は Tool の名前、phrase は tool_result に現れたら数える文、
# cause は呼び出しの Bash のコマンドの正規表現と、結果 JSON の `cause.kind` の値
KINDS = {"bash": ("pattern",), "tool": ("tool",), "phrase": ("phrase",), "cause": ("command", "cause")}
# 正規表現として読む項目
REGEX_FIELDS = {"bash": "pattern", "cause": "command"}
RULE_ID = re.compile(r"^R\d{2,}$")
MEASURE_VALUES = ("G1", "G2", "G3", "none")
SOURCES = ("transcripts", "progress", "ledger", "records", "none")


def _fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(2)


def _read_json(path: pathlib.Path):
    """表の JSON を読む。構文の誤りはファイルと行・列を示して終了コード 2。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(f"{path}: {e.lineno} 行 {e.colno} 列で JSON として読めない（{e.msg}）")
    except (OSError, UnicodeDecodeError) as e:
        _fail(f"{path}: 読めない（{e}）")


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value)


def load_rules(path: pathlib.Path) -> list[dict]:
    """規則の表を読み、行ごとに `rule`・`form`・`kind` と種類の項目を確かめて返す。bash と cause の行は `compiled` を持つ。"""
    rows = _read_json(path)
    if not isinstance(rows, list):
        _fail(f"{path}: 規則の表は配列で書く")
    out = []
    for i, row in enumerate(rows, 1):
        where = f"{path}: {i} 行目"
        if not isinstance(row, dict):
            _fail(f"{where}: 行はオブジェクトで書く")
        if not isinstance(row.get("rule"), str) or not RULE_ID.match(row["rule"]):
            _fail(f"{where}: rule が無いか R と 2 桁以上の数字の文字列でない（{row.get('rule')!r}）")
        if not isinstance(row.get("form"), str) or row["form"] not in FORMS:
            _fail(f"{where}: form が無いか {'・'.join(FORMS)} でない（{row.get('form')!r}）")
        if not isinstance(row.get("kind"), str) or row["kind"] not in KINDS:
            _fail(f"{where}: kind が無いか {'・'.join(KINDS)} でない（{row.get('kind')!r}）")
        for field in KINDS[row["kind"]]:
            if not _is_text(row.get(field)):
                _fail(f"{where}: kind {row['kind']} の行に {field}（空でない文字列）が無い")
        if not _is_text(row.get("name")):
            _fail(f"{where}: name（空でない文字列）が無い")
        if not isinstance(row.get("aside", False), bool):
            _fail(f"{where}: aside は true か false で書く（{row.get('aside')!r}）")
        r = dict(row)
        field = REGEX_FIELDS.get(row["kind"])
        if field:
            try:
                r["compiled"] = re.compile(row[field])
            except re.error as e:
                _fail(f"{where}: {field} が正規表現として読めない（{e}）")
        out.append(r)
    return out


def _is_texts(value) -> bool:
    """空でない文字列の、空でない配列か。"""
    return isinstance(value, list) and bool(value) and all(_is_text(v) for v in value)


def _check_source(where: str, src) -> None:
    if not isinstance(src, dict) or not isinstance(src.get("source"), str) or src["source"] not in SOURCES:
        _fail(f"{where}: source が無いか {'・'.join(SOURCES)} でない")
    if src["source"] in ("progress", "ledger"):
        if not _is_text(src.get("counter")) or ("keys" not in src and "prefix" not in src):
            _fail(f"{where}: {src['source']} の出所に counter と keys か prefix が無い")
        if "keys" in src and not _is_texts(src["keys"]):
            _fail(f"{where}: keys は空でない文字列の配列で書く（{src['keys']!r}）")
        if "prefix" in src and not _is_text(src["prefix"]):
            _fail(f"{where}: prefix は空でない文字列で書く（{src['prefix']!r}）")
    if src["source"] == "none" and not _is_text(src.get("reason")):
        _fail(f"{where}: none の出所に reason（数えない理由の文字列）が無い")


def load_measures(path: pathlib.Path) -> dict[str, dict]:
    """規則の番号ごとの `measure`（G1 / G2 / G3 / none）と、形ごとの出所 `before`・`after` を確かめて返す。"""
    data = _read_json(path)
    if not isinstance(data, dict):
        _fail(f"{path}: 分類の表は規則の番号を鍵にしたオブジェクトで書く")
    for rule, entry in data.items():
        where = f"{path}: {rule}"
        if not RULE_ID.match(rule) or not isinstance(entry, dict):
            _fail(f"{where}: 鍵は R と数字、値はオブジェクトで書く")
        if not isinstance(entry.get("measure"), str) or entry["measure"] not in MEASURE_VALUES:
            _fail(f"{where}: measure が無いか {'・'.join(MEASURE_VALUES)} でない（{entry.get('measure')!r}）")
        for form in FORMS:
            _check_source(f"{where} の {form}", entry.get(form))
    return data


def check_measures(rules: list[dict], measures: dict[str, dict]) -> None:
    """規則の表にある番号が、すべて分類の表にあるかを照らす。欠けたら番号を挙げて終了コード 2。"""
    missing = sorted({r["rule"] for r in rules} - set(measures))
    if missing:
        _fail(f"分類の表に無い規則の番号: {'・'.join(missing)}")


def default_transcripts() -> list[str]:
    return sorted(glob.glob(os.path.expanduser("~/.claude/ndf/accounts/*/projects/-work-ai-plugins*")))


def read_jsonl(path: pathlib.Path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


# ---- プランの記録 ----


def progress_files(ndf: pathlib.Path, state: pathlib.Path) -> list[pathlib.Path]:
    seen = set()
    out = []
    for root in (ndf, state):
        if not root.is_dir():
            continue
        for p in root.rglob("progress.jsonl"):
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append(r)
    return sorted(out)


def plan_runs(rows: list[dict]) -> list[list[dict]]:
    """`run` の行で区切った実行ごとの行の並び。"""
    runs: list[list[dict]] = []
    for row in rows:
        if row.get("kind") == "run" or not runs:
            runs.append([])
        runs[-1].append(row)
    return runs


def run_end(run: list[dict]) -> tuple[str, str, str]:
    """実行の終わり方: (種類, 最後のステップ, 終了コード)。種類は 関門 / 失敗 / 完了か不明。"""
    steps = [r for r in run if r.get("kind") == "step"]
    attn = [r for r in run if r.get("kind") == "attention"]
    if not steps:
        return "記録なし", "", ""
    last = steps[-1]
    code = last.get("exit")
    sid = str(last.get("step", ""))
    if (attn and attn[-1].get("reason") == "関門") or (isinstance(code, int) and 10 <= code <= 19):
        return "関門", sid, str(code)
    if last.get("next") == "end" and code not in (0, None) and last.get("type") != "judge":
        return "失敗", sid, str(code)
    if code not in (0, None) and not last.get("next"):
        return "失敗", sid, str(code)
    return "完了か不明", sid, str(code)


def count_progress(files: list[pathlib.Path], since: str) -> dict:
    resume = collections.Counter()
    resume_from = collections.Counter()
    attention = collections.Counter()
    judge = collections.Counter()
    stopped = collections.Counter()
    plans = 0
    for path in files:
        rows = [r for r in read_jsonl(path) if str(r.get("at", "")) >= since]
        if not rows:
            continue
        plans += 1
        runs = plan_runs(rows)
        for i, run in enumerate(runs):
            head = run[0]
            if head.get("kind") == "run" and head.get("from") and i > 0:
                kind, sid, _ = run_end(runs[i - 1])
                same = "同じステップから" if sid == head["from"] else "別のステップから"
                resume[f"{kind}の後・{same}"] += 1
                resume_from[str(head["from"])] += 1
            kind, sid, code = run_end(run)
            if kind == "失敗":
                stopped[f"{sid} exit={code}"] += 1
        for r in rows:
            if r.get("kind") == "attention":
                attention[str(r.get("reason"))] += 1
            if r.get("kind") == "step" and r.get("type") == "judge":
                judge[f"{r.get('step')} → {r.get('decision') or r.get('next')}"] += 1
    return {
        "plans": plans,
        "resume_by_previous_end": dict(resume.most_common()),
        "resume_from_step": dict(resume_from.most_common(20)),
        "attention_by_reason": dict(attention.most_common()),
        "judge_by_decision": dict(judge.most_common()),
        "stopped_by_step_exit": dict(stopped.most_common(25)),
    }


# ---- 並列の計画の台帳 ----


def count_ledgers(ndf: pathlib.Path, since: str) -> dict:
    ask = collections.Counter()
    resume = collections.Counter()
    kinds = collections.Counter()
    for path in sorted((ndf / "parallel").glob("*/ledger.jsonl")):
        for r in read_jsonl(path):
            if str(r.get("at", "")) < since:
                continue
            kinds[str(r.get("kind"))] += 1
            msg = r.get("msg") or {}
            if r.get("kind") == "ask":
                ask[str(msg.get("source"))] += 1
            if r.get("kind") == "resume":
                key = (
                    f"{msg.get('phase')}: take={msg.get('take')}"
                    if msg.get("phase") == "confirm"
                    else f"{msg.get('phase')}: {msg.get('result')}"
                )
                resume[key] += 1
    return {"kinds": dict(kinds.most_common()), "ask_by_source": dict(ask.most_common()), "resume": dict(resume.most_common())}


# ---- 会話の記録 ----


def _phrases(rules: list[dict]) -> list[str]:
    """規則の表の文（重ねずに表の順）。"""
    return list(dict.fromkeys(r["phrase"] for r in rules if r["kind"] == "phrase"))


def build_matcher(rules: list[dict]) -> dict:
    """規則から照合用の値（Bash の正規表現・Tool の名前・文）を組む。ワーカーごとに 1 度だけ組む。"""
    return {
        "compiled": [(r["name"], r["compiled"]) for r in rules if r["kind"] == "bash"],
        "tools": {r["tool"] for r in rules if r["kind"] == "tool"},
        "phrases": _phrases(rules),
        "causes": [(r["name"], r["compiled"], r["cause"]) for r in rules if r["kind"] == "cause"],
    }


# ndf ci watch の結果 JSON の `tool` と `status` の値（`plugins/ndf/contracts` の結果の形）
CI_TOOL = "ci"
CI_STATUSES = ("ok", "gate", "stopped")


def is_ci_result(obj) -> bool:
    """ndf ci watch の結果 JSON の形（`tool: ci`・決まった `status`・`cause` は `kind` を持つ object）か。

    `gh run view --json status` などのほかの JSON も `status` を持つので、`status` だけでは決めない。
    """
    if not isinstance(obj, dict) or obj.get("tool") != CI_TOOL or obj.get("status") not in CI_STATUSES:
        return False
    cause = obj.get("cause")
    if obj["status"] == "stopped":
        return isinstance(cause, dict) and isinstance(cause.get("kind"), str)
    return cause is None


def result_json(content) -> dict | None:
    """tool_result の本文に載った ndf ci watch の結果 JSON（`is_ci_result` に当たる JSON の object）。無ければ None。

    正常終了の結果は `status: ok` で `cause` を持たない。結果を取得できたかどうかはこの戻り値で、停止理由は
    `cause_kind` で別々に判定する。形の合わない JSON（GitHub の JSON など）は飛ばして後ろを探し続ける。
    """
    if isinstance(content, list):
        content = "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    text = str(content or "")
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start >= 0:
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            obj, end = None, start + 1
        if is_ci_result(obj):
            return obj
        # 読めた object の中は探し直さない（進捗の行などの別の JSON を飛ばす）
        start = text.find("{", end)
    return None


def cause_kind(obj: dict | None) -> str:
    """結果 JSON の `cause.kind`。cause が無ければ（正常終了など）空。"""
    cause = obj.get("cause") if obj else None
    kind = cause.get("kind") if isinstance(cause, dict) else None
    return kind if isinstance(kind, str) else ""


# ワーカーのプロセスが持つ照合用の値。`init_worker` が 1 度だけ置き、タスクにはファイルと期間だけを渡す
_MATCHER: dict = build_matcher([])


def init_worker(rules: list[dict]) -> None:
    global _MATCHER
    _MATCHER = build_matcher(rules)


# バックグラウンドで起動した Bash の起動応答（`run_in_background: true`）。タスクの識別子は行の `toolUseResult.backgroundTaskId`
# に載り、無い版では本文の文から読む。完了通知は `<task-id>` で同じ識別子を持ち、保存済み出力のパスを `<output-file>` に持つ
BACKGROUND_TEXT = re.compile(r"running in background with ID: (\w+)")
NOTIFICATION_TASK = re.compile(r"<task-id>(\w+)</task-id>")
NOTIFICATION_OUTPUT = re.compile(r"<output-file>([^<]+)</output-file>")
# 起動応答の本文が示す出力の置き場。完了通知の `<output-file>` はこれと一致するときだけ読む
BACKGROUND_OUTPUT = re.compile(r"Output is being written to: (\S+?/tasks/(\w+)\.output)\b")
# 保存済み出力の 1 行から覚えておく上限。ndf ci watch の結果 JSON は 1 行で、先頭に `tool`・`status`、末尾に `cause` が載る。
# 上限を超える行（落ちたテストが多い結果）は、先頭の `OUTPUT_HEAD_LIMIT` バイトと末尾の `OUTPUT_READ_LIMIT` バイトから
# 集計に要る項目（`tool`・`status`・`cause`）だけを取る
OUTPUT_READ_LIMIT = 1 << 20
OUTPUT_HEAD_LIMIT = 1 << 12
OUTPUT_CHUNK = 1 << 16
RESULT_HEAD = re.compile(r'\{\s*"tool"\s*:\s*"([^"\\]*)"\s*,\s*"status"\s*:\s*"([^"\\]*)"')
RESULT_CAUSE = '"cause"'
# 出力を読む Tool の入力の中のタスクの出力ファイル（Bash の command・Read の file_path）
OUTPUT_TASK = re.compile(r"tasks/(\w+)\.output\b")
# 出力を識別子で受け取る Tool（TaskOutput・BashOutput）の入力の項目
TASK_ID_FIELDS = ("task_id", "bash_id")
TOOL_USE_ID = re.compile(r'"tool_use_id":\s*"([^"]+)"')


def _strings(value):
    """行の中の文字列をすべて返す（完了通知は版によって `content`・`attachment.prompt`・`message.content` に載る）。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def read_tasks(name, inp: dict) -> set[str]:
    """出力を読む Tool の入力から、読んだタスクの識別子を抜き出す（照合は呼び出し元が辞書で引く）。"""
    found: set[str] = set()
    if name == "Bash":
        found.update(OUTPUT_TASK.findall(str(inp.get("command", ""))))
    elif name == "Read":
        found.update(OUTPUT_TASK.findall(str(inp.get("file_path", ""))))
    for key in TASK_ID_FIELDS:
        v = inp.get(key)
        if isinstance(v, str) and v:
            found.add(v)
    return found


def default_task_roots() -> list[str]:
    """Claude Code がバックグラウンドのタスクの出力を置く根（`/tmp/claude-<uid>`）。"""
    getuid = getattr(os, "getuid", None)
    return [os.path.join("/tmp", f"claude-{getuid()}")] if getuid else []


def _open_root(root: str) -> int | None:
    """タスク出力の根を開いた記述子。実行ユーザーが持つ実際のディレクトリ（シンボリックリンクでない）でなければ None。"""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(root, flags)
    except OSError:
        return None
    st = os.fstat(fd)
    getuid = getattr(os, "getuid", None)
    if not stat.S_ISDIR(st.st_mode) or (getuid and st.st_uid != getuid()):
        os.close(fd)
        return None
    return fd


def open_task_output(path: str, allowed: str | None, roots: list[str]) -> int | None:
    """完了通知の `<output-file>` のうち、読んでよい保存済み出力を開いた記述子。読まないなら None。

    起動応答が示した同じタスクの出力のパス（`allowed`）と一致し、会話の記録とは別に渡したタスク出力の根（`roots`）の
    下の `tasks/<識別子>.output` である通常のファイルだけを開く。根は実行ユーザーが持つ実際のディレクトリに限り、
    開いた根の記述子から 1 階層ずつ、シンボリックリンクを拒んで開く（確かめてから開くまでの差し替えで根の外を読まない）。
    会話の記録は利用者が渡す入力なので、記録が示すパスだけでは読む先を決めない。
    """
    path = path.strip()
    if not allowed or path != allowed or not os.path.isabs(path):
        return None
    target = os.path.normpath(path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    for root in roots:
        base = os.path.normpath(os.path.abspath(root))
        rel = os.path.relpath(target, base) if target.startswith(base.rstrip(os.sep) + os.sep) else ""
        parts = rel.split(os.sep) if rel else []
        if len(parts) < 2 or parts[-2] != "tasks" or not parts[-1].endswith(".output") or ".." in parts:
            continue
        fd = _open_root(base)
        if fd is None:
            continue
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow, dir_fd=fd)
                os.close(fd)
                fd = nxt
            out = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | nofollow, dir_fd=fd)
        except OSError:
            continue
        finally:
            os.close(fd)
        if stat.S_ISREG(os.fstat(out).st_mode):
            return out
        os.close(out)
    return None


def _long_line_result(head: bytes, tail: bytes) -> dict | None:
    """上限を超えた 1 行の結果 JSON の、先頭から `tool`・`status` を、末尾から `cause` を取る。形が合わなければ None。"""
    m = RESULT_HEAD.match(head.decode("utf-8", errors="replace").lstrip())
    text = tail.decode("utf-8", errors="replace").rstrip()
    if not m or not text.endswith("}"):
        return None
    obj: dict = {"tool": m.group(1), "status": m.group(2)}
    at = text.rfind(RESULT_CAUSE)
    if at >= 0:
        rest = text[at + len(RESULT_CAUSE) :].lstrip()
        if rest.startswith(":"):
            try:
                cause, _ = json.JSONDecoder().raw_decode(rest[1:].lstrip())
            except json.JSONDecodeError:
                cause = None
            if isinstance(cause, dict):
                obj["cause"] = cause
    return obj if is_ci_result(obj) else None


def _line_start(fd: int, at: int) -> int:
    """`at` の位置を含む行の先頭の位置。`at` より前を後ろから `OUTPUT_CHUNK` ずつ読んで改行を探す。"""
    pos = at
    while pos > 0:
        size = min(OUTPUT_CHUNK, pos)
        chunk = os.pread(fd, size, pos - size)
        nl = chunk.rfind(b"\n")
        if nl >= 0:
            return pos - size + nl + 1
        pos -= size
    return 0


def read_output(path: str, allowed: str | None, roots: list[str]) -> str | None:
    """完了通知の `<output-file>` の保存済み出力から、最後の ndf ci watch の結果 JSON を文にして返す。無ければ None。

    開く先は `open_task_output` が決める。結果は標準出力の末尾に出るので、末尾の `OUTPUT_READ_LIMIT` バイトだけを
    読み、後ろの行から探す。末尾の読み取りが行の途中から始まり、それより後ろの行に結果が無いときだけ、その行の
    先頭を後ろ向きに探す。行が上限を超えれば、先頭の `OUTPUT_HEAD_LIMIT` バイトと末尾から `_long_line_result` が
    集計に要る項目だけを取る。
    """
    fd = open_task_output(path, allowed, roots)
    if fd is None:
        return None
    found: dict | None = None
    try:
        size = os.fstat(fd).st_size
        start = max(0, size - OUTPUT_READ_LIMIT)
        buf = os.pread(fd, size - start, start)
        size = start + len(buf)
        # 読み取りの先頭が行の途中か（直前の 1 バイトが改行でなければ途中）
        partial = start > 0 and os.pread(fd, 1, start - 1) != b"\n"
        lines = buf.split(b"\n")
        first = lines.pop(0) if partial else None
        for line in reversed(lines):
            found = result_json(line.decode("utf-8", errors="replace"))
            if found is not None:
                break
        if found is None and first is not None:
            begin = _line_start(fd, start)
            end = start + len(first)
            if end - begin <= OUTPUT_READ_LIMIT:
                found = result_json(os.pread(fd, end - begin, begin).decode("utf-8", errors="replace"))
            else:
                found = _long_line_result(os.pread(fd, OUTPUT_HEAD_LIMIT, begin), first)
    except OSError:
        return None
    finally:
        os.close(fd)
    return json.dumps(found, ensure_ascii=False) if found is not None else None


def scan_transcript(args: tuple[str, str, list[str]]) -> dict:
    """会話の記録 1 本を読む。cause の規則は Bash の結果を、起動したその呼び出しの id で 1 度だけ数える。

    バックグラウンドで起動した呼び出しは、直接の tool_result が起動応答で、結果 JSON は後から届く。起動応答のタスクの識別子を
    覚え、出力を読む Tool の呼び出し（`tasks/<識別子>.output` を読む Bash・Read、識別子を受け取る TaskOutput など）のうち
    1 つのタスクだけを読んだものの結果と、完了通知（結果を載せなければ `<output-file>` の保存済み出力）を、起動した呼び出しの
    結果として読む。複数のタスクの出力をまとめて読んだ結果は、どのタスクの結果かを決められないので数えない。
    """
    path, since, task_roots = args
    compiled = _MATCHER["compiled"]
    tool_names = _MATCHER["tools"]
    phrase_list = _MATCHER["phrases"]
    cause_rules = _MATCHER["causes"]
    cause_words = {c for _, _, c in cause_rules}
    # cause の規則の command に当たった Bash の id → 当たった規則の (名前, cause.kind)
    awaiting: dict[str, list[tuple[str, str]]] = {}
    # バックグラウンドのタスクの識別子 → 起動した Bash の id / 出力を読んだ呼び出しの id → 起動した Bash の id
    tasks: dict[str, str] = {}
    readers: dict[str, str] = {}
    # タスクの識別子 → 起動応答が示した出力のパス（完了通知の `<output-file>` を読んでよい唯一のパス）
    outputs: dict[str, str] = {}
    # 結果 JSON を取得できた、起動した Bash の id（cause の有無によらない。正常終了は cause を持たない）
    known: set[str] = set()
    caused: dict[str, set[str]] = {}

    def settle(launch: str, content) -> None:
        obj = result_json(content)
        if obj is None:
            return
        known.add(launch)
        kind = cause_kind(obj)
        if not kind:
            return
        for name, expected in awaiting[launch]:
            if kind == expected:
                caused.setdefault(name, set()).add(launch)

    bash: dict[str, list[str]] = {}
    writes: set[str] = set()
    tools: dict[str, str] = {}
    phrases: dict[str, set[str]] = {p: set() for p in phrase_list}
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return {"bash": {}, "writes": [], "tools": {}, "phrases": {}, "causes": {}, "unread": {}}
    with f:
        for line in f:
            if '"tool_use"' in line and '"assistant"' in line:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("timestamp", ""))[:10] < since:
                    continue
                for c in (row.get("message") or {}).get("content") or []:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    inp = c.get("input") or {}
                    if tasks and isinstance(inp, dict):
                        read = read_tasks(c.get("name"), inp)
                        # 1 つのタスクだけを読んだ呼び出しを対応づける。複数を読めば、どの出力の結果かを決められない
                        if len(read) == 1:
                            launch = tasks.get(next(iter(read)))
                            if launch:
                                readers[str(c.get("id"))] = launch
                    if c.get("name") == "Bash":
                        cmd = str(inp.get("command", ""))
                        hits = [name for name, rx in compiled if rx.search(cmd)]
                        if hits:
                            bash[c["id"]] = hits
                        want = [(name, kind) for name, rx, kind in cause_rules if rx.search(cmd)]
                        if want:
                            awaiting[c["id"]] = want
                    elif c.get("name") in tool_names:
                        tools[c["id"]] = tool_key(c.get("name"), inp)
                    elif c.get("name") == "Write":
                        fp = str(inp.get("file_path", ""))
                        if fp.endswith(".json") and '"steps"' in str(inp.get("content", "")):
                            writes.add(c["id"])
            elif '"tool_result"' in line and (
                any(p in line for p in phrase_list)
                or any(w in line for w in cause_words)
                or ((awaiting or readers) and any(i in awaiting or i in readers for i in TOOL_USE_ID.findall(line)))
            ):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("timestamp", ""))[:10] < since:
                    continue
                content = (row.get("message") or {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict) or c.get("type") != "tool_result":
                        continue
                    tid = str(c.get("tool_use_id"))
                    text = json.dumps(c.get("content"), ensure_ascii=False)
                    for p in phrase_list:
                        if p in text:
                            phrases[p].add(tid)
                    if tid in awaiting:
                        task = (
                            (row.get("toolUseResult") or {}).get("backgroundTaskId") if isinstance(row.get("toolUseResult"), dict) else None
                        )
                        m = BACKGROUND_TEXT.search(text)
                        task = task or (m.group(1) if m else None)
                        if isinstance(task, str) and task:
                            tasks[task] = tid
                            out = BACKGROUND_OUTPUT.search(text)
                            if out and out.group(2) == task:
                                outputs[task] = out.group(1)
                        settle(tid, c.get("content"))
                    elif tid in readers:
                        settle(readers[tid], c.get("content"))
            elif tasks and "<task-notification>" in line and any(t in tasks for t in NOTIFICATION_TASK.findall(line)):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("timestamp", ""))[:10] < since:
                    continue
                for text in _strings(row):
                    m = NOTIFICATION_TASK.search(text)
                    if not m or m.group(1) not in tasks:
                        continue
                    task = m.group(1)
                    launch = tasks[task]
                    if launch in known:
                        continue
                    rest = text[m.end() :]
                    settle(launch, rest)
                    # 実ログの完了通知は終了コードと出力のパスだけを載せる。結果は保存済み出力から読む
                    out = NOTIFICATION_OUTPUT.search(rest)
                    if launch not in known and out:
                        saved = read_output(out.group(1), outputs.get(task), task_roots)
                        if saved is not None:
                            settle(launch, saved)
    # バックグラウンドで起動し、結果 JSON を 1 度も取得できなかった回（出力が消えた・読まれなかった）。数えた回と分けて出す
    unread: dict[str, set[str]] = {}
    for launch in set(tasks.values()) - known:
        for name, _ in awaiting[launch]:
            unread.setdefault(name, set()).add(launch)
    return {
        "bash": bash,
        "writes": sorted(writes),
        "tools": tools,
        "phrases": {k: sorted(v) for k, v in phrases.items() if v},
        "causes": {k: sorted(v) for k, v in caused.items()},
        "unread": {k: sorted(v) for k, v in unread.items()},
    }


def tool_key(name: str, inp: dict) -> str:
    """AskUserQuestion は問いの `metadata.source` の接頭辞（承認ゲートの問いか、ほかの問い）で分ける。"""
    if name != "AskUserQuestion":
        return name
    src = str(((inp.get("metadata") or {}).get("source")) or "")
    return f"AskUserQuestion（{src.split(':')[0] + ':' + src.split(':')[1] if src.count(':') else src or '印なし'}）"


def count_transcripts(roots: list[str], since: str, jobs: int, rules: list[dict], task_roots: list[str] | None = None) -> dict:
    """会話の記録を数える。完了通知の保存済み出力は `task_roots`（既定 `default_task_roots`）の下のものだけを読む。"""
    task_roots = default_task_roots() if task_roots is None else list(task_roots)
    files = [str(p) for r in roots for p in pathlib.Path(r).rglob("*.jsonl")]
    files = [f for f in files if os.path.getmtime(f) >= _epoch(since)]
    bash: dict[str, list[str]] = {}
    writes: set[str] = set()
    tools: dict[str, str] = {}
    phrases: dict[str, set[str]] = collections.defaultdict(set)
    caused: dict[str, set[str]] = collections.defaultdict(set)
    unread: dict[str, set[str]] = collections.defaultdict(set)
    with ProcessPoolExecutor(max_workers=jobs, initializer=init_worker, initargs=(rules,)) as ex:
        for res in ex.map(scan_transcript, [(f, since, task_roots) for f in files], chunksize=8):
            bash.update(res["bash"])
            writes.update(res["writes"])
            tools.update(res["tools"])
            for k, v in res["phrases"].items():
                phrases[k].update(v)
            for k, v in res["causes"].items():
                caused[k].update(v)
            for k, v in res.get("unread", {}).items():
                unread[k].update(v)
    by_rule = collections.Counter(name for hits in bash.values() for name in hits)
    out = {
        "files": len(files),
        "bash_by_rule": dict(by_rule.most_common()),
        "hand_written_plans": len(writes),
        "tools": dict(collections.Counter(tools.values()).most_common()),
        "results_with_phrase": {k: len(phrases[k]) for k in _phrases(rules) if phrases.get(k)},
    }
    # cause の規則を持つ表（後の形）だけが出す。前の形の出力の形は変えない
    if any(r["kind"] == "cause" for r in rules):
        names = [r["name"] for r in rules if r["kind"] == "cause"]
        out["results_with_cause"] = {k: len(caused[k]) for k in dict.fromkeys(names) if caused.get(k)}
        # バックグラウンドの回のうち結果 JSON を読めなかった回。上の件数に入っていないことを集計で判別できるようにする
        out["results_without_cause"] = {k: len(unread[k]) for k in dict.fromkeys(names) if unread.get(k)}
    return out


def _epoch(day: str) -> float:
    return datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.timezone.utc).timestamp()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="2026-10-14", help="数え始める日（UTC の日付。両端を含む）")
    ap.add_argument("--ndf", default=".ndf", help="リポジトリの .ndf")
    ap.add_argument("--state", default=os.path.expanduser("~/.local/state/ndf"), help="プランの状態の置き場")
    ap.add_argument("--transcripts", action="append", help="会話の記録の根（複数可）")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument(
        "--task-outputs",
        action="append",
        help="完了通知の保存済み出力を読んでよいタスク出力の根（複数可。既定 /tmp/claude-<uid>）",
    )
    ap.add_argument("--skip-transcripts", action="store_true")
    a = ap.parse_args()
    rules = load_rules(RULES_FILE)
    check_measures(rules, load_measures(MEASURES_FILE))
    # 形ごとの数え（forms）を足すまで、今の出力は前の形の規則だけで数える
    before = [r for r in rules if r["form"] == "before"]
    ndf = pathlib.Path(a.ndf)
    out = {
        "since": a.since,
        "progress": count_progress(progress_files(ndf, pathlib.Path(a.state)), a.since),
        "ledger": count_ledgers(ndf, a.since),
    }
    if not a.skip_transcripts:
        out["transcripts"] = count_transcripts(a.transcripts or default_transcripts(), a.since, a.jobs, before, a.task_outputs)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
