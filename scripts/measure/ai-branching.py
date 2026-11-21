#!/usr/bin/env python3
"""#1142（#3152 から統合）の洗い出し: 開発のワークフローで AI が分岐と例外の行き先を決めた回を、実際の記録から数える。

記録を読み、回数を規則の番号（R01〜R27）ごとに数えて JSON で出す。数は「AI が出力を読んで次の手を決めた跡」の件数で、
すべてが誤りではない。入れ替えの前の形（10 系のコマンドと記録）と後の形（11 系のコマンドと選択の記録）を同じ規則の番号へ
寄せ、同じコマンドで打つ（#3412 の決定 8・9）。

    python3 scripts/measure/ai-branching.py --since 2026-11-18T00:00:00+09:00 --until 2026-12-18T00:00:00+09:00 \\
        [--ndf .ndf] [--state ~/.local/state/ndf] [--transcripts <根>]... [--task-outputs <根>]... [--jobs 6] [--skip-transcripts]

期間は半開区間 `[--since, --until)` で、両端は時差つきの時刻（日付だけと時差の無い値は終了コード 2）。どの記録も行の
時刻を時差つきの時刻に直して比べ、`--until` ちょうどの行を数えない。

- プランの記録（`progress.jsonl`）: `--ndf` の下と `--state` の下のすべてを 1 回ずつ読み、進捗と選択の記録を同じ走査で
  数える。実行の区切りと前の実行の終わり方はファイルの
  全行から組み、数えるのは期間の内の行だけ。`run` の行の `from`（途中からの再開）を直前の実行の終わり方で分け、`attention`
  を理由ごとに、`judge` のステップを決定ごとに、行を `kind` ごとに数える。id が `judge` の判断は直前のステップの行で
  テストの判断・設計との突き合わせの判断・判別できない判断に分ける（R20）
- 選択の記録（同じ `progress.jsonl` の `event: route_chosen` の行）: `plugins/ndf/contracts/schemas/route-chosen.schema.json`
  （ファイルとして読む）で確かめ、規則の番号と `chooser` ごとに数える。形の合わない行と `at` を時刻に直せない行は `rejected`
- 並列の計画の台帳（`<ndf>/parallel/*/ledger.jsonl`）: `ask` を `source` ごとに、`resume` の `take`・`settle` の `result` を数える
- 会話の記録（`--transcripts`。既定 `~/.claude/ndf/accounts/*/projects/-work-ai-plugins*`）: 規則の表の Bash の正規表現・
  Tool・tool_result の文・結果 JSON の停止理由の当たりを、規則の番号・形・役割（conductor / other / unattributed）・
  `follows_next` で数える。後の形の Bash は `&&`・`;`・`|`・改行で区切った 1 つずつのコマンドを、実行ファイルの位置から照らす。
  再開の寄せ先と `follows_next` に使う直前の結果は、NDF の結果 JSON（`tool` と決まった `status` を持つ形）だけで、
  バックグラウンドで起動した ndf のコマンドの結果は、出力を読んだ Tool の結果か完了通知で起動したコマンドへ結ぶ。
  `--until` 以降の行は解析しない
- 結果 JSON の停止理由（`kind: cause`）は、`command` の正規表現に当たる Bash の結果が `ndf ci` の結果 JSON（`tool: ci` と
  状態・停止理由の形で見分ける）で、その `cause.kind` が `cause` の値の回を、起動した呼び出しごとに 1 度だけ数える。
  バックグラウンドで起動した回は、起動応答のタスクの識別子で、後から出力を読んだ Tool の結果と完了通知を起動した回へ対応づける。
  完了通知が結果を載せなければ `<output-file>` の保存済み出力を読む（`--task-outputs`。既定 `/tmp/claude-<uid>` の下の
  `tasks/<識別子>.output` だけ）。1 つの呼び出しが複数のタスクの出力を読んだ結果は数えない。結果 JSON を 1 度も読めなかった
  バックグラウンドの回は `results_without_cause` に分けて数える
- 規則の行の `measured: false`（省けば true）は補助の件数（R09 の `gh run rerun`・番号を読めない行など）。当たりは参考に
  出し、旧い指標に対応する件数と同じ物差し（G・`value`・`all_roles`）へ足さない
- 規則の表（`ai-branching-rules.json`）と分類の表（`ai-branching-measures.json`）の形が誤っていれば終了コード 2、
  選択の記録のスキーマのファイルが無ければ終了コード 3

`plugins/ndf/src` を import しない（計測が配るコードの版に縛られないため。決定 9）。
"""

import argparse
import collections
import datetime
import glob
import json
import os
import pathlib
import re
import shlex
import stat
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（#1142 の決定 19）

require("jsonschema")
import jsonschema  # noqa: E402  選択の記録の行をスキーマで確かめる

# 数える規則（規則の番号・形・種類と、Bash の正規表現・Tool の名前・tool_result の文）。実在のエントリポイントの名前を持つため、
# コードではなくデータに置く。規則の番号を G1〜G3 へ寄せる分類の表と形ごとの出所は ai-branching-measures.json が持つ
RULES_FILE = pathlib.Path(__file__).with_name("ai-branching-rules.json")
MEASURES_FILE = pathlib.Path(__file__).with_name("ai-branching-measures.json")
# 選択の記録のスキーマ（リポジトリの根からの相対。ファイルとして読む）
SCHEMA = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "ndf" / "contracts" / "schemas" / "route-chosen.schema.json"
FORMS = ("before", "after")
# 種類ごとに要る項目。bash は正規表現、tool は Tool の名前、phrase は tool_result に現れたら数える文、
# cause は呼び出しの Bash のコマンドの正規表現と、結果 JSON の `cause.kind` の値
KINDS = {"bash": ("pattern",), "tool": ("tool",), "phrase": ("phrase",), "cause": ("command", "cause")}
# 正規表現として読む項目
REGEX_FIELDS = {"bash": "pattern", "cause": "command"}
RULE_ID = re.compile(r"^R\d{2,}$")
MEASURE_VALUES = ("G1", "G2", "G3", "none")
SOURCES = ("transcripts", "progress", "ledger", "records", "none")
ROLES = ("conductor", "other", "unattributed")
# 後の形の再開のコマンド（ndf plan next --from・ndf plan queue --from/--resume）を拾う規則。当たった回は直前の結果で
# 対象ごとに R03・R10・R13 へ寄せる（#3412 の詳細設計「後の形の R03・R10・R13」）
RESUME_RULE = ("after", "R03")
# 寄せ先を決められなかった再開（cause_unknown）は R03 の unresolved に出し、R03 と R10 を含む物差しを partial にする
UNRESOLVED_ALSO = {"R03": ("R10",)}
# R20 の判断（id が judge のステップ）の前方一致
JUDGE_PREFIX = "judge → "
# 値を取るオプション（ndf plan next / queue / wait）
PLAN_VALUE_FLAGS = ("--from", "--answer", "--state-dir", "--set", "--max", "--queue-dir", "--timeout")
# キューの記録の既定の置き場は先頭のプランの隣の `<名前>.queue`（`queue_run.queue_dir` と同じ決め方）
QUEUE_SUFFIX = ".queue"
# 保存先を省いたプランの鍵の仮の保存先（パスにならない字）。実際の保存先はプランの宣言で決まり、結果から分かるまで結ばない
OMITTED = "\x00omitted"
# リダイレクト演算子に隣接した記述子の数字の印（コマンドに現れない字）。字句を読んだ後に空白を挟んだ数字と見分ける
FD_MARK = "\x01"
# コマンドを区切る記号。改行もコマンドを終える（引用の内の改行はトークンに残る）
SEPARATORS = "();<>|&\n"
# タスク出力を開くときの印（無い OS では 0）。根・途中のディレクトリ・出力のファイルを開く _open_root と open_task_output が共有する
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def _fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(2)


# ---- 規則の表と分類の表 ----


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


def _check_row(where: str, row) -> None:
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
    if not isinstance(row.get("measured", True), bool):
        _fail(f"{where}: measured は true か false で書く（{row.get('measured')!r}）")
    if "source_prefix" in row and (row["kind"] != "tool" or not _is_text(row["source_prefix"])):
        _fail(f"{where}: source_prefix は kind tool の行に文字列で書く")


def load_rules(path: pathlib.Path) -> list[dict]:
    """規則の表を読み、行ごとに `rule`・`form`・`kind` と種類の項目を確かめて返す。

    bash と cause の行は `compiled` を持つ。`measured: false`（省けば true）の行の当たりは参考として出し、G に足さない。
    tool の行の `source_prefix` は問いの `metadata.source` の接頭辞の条件で、同じ形の同じ Tool では条件の合う行が
    条件の無い行より先に当たる。
    """
    rows = _read_json(path)
    if not isinstance(rows, list):
        _fail(f"{path}: 規則の表は配列で書く")
    out = []
    for i, row in enumerate(rows, 1):
        where = f"{path}: {i} 行目"
        _check_row(where, row)
        r = {**row, "measured": row.get("measured", True)}
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


# ---- 時刻 ----


def parse_at(value) -> datetime.datetime | None:
    """時差つきの ISO 8601 の時刻。日付だけ・時差の無い値・読めない値は None。"""
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        t = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def in_period(value, since: datetime.datetime, until: datetime.datetime) -> bool:
    t = parse_at(value)
    return t is not None and since <= t < until


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


def _judge_bucket(prev: dict | None) -> str:
    """id が judge の判断の分け先を、直前のステップの行（前の実行の分を含む）で決める（R20 の判断の行）。"""
    if prev is None:
        return "judge_unclassified_by_decision"
    if prev.get("step") == "pr" and prev.get("next") == "judge":
        return "judge_design_diff_by_decision"
    return "judge_tests_by_decision"


class _Progress:
    """旧い進捗の数え（`count_progress` の出力）を、ファイルごとに足していく。"""

    def __init__(self) -> None:
        self.resume = collections.Counter()
        self.resume_from = collections.Counter()
        self.attention = collections.Counter()
        self.judge = collections.Counter()
        self.stopped = collections.Counter()
        self.kinds = collections.Counter()
        self.split = {
            k: collections.Counter() for k in ("judge_tests_by_decision", "judge_design_diff_by_decision", "judge_unclassified_by_decision")
        }
        self.plans = 0

    def add_file(self, rows: list[dict], since: datetime.datetime, until: datetime.datetime) -> None:
        """1 つのファイルの `kind` を持つ行の並び。実行の区切りと前の実行の終わり方は全行から組み、期間の内の行だけを数える。"""
        inside = {id(r) for r in rows if in_period(r.get("at"), since, until)}
        if not inside:
            return
        self.plans += 1
        runs = plan_runs(rows)
        for i, run in enumerate(runs):
            head = run[0]
            if id(head) in inside and head.get("kind") == "run" and head.get("from") and i > 0:
                kind, sid, _ = run_end(runs[i - 1])
                same = "同じステップから" if sid == head["from"] else "別のステップから"
                self.resume[f"{kind}の後・{same}"] += 1
                self.resume_from[str(head["from"])] += 1
            kind, sid, code = run_end(run)
            steps = [r for r in run if r.get("kind") == "step"]
            if kind == "失敗" and id(steps[-1]) in inside:
                self.stopped[f"{sid} exit={code}"] += 1
        prev_step = None
        for r in rows:
            if id(r) in inside:
                self.kinds[str(r["kind"])] += 1
                if r["kind"] == "attention":
                    self.attention[str(r.get("reason"))] += 1
                if r["kind"] == "step" and r.get("type") == "judge":
                    key = f"{r.get('step')} → {r.get('decision') or r.get('next')}"
                    self.judge[key] += 1
                    if r.get("step") == "judge":
                        self.split[_judge_bucket(prev_step)][key] += 1
            if r["kind"] == "step":
                prev_step = r

    def out(self) -> dict:
        return {
            "plans": self.plans,
            "resume_by_previous_end": dict(self.resume.most_common()),
            "resume_from_step": dict(self.resume_from.most_common(20)),
            "attention_by_reason": dict(self.attention.most_common()),
            "judge_by_decision": dict(self.judge.most_common()),
            "stopped_by_step_exit": dict(self.stopped.most_common()),
            "kinds": dict(self.kinds.most_common()),
            **{k: dict(v.most_common()) for k, v in self.split.items()},
        }


class _Choices:
    """`event: route_chosen` の行をスキーマで確かめ、規則の番号と `chooser` ごとに足していく。"""

    def __init__(self, schema: dict) -> None:
        self.validator = jsonschema.Draft202012Validator(schema)
        self.by_rule: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.rejected = 0
        self.files = 0

    def add(self, row, since: datetime.datetime, until: datetime.datetime) -> None:
        """1 行（JSON として読めなければ None）。`at` を時刻に直せない行は期間に入るか確かめられないので `rejected` に入れる。"""
        if row is None:
            self.rejected += 1
            return
        at = parse_at(row.get("at"))
        if at is None:
            self.rejected += 1
            return
        if not since <= at < until:
            return
        if not self.validator.is_valid(row):
            self.rejected += 1
            return
        self.by_rule[row["rule"]][row["chooser"]] += 1

    def out(self) -> dict:
        return {"by_rule": {k: dict(v) for k, v in sorted(self.by_rule.items())}, "rejected": self.rejected, "files": self.files}


def count_plan_records(
    files: list[pathlib.Path], since: datetime.datetime, until: datetime.datetime, schema: dict | None
) -> tuple[dict, dict | None]:
    """`progress.jsonl` を 1 つずつ 1 回だけ読み、旧い進捗の数えと選択の記録の数え（`schema` が None なら None）を返す。"""
    progress = _Progress()
    choices = _Choices(schema) if schema is not None else None
    for path in files:
        rows: list[dict] = []
        found = False
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        if choices is not None and "route_chosen" in line:
                            found = True
                            choices.add(None, since, until)
                        continue
                    if not isinstance(row, dict):
                        continue
                    if row.get("event") == "route_chosen":
                        found = True
                        if choices is not None:
                            choices.add(row, since, until)
                    elif row.get("kind"):
                        rows.append(row)
        except OSError:
            continue
        progress.add_file(rows, since, until)
        if choices is not None:
            choices.files += found
    return progress.out(), choices.out() if choices is not None else None


def count_progress(files: list[pathlib.Path], since: datetime.datetime, until: datetime.datetime) -> dict:
    """旧い進捗の数えだけ（`count_plan_records` の片方）。"""
    return count_plan_records(files, since, until, None)[0]


def count_choices(files: list[pathlib.Path], since: datetime.datetime, until: datetime.datetime, schema: dict) -> dict:
    """選択の記録の数えだけ（`count_plan_records` の片方）。"""
    out = count_plan_records(files, since, until, schema)[1]
    assert out is not None
    return out


# ---- 並列の計画の台帳 ----


def count_ledgers(ndf: pathlib.Path, since: datetime.datetime, until: datetime.datetime) -> dict:
    ask = collections.Counter()
    resume = collections.Counter()
    kinds = collections.Counter()
    for path in sorted((ndf / "parallel").glob("*/ledger.jsonl")):
        for r in read_jsonl(path):
            if not isinstance(r, dict) or not in_period(r.get("at"), since, until):
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


def _follows(suggested: dict, taken: list[str]) -> bool:
    """打った引数が `next` の argv で始まり、残りが ask のキーの `--<キー> <値>` の組だけか。

    `plugins/ndf/src/ndf/contracts/chosen.py` の `_follows` と同じ規則を写したもの（import しない。決定 9）。
    """
    argv = suggested["argv"]
    if list(taken[: len(argv)]) != list(argv):
        return False
    rest = list(taken[len(argv) :])
    asked = {f"--{key}" for key in (suggested["ask"] if "ask" in suggested else ())}
    flags = rest[0::2]
    return len(rest) % 2 == 0 and len(flags) == len(set(flags)) and set(flags) <= asked


def _strip_comments(cmd: str) -> str:
    """引用の外で単語の先頭に現れる `#` から行末の手前までを落とし、継続行（引用の外と `"` の内の行末の `\\`）をつなぐ。

    引用の外で単語の先頭にあり、リダイレクト演算子（`<`・`>`）に隣接した数字には `FD_MARK` を前に付ける（記述子の印）。

    注釈の本文は字句解析しない（閉じていない引用符があっても読める）。注釈を終える改行はコマンドの区切りとして残す。
    """
    out: list[str] = []
    quote: str | None = None
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote == "'":
            out.append(ch)
            quote = None if ch == "'" else quote
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            if cmd[i + 1] != "\n":
                out.append(cmd[i : i + 2])
            i += 2
            continue
        if quote == '"':
            out.append(ch)
            quote = None if ch == '"' else quote
            i += 1
            continue
        at_word = not out or out[-1] in " \t\r" + SEPARATORS
        if ch.isdigit() and at_word:
            end = i
            while end < n and cmd[end].isdigit():
                end += 1
            if end < n and cmd[end] in "<>":
                # リダイレクト演算子に隣接した数字（`2>`）だけを記述子の印つきにする（`--timeout 30 > f` の 30 は引数）
                out.append(FD_MARK + cmd[i:end])
                i = end
                continue
        if ch in "'\"":
            quote = ch
        elif ch == "#" and at_word:
            end = cmd.find("\n", i)
            i = n if end < 0 else end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# リダイレクトの行き先のうち、ファイルでなく今の記述子を指すもの（`/dev/stdout` は今の標準出力）
_FD_TARGETS = {"/dev/stdout": 1, "/dev/stderr": 2, "/dev/fd/1": 1, "/dev/fd/2": 2}
# 入力の行をそのまま出しうるパイプの後段（行を選ぶだけで書き換えない）と、値を取るオプション。ほかの後段（wc・
# フィールドを抜く jq など）は結果 JSON を変えるとみる。jq は `jq -c .` のように 1 行のまま出す形だけを通す（_jq_echoes）
_PASS_THROUGH = {
    "cat": (),
    "tee": (),
    "head": ("-n", "-c", "--lines", "--bytes"),
    "tail": ("-n", "-c", "-s", "--lines", "--bytes", "--pid", "--sleep-interval"),
    "grep": ("-e", "-f", "-m", "-A", "-B", "-C", "--regexp", "--file", "--max-count", "--after-context", "--before-context", "--context"),
}
# jq の値を取るオプション（値の数）
_JQ_VALUE_FLAGS = {"--arg": 2, "--argjson": 2, "--slurpfile": 2, "--rawfile": 2, "--indent": 1, "-L": 1}
# 記述子の行き先: ("fd", k) は単位の元の記述子 k（外の文脈かパイプ）、_HIDDEN はファイルか閉じた記述子
_HIDDEN = ("file", 0)


def _operands(rest: list[str], value_flags) -> list[str]:
    """オプションとその値を除いた引数（`--` の後はすべて引数）。"""
    out: list[str] = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t == "--":
            return out + rest[i + 1 :]
        if t.startswith("-") and t != "-":
            i += 2 if t in value_flags else 1
            continue
        out.append(t)
        i += 1
    return out


def _jq_echoes(rest: list[str]) -> bool:
    """`jq` が標準入力の結果オブジェクトを 1 行のまま出すか（フィルタが `.` で `-c`。`-n`・`-s`・`-f`・入力のファイルは除く）。"""
    short = "".join(t[1:] for t in rest if t.startswith("-") and not t.startswith("--") and t != "-")
    longs = {t for t in rest if t.startswith("--")}
    if "n" in short or "s" in short or "f" in short or longs & {"--null-input", "--slurp", "--from-file", "--args", "--jsonargs"}:
        return False
    if "c" not in short and "--compact-output" not in longs:
        return False
    ops: list[str] = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t in _JQ_VALUE_FLAGS:
            i += 1 + _JQ_VALUE_FLAGS[t]
            continue
        if not (t.startswith("-") and t != "-"):
            ops.append(t)
        i += 1
    return len(ops) == 1 and ops[0].strip() == "."


def _echoes_pipe(unit: dict) -> bool:
    """パイプの後段が、標準入力をパイプから受け取り、結果 JSON の行をそのまま出しうるか。

    標準入力をリダイレクトで差し替えた後段（`cat < notes.txt`）と、入力のファイルを渡した後段（`cat notes.txt`）は
    前段を読まない。grep は `-e`・`-f` が無ければ最初の引数をパターンとみ、`-r` でファイルが無ければ作業場所を読む。
    """
    if unit["kind"] != "cmd" or not unit.get("stdin", True):
        return False
    name, rest = os.path.basename(unit["args"][0]), unit["args"][1:]
    if name == "jq":
        return _jq_echoes(rest)
    if name not in _PASS_THROUGH:
        return False
    if name == "tee":
        return True  # 引数は書き出すファイル
    ops = _operands(rest, _PASS_THROUGH[name])
    if name == "grep":
        flags = [t for t in rest if t.startswith("-") and t != "-"]
        if not any(t in ("-e", "-f", "--regexp", "--file") or t.startswith(("--regexp=", "--file=")) for t in flags):
            ops = ops[1:]  # パターン
        if not ops and any(t in ("-r", "-R", "--recursive", "--dereference-recursive") for t in flags):
            return False
    return not ops or "-" in ops


def _target_of(word: str, dup: bool, fds: dict) -> tuple[str, int]:
    """リダイレクトの行き先の語から、記述子の行き先（その時点の `fds` を写す）。"""
    if dup and word.isdigit():
        return fds.get(int(word), ("fd", int(word)))
    if word in _FD_TARGETS:
        return fds.get(_FD_TARGETS[word], ("fd", _FD_TARGETS[word]))
    return _HIDDEN


def _resolve_shown(body: list[dict], env: dict[int, bool]) -> None:
    """単位の並びの各コマンドの標準出力が会話に出るかを、外の文脈の記述子 `env`（出るか）から決めて `shown` に書く。

    パイプの前段の標準出力は後段へ入り、後段がパイプから読んだ入力をそのまま出すコマンド（`_echoes_pipe`）で、その標準出力が
    出るときだけ出る。
    """
    outs: list[bool] = [False] * len(body)
    for i in range(len(body) - 1, -1, -1):
        item = body[i]
        base = dict(env)
        if item["pipe"] and i + 1 < len(body):
            passes = _echoes_pipe(body[i + 1]) and outs[i + 1]
            base[1] = passes
            if item["pipe"] == "|&":
                base[2] = passes
        fds = item["fds"]

        def shown(k: int) -> bool:
            ref = fds.get(k, ("fd", k))
            return ref != _HIDDEN and base.get(ref[1], False)

        outs[i] = shown(1)
        if item["kind"] == "cmd":
            item["event"]["shown"] = outs[i]
        else:
            _resolve_shown(item["body"], {k: shown(k) for k in {1, 2, *fds}})


def parse_bash(cmd: str) -> list[dict]:
    """Bash のコマンドを、コマンドとサブシェルの出入りの並びに読む。読めなければ空。

    コマンドは `{"kind": "cmd", "args": [...], "shown": bool}`（`shown` は標準出力が最後に会話へ出るか）、
    サブシェルの出入りは `{"kind": "("}`・`{"kind": ")"}`。`&&`・`;`・`|`・コマンドを終える改行などで区切る。
    リダイレクトは Bash と同じく左から順に記述子を付け替え、サブシェルの中のコマンドはサブシェルの記述子を継ぐ。
    """
    try:
        lex = shlex.shlex(_strip_comments(cmd), posix=True, punctuation_chars=SEPARATORS)
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        lex.commenters = ""  # 注釈は _strip_comments で落とした
        tokens = list(lex)
    except ValueError:
        return []
    events: list[dict] = []
    root: list[dict] = []
    bodies: list[list[dict]] = [root]  # 開いているサブシェルの中身（先頭は全体）
    groups: list[dict] = []  # 開いているサブシェル

    def new_unit() -> dict:
        return {"kind": "cmd", "args": [], "fds": {}, "pipe": None}

    unit = new_unit()
    closed: dict | None = None  # 直前に閉じたサブシェル（続くリダイレクトとパイプの受け手）
    pending: tuple[dict, list[int], bool] | None = None  # 行き先を待つリダイレクト: (受け手, 付け替える記述子, `>&` の形か)

    def end_unit() -> None:
        nonlocal unit
        if unit["args"]:
            unit["event"] = {"kind": "cmd", "args": unit["args"], "shown": True}
            events.append(unit["event"])
            bodies[-1].append(unit)
        unit = new_unit()

    def receiver() -> dict:
        return closed if closed is not None and not unit["args"] else unit

    for t in tokens:
        if not (t and all(c in SEPARATORS for c in t)):
            if pending is not None:
                owner, fds, dup = pending
                pending = None
                ref = _HIDDEN if dup and t == "-" else _target_of(t, dup, owner["fds"])
                if dup and not t.isdigit() and t != "-" and fds == [1]:
                    fds = [1, 2]  # `>&ファイル` は `&>` と同じ
                for n in fds:
                    owner["fds"][n] = ref
                continue
            if not (t.startswith(FD_MARK) and not unit["args"]):
                closed = None  # `) 2>log` の 2 は閉じたサブシェルへのリダイレクトの記述子
            unit["args"].append(t)
            continue
        for piece in re.findall(r"[()]|[^()]+", t):
            if piece == "(":
                end_unit()
                group = {"kind": "group", "body": [], "fds": {}, "pipe": None}
                bodies[-1].append(group)
                bodies.append(group["body"])
                groups.append(group)
                events.append({"kind": "("})
                closed = None
            elif piece == ")":
                end_unit()
                events.append({"kind": ")"})
                closed = groups.pop() if groups else None
                if len(bodies) > 1:
                    bodies.pop()
            elif "<" in piece or ">" in piece:
                # リダイレクトは向きの印と行き先を落とす（2>&1 の 2 も）。出力の記述子の付け替えと標準入力の差し替えを受け手に残す
                fd = unit["args"].pop()[len(FD_MARK) :] if unit["args"] and unit["args"][-1].startswith(FD_MARK) else None
                owner = receiver()
                if piece.startswith("<"):
                    # 入力は出力の行き先を変えない。標準入力を差し替えたら、パイプの前段を読まない
                    if fd is None or fd == "0":
                        owner["stdin"] = False
                    pending = (owner, [], False)
                elif piece.startswith("&"):
                    pending = (owner, [1, 2], False)
                else:
                    pending = (owner, [int(fd) if fd is not None else 1], piece.endswith("&"))
            elif piece in ("|", "|&"):
                owner = receiver()
                owner["pipe"] = piece
                end_unit()
                closed = None
            else:
                end_unit()
                closed = None
    end_unit()
    _resolve_shown(root, {1: True, 2: True})
    return events


def segments(cmd: str) -> list[list[str]]:
    """Bash のコマンドを `&&`・`;`・`|`・コマンドを終える改行などで区切った、引数の並びの組。読めなければ空。

    継続行（行末の `\\`）はつなぎ、引用の内の改行はトークンに残す。引用の外で単語の先頭の `#` から行末までは注釈として落とす。
    """
    return [e["args"] for e in parse_bash(cmd) if e["kind"] == "cmd"]


# コマンドの前に置く変数の代入（`NAME=値 ndf …`）
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


# コマンドの前に置く Bash の予約語（`if ndf ci watch …; then …` の `if`・`then` など）。続く語がコマンドの位置になる
SHELL_KEYWORDS = frozenset({"if", "then", "elif", "else", "while", "until", "do", "!", "{"})
# 後ろの引数のコマンドを実行する組み込みと Tool: 名前 → (値を 1 つ取るオプション, オプションの後でコマンドの前に置く被演算子の数)
COMMAND_WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "command": (frozenset(), 0),
    "builtin": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "nohup": (frozenset(), 0),
    "time": (frozenset(), 0),
    "env": (frozenset({"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}), 0),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "timeout": (frozenset({"-s", "-k", "--signal", "--kill-after"}), 1),
}
# `command` のうち、コマンドを実行せずに在りかを出すオプション
COMMAND_LOOKUP = frozenset({"-v", "-V"})


def command_words(seg: list[str]) -> list[str]:
    """Bash が実行するコマンドの位置からの引数の並び。実行しなければ空。

    先頭の `名前=値` の代入、予約語（`if`・`then`・`do`・`!`・`{` など）、後ろの引数のコマンドを実行する組み込みと
    Tool（`command`・`exec`・`env`・`timeout` など）とそのオプションを飛ばす。`printf`・`echo` などの引数に書かれた
    コマンドは、その Tool の引数のまま残す（コマンドの位置にしない）。
    """
    return _command_start(seg, None)[0]


def command_cwd(seg: list[str], cwd: str | None) -> str | None:
    """コマンドを実行する作業場所。`env -C <先>`・`env --chdir <先>` が変えた先を `cwd` から引く（`cd` と違い後に残らない）。"""
    return _command_start(seg, cwd)[1]


def _command_start(seg: list[str], cwd: str | None) -> tuple[list[str], str | None]:
    """`command_words` の並びと、`env` の作業場所の指定を反映した作業場所。"""
    i = 0
    while i < len(seg):
        t = seg[i]
        if ASSIGNMENT.match(t) or t in SHELL_KEYWORDS:
            i += 1
            continue
        name = os.path.basename(t)
        if name not in COMMAND_WRAPPERS:
            return seg[i:], cwd
        values, operands = COMMAND_WRAPPERS[name]
        i += 1
        while i < len(seg) and seg[i].startswith("-") and seg[i] != "-":
            o = seg[i]
            if o == "--":
                i += 1
                break
            if name == "command" and o in COMMAND_LOOKUP:
                return [], cwd
            if name == "env":
                if o in ("-C", "--chdir") and i + 1 < len(seg):
                    cwd = _abs(seg[i + 1], cwd)
                elif o.startswith("--chdir="):
                    cwd = _abs(o.partition("=")[2], cwd)
                elif o.startswith("-C") and not o.startswith("--") and len(o) > 2:
                    cwd = _abs(o[2:], cwd)
            i += 2 if o in values else 1
        i += operands
    return [], cwd


def _ndf_args(seg: list[str]) -> list[str] | None:
    """`ndf` を打つ引数の並び（`command_words` の位置から、先頭を `ndf` にそろえる）。ndf でなければ None。"""
    words = command_words(seg)
    if words and os.path.basename(words[0]) == "ndf":
        return ["ndf", *words[1:]]
    return None


def command_text(seg: list[str]) -> str:
    """1 つのコマンドの実行ファイル（`command_words` の位置。パスは名前だけ）から始まる、引数を空白でつないだ文。

    後の形の規則は、この文の先頭から照らす（`printf … 'ndf plan next …'` の引数の中の文をコマンドとして数えない）。
    """
    rest = command_words(seg)
    return " ".join([os.path.basename(rest[0]), *rest[1:]]) if rest else ""


def _abs(path: str, cwd: str | None) -> str:
    """パスを作業場所から引いた絶対のパスに直す（ファイルは見ない）。作業場所が分からない相対のパスは字面のまま正規化する。"""
    p = os.path.expanduser(path)
    if not os.path.isabs(p) and cwd:
        p = os.path.join(cwd, p)
    return os.path.normpath(p)


def plan_call(seg: list[str], cwd: str | None = None) -> dict | None:
    """`ndf plan next|queue|wait …` の引数。ほかのコマンドは None。

    `ids` はプランごとの鍵（作業場所から引いた絶対のパスと状態の保存先。同じプランを相対と絶対のどちらで指しても同じ鍵）、
    `given` はプランごとの `--state-dir`（絶対のパス。省いたら None）、`queue` はキューの記録の置き場（`--queue-dir` か
    先頭のプランの隣の `<名前>.queue`。queue のときだけ）。

    保存先を省いた形の鍵は、プランの宣言を読まずに省略形の仮キー（`<プラン>|` と `OMITTED`）にする。実際の保存先は宣言の
    有無で `<名前>.state` にもほかの先にもなり、計測の時点のプランのファイルは片付け・書き換えで変わるため。`--state-dir` を
    明示した鍵とは分け、結果が示したコマンドで実際の保存先が分かった時点で `_Conversation` が仮キーをその保存先の鍵へ結ぶ。
    """
    args = _ndf_args(seg)
    if args is None or len(args) < 3 or args[1] != "plan" or args[2] not in ("next", "queue", "wait"):
        return None
    rest = args[3:]
    plans: list[str] = []
    starts: list[str] = []
    flags: set[str] = set()
    values: dict[str, str] = {}
    i = 0
    while i < len(rest):
        t = rest[i]
        name, eq, value = t.partition("=")
        if t.startswith("--") and eq:
            opt, val, i = name, value, i + 1
        elif t in PLAN_VALUE_FLAGS:
            # 値が欠けた末尾のオプションは、値を記録せずに名前だけ数える
            opt, val, i = t, (rest[i + 1] if i + 1 < len(rest) else None), i + 2
        elif t == "--then":
            # --then の後のプランも、結果の items と同じ並びで plans に入れる
            i += 1
            while i < len(rest) and not rest[i].startswith("--"):
                plans.append(rest[i])
                i += 1
            continue
        elif t.startswith("--"):
            flags.add(t)
            i += 1
            continue
        else:
            plans.append(t)
            i += 1
            continue
        flags.add(opt)
        if val is not None:
            values[opt] = val
            if opt == "--from":
                starts.append(val)
    # queue の子は --state-dir を渡さずに流すので、queue のプランは保存先を省いた形の鍵にする
    state = values.get("--state-dir") if args[2] != "queue" and len(plans) == 1 else None
    paths = [_abs(p, cwd) for p in plans]
    given = [_abs(state, cwd) if state is not None else None for _ in plans]
    ids = [f"{p}|{g if g is not None else OMITTED}" for p, g in zip(paths, given)]
    queue = None
    if args[2] == "queue" and plans:
        qdir = values.get("--queue-dir")
        queue = _abs(qdir, cwd) if qdir else os.path.splitext(paths[0])[0] + QUEUE_SUFFIX
    return {
        "sub": args[2],
        "plans": plans,
        "paths": paths,
        "given": given,
        "cwd": cwd,
        "ids": ids,
        "queue": queue,
        "from": starts,
        "answer": "--answer" in flags,
        "resume": "--resume" in flags,
    }


# NDF の結果 JSON の `status` の値（`plugins/ndf/contracts/schemas/result.schema.json`）
RESULT_STATUSES = ("ok", "gate", "stopped")


def _is_command(c) -> bool:
    """結果が示す打つコマンドの形（`argv` は空でない文字列の空でない配列、`ask` は省くか文字列の配列）か。

    前の形の結果は `python3 supervise.py …` を示すので、先頭を `ndf` に限らない。
    """
    if not isinstance(c, dict):
        return False
    argv, ask = c.get("argv"), c.get("ask", [])
    return (
        isinstance(argv, list)
        and bool(argv)
        and all(isinstance(a, str) and a for a in argv)
        and isinstance(ask, list)
        and all(isinstance(k, str) for k in ask)
    )


# 結果 JSON が持てば型の決まった項目（`next` は None も受けるので別に見る）
RESULT_FIELD_TYPES = {"items": list, "routes": dict, "cause": dict}


def is_ndf_result(obj) -> bool:
    """NDF の結果 JSON の形か。`tool` と決まった `status` を持ち、`items`・`routes`・`next`・`cause` は持てば契約の型。

    ほかの Tool の出力にも `status` を持つ JSON（`gh run view --json status` など）があるので、`status` だけでは決めない。
    `next` は後の形の打つコマンド（object）のほか、前の形の `step_result.py` が出す案内の文（文字列）も受ける。文字列の
    `next` の結果も直前の結果にし（先行する別の結果の `argv` を残さない）、コマンドの照合には `argv` を持つ案内だけを使う
    （`_commands`）。
    """
    if not isinstance(obj, dict) or not _is_text(obj.get("tool")) or obj.get("status") not in RESULT_STATUSES:
        return False
    if any(key in obj and not isinstance(obj[key], kind) for key, kind in RESULT_FIELD_TYPES.items()):
        return False
    return "next" not in obj or obj["next"] is None or isinstance(obj["next"], (dict, str))


# Read の Tool が行の前に付ける行番号（`     1→`・`1\t`）
READ_LINE_NUMBER = re.compile(r"^\d+(?:→|\t)")


def result_jsons(content) -> list[dict]:
    """tool_result の中身から、NDF の結果 JSON（`is_ndf_result` に当たる 1 行のオブジェクト）を出た順に。"""
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        texts += [str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("type") == "text"]
    found = []
    for text in texts:
        for line in text.splitlines():
            line = READ_LINE_NUMBER.sub("", line.strip(), count=1)
            if not line.startswith("{") or '"status"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_ndf_result(obj):
                found.append(obj)
    return found


def pair_results(commands: list[tuple[list[str], dict | None, bool]], results: list[dict]) -> list[tuple[dict | None, dict]]:
    """1 つの Bash の結果 JSON を出た順に、それを出した ndf のコマンドの `plan_call`（plan でなければ None）と組む。

    `commands` は（引数, plan_call, 標準出力が会話に出うるか）の並び。結果の `tool` はサブコマンドの名前なので、標準出力が
    会話に出うるコマンドのうち名前の合うものと出た順に組む。名前の合う結果とコマンドの数が違う（どれかが出さなかった・
    ほかの出力が混じった）ときは対応を一意に決められないので、その名前の結果はすべて None と組む。`tool` の無い結果も None。
    """
    shown: dict[str, list[dict | None]] = collections.defaultdict(list)
    for args, call, visible in commands:
        if visible and len(args) > 1:
            shown[args[1]].append(call)
    counts = collections.Counter(r.get("tool") for r in results if isinstance(r.get("tool"), str))
    taken: collections.Counter = collections.Counter()
    out: list[tuple[dict | None, dict]] = []
    for res in results:
        tool = res.get("tool")
        if not isinstance(tool, str) or counts[tool] != len(shown.get(tool, ())):
            out.append((None, res))
            continue
        out.append((shown[tool][taken[tool]], res))
        taken[tool] += 1
    return out


def _cause_kind(obj: dict) -> str | None:
    c = obj.get("cause")
    return c["kind"] if isinstance(c, dict) and isinstance(c.get("kind"), str) else None


def _plan_items(res: dict) -> list[dict]:
    items = res.get("items")
    return [
        it
        for it in (items if isinstance(items, list) else [])
        if isinstance(it, dict) and it.get("kind") == "plan" and isinstance(it.get("name"), str)
    ]


def _commands(res: dict | None) -> list[dict]:
    """結果が提示した打つコマンド: `next` と、承認ゲートの `routes`（結果と items の行の分）の各回答。"""
    if res is None:
        return []
    routes = res.get("routes")
    found = [res.get("next"), *(routes.values() if isinstance(routes, dict) else ())]
    items = res.get("items")
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("routes"), dict):
            found += list(it["routes"].values())
    return [c for c in found if _is_command(c)]


def _is_resume(call: dict | None) -> bool:
    """プランの再開のコマンドか: `--from` を持つ `ndf plan next`、`--from` か `--resume` を持つ `ndf plan queue`。"""
    if call is None:
        return False
    return bool(call["from"]) if call["sub"] == "next" else call["sub"] == "queue" and bool(call["from"] or call["resume"])


def _route_of(row: dict, top: dict, name: str | None) -> tuple[str, str]:
    """対象の行（単独ならトップレベル）から、再開の寄せ先と根拠（後の形の R03・R10・R13 の 1〜4）。"""
    if row.get("status") == "gate" or row.get("gate") is True:
        return "R13", "gate"
    kind = _cause_kind(row)
    if kind is None and name is not None and row.get("status") == "stopped":
        stopped = [it for it in _plan_items(top) if it.get("status") == "stopped"]
        if str(top.get("summary", "")).startswith(f"{name}: ") or (len(stopped) == 1 and stopped[0] is row):
            kind = _cause_kind(top)
    if kind == "base_conflict":
        return "R10", "base_conflict"
    if row.get("status") == "stopped":
        return ("R03", "stopped") if kind is not None else ("R03", "cause_unknown")
    return "R03", "other"


class _Conversation:
    """1 つの会話の中で、再開の寄せ先と follows_next を決めるための直前の結果。

    再開の対象ごとに、同じプランを対象にした直近の `ndf plan next`・`queue`・`wait` の結果の行を引く（サブコマンドを
    またいで新しいほう）。プランは作業場所から引いた絶対のパスと状態の保存先の組（`plan_call` の `ids`）で引き、名前は
    会話全体でなく、そのコマンドに渡したプランの中で解決する。別のディレクトリの同じ名前のプランの結果を混ぜない。

    `queue --resume` が流さない完了済みのプランは、分類に使う直近の結果と別に、キューの記録の置き場（`--queue-dir`）
    ごとの queue の結果で追う（`queue.json` と同じく、プランの名前ごとに最後に記録された status）。
    """

    def __init__(self) -> None:
        self.last: list[dict] = []  # 直前の tool_result の結果 JSON（どのコマンドでも。出た順）
        self.seq = 0
        # 対象の鍵 → (順番, 対象の行, 結果のトップレベル, 行の名前。単独の結果は None)。鍵は `id:<プランの鍵>` と、
        # items の数が渡したプランと合わずパスと名前を結べない queue・wait の行の `group:<プランの鍵の組>|<名前>`
        self.latest: dict[str, tuple[int, dict, dict, str | None]] = {}
        self.name_of: dict[str, str] = {}  # プランの鍵 → プランの名前
        self.group_names: dict[str, list[str]] = {}  # プランの鍵の組 → パスと結べなかった items の名前
        self.done: dict[str, dict[str, str]] = {}  # キューの記録の置き場 → プランの名前 → queue の結果の status
        self.answered: dict[str, bool] = {}  # プランの鍵 → conductor が直前に打った ndf plan next が --answer を持つか
        self.alias: dict[str, str] = {}  # 保存先を省いた形の仮キー → 結果が示した実際の保存先の鍵

    def ids(self, call: dict) -> list[str]:
        """そのコマンドのプランごとの鍵。省略形の仮キーは、結果で実際の保存先が分かっていればその保存先の鍵に寄せる。"""
        return [self.alias.get(pid, pid) for pid in call["ids"]]

    def _group(self, call: dict) -> str:
        return "\x00".join(sorted(self.ids(call)))

    def _bind(self, omitted: str, actual: str) -> None:
        """省略形の仮キーを実際の保存先の鍵に結ぶ。まだ結んでいない仮キーの履歴だけを、新しければその保存先の鍵へ移す。

        結び直し（プランの宣言が変わった）では、前の保存先の鍵に残った履歴をその保存先のものとして動かさない。
        """
        if self.alias.get(omitted) == actual:
            return
        if omitted not in self.alias:
            old, new = f"id:{omitted}", f"id:{actual}"
            if old in self.latest and (new not in self.latest or self.latest[new][0] < self.latest[old][0]):
                self.latest[new] = self.latest[old]
            self.latest.pop(old, None)
            if omitted in self.name_of:
                self.name_of.setdefault(actual, self.name_of.pop(omitted))
            if omitted in self.answered:
                self.answered[actual] = self.answered.pop(omitted)
        self.alias[omitted] = actual

    def _learn(self, call: dict, res: dict) -> None:
        """保存先を省いて打ったプランの結果が、同じプランを `--state-dir <先>` で打つコマンドを示したら、仮キーをその先に結ぶ。

        承認待ちの `next` が返す `wait <プラン> --state-dir <先>` など。プランが `state_dir` を宣言していても、実行時の結果に
        残った保存先で `wait --state-dir <先>` と省略形の再開を同じ鍵にする（計測の時点のプランのファイルを読まない）。
        結果が保存先を示すまでは、`--state-dir` を明示した実行（同じプランの別の保存先かもしれない）と履歴を共有しない。
        """
        for path, given, raw in zip(call["paths"], call["given"], call["ids"]):
            if given is not None:
                continue
            for c in _commands(res):
                seen = plan_call([str(a) for a in c["argv"]], call["cwd"])
                if seen is not None and seen["paths"] == [path] and seen["given"][0] is not None:
                    self._bind(raw, seen["ids"][0])
                    break

    def _put(self, key: str, row: dict, top: dict, name: str | None) -> None:
        self.latest[key] = (self.seq, row, top, name)

    def saw_result(self, call: dict | None, res: dict) -> None:
        if call is None or not call["ids"]:
            return
        self._learn(call, res)
        self.seq += 1
        items = _plan_items(res)
        ids = self.ids(call)
        if call["sub"] == "next":
            pid = ids[0]
            if items:
                self.name_of[pid] = items[0]["name"]
            self._put(f"id:{pid}", res, res, None)
            return
        # queue / wait の items は渡したプランの並び。数が合うときだけプランの鍵と名前を結ぶ
        if len(items) == len(ids):
            for pid, it in zip(ids, items):
                self.name_of[pid] = it["name"]
                self._put(f"id:{pid}", it, res, it["name"])
        else:
            group = self._group(call)
            self.group_names[group] = [it["name"] for it in items]
            for it in items:
                self._put(f"group:{group}|{it['name']}", it, res, it["name"])
        if call["sub"] == "queue" and call["queue"] is not None:
            record = self.done.setdefault(call["queue"], {})
            for it in items:
                if isinstance(it.get("status"), str):
                    record[it["name"]] = it["status"]

    def _keys(self, call: dict, name: str | None, pid: str | None = None) -> list[str]:
        """そのコマンドに渡したプランの中で、プランの鍵 `pid` か名前 `name` に当たる対象の鍵。"""
        keys = [f"id:{pid}"] if pid is not None else []
        if name is not None:
            keys += [f"id:{p}" for p in self.ids(call) if p != pid and self.name_of.get(p) == name]
            keys.append(f"group:{self._group(call)}|{name}")
        return keys

    def _lookup(self, keys: list[str]) -> tuple[dict, dict, str | None] | None:
        found = [self.latest[k] for k in keys if k in self.latest]
        if not found:
            return None
        _, row, top, row_name = max(found, key=lambda e: e[0])
        return row, top, row_name

    def _answered(self, call: dict, name: str | None, pid: str | None = None) -> bool:
        pids = [pid] if pid is not None else []
        pids += [p for p in self.ids(call) if name is not None and self.name_of.get(p) == name]
        return any(self.answered.get(p) for p in pids)

    def _route(self, found: tuple[dict, dict, str | None] | None) -> tuple[str, str]:
        if found is None:
            return "R03", "no_prior_result"
        row, top, name = found
        return _route_of(row, top, name)

    def _target(self, call: dict, name: str | None, pid: str | None = None) -> tuple[str, str]:
        if self._answered(call, name, pid):
            return "R13", "answer"
        return self._route(self._lookup(self._keys(call, name, pid)))

    def targets(self, call: dict | None) -> list[tuple[str, str]]:
        """再開のコマンドの対象ごとの（寄せ先, 根拠）。

        `--resume` の対象は、そのキューの記録で完了済みと確認できない、そのコマンドに渡したプラン。履歴の無いプランも対象ごとに
        `no_prior_result` で数える。すべて完了済みか `--from` の対象と分かれば `--resume` の分は足さない。
        """
        if call is None:
            return [("R03", "no_prior_result")]
        if call["sub"] == "next":
            return [self._target(call, None, self.ids(call)[0] if self.ids(call) else "")]
        names = [s.partition("=")[0] for s in call["from"]]
        out = [self._target(call, name) for name in names]
        if call["resume"]:
            done = self.done.get(call["queue"] or "", {})
            known = [(pid, self.name_of.get(pid)) for pid in self.ids(call)]
            seen = {n for _, n in known if n is not None}
            extra = [n for n in self.group_names.get(self._group(call), []) if n not in seen]
            # 名前の分からないプランは、まず名前だけ分かる --from の対象と、パスと結べなかった items の名前に当てる（重ねて数えない）
            absorb = len([n for n in names if n not in seen]) + len(extra)
            for pid, name in [*known, *((None, n) for n in extra)]:
                if name is not None and (name in names or done.get(name) == "ok"):
                    continue  # --from の対象か、前の回で完了した（--resume は流さない）
                if name is None and absorb and not any(k in self.latest for k in self._keys(call, None, pid)):
                    absorb -= 1
                    continue
                out.append(self._target(call, name, pid))  # 履歴の無い初めてのプランは no_prior_result
        if call["resume"]:
            return out  # 空ならすべて完了済み（架空の対象を足さない）
        return out or [("R03", "no_prior_result")]


def role_of(row: dict, under_subagents: bool) -> str | None:
    """行の役割。役割の欄を持たない行は None（tool_result なら呼び出しの行の役割を継ぐ）。"""
    if under_subagents or row.get("isSidechain") is True or row.get("entrypoint") == "sdk-cli":
        return "other"
    if row.get("isSidechain") is False and row.get("entrypoint") == "cli":
        return "conductor"
    if "isSidechain" in row or "entrypoint" in row:
        return "unattributed"
    return None


def _phrases(rules: list[dict]) -> list[str]:
    """規則の表の文（重ねずに表の順）。"""
    return list(dict.fromkeys(r["phrase"] for r in rules if r["kind"] == "phrase"))


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


def result_json(content, last: bool = False) -> dict | None:
    """tool_result の本文に載った ndf ci watch の結果 JSON（`is_ci_result` に当たる JSON の object）。無ければ None。

    正常終了の結果は `status: ok` で `cause` を持たない。結果を取得できたかどうかはこの戻り値で、停止理由は
    `_cause_kind` で別々に判定する。形の合わない JSON（GitHub の JSON など）は飛ばして後ろを探し続ける。
    `last` なら最後の結果を返す（完了通知の本文に複数の監視の結果が載れば、保存済み出力と同じく最後の停止理由を使う）。
    """
    if isinstance(content, list):
        content = "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    text = str(content or "")
    decoder = json.JSONDecoder()
    start = text.find("{")
    found: dict | None = None
    while start >= 0:
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            obj, end = None, start + 1
        if is_ci_result(obj):
            if not last:
                return obj
            found = obj
        # 読めた object の中は探し直さない（進捗の行などの別の JSON を飛ばす）
        start = text.find("{", end)
    return found


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
    flags = os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW
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
                nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            out = os.open(parts[-1], os.O_RDONLY | O_NONBLOCK | O_NOFOLLOW, dir_fd=fd)
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


def read_saved_output(path: str, allowed: str | None, roots: list[str]) -> tuple[list[dict], str | None]:
    """完了通知の `<output-file>` の保存済み出力を 1 度だけ読み、NDF の結果 JSON の並びと最後の ndf ci watch の結果を返す。

    1 つ目は `result_jsons` に当たる結果を出た順に（会話の履歴へ入れる分）、2 つ目は最後の ndf ci watch の結果 JSON を
    文にしたもの（R09 の停止理由を読む分。無ければ None）。読めなければ `([], None)`。
    開く先は `open_task_output` が決める。結果は標準出力の末尾に出るので、末尾の `OUTPUT_READ_LIMIT` バイトだけを
    読み、後ろの行から探す。途中から始まる先頭の行は NDF の結果の並びに入れない。ndf ci watch の結果は、末尾の読み取りが
    行の途中から始まり、それより後ろの行に結果が無いときだけ、その行の先頭を後ろ向きに探す。行が上限を超えれば、
    先頭の `OUTPUT_HEAD_LIMIT` バイトと末尾から `_long_line_result` が集計に要る項目だけを取る。
    """
    fd = open_task_output(path, allowed, roots)
    if fd is None:
        return [], None
    found: dict | None = None
    try:
        size = os.fstat(fd).st_size
        start = max(0, size - OUTPUT_READ_LIMIT)
        buf = os.pread(fd, size - start, start)
        # 読み取りの先頭が行の途中か（直前の 1 バイトが改行でなければ途中）
        partial = start > 0 and os.pread(fd, 1, start - 1) != b"\n"
        first: bytes | None = None
        if partial:
            first, _, buf = buf.partition(b"\n")
        text = buf.decode("utf-8", errors="replace")
        results = result_jsons(text)
        for line in reversed(text.split("\n")):
            found = result_json(line)
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
        return [], None
    finally:
        os.close(fd)
    return results, (json.dumps(found, ensure_ascii=False) if found is not None else None)


def read_output(path: str, allowed: str | None, roots: list[str]) -> str | None:
    """保存済み出力の最後の ndf ci watch の結果 JSON を文にして返す（`read_saved_output` の 2 つ目）。無ければ None。"""
    return read_saved_output(path, allowed, roots)[1]


def _tool_rows(rules: list[dict], name: str, inp: dict) -> list[tuple[int, dict]]:
    """Tool の呼び出しに当たる規則の行。同じ形では `source_prefix` の合う行が、条件の無い行より先に当たる。"""
    src = str(((inp.get("metadata") or {}).get("source")) or "")
    out = []
    for form in FORMS:
        rows = [(i, r) for i, r in enumerate(rules) if r["kind"] == "tool" and r["tool"] == name and r["form"] == form]
        hit = [(i, r) for i, r in rows if r.get("source_prefix") and src.startswith(r["source_prefix"])]
        out += hit or [(i, r) for i, r in rows if not r.get("source_prefix")]
    return out


def build_matcher(rules: list[dict]) -> dict:
    """規則から照合用の値（行の番号つきの Bash の正規表現・Tool の名前・文・停止理由）を組む。ワーカーごとに 1 度だけ組む。"""
    return {
        "rules": rules,
        "compiled": [(i, r) for i, r in enumerate(rules) if r["kind"] == "bash"],
        "tool_names": {r["tool"] for r in rules if r["kind"] == "tool"},
        "before_tools": {r["tool"] for r in rules if r["kind"] == "tool" and r["form"] == "before"},
        "phrase_rows": [(i, r) for i, r in enumerate(rules) if r["kind"] == "phrase"],
        "cause_rows": [(i, r) for i, r in enumerate(rules) if r["kind"] == "cause"],
        "before_phrases": _phrases([r for r in rules if r["form"] == "before"]),
    }


# ワーカーのプロセスが持つ照合用の値。`init_worker` が 1 度だけ置き、タスクにはファイルと期間だけを渡す
_MATCHER: dict = build_matcher([])


def init_worker(rules: list[dict]) -> None:
    global _MATCHER
    _MATCHER = build_matcher(rules)


class _Scan:
    """1 つの会話の記録を読んだ当たり。当たりの鍵は tool_use の id を含み、ファイルをまたいで 1 度だけ数える。

    停止理由（cause）の規則は Bash の結果を、起動したその呼び出しの id で 1 度だけ数える。バックグラウンドで起動した
    呼び出しは、直接の tool_result が起動応答で、結果 JSON は後から届く。起動応答のタスクの識別子を覚え、出力を読む Tool の
    呼び出し（`tasks/<識別子>.output` を読む Bash・Read、識別子を受け取る TaskOutput など）のうち 1 つのタスクだけを読んだ
    ものの結果と、完了通知（結果を載せなければ `<output-file>` の保存済み出力）を、起動した呼び出しの結果として読む。
    複数のタスクの出力をまとめて読んだ結果は、どのタスクの結果かを決められないので数えない。
    """

    def __init__(self, matcher: dict, under_subagents: bool, task_roots: list[str]) -> None:
        self.rules = matcher["rules"]
        self.sub = under_subagents
        self.task_roots = task_roots
        self.compiled = matcher["compiled"]
        self.tool_names = matcher["tool_names"]
        # 前の形の tool の規則を持つ Tool の名前（今の出力の Tool の鍵を残すかを決める）
        self.before_tools = matcher["before_tools"]
        self.phrase_rows = matcher["phrase_rows"]
        self.cause_rows = matcher["cause_rows"]
        self.before_phrases = matcher["before_phrases"]
        # cause の規則の command に当たった Bash の id → 当たった規則の (行の番号, 行)
        self.awaiting: dict[str, list[tuple[int, dict]]] = {}
        # バックグラウンドのタスクの識別子 → 起動した Bash の id / 出力を読んだ呼び出しの id → 起動した Bash の id
        self.tasks: dict[str, str] = {}
        self.readers: dict[str, str] = {}
        # タスクの識別子 → 起動応答が示した出力のパス（完了通知の `<output-file>` を読んでよい唯一のパス）
        self.outputs: dict[str, str] = {}
        # 結果 JSON を取得できた、起動した Bash の id（cause の有無によらない。正常終了は cause を持たない）
        self.known: set[str] = set()
        # バックグラウンドで起動した ndf のコマンドの Bash の id → 取り込んだ結果の（印, 結果）の出た順の並びと、そのうち
        # 起動したコマンドと組んで履歴へ入れた結果の（印, 同じ印の何番目か）。結果ごとに 1 度だけ入れる
        self.fed: dict[str, list[tuple[str, dict]]] = {}
        self.applied: dict[str, set[tuple[str, int]]] = {}
        # 起動の出力から読んで直前の結果にした並び（同じ並びが直前の結果のままか、`is` で確かめる）
        self.last_of: dict[str, list[dict]] = {}
        # 取り込んだ結果の出た順が分からない（一部ずつ前後のどちらとも決まらない読み方をした）起動の id
        self.unordered: set[str] = set()
        # 完了通知で停止理由を確かめた起動の id（実行中に途中の結果を読んだ `known` とは分ける）
        self.completed: set[str] = set()
        # 完了の結果で停止理由を決めた起動の id（途中の読み取りの当たりを置き換え済み。後の読み直しでは変えない）
        self.final: set[str] = set()
        # cause の規則の名前 → 当たった起動の id
        self.caused: dict[str, set[str]] = {}
        self.conv = _Conversation()
        self.roles: dict[str, str] = {}
        # tool_use の id → その呼び出しの ndf のコマンドの（引数, plan_call, 標準出力が会話に出うるか）の並び
        self.calls: dict[str, list[tuple[list[str], dict | None, bool]]] = {}
        # 規則の番号で数える当たり: 鍵 → (形, 番号, 数えるか, 役割, follows_next, 根拠)
        self.hits: dict[str, tuple] = {}
        # 前の形の今の出力（規則の名前・Tool・文で数える）
        self.bash_names: dict[str, list[str]] = {}
        self.writes: set[str] = set()
        self.tools: dict[str, str] = {}
        self.phrase_ids: dict[str, set[str]] = {p: set() for p in self.before_phrases}

    def _hit(self, key: str, row: dict, role: str, follows: bool, rule: str | None = None, basis: str | None = None) -> None:
        self.hits[key] = (row["form"], rule or row["rule"], row["measured"], role, follows, basis)

    def tool_use(self, row: dict, c: dict, counted: bool) -> None:
        tid = str(c.get("id"))
        role = role_of(row, self.sub) or "unattributed"
        self.roles[tid] = role
        inp = c.get("input") or {}
        name = c.get("name")
        if self.tasks and isinstance(inp, dict):
            read = read_tasks(name, inp)
            # 1 つのタスクだけを読んだ呼び出しを対応づける。複数を読めば、どの出力の結果かを決められない
            if len(read) == 1:
                launch = self.tasks.get(next(iter(read)))
                if launch:
                    self.readers[tid] = launch
        if name == "Bash":
            cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else None
            cmd = str(inp.get("command", ""))
            self._bash(tid, cmd, role, counted, cwd)
            want = [(i, r) for i, r in self.cause_rows if r["compiled"].search(cmd)] if counted else []
            if want:
                self.awaiting[tid] = want
        elif counted and name in self.tool_names:
            for i, r in _tool_rows(self.rules, name, inp):
                self._hit(f"{tid}|{i}", r, role, False)
            if name in self.before_tools:
                self.tools[tid] = tool_key(name, inp)
        elif counted and name == "Write":
            fp = str(inp.get("file_path", ""))
            if fp.endswith(".json") and '"steps"' in str(inp.get("content", "")):
                self.writes.add(tid)

    def _bash(self, tid: str, cmd: str, role: str, counted: bool, cwd: str | None) -> None:
        # コマンドごとの作業場所（同じ呼び出しの `cd <先>` を追う）。相対のパスのプランを絶対のパスの鍵にするのに使う。
        # サブシェル `( … )` の中の `cd` は、サブシェルを出たら親の作業場所へ戻す
        placed: list[tuple[list[str], str | None, bool]] = []
        scopes: list[str | None] = []
        for e in parse_bash(cmd):
            if e["kind"] == "(":
                scopes.append(cwd)
            elif e["kind"] == ")":
                cwd = scopes.pop() if scopes else cwd
            else:
                g = e["args"]
                if g[0] == "cd" and len(g) == 2:
                    cwd = _abs(g[1], cwd)
                # `env -C <先>` はそのコマンドだけの作業場所にする（後のコマンドの作業場所は変えない）
                placed.append((g, command_cwd(g, cwd), e["shown"]))
        segs = [g for g, _, _ in placed]
        # 結果 JSON を出しうる ndf のコマンドを並びの順に。結果は tool_result で出た順にこれと組む
        commands = [(args, plan_call(g, at), shown) for g, at, shown in placed if (args := _ndf_args(g)) is not None]
        self.calls[tid] = commands
        suggested = [c for res in self.conv.last for c in _commands(res)] if counted else []

        def follows(seg: list[str]) -> bool:
            return any(_follows(c, _ndf_args(seg) or command_words(seg)) for c in suggested)

        if counted:
            # 前の形は呼び出しごとに 1 度数える（入れ替えの前の手の表と同じ数え方）。follows_next は規則に当たったコマンドごとに
            # next と照らし、手で選んだコマンドが 1 つでもあれば従う回にしない。どのコマンドにも当たらない（区切りをまたぐ）
            # 規則は、呼び出しのどれかが next に従うかで決める
            for i, r in self.compiled:
                if r["form"] == "before" and r["compiled"].search(cmd):
                    hit = [g for g in segs if r["compiled"].search(" ".join(g))]
                    self.bash_names.setdefault(tid, []).append(r["name"])
                    self._hit(f"{tid}|{i}", r, role, all(follows(g) for g in hit) if hit else any(follows(g) for g in segs))
        # 後の形は `&&`・`;`・`|`・改行で区切った 1 つずつのコマンドで、規則と next を照らす。区切れないコマンドは全体を 1 つにする。
        # conductor の ndf plan next の --answer の有無は、そのコマンドを今の履歴で分類した直後に反映する（後に続く再開が読む）
        units = [(g, command_text(g), at) for g, at, _ in placed] or ([([], cmd, cwd)] if cmd.strip() else [])
        for j, (seg, text, at) in enumerate(units):
            if counted:
                self._after_segment(f"{tid}|{j}", seg, text, role, bool(seg) and follows(seg), at)
            p = plan_call(seg, at) if role == "conductor" and seg else None
            if p is not None and p["sub"] == "next" and p["ids"]:
                self.conv.answered[self.conv.ids(p)[0]] = p["answer"]

    def _after_segment(self, key: str, seg: list[str], text: str, role: str, follows: bool, cwd: str | None) -> None:
        call = plan_call(seg, cwd)
        # 区切れたコマンドは実行ファイルの位置から照らす。区切れない（字句を読めない）コマンドは全体を探す
        match = (lambda r: r["compiled"].match(text)) if seg else (lambda r: r["compiled"].search(text))
        for i, r in self.compiled:
            if r["form"] != "after":
                continue
            if (r["form"], r["rule"]) == RESUME_RULE:
                # 字句を読めた再開は、正規表現でなく `plan_call` のサブコマンドとオプションで決める（`--set 'a; b'` の
                # 値の中の `;`・`--from` で判定を変えない）。字句を読めないコマンドだけ全体を正規表現で探す
                if not (_is_resume(call) if seg else match(r)):
                    continue
                resume = call if _is_resume(call) else None
                for k, (rule, basis) in enumerate(self.conv.targets(resume)):
                    self._hit(f"{key}|{i}|{k}", r, role, follows, rule, basis)
            elif match(r):
                self._hit(f"{key}|{i}", r, role, follows)

    def _feed(self, launch: str | None, results: list[dict]) -> None:
        """結果 JSON を、それを出した呼び出し `launch` の ndf のコマンドと組んで会話の履歴へ入れ、next の照合に使う結果にする。

        バックグラウンドで起動した呼び出しの結果は、出力を読んだ呼び出しの結果か完了通知で 1 度だけ入れる。
        """
        if launch is not None and results:
            for call, res in pair_results(self.calls.get(launch) or [], results):
                self.conv.saw_result(call, res)
        if results:
            self.conv.last = results

    def _take(self, launch: str, results: list[dict]) -> None:
        """バックグラウンドで起動した呼び出し `launch` の出力から読んだ結果のうち、まだ取り込んでいない結果だけを入れる。

        出力を一部だけ読んだ回（`tail`・実行中の `TaskOutput`）の後に届いた結果も、起動したコマンドと組んで履歴へ入れる。
        読んだ結果が取り込み済みの結果をすべて含めば、その並びを出た順とする（一部を後ろから読んだ回の順を直す）。
        含まなければ（`tail` の後に `head` で読んだなど）、新しい結果が取り込み済みの結果の前か後かを決められないので、
        出た順が分かるまで（全体を読むまで）その起動の並びを順の不明なものとして持つ。
        起動したコマンドとの組は、取り込んだ結果の全体で `pair_results` が決め、組めた結果を 1 度だけ履歴へ入れる。順が
        不明な間は、同じサブコマンドのコマンドが 1 つだけの結果（順によらず組が決まる）だけを入れる。
        直前の結果（follows_next の照合）は、出た順で取り込み済みの結果より後ろの結果だけで置き換える。取り込み済みの出力の
        読み直しと、取り込み済みの結果より前の行の補完では変えない。順が不明な読み取りでは、新しく読めた結果で置き換える。
        順が不明だった並びの出た順が決まったとき、この起動の部分読みで置いた直前の結果がまだ有効なら、出力の最後の結果に直す。
        """
        if not results:
            return
        old = self.fed.get(launch, [])
        have = collections.Counter(k for k, _ in old)
        seen: collections.Counter = collections.Counter()
        read: list[tuple[str, dict]] = []
        new: list[tuple[str, dict]] = []
        last_old = -1
        for j, res in enumerate(results):
            k = json.dumps(res, sort_keys=True, ensure_ascii=False)
            seen[k] += 1
            read.append((k, res))
            if seen[k] > have[k]:
                new.append((k, res))
            else:
                last_old = j
        covers = not (have - seen)
        settled = covers and launch in self.unordered
        # 新しい結果が無ければ読み直し。順が不明だった並びを全体で読めたときだけ、順を直して組み直す
        if not new and not settled:
            return
        if covers:
            merged = read
            self.unordered.discard(launch)
        else:
            merged = old + new
            self.unordered.add(launch)
        self.fed[launch] = merged
        commands = self.calls.get(launch) or []
        single = collections.Counter(args[1] for args, _, visible in commands if visible and len(args) > 1)
        applied = self.applied.setdefault(launch, set())
        nth: collections.Counter = collections.Counter()
        pairs = pair_results(commands, [res for _, res in merged])
        for (k, _), (call, res) in zip(merged, pairs):
            nth[k] += 1
            if call is None or (k, nth[k]) in applied:
                continue
            if launch in self.unordered and single[res.get("tool")] != 1:
                continue
            applied.add((k, nth[k]))
            self.conv.saw_result(call, res)
        arrived = [res for _, res in read[last_old + 1 :]] if covers else [res for _, res in new]
        # 順が不明だった並びの出た順が決まり、この起動の部分読みで置いた直前の結果がまだ有効なら、出力の最後の結果に直す
        if not arrived and settled and self.conv.last is self.last_of.get(launch):
            arrived = [read[-1][1]]
        if arrived:
            self.conv.last = arrived
            self.last_of[launch] = arrived

    def tool_result(self, row: dict, c: dict, counted: bool) -> None:
        tid = str(c.get("tool_use_id"))
        results = result_jsons(c.get("content"))
        text = json.dumps(c.get("content"), ensure_ascii=False)
        # バックグラウンドの起動応答。cause の規則に当たる Bash と、結果を履歴へ入れる ndf のコマンドの Bash のタスクを覚える
        if (counted and tid in self.awaiting) or self.calls.get(tid):
            tur = row.get("toolUseResult")
            task = tur.get("backgroundTaskId") if isinstance(tur, dict) else None
            m = BACKGROUND_TEXT.search(text)
            task = task or (m.group(1) if m else None)
            if isinstance(task, str) and task:
                self.tasks[task] = tid
                out = BACKGROUND_OUTPUT.search(text)
                if out and out.group(2) == task:
                    self.outputs[task] = out.group(1)
        # 1 つの呼び出しの結果を出た順に読み、それを出したコマンドと組む（最後の結果だけを最後のコマンドに結ばない）。
        # 起動した呼び出しの出力を読んだ結果は、起動した呼び出しのコマンドと組む
        launch = self.readers.get(tid)
        if launch is not None and self.calls.get(launch):
            self._take(launch, results)
        else:
            self._feed(tid, results)
        if not counted:
            return
        role = role_of(row, self.sub) or self.roles.get(tid, "unattributed")
        for i, r in self.phrase_rows:
            if r["phrase"] in text:
                self._hit(f"{tid}|{i}", r, role, False)
        if tid in self.awaiting:
            self._settle(tid, c.get("content"), role)
        elif launch in self.awaiting:
            self._settle(launch, c.get("content"), role_of(row, self.sub) or self.roles.get(launch, "unattributed"))
        for p in self.before_phrases:
            if p in text:
                self.phrase_ids[p].add(tid)

    def _settle(self, launch: str, content, role: str, last: bool = False) -> bool:
        """起動した呼び出し `launch` の結果を読む。結果 JSON を取得できたら覚え、停止理由の合う規則に 1 度だけ数える。

        `last` なら本文の最後の結果で停止理由を決める（完了通知）。完了の結果を取得できたら、実行中に読んだ途中の結果で
        数えた当たりをその結果で置き換え、以後の読み直しでは変えない。結果 JSON を取得できたかを返す。
        """
        if launch in self.final:
            return True
        obj = result_json(content, last)
        if obj is None:
            return False
        self.known.add(launch)
        if last:
            self.final.add(launch)
            for i, r in self.awaiting[launch]:
                self.hits.pop(f"{launch}|{i}", None)
                self.caused.get(r["name"], set()).discard(launch)
        kind = _cause_kind(obj)
        if not kind:
            return True
        for i, r in self.awaiting[launch]:
            if kind == r["cause"]:
                self.caused.setdefault(r["name"], set()).add(launch)
                self._hit(f"{launch}|{i}", r, role, False)
        return True

    def notification(self, row: dict, counted: bool) -> None:
        """完了通知の行。結果を載せなければ、起動応答が示した `<output-file>` の保存済み出力を読む。

        ndf のコマンドの結果は期間によらず会話の履歴へ入れ（期間の前の結果も期間の内の再開の分類に要る）、cause の規則は
        期間の内の通知だけを数える。
        """
        for text in _strings(row):
            m = NOTIFICATION_TASK.search(text)
            if not m or m.group(1) not in self.tasks:
                continue
            task = m.group(1)
            launch = self.tasks[task]
            rest = text[m.end() :]
            out = NOTIFICATION_OUTPUT.search(rest)
            # 保存済み出力は 1 つの通知で 1 度だけ読み、履歴へ入れる結果と R09 の停止理由の両方に使う
            saved: tuple[list[dict], str | None] | None = None

            def load() -> tuple[list[dict], str | None]:
                nonlocal saved
                if saved is None:
                    saved = read_saved_output(out.group(1), self.outputs.get(task), self.task_roots) if out else ([], None)
                return saved

            if self.calls.get(launch):
                # 実ログの完了通知は終了コードと出力のパスだけを載せる。結果は保存済み出力から読む
                self._take(launch, result_jsons(rest) or load()[0])
            # 実行中に途中の結果を読んで `known` になった起動も、完了時の出力で最後の停止理由を確かめる
            if not counted or launch not in self.awaiting or launch in self.completed:
                continue
            self.completed.add(launch)
            role = role_of(row, self.sub) or self.roles.get(launch, "unattributed")
            if not self._settle(launch, rest, role, last=True) and out and load()[1] is not None:
                self._settle(launch, load()[1], role, last=True)

    def unread(self) -> dict[str, list[str]]:
        """バックグラウンドで起動し、結果 JSON を 1 度も取得できなかった回（出力が消えた・読まれなかった）。"""
        out: dict[str, set[str]] = {}
        for launch in set(self.tasks.values()) - self.known:
            for _, r in self.awaiting.get(launch, ()):
                out.setdefault(r["name"], set()).add(launch)
        return {k: sorted(v) for k, v in out.items()}


def scan_transcript(args: tuple[str, datetime.datetime, datetime.datetime, list[str]]) -> dict:
    """会話の記録 1 本を読む。照合用の値はワーカーの初期化で置いた `_MATCHER` を使う。"""
    path, since, until, task_roots = args
    scan = _Scan(_MATCHER, f"{os.sep}subagents{os.sep}" in path, task_roots)
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        f = None
    if f is not None:
        with f:
            for line in f:
                calls = '"tool_use"' in line or '"tool_result"' in line
                if not calls and not (scan.tasks and "<task-notification>" in line):
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                # 期間の終了以降の行は、コマンドと結果の解析に進まない（期間の内の判定に要るのは開始前までの結果だけ）
                at = parse_at(row.get("timestamp"))
                if at is not None and at >= until:
                    continue
                counted = at is not None and since <= at
                if not calls:
                    scan.notification(row, counted)
                    continue
                content = (row.get("message") or {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use":
                        scan.tool_use(row, c, counted)
                    elif c.get("type") == "tool_result":
                        scan.tool_result(row, c, counted)
    return {
        "hits": scan.hits,
        "bash": scan.bash_names,
        "writes": sorted(scan.writes),
        "tools": scan.tools,
        "phrases": {k: sorted(v) for k, v in scan.phrase_ids.items() if v},
        "causes": {k: sorted(v) for k, v in scan.caused.items()},
        "unread": scan.unread(),
    }


def tool_key(name: str, inp: dict) -> str:
    """AskUserQuestion は問いの `metadata.source` の接頭辞（承認ゲートの問いか、ほかの問い）で分ける。"""
    if name != "AskUserQuestion":
        return name
    src = str(((inp.get("metadata") or {}).get("source")) or "")
    return f"AskUserQuestion（{src.split(':')[0] + ':' + src.split(':')[1] if src.count(':') else src or '印なし'}）"


def _empty_entry() -> dict:
    return {
        "value": 0,
        "all_roles": 0,
        "roles": collections.Counter(),
        "follows_next": 0,
        "basis": collections.Counter(),
        "unresolved": 0,
        "reference": {"value": 0, "all_roles": 0},
    }


def by_form(hits: dict[str, tuple]) -> dict:
    """当たりを形と規則の番号ごとに足す。`value` は conductor の回のうち follows_next でないもの、`all_roles` は役割で絞らない回。

    `measured: false` の行の当たりは `reference` へ、寄せ先を決められなかった再開は `unresolved` へ入れ、どちらも
    `value`・`all_roles` に足さない。
    """
    forms: dict[str, dict] = {f: collections.defaultdict(_empty_entry) for f in FORMS}
    for form, rule, measured, role, follows, basis in hits.values():
        e = forms[form][rule]
        if basis is not None:
            e["basis"][basis] += 1
        if basis == "cause_unknown":
            e["unresolved"] += role == "conductor" and not follows
            continue
        target = e if measured else e["reference"]
        target["all_roles"] += 1
        target["value"] += role == "conductor" and not follows
        if measured:
            e["roles"][role] += 1
            e["follows_next"] += role == "conductor" and follows
    return {
        f: {r: {**e, "roles": dict(e["roles"]), "basis": dict(e["basis"])} for r, e in sorted(rows.items())} for f, rows in forms.items()
    }


def count_transcripts(
    roots: list[str],
    since: datetime.datetime,
    until: datetime.datetime,
    jobs: int,
    rules: list[dict],
    task_roots: list[str] | None = None,
) -> dict:
    """会話の記録を数える。完了通知の保存済み出力は `task_roots`（既定 `default_task_roots`）の下のものだけを読む。

    照合用の値はワーカーの初期化で 1 度だけ組み、タスクにはファイルと期間だけを渡す。
    """
    task_roots = default_task_roots() if task_roots is None else list(task_roots)
    files = [str(p) for r in roots for p in pathlib.Path(r).rglob("*.jsonl")]
    files = [f for f in files if os.path.getmtime(f) >= since.timestamp()]
    hits: dict[str, tuple] = {}
    bash: dict[str, list[str]] = {}
    writes: set[str] = set()
    tools: dict[str, str] = {}
    phrases: dict[str, set[str]] = collections.defaultdict(set)
    caused: dict[str, set[str]] = collections.defaultdict(set)
    unread: dict[str, set[str]] = collections.defaultdict(set)
    with ProcessPoolExecutor(max_workers=max(1, jobs), initializer=init_worker, initargs=(rules,)) as ex:
        for res in ex.map(scan_transcript, [(f, since, until, task_roots) for f in files], chunksize=8):
            hits.update(res["hits"])
            bash.update(res["bash"])
            writes.update(res["writes"])
            tools.update(res["tools"])
            for k, v in res["phrases"].items():
                phrases[k].update(v)
            for k, v in res["causes"].items():
                caused[k].update(v)
            for k, v in res["unread"].items():
                unread[k].update(v)
    by_name = collections.Counter(name for names in bash.values() for name in names)
    before_phrases = _phrases([r for r in rules if r["form"] == "before"])
    out = {
        "files": len(files),
        "bash_by_rule": dict(by_name.most_common()),
        "hand_written_plans": len(writes),
        "tools": dict(collections.Counter(tools.values()).most_common()),
        "results_with_phrase": {k: len(phrases[k]) for k in before_phrases if phrases.get(k)},
        "by_form": by_form(hits),
    }
    # cause の規則を持つ表（後の形）だけが出す
    if any(r["kind"] == "cause" for r in rules):
        names = [r["name"] for r in rules if r["kind"] == "cause"]
        out["results_with_cause"] = {k: len(caused[k]) for k in dict.fromkeys(names) if caused.get(k)}
        # バックグラウンドの回のうち結果 JSON を読めなかった回。上の件数に入っていないことを集計で判別できるようにする
        out["results_without_cause"] = {k: len(unread[k]) for k in dict.fromkeys(names) if unread.get(k)}
    return out


# ---- 物差し ----


def _counter_sum(counter: dict, src: dict) -> int:
    if src.get("keys"):
        return sum(counter.get(k, 0) for k in src["keys"])
    return sum(v for k, v in counter.items() if k.startswith(src["prefix"]))


def _rule_value(rule: str, form: str, src: dict, counts: dict) -> tuple[dict, str | None]:
    """規則の番号の出所 1 つの回数と、数えられないときの理由。"""
    kind = src["source"]
    if kind == "none":
        return {}, src["reason"]
    if kind == "transcripts":
        if counts.get("transcripts") is None:
            return {}, "会話の記録を読んでいない（--skip-transcripts）"
        t = counts["transcripts"][form].get(rule, {})
        return {"value": t.get("value", 0), "all_roles": t.get("all_roles", 0)}, None
    if kind == "records":
        c = counts["records"]["by_rule"].get(rule, {})
        return {"value": c.get("conductor", 0), "all_roles": sum(c.values())}, None
    n = _counter_sum(counts[kind].get(src["counter"], {}), src)
    return {"value": n, "all_roles": n}, None


def _references(rule: str, form: str, kind: str, counts: dict) -> dict:
    """G に足さない参考の当たり（ほかの出所と、measured: false の行）。"""
    ref = {}
    tr = (counts.get("transcripts") or {}).get(form, {}).get(rule)
    if tr is not None:
        if kind != "transcripts" and tr["all_roles"]:
            ref["transcripts"] = {"value": tr["value"], "all_roles": tr["all_roles"]}
        if tr["reference"]["all_roles"]:
            ref["unmeasured_rows"] = tr["reference"]
    rec = counts["records"]["by_rule"].get(rule)
    if form == "after" and rec and kind != "records":
        ref["records"] = rec
    return ref


def summarize(counts: dict, measures: dict) -> dict:
    """形ごとに、規則の番号の出所 1 つの回数だけから G1〜G3 を足す。

    出所が `none` の規則は 0 でなく `unmeasured` に理由とともに挙げ、寄せ先か判断の種類を決められなかった回は `unresolved`
    に挙げる。どちらもその物差しの値に `partial: true` を付ける。
    """
    out = {}
    for form in FORMS:
        by_rule: dict[str, dict] = {}
        unmeasured: dict[str, str] = {}
        unresolved: dict[str, int] = {}
        for rule, m in measures.items():
            src = m[form]
            value, reason = _rule_value(rule, form, src, counts)
            if reason is not None:
                unmeasured[rule] = reason
            entry = {"source": src["source"], **value}
            ref = _references(rule, form, src["source"], counts)
            if ref:
                entry["reference"] = ref
            by_rule[rule] = entry
            tr = (counts.get("transcripts") or {}).get(form, {}).get(rule)
            if src["source"] == "transcripts" and tr is not None and tr["unresolved"]:
                unresolved[rule] = tr["unresolved"]
        if rule_src := measures.get("R20", {}).get(form):
            if rule_src["source"] == "progress":
                n = _counter_sum(counts["progress"].get("judge_unclassified_by_decision", {}), {"prefix": JUDGE_PREFIX})
                if n:
                    unresolved["R20"] = n
        partial = set(unmeasured) | set(unresolved) | {r for u in unresolved for r in UNRESOLVED_ALSO.get(u, ())}
        g = {}
        for name in MEASURE_VALUES[:-1]:
            rules = [r for r, m in measures.items() if m["measure"] == name]
            g[name] = {
                "value": sum(by_rule[r].get("value", 0) for r in rules),
                "all_roles": sum(by_rule[r].get("all_roles", 0) for r in rules),
                "partial": any(r in partial for r in rules),
            }
        out[form] = {"by_rule": by_rule, "measures": g, "unmeasured": unmeasured, "unresolved": unresolved}
    out["after"]["rejected"] = counts["records"]["rejected"]
    return out


def _moment(text: str, flag: str) -> datetime.datetime:
    t = parse_at(text)
    if t is None:
        _fail(f"{flag} は時差つきの時刻で渡す（例 2026-11-18T00:00:00+09:00）: {text!r}")
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", required=True, help="数え始める時刻（含む。時差つきの ISO 8601）")
    ap.add_argument("--until", required=True, help="数え終える時刻（含まない。時差つきの ISO 8601）")
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
    since = _moment(a.since, "--since")
    until = _moment(a.until, "--until")
    rules = load_rules(RULES_FILE)
    measures = load_measures(MEASURES_FILE)
    check_measures(rules, measures)
    if not SCHEMA.is_file():
        print(f"選択の記録のスキーマが無い: {SCHEMA}", file=sys.stderr)
        return 3
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    ndf = pathlib.Path(a.ndf)
    files = progress_files(ndf, pathlib.Path(a.state))
    out = {
        "since": a.since,
        "until": a.until,
        "progress": None,
        "ledger": count_ledgers(ndf, since, until),
    }
    out["progress"], choices = count_plan_records(files, since, until, schema)
    forms_in = None
    if not a.skip_transcripts:
        out["transcripts"] = count_transcripts(a.transcripts or default_transcripts(), since, until, a.jobs, rules, a.task_outputs)
        forms_in = out["transcripts"]["by_form"]
    out["forms"] = summarize({"transcripts": forms_in, "progress": out["progress"], "ledger": out["ledger"], "records": choices}, measures)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
