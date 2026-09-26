"""run・sync-check・design-glossary・note・history・expected の副命令（#1142 の C1）。

`engine` を import するのはこのモジュールだけである。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import mdtable
import slow_step as ss
from step_result import result
from supervise_lib.decl import SUPERVISE_DECL, DeclError, decl_roots, read_decl, sync_checks_of
from supervise_lib.engine import Engine
from supervise_lib.paths import HERE, state_dir_of
from supervise_lib.plan import expand_parts, normalize_plan


def sync_check(root: str, commit: bool, checks: list[tuple[str, str]] | None = None) -> dict:
    """宣言（.ndf/supervise.json の sync_checks）の同期とチェックを順に回す。同期で変わったファイルは commit なら
    1 つのコミットにする。宣言が無ければ回さずに止まる。"""
    if checks is None:
        try:
            checks = sync_checks_of(read_decl([root], SUPERVISE_DECL))
        except DeclError as e:
            return result("supervise-sync-check", "stopped", str(e), [], {"failed": 0, "changed": 0})
    if not checks:
        return result("supervise-sync-check", "stopped",
                      f"同期とチェックの宣言が無い（{root}/.ndf/{SUPERVISE_DECL} の sync_checks）", [],
                      {"failed": 0, "changed": 0})
    items, failed = [], []
    for name, cmd in checks:
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True)
        out = (p.stdout + p.stderr).strip()
        items.append({"name": name, "result": "ok" if p.returncode == 0 else "failed", "exit": p.returncode,
                      "tail": out[-1500:] if p.returncode else ""})
        if p.returncode:
            failed.append(name)
    changed = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                             text=True).stdout.splitlines()
    if commit and changed and "build" not in failed:
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, text=True)
        c = subprocess.run(["git", "commit", "-q", "-m", "Update: 生成物を同期する"], cwd=root,
                           capture_output=True, text=True)
        items.append({"name": "commit", "result": "ok" if c.returncode == 0 else "failed",
                      "exit": c.returncode, "files": len(changed)})
        if c.returncode:
            failed.append("commit")
    summary = f"失敗: {', '.join(failed)}" if failed else f"同期とチェック {len(checks)} 本が通った"
    return result("supervise-sync-check", "stopped" if failed else "ok", summary, items,
                  {"failed": len(failed), "changed": len(changed)})


def cmd_design_glossary(root: str, mode: str, out: str) -> tuple[dict, int | None]:
    """設計のプランの入口（glossary のステップ）。glossary.py gate が通ればそのまま進む。宣言か用語集が無くて
    止まったら、worktree の中で init と candidates を打ち、起こしたファイルをコミットし、候補の語を out へ
    書く（pr のステップが PR 本文へ足す。語の採否は承認ゲート 1 で利用者が見る）。それ以外の失敗は止まる。"""
    gl = [sys.executable, str(HERE / "glossary.py")]

    def call(*args) -> tuple[int, dict]:
        p = subprocess.run([*gl, *args, "--root", root], capture_output=True, text=True, cwd=root)
        last = next((l for l in reversed(p.stdout.splitlines()) if l.strip()), "")
        try:
            return p.returncode, json.loads(last)
        except ValueError:
            return p.returncode, {"summary": (p.stderr or p.stdout).strip()[-300:]}

    code, res = call("gate", "--mode", mode)
    if code == 0:
        return result("supervise-design-glossary", "ok", res.get("summary", "用語集は揃っている"), [],
                      {"initialized": 0}), None
    if code != 1:
        return result("supervise-design-glossary", "stopped", f"glossary.py gate が失敗した: {res.get('summary')}",
                      [], {"initialized": 0}), code
    # candidates は init より前に打つ（宣言が無くても既定の節で動く）。init の後で止まると起こしたファイルが
    # 未コミットで残り、打ち直しの gate が通って用語集のコミットも候補の語も書かれずに進むため
    code, cand = call("candidates")
    if code != 0:
        # 候補の欠落を「0 件」と区別できなくなるので、承認ゲート 1 の材料が揃わないまま進めない
        return result("supervise-design-glossary", "stopped", f"glossary.py candidates が失敗した: {cand.get('summary')}",
                      [], {"initialized": 0}), code
    words = cand.get("items") or []
    code, init = call("init")
    if code != 0:
        return result("supervise-design-glossary", "stopped", f"glossary.py init が失敗した: {init.get('summary')}",
                      [], {"initialized": 0}), code
    created = [it["name"] for it in init.get("items") or []]
    if created:
        for args in (["add", "--", *created],
                     ["commit", "-q", "-m", "docs(glossary): 設計の入口で用語集を起こす", "--", *created]):
            p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
            if p.returncode != 0:
                # 起こしたファイルを消して、打ち直しが同じ経路（gate の停止 → init → コミット）を通るようにする
                subprocess.run(["git", "rm", "-q", "--cached", "--ignore-unmatch", "--", *created],
                               cwd=root, capture_output=True, text=True)
                for c in created:
                    Path(root, c).unlink(missing_ok=True)
                return result("supervise-design-glossary", "stopped",
                              f"起こした用語集をコミットできない: {p.stderr.strip()[-300:]}", [],
                              {"initialized": 0}), 1
    rows = [[w.get("term"), w.get("count"), w.get("kind"), f"`{w.get('first')}`"] for w in words]
    note = ("## 用語集の候補\n\nこの Pull Request で用語集を起こした（`glossary.py init`）: "
            + "、".join(f"`{c}`" for c in created) + "。\n語の採否は承認ゲート 1 で見る。候補の語（`glossary.py candidates`）:\n\n"
            + (mdtable.table_markdown(("語", "回数", "種類", "最初の場所"), rows, align=(None, "right", None, None))
               if rows
               else "候補は 0 件（要求から語を起こす）") + "\n")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(note)
    shown = "、".join(str(w.get("term")) for w in words[:10])
    return result("supervise-design-glossary", "ok",
                  f"用語集を起こしてコミットした（候補 {len(words)} 件{': ' + shown if shown else ''}）",
                  [{"name": c, "result": "created"} for c in created],
                  {"initialized": 1, "candidates": len(words)}), None


def note_row(report: str, next_text: str) -> str:
    def field(name: str) -> str:
        m = re.search(rf"^- {name}: (.*)$", report, re.M)
        return m.group(1).strip() if m else ""
    cost = re.search(r"/ \$([0-9.]+)\s*$", field("LLM の使用量"))
    pr = field("Pull Request")
    state = f"{field('フェーズ') or field('持ち場')}: {field('結果')}"  # 旧い報告（持ち場）も読む
    extra = [x for x in ((pr if pr and pr != "無し" else ""), (f"${cost.group(1)}" if cost else "")) if x]
    if extra:
        state += "（" + "、".join(extra) + "）"
    if field("結果") != "完了" and field("理由") not in ("", "無し"):
        state += f"。理由: {field('理由')}"
    return f"| {field('課題') or '—'} | {state} | {next_text or '—'} |"


def cmd_note(doc: str, report_path: str, next_text: str, section: str) -> dict:
    """引継ぎ文書の、見出しに section を含む節の最初の表の末尾へ 1 行を足す。"""
    lines = Path(doc).read_text().splitlines()
    head = next((i for i, l in enumerate(lines) if l.startswith("#") and section in l), None)
    if head is None:
        return result("supervise-note", "stopped", f"見出しに「{section}」を含む節が無い", [], {})
    last = None
    for i in range(head + 1, len(lines)):
        if lines[i].startswith("#"):
            break
        if lines[i].startswith("|"):
            last = i
        elif last is not None:
            break
    if last is None:
        return result("supervise-note", "stopped", f"節「{lines[head].lstrip('# ')}」に表が無い", [], {})
    row = note_row(Path(report_path).read_text(), next_text)
    lines.insert(last + 1, row)
    Path(doc).write_text("\n".join(lines) + "\n")
    return result("supervise-note", "ok", "表へ 1 行を足した", [{"path": doc, "line": last + 2, "row": row}],
                  {"rows": 1})


def cmd_history_import(paths: list[str], history: str | None) -> dict:
    """既存の progress.jsonl のステップの所要を履歴へ取り込む。"""
    if history:
        target = Path(history)
    else:
        try:
            conf = (read_decl([Path.cwd()], SUPERVISE_DECL).get("slow") or {}).get("history")
        except (DeclError, AttributeError):
            conf = None
        target = ss.history_path(Path.cwd(), Path(paths[0]).resolve().parent, conf)
    try:
        got = ss.import_progress(paths, target)
    except OSError as e:
        return result("supervise-history", "stopped", str(e))
    items = [{"kind": "file", "name": u, "result": "unreadable"} for u in got["unreadable"]]
    if len(got["unreadable"]) == len(paths):
        return result("supervise-history", "stopped", "progress.jsonl を 1 本も読めない", items)
    return result("supervise-history", "ok", f"{got['added']} 行を {target} へ取り込んだ（重複 {got['skipped']} 行）",
                  items, {"added": got["added"], "skipped": got["skipped"], "history": str(target)})


def cmd_expected(plan_path: str, history: str | None, slow_pairs: list[str]) -> tuple[dict, int | None]:
    """計画のステップごとの想定と根拠を出す（run・work・drive のステップ）。"""
    try:
        plan = normalize_plan(json.loads(Path(plan_path).read_text()))
        steps = expand_parts(list(plan["steps"]))
    except (OSError, ValueError, KeyError, TypeError) as e:
        return result("supervise-expected", "stopped", f"計画を読めない: {e}"), 2
    wt = Path(str(plan.get("作業場所") or "."))
    repo = plan.get("リポジトリ") or (str(wt).split("/.worktrees/")[0] if "/.worktrees/" in str(wt) else None)
    try:
        decl = read_decl(decl_roots(str(wt), repo), SUPERVISE_DECL).get("slow")
        cfg = ss.resolve_config(ss.parse_overrides(slow_pairs), plan.get("slow"), decl)
    except (DeclError, ss.SlowConfigError) as e:
        return result("supervise-expected", "stopped", f"slow の設定が読めない（{e.args[0]}）"), 2
    cwd = wt if wt.is_dir() else Path(repo) if repo else None
    target = Path(history) if history else ss.history_path(cwd, state_dir_of(plan_path), cfg.history)
    phase = plan.get("フェーズ") or "?"
    items = []
    for st in steps:
        if st.get("type") not in ss.WATCHED_TYPES:
            continue
        value, basis = ss.expected_for(ss.read_history(target, phase, st["id"], cfg.window), cfg, st.get("expected"))
        items.append({"kind": "step", "name": st["id"], "type": st["type"], "expected": value, "basis": basis})
    return result("supervise-expected", "ok", f"{len(items)} ステップの想定を出した（フェーズ {phase}・履歴 {target}）",
                  items, {"history": str(target), "enabled": cfg.enabled}), None


def cmd_run(plan_path: str, state_dir: str | None, slow: list[str], start: str | None) -> int:
    """プランを 1 本流し、報告を標準出力へ出す。終了コードは完了か関門なら 0、それ以外は 3。"""
    plan = json.loads(Path(plan_path).read_text())
    state = Path(state_dir) if state_dir else state_dir_of(plan_path)
    text = Engine(plan, state, slow, plan_path).run(start)
    print(text)
    return 0 if "結果: 完了" in text or "結果: 関門" in text else 3
