"""ステップの上限をプランの遷移から求める（#1909 #1676）。ファイルもプロセスも触らない純粋な計算。

`上限 = D + FIX_ROUNDS × Σ 修正のループ + FIX_ROUNDS × Σ 戻りの輪`。
- D（ループを通らない最長の経路）: すべての遷移のうち、プランの並びで後ろのステップへ行くものだけをたどった
  最長の経路のステップの数。流し始めるステップを含む
- 修正のループ: 検査のステップ `c` の失敗の遷移 `c → f` 1 本ごとに 1 つ。長さは `f` から成功の遷移だけを通って
  `c` へ戻る単純な輪のうち、修正の work を 1 つ以上含む最も長いもののステップの数
- 戻りの輪: 修正の work を含む輪が無く、`kind` が「戻り」のステップを含む輪（数え方は修正のループと同じ）

書き出し（`supervise.py new` / `new sprint`）が `fill` でプランの `上限` を埋め、`run` は流す前に `run_error` で
`上限` と D を照らす。手で書いたプランの `上限` は書き換えない。
"""

from __future__ import annotations

import copy

from supervise_lib.plan import expand_parts

FIX_ROUNDS = 3  # 1 本の実行で修正のループのそれぞれを通る想定の回数（#1909 の前提 1）
DEFAULT_LIMIT = 30  # `上限` の無いプランの既定（前提 3）
SEARCH_CAP = 100_000  # 1 つのループの輪の探索で展開する回数の上限。超えたら多めに見積もる
END = ("end", "stop")  # judge 以外の遷移の終わり
JUDGE_END = ("gate", "stop", "next", "end")  # judge の choices のうちステップの id でない値
RETURN_KIND = "戻り"


class LimitError(ValueError):
    """遷移の行き先が知らないステップ。"""


def _steps(plan: dict) -> list[dict]:
    """`parts` を展開した後のステップ（Engine が流す形）。元のプランは書き換えない。"""
    return expand_parts(copy.deepcopy(plan.get("steps") or []))


def default_next(steps: list[dict], sid: str) -> str | None:
    """成功したときの次のステップ。`next` が無ければ並びの次へ進むが、失敗したときにだけ通るステップ
    （どこかの `on_fail` が指すステップと、そこから `next` で戻るステップ）と、`--from` でだけ入るステップ
    （`from_only`。差し戻しの後の reject）は飛ばす。終わりなら None。"""
    by = {s["id"]: s for s in steps}
    step = by[sid]
    if step.get("next"):
        return None if step["next"] == "end" else step["next"]
    fail_only = {s["on_fail"] for s in steps if s.get("on_fail")} | {s["id"] for s in steps if s.get("from_only")}
    fail_only |= {
        s["id"]
        for s in steps
        if s["type"] == "work" and s.get("next") in by and s.get("inputs") and any(by.get(i, {}).get("on_fail") for i in s["inputs"])
    }
    order = [s["id"] for s in steps]
    i = order.index(sid) + 1
    while i < len(order) and order[i] in fail_only:
        i += 1
    return order[i] if i < len(order) else None


def _judge_targets(steps: list[dict], step: dict) -> list[tuple[str, str, bool]]:
    """judge のステップの遷移。`choices` の各値と、`choices` が無いときの並びの次。"""
    out: list[tuple[str, str, bool]] = []
    for c in step.get("choices") or []:
        if c == "next":
            nxt = default_next(steps, step["id"])
            if nxt:
                out.append(("choices", nxt, True))
        elif c not in JUDGE_END:
            out.append(("choices", str(c), True))
    if not step.get("choices"):
        nxt = default_next(steps, step["id"])
        if nxt:
            out.append(("next", nxt, True))
    return out


def _edge_targets(steps: list[dict], step: dict) -> list[tuple[str, str, bool]]:
    """judge 以外のステップの遷移（next / skip_to / gate_next / on_fail / on_section_missing / on_exit）。"""
    out: list[tuple[str, str, bool]] = []
    nxt = default_next(steps, step["id"])
    if nxt:
        out.append(("next", nxt, True))
    for key in ("skip_to", "gate_next"):
        if step.get(key) and step[key] not in END:
            out.append((key, step[key], True))
    for key in ("on_fail", "on_section_missing"):
        if step.get(key) and step[key] not in END:
            out.append((key, step[key], False))
    table = step.get("on_exit")
    if isinstance(table, dict):
        for code, dest in table.items():
            if dest not in END:
                out.append(("on_exit", str(dest), str(code) == "0"))
    return out


def _targets(steps: list[dict], step: dict) -> list[tuple[str, str, bool]]:
    """ステップの遷移の (鍵, 行き先, 成功か)。終わりの値は含めない。"""
    if step.get("type") == "judge":
        return _judge_targets(steps, step)
    return _edge_targets(steps, step)


def unknown_targets(plan: dict) -> list[tuple[str, str, str]]:
    """遷移の行き先が知らないステップの (ステップ, 鍵, 行き先) の一覧。"""
    steps = _steps(plan)
    ids = {s.get("id") for s in steps}
    return [(str(s.get("id")), key, dest) for s in steps for key, dest, _ in _targets(steps, s) if dest not in ids]


def _graph(steps: list[dict]) -> dict[str, list[tuple[str, bool]]]:
    ids = {s["id"] for s in steps}
    return {s["id"]: [(d, ok) for _, d, ok in _targets(steps, s) if d in ids] for s in steps}


def longest_path(plan: dict, start: str | None = None) -> int:
    """ループを通らない最長の経路（並びで後ろへ行く遷移だけ）のステップの数。start が無ければ最初のステップから。"""
    steps = _steps(plan)
    if not steps:
        return 0
    order = [s["id"] for s in steps]
    start = start or order[0]
    if start not in order:
        return 0
    pos = {sid: i for i, sid in enumerate(order)}
    graph = _graph(steps)
    best: dict[str, int] = {}
    for sid in reversed(order):
        best[sid] = 1 + max((best[d] for d, _ in graph[sid] if pos[d] > pos[sid]), default=0)
    return best[start]


def _is_fix(step: dict) -> bool:
    return step.get("type") == "work" and step.get("kind", "修正") == "修正"


def _loop_length(steps: list[dict], check: str, first: str, want) -> int:
    """`check → first` の失敗の遷移から成功の遷移だけで `check` へ戻る単純な輪のうち、`want` に当たるステップを
    含む最も長いもののステップの数。無ければ 0。探索が SEARCH_CAP を超えたら、first から行けて check へ戻れる
    ステップの数で代える。"""
    by = {s["id"]: s for s in steps}
    succ = {sid: [d for d, ok in edges if ok] for sid, edges in _graph(steps).items()}
    # `back_to_failed` の work は、直した後にこのループの検査のステップへ戻る
    for s in steps:
        if s.get("back_to_failed") and check not in succ[s["id"]]:
            succ[s["id"]] = [*succ[s["id"]], check]
    best, expanded = 0, 0
    stack = [(first, (first,), want(by[first]))]
    while stack:
        sid, path, hit = stack.pop()
        expanded += 1
        if expanded > SEARCH_CAP:
            return _reachable_loop(succ, check, first)
        for d in succ[sid]:
            if d == check:
                if hit:
                    best = max(best, len(path) + 1)
            elif d not in path:
                stack.append((d, (*path, d), hit or want(by[d])))
    return best


def _reachable_loop(succ: dict[str, list[str]], check: str, first: str) -> int:
    """first から行けて check へ戻れるステップの数（check を含む）。輪の探索が打ち切られたときの多めの見積もり。"""
    fwd, todo = {first}, [first]
    while todo:
        for d in succ[todo.pop()]:
            if d not in fwd and d != check:
                fwd.add(d)
                todo.append(d)
    back = {check}
    changed = True
    while changed:
        changed = False
        for sid in fwd:
            if sid not in back and any(d in back for d in succ[sid]):
                back.add(sid)
                changed = True
    return len(fwd & back) + 1


def loops(plan: dict) -> tuple[dict[str, int], dict[str, int]]:
    """(修正のループ, 戻りの輪)。鍵は `<検査のステップ>→<失敗の行き先>`、値は輪の長さ。"""
    steps = _steps(plan)
    fixes: dict[str, int] = {}
    returns: dict[str, int] = {}
    for check, edges in _graph(steps).items():
        for first, ok in edges:
            if ok:
                continue
            key = f"{check}→{first}"
            n = _loop_length(steps, check, first, _is_fix)
            if n:
                fixes[key] = n
            elif n := _loop_length(steps, check, first, lambda s: s.get("kind") == RETURN_KIND):
                returns[key] = n
    return fixes, returns


def step_limit(plan: dict) -> int:
    """雛形が書く `上限`: ループを通らない最長の経路に、修正のループと戻りの輪のそれぞれを FIX_ROUNDS 回ずつ足した長さ。"""
    unknown = unknown_targets(plan)
    if unknown:
        sid, key, dest = unknown[0]
        raise LimitError(f"プランの遷移の行き先が知らないステップ: {sid} の {key} → {dest}。プランを書かない")
    fixes, returns = loops(plan)
    return longest_path(plan) + FIX_ROUNDS * (sum(fixes.values()) + sum(returns.values()))


def fill(plan: dict) -> dict:
    """プランの `上限` を遷移から求めて埋める（`steps` の直前に置く）。知らない行き先なら LimitError。"""
    limit = step_limit(plan)
    plan.pop("上限", None)
    items = list(plan.items())
    plan.clear()
    for k, v in items:
        if k == "steps":
            plan["上限"] = limit
        plan[k] = v
    plan.setdefault("上限", limit)
    return plan


def fill_all(plans) -> str | None:
    """プランをすべて埋める。どれかが知らない行き先を持てば、どれも埋めずに誤りの文を返す（無ければ None）。"""
    plans = list(plans)
    for p in plans:
        try:
            step_limit(p)
        except LimitError as e:
            return str(e)
    for p in plans:
        fill(p)
    return None


def run_error(plan: dict, start: str | None = None) -> str | None:
    """流す前の照合。知らない行き先か、`上限` がループを通らない最長の経路より短ければ理由を返す（無ければ None）。"""
    unknown = unknown_targets(plan)
    if unknown:
        sid, key, dest = unknown[0]
        return f"ステップ {sid} の {key} の行き先が知らないステップ: {dest}"
    steps = _steps(plan)
    first = start or (steps[0]["id"] if steps else None)
    if first not in {s["id"] for s in steps}:
        return None  # 知らない開始のステップは流す側が「知らないステップ」で止める
    limit = plan.get("上限", DEFAULT_LIMIT)
    d = longest_path(plan, first)
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit < d:
        return f"上限 {limit} がループを通らない最長の経路 {d} 本（{first} から）より短い。上限を上げるか supervise.py new で書き直す"
    return None
