"""工程の担当へ渡す指示文。純粋な関数だけを置く（引数は文面に要る値だけ）。

| 関数 | 工程 |
| --- | --- |
| `propose` | 提案（課題が触る関数だけを提案させる） |
| `plan` | 計画（採る項目を選んで並べ、グレードと見積りを付けさせる） |
| `add_tests` | テストの追加（直す前の振る舞いを固定させる） |
| `implement` | 実装 |
| `fix` | 検証で落ちた検査とテストの修正 |
| `judge` | 修正が停滞か振動した項目の成立の判定 |
| `flaky` | フレーキーなテストの直し |
| `final_fix` | 最終ゲートの CI で落ちたテストの直し |
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ndf.domains.crossrefactor import flaky_decide
from ndf.domains.crossrefactor.symbols_decide import Touched

# コードを書く工程の結果の形の指示（前置きの改行・空白は呼び出し側が付ける）
COMMIT_RESULT = '結果は {"commit": "<最後のコミット>"}。'


def item_text(item: Mapping[str, Any]) -> str:
    return f"{item['id']}（{item['path']} の {item['symbol']}）: {item['title']}"


def propose(pr: int, touched: Touched) -> str:
    symbols = "\n".join(f"- {p}: {', '.join(sorted(n))}" for p, n in touched.symbols.items())
    return (
        f"PR #{pr} が触る次の関数・クラスについて、振る舞いを変えないリファクタリングを提案する。ほかの関数は提案しない。\n"
        f"{symbols}\n"
        '最後の行に JSON {"proposals": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>"}]} を出す。'
    )


def plan(pr: int, proposals: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"- {p['path']} {p['symbol']}: {p['title']}（{p['reason']}）" for p in proposals]
    return (
        f"PR #{pr} のリファクタリングの提案から、採る項目を選んで並べる（重なるものは 1 つにする）。\n"
        + "\n".join(lines)
        + "\n項目ごとにグレード（tier: high・medium・low）と見積り（estimate: テストの追加と実装の分）を付ける。"
        + '\n最後の行に JSON {"items": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>",'
        + ' "tier": "<グレード>", "estimate": {"test": <分>, "implement": <分>}}]} を出す。'
    )


def add_tests(item: Mapping[str, Any]) -> str:
    return (
        f"{item_text(item)} を直す前に、今の振る舞いを固定するテストを足してコミットする（push しない）。"
        ' 結果は {"tests": ["<足したテストのファイル>"]}。'
    )


def implement(item: Mapping[str, Any]) -> str:
    return f"{item_text(item)} を、振る舞いを変えずに直してコミットする（push しない）。ほかの関数は変えない。 " + COMMIT_RESULT


def fix(item: Mapping[str, Any], own: Iterable[str]) -> str:
    judged = item["feasibility"] if "feasibility" in item else []
    hint = "".join(f"\n成立の判定（{j['by']}）: {j['reason']} {j['evidence']}" for j in judged if j["feasible"])
    return (
        f"{item_text(item)} の変更で、次の検査かテストが落ちた（<suite>:<ファイル>）。この項目の変更の中で直してコミットする。\n"
        + "\n".join(f"- {k}" for k in sorted(own))
        + hint
        + "\n"
        + COMMIT_RESULT
    )


def judge(item: Mapping[str, Any], own: Iterable[str]) -> str:
    return (
        f"{item_text(item)} の修正が停滞か振動した。次の失敗を、項目の変更（振る舞いを変えないリファクタリング）の中で"
        "直せるかを判定する。\n" + "\n".join(f"- {k}" for k in sorted(own))
        + '\n最後の行に JSON {"items": [{"item": "' + item["id"] + '", "feasible": <true|false>, "reason": "<理由>", "evidence": "<根拠>"}]} を出す。'
    )  # fmt: skip


def flaky(pending: Mapping[str, Any]) -> str:
    back = "".join(f"\n前回の違反: {v['commit']} {v['detail']}" for v in pending["sendback"])
    return (
        "次のテストは打ち直すと通るフレーキーなテストである（<suite>:<ファイル>）。テストのファイルだけを直し、"
        f"鍵ごとに 1 コミットにしてトレーラー `{flaky_decide.TRAILER}: <鍵>` を付ける（push しない）。\n"
        + "\n".join(f"- {k}" for k in pending["keys"])
        + back
        + "\n"
        + COMMIT_RESULT
    )


def final_fix(pr: int, scope: Sequence[str], failed: Sequence[str]) -> str:
    return (
        f"PR #{pr} の CI で次のテストが落ちた。リファクタリング計画の対象範囲（{', '.join(scope)}）の中だけを変えて直し、"
        "コミットする（push しない）。\n" + "\n".join(f"- {f}" for f in failed) + "\n" + COMMIT_RESULT
    )
