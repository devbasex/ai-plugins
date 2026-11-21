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
  `follows_next` で数える。後の形の Bash は `&&`・`;`・`|` で区切った 1 つずつのコマンドで照らす
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
# 値を取るオプション（ndf plan next / queue）
PLAN_VALUE_FLAGS = ("--from", "--answer", "--state-dir", "--set", "--max", "--queue-dir")


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


def segments(cmd: str) -> list[list[str]]:
    """Bash のコマンドを `&&`・`;`・`|` などで区切った、引数の並びの組。読めなければ空。"""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return []
    out: list[list[str]] = [[]]
    skip = False
    for t in tokens:
        if skip:
            skip = False
            continue
        if t and all(c in "();<>|&" for c in t):
            if "<" in t or ">" in t:
                # リダイレクトは向きの印と行き先を落とす（2>&1 の 2 も）
                if out[-1] and out[-1][-1].isdigit():
                    out[-1].pop()
                skip = not t.endswith("&") or t == ">&"
                continue
            out.append([])
            continue
        out[-1].append(t)
    return [s for s in out if s]


def _ndf_args(seg: list[str]) -> list[str] | None:
    """`ndf` を打つ引数の並び（先頭を `ndf` にそろえる）。ndf でなければ None。"""
    if seg and os.path.basename(seg[0]) == "ndf":
        return ["ndf", *seg[1:]]
    return None


def plan_call(seg: list[str]) -> dict | None:
    """`ndf plan next|queue|wait …` の引数。ほかのコマンドは None。"""
    args = _ndf_args(seg)
    if args is None or len(args) < 3 or args[1] != "plan" or args[2] not in ("next", "queue", "wait"):
        return None
    rest = args[3:]
    plans: list[str] = []
    starts: list[str] = []
    flags: set[str] = set()
    i = 0
    while i < len(rest):
        t = rest[i]
        name, eq, value = t.partition("=")
        if t.startswith("--") and eq:
            flags.add(name)
            if name == "--from":
                starts.append(value)
            i += 1
        elif t in PLAN_VALUE_FLAGS:
            flags.add(t)
            if t == "--from" and i + 1 < len(rest):
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
    return {"sub": args[2], "plans": plans, "from": starts, "answer": "--answer" in flags, "resume": "--resume" in flags}


def result_json(content) -> dict | None:
    """tool_result の中身から、結果 JSON（`status` を持つ 1 行のオブジェクト）の最後のもの。"""
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        texts += [str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("type") == "text"]
    found = None
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
                found = obj
    return found


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
    またいで新しいほう）。プランはパスか名前で指すので、結果からパス → 名前を覚え、両方の鍵で引いて新しいほうを使う。
    """

    def __init__(self) -> None:
        self.last: dict | None = None  # 直前の結果 JSON（どのコマンドでも）
        self.seq = 0
        # 対象の鍵（`path:<パス>` か `name:<名前>`）→ (順番, 対象の行, 結果のトップレベル, 行の名前。単独の結果は None)
        self.latest: dict[str, tuple[int, dict, dict, str | None]] = {}
        self.name_of: dict[str, str] = {}  # ndf plan に渡したパス → プランの名前
        self.queues: dict[frozenset, list[str]] = {}  # queue / wait に渡したパスの組 → 直近の結果のプランの名前
        self.answered: dict[str, bool] = {}  # conductor が直前に打った ndf plan next <パス> が --answer を持つか

    def _put(self, key: str, row: dict, top: dict, name: str | None) -> None:
        self.latest[key] = (self.seq, row, top, name)

    def saw_result(self, call: dict | None, res: dict) -> None:
        self.last = res
        if call is None or not call["plans"]:
            return
        self.seq += 1
        items = _plan_items(res)
        if call["sub"] == "next":
            path = call["plans"][0]
            if items:
                self.name_of[path] = items[0]["name"]
                self._put(f"name:{items[0]['name']}", res, res, None)
            self._put(f"path:{path}", res, res, None)
            return
        # queue / wait の items は渡したプランの並び。数が合うときだけパスと名前を結ぶ
        if len(items) == len(call["plans"]):
            for path, it in zip(call["plans"], items):
                self.name_of[path] = it["name"]
        self.queues[frozenset(call["plans"])] = [it["name"] for it in items]
        for it in items:
            self._put(f"name:{it['name']}", it, res, it["name"])

    def _lookup(self, path: str | None = None, name: str | None = None) -> tuple[dict, dict, str | None] | None:
        keys = []
        if path is not None:
            keys.append(f"path:{path}")
            if path in self.name_of:
                keys.append(f"name:{self.name_of[path]}")
        if name is not None:
            keys.append(f"name:{name}")
        found = [self.latest[k] for k in keys if k in self.latest]
        if not found:
            return None
        _, row, top, row_name = max(found, key=lambda e: e[0])
        return row, top, row_name

    def _answered(self, path: str | None = None, name: str | None = None) -> bool:
        if path is not None:
            return bool(self.answered.get(path))
        return any(v and self.name_of.get(p) == name for p, v in self.answered.items())

    def _route(self, found: tuple[dict, dict, str | None] | None) -> tuple[str, str]:
        if found is None:
            return "R03", "no_prior_result"
        row, top, name = found
        return _route_of(row, top, name)

    def targets(self, call: dict | None) -> list[tuple[str, str]]:
        """再開のコマンドの対象ごとの（寄せ先, 根拠）。`--resume` の対象は、そのコマンドに渡したプランの直近の結果で決める。"""
        if call is None:
            return [("R03", "no_prior_result")]
        if call["sub"] == "next":
            path = call["plans"][0] if call["plans"] else ""
            if self._answered(path=path):
                return [("R13", "answer")]
            return [self._route(self._lookup(path=path))]
        names = [s.partition("=")[0] for s in call["from"]]
        out = []
        for name in names:
            out.append(("R13", "answer") if self._answered(name=name) else self._route(self._lookup(name=name)))
        if call["resume"]:
            own = self.queues.get(frozenset(call["plans"]))
            if own is None:
                own = [self.name_of[p] for p in call["plans"] if p in self.name_of]
            rest = [n for n in dict.fromkeys(own) if n not in names]
            known = [(n, self._lookup(name=n)) for n in rest]
            for name, found in known:
                if found is None or found[0].get("status") == "ok":
                    continue  # --resume は前の回で完了したプランを流さない
                out.append(("R13", "answer") if self._answered(name=name) else self._route(found))
            if not own:
                out.append(("R03", "no_prior_result"))
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
        self.calls: dict[str, dict | None] = {}
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
            self._bash(tid, str(inp.get("command", "")), role, counted)
        elif counted and name in self.tool_names:
            for i, r in _tool_rows(self.rules, name, inp):
                self._hit(f"{tid}|{i}", r, role, False)
            if any(r["form"] == "before" and r["tool"] == name for r in self.rules if r["kind"] == "tool"):
                self.tools[tid] = tool_key(name, inp)
        elif counted and name == "Write":
            fp = str(inp.get("file_path", ""))
            if fp.endswith(".json") and '"steps"' in str(inp.get("content", "")):
                self.writes.add(tid)

    def _bash(self, tid: str, cmd: str, role: str, counted: bool) -> None:
        segs = segments(cmd)
        calls = [p for p in (plan_call(s) for s in segs) if p is not None]
        self.calls[tid] = calls[-1] if calls else None
        if counted:
            suggested = _commands(self.conv.last)

            def follows(seg: list[str]) -> bool:
                return any(_follows(c, _ndf_args(seg) or seg) for c in suggested)

            # 前の形は呼び出しごとに照らす（入れ替えの前の手の表と同じ数え方）
            whole = any(follows(g) for g in segs)
            for i, r in self.compiled:
                if r["form"] == "before" and r["compiled"].search(cmd):
                    self.bash_names.setdefault(tid, []).append(r["name"])
                    self._hit(f"{tid}|{i}", r, role, whole)
            # 後の形は `&&`・`;`・`|` で区切った 1 つずつのコマンドで、規則と next を照らす。区切れないコマンドは全体を 1 つにする
            units = [(g, " ".join(g)) for g in segs] or ([([], cmd)] if cmd.strip() else [])
            for j, (seg, text) in enumerate(units):
                self._after_segment(f"{tid}|{j}", seg, text, role, bool(seg) and follows(seg))
        if role == "conductor":
            for p in calls:
                if p["sub"] == "next" and p["plans"]:
                    self.conv.answered[p["plans"][0]] = p["answer"]

    def _after_segment(self, key: str, seg: list[str], text: str, role: str, follows: bool) -> None:
        call = plan_call(seg)
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
        res = result_json(c.get("content"))
        if res is not None:
            self.conv.saw_result(self.calls.get(tid), res)
        if not counted:
            return
        text = json.dumps(c.get("content"), ensure_ascii=False)
        role = role_of(row, self.sub) or self.roles.get(tid, "unattributed")
        for i, r in self.phrase_rows:
            if r["phrase"] in text:
                self._hit(f"{tid}|{i}", r, role, False)
        for i, r in self.cause_rows:
            if res is not None and _cause_kind(res) == r["cause"] and r.get("result_tool") in (None, res.get("tool")):
                self._hit(f"{tid}|{i}", r, role, False)
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
