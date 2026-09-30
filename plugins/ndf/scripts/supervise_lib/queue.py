"""queue と wait: プランを 1 つのプロセスの中で並べて流し、終わりか attention まで待つ（#1142 の C1・決定 26）。

`cmd_queue` は耐久ワークフロー `flow.queue_workflow` を始めるか続け、終わるまでプランの attention を標準出力へ
知らせる。落ちた後に同じコマンドを打ち直すと、耐久の記録から流れていたステップで続ける。このモジュールは
DBOS を import しない（`wait` ほかの副命令が払わないよう、`flow` は `cmd_queue` の中で読む）。`flow` の
耐久ステップが呼ぶ、ファイルだけに触る関数（一覧・QUEUE_PRS の埋め込み・結果の組み立て）もここに置く。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from clock import now_iso  # cmd_wait の引数 clock（時計の差し替え）と名前を分ける
from step_result import result
from supervise_lib.decl import DeclError, queue_decl_of
from supervise_lib.paths import queue_done_path, queue_plans_path, report_result, state_dir_of, wait_cursor_path
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
        print(
            json.dumps(
                {
                    "tool": "supervise-queue",
                    "event": "attention",
                    "plan": plan,
                    "progress": str(prog),
                    **{k: d.get(k) for k in ("at", "step", "reason", "text")},
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
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
            hits = [
                {
                    "plan": str(f),
                    "result": report_result((state_dir_of(str(f)) / "report.md").read_text()),
                    "report": str(state_dir_of(str(f)) / "report.md"),
                }
                for f in sorted(Path(plan).parent.glob("*.json"))
                if str(f) != plan and named(str(f), name) and (state_dir_of(str(f)) / "report.md").is_file()
            ]
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


NOT_RUN = "流さなかった"
FINISHED = ("完了", "関門")  # 打ち直しでも流し直さない結果（止まったは次の実行の回で頭から流す）
NO_REPORT = "報告なし"


def cmd_queue(plans: list[str], max_: int, poll: float = 1.0, then: list | None = None, done: str | None = None) -> dict:
    """プランを同時に max_ 本まで、同じプロセスの中で流す。空いた枠へ入れた順に流す。

    流れているプランの progress.jsonl に conductor 向けの行（"kind": "attention"）が足されたら、
    標準出力へ 1 行の JSON（"event": "attention"）で知らせる。最後の行は結果の JSON。
    then のプランは、前のプランがすべて 完了 のときだけ同じ枠（max_）で続けて流す。1 本でも 完了 でなければ
    流さず、items に 流さなかった と理由を残す。then のプランの QUEUE_PRS（new release --prs-from-queue）は、
    流す前に前のプランの報告の Pull Request の番号で置き換える。
    then はステージの並び（[[プラン...], [プラン...]]）でもよい。ステージは前のすべてのステージが 完了 のときだけ流し、QUEUE_PRS は
    前のすべてのステージの Pull Request、{queue_pr:<名>} は前のステージの名前の一致するプランの Pull Request 1 本で置き換える。
    同じステージで `触るファイル` が重なるか同じ共有の一覧に当たるプラン（重なりの組）は、知らせて 1 本ずつ流す。資源のタグの
    あるステップは、資源の枠の本数まで同時に流す（宣言は .ndf/supervise.json の queue）。
    始めに流すプランの一覧を done の隣へ書き（wait が読む）、終わったら（後続を含めて）結果の JSON を done へ書く。
    落ちた後に同じコマンドを打ち直すと、流れていたステップから続ける。流すプランの並びを変えて打ち直すと、途中の
    記録を止めて頭から流す（完了か関門の記録があるプランは流し直さない）。"""
    import durable
    from supervise_lib import flow  # DBOS の import は run と queue だけが払う

    then_stages = [list(t) for t in then] if then and not isinstance(then[0], str) else ([list(then)] if then else [])
    stages = [list(plans), *then_stages]
    all_plans = [p for stage in stages for p in stage]
    done_path = queue_done_path(plans, done)
    try:
        decl = queue_decl_of([_read_plan(p) for p in all_plans])
    except DeclError as e:
        return _not_started(str(e), max_, done_path)
    done_path.unlink(missing_ok=True)  # 前の queue の終わりを待つ側が読まないように、始めに消す
    seen = {p: progress_size(p) for p in all_plans}  # 前の実行の行は知らせない
    listing = _read_listing(queue_plans_path(done_path))
    try:
        prefix = flow.launch_queue(done_path, max_, decl["resources"], listing is not None and listing.get("plans") != all_plans)
    except durable.DurableError as e:
        return _not_started(str(e), max_, done_path)
    try:
        ref = durable.resolve(prefix)
        if ref.action == "continue":
            print(f"[ndf supervise] 落ちた前の起動の {ref.id} を続ける（済んだステップは流し直さない）", file=sys.stderr)
        args = {"stages": stages, "max": max_, "done": str(done_path), "resources": decl["resources"], "shared": decl["shared"]}
        wid = durable.start(ref, flow.queue_workflow, args)
        while True:
            got = durable.wait(wid, poll=min(poll, durable.POLL_SECONDS), timeout=poll)
            for p in all_plans:
                seen[p] = notify_attention(p, seen[p])
            if got.kind != "timeout":
                break
        if got.kind != "done":
            durable.output_of(wid)  # 耐久ワークフローの中の例外をそのまま上げる
            raise durable.DurableError(f"耐久ワークフロー {wid} が終わらなかった（{got.kind}: {got.value}）")
        return got.value
    finally:
        durable.close()


def _read_plan(plan: str) -> dict:
    try:
        data = json.loads(Path(plan).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _not_started(reason: str, max_: int, done_path: Path) -> dict:
    """流す前に止まったときの結果（宣言の形が違う・耐久の記録を開けない）。"""
    metrics = {"plans": 0, "stopped": 0, "gate": 0, "not_run": 0, "max": max_, "done": str(done_path)}
    return result("supervise-queue", "stopped", reason, [], metrics)


def start_listing(done_path: Path, all_plans: list[str]) -> None:
    """前の queue の終わりと wait の続きを消し、流すプランの一覧と読み始める所を done の隣へ書く（wait が読む）。"""
    done_path.unlink(missing_ok=True)
    wait_cursor_path(done_path).unlink(missing_ok=True)
    write_text_atomic(
        queue_plans_path(done_path),
        json.dumps({"started": now_iso(), "plans": all_plans, "offsets": {p: progress_size(p) for p in all_plans}}, ensure_ascii=False)
        + "\n",
    )


def fill_stage(first: bool, stage: list[str], items: list[dict]) -> tuple[list[dict], list[str]]:
    """ステージを流す前に、プランの {queue_pr:<名>} と QUEUE_PRS を埋める。(流さないプランの items, 流すプラン) を返す。

    後続のステージ（first でない）は、前のすべてのプランが 完了 のときだけ流す。埋められないプランは流さずに理由を残す。"""
    not_done = [i for i in items if i["result"] != "完了"]
    if not first and not_done:
        ran_bad = [i for i in not_done if i["result"] != NOT_RUN]
        reason = (
            "前の計画が完了していない: " + "、".join(f"{i['plan']}（{i['result']}）" for i in ran_bad)
            if ran_bad
            else "前のステージを流さなかった"
        )
        return [{"plan": p, "result": NOT_RUN, "reason": reason} for p in stage], []
    prs = None if first else queue_prs(items)
    skipped, runnable = [], []
    for p in stage:
        err = (None if prs is None else fill_queue_prs(p, prs)) or fill_queue_pr(p, items)
        if err:
            skipped.append({"plan": p, "result": NOT_RUN, "reason": err})
        else:
            runnable.append(p)
    return skipped, runnable


def touched_files(body: str) -> list[str]:
    """プランの JSON の `触るファイル`（読めない・無いなら空）。"""
    try:
        files = json.loads(body).get("触るファイル")
    except (ValueError, AttributeError):
        return []
    return [str(f) for f in files if str(f).strip()] if isinstance(files, list) else []


def plan_item(plan: str, out: dict) -> dict:
    """プランの耐久ワークフローの出力から、結果の JSON の items の 1 件を作り、報告を <プラン>.log へ書く。"""
    rep = state_dir_of(plan) / "report.md"
    if "error" in out:
        print(f"supervise-queue: {plan} が例外で終わった: {out['error']}", file=sys.stderr, flush=True)
        return {"plan": plan, "result": NO_REPORT, "exit": 1, "report": str(rep), "seconds": 0.0, "reason": out["error"]}
    try:
        Path(plan).with_suffix(".log").write_text(out["report"].rstrip("\n") + "\n", encoding="utf-8")
    except OSError:
        pass
    code = 0 if out["result"] in FINISHED else 3
    return {"plan": plan, "result": out["result"], "exit": code, "report": str(rep), "seconds": out.get("seconds", 0.0)}


def write_done(done_path: Path, items: list[dict], max_: int, overlaps: list[dict], limits: dict) -> dict:
    """items へ重なりの組を足して結果の JSON を組み立て、done へ書いて返す。"""
    for i in items:
        mine = [{"with": o["b"], "paths": o["paths"]} for o in overlaps if o["a"] == i["plan"]]
        mine += [{"with": o["a"], "paths": o["paths"][::-1]} for o in overlaps if o["b"] == i["plan"]]
        if mine:
            i["overlap"] = mine
    res = _queue_result(items, max_, done_path)
    res["metrics"].update(overlaps=len(overlaps), resource_limits=limits)
    write_text_atomic(done_path, json.dumps(res, ensure_ascii=False) + "\n")
    return res


def _queue_result(items: list[dict], max_: int, done_path: Path) -> dict:
    """items から status・summary・metrics を組み立てる。"""
    ran = [i for i in items if i["result"] != NOT_RUN]
    skipped = len(items) - len(ran)
    stopped = [i for i in ran if i["result"] not in FINISHED]
    gates = [i for i in ran if i["result"] == "関門"]
    status = "stopped" if stopped else "gate" if gates else "ok"
    summary = f"{len(ran)} 本: 完了 {len(ran) - len(stopped) - len(gates)} / 関門 {len(gates)} / 止まった {len(stopped)}"
    if skipped:
        summary += f"。後続 {skipped} 本は流さなかった"
    nxt = "関門の計画の report.md を読んで提示する" if status == "gate" else None
    return result(
        "supervise-queue",
        status,
        summary,
        items,
        {"plans": len(ran), "stopped": len(stopped), "gate": len(gates), "not_run": skipped, "max": max_, "done": str(done_path)},
        next=nxt,
    )


WAIT_DONE, WAIT_ATTENTION, WAIT_TIMEOUT = 0, 20, 3  # 20 は共通の契約の「LLM の判断待ち」、3 は前提が無い


def cmd_wait(done: str, timeout: float, poll: float = 5.0, clock=time.time, sleep=time.sleep) -> tuple[str, dict, int]:
    """queue の終わり（done）か、queue が流す計画の attention の行まで待つ。(要約, 結果, 終了コード) を返す。

    計画の一覧と読み始める所は queue が done の隣に書いた <done>.plans.json から読む。知らせた attention の
    続きは <done>.wait.json に残し、次の wait はそこから読む（同じ行を 2 度知らせない）。"""
    done_path = Path(done)
    plans_path, cursor_path = queue_plans_path(done_path), wait_cursor_path(done_path)
    start = clock()
    while True:
        res = _read_done(done_path, plans_path)
        if res is not None:
            return _done_response(res, done_path)
        listing = _read_listing(plans_path)
        if listing is not None:
            found, offsets = _collect_attention(listing, cursor_path)
            if found:
                write_text_atomic(
                    cursor_path, json.dumps({"started": listing.get("started"), "offsets": offsets}, ensure_ascii=False) + "\n"
                )
                return _attention_response(found, done_path)
        if clock() - start >= timeout:
            return _timeout_response(timeout, done_path)
        sleep(poll)


def _read_done(done_path: Path, plans_path: Path) -> dict | None:
    """queue の結果（done）を読む。無い・書きかけ・今の queue より古い・読めないときは None。"""
    if not (
        done_path.is_file()
        and done_path.stat().st_size > 0
        and not (plans_path.is_file() and plans_path.stat().st_mtime > done_path.stat().st_mtime)
    ):
        return None
    try:
        res = json.loads(done_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return res if isinstance(res, dict) else None


def _read_listing(plans_path: Path) -> dict | None:
    try:
        listing = json.loads(plans_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return listing if isinstance(listing, dict) else None


def _collect_attention(listing: dict, cursor_path: Path) -> tuple[list[dict], dict]:
    """計画の一覧と前の wait の続きから、まだ知らせていない attention の行と次の読み始める所を返す。"""
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
        found += [{"plan": plan, "progress": str(prog), **{k: d.get(k) for k in ("at", "step", "reason", "text")}} for d in lines]
    return found, offsets


def _done_response(res: dict, done_path: Path) -> tuple[str, dict, int]:
    summary = f"queue が終わった（{res.get('status')}）: {res.get('summary')}"
    return (
        summary,
        result(
            "supervise-wait",
            "ok",
            summary,
            [res],
            {"event": "done", "queue_status": res.get("status"), "done": str(done_path)},
            next=res.get("next"),
        ),
        WAIT_DONE,
    )


def _attention_response(found: list[dict], done_path: Path) -> tuple[str, dict, int]:
    first = found[0]
    summary = f"attention {len(found)} 件: {first['plan']} のステップ {first.get('step')}（{first.get('reason')}）: {first.get('text')}"
    return (
        summary,
        result(
            "supervise-wait",
            "gate",
            summary,
            found,
            {"event": "attention", "attention": len(found), "done": str(done_path)},
            next="attention を読んで対処し、もう一度 wait を打つ（続きから待つ）",
        ),
        WAIT_ATTENTION,
    )


def _timeout_response(timeout: float, done_path: Path) -> tuple[str, dict, int]:
    summary = f"{timeout:g} 秒待ったが queue が終わらず attention も無い"
    return (
        summary,
        result(
            "supervise-wait",
            "stopped",
            summary,
            [],
            {"event": "timeout", "timeout": timeout, "done": str(done_path)},
            next="プランの progress.jsonl を見て、続けるならもう一度 wait を打つ",
        ),
        WAIT_TIMEOUT,
    )
