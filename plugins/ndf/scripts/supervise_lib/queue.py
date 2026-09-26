"""queue と wait: プランを別プロセスの run として並べて流し、終わりか attention まで待つ（#1142 の C1）。

`engine` を import しない。プランは `SELF` の `run` として流す。worktree は `paths.ensure_worktree` で作る
（テストがモジュールの属性を差し替える）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from clock import now_iso  # cmd_wait の引数 clock（時計の差し替え）と名前を分ける
from step_result import result
from supervise_lib import paths
from supervise_lib.paths import SELF, queue_done_path, queue_plans_path, report_result, state_dir_of, wait_cursor_path
from supervise_lib.plan import QUEUE_PRS, pr_number


def attention_lines(prog: Path, offset: int) -> tuple[list[dict], int]:
    """progress.jsonl の offset から後の、書き終わった attention の行と、読んだ所を返す。"""
    if not prog.is_file() or prog.stat().st_size <= offset:
        return [], offset
    with open(prog, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1
    found = []
    for raw in data[:end].decode("utf-8", "replace").splitlines():
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("kind") == "attention":
            found.append(d)
    return found, offset + end


def notify_attention(plan: str, offset: int) -> int:
    """計画の progress.jsonl の offset から後の attention の行を標準出力へ知らせ、読んだ所を返す。"""
    prog = state_dir_of(plan) / "progress.jsonl"
    found, offset = attention_lines(prog, offset)
    for d in found:
        print(json.dumps({"tool": "supervise-queue", "event": "attention", "plan": plan,
                          "progress": str(prog), **{k: d.get(k) for k in ("at", "step", "reason", "text")}},
                         ensure_ascii=False), flush=True)
    return offset


def progress_size(plan: str) -> int:
    prog = state_dir_of(plan) / "progress.jsonl"
    return prog.stat().st_size if prog.is_file() else 0


def queue_prs(items: list[dict]) -> list[str]:
    """完了した計画の報告の Pull Request を番号にして、重ねずに番号の順に返す（計画の終わった順に依らない）。
    配布の計画（フェーズが「配布」で始まる）の Pull Request は含めない（開発版の後に本番をステージで流すとき）。"""
    out: list[str] = []
    for i in items:
        rep = Path(i.get("report") or "")
        if i.get("result") != "完了" or not rep.is_file():
            continue
        if re.search(r"^- フェーズ: 配布", rep.read_text(), re.M):
            continue
        m = re.search(r"^- Pull Request: (.*)$", rep.read_text(), re.M)
        n = pr_number(m.group(1).strip()) if m else ""
        if n and n not in out:
            out.append(n)
    return sorted(out, key=int)


def fill_queue_prs(plan: str, prs: list[str]) -> str | None:
    """計画の QUEUE_PRS を prs で置き換えて書き戻す。置き換えられなければ理由を返す。"""
    try:
        text = Path(plan).read_text()
    except OSError as e:
        return f"計画を読めない: {e}"
    if QUEUE_PRS not in text:
        return None
    if not prs:
        try:
            conditional = bool(json.loads(text).get("実行の条件"))
        except ValueError:
            conditional = False
        if not conditional:
            return "--prs-from-queue の計画だが、先行の計画の報告に Pull Request が無い"
        # 実行の条件のある計画（最終の検査で変更が無ければ飛ぶ配布）は、条件に判断を任せる
    write_text_atomic(Path(plan), text.replace(QUEUE_PRS, " ".join(prs)))
    return None


QUEUE_PR = re.compile(r"\{queue_pr:([A-Za-z0-9._-]+)\}")  # 名前の一致する計画の Pull Request 1 本


def plan_pr(item: dict) -> str:
    """完了した計画の報告の Pull Request の番号。無ければ空。"""
    rep = Path(item.get("report") or "")
    if item.get("result") != "完了" or not rep.is_file():
        return ""
    m = re.search(r"^- Pull Request: (.*)$", rep.read_text(), re.M)
    return pr_number(m.group(1).strip()) if m else ""


def named(plan: str, name: str) -> bool:
    stem = Path(plan).stem
    return stem == name or stem.endswith(f"-{name}")


def fill_queue_pr(plan: str, items: list[dict]) -> str | None:
    """計画の {queue_pr:<名>} を、前のステージの名前の一致する計画の Pull Request 1 本で置き換える。
    前のステージに無ければ同じディレクトリの計画の報告を読む（関門の後に単独で流すとき）。無い・飛ばされたなら 0。"""
    try:
        text = Path(plan).read_text()
    except OSError as e:
        return f"計画を読めない: {e}"
    names = set(QUEUE_PR.findall(text))
    if not names:
        return None
    for name in names:
        hits = [i for i in items if named(i.get("plan", ""), name)]
        if not hits:
            hits = [{"plan": str(f), "result": report_result((state_dir_of(str(f)) / "report.md").read_text()),
                     "report": str(state_dir_of(str(f)) / "report.md")}
                    for f in sorted(Path(plan).parent.glob("*.json"))
                    if str(f) != plan and named(str(f), name) and (state_dir_of(str(f)) / "report.md").is_file()]
        prs = [n for n in (plan_pr(i) for i in hits) if n]
        text = text.replace(f"{{queue_pr:{name}}}", prs[-1] if prs else "0")
    write_text_atomic(Path(plan), text)
    return None


def write_text_atomic(path: Path, text: str) -> None:
    """一時ファイルへ書いてから rename する（待つ側が書きかけを読まない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def run_batch(plans: list[str], max_: int, poll: float) -> list[dict]:
    """計画を同時に max_ 本まで走らせ、空いた枠へ順に流し、終わった順に結果を返す。"""
    pending, running, items = list(plans), {}, []
    seen: dict[str, int] = {}
    while pending or running:
        while pending and len(running) < max_:
            plan = pending.pop(0)
            # 作業ツリーは queue の側で順に作る（同時の git worktree add は .git/config の lock で落ちる）。
            # 作れなかったときの報告は run が同じ誤りで書く
            try:
                data = json.loads(Path(plan).read_text())
                if not data.get("実行の条件"):  # 条件のある計画は run が条件を打ってから作る
                    paths.ensure_worktree(data)
            except (OSError, ValueError, KeyError, AttributeError):
                pass
            prog = state_dir_of(plan) / "progress.jsonl"
            seen[plan] = prog.stat().st_size if prog.is_file() else 0  # 前の実行の行は知らせない
            log = open(Path(plan).with_suffix(".log"), "w")
            running[plan] = (subprocess.Popen([sys.executable, str(SELF), "run", plan], stdout=log,
                                              stderr=subprocess.STDOUT), log, time.time())
        for plan in list(running):
            seen[plan] = notify_attention(plan, seen[plan])
        for plan, (proc, log, started) in list(running.items()):
            if proc.poll() is None:
                continue
            seen[plan] = notify_attention(plan, seen[plan])
            log.close()
            rep = state_dir_of(plan) / "report.md"
            res = report_result(rep.read_text()) if rep.is_file() else "報告なし"
            items.append({"plan": plan, "result": res, "exit": proc.returncode, "report": str(rep),
                          "seconds": round(time.time() - started, 1)})
            del running[plan]
        if running:
            time.sleep(poll)
    return items


NOT_RUN = "流さなかった"


def cmd_queue(plans: list[str], max_: int, poll: float = 1.0, then: list | None = None,
              done: str | None = None) -> dict:
    """計画を同時に max_ 本まで走らせ、空いた枠へ順に流す。

    走っている計画の progress.jsonl に conductor 向けの行（"kind": "attention"）が足されたら、
    標準出力へ 1 行の JSON（"event": "attention"）で知らせる。最後の行は従来どおり結果の JSON。
    then の計画は、前の計画がすべて 完了 のときだけ同じ枠（max_）で続けて流す。1 本でも 完了 でなければ
    流さず、items に 流さなかった と理由を残す。then の計画の QUEUE_PRS（new release --prs-from-queue）は、
    流す前に前の計画の報告の Pull Request の番号で置き換える。
    then はステージの並び（[[計画...], [計画...]]）でもよい。ステージは前のすべてのステージが 完了 のときだけ流し、QUEUE_PRS は
    前のすべてのステージの Pull Request、{queue_pr:<名>} は前のステージの名前の一致する計画の Pull Request 1 本で置き換える。
    始めに流す計画の一覧を done の隣へ書き（wait が読む）、終わったら（後続を含めて）結果の JSON を done へ書く。"""
    stages = [list(t) for t in then] if then and not isinstance(then[0], str) else ([list(then)] if then else [])
    done_path = queue_done_path(plans, done)
    done_path.unlink(missing_ok=True)  # 前の queue の終わりを待つ側が読まないように、始めに消す
    wait_cursor_path(done_path).unlink(missing_ok=True)
    all_plans = [*plans, *(p for st in stages for p in st)]
    write_text_atomic(queue_plans_path(done_path), json.dumps(
        {"started": now_iso(), "plans": all_plans, "offsets": {p: progress_size(p) for p in all_plans}},
        ensure_ascii=False) + "\n")
    first, early = [], []
    for p in plans:
        err = fill_queue_pr(p, [])
        (early if err else first).append({"plan": p, "result": NOT_RUN, "reason": err} if err else p)
    items = early + run_batch(first, max_, poll)
    for stage in stages:
        not_done = [i for i in items if i["result"] != "完了"]
        if not_done:
            ran_bad = [i for i in not_done if i["result"] != NOT_RUN]
            reason = ("前の計画が完了していない: " + "、".join(f"{i['plan']}（{i['result']}）" for i in ran_bad)
                      if ran_bad else "前のステージを流さなかった")
            items += [{"plan": p, "result": NOT_RUN, "reason": reason} for p in stage]
            continue
        prs, runnable, skipped_then = queue_prs(items), [], []
        for p in stage:
            err = fill_queue_prs(p, prs) or fill_queue_pr(p, items)
            if err:
                skipped_then.append({"plan": p, "result": NOT_RUN, "reason": err})
            else:
                runnable.append(p)
        items += skipped_then + (run_batch(runnable, max_, poll) if runnable else [])
    ran = [i for i in items if i["result"] != NOT_RUN]
    skipped = len(items) - len(ran)
    stopped = [i for i in ran if i["result"] not in ("完了", "関門")]
    gates = [i for i in ran if i["result"] == "関門"]
    status = "stopped" if stopped else "gate" if gates else "ok"
    summary = f"{len(ran)} 本: 完了 {len(ran) - len(stopped) - len(gates)} / 関門 {len(gates)} / 止まった {len(stopped)}"
    if skipped:
        summary += f"。後続 {skipped} 本は流さなかった"
    nxt = "関門の計画の report.md を読んで提示する" if status == "gate" else None
    res = result("supervise-queue", status, summary, items,
                 {"plans": len(ran), "stopped": len(stopped), "gate": len(gates), "not_run": skipped,
                  "max": max_, "done": str(done_path)}, next=nxt)
    write_text_atomic(done_path, json.dumps(res, ensure_ascii=False) + "\n")
    return res


WAIT_DONE, WAIT_ATTENTION, WAIT_TIMEOUT = 0, 20, 3  # 20 は共通の契約の「LLM の判断待ち」、3 は前提が無い


def cmd_wait(done: str, timeout: float, poll: float = 5.0, clock=time.time, sleep=time.sleep) -> tuple[str, dict, int]:
    """queue の終わり（done）か、queue が流す計画の attention の行まで待つ。(要約, 結果, 終了コード) を返す。

    計画の一覧と読み始める所は queue が done の隣に書いた <done>.plans.json から読む。知らせた attention の
    続きは <done>.wait.json に残し、次の wait はそこから読む（同じ行を 2 度知らせない）。"""
    done_path = Path(done)
    plans_path, cursor_path = queue_plans_path(done_path), wait_cursor_path(done_path)
    start = clock()
    while True:
        if done_path.is_file() and done_path.stat().st_size > 0 and not (
                plans_path.is_file() and plans_path.stat().st_mtime > done_path.stat().st_mtime):
            try:
                res = json.loads(done_path.read_text())
            except (OSError, json.JSONDecodeError):
                res = None
            if isinstance(res, dict):
                summary = f"queue が終わった（{res.get('status')}）: {res.get('summary')}"
                return summary, result("supervise-wait", "ok", summary, [res],
                                       {"event": "done", "queue_status": res.get("status"), "done": str(done_path)},
                                       next=res.get("next")), WAIT_DONE
        try:
            listing = json.loads(plans_path.read_text())
        except (OSError, json.JSONDecodeError):
            listing = None
        if isinstance(listing, dict):
            offsets = dict(listing.get("offsets") or {})
            try:
                cur = json.loads(cursor_path.read_text())
                if cur.get("started") == listing.get("started"):
                    offsets.update(cur.get("offsets") or {})
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
            found = []
            for plan in listing.get("plans") or []:
                prog = state_dir_of(plan) / "progress.jsonl"
                lines, offsets[plan] = attention_lines(prog, int(offsets.get(plan, 0)))
                found += [{"plan": plan, "progress": str(prog),
                           **{k: d.get(k) for k in ("at", "step", "reason", "text")}} for d in lines]
            if found:
                write_text_atomic(cursor_path, json.dumps({"started": listing.get("started"), "offsets": offsets},
                                                     ensure_ascii=False) + "\n")
                first = found[0]
                summary = (f"attention {len(found)} 件: {first['plan']} のステップ {first.get('step')}"
                           f"（{first.get('reason')}）: {first.get('text')}")
                return summary, result("supervise-wait", "gate", summary, found,
                                       {"event": "attention", "attention": len(found), "done": str(done_path)},
                                       next="attention を読んで対処し、もう一度 wait を打つ（続きから待つ）"), WAIT_ATTENTION
        if clock() - start >= timeout:
            summary = f"{timeout:g} 秒待ったが queue が終わらず attention も無い"
            return summary, result("supervise-wait", "stopped", summary, [],
                                   {"event": "timeout", "timeout": timeout, "done": str(done_path)},
                                   next="queue の <計画>.log と progress.jsonl を見て、続けるならもう一度 wait を打つ"), WAIT_TIMEOUT
        sleep(poll)
