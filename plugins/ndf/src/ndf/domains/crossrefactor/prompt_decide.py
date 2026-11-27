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
from typing import TYPE_CHECKING, Any

from ndf.domains.crossrefactor import budget_decide, flaky_decide
from ndf.domains.crossrefactor.symbols_decide import Touched
from ndf.domains.records.mvv_decide import State

if TYPE_CHECKING:
    from ndf.domains.records.mvv import Snapshot

# コードを書く工程の結果の形の指示（前置きの改行・空白は呼び出し側が付ける）
COMMIT_RESULT = '結果は {"commit": "<最後のコミット>"}。'


def item_text(item: Mapping[str, Any]) -> str:
    return f"{item['id']}（{item['path']} の {item['symbol']}）: {item['title']}"


def mvv_text(snap: Snapshot) -> str:
    """提案と計画の指示文に足す、承認済みのプロジェクト MVV を読ませる文。承認済みの MVV が無ければ空文字列。"""
    if snap.state != State.APPROVED:
        return ""
    return (
        "このリポジトリには、承認済みのプロジェクト MVV がある。プロジェクトの使命（Mission）・目指す姿（Vision）・判断で大切にすること"
        f"（Value）と、必ず人の承認が要る操作を書いた文書である（バージョン {snap.version}）。作業の前に次の 2 つのファイルの全体を読み、"
        "判断の基準にする。どちらかに反するリファクタリングは、提案も採用もしない。2 つとも作業ディレクトリの外にあるが、読んでよい。\n"
        f"- 本文: {snap.path}\n"
        f"- 共通原則（どのプロジェクトにも当てはまり、本文より優先する原則）: {snap.principles_path}\n"
    )


def propose(pr: int, touched: Touched, candidates: str = "", mvv: str = "") -> str:
    """提案の指示文。`candidates` は実装計画が挙げたスプリントの終わりの候補、`mvv` は `mvv_text` の文（空ならどちらも載せない）。"""
    lines = [f"- {p}: {', '.join(sorted(n))}" for p, n in touched.symbols.items()]
    # 構文木の読み手の無い言語のファイルは、ファイル単位で提案を通す（`symbols_decide.in_touched`）
    lines += [f"- {p}: （ファイル全体。関数の範囲を読めないため、ファイルの中のどの関数・クラスも提案できる）" for p in touched.whole_files]
    symbols = "\n".join(lines)
    # 末尾に改行の無いファイルでも、候補と結果の形の指示を同じ行に繋げない
    listed = candidates.rstrip("\n")
    planned = f"実装計画が挙げた候補（採るかは他の提案と同じ基準で判断する）:\n{listed}\n" if candidates else ""
    return (
        f"PR #{pr} が触る次の関数・クラスについて、振る舞いを変えないリファクタリングを提案する。ほかの関数は提案しない。\n"
        f"{symbols}\n"
        f"{planned}{mvv}"
        '最後の行に JSON {"proposals": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>"}]} を出す。'
    )


def _tiers() -> str:
    """計画の担当へ示すグレードの並び（予算の選び方が読む `budget_decide.TIER_ORDER` の順）。"""
    return "・".join(budget_decide.TIER_ORDER)


def plan(pr: int, proposals: Sequence[Mapping[str, Any]], mvv: str = "") -> str:
    """計画の指示文。`mvv` は `mvv_text` の文（空なら載せない）。"""
    lines = [f"- {p['path']} {p['symbol']}: {p['title']}（{p['reason']}）" for p in proposals]
    return (
        f"PR #{pr} のリファクタリングの提案から、採る項目を選んで並べる（重なるものは 1 つにする）。\n"
        + "\n".join(lines)
        + f"\n項目ごとにグレード（tier: {_tiers()}）と、手法（technique: extract_function などの snake_case の識別子）と、"
        + "直す前に振る舞いを固定するテストを足すか（tests: true / false）を付ける。所要の分は書かない（配分の表から見積もる）。\n"
        + mvv
        + '最後の行に JSON {"items": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>",'
        + ' "tier": "<グレード>", "technique": "<手法>", "tests": <真偽>}]} を出す。'
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
        f"キーごとに 1 コミットにしてトレーラー `{flaky_decide.TRAILER}: <キー>` を付ける（push しない）。\n"
        + "\n".join(f"- {k}" for k in pending["keys"])
        + back
        + "\n"
        + COMMIT_RESULT
    )


# 手元の経路の指示文全体の上限（UTF-8 のバイト）。Linux が引数 1 つに許す 131,072 バイトから、起動の側が足す文の余裕を引いた値（#4566）
PROMPT_BYTES = 60_000


def final_fix(pr: int, scope: Sequence[str], failed: Sequence[str], *, local: bool = False) -> str:
    """最終ゲート修正の指示文。`local` なら `failed` は `local_gate.fix_excerpt` の抜粋で、全体を `PROMPT_BYTES` 以下にする。"""
    if not local:
        return (
            f"PR #{pr} の CI で次のテストが落ちた。リファクタリング計画の対象範囲（{', '.join(scope)}）の中だけを変えて直し、"
            "コミットする（push しない）。\n" + "\n".join(f"- {f}" for f in failed) + "\n" + COMMIT_RESULT
        )
    head = (
        f"PR #{pr} の変更を手元でテストしたところ、次のテストの suite が落ちた。これらの suite は、取得した PR のブランチの先頭"
        "（この実行の変更を載せる前）では合格した。原因はこの実行の変更と確定していない（非決定的なテストや外部の状態の変化でも"
        f"同じ結果になる）。リファクタリング計画の対象範囲（{', '.join(scope)}）の中だけを変えて直し、コミットする（push しない）。\n"
    )
    tail = "\n" + COMMIT_RESULT

    def size(text: str) -> int:
        return len(text.encode("utf-8"))

    body: list[str] = []
    for i, block in enumerate(failed):
        rest = len(failed) - i - 1
        note = f"\nほかに {rest} 件が失敗した（指示文の上限で載せていない）" if rest else ""
        if size(head + "\n\n".join([*body, block]) + note + tail) > PROMPT_BYTES:
            dropped = len(failed) - i
            return head + "\n\n".join(body) + f"\nほかに {dropped} 件が失敗した（指示文の上限で載せていない）" + tail
        body.append(block)
    return head + "\n\n".join(body) + tail
