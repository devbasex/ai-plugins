#!/usr/bin/env python3
"""Jev（TypeSafe AI の判断専用モデル）へ 1 問ずつ問う（#933 / #841）。

**失敗しても進行を止めない。** 呼び出しが失敗したら `None` を返し、呼び出し側が
実装担当の答えで決める。鍵は環境変数 `AI_GATEWAY_API_KEY` からだけ読み、引数で
受け取らない（状態ファイル・報告・ログへ値が流れる経路を作らない）。

経路は Vercel AI Gateway の `POST /v1/evaluate`。要求と応答の形は 2026-09-24 に実測した。

```json
{"model": "typesafe-ai/jev", "state": "<判断の材料>",
 "questions": {"q": {"type": "boolean", "instructions": "..."}}}
→ {"answers": {"q": {"type": "boolean", "probability": 0.99}}}

{"questions": {"q": {"type": "score", "criteria": ["low", "medium", "high"], "instructions": "..."}}}
→ {"answers": {"q": {"type": "score", "probabilities": {"0": 0, "1": 0.29, "2": 0.71}, "confidence": 0.55}}}
```

**送るのは呼び出し側が組み立てた文だけである。** 差分の本文・ファイルの本文・テストの
出力を入れない規則は呼び出し側（cross-refactoring の `commands/plan.py` と `converge.py`）が守る。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Optional

ENDPOINT = "https://ai-gateway.vercel.sh/v1/evaluate"
MODEL = "typesafe-ai/jev"
KEY_ENV = "AI_GATEWAY_API_KEY"
# `0` で無効にする。鍵がある環境でも、使わない実行を選べるようにする。
SWITCH_ENV = "NDF_JEV"
# 疎通の確認の上限（秒）。設計の「init の疎通の確認（固定の boolean の問い 1 回、10 秒）」。
PROBE_TIMEOUT = 10
# 1 問の上限（秒）。応答は実測で 0.3〜0.5 秒。
ASK_TIMEOUT = 20

# 使わなかった理由（状態ファイルの `judge.reason`）。
NO_KEY = "no_key"
DISABLED = "disabled"
PRIVATE_REPO = "private_repo"
PROBE_FAILED = "probe_failed"

Post = Callable[[dict[str, Any], int], Optional[dict[str, Any]]]


def _post(body: dict[str, Any], timeout: int) -> Optional[dict[str, Any]]:
    """要求を 1 回送り、応答の JSON を返す。失敗は `None`（例外を外へ出さない）。"""
    key = os.environ.get(KEY_ENV, "")
    if not key:
        return None
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return payload if isinstance(payload, dict) else None


def _answer(payload: Optional[dict[str, Any]], name: str) -> Optional[dict[str, Any]]:
    if not payload:
        return None
    answer = (payload.get("answers") or {}).get(name)
    return answer if isinstance(answer, dict) else None


def ask_boolean(
    state: str, instructions: str, *, post: Post = _post, timeout: int = ASK_TIMEOUT,
) -> Optional[tuple[bool, float]]:
    """真偽の問い。`(答え, 確信度)` を返す。確信度は答えの側の確率。失敗は `None`。"""
    body = {"model": MODEL, "state": state,
            "questions": {"q": {"type": "boolean", "instructions": instructions}}}
    answer = _answer(post(body, timeout), "q")
    probability = (answer or {}).get("probability")
    if not isinstance(probability, (int, float)) or isinstance(probability, bool):
        return None
    value = float(probability) >= 0.5
    return value, float(probability) if value else 1.0 - float(probability)


def ask_score(
    state: str, instructions: str, criteria: list[str], *,
    post: Post = _post, timeout: int = ASK_TIMEOUT,
) -> Optional[tuple[str, float]]:
    """段階の問い。`(選ばれた段, 確信度)` を返す。失敗は `None`。

    段は `probabilities` の最大の位置で決める（`score` は位置の期待値で、段の名前へ
    そのまま戻せない）。確信度は応答の `confidence` を使い、無ければ最大の確率を使う。
    """
    body = {"model": MODEL, "state": state,
            "questions": {"q": {"type": "score", "criteria": list(criteria),
                                "instructions": instructions}}}
    answer = _answer(post(body, timeout), "q")
    probabilities = (answer or {}).get("probabilities")
    if not isinstance(probabilities, dict) or not probabilities:
        return None
    best: Optional[tuple[int, float]] = None
    for position, probability in probabilities.items():
        try:
            index, value = int(position), float(probability)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(criteria) and (best is None or value > best[1]):
            best = (index, value)
    if best is None:
        return None
    confidence = answer.get("confidence") if answer else None
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = best[1]
    return criteria[best[0]], float(confidence)


def probe(*, post: Post = _post) -> bool:
    """疎通の確認。固定の真偽の問いを 1 回送り、答えが返れば真。"""
    result = ask_boolean(
        "The sky is blue today.", "Is the sky described as blue?",
        post=post, timeout=PROBE_TIMEOUT,
    )
    return result is not None


def decide(
    is_public: Callable[[], Optional[bool]],
    env: Optional[Mapping[str, str]] = None,
    *, post: Post = _post,
) -> dict[str, Any]:
    """この実行で Jev を使うかを 1 度だけ決め、状態ファイルの `judge` の形で返す。

    判定の順は「鍵 → 無効の指定 → 公開か → 疎通」。安い判定から行い、外へ出る問い
    （公開か・疎通）は前の条件を満たしたときだけ行う。公開かを判定できないときは
    非公開として扱う（送ってよいと確かめられない中身を外へ出さない）。
    """
    env = os.environ if env is None else env
    if not env.get(KEY_ENV):
        return {"kind": "runtime", "reason": NO_KEY, "failures": 0}
    if env.get(SWITCH_ENV) == "0":
        return {"kind": "runtime", "reason": DISABLED, "failures": 0}
    if is_public() is not True:
        return {"kind": "runtime", "reason": PRIVATE_REPO, "failures": 0}
    if not probe(post=post):
        return {"kind": "runtime", "reason": PROBE_FAILED, "failures": 0}
    return {"kind": "jev", "reason": None, "failures": 0}
