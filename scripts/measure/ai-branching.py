#!/usr/bin/env python3
"""#1142（#3152 から統合）の洗い出し: 開発のワークフローで AI が分岐と例外の行き先を決めた回を、実際の記録から数える。

記録を読み、回数を規則の番号（R01〜R27）ごとに数えて JSON で出す。数は「AI が出力を読んで次の手を決めた跡」の件数で、
すべてが誤りではない。入れ替えの前の形（10 系のコマンドと記録）と後の形（11 系のコマンドと選択の記録）を同じ規則の番号へ
寄せ、同じコマンドで打つ（#3412 の決定 8・9）。

    python3 scripts/measure/ai-branching.py --since 2026-11-18T00:00:00+09:00 --until 2026-12-18T00:00:00+09:00 \\
        [--ndf .ndf] [--state ~/.local/state/ndf] [--transcripts <根>]... [--jobs 6] [--skip-transcripts]

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
  `follows_next` で数える。後の形の Bash は `&&`・`;`・`|`・改行で区切った 1 つずつのコマンドで照らす
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
# 種類ごとに要る項目。bash は正規表現、tool は Tool の名前、phrase は tool_result に現れたら数える文、cause は tool_result の
# 結果 JSON の停止理由の種類（`cause.kind`。`result_tool` があれば結果の `tool` もそれに限る）
KINDS = {"bash": "pattern", "tool": "tool", "phrase": "phrase", "cause": "cause"}
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
# コマンドを区切る記号。改行もコマンドを終える（引用の内の改行はトークンに残る）
SEPARATORS = "();<>|&\n"


def _fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(2)


# ---- 規則の表と分類の表 ----


def _check_row(where: str, row) -> None:
    if not isinstance(row, dict):
        _fail(f"{where}: 行はオブジェクトで書く")
    if not RULE_ID.match(str(row.get("rule", ""))):
        _fail(f"{where}: rule が無いか R と 2 桁以上の数字でない（{row.get('rule')!r}）")
    if row.get("form") not in FORMS:
        _fail(f"{where}: form が無いか {'・'.join(FORMS)} でない（{row.get('form')!r}）")
    field = KINDS.get(row.get("kind"))
    if field is None:
        _fail(f"{where}: kind が無いか {'・'.join(KINDS)} でない（{row.get('kind')!r}）")
    if not isinstance(row.get(field), str) or not row[field]:
        _fail(f"{where}: kind {row['kind']} の行に {field} が無い")
    if not isinstance(row.get("name"), str) or not row["name"]:
        _fail(f"{where}: name が無い")
    if not isinstance(row.get("measured", True), bool):
        _fail(f"{where}: measured は true か false で書く")
    if "source_prefix" in row and (row["kind"] != "tool" or not isinstance(row["source_prefix"], str) or not row["source_prefix"]):
        _fail(f"{where}: source_prefix は kind tool の行に文字列で書く")
    if "result_tool" in row and (row["kind"] != "cause" or not isinstance(row["result_tool"], str) or not row["result_tool"]):
        _fail(f"{where}: result_tool は kind cause の行に文字列で書く")


def load_rules(path: pathlib.Path) -> list[dict]:
    """規則の表を読み、行ごとに `rule`・`form`・`kind` と種類の項目を確かめて返す。

    bash の行は `compiled` を持つ。`measured: false`（省けば true）の行の当たりは参考として出し、G に足さない。tool の行の
    `source_prefix` は問いの `metadata.source` の接頭辞の条件で、同じ形の同じ Tool では条件の合う行が条件の無い行より先に当たる。
    """
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        _fail(f"{path}: 規則の表は配列で書く")
    out = []
    for i, row in enumerate(rows, 1):
        where = f"{path}: {i} 行目"
        _check_row(where, row)
        r = {**row, "measured": row.get("measured", True)}
        if row["kind"] == "bash":
            try:
                r["compiled"] = re.compile(row["pattern"])
            except re.error as e:
                _fail(f"{where}: pattern が正規表現として読めない（{e}）")
        out.append(r)
    return out


def _check_source(where: str, src) -> None:
    if not isinstance(src, dict) or src.get("source") not in SOURCES:
        _fail(f"{where}: source が無いか {'・'.join(SOURCES)} でない")
    if src["source"] in ("progress", "ledger"):
        if not isinstance(src.get("counter"), str) or not (src.get("keys") or src.get("prefix")):
            _fail(f"{where}: {src['source']} の出所に counter と keys か prefix が無い")
    if src["source"] == "none" and not src.get("reason"):
        _fail(f"{where}: none の出所に reason（数えない理由）が無い")


def load_measures(path: pathlib.Path) -> dict[str, dict]:
    """規則の番号ごとの `measure`（G1 / G2 / G3 / none）と、形ごとの出所 `before`・`after` を確かめて返す。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        _fail(f"{path}: 分類の表は規則の番号を鍵にしたオブジェクトで書く")
    for rule, entry in data.items():
        where = f"{path}: {rule}"
        if not RULE_ID.match(rule) or not isinstance(entry, dict):
            _fail(f"{where}: 鍵は R と数字、値はオブジェクトで書く")
        if entry.get("measure") not in MEASURE_VALUES:
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
        if ch in "'\"":
            quote = ch
        elif ch == "#" and (not out or out[-1] in " \t\r" + SEPARATORS):
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
            if not (t.isdigit() and not unit["args"]):
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
                fd = unit["args"].pop() if unit["args"] and unit["args"][-1].isdigit() else None
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


def _ndf_args(seg: list[str]) -> list[str] | None:
    """`ndf` を打つ引数の並び（先頭を `ndf` にそろえる）。ndf でなければ None。"""
    if seg and os.path.basename(seg[0]) == "ndf":
        return ["ndf", *seg[1:]]
    return None


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
            flags.add(name)
            values[name] = value
            if name == "--from":
                starts.append(value)
            i += 1
        elif t in PLAN_VALUE_FLAGS:
            flags.add(t)
            if i + 1 < len(rest):
                values[t] = rest[i + 1]
                if t == "--from":
                    starts.append(rest[i + 1])
            i += 2
        elif t == "--then":
            # --then の後のプランも、結果の items と同じ並びで plans に入れる
            i += 1
            while i < len(rest) and not rest[i].startswith("--"):
                plans.append(rest[i])
                i += 1
        elif t.startswith("--"):
            flags.add(t)
            i += 1
        else:
            plans.append(t)
            i += 1
    # queue の子は --state-dir を渡さずに流すので、queue のプランは保存先を省いた形の鍵にする
    state = values.get("--state-dir") if args[2] != "queue" and len(plans) == 1 else None
    paths = [_abs(p, cwd) for p in plans]
    given = [_abs(state, cwd) if state is not None else None for _ in plans]
    ids = [f"{p}|{g if g is not None else OMITTED}" for p, g in zip(paths, given)]
    queue = None
    if args[2] == "queue" and plans:
        qdir = values.get("--queue-dir")
        queue = _abs(qdir, cwd) if qdir else os.path.splitext(_abs(plans[0], cwd))[0] + QUEUE_SUFFIX
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


def result_jsons(content) -> list[dict]:
    """tool_result の中身から、結果 JSON（`status` を持つ 1 行のオブジェクト）を出た順に。"""
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        texts += [str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("type") == "text"]
    found = []
    for text in texts:
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{") or '"status"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("status"), str):
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
    return [it for it in res.get("items") or [] if isinstance(it, dict) and it.get("kind") == "plan" and isinstance(it.get("name"), str)]


def _commands(res: dict | None) -> list[dict]:
    """結果が提示した打つコマンド: `next` と、承認ゲートの `routes`（結果と items の行の分）の各回答。"""
    if res is None:
        return []
    found = [res.get("next"), *((res.get("routes") or {}).values())]
    for it in res.get("items") or []:
        if isinstance(it, dict) and isinstance(it.get("routes"), dict):
            found += list(it["routes"].values())
    return [c for c in found if isinstance(c, dict) and isinstance(c.get("argv"), list)]


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
        if call["sub"] == "next":
            pid = self.ids(call)[0]
            if items:
                self.name_of[pid] = items[0]["name"]
            self._put(f"id:{pid}", res, res, None)
            return
        # queue / wait の items は渡したプランの並び。数が合うときだけプランの鍵と名前を結ぶ
        if len(items) == len(self.ids(call)):
            for pid, it in zip(self.ids(call), items):
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


def _tool_rows(rules: list[dict], name: str, inp: dict) -> list[tuple[int, dict]]:
    """Tool の呼び出しに当たる規則の行。同じ形では `source_prefix` の合う行が、条件の無い行より先に当たる。"""
    src = str(((inp.get("metadata") or {}).get("source")) or "")
    out = []
    for form in FORMS:
        rows = [(i, r) for i, r in enumerate(rules) if r["kind"] == "tool" and r["tool"] == name and r["form"] == form]
        hit = [(i, r) for i, r in rows if r.get("source_prefix") and src.startswith(r["source_prefix"])]
        out += hit or [(i, r) for i, r in rows if not r.get("source_prefix")]
    return out


class _Scan:
    """1 つの会話の記録を読んだ当たり。当たりの鍵は tool_use の id を含み、ファイルをまたいで 1 度だけ数える。"""

    def __init__(self, rules: list[dict], under_subagents: bool) -> None:
        self.rules = rules
        self.sub = under_subagents
        self.compiled = [(i, r) for i, r in enumerate(rules) if r["kind"] == "bash"]
        self.tool_names = {r["tool"] for r in rules if r["kind"] == "tool"}
        self.phrase_rows = [(i, r) for i, r in enumerate(rules) if r["kind"] == "phrase"]
        self.cause_rows = [(i, r) for i, r in enumerate(rules) if r["kind"] == "cause"]
        self.before_phrases = _phrases([r for r in rules if r["form"] == "before"])
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
        if name == "Bash":
            cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else None
            self._bash(tid, str(inp.get("command", "")), role, counted, cwd)
        elif counted and name in self.tool_names:
            for i, r in _tool_rows(self.rules, name, inp):
                self._hit(f"{tid}|{i}", r, role, False)
            if any(r["form"] == "before" and r["tool"] == name for r in self.rules if r["kind"] == "tool"):
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
                placed.append((g, cwd, e["shown"]))
        segs = [g for g, _, _ in placed]
        # 結果 JSON を出しうる ndf のコマンドを並びの順に。結果は tool_result で出た順にこれと組む
        commands = [(args, plan_call(g, at), shown) for g, at, shown in placed if (args := _ndf_args(g)) is not None]
        self.calls[tid] = commands
        if counted:
            suggested = [c for res in self.conv.last for c in _commands(res)]

            def follows(seg: list[str]) -> bool:
                return any(_follows(c, _ndf_args(seg) or seg) for c in suggested)

            # 前の形は呼び出しごとに照らす（入れ替えの前の手の表と同じ数え方）
            whole = any(follows(g) for g in segs)
            for i, r in self.compiled:
                if r["form"] == "before" and r["compiled"].search(cmd):
                    self.bash_names.setdefault(tid, []).append(r["name"])
                    self._hit(f"{tid}|{i}", r, role, whole)
            # 後の形は `&&`・`;`・`|`・改行で区切った 1 つずつのコマンドで、規則と next を照らす。区切れないコマンドは全体を 1 つにする
            units = [(g, " ".join(g), at) for g, at, _ in placed] or ([([], cmd, cwd)] if cmd.strip() else [])
            for j, (seg, text, at) in enumerate(units):
                self._after_segment(f"{tid}|{j}", seg, text, role, bool(seg) and follows(seg), at)
        if role == "conductor":
            for _, p, _ in commands:
                if p is not None and p["sub"] == "next" and p["ids"]:
                    self.conv.answered[self.conv.ids(p)[0]] = p["answer"]

    def _after_segment(self, key: str, seg: list[str], text: str, role: str, follows: bool, cwd: str | None) -> None:
        call = plan_call(seg, cwd)
        for i, r in self.compiled:
            if r["form"] != "after" or not r["compiled"].search(text):
                continue
            if (r["form"], r["rule"]) == RESUME_RULE:
                resume = call if call is not None and (call["from"] or call["resume"]) else None
                for k, (rule, basis) in enumerate(self.conv.targets(resume)):
                    self._hit(f"{key}|{i}|{k}", r, role, follows, rule, basis)
            else:
                self._hit(f"{key}|{i}", r, role, follows)

    def tool_result(self, row: dict, c: dict, counted: bool) -> None:
        tid = str(c.get("tool_use_id"))
        results = result_jsons(c.get("content"))
        # 1 つの呼び出しの結果を出た順に読み、それを出したコマンドと組む（最後の結果だけを最後のコマンドに結ばない）
        for call, res in pair_results(self.calls.get(tid) or [], results):
            self.conv.saw_result(call, res)
        if results:
            self.conv.last = results
        if not counted:
            return
        text = json.dumps(c.get("content"), ensure_ascii=False)
        role = role_of(row, self.sub) or self.roles.get(tid, "unattributed")
        for i, r in self.phrase_rows:
            if r["phrase"] in text:
                self._hit(f"{tid}|{i}", r, role, False)
        for i, r in self.cause_rows:
            for k, res in enumerate(results):
                if _cause_kind(res) == r["cause"] and r.get("result_tool") in (None, res.get("tool")):
                    self._hit(f"{tid}|{i}|{k}", r, role, False)
        for p in self.before_phrases:
            if p in text:
                self.phrase_ids[p].add(tid)


def scan_transcript(args: tuple[str, datetime.datetime, datetime.datetime, list[dict]]) -> dict:
    path, since, until, rules = args
    scan = _Scan(rules, f"{os.sep}subagents{os.sep}" in path)
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        f = None
    if f is not None:
        with f:
            for line in f:
                if '"tool_use"' not in line and '"tool_result"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                counted = in_period(row.get("timestamp"), since, until)
                for c in (row.get("message") or {}).get("content") or []:
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


def count_transcripts(roots: list[str], since: datetime.datetime, until: datetime.datetime, jobs: int, rules: list[dict]) -> dict:
    files = [str(p) for r in roots for p in pathlib.Path(r).rglob("*.jsonl")]
    files = [f for f in files if os.path.getmtime(f) >= since.timestamp()]
    hits: dict[str, tuple] = {}
    bash: dict[str, list[str]] = {}
    writes: set[str] = set()
    tools: dict[str, str] = {}
    phrases: dict[str, set[str]] = collections.defaultdict(set)
    tasks = [(f, since, until, rules) for f in files]
    if jobs <= 1:
        results = map(scan_transcript, tasks)
    else:
        ex = ProcessPoolExecutor(max_workers=jobs)
        results = ex.map(scan_transcript, tasks, chunksize=8)
    try:
        for res in results:
            hits.update(res["hits"])
            bash.update(res["bash"])
            writes.update(res["writes"])
            tools.update(res["tools"])
            for k, v in res["phrases"].items():
                phrases[k].update(v)
    finally:
        if jobs > 1:
            ex.shutdown()
    by_name = collections.Counter(name for names in bash.values() for name in names)
    before_phrases = _phrases([r for r in rules if r["form"] == "before"])
    return {
        "files": len(files),
        "bash_by_rule": dict(by_name.most_common()),
        "hand_written_plans": len(writes),
        "tools": dict(collections.Counter(tools.values()).most_common()),
        "results_with_phrase": {k: len(phrases[k]) for k in before_phrases if phrases.get(k)},
        "by_form": by_form(hits),
    }


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
        out["transcripts"] = count_transcripts(a.transcripts or default_transcripts(), since, until, a.jobs, rules)
        forms_in = out["transcripts"]["by_form"]
    out["forms"] = summarize({"transcripts": forms_in, "progress": out["progress"], "ledger": out["ledger"], "records": choices}, measures)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
