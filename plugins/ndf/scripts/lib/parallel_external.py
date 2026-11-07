"""外のオーガナイザーとファイルでやり取りする切り替え口（#2439。契約 `contracts/forms/parallel-external.json`）。

環境変数 `NDF_ORGANIZER_DIR` があるとき、このセッションは外のオーガナイザーのレーン（名前は `NDF_ORGANIZER_LANE`）で、
リリースを自分で組まずに置き場へリリースの依頼を書く。外のオーガナイザーが答え・リリース済みの範囲・まとめたリリースの中身を
置き場へ書き、リリースの実行は提示・届いた段・結果を書き返す。どちらの書き手も一時ファイルへ書いて名前を付け替え、
読み手は名前の付いたファイルだけを読む。

```text
<NDF_ORGANIZER_DIR>/
├── requests/<依頼の id>.json      # レーンが書く
├── answers/<依頼の id>.json       # 外のオーガナイザーが書く
├── ranges/<連番>.json             # 外のオーガナイザーが書く
├── batch.json                     # 外のオーガナイザーが書く（リリースの実行だけ）
├── presented/<連番>-<提示>.json   # リリースの実行が書く
├── progress/<連番>.json           # リリースの実行が書く
├── results/<連番>.json            # リリースの実行が書く
└── releases/<連番>/<提示>/        # リリースの実行の提示のプランと本番のプラン
```
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import parallel_request_form as prf
import repo
from gate_answer import RELEASE_GATE, canonical

DIR_ENV = "NDF_ORGANIZER_DIR"
LANE_ENV = "NDF_ORGANIZER_LANE"
# 外のオーガナイザーのレーンの計画名の代わり。`parallel.py <副命令> --external` の形にそのままなる
EXTERNAL = "--external"
STEPS = ("present", "represent", "confirm:1", "confirm:2", "continue")
STAGES = ("release_branch", "develop_merged", "prod_pr_opened", "main_merged", "tagged")


def organizer_dir() -> Optional[Path]:
    v = os.environ.get("NDF_ORGANIZER_DIR")
    return Path(v) if v else None


def organizer_lane() -> Optional[str]:
    return os.environ.get("NDF_ORGANIZER_LANE") or None


def is_external(name: Optional[str]) -> bool:
    return name == EXTERNAL


def put_json(path: Path, data) -> Path:
    """一時ファイルへ書いて名前を付け替える（読み手が書きかけを読まない）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)
    return path


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def session_ndf_version() -> str:
    """このセッションの NDF の版（plugin.json）。読めなければ空。

    サービスの実行ジョブは入れた版を `NDF_VERSION` で渡す。開発版は `<version>@<コミットの SHA>` で、plugin.json の
    version だけでは固定したコミットが落ち、リリースの実行が無い開発版のタグを取りに行くため、version が同じならそちらを返す。"""
    got = read_json(Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json", {}) or {}
    version = str(got.get("version") or "")
    pinned = os.environ.get("NDF_VERSION") or ""
    if version and "@" in pinned and pinned.split("@", 1)[0] == version:
        return pinned
    return version


# ---- レーンの側 ----


def write_request_file(d: Path, rid: str, lane: str, prs: list[int], issues: list[int], state: Path) -> dict:
    """リリースの依頼 `requests/<id>.json`（#2436 の依頼の形に `ndf_version` を足す）。同じ id の打ち直しは上書きで同じ中身。"""
    msg = {
        "kind": "request",
        "id": rid,
        "lane": lane,
        "prs": list(prs),
        "issues": list(issues),
        "sprint_state": str(state),
        "at": utc_stamp(),
        "ndf_version": session_ndf_version(),
    }
    old = read_json(Path(d) / "requests" / f"{rid}.json")
    if isinstance(old, dict) and old.get("prs") == msg["prs"]:
        return old  # 打ち直しでは時刻を変えない（送り直しで同じ依頼になる）
    put_json(Path(d) / "requests" / f"{rid}.json", msg)
    return msg


def read_answer_file(d: Path, rid: str) -> Optional[dict]:
    got = read_json(Path(d) / "answers" / f"{rid}.json")
    return got if isinstance(got, dict) else None


def answer_files_of(d: Path, ids: list[str]) -> dict[str, dict]:
    """`parallel_lane.answers_of` と同じ形（{id: {"answer", "defer"}}）。外のオーガナイザーは知らせを書かない。"""
    return {i: {"answer": read_answer_file(d, i), "defer": None} for i in ids}


def range_version(d: Path, prs: list[int]) -> Optional[str]:
    """`prs` を含むリリース済みの範囲 `ranges/<連番>.json` の版（無ければ None）。"""
    found = None
    rd = Path(d) / "ranges"
    for p in sorted(rd.glob("*.json"), key=lambda x: int(x.stem) if x.stem.isdigit() else 0) if rd.is_dir() else []:
        got = read_json(p, {}) or {}
        if set(prs) & set(got.get("prs") or []):
            found = got.get("version")
    return found


# ---- リリースの実行の側 ----


def read_batch(d: Path) -> dict:
    got = read_json(Path(d) / "batch.json")
    if not isinstance(got, dict) or got.get("step") not in STEPS or not isinstance(got.get("seq"), int):
        raise ValueError(f"batch.json が無いか形が違う（置き場: {d}）")
    return got


def requests_of(batch: dict) -> list[dict]:
    """まとめたリリースの依頼（`parallel_release.map_lanes` が読む形 {id, lane, prs}）。"""
    return [
        {"id": r["id"], "lane": ln["lane"], "prs": list(r.get("prs") or [])}
        for ln in batch.get("lanes") or []
        for r in ln.get("requests") or []
    ]


def plan_of(batch: dict):
    """まとめたリリースのレーンを、`parallel_release.map_lanes` が読む並列の計画の形にする（依頼の無いレーンは課題で対応づける）。"""
    import parallel_plan as pp

    lanes = [
        pp.Lane.model_construct(
            name=ln["lane"],
            input=pp.SprintInput.model_construct(kind="sprint", issues=list(ln.get("issues") or []), args=[]),
            plans=1,
            touches=[],
        )
        for ln in batch.get("lanes") or []
    ]
    return pp.ParallelPlan.model_construct(version=1, name=EXTERNAL, rules=[], lanes=lanes)


def batch_release_dir(d: Path, seq: int, k: int) -> Path:
    return Path(d) / "releases" / str(seq) / str(k)


def step_k(step: str, batch: dict) -> int:
    """手の提示の番号。present は 1、represent は 2、confirm:<k> は k、continue は最後の提示。"""
    if step == "present":
        return 1
    if step == "represent":
        return 2
    if step.startswith("confirm:"):
        return int(step.split(":", 1)[1])
    ks = [int(p.get("k") or 0) for p in batch.get("presentations") or []]
    return max(ks) if ks else 1


def compose(main: Path, d: Path, batch: dict, supervise: str, parallel: str, version_of: Callable, builder=None) -> dict:
    """まとめたリリースの手（`batch.json` の `step`）ごとに流すものを返す（#2436 の一時レーンの `helper` と同じ形）。

    - present / represent: 提示 k の提示のプランと本番のプランを `releases/<連番>/<k>/` に作り、`pipeline` は提示のプラン → 本番のプラン
    - confirm:<k>: 答えの記録 → 提示 k のプランを `approve` から → 提示 k の本番のプラン（確定していない提示の本番のプランは流さない）
    - continue: 止まった本番のプランを再開の手順で続ける"""
    import parallel_release as pr

    step, seq = batch["step"], batch["seq"]
    k = step_k(step, batch)
    rd = batch_release_dir(d, seq, k)
    out = {"seq": seq, "k": k, "step": step, "dir": str(rd), "record": f"{parallel} present --external {seq} {k} --record"}
    if step in ("present", "represent"):
        version = version_of(Path(main))
        if not version:
            raise ValueError("出す版を決められない（ベースブランチの配るプラグインの plugin.json の版を読めない）")
        rd.mkdir(parents=True, exist_ok=True)
        put_json(rd / "version.json", {"version": version})
        present_plan, release_plan = (builder or pr.plan_parallel_release)(main, EXTERNAL, rd, version)
        put_json(rd / "present.json", present_plan)
        put_json(rd / "release.json", release_plan)
        if not (rd / "answer.json").exists():
            put_json(rd / "answer.json", pr.answer_state(Path(main)))
        done = rd / "done.json"
        return {
            **out,
            "pipeline": f"{pr.pipeline_cmd(supervise, rd)} && {supervise} wait {done}",
            "settle": f"{parallel} settle --external {seq} --done {done}",
        }
    done = rd / "release-done.json"
    if step.startswith("confirm:"):
        gate = pr.record_answer_cmd(rd, seq, k)
        steps = [gate, f"{supervise} run {rd / 'present.json'} --from approve", f"{supervise} queue {rd / 'release.json'} --done {done}"]
        return {
            **out,
            "pipeline": " && ".join([*steps, f"{supervise} wait {done}"]),
            "settle": f"{parallel} settle --external {seq} --done {done}",
        }
    # continue は前に実際に起動したキューの記録から続ける。confirm は release-done.json に書き、present / represent を
    # MVV 判定が通して本番まで流れたときは done.json にしか無い（confirm は present の後なので release-done.json を先に見る）
    if not done.exists() and (rd / "done.json").exists():
        done = rd / "done.json"
    return {
        **out,
        "pipeline": f"{supervise} queue --resume {done} && {supervise} wait {done}",
        "settle": f"{parallel} settle --external {seq} --done {done}",
    }


def present_batch(main: Path, d: Path, batch: dict, seq: int, k: int, approval: Path, closes_of=None) -> dict:
    """提示のプランの中で、範囲の Pull Request をレーンへ対応づけて `releases/<連番>/<k>/presented.json` を書く（#2436 と同じ関数）。"""
    import parallel_release as pr

    rd = batch_release_dir(d, seq, k)
    first = read_json(batch_release_dir(d, seq, 1) / "presented.json") if k != 1 else None
    kwargs = {"closes_of": closes_of} if closes_of else {}
    return pr.present(main, plan_of(batch), approval, requests_of(batch), rd, first, **kwargs)


def record_presented(d: Path, seq: int, k: int, auto: bool) -> dict:
    """提示を外のオーガナイザーへ書き返す `presented/<連番>-<k>.json`。リリース承認ゲートで止まったら auto は偽、MVV 判定が通したら真。"""
    rd = batch_release_dir(d, seq, k)
    got = read_json(rd / "presented.json")
    if not isinstance(got, dict) or not got.get("sha"):
        raise ValueError(f"提示 {seq}-{k} の presented.json が無い")
    version = (read_json(rd / "version.json", {}) or {}).get("version") or ""
    added = (got.get("added") or {}).get("lanes") or {}
    data = {
        "seq": seq,
        "k": k,
        "sha": got["sha"],
        "version": version,
        "prs": list(got.get("prs") or []),
        "lanes": [{"lane": n, "requested": bool(r.get("requested"))} for n, r in sorted((got.get("lanes") or {}).items())],
        "added_prs": sorted({n for r in added.values() for n in r.get("prs") or []} | set((got.get("added") or {}).get("orphans") or [])),
        "auto": bool(auto),
    }
    put_json(Path(d) / "presented" / f"{seq}-{k}.json", data)
    return data


def stages_of(root: Path, version: str, run=subprocess.run) -> list[str]:
    """リリースが届いた段を GitHub で読む（head が `release/v<版>` の本番のブランチ宛ての Pull Request）。

    本番のブランチが決まらなければ Pull Request を照会せず、ブランチの有無だけを見る。"""
    if not version:
        return []
    head = f"release/v{version}"
    prod = repo.production_branch(root)[0]
    pulls = []
    if prod:
        got = run(
            ["gh", "pr", "list", "--head", head, "--base", prod, "--state", "all", "--json", "state,url"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        try:
            pulls = json.loads(got.stdout or "[]") if got.returncode == 0 else []
        except ValueError:
            pulls = []
    if any(p.get("state") == "MERGED" for p in pulls):
        return ["release_branch", "prod_pr_opened", "main_merged"]
    if pulls:
        return ["release_branch", "prod_pr_opened"]
    br = run(["git", "ls-remote", "--exit-code", "--heads", "origin", head], cwd=root, capture_output=True, text=True)
    return ["release_branch"] if br.returncode == 0 else []


def prod_pr_url(root: Path, version: str, run=subprocess.run) -> Optional[str]:
    prod = repo.production_branch(root)[0]
    if not prod:
        return None
    got = run(
        ["gh", "pr", "list", "--head", f"release/v{version}", "--base", prod, "--state", "merged", "--json", "url", "-q", ".[0].url"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    url = (got.stdout or "").strip()
    return url if got.returncode == 0 and url else None


def mvv_passed(rd: Path) -> bool:
    """提示の答えの記録（`answer.json`）に、MVV 判定がリリース承認ゲートを通した記録（by: mvv）があるか。

    presented.json を書いた後に MVV 判定そのものが落ちた提示を、自動で承認したと書き返さないために見る。"""
    gates = (read_json(Path(rd) / "answer.json", {}) or {}).get("gates") or []
    return any(isinstance(g, dict) and canonical(g.get("name")) == RELEASE_GATE and g.get("by") == "mvv" for g in gates)


def settle_batch(main: Path, d: Path, batch: dict, seq: int, done: dict, reason: Optional[str] = None, run=subprocess.run) -> dict:
    """本番のプランの done とリリース記録から、届いた段 `progress/<連番>.json` と結果 `results/<連番>.json` を書く。

    結果は done の全項目が完了なら released、承認ゲートで止まっていれば ValueError（書かない）、ほかは failed。
    MVV 判定が通してリリース承認ゲートで止まらなかった提示は、ここで `presented/` を auto 真で書く。"""
    results = [str(i.get("result")) for i in done.get("items") or [] if isinstance(i, dict)]
    if "関門" in results:
        raise ValueError("本番のプランが承認ゲートで止まっている")
    k = step_k(batch.get("step") or "continue", batch)
    rd = batch_release_dir(d, seq, k)
    version = (read_json(rd / "version.json", {}) or {}).get("version") or done.get("version") or ""
    if not (Path(d) / "presented" / f"{seq}-{k}.json").exists() and (rd / "presented.json").exists() and mvv_passed(rd):
        record_presented(d, seq, k, auto=True)
    presented = read_json(rd / "presented.json", {}) or {}
    stages = stages_of(main, version, run)
    put_json(Path(d) / "progress" / f"{seq}.json", {"seq": seq, "stages": stages})
    released = bool(results) and all(r == "完了" for r in results)
    data = {
        "seq": seq,
        "result": "released" if released else "failed",
        "version": version or None,
        "prod_pr_url": prod_pr_url(main, version, run) if released else None,
        "sha": presented.get("sha"),
        "prs": list(presented.get("prs") or []),
        "lanes": sorted((presented.get("lanes") or {}).keys()),
        "reason": None if released else (reason or "本番のプランが完了しなかった"),
    }
    put_json(Path(d) / "results" / f"{seq}.json", data)
    return data


# ---- 形の検査（契約 `contracts/forms/parallel-external.json` の例の組） ----

_INTS = lambda v: isinstance(v, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in v)  # noqa: E731


def _request_errors(name: str, body: dict) -> list[str]:
    errs = prf.request_errors(body)
    if not isinstance(body.get("ndf_version"), str) or not body.get("ndf_version"):
        errs.append("ndf_version が空でない文字列でない")
    if name != f"{body.get('id')}.json":
        errs.append("ファイル名が依頼の id でない")
    return errs


def _answer_errors(body: dict) -> list[str]:
    errs: list[str] = []
    if body.get("result") not in ("released", "rejected"):
        errs.append("result が released / rejected でない")
    if not isinstance(body.get("at"), str):
        errs.append("at が文字列でない")
    return errs


def _batch_errors(body: dict) -> list[str]:
    errs: list[str] = []
    if body.get("step") not in STEPS or not isinstance(body.get("seq"), int):
        errs.append("step か seq の形が違う")
    for ln in body.get("lanes") or []:
        if (
            not isinstance(ln.get("lane"), str)
            or not _INTS(ln.get("issues"))
            or not all(_INTS(r.get("prs")) for r in ln.get("requests") or [])
        ):
            errs.append("lanes の形が違う")
    return errs


def file_errors(message: object) -> list[str]:
    """置き場のファイル 1 つ（{file, body}）の形の誤り。空なら形に合う。"""
    if not isinstance(message, dict) or not isinstance(message.get("body"), dict) or not isinstance(message.get("file"), str):
        return ["{file, body} の形でない"]
    path, body = message["file"], message["body"]
    top, _, name = path.partition("/")
    errs: list[str] = []
    if top == "requests":
        return _request_errors(name, body)
    if top == "answers":
        errs.extend(_answer_errors(body))
    elif top == "ranges":
        if not _INTS(body.get("prs")) or not isinstance(body.get("lanes"), list):
            errs.append("prs が整数の配列でないか lanes が配列でない")
    elif path == "batch.json":
        errs.extend(_batch_errors(body))
    elif top == "presented":
        if body.get("k") not in (1, 2) or not isinstance(body.get("auto"), bool) or not _INTS(body.get("prs")):
            errs.append("k・auto・prs の形が違う")
    elif top == "progress":
        if not isinstance(body.get("stages"), list) or any(s not in STAGES for s in body["stages"]):
            errs.append("stages に知らない段がある")
    elif top == "results":
        if body.get("result") not in ("released", "failed") or not _INTS(body.get("prs")):
            errs.append("result か prs の形が違う")
    else:
        errs.append(f"知らないファイル: {path}")
    return errs
