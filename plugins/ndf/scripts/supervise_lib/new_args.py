"""new の種別ごとの引数の表と検査（#1142 の C1）。ほかの副命令と共有しない。"""

from __future__ import annotations

import argparse
import re

from supervise_lib.decl import NEEDS
from supervise_lib.templates import fix_worktree


DECL_HELP = "要る設定（引数が先に効く）: {needs}。new は作業場所 → 元のリポジトリ → 今のディレクトリの順に .ndf/ を探す"
DECL_WORDS = {
    "base": "起点のブランチ（--base か .ndf/worktree.json の base_branch）",
    "test": "テストの宣言（.ndf/project.json の test。無ければ --test-cmd か .ndf/supervise.json の test.command。{paths} を範囲に置き換える）",
    "release": "リリースの形（.ndf/supervise.json の release.form。雛形のある形: package-plugin）",
}
NEW_KINDS = {
    "impl": (
        "1 課題を実装するプラン",
        "ステップ: 実装（worker）→ 同期とチェック（宣言があるとき）→ 範囲テスト → Pull Request → 全体テスト → "
        "doc-lint → ready → マージ。落ちた run は judge が fix・やり直し・stop を選ぶ",
    ),
    "fix": (
        "即時修正のプラン（worker の実装のステップが無い）",
        "作業場所の今のコミットを流す。ステップ: 同期とチェック（宣言があるとき）→ 範囲テスト → Pull Request → "
        "全体テスト → doc-lint → ready → マージ（merge-when-green）。落ちた run は judge が fix・やり直し・stop を選ぶ。"
        "--escape-of なら最後に逃げた不具合を記録する",
    ),
    "check": (
        "検査のプラン",
        "--pr N: 構造改善の要否 → 構造改善 → cross-review → 全体テスト → ready → マージ。\n"
        "--since-last: 前回の検査からの差分を範囲にし、check/<名> のブランチで Pull Request を出して同じ並びを"
        "通す（pace: fast。--review-only なら実装レビューだけ）",
    ),
    "release": (
        "リリースのプラン（形は .ndf/supervise.json の release.form ごと。雛形の無い形は /ndf:release で行う）",
        "package-plugin の dev: bump → changelog → 説明文 → sync-check → release → verify-install → "
        "approval-facts → 提示物の説明文。prod: bump → 差分のある他のプラグインの bump → changelog → 説明文 → トークン消費の記録 → sync-check → "
        "release → verify-install（本番のブランチ）→ 後片付け。実装の queue へ --then で渡すと、実装がすべて"
        "完了した後に続けて流れる",
    ),
    "sprint": (
        "スプリントのステージごとのプランと sprint.json を書き出す",
        "ステージ: 設計（--design の課題ごと。用語集 → 要求と受け入れ条件 → 設計 → 設計 Pull Request → "
        "cross-review → 用語チェック）→ 承認ゲート 1 → スプリントブランチ → 実装（課題ごと。スプリントの"
        "ブランチへ集める）→ 検査 1 回 → リリース（開発版）。ステージの中は queue --max 3 で流す。\n"
        "リリースの形（release.form）が無いか雛形の無い形なら、リリースのプランを書かず、最後のステージに"
        "「/ndf:release で行う」を置く。\n"
        "--state <状態>（normal）: 設計の用語チェックの後と開発版の承認資料の後に助言の MVV 判定（mvv-gate.py check --advise）を置く。"
        "承認は利用者が行う。\n"
        "--pace fast --state <状態>: 使ってよい条件を確かめ、設計（承認ゲート 1 は MVV 判定）→ 実装（起点の"
        "ブランチへ直接）→ 検査（実行の条件）→ 開発版 → 本番（承認ゲート 2 は MVV 判定）を書く",
    ),
    "close": (
        "スプリントの終わりのプラン",
        "ステージ: 最終の検査 → 開発版 → 本番（最終の検査で変更があったときだけ）→ 確定仕様化・課題を閉じる・振り返り",
    ),
}
# 引数: (名前, add_argument の引数, 受ける種別)
NEW_ARGS = [
    (
        "--worktree",
        {
            "required": True,
            "help": {
                "*": "作業場所（worktree）",
                "sprint": "リポジトリの根",
                "close": "リポジトリの根",
                "check": "作業場所（worktree。--since-last はリポジトリの根）",
                "fix": "作業場所（worktree。省けば --branch から <リポジトリ>/.worktrees/<ブランチ>）",
            },
        },
        "impl fix check release sprint close",
    ),
    (
        "--issue",
        {
            "type": int,
            "nargs": "+",
            "default": [],
            "help": {
                "*": "課題の番号",
                "sprint": "実装する課題（課題ごとに実装のプランを書く）",
                "close": "スプリントの課題（最後に閉じる）",
                "fix": "関連する課題の番号（任意）",
            },
        },
        "impl fix check release sprint close",
    ),
    ("--base", {"help": "起点のブランチ（PR の宛先。既定は .ndf/worktree.json の base_branch）"}, "impl fix check release sprint close"),
    ("--production-branch", {"help": "本番のブランチ（既定は .ndf/worktree.json の production_branch）"}, "release sprint close"),
    (
        "--test-cmd",
        {"help": "範囲テストの雛形（{paths} を 1 語で含むコマンド）。宣言（.ndf/project.json の test）より先に効く"},
        "impl fix check sprint close",
    ),
    (
        "--test-kind",
        {
            "choices": ("test", "lint"),
            "default": "test",
            "help": "範囲テストの雛形（--test-cmd）の種別。test = テスト / lint = 静的解析（整形の検査を含む）。既定 test",
        },
        "impl fix check sprint close",
    ),
    ("--test-all", {"help": "廃止（知らせて無視する。全体テストは宣言の suites[].command）"}, "impl fix check sprint close"),
    ("--mode", {"default": "standard", "help": "モード（既定 standard）"}, "impl fix check release sprint close"),
    (
        "--out",
        {
            "help": {
                "*": "書き出すプランのファイル",
                "sprint": "書き出すディレクトリ（既定 sprint-<名前>）",
                "close": "書き出すディレクトリ（既定 sprint-<名前>）",
            }
        },
        "impl fix check release sprint close",
    ),
    (
        "--tests",
        {
            "nargs": "+",
            "default": [],
            "metavar": "PATH",
            "help": {
                "*": "範囲テストの対象（テストのコマンドの {paths} に入る）",
                "sprint": "課題ごとの実装のプランの範囲テストの対象（{paths} に入る。既定 .）",
            },
        },
        "impl fix sprint",
    ),
    ("--scope", {"nargs": "+", "default": [], "metavar": "PATH", "help": "構造改善の範囲"}, "check sprint"),
    ("--title", {"help": "Pull Request の題名"}, "impl fix"),
    ("--summary", {"help": "Pull Request 本文の要約"}, "impl fix"),
    ("--prompt", {"help": "実装の指示文"}, "impl"),
    ("--prompt-file", {"help": "実装の指示文のファイル"}, "impl"),
    (
        "--files",
        {
            "nargs": "+",
            "default": [],
            "metavar": "PATH",
            "help": "触るファイル。同じ出力先の、まだ終わっていない他のプランの指示文へ除外として載る",
        },
        "impl",
    ),
    ("--changes", {"help": "PR 本文の「利用者向けの変化」の材料（リリースの説明文になる）"}, "impl"),
    ("--branch", {"help": "作業場所が無ければ作る worktree のブランチ"}, "impl fix release"),
    ("--escape-of", {"type": int, "help": "直す不具合を持ち込んだ PR（分からなければ 0）。check-trigger.py escape で記録する"}, "impl fix"),
    ("--pr", {"type": int, "help": "検査する Pull Request（--since-last と排他）"}, "check"),
    ("--since-last", {"action": "store_true", "help": "前回の検査からの差分を範囲にする（--pr と排他）"}, "check"),
    ("--id", {"help": "検査の名前（--since-last と組。ブランチ check/<名>）"}, "check"),
    ("--final", {"action": "store_true", "help": "スプリントの終わりの検査（--since-last と組）"}, "check"),
    (
        "--since-ref",
        {
            "help": "前回の検査の位置が origin にも手元の記録にも無いときの範囲の起点"
            "（--since-last と組。初めて使うリポジトリで、どこまで見たか）"
        },
        "check",
    ),
    (
        "--review-only",
        {"action": "store_true", "help": "実装レビューだけ（--since-last と組。開発版ごと。構造改善はトリガーが立ったときの検査）"},
        "check",
    ),
    ("--sprint", {"help": "スプリントの状態（--since-last と組。課題を読む）"}, "check"),
    (
        "--version",
        {
            "help": {
                "*": "配る版（例 10.17.11-dev.1）",
                "sprint": "開発版の版（例 3.8.0-dev.1。release.form の雛形で配るときだけ要る）",
                "close": "開発版の版（最終の検査で変更があったときに配る。release.form の雛形で配るときだけ要る）",
            }
        },
        "release sprint close",
    ),
    ("--prs", {"type": int, "nargs": "+", "default": [], "help": "含む Pull Request"}, "release"),
    (
        "--prs-from-queue",
        {"action": "store_true", "help": "queue が --then で流す前に、先行のプランの報告の Pull Request を --prs に足す"},
        "release",
    ),
    ("--channel", {"choices": ["dev", "prod"], "help": "開発版（dev）か本番（prod）か"}, "release"),
    ("--prev-tag", {"help": "dev: approval-facts の前のタグ。prod: 他のプラグインの差分の起点（省略時は自動）"}, "release"),
    ("--repo", {"help": "元のリポジトリ（省略時は作業場所の /.worktrees/ より前）"}, "release"),
    ("--mvv", {"help": "承認ゲート 2 を MVV で判定する（スプリントの状態）"}, "release"),
    ("--name", {"help": "スプリントの名前（英数字・. _ -。ブランチは sprint/<名前>）"}, "sprint close"),
    (
        "--design",
        {
            "type": int,
            "nargs": "+",
            "default": [],
            "help": "設計のプラン（設計 Pull Request と承認ゲート 1）を作る課題。省けば設計と承認ゲート 1 のステージを置かない",
        },
        "sprint",
    ),
    (
        "--pace",
        {
            "choices": ["normal", "fast", "auto"],
            "default": "normal",
            "help": "進め方（fast と auto は使ってよい条件と MVV の承認を確かめる。auto は normal の工程で承認ゲート 1・2 を MVV 判定にする）",
        },
        "sprint",
    ),
    (
        "--state",
        {
            "help": {
                "*": "スプリントの状態（sprint-state.py のファイル）",
                "sprint": "スプリントの状態（sprint-state.py のファイル）。--pace fast / auto では要る。normal で渡すと承認ゲート 1・2 の前に"
                "助言の MVV 判定を置き、worker と judge が状態のスプリント MVV を読む",
            }
        },
        "sprint close",
    ),
    ("--prod", {"help": "本番の版（例 10.18.0。release.form の雛形で配るときだけ要る）"}, "close"),
    ("--milestone", {"help": "マイルストーン（振り返りの材料）"}, "close"),
]
NEW_REQUIRED = {
    "impl": "--issue・--tests・--title",
    "fix": "--worktree か --branch・--tests・--title",
    "check": "--pr か --since-last（--id と組）",
    "release": "--version・--prs（か --prs-from-queue）・--channel",
    "sprint": "--name・--issue（release.form の雛形で配るときは --version も）",
    "close": "--name・--issue・--state（release.form の雛形で配るときは --version・--prod も）",
}


def add_new_parsers(sub, epilog: str) -> None:
    """new を種別ごとの subparser にする（`epilog` は種別の一覧）。new <種別> --help はその種別の説明・要る設定・引数だけを出す。"""
    n = sub.add_parser(
        "new",
        help="雛形からプランを作る（new <種別> --help で種別ごとの説明）",
        description="雛形からプランを作る。種別ごとの説明・要る設定・引数は new <種別> --help",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = n.add_subparsers(dest="kind", required=True, metavar="{" + ",".join(NEW_KINDS) + "}")
    for kind, (short, steps) in NEW_KINDS.items():
        needs = "・".join(DECL_WORDS[k] for k in NEEDS[kind])
        k = kinds.add_parser(
            kind,
            help=short,
            description=f"{short}。\n\n{steps}",
            epilog=f"必須の引数: {NEW_REQUIRED[kind]}。\n"
            + DECL_HELP.format(needs=needs)
            + (
                "。\n任意の設定: " + DECL_WORDS["release"] + "（無ければ .ndf/project.json の delivery からリリースの経路を導く）"
                if kind in ("sprint", "close")
                else ""
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        for name, kw, allowed in NEW_ARGS:
            if kind in allowed.split():
                helps = kw.get("help")
                extra = {"required": False} if (name, kind) == ("--worktree", "fix") else {}  # --branch でもよい
                k.add_argument(name, **{**kw, **extra, "help": helps.get(kind, helps["*"]) if isinstance(helps, dict) else helps})


def fill_new_defaults(a) -> None:
    """種別の parser に無い引数を既定値で埋める（雛形は種別をまたいで属性を読む）。"""
    for name, kw, _ in NEW_ARGS:
        dest = name.lstrip("-").replace("-", "_")
        if not hasattr(a, dest):
            setattr(a, dest, kw.get("default", False if kw.get("action") == "store_true" else None))


def _check_impl(ap: argparse.ArgumentParser, a) -> None:
    if not (a.issue and a.tests and a.title):
        ap.error("new impl には --issue・--tests・--title が要る")


def _check_fix(ap: argparse.ArgumentParser, a) -> None:
    if not ((a.worktree or a.branch) and a.tests and a.title):
        ap.error("new fix には --worktree か --branch・--tests・--title が要る")
    if not a.worktree:
        a.worktree = fix_worktree(a.branch)


def _check_check(ap: argparse.ArgumentParser, a) -> None:
    if a.since_last and a.pr:
        ap.error("new check の --since-last と --pr は同時に渡せない")
    if a.since_last and not a.id:
        ap.error("new check --since-last には --id（検査の名前）が要る")
    if a.review_only and not a.since_last:
        ap.error("new check の --review-only は --since-last と組にする")
    if a.review_only and a.final:
        ap.error("new check の --review-only と --final は同時に渡せない")
    if not (a.pr or a.since_last):
        ap.error("new check には --pr か --since-last が要る")


def _check_release(ap: argparse.ArgumentParser, a) -> None:
    if not (a.version and (a.prs or a.prs_from_queue) and a.channel):
        ap.error("new release には --version・--prs（か --prs-from-queue）・--channel が要る")
    if not (a.repo or "/.worktrees/" in a.worktree):
        ap.error("new release には --repo が要る（作業場所が /.worktrees/ の下に無い）")


# 種類ごとの引数の組の検査（表に無い種類は確かめない）
NEW_CHECKS = {"impl": _check_impl, "fix": _check_fix, "check": _check_check, "release": _check_release}


def check_new(ap: argparse.ArgumentParser, a) -> None:
    """impl / fix / check / release の引数の組を確かめる（足りなければ ap.error で終了コード 2）。"""
    check = NEW_CHECKS.get(a.kind)
    if check is not None:
        check(ap, a)


def check_sprint(ap: argparse.ArgumentParser, a) -> None:
    """sprint / close の引数の組を確かめる。版数（--version・--prod）はリリースの経路が雛形のときだけ要り、
    宣言を読んだ後に apply_decls が確かめる（#1336）。"""
    if not (a.name and a.issue):
        ap.error(f"new {a.kind} には --name・--issue が要る")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", a.name):
        ap.error("--name は英数字・. _ - だけで書く（ブランチ名 sprint/<名前> に使う）")
    if a.kind == "close" and not a.state:
        ap.error("new close には --state（スプリントの状態）が要る")
