#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-refactoring の状態管理 CLI。

`<work>/.cross_refactoring/cross-refactoring-rf<ID>-state.json` の初期化・読み書きと、
二段の収束判定（提案ラウンドの繰り返しの中にレビュー収束の繰り返しが入る）を
1 つの CLI に集約する。

サブコマンド:
  init             Step 0  ホスト確定 / 母集合の確定 / 作業ディレクトリ root / 状態初期化
  start-round      Step 2  提案ラウンドを開く。実装担当とレビュー担当を返す
  merge-proposals  Step 3  提案の語彙検証・重複排除・優先度付け・採否
  merge-apply      Step 4  適用結果の検証（差分予算 / テスト / トレーラー / 固定テスト先行）
  judge-review     Step 5  レビュー 2 者の判定
  should-abandon   Step 6  修正ラウンド上限の到達判定
  abandon-items    Step 6  未解決の指摘に紐づく項目だけを取り消す
  merge-fix        Step 6  修正結果の取り込み
  advance          Step 7  提案ラウンドの収束判定
  status                   現在の状態を人が読む形で出す
  report           Step 8  ラウンド表・項目表・見送り・指標

終了コードは呼び出し側の bash が分岐に使う。各サブコマンドの docstring を参照。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
from typing import Any, Iterable, Optional

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent.parent
        / "cross-review" / "scripts" / "lib"),
)

import assignment  # noqa: E402
import metrics as metrics_lib  # noqa: E402
import models as models_lib  # noqa: E402
import statefile  # noqa: E402

info = statefile.info

# 中断の終了コード。**「全件失敗」（2）と区別する。** 進行スクリプトは 2 なら次の
# 提案ラウンドへ進み、4 なら進行そのものを止める。区別しないと、取り消しに失敗した
# 状態を「全件失敗」として握り潰し、**検証を通っていない変更を Pull Request に
# 残したまま**次の提案が始まる（実測）。
ABORT = 4


def die(msg: str, code: int = ABORT) -> None:
    """中断して終了する。既定は「中断」を表す終了コード。"""
    statefile.die(msg, code)


# ---------------- 語彙 ----------------
# スメルと手法の語彙は `refactoring` Skill の references と 1 対 1 で対応させる。
# **語彙を固定しないと重複排除が効かない**（同じ箇所への提案が別物として残る）。

SMELLS: dict[str, str] = {
    "long_method": "長すぎるメソッド",
    "large_class": "肥大したクラス",
    "duplication": "重複",
    "long_parameter_list": "長い引数リスト",
    "feature_envy": "他クラスへの過度な関心",
    "primitive_obsession": "基本型への固執",
    "magic_value": "マジックナンバー・文字列",
    "deep_nesting": "深いネスト",
    "dead_code": "デッドコード",
    "circular_dependency": "過度な相互依存",
    "inconsistent_naming": "一貫しない命名",
    "swallowed_exception": "例外の飲み込み",
    "conditional_chain": "条件分岐の連鎖",
    "scattered_config": "設定の散在",
    "embedded_business_rule": "業務ルールの埋め込み",
    "one_by_one_iteration": "一件ずつの反復",
    "unvalidated_externalization": "検証のない外部化",
}

TECHNIQUES: dict[str, str] = {
    "extract_method": "メソッドの抽出",
    "rename": "変数・関数・クラスの改名",
    "introduce_parameter_object": "引数オブジェクトの導入",
    "introduce_value_object": "値オブジェクトの導入",
    "flatten_conditional": "条件分岐の平坦化",
    "replace_conditional_with_polymorphism": "多態による分岐の置き換え",
    "replace_with_lookup_table": "対応表への置き換え",
    "replace_with_bulk_operation": "一括処理への置き換え",
    "extract_strategy": "戦略の切り出し",
    "move_responsibility": "責務の移動",
    "fix_dependency_direction": "依存の向きを整える",
    "split_into_pipeline": "処理の連鎖への分解",
    "remove_dead_code": "死んだコードの削除",
    "consolidate_duplication": "重複の共通化",
    "introduce_named_constant": "名前付き定数・列挙の導入",
    "propagate_exception": "呼び出し元へ伝える",
    "centralize_configuration": "定義を 1 箇所へ寄せる",
    "validate_at_boundary": "スキーマと版を与え、読み込み境界で検証する",
}

# 重要度。語彙外の提案は `unknown` へ降格し、しきい値で自動的に落ちるようにする。
SEVERITY_ORDER = {"unknown": 0, "minor": 1, "major": 2, "critical": 3}
DEFAULT_SEVERITY_THRESHOLD = "minor"

# 提案が名乗ってよい重要度。`unknown` は降格先なので含めない。
SEVERITIES: tuple[str, ...] = tuple(s for s in SEVERITY_ORDER if s != "unknown")


def vocabulary() -> dict[str, Any]:
    """提案プロンプトへ**そのまま列挙する**ための語彙集合。

    手順書の見出しは日本語なので、「語彙に限定する」とだけ書くと読んだ側が
    日本語を語彙と解釈する（実測では gemini の提案 4 件が全て日本語で返り、
    語彙外の降格規則により全件見送りになった）。**検証側が持つ集合をそのまま
    渡す**ことで、許容値の定義を 1 箇所に保ったまま列挙できる。
    """
    return {
        "smells": dict(SMELLS),
        "techniques": dict(TECHNIQUES),
        "severities": list(SEVERITIES),
    }


# 適用と修正のコミットに必須のトレーラー。1 つでも欠けたら当該項目を失敗にする。
# 自由文で「codex が実装」と書かせると集計に使えないため、必ずトレーラー形式にする。
REQUIRED_TRAILERS = ("Item-Id", "Round", "Impl-Runtime", "Impl-Model")

# 適用で必ず配置する Skill。ここに無いものは配らない。
REQUIRED_SKILLS = ("refactoring", "tdd-cycle", "quality-gates")

# 生成物を同期したコミットのメッセージ。**どの改善項目にも属さない**ことが分かる形にする。
SYNC_COMMIT_MESSAGE = (
    "Chore: 生成物を同期する（cross-refactoring 進行側）\n\n"
    "実装担当は対象範囲だけを変更するため、生成物が同期されない。\n"
    "同期を検査する pre-push を持つリポジトリでも push できるよう、\n"
    "公開の直前に進行側がまとめて生成する。"
)

# 実差分行数が見積りのこの倍数を超えたら範囲の逸脱とみなす。
DIFF_BUDGET_FACTOR = 2

# テスト 1 回あたりの上限（秒）。生成されたコードやテストが無限ループに入ると、
# 待ち続けて**進行全体が止まる**。打ち切って失敗として扱う。
DEFAULT_TEST_TIMEOUT = 900

# 提案の重複率がこの割合を超えたら、提案ラウンドの繰り返しを収束とみなす。
DUPLICATE_RATE_THRESHOLD = 0.7

# レビュー結果の形式不正で差し戻せる回数。超えたら変更要求として扱う。
# 差し戻しを無限に繰り返すと、形式を満たせないランタイムでループが止まらなくなる。
MAX_INVALID_REVIEWS = 1

# 認証状態の確認コマンド。**CLI の存在確認だけでは足りない。** 未認証の CLI は
# 起動から 15 秒で終わり、結果ファイルを残さないまま担当から脱落する（実測）。
# それでも初期化は成功として扱われるため、参加者が 1 人欠けた構成のまま進行する。
AUTH_PROBES: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "auth", "status"),
    "codex": ("codex", "login", "status"),
    # gemini には認証確認の副コマンドが無い。最小のプロンプトで疎通を見る。
    # 作業ディレクトリの信頼判定に引っ掛からないよう `--skip-trust` を付ける。
    "gemini": ("gemini", "--skip-trust", "-p", "ping", "--output-format", "text"),
    "kiro": ("kiro-cli", "whoami"),
}
AUTH_PROBE_TIMEOUT = 120

# **終了コード 0 でも未認証を示すことがある。** kiro は成否を終了コードで表さない。
UNAUTHENTICATED_MARKERS = (
    "not logged in", "not authenticated", "authentication failed",
    "login required", "unauthorized", "please log in",
)


# ---------------- パス解決 ----------------

def _default_worktree_base() -> pathlib.Path:
    """作業ディレクトリの親。解決順は cross-review と揃える。

    1. 環境変数 `NDF_WORKTREE_BASE`（明示指定）
    2. `<システム tmpdir>/ndf-worktrees`（非永続領域。コンテナ再作成で自動消滅）
    """
    import tempfile
    env = os.environ.get("NDF_WORKTREE_BASE")
    if env:
        return pathlib.Path(env).resolve()
    return pathlib.Path(tempfile.gettempdir()) / "ndf-worktrees"


def _repo_slug(repo: str) -> str:
    return repo.replace("/", "--")


def _tmp_dir_for(work: pathlib.Path) -> pathlib.Path:
    """一時ディレクトリ。解決順は cross-review と同じ規約に揃える。

    1. 環境変数 `CROSS_REFACTORING_TMP_DIR`（明示指定）
    2. `<work>/.cross_refactoring/`
    """
    env = os.environ.get("CROSS_REFACTORING_TMP_DIR")
    return pathlib.Path(env).resolve() if env else work / ".cross_refactoring"


def _state_path(tmp_dir: pathlib.Path, state_id: int) -> pathlib.Path:
    return tmp_dir / f"cross-refactoring-rf{state_id}-state.json"


def _find_state(state_id: int) -> pathlib.Path:
    """状態ファイルを探す。見つからなければ終了する。

    環境変数が設定されていればそこを、無ければ現在の作業ディレクトリからの
    相対で探す。呼び出し側の bash は `init` の出力を `export` してから使う。
    """
    env = os.environ.get("CROSS_REFACTORING_TMP_DIR")
    candidates = []
    if env:
        candidates.append(pathlib.Path(env) / f"cross-refactoring-rf{state_id}-state.json")
    candidates.append(
        pathlib.Path.cwd() / ".cross_refactoring"
        / f"cross-refactoring-rf{state_id}-state.json"
    )
    for c in candidates:
        if c.exists():
            return c
    die(
        f"状態ファイルが見つかりません（rf{state_id}）。"
        "CROSS_REFACTORING_TMP_DIR を export してから実行してください"
    )
    raise SystemExit(1)  # die が抜けることはないが型のために置く


def _load(state_id: int) -> tuple[pathlib.Path, dict[str, Any]]:
    path = _find_state(state_id)
    return path, statefile.load(path)


def _sh(cmd: list[str], cwd: Optional[str] = None, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        die(f"コマンドが失敗しました ({' '.join(cmd)}): {r.stderr.strip()}")
    return r.stdout.strip()


def _result_path(state: dict[str, Any], runtime: str, stem: str) -> pathlib.Path:
    """CLI が結果を書き出すパス。

    gemini だけは作業領域の外への書き込みが拒否されるため、起動時に一時
    ディレクトリを作業領域へ追加している（`--include-directories`）。
    したがって置き場所は全ランタイムで共通でよい。
    """
    return pathlib.Path(state["tmp_dir"]) / f"{stem}-result.json"


def stem_for(runtime: str, phase: str, state_id: int, round_no: Optional[int] = None) -> str:
    """一時ファイル名の骨格。監視スクリプトの `--stem-template` と揃える。

    **提案にもラウンド番号を入れる。** CLI の起動時に同名の結果ファイルを消すため、
    番号が無いと 2 巡目の提案が始まった時点で 1 巡目の提案内容が失われる。
    統合後の採否は状態ファイルに残るが、**各ランタイムが何をどう提案したかは
    復元できなくなる**（実測）。
    """
    if phase == "propose":
        return f"{runtime}-propose-rf{state_id}-r{round_no}"
    return f"{runtime}-{phase}-r{round_no}"


# ---------------- 提案のマージ ----------------

def _normalize_proposal(raw: dict[str, Any], source: str) -> Optional[dict[str, Any]]:
    """1 件の提案を正規化する。必須項目を欠くものは捨てる。

    語彙外の `smell` / `technique` は `unknown` として警告し、**最低の重要度へ
    降格**させる。しきい値で自動的に落ちるため、語彙を守らない提案が
    重複排除をすり抜けて残ることがない。
    """
    path = str(raw.get("path") or "").strip()
    symbol = str(raw.get("symbol") or "").strip()
    if not path or not symbol:
        info(f"⚠ {source}: path / symbol の無い提案を無視しました: {raw!r:.120}")
        return None

    smell = str(raw.get("smell") or "").strip()
    technique = str(raw.get("technique") or "").strip()
    severity = str(raw.get("severity") or "").strip().lower()
    degraded = False
    if smell not in SMELLS:
        info(f"⚠ {source}: 語彙外のスメル `{smell}` — unknown へ降格 ({path}#{symbol})")
        smell = "unknown"
        degraded = True
    if technique not in TECHNIQUES:
        info(f"⚠ {source}: 語彙外の手法 `{technique}` — unknown へ降格 ({path}#{symbol})")
        technique = "unknown"
        degraded = True
    if severity not in SEVERITY_ORDER:
        info(f"⚠ {source}: 語彙外の重要度 `{severity}` — unknown へ降格 ({path}#{symbol})")
        severity = "unknown"
        degraded = True
    if degraded:
        severity = "unknown"

    estimated = _safe_int(raw.get("estimated_diff_lines"))

    return {
        "path": path,
        "symbol": symbol,
        "smell": smell,
        "technique": technique,
        "severity": severity,
        "rationale": str(raw.get("rationale") or "").strip(),
        "plan": str(raw.get("plan") or "").strip(),
        "test_gap": bool(raw.get("test_gap")),
        "estimated_diff_lines": max(estimated, 0),
        "proposed_by": [source],
    }


def _dedupe_key(item: dict[str, Any]) -> tuple[str, str, str]:
    """重複排除の鍵。同じ箇所への同じスメルの指摘を 1 件へまとめる。"""
    return (item["path"], item["symbol"], item["smell"])


def _merge_one(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """同一の鍵を持つ提案を統合する。

    `rationale` と `plan` は**最も具体的なもの**（長い方）を採る。重要度は高い方、
    推定差分行数は大きい方を採り、見積りを楽観側へ倒さない。
    """
    for source in incoming["proposed_by"]:
        if source not in existing["proposed_by"]:
            existing["proposed_by"].append(source)
    if len(incoming["rationale"]) > len(existing["rationale"]):
        existing["rationale"] = incoming["rationale"]
    if len(incoming["plan"]) > len(existing["plan"]):
        existing["plan"] = incoming["plan"]
    if SEVERITY_ORDER[incoming["severity"]] > SEVERITY_ORDER[existing["severity"]]:
        existing["severity"] = incoming["severity"]
        existing["technique"] = incoming["technique"]
    existing["test_gap"] = existing["test_gap"] or incoming["test_gap"]
    existing["estimated_diff_lines"] = max(
        existing["estimated_diff_lines"], incoming["estimated_diff_lines"]
    )


def merge_proposals(
    proposals: dict[str, list[dict[str, Any]]],
    threshold: str = DEFAULT_SEVERITY_THRESHOLD,
    max_items: int = 5,
    excluded_keys: Iterable[tuple[str, str, str]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """提案をマージして `(採用, 見送り)` を返す。

    優先度は「**合意したランタイム数 → 重要度 → 推定差分行数の昇順**」。
    小さく合意の多いものから直す。合意が多い提案は誤検知の確率が低く、
    小さい提案は失敗したときの取り消し範囲も小さい。

    `excluded_keys` には過去に見送った項目の鍵を渡す。見送った項目を毎ラウンド
    再提案されると収束しないため、対象外として落とす。
    """
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source, items in proposals.items():
        for raw in items:
            norm = _normalize_proposal(raw, source)
            if norm is None:
                continue
            key = _dedupe_key(norm)
            if key in merged:
                _merge_one(merged[key], norm)
            else:
                merged[key] = norm

    excluded = set(excluded_keys)
    adopted: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    min_severity = SEVERITY_ORDER.get(threshold, SEVERITY_ORDER[DEFAULT_SEVERITY_THRESHOLD])

    for key, item in merged.items():
        if key in excluded:
            item["defer_reason"] = "過去のラウンドで見送った項目のため対象外"
            deferred.append(item)
        elif SEVERITY_ORDER[item["severity"]] < min_severity:
            item["defer_reason"] = f"重要度 {item['severity']} がしきい値 {threshold} 未満"
            deferred.append(item)
        else:
            adopted.append(item)

    adopted.sort(
        key=lambda i: (
            -len(i["proposed_by"]),
            -SEVERITY_ORDER[i["severity"]],
            i["estimated_diff_lines"],
            i["path"],
            i["symbol"],
        )
    )
    if len(adopted) > max_items:
        for item in adopted[max_items:]:
            item["defer_reason"] = f"1 ラウンドの採用上限 {max_items} 件を超えた"
        deferred.extend(adopted[max_items:])
        adopted = adopted[:max_items]
    return adopted, deferred


def duplicate_rate(
    current: Iterable[tuple[str, str, str]], previous: Iterable[tuple[str, str, str]]
) -> float:
    """前ラウンドの提案とどれだけ重なっているか。前ラウンドが空なら 0 を返す。"""
    prev = set(previous)
    cur = set(current)
    if not prev or not cur:
        return 0.0
    return len(cur & prev) / len(cur)


# ---------------- 適用結果の検証 ----------------
#
# **結果ファイルの申告は検証の材料にしない。** 実装担当は自分の成果を報告する側なので、
# トレーラーもテスト結果も差分行数も、JSON の値を書き換えるだけで検査を通せてしまう。
# ここで使う事実（コミットの実在 / トレーラー / 差分行数 / テストの成否）は、すべて
# **git と実際のテスト実行**から取る。結果ファイルから使うのは「どのコミットが
# どの項目のものか」という対応付けの手がかりだけである。

def path_in_scope(path: str, scope: Iterable[str]) -> bool:
    """`path` が対象範囲の中にあるか。判定は**前方一致だけ**で行う。

    除外規則を足さない。規則を書けるようにすると、規則を 1 行足すだけで
    範囲の検査を骨抜きにできてしまう。

    突き合わせる前に `./` を落とす。シェルの補完で `--scope ./src` の形になることが
    多い一方、git が出すのは `src/foo.py` なので、**そのまま比べると全てのコミットが
    範囲外**になり、適用が必ず失敗する。`.` と `./` はリポジトリ全体を指す。
    """
    for entry in scope:
        raw = str(entry).strip()
        if not raw:
            continue
        prefix = raw
        while prefix.startswith("./"):
            prefix = prefix[2:]
        prefix = prefix.rstrip("/")
        if prefix in {"", "."}:
            return True
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def out_of_scope_files(commit: dict[str, Any], scope: Iterable[str]) -> list[str]:
    """コミットが触った**対象範囲の外**のファイル。範囲が空なら検査しない。"""
    paths = list(scope)
    if not paths:
        return []
    return sorted(
        p for p in (commit.get("files") or []) if not path_in_scope(p, paths)
    )


def verify_scope(commit: dict[str, Any], scope: Iterable[str]) -> Optional[str]:
    """対象範囲の外を触っていれば理由を返す。

    範囲を必須にした目的は**提案の発散と変更の肥大を防ぐ**ことなので、指定を
    検証に反映しないと目的を果たせない。実測では、生成物を同期する規約に従った
    結果として範囲外が 3 系統変更され、差分が 4 倍に膨らんで差分予算を超えた。
    生成物の同期が要る構成では、**同期は進行側の責務**として分離する。
    """
    outside = out_of_scope_files(commit, scope)
    if not outside:
        return None
    shown = ", ".join(outside[:5])
    more = f" ほか {len(outside) - 5} 件" if len(outside) > 5 else ""
    return (
        f"コミット {commit.get('sha', '?')} が対象範囲の外を変更しています"
        f"（{shown}{more}）。生成物の同期は進行側が公開の直前に行います。"
        "現状固定テストの置き場所が範囲外なら、`--scope` に含めてから実行してください"
    )


def verify_commit_trailers(commit: dict[str, Any]) -> Optional[str]:
    """コミットのトレーラーが 4 つ揃っているか。欠けていれば理由を返す。

    `commit` は **git から取った事実**（`collect_commit_facts()` の戻り値）である。
    結果ファイルの `trailers` を渡してはならない。
    """
    trailers = commit.get("trailers") or {}
    missing = [k for k in REQUIRED_TRAILERS if not str(trailers.get(k) or "").strip()]
    if missing:
        return f"コミット {commit.get('sha', '?')} にトレーラーが欠けています: {', '.join(missing)}"
    return None


def verify_fix_commit(
    commit: dict[str, Any], scope: Optional[Iterable[str]] = None
) -> Optional[str]:
    """修正コミットを適用と同じ基準で検証する。問題があれば理由を返す。

    適用側だけ厳しくして修正側を素通しにすると、**レビュー指摘への対応という
    名目で手順を外れた変更が入り、そのまま収束済みになる**。
    """
    if not commit.get("exists", True):
        return f"コミット {commit.get('sha', '?')} が対象の範囲に存在しません"
    problem = verify_commit_trailers(commit)
    if problem:
        return problem
    problem = verify_scope(commit, scope or [])
    if problem:
        return problem
    if commit.get("test_status") != "pass":
        return (
            f"コミット {commit.get('sha', '?')} でテストが成功していません "
            f"({commit.get('test_status')})"
        )
    return None


def verify_apply_item(
    item: dict[str, Any], facts: list[dict[str, Any]],
    scope: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """1 項目の適用結果を検証する。問題があれば失敗理由を返す。

    `facts` は `collect_commit_facts()` が git と実際のテスト実行から作る。
    振る舞い不変そのものは機械的に確かめられないが、**手順が守られたかは結果から
    確かめられる**。読ませ方の不確実性に対する最後の砦としてここを厚くする。
    """
    if not facts:
        return "コミットが 1 件もありません（1 手 1 コミットの前提を満たしていません）"

    for commit in facts:
        if not commit.get("exists", True):
            return (
                f"コミット {commit.get('sha', '?')} が base..head の範囲にありません"
                "（申告だけで実体がありません）"
            )
        problem = verify_commit_trailers(commit)
        if problem:
            return problem
        problem = verify_scope(commit, scope or [])
        if problem:
            return problem
        if commit.get("test_status") != "pass":
            return (
                f"コミット {commit.get('sha', '?')} でテストが成功していません "
                f"({commit.get('test_status')})"
            )
        item_id = (commit.get("trailers") or {}).get("Item-Id")
        if item_id != item["item_id"]:
            return (
                f"コミット {commit.get('sha', '?')} の Item-Id が {item_id} で、"
                f"項目 {item['item_id']} と一致しません"
                "（複数の項目を 1 コミットにまとめると取り消し範囲が決まりません）"
            )

    if item.get("test_gap"):
        # テストが乏しいと申告された項目は、現状固定テストの追加が先行していること。
        # 実測では同じ課題で固定テストの追加数が 17 本 / 1 メソッド / 0 本と揃わなかった。
        # 「テストを足した」かどうかは、そのコミットがテストの置き場所を触ったかで見る。
        if not facts[0].get("touches_tests"):
            return (
                "テストが乏しい項目なのに、現状固定テストの追加コミットが先行していません"
                f"（先頭コミット {facts[0].get('sha', '?')} がテストを触っていません）"
            )

    budget = _safe_int(item.get("estimated_diff_lines")) * DIFF_BUDGET_FACTOR
    actual = sum(int(c.get("diff_lines") or 0) for c in facts)
    if budget and actual > budget:
        return f"実差分 {actual} 行が差分予算 {budget} 行を超えました（範囲の逸脱）"
    return None


# ---------------- レビュー判定 ----------------

def judge(
    reviews: dict[str, dict[str, Any]], reviewers: list[str], round_items: list[str]
) -> tuple[str, list[str]]:
    """レビュー結果を判定し `(判定, 問題の一覧)` を返す。

    判定は `approved` / `changes` / `invalid` の 3 つ。
    `invalid` は差し戻して**再レビューさせる**もので、承認にも変更要求にもしない。

    指摘には改善項目 ID を必須とする。取り消しを項目単位で行うために必要で、
    そのラウンドに無い ID や欠落は判定に使えない。ラウンド全体に対する指摘は
    `null` を明示させ、取り消し時はラウンド全件の対象とする。
    """
    # 出力の形が崩れていても落ちないようにする。相手は LLM なので、
    # 期待した型で返ってこないことがある。崩れていたら差し戻す。
    problems: list[str] = []
    for name in reviewers:
        review = reviews.get(name)
        if not review:
            problems.append(f"{name} のレビュー結果がありません")
            continue
        if not isinstance(review, dict):
            problems.append(f"{name} のレビュー結果が JSON オブジェクトではありません")
            continue
        verdict = review.get("verdict")
        if verdict not in {"APPROVE", "REQUEST_CHANGES"}:
            problems.append(
                f"{name} の判定 `{verdict}` は APPROVE / REQUEST_CHANGES のいずれかで"
                "なければなりません（COMMENT は使いません）"
            )
        findings = review.get("findings") or []
        if not isinstance(findings, list):
            problems.append(f"{name} の findings が配列ではありません")
            continue
        for i, finding in enumerate(findings):
            if not isinstance(finding, dict):
                problems.append(
                    f"{name} の指摘 {i + 1} が JSON オブジェクトではありません"
                )
                continue
            if "item_id" not in finding:
                problems.append(f"{name} の指摘 {i + 1} に item_id がありません")
                continue
            item_id = finding["item_id"]
            if item_id is not None and item_id not in round_items:
                problems.append(
                    f"{name} の指摘 {i + 1} の item_id `{item_id}` は"
                    "このラウンドの改善項目ではありません"
                )
    if problems:
        return "invalid", problems
    if all((reviews[name] or {}).get("verdict") == "APPROVE" for name in reviewers):
        return "approved", []
    return "changes", []


def unresolved_item_ids(
    review_history: list[dict[str, Any]], round_items: list[str]
) -> tuple[list[str], bool]:
    """未解決の指摘から `(取り消す項目 ID, ラウンド全件が対象か)` を求める。

    ID が `null` の未解決指摘（ラウンド全体に対する指摘）が 1 件でもあれば、
    そのラウンドで適用した項目を全件取り消す。どの項目に紐づくか決められない
    以上、一部だけ残すと Pull Request に中途半端な状態が残るためである。
    """
    targets: list[str] = []
    whole_round = False
    for review in review_history:
        for finding in review.get("findings") or []:
            if finding.get("resolved"):
                continue
            item_id = finding.get("item_id")
            if item_id is None:
                whole_round = True
            elif item_id in round_items and item_id not in targets:
                targets.append(item_id)
    if whole_round:
        return list(round_items), True
    return targets, False


# ---------------- サブコマンド ----------------

def check_auth(runtimes: Iterable[str]) -> dict[str, dict[str, Any]]:
    """参加する CLI の認証状態を確かめる。1 つでも欠けたら初期化を中断する。

    存在確認だけでは足りない。未認証の CLI は起動から 15 秒で終わり、結果ファイルを
    残さないまま提案・レビューの担当から脱落するが、**初期化は成功として扱われる**
    ため、参加者が 1 人欠けた構成のまま最後まで進んでしまう。

    確認コマンドは CLI の版で変わりうるので、`NDF_SKIP_AUTH_CHECK` で飛ばせるように
    しておく。飛ばしたことは必ず出力へ残す（黙って劣化させない）。
    """
    if os.environ.get("NDF_SKIP_AUTH_CHECK"):
        info("⚠ NDF_SKIP_AUTH_CHECK が設定されているため認証確認を飛ばしました")
        return {}

    results: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for runtime in runtimes:
        probe = AUTH_PROBES.get(runtime)
        if probe is None:
            continue
        env = dict(os.environ)
        if runtime == "gemini":
            # 新規パスは untrusted と判定されるため、確認でも信頼を明示する。
            env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        try:
            r = subprocess.run(list(probe), capture_output=True, text=True,
                               timeout=AUTH_PROBE_TIMEOUT, env=env)
            merged = f"{r.stdout}\n{r.stderr}".lower()
            ok = r.returncode == 0 and not any(
                m in merged for m in UNAUTHENTICATED_MARKERS
            )
            detail = (r.stderr.strip() or r.stdout.strip())[:200]
        except FileNotFoundError:
            ok, detail = False, "コマンドが見つかりません"
        except subprocess.TimeoutExpired:
            ok, detail = False, f"{AUTH_PROBE_TIMEOUT} 秒で応答しませんでした"
        results[runtime] = {"command": " ".join(probe), "ok": ok, "detail": detail}
        info(f"{'✅' if ok else '❌'} {runtime}: {' '.join(probe)}")
        if not ok:
            failed.append(f"{runtime}（{detail}）")

    if failed:
        die(
            "認証されていない CLI があります: " + " / ".join(failed) + "。"
            "参加者が欠けたまま進むと、その者の提案とレビューが無いまま収束します。"
            "各 CLI でログインしてから再実行してください"
        )
    return results


def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — ホストと母集合を確定し、作業ディレクトリ root と状態を用意する。

    **提案・レビューの母集合（全 − ホスト）と適用の母集合（全 − gemini）を
    別々に確定する。** 両者は重なるが一致しない。
    """
    try:
        host, detection = assignment.detect_host(args.host)
    except assignment.AssignmentError as e:
        die(str(e))
        return
    try:
        model_spec = models_lib.parse_model_args(args.model)
    except models_lib.ModelSpecError as e:
        die(str(e))
        return

    runtimes = assignment.review_pool(host)
    impl_capable = assignment.impl_pool()
    if host in runtimes:
        die(f"提案・レビューの母集合にホスト {host} が含まれています（判定の誤り）")

    # **認証は作業ディレクトリを作る前に確かめる。** 未認証のまま進むと、
    # 参加者が欠けた構成のまま最後まで走り切ってしまう。
    auth = check_auth(sorted(set(runtimes) | set(impl_capable)))

    repo = _sh(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    head_branch = _sh(
        ["gh", "pr", "view", str(args.pr), "--json", "headRefName", "--jq", ".headRefName"]
    )
    base_branch = _sh(
        ["gh", "pr", "view", str(args.pr), "--json", "baseRefName", "--jq", ".baseRefName"]
    )

    root = (
        pathlib.Path(args.worktree_root).resolve() if args.worktree_root
        else _default_worktree_base() / _repo_slug(repo) / f"rf{args.pr}"
    )
    work = root / "work"
    _ensure_work_worktree(work, head_branch)

    tmp_dir = _tmp_dir_for(work)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_file = _state_path(tmp_dir, args.pr)

    if state_file.exists():
        state = statefile.load(state_file)
        if state.get("final") is None:
            info(f"↻ 前回中断した状態から再開します（提案ラウンド {state.get('outer_round', 0)}）")
            _emit_init(state)
            return

    baseline = _run_baseline_test(args.baseline_test, work, args.test_timeout)

    state: dict[str, Any] = {
        "id": args.pr,
        "started_at": statefile.now(),
        "repo": repo,
        "current_pr": args.pr,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "worktree_root": str(root),
        "worktrees": {"work": str(work), **{r: str(root / r) for r in runtimes}},
        "tmp_dir": str(tmp_dir),
        "target_scope": list(args.scope),
        "host": host,
        "host_detection": detection,
        "runtimes": runtimes,
        "impl_capable": impl_capable,
        "models": model_spec,
        "auth": auth,
        # 提案プロンプトへ許容値をそのまま列挙するために持たせる。
        # 定義は検証側（この CLI）にあり、状態ファイル経由で起動側へ渡す。
        "vocabulary": vocabulary(),
        "skills": {"required": list(REQUIRED_SKILLS)},
        "max_outer_rounds": args.max_outer_rounds,
        "max_fix_rounds": args.max_fix_rounds,
        "max_items_per_round": args.max_items_per_round,
        "severity_threshold": args.severity_threshold,
        "baseline_test": baseline,
        # 生成物の同期は**進行側の責務**。push の直前に実行する。
        "sync_command": args.sync_command,
        "test_timeout": args.test_timeout,
        "outer_round": 0,
        "phase": "init",
        "rounds": [],
        "items": [],
        "deferred_items": [],
        "final": None,
    }
    statefile.save(state_file, state)
    info(f"✅ 状態を初期化しました: {state_file}")
    info(f"   ホスト: {host}（{detection}）")
    info(f"   提案・レビュー: {' / '.join(runtimes)}")
    info(f"   適用の母集合: {' / '.join(impl_capable)}")
    _emit_init(state)


def _emit_init(state: dict[str, Any]) -> None:
    statefile.emit(
        ID=state["id"],
        REPO=state["repo"],
        HOST=state["host"],
        RUNTIMES=" ".join(state["runtimes"]),
        RUNTIMES_CSV=",".join(state["runtimes"]),
        IMPL_POOL=" ".join(state["impl_capable"]),
        WORKTREE_ROOT=state["worktree_root"],
        WORK=state["worktrees"]["work"],
        TMP_DIR=state["tmp_dir"],
        HEAD_BRANCH=state["head_branch"],
        BASE_BRANCH=state["base_branch"],
        SCOPE=" ".join(state["target_scope"]),
    )


def _ensure_work_worktree(work: pathlib.Path, head_branch: str) -> None:
    """書き込み用の作業ディレクトリを冪等に用意する。

    ここだけが**唯一の非 detach**（Pull Request の head ブランチを checkout する）。
    読み取り用は `prepare-worktrees.sh` が `--detach` で作る。同一ブランチを
    2 つの作業ディレクトリへ checkout できないという git の制約があるためである。
    """
    if work.exists():
        if _is_registered_worktree(work):
            _sync_work_worktree(work, head_branch)
            return
        stale = work.with_name(f"work.stale-{time.strftime('%Y%m%d%H%M%S')}")
        work.rename(stale)
        info(f"⚠ 現リポジトリの作業ディレクトリではないため退避しました: {stale}")
    work.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "prune"], capture_output=True, text=True)
    _sh(["git", "fetch", "origin", head_branch])
    # ローカルに head ブランチがあるかどうかで作り方が変わる。無い状態で
    # `worktree add <path> <branch>` を叩くと「そんなブランチは無い」で失敗する。
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{head_branch}"],
        capture_output=True, text=True,
    ).returncode == 0
    if exists:
        _sh(["git", "worktree", "add", str(work), head_branch])
    else:
        _sh(["git", "worktree", "add", "-b", head_branch, str(work),
             f"origin/{head_branch}"])
    info(f"✅ 書き込み用の作業ディレクトリを作成しました: {work}")


def _sync_work_worktree(work: pathlib.Path, head_branch: str) -> None:
    """既存の書き込み用作業ディレクトリを origin の head へ追いつかせる。

    再開までに Pull Request の head が進んでいることがある。同期せずに使うと、
    **古い HEAD に対して提案・適用**してしまう。早送りできない（履歴が分かれた）
    ときは、どちらが正しいかを機械が決められないので中断する。
    """
    fetched = subprocess.run(
        ["git", "fetch", "origin", head_branch],
        cwd=str(work), capture_output=True, text=True,
    )
    if fetched.returncode != 0:
        # 取得できないまま古い `origin/<head>` へ早送りすると、同期したつもりで
        # **古い HEAD のまま**進んでしまう。通信・認証の失敗はここで止める。
        die(
            f"origin/{head_branch} を取得できませんでした: "
            f"{fetched.stderr.strip()[:300]}。"
            "古い HEAD のまま進めないため中断します"
        )
    r = subprocess.run(
        ["git", "merge", "--ff-only", f"origin/{head_branch}"],
        cwd=str(work), capture_output=True, text=True,
    )
    if r.returncode != 0:
        die(
            f"作業ディレクトリを origin/{head_branch} へ早送りできませんでした: "
            f"{r.stderr.strip()[:300]}。"
            "履歴が分かれています。内容を確認してから再実行してください"
        )
    info(f"↻ 作業ディレクトリを origin/{head_branch} へ同期しました: {work}")


def _is_registered_worktree(path: pathlib.Path) -> bool:
    out = _sh(["git", "worktree", "list", "--porcelain"], check=False)
    target = str(path.resolve())
    return any(line == f"worktree {target}" for line in out.splitlines())


def _run_baseline_test(
    command: str, work: pathlib.Path, timeout: int = DEFAULT_TEST_TIMEOUT
) -> dict[str, Any]:
    """着手前のテストを実行して記録する。

    失敗している状態で構造改善に入ると、**壊したのか元から壊れていたのか**
    区別できない。そもそも振る舞いが変わっていないことを示す手段が無い書き換えは
    構造改善ではないため、テストコマンドは必須にしている。
    """
    code, timed_out = _run_with_timeout(command, str(work), timeout)
    if timed_out:
        die(
            f"着手前のテストが {timeout} 秒で終わりませんでした（{command}）。"
            "打ち切りました"
        )
        raise SystemExit(1)
    status = "green" if code == 0 else "red"
    if status == "red":
        die(
            f"着手前のテストが失敗しています（{command}）。"
            "先に直してから開始してください"
        )
    info(f"✅ 着手前のテスト成功: {command}")
    return {"command": command, "status": status, "checked_at": statefile.now()}


def cmd_start_round(args: argparse.Namespace) -> None:
    """Step 2 — 提案ラウンドを開き、実装担当とレビュー担当を返す。

    終了コード: 0 = ラウンドを開いた / 1 = 提案ラウンドの繰り返しが終了済み。

    **再開しても担当は変わらない。** 同じラウンド番号を開き直したときは記録済みの
    割り当てをそのまま返す。
    """
    path, state = _load(args.id)
    if state.get("final"):
        info(f"提案ラウンドの繰り返しは終了しています（{state['final']}）")
        sys.exit(1)

    rounds = state["rounds"]
    if len(rounds) >= state["max_outer_rounds"]:
        _finish(path, state, "max_outer_rounds")
        sys.exit(1)

    round_no = len(rounds) + 1
    existing = next((r for r in rounds if r["round"] == round_no), None)
    if existing is None:
        impl, reviewers = assignment.assign(round_no, state["host"])
        models = state["models"]
        existing = {
            "round": round_no,
            "started_at": statefile.now(),
            "impl": impl,
            "impl_model": {"requested": models.get(impl), "observed": None},
            "reviewers": reviewers,
            "reviewer_models": {
                r: {"requested": models.get(r), "observed": None} for r in reviewers
            },
            "proposed": {},
            "merged": 0, "adopted": 0, "deferred": 0,
            "items": [],
            "apply": {"applied": [], "failed": [], "base_sha": None, "head_sha": None},
            "fix_rounds": 0,
            "durations": {},
            "reviews": [],
        }
        rounds.append(existing)
        state["outer_round"] = round_no
        state["phase"] = "propose"
        statefile.save(path, state)

    info(
        f"=== 提案ラウンド {round_no} / {state['max_outer_rounds']} "
        f"（実装 {existing['impl']} / レビュー {' + '.join(existing['reviewers'])}）==="
    )
    statefile.emit(
        ROUND=round_no,
        IMPL=existing["impl"],
        IMPL_MODEL=existing["impl_model"]["requested"],
        REVIEWERS=" ".join(existing["reviewers"]),
        REVIEWERS_CSV=",".join(existing["reviewers"]),
        MAX_FIX_ROUNDS=state["max_fix_rounds"],
    )


def cmd_merge_proposals(args: argparse.Namespace) -> None:
    """Step 3 — 提案をマージして改善項目を作る。

    終了コード: 0 = 採用あり / 2 = 採用 0 件（提案ラウンドの繰り返しを終える）。

    **同じラウンドで叩き直しても二重に項目を作らない。** 進行を止めても再開できる
    ことが前提なので、統合済みなら前回と同じ結果をそのまま返す。
    """
    path, state = _load(args.id)
    entry = _current_round(state)

    if entry.get("proposal_keys") is not None:
        info(
            f"↻ 提案ラウンド {entry['round']} は統合済みです"
            f"（採用 {entry.get('adopted', 0)} 件 / 見送り {entry.get('deferred', 0)} 件）"
        )
        for item_id in entry.get("items", []):
            item = _find_item(state, item_id, required=False)
            if item is not None:
                info(f"  {item_id} [{item['severity']}] {item['path']}#{item['symbol']}")
        if not entry.get("adopted"):
            sys.exit(2)
        return

    proposals: dict[str, list[dict[str, Any]]] = {}
    for runtime in state["runtimes"]:
        result = _result_path(
            state, runtime,
            stem_for(runtime, "propose", state["id"], entry["round"]),
        )
        if not result.exists():
            info(f"⚠ {runtime} の提案結果がありません: {result}")
            continue
        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            info(f"⚠ {runtime} の提案結果が JSON として読めません: {e}")
            continue
        if not isinstance(payload, dict):
            # 配列や数値のまま `payload.get(...)` を呼ぶと落ちる。
            # 提案は無かったものとして続ける（1 者の不調で全体を止めない）。
            info(
                f"⚠ {runtime} の提案結果が JSON オブジェクトではありません"
                f"（{type(payload).__name__}）。提案なしとして扱います"
            )
            proposals[runtime] = []
            entry["proposed"][runtime] = 0
            continue
        items = payload.get("items")
        proposals[runtime] = [i for i in items if isinstance(i, dict)] \
            if isinstance(items, list) else []
        entry["proposed"][runtime] = len(proposals[runtime])

    excluded = {
        (d["path"], d["symbol"], d["smell"]) for d in state["deferred_items"]
    }
    adopted, deferred = merge_proposals(
        proposals,
        threshold=state["severity_threshold"],
        max_items=state["max_items_per_round"],
        excluded_keys=excluded,
    )

    # 収束判定に使う「前ラウンドとの重複率」。見送りも含めた提案全体で測る。
    current_keys = [(i["path"], i["symbol"], i["smell"]) for i in adopted + deferred]
    entry["proposal_keys"] = [list(k) for k in current_keys]
    entry["merged"] = len(current_keys)
    entry["adopted"] = len(adopted)
    entry["deferred"] = len(deferred)

    round_no = entry["round"]
    for n, item in enumerate(adopted, start=1):
        item_id = f"R{round_no}-{n:03d}"
        state["items"].append({
            "item_id": item_id,
            "round": round_no,
            **item,
            "status": "pending",
            "commits": [],
        })
        entry["items"].append(item_id)
    for item in deferred:
        state["deferred_items"].append({**item, "round": round_no})

    # 適用の起点は**オーケストレータ側で**確定させる。実装担当の申告に委ねると、
    # 欠落・不正時に範囲検査が無効になり、過去の任意のコミットが実在扱いになる。
    # 提案は読むだけなので、この時点の HEAD が着手前の状態である。
    entry["apply_base_sha"] = _git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])

    state["phase"] = "apply" if adopted else "converged"
    if not adopted:
        # 呼び出し側は終了コード 2 で繰り返しを抜けるため、`advance` を通らない。
        # 終了理由をここで確定させないと、報告が「未終了」のままになる。
        state["final"] = "no_more_proposals"
        state["ended_at"] = statefile.now()
    statefile.save(path, state)
    info(
        f"提案 {sum(entry['proposed'].values())} 件 → 統合 {entry['merged']} 件 → "
        f"採用 {entry['adopted']} 件 / 見送り {entry['deferred']} 件"
    )
    for item_id in entry["items"]:
        item = _find_item(state, item_id)
        info(
            f"  {item_id} [{item['severity']}] {item['path']}#{item['symbol']} "
            f"{item['smell']} → {item['technique']} "
            f"(合意 {len(item['proposed_by'])} / 見積 {item['estimated_diff_lines']} 行)"
        )
    if not adopted:
        info("採用 0 件のため、提案ラウンドの繰り返しを終えます")
        sys.exit(2)


def cmd_merge_apply(args: argparse.Namespace) -> None:
    """Step 4 — 適用結果を検証して取り込む。

    終了コード: 0 = 1 件以上成功 / 2 = 全件失敗（次の提案ラウンドへ進む）。

    **1 件の失敗でラウンドを止めない。** 失敗した項目だけを見送りにして、
    残りは採用する。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    if not args.dry_run:
        _discard_impl_leftovers(state, state["worktrees"]["work"])
        _resume_incomplete_apply(path, state, entry)

    # **叩き直しても同じ判定を返す。** 取り込み済みで再実行すると、前回作った
    # 取り消しコミットが「未割当」と判定され、成功した項目まで巻き込んで
    # ラウンド全体を取り消してしまう。
    if (entry.get("apply") or {}).get("merged_at"):
        applied_before = entry["apply"].get("applied") or []
        info(
            f"↻ ラウンド {args.round} の適用は取り込み済みです"
            f"（採用 {len(applied_before)} 件 / 失敗 "
            f"{len(entry['apply'].get('failed') or [])} 件）"
        )
        if not applied_before:
            sys.exit(2)
        return

    impl = entry["impl"]
    result = _result_path(state, impl, stem_for(impl, "apply", state["id"], args.round))
    payload = _read_result(result, impl)

    _record_observed_model(entry, "impl", impl, state, "apply", args.round)

    # 着手前のテストが**成功と確認できていない限り**適用結果を採らない。
    # `red` だけでなく `unknown`（確認していない）も拒否する。確認していない状態を
    # 通すと、「壊したのか元から壊れていたのか」を判別する手段が無いまま進む。
    baseline = state.get("baseline_test") or {}
    if baseline.get("status") != "green":
        for item_id in entry["items"]:
            _find_item(state, item_id)["status"] = "blocked"
        if not args.dry_run:
            statefile.save(path, state)
        die(
            f"着手前のテストが成功と確認できていません（status={baseline.get('status')}）。"
            "適用へ着手しません（全項目を blocked）",
            code=2,
        )

    # 検証の材料は git から取る。結果ファイルから使うのは
    # 「どのコミットがどの項目のものか」という対応付けだけ。
    work = state["worktrees"]["work"]
    head_branch = state["head_branch"]
    test_command = baseline["command"]
    head_sha = _git_out(work, ["rev-parse", "HEAD"]) or ""
    # 起点は `merge-proposals` が記録したもの。**実装担当の申告は使わない。**
    ordered_range = commits_in_range(work, entry.get("apply_base_sha"), head_sha)
    in_range = set(ordered_range or [])
    if ordered_range is None:
        # 範囲を確定できないなら、何も検証できない。素通しにせず失敗させる。
        for item_id in entry["items"]:
            _find_item(state, item_id)["status"] = "blocked"
        if not args.dry_run:
            statefile.save(path, state)
        die(
            "適用の範囲を確定できませんでした"
            f"（起点 {entry.get('apply_base_sha')} / HEAD {head_sha}）。"
            "検証できない適用は採りません",
            code=2,
        )

    # 申告は**このラウンドの改善項目のものだけ**を採る。架空の項目 ID へ割り当てられた
    # コミットを数に入れると、割り当て済みに見えるのに項目別の検証にも入らず、
    # そのまま Pull Request に残せてしまう。
    round_items = set(entry["items"])
    reported: dict[str, dict[str, Any]] = {}
    unknown_ids: list[str] = []
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        info(f"⚠ 適用結果の items が配列ではありません（{type(raw_items).__name__}）")
        raw_items = []
    for r in raw_items:
        if not isinstance(r, dict):
            continue
        item_id = r.get("item_id")
        if item_id in round_items:
            reported[item_id] = r
        elif item_id is not None:
            unknown_ids.append(str(item_id))

    # **範囲のコミットは全て、いずれかの改善項目に割り当てられていること。**
    # 申告から漏れたコミットはテストもトレーラーも差分予算も検査されず、そのまま
    # Pull Request に残る。都合の悪い変更を申告しないだけで検査を回避できてしまう。
    # **1 コミットの所有項目は 1 つだけ。** 同じコミットを 2 つの項目が申告すると、
    # 片方が失敗して取り消したときに、もう片方は成功のまま残る。状態ファイルと
    # 実際の差分が食い違い、どちらが正しいか決められなくなる。
    #
    # 判定は**完全な SHA へ正規化してから**行う。申告の文字列をそのまま鍵にすると、
    # 一方が完全 SHA、他方が短縮 SHA で同じコミットを指したときに重複を見逃す。
    owner_of: dict[str, str] = {}
    duplicated: list[str] = []
    for item_id, r in reported.items():
        for sha in _reported_shas(r):
            full = _git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
            if full is None:
                continue          # 実在しない申告は項目ごとの検証で落ちる
            if full in owner_of and owner_of[full] != item_id:
                duplicated.append(full)
            owner_of.setdefault(full, item_id)

    unassigned = sorted(in_range - set(owner_of))
    if unassigned or unknown_ids or duplicated:
        causes = []
        if unassigned:
            causes.append(
                f"どの改善項目にも割り当てられていないコミットが {len(unassigned)} 件"
                f"（{', '.join(s[:7] for s in unassigned[:5])}）"
            )
        if unknown_ids:
            causes.append(
                f"このラウンドに無い改善項目 ID の申告"
                f"（{', '.join(unknown_ids[:5])}）"
            )
        if duplicated:
            causes.append(
                f"複数の項目が同じコミットを申告しています"
                f"（{', '.join(s[:7] for s in duplicated[:5])}）"
            )
        reason = (
            "、".join(causes)
            + "。検証を回避した変更や、状態と実差分の食い違いを Pull Request に"
              "残さないため、ラウンドごと取り消します"
        )
        info(f"❌ {reason}")
        for item_id in entry["items"]:
            it = _find_item(state, item_id)
            it["status"] = "abandoned"
            it["failure_reason"] = reason
        # 範囲全体を取り消す。どのコミットが安全かを決められない以上、
        # 起点まで戻すのが最も確実である。順序は `_revert_item_commits` が
        # git の履歴から決め直す。
        whole_round = {
            "item_id": f"R{entry['round']}-range",
            "commits": list(ordered_range),
        }
        if not args.dry_run:
            # **取り消しへ着手する前に印を立てる。** 取り消しは済んだのに push
            # できずに終わると、未検証の変更が Pull Request に残ったままになる。
            entry["pending_push"] = True
            statefile.save(path, state)
        _revert_item_commits(state, whole_round, args.dry_run)
        if not args.dry_run:
            # 取り消し後の状態を新しい起点にする。叩き直しても範囲が空になり、
            # 取り消しコミット自体を「未割当」として再び戻すことがない。
            entry["apply_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
        entry["apply"] = {
            "applied": [], "failed": list(entry["items"]),
            "base_sha": entry.get("apply_base_sha"), "head_sha": head_sha,
            "unassigned_commits": unassigned,
            "unknown_item_ids": unknown_ids,
            "duplicated_commits": duplicated,
            "merged_at": statefile.now(),
        }
        state["phase"] = "propose"
        if args.dry_run:
            info("（dry-run）状態ファイルは更新していません")
        else:
            # 項目別の失敗と同じく、**ここで取り消した項目も「対象外」に残す**。
            # 残さないと同じ提案が次のラウンドで再び採用される。
            _defer_abandoned_items(state, entry)
            statefile.save(path, state)
            _push_with_retry_marker(path, state, entry)
        sys.exit(2)

    applied: list[str] = []
    failed: list[str] = []
    scope = state.get("target_scope") or []
    # **判定はその都度残す。** まとめて最後に保存すると、取り消しの途中で中断した
    # ときに適用の記録が一切残らず、どのコミットが検証を通ったのかを状態から
    # 復元できなくなる。再開可能性は収束ループの前提なので、ここが崩れると
    # 中断からの復帰手段が無くなる。
    progress: list[dict[str, Any]] = []
    entry["apply_progress"] = progress
    for item_id in entry["items"]:
        item = _find_item(state, item_id)
        got = reported.get(item_id)
        if got is None:
            problem = "適用結果に項目がありません"
            facts: list[dict[str, Any]] = []
        else:
            facts = collect_commit_facts(
                work, _reported_shas(got), in_range, test_command, head_branch,
                _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT),
            )
            problem = verify_apply_item(item, facts, scope)
        if problem:
            item["status"] = "abandoned"
            item["failure_reason"] = problem
            item["test_failed"] = bool(got and "テストが成功していません" in problem)
            item["budget_exceeded"] = bool(got and "差分予算" in problem)
            item["out_of_scope"] = bool(got and "対象範囲の外" in problem)
            # 取り消しは全項目の判定が出そろってから**まとめて**行う。項目ごとに
            # その場で戻すと、まだ判定していない項目のコミットと競合する。
            item["commits"] = _reported_shas(got)
            failed.append(item_id)
            info(f"❌ {item_id}: {problem}")
        else:
            item["status"] = "reviewing"
            item["commits"] = _reported_shas(got)
            item["diff_lines"] = sum(_safe_int(c.get("diff_lines")) for c in facts)
            applied.append(item_id)
            info(f"✅ {item_id}: {len(item['commits'])} コミット / {item['diff_lines']} 行")
        progress.append({
            "item_id": item_id, "at": statefile.now(),
            "result": "failed" if problem else "ok",
            "reason": problem, "commits": list(item.get("commits") or []),
        })
        if not args.dry_run:
            statefile.save(path, state)

    entry["apply"] = {
        "applied": applied,
        "failed": failed,
        # 起点はオーケストレータが記録したもの。申告は記録にも残さない。
        "base_sha": entry.get("apply_base_sha"),
        "head_sha": head_sha,
        # **取り込み済みの印は最後に立てる。** 取り消しより先に立てると、取り消しに
        # 失敗して中断したときに、次の実行が処理済みガードで素通りしてしまい、
        # 検証を通っていない変更が Pull Request に残り続ける。
        "merged_at": None,
    }
    entry.setdefault("durations", {})["apply"] = _safe_int(
        payload.get("elapsed_seconds")
    )
    state["phase"] = "review" if applied else "propose"

    # `--dry-run` では git も状態ファイルも触らない。片方だけ進むと、確認の
    # つもりで実行した利用者の進行が壊れる。
    if args.dry_run:
        if failed:
            _drop_items(state, entry, failed, dry_run=True)
        info("（dry-run）状態ファイルは更新していません")
        applied = list(entry["apply"]["applied"])
    elif failed:
        # `merged_at` は `_apply_drop` が取り消しの完了時点で立てる。
        applied = _apply_drop(path, state, entry, failed)
    else:
        # **全項目が通ったときも進行側が公開する。** 実装担当は push しないため、
        # ここで公開しないとレビュー担当が Pull Request 上の差分へ指摘を書けない。
        entry["apply"]["merged_at"] = statefile.now()
        _push_with_retry_marker(path, state, entry)

    if not applied:
        info("全項目が失敗したため、このラウンドのレビューは行いません")
        sys.exit(2)


def _defer_abandoned_items(state: dict[str, Any], entry: dict[str, Any]) -> None:
    """このラウンドで取り消した項目を「対象外」として記録する。

    記録しないと、**同じ提案が次のラウンドで再び採用され、同じ理由で失敗する**。
    実測では適用で失敗した項目が 3 ランタイム全員から再提案され、合意数が最大に
    なって最優先で採用された。手順書が「同じ提案が毎ラウンド出続けて収束しない」
    として禁じている状態そのものである。

    除外の鍵は `path` + `symbol` + `smell` なので、その 3 つを必ず残す。
    """
    already = {d.get("item_id") for d in state["deferred_items"]}
    for item_id in entry["items"]:
        item = _find_item(state, item_id, required=False)
        if item is None or item.get("status") != "abandoned" or item_id in already:
            continue
        state["deferred_items"].append({
            "item_id": item_id,
            "path": item["path"], "symbol": item["symbol"], "smell": item["smell"],
            "round": entry["round"],
            "defer_reason": item.get("failure_reason") or "適用結果の検証を通らなかった",
        })


def _run_drop(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any],
    targets: list[str],
) -> dict[str, Any]:
    """取り消しを、中断しても再開できる形で実行する。

    `pending_drop` と `pending_push` を立ててから入り、**戻ったらすぐ保存する**。
    保存しないまま落ちると、積み直しで変わった SHA と取り消し済みの印が失われ、
    次の実行は**履歴に無い SHA を相手に**取り消しをやり直すことになる。

    印はここでは消さない。**呼び出し側が完了の記録と同じ保存で消す。** 先に消すと、
    完了を記録する前に落ちたときに、次の実行が「取り消し済みだが未完了」の状態を
    見分けられなくなる。
    """
    entry["pending_drop"] = list(targets)
    entry["pending_push"] = True
    statefile.save(path, state)
    result = _drop_items(state, entry, list(targets))
    statefile.save(path, state)
    return result


def _apply_drop(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any],
    failed: list[str],
) -> list[str]:
    """検証に失敗した項目を取り消し、採用として残る項目 ID を返す。

    **中断しても再開できる形で記録する。** 失敗の位置で必要な再開が変わるため、
    印は次の順で切り替える。

    | 中断した位置 | 残る印 | 次の実行がすること |
    | --- | --- | --- |
    | 取り消しの途中 | `pending_drop` あり / `merged_at` なし | 取り消しをやり直す |
    | 取り消し後・push 前 | `pending_drop` なし / `merged_at` あり / `pending_push` あり | **push の再送だけ** |

    取り消しより先に `merged_at` を立てると、取り消しに失敗したときに次の実行が
    処理済みガードで素通りし、**再試行できない**。逆に push まで終えるまで
    `merged_at` を立てないと、push だけ失敗したときに次の実行が適用の検証をやり直し、
    取り消しと積み直しのコミットを「未割当」と判定してラウンドごと巻き込む。
    """
    work = state["worktrees"]["work"]
    result = _run_drop(path, state, entry, failed)
    applied = list(entry["apply"].get("applied") or [])
    if result["mode"] == "round":
        # 積み直せなかった。合意済みの項目も含めて全件捨てる。
        for item_id in entry["items"]:
            it = _find_item(state, item_id)
            it["status"] = "abandoned"
            it.setdefault(
                "failure_reason",
                "残す項目を積み直せなかったため、ラウンドごと取り消した",
            )
        applied = []
        entry["apply"]["applied"] = []
        entry["apply"]["failed"] = list(entry["items"])
        # 取り消し後の状態を新しい起点にする（叩き直しでの二重取り消しを防ぐ）。
        entry["apply_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
        state["phase"] = "propose"

    # 取り消した項目は「対象外」として残す。次のラウンドで同じ提案が採用され、
    # 同じ理由で失敗するのを防ぐ。
    _defer_abandoned_items(state, entry)
    # **取り消しが済んだことを push より先に、印の解除と同じ保存で永続化する。**
    # 保存せずに push して失敗すると、次の実行が適用の検証をやり直し、取り消しと
    # 積み直しのコミットを「未割当」と判定してラウンドごと巻き込んでしまう。
    # `pending_push` は残るので、次の実行は push の再送だけを行う。
    entry["pending_drop"] = []
    entry["apply"]["merged_at"] = statefile.now()
    statefile.save(path, state)
    _push_with_retry_marker(path, state, entry)
    return applied


def _resume_incomplete_apply(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """前回終わらなかった取り消しと push を、処理済みの判定より**先に**片づける。

    取り消しをやり残したまま push だけ先に流すと、検証を通っていない HEAD が
    Pull Request へ反映されてしまう。**取り消しの再実行を先に行う。**
    """
    if entry.get("pending_drop"):
        info("↻ 前回終わらなかった取り消しを再実行します")
        _apply_drop(path, state, entry, list(entry["pending_drop"]))
        return
    _flush_pending_push(path, state, entry)


def cmd_judge_review(args: argparse.Namespace) -> None:
    """Step 5 — レビュー 2 者の判定を取り込む。

    終了コード: 0 = 2 者とも承認 / 2 = 修正へ / 3 = 差し戻して再レビュー。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    reviewers = entry["reviewers"]

    reviews: dict[str, dict[str, Any]] = {}
    # 鍵には**修正の世代**を含める。1 回修正したあとに同じ指摘文が返ってくることは
    # 普通にあり、内容だけで見ると「叩き直し」と区別できず、起点も試行番号も
    # 更新されないまま止まってしまう。
    digest = hashlib.sha256(f"fix{entry.get('fix_rounds', 0)}:".encode("ascii"))
    for name in reviewers:
        result = _result_path(state, name, stem_for(name, "review", state["id"], args.round))
        digest.update(name.encode("utf-8"))
        if not result.exists():
            info(f"⚠ {name} のレビュー結果がありません: {result}")
            continue
        digest.update(result.read_bytes())
        try:
            reviews[name] = json.loads(result.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            info(f"⚠ {name} のレビュー結果が JSON として読めません: {e}")
        _record_observed_model(entry, "reviewer", name, state, "review", args.round)

    # **同じレビュー結果で叩き直しても、記録も起点も試行番号も動かさない。**
    # 動かすと、同じ修正結果を別の試行として再処理したり、修正コミットを検証範囲の
    # 外へ追い出したりできてしまう。前回の終了コードだけを再現する。
    review_key = digest.hexdigest()
    for seen in entry.get("review_merged", []):
        if seen.get("key") == review_key:
            info(f"↻ このレビュー結果は判定済みです（前回の終了コード {seen['exit']}）")
            if seen["exit"]:
                sys.exit(seen["exit"])
            return

    verdict, problems = judge(reviews, reviewers, entry["items"])

    # 記録も**型検査済みの値だけ**で作る。`judge()` が invalid と判定した入力でも
    # ここを通るため、無条件に `.get()` を呼ぶと差し戻す前に落ちる。
    record: dict[str, Any] = {"round": len(entry["reviews"]) + 1, "findings": []}
    for name in reviewers:
        review = reviews.get(name)
        review = review if isinstance(review, dict) else {}
        record[name] = review.get("verdict")
        findings = review.get("findings")
        for finding in findings if isinstance(findings, list) else []:
            if not isinstance(finding, dict):
                continue
            record["findings"].append({
                "reviewer": name,
                "item_id": finding.get("item_id"),
                "thread_id": finding.get("thread_id"),
                "summary": finding.get("summary"),
                "resolved": bool(finding.get("resolved")),
            })
    entry["reviews"].append(record)
    # レビュー担当ごとの所要時間は**別々に**持つ。ラウンドの合計を各担当へ配ると、
    # 2 者分を両方に数えることになり、担当同士の比較が成り立たない。
    per_reviewer = entry.setdefault("reviewer_seconds", {})
    for name in reviewers:
        review = reviews.get(name)
        elapsed = review.get("elapsed_seconds") if isinstance(review, dict) else 0
        per_reviewer[name] = per_reviewer.get(name, 0) + _safe_int(elapsed)
    entry.setdefault("durations", {})["review"] = sum(per_reviewer.values())
    statefile.save(path, state)

    def _remember(exit_code: int) -> None:
        entry.setdefault("review_merged", []).append(
            {"key": review_key, "exit": exit_code}
        )

    if verdict == "invalid":
        for p in problems:
            info(f"❌ {p}")
        entry["invalid_reviews"] = entry.get("invalid_reviews", 0) + 1
        if entry["invalid_reviews"] > MAX_INVALID_REVIEWS:
            # 差し戻しを無限に繰り返さない。形式を満たせないレビューが続く以上、
            # このラウンドの成果は検証されていないものとして扱い、変更要求へ落とす。
            # 紐づけ先が決まらないので、取り消しはラウンド全件が対象になる。
            record["findings"].append({
                "reviewer": "cross-refactoring",
                "item_id": None,
                "thread_id": None,
                "summary": (
                    f"レビュー結果の形式が {MAX_INVALID_REVIEWS + 1} 回続けて不正だった: "
                    + " / ".join(problems)
                ),
                "resolved": False,
            })
            _remember(2)
            statefile.save(path, state)
            info("差し戻しの上限に達したため、変更要求として扱います")
            sys.exit(2)
        _remember(3)
        statefile.save(path, state)
        info("レビュー結果を差し戻します。指摘には必ず改善項目 ID を付けてください")
        sys.exit(3)
    if verdict == "approved":
        for item_id in entry["apply"]["applied"]:
            _find_item(state, item_id)["status"] = "done"
        state["phase"] = "propose"
        _remember(0)
        statefile.save(path, state)
        info("✅ レビュー担当 2 者とも承認しました")
        return
    # 修正フェーズの範囲の起点。ここを記録しておかないと、修正コミットが
    # 実在するかを確かめられない。
    entry["fix_base_sha"] = _git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])
    # 試行番号。`merge-fix` が「叩き直し」と「次のラウンド」を区別するのに使う。
    entry["fix_attempts"] = entry.get("fix_attempts", 0) + 1
    _remember(2)
    statefile.save(path, state)
    open_findings = sum(1 for f in record["findings"] if not f["resolved"])
    info(f"変更要求があります（未解決の指摘 {open_findings} 件）")
    sys.exit(2)


def cmd_should_abandon(args: argparse.Namespace) -> None:
    """Step 6 — 修正ラウンドの上限に達したか。

    終了コード: 0 = 見送りへ移る / 2 = まだ修正できる。
    """
    _, state = _load(args.id)
    entry = _round(state, args.round)
    limit = state["max_fix_rounds"]
    if entry["fix_rounds"] >= limit:
        info(f"修正ラウンドが上限 {limit} に達しました。未解決の項目を見送ります")
        return
    info(f"修正ラウンド {entry['fix_rounds']} / {limit} — まだ修正します")
    sys.exit(2)


def cmd_abandon_items(args: argparse.Namespace) -> None:
    """Step 6 — 未解決の指摘に紐づく改善項目だけを取り消す。

    **合意済みの項目は Pull Request に残す。** これを可能にするために、適用は
    項目ごとに 1 手 1 コミットへ分け、状態ファイルへコミットを記録している。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    if not args.dry_run:
        # **やり残した取り消しを push の再送より先に片づける。** 先に push すると、
        # 取り消しが途中の HEAD をそのまま Pull Request へ反映してしまう。
        if entry.get("pending_drop"):
            info("↻ 前回終わらなかった取り消しを再実行します")
            _run_drop(path, state, entry, list(entry["pending_drop"]))
        else:
            _flush_pending_push(path, state, entry)

    # 取り消し自体は `reverted` で冪等だが、見送りの記録は重複しうる。
    if entry.get("abandoned") is not None:
        info(f"↻ ラウンド {args.round} の見送りは処理済みです"
             f"（{len(entry['abandoned'])} 件）")
        return

    targets, whole_round = unresolved_item_ids(entry["reviews"], entry["apply"]["applied"])
    if whole_round:
        info(
            "どの項目にも紐づかない未解決の指摘があるため、"
            "このラウンドで適用した項目を全件取り消します"
        )
    if not targets:
        info("取り消す項目はありません")
        if not args.dry_run:
            entry["abandoned"] = []
            statefile.save(path, state)
        return

    if args.dry_run:
        _drop_items(state, entry, targets, dry_run=True)
        info("（dry-run）状態ファイルは更新していません")
        return

    result = _run_drop(path, state, entry, targets)
    if result["mode"] == "round":
        info("積み直せなかったため、このラウンドで適用した項目を全件見送ります")
        targets = list(entry["apply"].get("applied") or targets)

    already = {d.get("item_id") for d in state["deferred_items"]}
    for item_id in targets:
        item = _find_item(state, item_id)
        item["status"] = "abandoned"
        item.setdefault("failure_reason", "修正ラウンドの上限に達しても指摘が解決しなかった")
        if item_id in already:
            continue
        state["deferred_items"].append({
            "item_id": item_id,
            "path": item["path"], "symbol": item["symbol"], "smell": item["smell"],
            "round": entry["round"],
            "defer_reason": item["failure_reason"],
        })
        info(f"↩ {item_id} を見送りました")

    # 見送りの記録と印の解除を**同じ保存で**行う。保存してから push するので、
    # push が失敗しても記録とローカルの git が食い違わない。
    entry["abandoned"] = targets
    entry["pending_drop"] = []
    state["phase"] = "propose"
    statefile.save(path, state)
    _push_with_retry_marker(path, state, entry)


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """Step 6 — 修正結果を取り込み、修正ラウンドを 1 つ進める。"""
    path, state = _load(args.id)
    entry = _round(state, args.round)
    _discard_impl_leftovers(state, state["worktrees"]["work"])
    _flush_pending_push(path, state, entry)
    impl = entry["impl"]
    result = _result_path(state, impl, stem_for(impl, "fix", state["id"], args.round))
    payload = _read_result(result, impl)

    # **叩き直しても二重に取り込まない。** 修正は同じラウンドで何度も回るため、
    # 「このラウンドで処理済みか」では判定できない。**入力が前回と同じか**で見る。
    # 次の修正ラウンドでは結果ファイルが上書きされ、HEAD も進むので鍵が変わる。
    # 鍵は**試行番号と結果ファイルの内容**から作る。
    #
    # - HEAD は混ぜない。検証に失敗して取り消すと HEAD が変わるため、鍵が一致せず
    #   同じ申告を再処理してしまう。
    # - 内容だけでも足りない。次の修正ラウンドが同じ JSON（コミットなし・同じ
    #   未解決 ID など）を返すと過去のラウンドと衝突し、`fix_rounds` が進まないまま
    #   同じ修正を起動し続ける。
    # - ファイルの更新時刻も使わない。粒度が環境によって違い、書き直しても同じ値に
    #   なりうる。
    #
    # 修正の前には必ず `judge-review` が走るので、そこで進めた試行番号が
    # **実行単位の識別子**になる。叩き直しただけなら番号は変わらない。
    work = state["worktrees"]["work"]
    head_now = _git_out(work, ["rev-parse", "HEAD"]) or ""
    attempt = entry.get("fix_attempts", 0)
    merge_key = (
        f"{attempt}:"
        + hashlib.sha256(result.read_bytes()).hexdigest()
    )
    merged_keys = entry.setdefault("fix_merged_keys", [])
    if merge_key in merged_keys:
        info(
            f"↻ この修正結果は取り込み済みです"
            f"（修正ラウンド {entry['fix_rounds']}）"
        )
        return

    # 自己申告をそのまま信じない。解決 API に失敗・未実行でも「解決済み」と
    # 書けてしまい、未解決の指摘が取り消し対象から外れる。GitHub 側の
    # `isResolved` と突き合わせ、**両方が解決と言っているものだけ**を反映する。
    raw_claimed = payload.get("resolved_thread_ids")
    # 文字列は 1 文字ずつに分解され、数値や真偽値は反復できずに落ちる。
    # **配列であることを先に確かめる。**
    claimed = {
        t for t in (raw_claimed if isinstance(raw_claimed, list) else [])
        if isinstance(t, str) and t.strip()
    }
    if raw_claimed is not None and not isinstance(raw_claimed, list):
        info(f"⚠ resolved_thread_ids が配列ではありません（{type(raw_claimed).__name__}）。"
             "解決の申告は無かったものとして扱います")
    actual = resolved_threads_on_github(state["repo"], state["current_pr"])
    if actual is None:
        info("⚠ レビュースレッドの解決状態を取得できませんでした。"
             "自己申告は採用せず、未解決のまま扱います")
        resolved: set[str] = set()
    else:
        resolved = claimed & actual
        for thread_id in sorted(claimed - actual):
            info(f"⚠ {thread_id} は解決済みと申告されましたが、GitHub では未解決です")

    # 修正コミットも適用と同じ基準で、**git と実際のテスト実行から**検証する。
    # 結果ファイルの申告で済ませると、手順を満たさない変更が収束済みになれてしまう。
    baseline = state.get("baseline_test") or {}
    # 修正の範囲も**オーケストレータが記録した起点**から取る。起点は
    # `judge-review` が変更要求を返したときの HEAD である。
    ordered_range = commits_in_range(work, entry.get("fix_base_sha"), head_now)
    if ordered_range is None:
        die(
            "修正の範囲を確定できませんでした"
            f"（起点 {entry.get('fix_base_sha')} / HEAD {head_now}）。"
            "検証できない修正は採りません",
            code=2,
        )
    reported_shas = _reported_shas(payload)

    # 適用と同じく、**範囲のコミットは全て申告されていること**を求める。
    # 申告から漏れた修正コミットは検証を受けないまま Pull Request に残る。
    reported_full = {
        full for full in (
            _git_out(work, ["rev-parse", "--verify", f"{s}^{{commit}}"])
            for s in reported_shas
        ) if full
    }
    unassigned = sorted(set(ordered_range) - reported_full)

    facts = collect_commit_facts(
        work, reported_shas, set(ordered_range),
        baseline.get("command") or "true", state["head_branch"],
        _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT),
    )

    # **不正なコミットが 1 件でもあれば、修正ラウンドの範囲ごと取り消す。**
    # 状態を記録しないだけでは、未検証の変更が Pull Request に残り続ける
    # （見送りの対象にもならない）。どのコミットが安全かは決められないので、
    # 適用フェーズの未割当コミットと同じ扱いにする。
    problems: list[str] = []
    accepted: list[tuple[str, str]] = []      # (item_id, sha)
    for commit in facts:
        item_id = (commit.get("trailers") or {}).get("Item-Id")
        problem = verify_fix_commit(commit, state.get("target_scope") or [])
        if problem:
            problems.append(problem)
            info(f"❌ 修正コミットが手順を満たしていません: {problem}")
            continue
        accepted.append((item_id, commit["sha"]))

    if unassigned:
        info(
            f"❌ どの申告にも含まれていない修正コミットが {len(unassigned)} 件あります"
            f"（{', '.join(s[:7] for s in unassigned[:5])}）"
        )

    if unassigned or problems:
        # **状態へ記録する前に取り消す。** 先に記録すると、取り消し済みのコミットが
        # 状態ファイルに残り、後の見送り処理が同じコミットをもう一度取り消そうとする。
        info("検証を通らない変更を残さないため、この修正ラウンドの範囲を取り消します")
        # **取り消しへ着手する前に印を立てる。** 取り消しは済んだのに push できずに
        # 終わると、未検証の変更が Pull Request に残ったままになる。
        entry["pending_push"] = True
        statefile.save(path, state)
        _revert_item_commits(
            state,
            {"item_id": f"R{entry['round']}-fix{entry['fix_rounds'] + 1}",
             "commits": list(ordered_range)},
            dry_run=False,
        )
        # 取り消し後の状態を新しい起点にし、**その場で保存する**。ここで保存せずに
        # 落ちると、次の実行は古い起点から範囲を取り直して取り消しコミット自体を
        # 「未申告」と判定し、**取り消しを取り消して**しまう。
        entry["fix_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
        statefile.save(path, state)
        # **push は保存のあと。** ここで push して失敗すると、取り消しコミットは
        # ローカルに残るのに起点の更新が保存されず、叩き直しで二重に取り消してしまう。
        info("⚠ 修正を取り消したため、解決の申告は採用しません")
        resolved = set()
    else:
        for item_id, sha in accepted:
            item = _find_item(state, item_id, required=False)
            if item is not None:
                item.setdefault("commits", []).append(sha)

    for review in entry["reviews"]:
        for finding in review["findings"]:
            if finding.get("thread_id") not in resolved:
                continue
            finding["resolved"] = True

    merged_keys.append(merge_key)
    entry["fix_rounds"] += 1

    entry.setdefault("durations", {})["fix"] = (
        entry.get("durations", {}).get("fix", 0)
        + _safe_int(payload.get("elapsed_seconds"))
    )
    statefile.save(path, state)
    # **取り消したかどうかに関わらず公開する。** 実装担当は push しないため、
    # ここで公開しないと再レビューが Pull Request 上の差分を見られない。
    _push_with_retry_marker(path, state, entry)
    info(f"修正を取り込みました（解決 {len(resolved)} スレッド / 修正ラウンド {entry['fix_rounds']}）")


def cmd_advance(args: argparse.Namespace) -> None:
    """Step 7 — 提案ラウンドの繰り返しを続けるか判定する。

    終了コード: 0 = 続ける / 1 = 終了。

    終了条件は 3 つ。採用 0 件 / 上限到達 / 前ラウンドとの提案重複率が
    しきい値以上。**同じ提案が毎ラウンド出続けて終わらない**ことを防ぐ。
    """
    path, state = _load(args.id)
    rounds = state["rounds"]
    if state.get("final"):
        info(f"終了済みです（{state['final']}）")
        sys.exit(1)
    if not rounds:
        return
    if len(rounds) >= state["max_outer_rounds"]:
        _finish(path, state, "max_outer_rounds")
        sys.exit(1)
    last = rounds[-1]
    if last.get("adopted") == 0:
        _finish(path, state, "no_more_proposals")
        sys.exit(1)
    if len(rounds) >= 2:
        rate = duplicate_rate(
            [tuple(k) for k in last.get("proposal_keys") or []],
            [tuple(k) for k in rounds[-2].get("proposal_keys") or []],
        )
        if rate >= DUPLICATE_RATE_THRESHOLD:
            info(f"提案の重複率が {rate:.0%} で、前ラウンドとほぼ同じです")
            _finish(path, state, "duplicate_proposals")
            sys.exit(1)


def _finish(path: pathlib.Path, state: dict[str, Any], reason: str) -> None:
    state["final"] = reason
    state["ended_at"] = statefile.now()
    state["phase"] = "final"
    statefile.save(path, state)
    info(f"提案ラウンドの繰り返しを終了します（理由: {reason}）")


def cmd_status(args: argparse.Namespace) -> None:
    """現在の状態を人が読む形で出す。"""
    _, state = _load(args.id)
    print(f"# cross-refactoring rf{state['id']}（{state['repo']} #{state['current_pr']}）")
    print(f"ホスト: {state['host']}（{state['host_detection']}）")
    print(f"提案・レビュー: {' / '.join(state['runtimes'])}")
    print(f"適用の母集合: {' / '.join(state['impl_capable'])}")
    print(f"局面: {state['phase']} / 提案ラウンド {state['outer_round']} "
          f"/ {state['max_outer_rounds']}")
    print(f"終了理由: {state.get('final') or '（未終了）'}")
    print()
    print(_round_table(state))


def cmd_report(args: argparse.Namespace) -> None:
    """Step 8 — ラウンド表・項目表・見送り項目・指標を出す。"""
    _, state = _load(args.id)
    print(f"# cross-refactoring 実行報告 — {state['repo']} #{state['current_pr']}")
    print()
    print(f"- ホスト: {state['host']}（{state['host_detection']}）")
    print(f"- 対象範囲: {', '.join(state['target_scope']) or '（未指定）'}")
    print(f"- 終了理由: {state.get('final') or '（未終了）'}")
    baseline = state.get("baseline_test") or {}
    print(f"- 着手前のテスト: {baseline.get('command') or '（未指定）'}"
          f"（{baseline.get('status')}）")
    print()
    print("## ラウンド")
    print()
    print(_round_table(state))
    print()
    print("## 改善項目")
    print()
    print(_item_table(state))
    if state["deferred_items"]:
        print()
        print("## 見送った提案")
        print()
        print("| ラウンド | 対象 | スメル | 理由 |")
        print("| --- | --- | --- | --- |")
        for d in state["deferred_items"]:
            print(f"| {d.get('round', '—')} | {d['path']}#{d['symbol']} | "
                  f"{d['smell']} | {d.get('defer_reason', '—')} |")
    if args.metrics:
        print()
        print("# 指標")
        print()
        print(metrics_lib.format_report(metrics_lib.aggregate(state)))


def _round_table(state: dict[str, Any]) -> str:
    lines = [
        "| R | 実装担当 | モデル | レビュー担当 | モデル | 採用 | 適用 | 見送り | 修正 | 初回承認 |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for entry in state["rounds"]:
        reviewers = entry.get("reviewers", [])
        reviewer_models = entry.get("reviewer_models") or {}
        reviews = entry.get("reviews") or []
        first_approved = "—"
        if reviews:
            first_approved = (
                "はい" if all(reviews[0].get(r) == "APPROVE" for r in reviewers) else "いいえ"
            )
        lines.append(
            f"| {entry['round']} | {entry.get('impl', '—')} | "
            f"{models_lib.label((entry.get('impl_model') or {}).get('requested'))} | "
            f"{' / '.join(reviewers) or '—'} | "
            f"{' / '.join(models_lib.label((reviewer_models.get(r) or {}).get('requested')) for r in reviewers) or '—'} | "
            f"{entry.get('adopted', 0)} | {len(entry.get('apply', {}).get('applied', []))} | "
            f"{len(entry.get('apply', {}).get('failed', []))} | {entry.get('fix_rounds', 0)} | "
            f"{first_approved} |"
        )
    return "\n".join(lines) if state["rounds"] else "（ラウンドなし）"


def _item_table(state: dict[str, Any]) -> str:
    if not state["items"]:
        return "（改善項目なし）"
    lines = [
        "| ID | 対象 | スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for item in state["items"]:
        lines.append(
            f"| {item['item_id']} | {item['path']}#{item['symbol']} | "
            f"{item['smell']} | {item['technique']} | {item['severity']} | "
            f"{'/'.join(item.get('proposed_by', []))} | {item['status']} | "
            f"{len(item.get('commits') or [])} |"
        )
    return "\n".join(lines)


# ---------------- 補助 ----------------

# ---------------- git から事実を取る ----------------
#
# 実装担当は自分の成果を報告する側なので、結果ファイルの値をそのまま検査に使うと
# 「JSON を書き換えるだけで通る」検査になる。ここは git だけを情報源にする。

# テストの置き場所。現状固定テストが先行しているかの判定に使う。
TEST_PATH_MARKERS = ("/test/", "/tests/", "/spec/", "/specs/", "__tests__/")
TEST_NAME_MARKERS = (".test.", ".spec.", "_test.", "_spec.", "test_", "spec_")


def _safe_int(value: Any, fallback: int = 0) -> int:
    """LLM が返した値を int にする。数値として読めなければ `fallback`。

    非数値の文字列・配列・辞書が返ってくることがあり、素の `int()` は
    `TypeError` / `ValueError` で落ちる。落とすと進行が止まるだけで、
    何の検証にもならない。
    """
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return fallback
    return fallback


def _reported_shas(reported: Any) -> list[str]:
    """結果ファイルの `commits[]` から SHA を安全に取り出す。

    相手は LLM なので、`commits` が配列でない・要素が辞書でない・`sha` が
    文字列でないといった崩れ方をする。**壊れた形で落ちないことを型で保証しない。**
    ここで受け止めて、取り出せたものだけを返す。
    """
    if not isinstance(reported, dict):
        return []
    commits = reported.get("commits")
    if not isinstance(commits, list):
        return []
    shas: list[str] = []
    for c in commits:
        sha = c.get("sha") if isinstance(c, dict) else None
        if isinstance(sha, str) and sha.strip():
            shas.append(sha.strip())
    return shas


def _git_out(work: str, args: list[str], strip: bool = True) -> Optional[str]:
    """`git` を実行して標準出力を返す。失敗したら `None`。

    **固定幅で読む出力には `strip=False` を渡す。** `git status --porcelain` の
    状態コードは未 stage の変更で ` M` と先頭が空白になるため、`strip()` すると
    1 行目だけ 1 文字ずれ、切り出したパスの先頭が欠ける。欠けたパスは
    `git add` で `pathspec ... did not match any files` になり、同期が止まる。
    """
    r = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip() if strip else r.stdout.rstrip("\n")


def commits_in_range(work: str, base: Optional[str], head: str) -> Optional[list[str]]:
    """`base..head` に含まれるコミットの完全な SHA を**新しい順**で返す。

    取得できなければ `None`。申告されたコミットが**実在し、このラウンドの範囲にある**
    ことを確かめるのと、範囲全体を取り消すときの順序に使う。

    **空リストと `None` を区別する。** 空リストは「1 件もコミットされていない」、
    `None` は「範囲を確定できなかった」である。混同すると、範囲を確定できないときに
    検査が素通りしてしまう（過去の任意のコミットが実在扱いになる）。
    """
    if not base:
        return None
    out = _git_out(work, ["rev-list", f"{base}..{head}"])
    return None if out is None else out.split()


def commit_trailers(work: str, sha: str) -> dict[str, str]:
    """コミットメッセージのトレーラーを git から読む。

    **結果ファイルの `trailers` は使わない。** JSON 上は仕様どおりでも、実際の
    `git commit` でトレーラーを書き忘れていれば集計に使えない。
    """
    out = _git_out(work, ["log", "-1", "--format=%(trailers:only,unfold)", sha])
    trailers: dict[str, str] = {}
    for line in (out or "").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            trailers[key.strip()] = value.strip()
    return trailers


def commit_diff_lines(work: str, sha: str) -> int:
    """コミットの追加 + 削除行数を git から数える。"""
    out = _git_out(work, ["show", "--numstat", "--format=", sha])
    total = 0
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        for n in parts[:2]:
            if n.isdigit():          # バイナリは `-` になるので数えない
                total += int(n)
    return total


def commit_files(work: str, sha: str) -> list[str]:
    """コミットが触ったファイルのリポジトリ相対パス。範囲の検査に使う。"""
    out = _git_out(work, ["show", "--name-only", "--format=", sha])
    return [p.strip() for p in (out or "").splitlines() if p.strip()]


def commit_touches_tests(work: str, sha: str) -> bool:
    """コミットがテストの置き場所を触っているか。"""
    out = _git_out(work, ["show", "--name-only", "--format=", sha])
    for path in (out or "").splitlines():
        lowered = f"/{path.lower()}"
        name = lowered.rsplit("/", 1)[-1]
        if any(m in lowered for m in TEST_PATH_MARKERS):
            return True
        if any(m in name for m in TEST_NAME_MARKERS):
            return True
    return False


def _run_with_timeout(
    command: str, cwd: str, timeout: int, kill_grace: float = 5.0
) -> tuple[Optional[int], bool]:
    """テストコマンドを実行し `(終了コード, 打ち切ったか)` を返す。

    **新しいプロセスグループで起動し、打ち切るときはグループごと止める。**
    `shell=True` のまま `subprocess.run(timeout=...)` を使うと、終了するのは
    シェルだけで、pytest などの子プロセスは走り続ける。残ったプロセスは同じ
    作業ディレクトリを書き換え続けるため、直後の `git checkout` と競合する。
    """
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        proc.communicate(timeout=timeout)
        return proc.returncode, False
    except subprocess.TimeoutExpired:
        _kill_process_group(proc, kill_grace)
        # 出力はもう使わない。**パイプを閉じてから**待つ。開いたままだと、
        # パイプを継承した子が残っている限り EOF が来ず、ここで止まる。
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        try:
            proc.wait(timeout=kill_grace)
        except subprocess.TimeoutExpired:
            proc.kill()
        return None, True


def _process_group_alive(pgid: int) -> bool:
    """プロセスグループに生きたプロセスが残っているか。"""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # 判断できないときは「残っている」側に倒す（SIGKILL まで進める）。
        return True


def _kill_process_group(
    proc: "subprocess.Popen[bytes]", grace: float = 5.0
) -> None:
    """プロセスグループごと止める。SIGTERM のあと、残っていれば SIGKILL。

    **親シェルの終了で打ち切らない。** 親が終わっても、SIGTERM を無視する子は
    グループに残って作業ディレクトリを書き換え続ける。判定は必ず
    **グループの存否**で行う。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except (PermissionError, OSError):
        proc.kill()
        return

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _process_group_alive(pgid):
            return
        time.sleep(0.2)

    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_test_at(
    work: str, sha: str, command: str, head_branch: str,
    timeout: int = DEFAULT_TEST_TIMEOUT, kill_grace: float = 5.0,
) -> str:
    """指定コミットを取り出してテストを実行し `pass` / `fail` を返す。

    **各コミットでテストが通ったかは、実際に走らせないと分からない。**
    結果ファイルの `test_status` は実装担当の申告にすぎず、検査の根拠にできない。
    実行後は必ず元のブランチへ戻す。

    上限時間を超えたら `fail` とする。生成されたコードやテストが無限ループに入ると、
    待ち続けて進行全体が止まるためで、通す側には倒さない。
    """
    if _git_out(work, ["checkout", "--detach", sha]) is None:
        return "missing"
    try:
        code, timed_out = _run_with_timeout(command, work, timeout, kill_grace)
        if timed_out:
            info(f"⚠ コミット {sha[:7]} のテストが {timeout} 秒で終わりませんでした")
            return "fail"
        return "pass" if code == 0 else "fail"
    finally:
        subprocess.run(
            ["git", "checkout", head_branch], cwd=work, capture_output=True, text=True
        )


def collect_commit_facts(
    work: str, shas: list[str], in_range: set[str], test_command: str,
    head_branch: str, test_timeout: int = DEFAULT_TEST_TIMEOUT,
) -> list[dict[str, Any]]:
    """申告されたコミットについて、git と実際のテスト実行から事実を集める。

    `in_range` は信頼できる起点から HEAD までのコミット集合。ここに無い SHA は
    `exists=False` として返す。実体が無いものにテストを走らせても意味がない。
    """
    facts: list[dict[str, Any]] = []
    for sha in shas:
        full = _git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
        if full is None or full not in in_range:
            facts.append({"sha": sha, "exists": False})
            continue
        facts.append({
            "sha": sha,
            "exists": True,
            "trailers": commit_trailers(work, full),
            "diff_lines": commit_diff_lines(work, full),
            "files": commit_files(work, full),
            "touches_tests": commit_touches_tests(work, full),
            "test_status": run_test_at(
                work, full, test_command, head_branch, test_timeout
            ),
        })
    return facts


_REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved }
      }
    }
  }
}
"""


def resolved_threads_on_github(repo: str, pr: int) -> Optional[set[str]]:
    """GitHub 上で実際に解決済みのレビュースレッド ID を返す。

    取得できなければ `None` を返す。呼び出し側は**空集合と区別する**こと。
    「取得できなかった」を「解決済みが 0 件」と混同すると、通信が失敗しただけで
    全ての指摘を未解決扱いにするか、逆に自己申告を素通しすることになる。
    """
    owner, _, name = repo.partition("/")
    if not owner or not name:
        return None
    resolved: set[str] = set()
    cursor: Optional[str] = None
    while True:
        cmd = [
            "gh", "api", "graphql",
            "-f", f"query={_REVIEW_THREADS_QUERY}",
            "-F", f"owner={owner}", "-F", f"repo={name}", "-F", f"pr={pr}",
        ]
        if cursor:
            cmd += ["-F", f"cursor={cursor}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            info(f"⚠ レビュースレッドの取得に失敗しました: {r.stderr.strip()[:200]}")
            return None
        try:
            threads = (
                json.loads(r.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            info(f"⚠ レビュースレッドの応答を解釈できませんでした: {e}")
            return None
        resolved.update(
            n["id"] for n in threads.get("nodes", []) if n.get("isResolved")
        )
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return resolved
        cursor = page.get("endCursor")
        if not cursor:
            return resolved


def _revert_item_commits(
    state: dict[str, Any], item: dict[str, Any], dry_run: bool = False
) -> int:
    """改善項目のコミットを取り消し、取り消した件数を返す。

    **新しいコミットから順に戻す。** 逆順にすると後続の取り消しが競合する。
    取り消しに失敗したら中断する。半端な状態を Pull Request に残さない。

    適用の検証に失敗したときと、レビューが収束しなかったときの両方から呼ぶ。
    前者で呼ばないと、実装担当が既に push した差分が Pull Request に残り、
    以後のレビュー対象にも混入する。
    """
    # **取り消し済みなら何もしない。** push の失敗などで叩き直したときに、
    # 既に戻したコミットへもう一度 `git revert` を掛けると必ず失敗し、
    # そこから先へ進めなくなる。
    if item.get("reverted"):
        info(f"↩ {item['item_id']} は取り消し済みです")
        return 0

    work = state["worktrees"]["work"]
    shas = _order_newest_first(
        work, [s for s in (item.get("commits") or []) if isinstance(s, str) and s]
    )
    if dry_run:
        for sha in shas:
            info(f"（dry-run）git revert --no-edit {sha}")
        return len(shas)

    # 途中で失敗したら**着手前の HEAD まで戻す**。1 項目が複数のコミットを持つとき、
    # 先行して成功した取り消しだけが履歴に残ると、再実行で不整合になって進めなくなる。
    before = _git_out(work, ["rev-parse", "HEAD"])
    for sha in shas:
        r = subprocess.run(
            ["git", "revert", "--no-edit", sha],
            cwd=work, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "revert", "--abort"], cwd=work,
                           capture_output=True, text=True)
            if before:
                subprocess.run(["git", "reset", "--hard", before], cwd=work,
                               capture_output=True, text=True)
            die(
                f"{item['item_id']} のコミット {sha} を取り消せませんでした: "
                f"{r.stderr.strip()[:400]}"
                f"（HEAD を {before} へ戻しました）"
            )
    item["reverted"] = True
    return len(shas)


def _reset_hard(work: str, sha: Optional[str]) -> None:
    """着手前の HEAD へ戻す。半端な履歴を Pull Request に残さないための後始末。"""
    if sha:
        subprocess.run(["git", "reset", "--hard", sha], cwd=work,
                       capture_output=True, text=True)


def _revert_range(work: str, ordered: list[str], before: Optional[str]) -> None:
    """範囲を**新しい順に**全て取り消す。失敗したら着手前へ戻して中断する。

    範囲全体を新しい順にたどる取り消しは、履歴をそのまま逆再生するだけなので
    **競合しない**。競合するのは「一部のコミットだけを飛ばして戻す」ときである。
    """
    for sha in ordered:
        r = subprocess.run(
            ["git", "revert", "--no-edit", sha],
            cwd=work, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "revert", "--abort"], cwd=work,
                           capture_output=True, text=True)
            _reset_hard(work, before)
            die(
                f"コミット {sha} を取り消せませんでした: {r.stderr.strip()[:400]}"
                f"（HEAD を {before} へ戻しました）"
            )


def _replay_commits(work: str, shas: list[str]) -> Optional[dict[str, str]]:
    """残す項目のコミットを**古い順に**積み直し、`{元の SHA: 新しい SHA}` を返す。

    競合したら `None` を返す。**ここで中断しない。** どの項目を残せるか決められない
    だけなので、呼び出し側がラウンド全件の取り消しへ退避できる。
    """
    mapping: dict[str, str] = {}
    for sha in shas:
        r = subprocess.run(
            ["git", "cherry-pick", "--allow-empty", sha],
            cwd=work, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "cherry-pick", "--abort"], cwd=work,
                           capture_output=True, text=True)
            info(f"⚠ {sha[:7]} を積み直せませんでした: {r.stderr.strip()[:200]}")
            return None
        mapping[sha] = _git_out(work, ["rev-parse", "HEAD"]) or sha
    return mapping


def _commit_owner(
    work: str, state: dict[str, Any], entry: dict[str, Any]
) -> dict[str, str]:
    """このラウンドの `コミット → 改善項目 ID` の対応。完全な SHA へ正規化する。

    どの項目にも属さないコミット（過去の取り消しなど）はここに現れない。
    積み直しの対象から外すために、**属さないこと**を判定できる形にしておく。
    """
    owner: dict[str, str] = {}
    for item_id in entry["items"]:
        item = _find_item(state, item_id, required=False)
        if item is None:
            continue
        for sha in item.get("commits") or []:
            if not isinstance(sha, str) or not sha.strip():
                continue
            full = _git_out(work, ["rev-parse", "--verify", f"{sha.strip()}^{{commit}}"])
            owner[full or sha.strip()] = item_id
    return owner


def _drop_items(
    state: dict[str, Any], entry: dict[str, Any], drop_ids: list[str],
    dry_run: bool = False,
) -> dict[str, Any]:
    """改善項目を取り消し、残す項目を積み直す。

    **範囲を新しい順に全て戻してから、残す項目を古い順に積み直す。** 項目のコミット
    だけを戻すと、取り消し対象より新しい**別項目**のコミットが同じ箇所を触っている
    ときに必ず競合する（実測では採用 5 件のうち 4 件が同一ファイルの隣接領域を
    変更しており、取り消しが競合して進行が止まった）。

    積み直しが競合したときは着手前 HEAD へ戻し、**ラウンド全件の取り消しへ退避する**。
    どの項目を残せるか決められない以上、半端な履歴を残すより全件捨てる方が安全である。

    戻り値の `mode` は次の 3 つ。

    | 値 | 意味 |
    | --- | --- |
    | `item` | 項目単位で取り消し、残す項目を積み直した |
    | `round` | 積み直せず、ラウンド全件を取り消した（退避） |
    | `skip` | 取り消すものが無かった（取り消し済み） |
    """
    work = state["worktrees"]["work"]
    pending = [
        i for i in drop_ids
        if not (_find_item(state, i, required=False) or {}).get("reverted")
    ]
    if not pending:
        info("↩ 取り消し対象は取り消し済みです")
        return {"mode": "skip", "dropped": [], "reverted": 0, "replayed": 0}

    head = _git_out(work, ["rev-parse", "HEAD"])
    ordered = commits_in_range(work, entry.get("apply_base_sha"), head or "HEAD")
    if ordered is None:
        # 起点を記録していない状態ファイル（旧版）では積み直せない。
        # 従来どおり項目のコミットだけを新しい順に戻す。
        info("⚠ 適用の範囲を確定できないため、項目のコミットだけを取り消します")
        reverted = 0
        for item_id in pending:
            reverted += _revert_item_commits(state, _find_item(state, item_id), dry_run)
        return {"mode": "item", "dropped": pending,
                "reverted": reverted, "replayed": 0}

    owner = _commit_owner(work, state, entry)
    drop = set(pending)
    keep_ids = [
        i for i in entry["items"]
        if i not in drop
        and not (_find_item(state, i, required=False) or {}).get("reverted")
    ]
    # `ordered` は新しい順なので、積み直しは反転して古い順にする。
    # **どの項目にも属さないコミット（過去の取り消しなど）は積み直さない。**
    replay = [s for s in reversed(ordered) if owner.get(s) in keep_ids]

    if dry_run:
        for sha in ordered:
            info(f"（dry-run）git revert --no-edit {sha}")
        for sha in replay:
            info(f"（dry-run）git cherry-pick {sha}")
        return {"mode": "item", "dropped": pending,
                "reverted": len(ordered), "replayed": len(replay)}

    _revert_range(work, ordered, head)
    # 取り消しが済んだ地点。積み直しに失敗したらここへ戻せばよい。
    reverted_head = _git_out(work, ["rev-parse", "HEAD"])
    mapping = _replay_commits(work, replay)
    mode = "item"
    if mapping is None:
        info("⚠ 残す項目を積み直せませんでした。このラウンドは全件取り消します")
        # **着手前まで戻して取り消しをやり直さない。** 同じ範囲に対する取り消しが
        # 2 組できて履歴が無駄に汚れる。積み直す前の地点へ戻すだけでよい。
        _reset_hard(work, reverted_head)
        mapping, mode = {}, "round"

    dropped = list(entry["items"]) if mode == "round" else pending
    for item_id in entry["items"]:
        item = _find_item(state, item_id, required=False)
        if item is None:
            continue
        if mode == "round" or item_id not in keep_ids:
            item["reverted"] = True
            continue
        # **積み直しで SHA が変わる。** 記録を更新しないと、次の取り消しが
        # 履歴に無い SHA を指してしまう。
        item["commits"] = [mapping[s] for s in replay if owner.get(s) == item_id]

    entry.setdefault("drops", []).append({
        "at": statefile.now(), "mode": mode, "dropped": dropped,
        "reverted": len(ordered), "replayed": len(mapping),
    })
    info(
        f"↩ 取り消し {len(ordered)} コミット / 積み直し {len(mapping)} コミット"
        f"（{'ラウンド全件へ退避' if mode == 'round' else '項目単位'}）"
    )
    return {"mode": mode, "dropped": dropped,
            "reverted": len(ordered), "replayed": len(mapping)}


def _order_newest_first(work: str, shas: list[str]) -> list[str]:
    """コミットを **git の履歴順（新しい順）** に並べ替える。

    申告された順序を信じない。古いコミットから取り消すと、後続の取り消しが
    競合して進めなくなる。履歴に無いものは順序を決められないので末尾へ置く。
    """
    if len(shas) < 2:
        return list(shas)
    history = _git_out(work, ["rev-list", "HEAD"])
    if history is None:
        return list(shas)
    rank = {sha: i for i, sha in enumerate(history.split())}   # 0 が最も新しい
    resolved = {
        s: (_git_out(work, ["rev-parse", "--verify", f"{s}^{{commit}}"]) or s)
        for s in shas
    }
    return sorted(shas, key=lambda s: rank.get(resolved[s], len(rank)))


def _worktree_changes(work: str) -> dict[str, str]:
    """作業ツリーの変更を `パス → 状態` で返す。同期の前後を比べるために使う。

    無視されているファイルは現れない（`--porcelain` の既定）。改名は移動先の
    パスだけを見る。
    """
    # `core.quotePath` の既定（true）では、非 ASCII を含むパスが `"` で囲まれ
    # `\343` の形へエスケープされる。そのまま `git add` へ渡すと見つからない。
    out = _git_out(
        work, ["-c", "core.quotePath=false", "status", "--porcelain", "-uall"],
        strip=False,
    )
    changes: dict[str, str] = {}
    for line in (out or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:            # 改名。移動先だけを対象にする
            path = path.split(" -> ", 1)[1]
        changes[path.strip('"')] = line[:2]
    return changes


def _control_prefix(state: dict[str, Any], work: str) -> Optional[str]:
    """作業ディレクトリから見た制御用ディレクトリの相対パス。外にあれば `None`。

    状態ファイル・プロンプト・結果・ログの置き場所で、**同期コミットへ入れない**。
    `prepare-worktrees.sh` が無視の設定を置くが、置き場所を環境変数で移した場合や
    配置前に同期が走った場合に備えて、ここでも明示的に外す。
    """
    tmp_dir = str(state.get("tmp_dir") or "")
    if not tmp_dir:
        return None
    try:
        relative = pathlib.Path(tmp_dir).resolve().relative_to(
            pathlib.Path(work).resolve()
        )
    except ValueError:
        return None
    return f"{relative}/"


def _dirty_paths(state: dict[str, Any], work: str) -> list[str]:
    """作業ツリーの未コミット変更のパス。制御用ディレクトリは除く。"""
    control = _control_prefix(state, work)
    return sorted(
        path for path in _worktree_changes(work)
        if not (control and path.startswith(control))
    )


def _discard_worktree_changes(work: str) -> None:
    """作業ツリーと index の未コミット変更を捨てる。**着手前が綺麗なときだけ呼ぶ。**

    **index も戻す。** `git checkout -- .` は staged された差分を戻さないため、
    同期コマンドが `git add` してから失敗すると清浄性の検査が通らないままになり、
    `pending_push` の再試行が永久に進まない。

    無視されたファイル（制御用ディレクトリを含む）は消さない（`git clean` に
    `-x` を付けない）。
    """
    for args in (["reset", "--hard", "HEAD"], ["clean", "-fd"]):
        subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)


def _discard_impl_leftovers(state: dict[str, Any], work: str) -> None:
    """実装担当が残した未コミットの変更を捨てる。取り込みの前に呼ぶ。

    **公開は進行側が検証を通してから行う**ので、コミットされなかった変更は
    どの検証も受けていない。Pull Request へ出す道が無い以上、残す意味がない。

    残したまま進むと、push の直前の清浄性の検査で中断する。実測では、修正
    フェーズでコミットを作れなかった実装担当が直しかけの差分を置いたまま終え、
    続く `merge-fix` が「修正 0 件」として先へ進むこともできなくなった。

    制御用ディレクトリ（状態・結果・ログ）は無視の設定で守られており、
    `git clean` に `-x` を付けないため消えない。
    """
    if not pathlib.Path(work).is_dir():
        return
    dirty = _dirty_paths(state, work)
    if not dirty:
        return
    shown = "、".join(dirty[:5])
    more = f" ほか {len(dirty) - 5} 件" if len(dirty) > 5 else ""
    _discard_worktree_changes(work)
    info(
        f"🧹 コミットされなかった変更を捨てました（{shown}{more}）。"
        "検証を受けていないため公開しません"
    )


def _require_clean_worktree(state: dict[str, Any], work: str) -> None:
    """同期の前に作業ツリーが綺麗であることを求める。汚れていたら中断する。

    汚れたまま同期すると、**同期が作った差分と元からあった差分を区別できない**。
    区別しようと状態コードを比べても足りず、次の 2 つを取りこぼす。

    - 元から ` M` のファイルを同期がさらに書き換えても、状態コードは ` M` のままで
      検知できない。その変更がコミットされず、**push がまた落ちる**
    - `git commit` は index の内容を全て含めるため、`git add` の対象を絞っても
      **先に staged だった変更が検証を受けないまま Pull Request へ入る**

    無視されたファイルはここに現れない。生成物やキャッシュを `.gitignore` へ
    入れてあれば止まらない。
    """
    dirty = _dirty_paths(state, work)
    if not dirty:
        return
    shown = ", ".join(dirty[:5])
    more = f" ほか {len(dirty) - 5} 件" if len(dirty) > 5 else ""
    die(
        f"生成物を同期する前に、作業ツリーへ未コミットの変更があります（{shown}{more}）。"
        "同期が作った差分と区別できず、検証を受けていない変更を公開しかねないため"
        "中断します。コミットするか `.gitignore` へ入れてから再実行してください"
    )


def _sync_generated(state: dict[str, Any]) -> None:
    """push の直前に生成物を同期し、差分があれば進行側のコミットとして積む。

    同期を**実装担当の責務にすると範囲外の変更が生まれ**、範囲の検査で全件失敗する
    （実測ではラウンドの採用 5 件が全て範囲外で落ちた）。かといって同期しないと、
    生成物の同期を検査する pre-push を持つリポジトリでは push そのものが通らず、
    取り消しを Pull Request へ反映できない。そこで**進行側が push の直前に同期する**。

    このコミットはどの改善項目にも属さない。取り消しでは積み直されないが、
    次の push で作り直されるので失われても問題にならない。

    同期に失敗したら中断する。**黙って push しない。** 同期できない状態を公開すると、
    利用者のリポジトリの検査を壊したまま進むことになる。
    """
    command = str(state.get("sync_command") or "").strip()
    if not command:
        return
    work = state["worktrees"]["work"]
    # **同期の前に作業ツリーが綺麗であることを求める。** 汚れたまま同期すると、
    # 同期が作った差分と元からあった差分を区別できない。
    _require_clean_worktree(state, work)
    code, timed_out = _run_with_timeout(
        command, work, _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT)
    )
    if timed_out or code != 0:
        # **途中まで書き換えた差分を残さない。** 残すと次の実行は
        # `_require_clean_worktree` で必ず止まり、`pending_push` の再試行が
        # 永久に進まなくなる。着手前が綺麗だったことは確認済みなので、
        # ここにある変更は全て同期が作ったものだと分かる。
        _discard_worktree_changes(work)
        die(
            f"生成物の同期に失敗しました（{command}）: "
            + ("打ち切りました" if timed_out else f"終了コード {code}")
            + "。同期が作った差分は破棄したので、原因を直せばそのまま再開できます"
        )
    produced = _dirty_paths(state, work)
    if not produced:
        return
    # **後段で落ちたときも差分を残さない。** `git add` / `git commit` の失敗で
    # 作業ツリーを汚したまま中断すると、次の実行は `_require_clean_worktree` で
    # 必ず止まり、`pending_push` の再試行が永久に進まない。捨ててよい根拠は
    # 同期コマンド自身が失敗したときと同じで、着手前が綺麗だったことを
    # 確認済みだからである。
    try:
        _sh(["git", "add", "--", *produced], cwd=work)
        _sh(["git", "commit", "-m", SYNC_COMMIT_MESSAGE], cwd=work)
    except SystemExit:
        _discard_worktree_changes(work)
        raise
    info(f"🔧 生成物を同期しました（{command} / {len(produced)} ファイル）")


def _push_head(state: dict[str, Any]) -> None:
    """head ブランチへ push する。**`--force` は使わない。**

    **公開するのは進行側だけである。** 実装担当に push させると、検証を通る前に
    変更が Pull Request へ現れ、取り消しの反映漏れがそのまま残る。
    """
    _sync_generated(state)
    _sh(
        ["git", "push", "origin", f"HEAD:{state['head_branch']}"],
        cwd=state["worktrees"]["work"],
    )


def _push_with_retry_marker(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """保留の印を立ててから push し、成功したら印を消す。

    印を残さずに push すると、失敗したときに**取り消しがローカルだけに留まる**。
    処理済みガードで次回は素通りするため、Pull Request へ永久に反映されない。
    """
    entry["pending_push"] = True
    statefile.save(path, state)
    _push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)


def _flush_pending_push(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """前回やり残した push を、処理済みの判定より**先に**片づける。"""
    if not entry.get("pending_push"):
        return
    info("↻ 前回 push できなかった取り消しを反映します")
    _push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)


def _current_round(state: dict[str, Any]) -> dict[str, Any]:
    if not state["rounds"]:
        die("提案ラウンドが開かれていません。先に start-round を実行してください")
    return state["rounds"][-1]


def _round(state: dict[str, Any], round_no: int) -> dict[str, Any]:
    for entry in state["rounds"]:
        if entry["round"] == round_no:
            return entry
    die(f"ラウンド {round_no} がありません")
    raise SystemExit(1)


def _find_item(
    state: dict[str, Any], item_id: Optional[str], required: bool = True
) -> Any:
    for item in state["items"]:
        if item["item_id"] == item_id:
            return item
    if required:
        die(f"改善項目 {item_id} がありません")
    return None


def _read_result(path: pathlib.Path, runtime: str) -> dict[str, Any]:
    """結果ファイルを読む。**JSON オブジェクトでなければ失敗させる。**

    配列や数値が返ってきたまま呼び出し側へ渡すと、`payload.get(...)` で
    `AttributeError` になって進行が止まる。読み込みの時点で弾く。
    """
    if not path.exists():
        die(f"{runtime} の結果ファイルがありません: {path}", code=2)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{runtime} の結果ファイルが JSON として読めません: {e}", code=2)
        raise SystemExit(2)
    if not isinstance(payload, dict):
        die(
            f"{runtime} の結果ファイルが JSON オブジェクトではありません"
            f"（{type(payload).__name__}）: {path}",
            code=2,
        )
    return payload


def _record_observed_model(
    entry: dict[str, Any], role: str, runtime: str,
    state: dict[str, Any], phase: str, round_no: Optional[int],
) -> None:
    """CLI の出力から実際に使われたモデル名を拾って記録する。

    取れるのは claude だけである。取れないランタイムは `None` のままにし、
    報告では既定モデルのラウンドとして集計から区別する。
    """
    stem = stem_for(runtime, phase, state["id"], round_no)
    stdout_log = pathlib.Path(state["tmp_dir"]) / f"{stem}-stdout.log"
    if not stdout_log.exists():
        return
    observed = models_lib.observed_model(
        runtime, stdout_log.read_text(encoding="utf-8", errors="replace")
    )
    if not observed:
        return
    if role == "impl":
        entry["impl_model"]["observed"] = observed
        requested = entry["impl_model"]["requested"]
    else:
        entry["reviewer_models"].setdefault(runtime, {"requested": None, "observed": None})
        entry["reviewer_models"][runtime]["observed"] = observed
        requested = entry["reviewer_models"][runtime]["requested"]
    warning = models_lib.mismatch_warning(runtime, requested, observed)
    if warning:
        info(warning)


# ---------------- main ----------------

def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="状態を初期化する")
    init.add_argument("pr", type=int)
    init.add_argument("--scope", nargs="+", required=True,
                      help="対象範囲。提案が無制限に広がらないよう必須にしている")
    init.add_argument("--host", choices=list(assignment.HOST_RUNTIMES), default=None,
                      help="ホストの明示指定。未指定時は環境変数から推定する")
    init.add_argument("--max-outer-rounds", type=int, default=3)
    init.add_argument("--max-fix-rounds", type=int, default=3)
    init.add_argument("--max-items-per-round", type=int, default=5)
    init.add_argument("--severity-threshold", default=DEFAULT_SEVERITY_THRESHOLD,
                      choices=[s for s in SEVERITY_ORDER if s != "unknown"])
    init.add_argument("--model", action="append", metavar="RUNTIME=MODEL",
                      help="ランタイムごとのモデル指定。繰り返し指定できる")
    init.add_argument("--test-timeout", type=int, default=DEFAULT_TEST_TIMEOUT,
                      help="テスト 1 回あたりの上限秒数。超えたら失敗として扱う "
                           f"(default: {DEFAULT_TEST_TIMEOUT})")
    init.add_argument("--sync-command", default=None,
                      help="生成物を同期するコマンド。**push の直前**に進行側が実行し、"
                           "差分があれば進行側のコミットとして積む。"
                           "同期を実装担当にさせると範囲外の変更になるため分離している")
    init.add_argument("--baseline-test", required=True,
                      help="着手前と各コミットで実行するテストコマンド。"
                           "振る舞い不変を示す手段が無い書き換えは構造改善ではないため必須")
    init.add_argument("--worktree-root", default=None)
    init.set_defaults(func=cmd_init)

    for name, func, help_ in (
        ("start-round", cmd_start_round, "提案ラウンドを開く"),
        ("merge-proposals", cmd_merge_proposals, "提案をマージして改善項目を作る"),
        ("advance", cmd_advance, "提案ラウンドの収束判定"),
        ("status", cmd_status, "現在の状態を出す"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.set_defaults(func=func)

    for name, func, help_ in (
        ("judge-review", cmd_judge_review, "レビュー 2 者の判定を取り込む"),
        ("should-abandon", cmd_should_abandon, "修正ラウンド上限の到達判定"),
        ("merge-fix", cmd_merge_fix, "修正結果を取り込む"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.add_argument("round", type=int)
        sp.set_defaults(func=func)

    # コミットを取り消しうる 2 つは、実行前に何が消えるかを確かめられるようにする。
    for name, func, help_ in (
        ("merge-apply", cmd_merge_apply, "適用結果を検証して取り込む"),
        ("abandon-items", cmd_abandon_items, "未解決の指摘に紐づく項目を取り消す"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.add_argument("round", type=int)
        sp.add_argument("--dry-run", action="store_true",
                        help="取り消すコミットを表示するだけで実行しない")
        sp.set_defaults(func=func)

    rp = sub.add_parser("report", help="実行報告を出す")
    rp.add_argument("id", type=int)
    rp.add_argument("--metrics", action="store_true",
                    help="ランタイムとモデルの組で指標を集計する")
    rp.set_defaults(func=cmd_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
