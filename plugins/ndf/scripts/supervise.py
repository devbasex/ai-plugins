#!/usr/bin/env python3
"""フェーズをスクリプトで駆動する。

supervisor の役割を主に担い、このスクリプトがフェーズの手順を順に進める（対応できないフェーズだけ、サブエージェント
`ndf:supervisor` が代行する）。
判断の要らないステップ（コマンドの実行・待ち・進行の記録）はスクリプトが行い、LLM は
次の 2 つのステップでだけ、毎回新しい最小構成の `claude -p` として起動する。

| ステップ | 何をするか | LLM |
| --- | --- | --- |
| run   | コマンドを実行して終わるまで待ち、出力をファイルへ残す | 使わない |
| work  | 1 つの作業（修正・調査）を worker として行わせる | Tool あり（Read/Edit/Write/Bash/Grep/Glob）。`"full": true` なら設定・プラグイン・Skill をそのまま読む claude -p で Skill を回す（cross-review など） |
| drive | 駆動（cross-review / cross-refactoring の drive.py）を run として回し、`pause` のときだけ worker に判断・修正をさせて駆動へ返す | pause のときだけ（work と同じ最小構成） |
| judge | 結果ファイルと規則の抜粋だけを渡し、次のステップを決めさせる | Tool なし |
| pr    | push して Draft の Pull Request を作る（スクリプト）。本文は材料（計画の値・コミット・変更の統計・run の結果・設計文書）から LLM が書く。`"body": "template"` なら材料をそのまま本文にする。本文には必ず「## 利用者向けの変化」の節を置く（ステップの `changes`、無ければ `summary`、それも無ければ題名から。デプロイの説明文の材料）。`"append": [<パス>...]`（`{state_dir}` を置き換える）のうち、あるファイルの中身を署名の前へそのまま足す。末尾の署名は本文に無いときだけ足す。`"decisions": true` なら、設計の PR の本文へ「決めたこと」の節を作る時点で入れる（`pr-body-decisions.sh render`。後の sync が書き込まず、CI を 2 度起動しない）。`"title_doc": <設計文書>` なら、囲みの外の最初の H1（256 コードポイント以内）を題にし、既存の PR の題も書き直す。読めなければ `title` で出し、既存の題は書き直さない | 本文だけTool なし |

使い方:
    supervise.py run <plan.json> [--state-dir DIR] [--from <ステップの id>] [--slow K=V]...
    supervise.py history import <progress.jsonl>... [--history F]   # 既存のステップの所要を遅れの見張りの履歴へ取り込む
    supervise.py expected <plan.json> [--history F] [--slow K=V]...  # 計画のステップごとの想定時間と根拠を出す
    supervise.py new impl --issue N --worktree DIR --tests PATH... --title T [--files PATH...] [--changes TEXT] [--prompt-file F] [--branch B] [--out F]
    supervise.py new impl ... --escape-of <PR番号|0>   # マージの後に逃げた不具合を記録する（check-trigger.py escape）
    supervise.py new fix (--worktree DIR | --branch B) --tests PATH... --title T [--issue N] [--escape-of N] [--summary S] [--out F]
        # 即時修正: 作業場所の今のコミットを 範囲テスト → Pull Request → 全体テスト → doc-lint → ready → マージ で流す
    supervise.py new check --pr N --worktree DIR [--issue N...] [--scope PATH...] [--out F]
    supervise.py new check --since-last --id <名> --worktree <リポジトリの根> [--sprint <状態>] [--final] [--since-ref R] [--out F]
        # 前回の検査からの差分を範囲にする検査（pace: fast）。実行の条件 check-trigger.py eval が立ったときだけ流れる
    supervise.py new check --since-last --review-only --id <名> --worktree <リポジトリの根> [--sprint <状態>] [--since-ref R] [--out F]
        # 実装レビューだけ（開発版ごと）。前回のレビューから PR が 1 本以上で流れ、構造改善のトリガーの起点は動かさない
        # new の共通: [--base B] [--test-cmd CMD] [--production-branch B]（宣言より先に効く。下の「宣言」）。
        # テストは .ndf/project.json の test（戦略・suites[].scope_command の {paths}・junit）を test-run.py が読む
    supervise.py new release --version V (--prs N... | --prs-from-queue) --channel dev|prod --worktree DIR
                             [--issue N...] [--prev-tag T] [--repo DIR] [--out F]
        # --prs-from-queue: queue が --then でこの計画を流す前に、先行の計画の報告の Pull Request を集めて
        # --prs に足す（--prs の固定の番号と併用できる）。prod のステップの最後は後片付け（merged-steps.py cleanup）
    supervise.py new release ... --mvv <スプリントの状態>
        # prod: 先頭に MVV 判定（mvv-gate.py）のステップを置く。dev: approval-facts のステップを gate_as_ok にする
    supervise.py new sprint --name M --worktree <リポジトリの根> --issue N... --version <開発版> [--design N...] [--tests PATH...] [--out DIR]
        # 並列の設計 → 設計承認ゲート→ 設計の結果 → スプリントブランチ → 並列の実装（スプリントブランチへ集める）→ 検査 1 回 → デプロイ
        # をステージごとの計画の JSON と sprint.json へ書き出す。ステージの中は queue --max 3 で流す。デプロイは検査の queue が --then で流す
        # 設計の計画: 用語集（無ければ worktree の中で起こしてコミットする）→ 要求と受け入れ条件（本文と写しが一致すれば
        # 飛ばす）→ 設計 → 設計 PR → cross-review → 用語チェック → 設計承認ゲート
        # release.form が無いか雛形の無い形ならリリースプランを書かず、最後のステージを「/ndf:release で行う」にする
        # --pace fast --state <スプリントの状態>: 使ってよい条件を確かめ、設計（設計承認ゲートは MVV 判定）→ 実装（develop へ直接）
        # → 検査（実行の条件）→ 開発版 → 本番（リリース承認ゲートは MVV 判定）を書く。条件に外れれば計画を書かずに止まる
    supervise.py new close --name M --worktree <根> --issue N... --version <開発版> --prod <正式版> --state <状態> [--out DIR]
        # スプリントの終わり: 確定仕様化・閉じる・振り返り・棚卸し → 最終の検査 → 開発版 → 本番（最終の検査で変更があったときだけ）
    supervise.py design-results --manifest <sprint.json> --base B --root <根> --design N...
        # 設計の結果のステージ: マージした設計 PR の「設計の結果」の表を読み、実装のステージのプランを書き換える
    supervise.py design-approved --pr N [--state <状態>]
        # 設計 PR のマージのプランの入口: 承認ラベルか設計承認ゲートの利用者の承認の記録が無ければ 10
    supervise.py design-glossary --mode M --root . --out <候補の語.md>
        # 設計の計画の入口: 用語集が揃えば何もしない。無ければ init と candidates を打ってコミットし、候補の語を書く
    supervise.py queue <plan.json>... [--max 3] [--then <plan.json>...]... [--done <パス>]
        # 空いた枠へ順に流す。作業ツリーは起動の前に 1 本ずつ作る。--then の計画は前の計画がすべて完了のときだけ
        # 続けて流す（実装の queue の後のデプロイなど）。--then を繰り返すとステージになり、ステージは前のステージがすべて完了のときだけ
        # 流れる。終わると結果の JSON を --done（省けば最初の計画の
        # <計画>-state/queue-done.json）へ書く。始めに流す計画の一覧とステージの並びを done の隣（<done>.plans.json）へ書く
    supervise.py queue --resume <done> [--from <プラン名>=<ステップ>]... [--max N]
        # キューの続き: 前の起動の止まった・承認ゲートのプランを終えたステップから続け、後続のステージも同じ条件で流す
    supervise.py wait <done のパス> [--timeout 秒] [--poll 秒]
        # queue の終わり（done）か、queue が流す計画の attention の行まで待つ。出力は要約の 1 行と結果の JSON。
        # 終了コード: done = 0 / attention = 20 / 上限 = 3。attention の後にもう一度打つと、その続きから待つ
    supervise.py advance --step "<NDF のスクリプトのコマンド>"... [--from N]
        # 手順の連続実行（#2909）: 判断の要らない手順を結果 JSON が ok の間だけ続けて打ち、gate か stopped で止まって
        # 理由と続きのコマンド（--from）を next に返す。打てるのは NDF の scripts/ と skills/*/scripts/ だけで、シェルを通さない。
        # 中継の副命令（gh_parts.py body-lock・post_queue.py retry）は打たない。中身は supervise_lib/advance.py
    supervise.py note <引継ぎ文書.md> --report <report.md> [--next 次の欄] [--section 見出しの語]
    supervise.py sync-check [--root DIR] [--commit]   # 宣言した同期とチェック（.ndf/supervise.json の sync_checks）
    supervise.py format [--root DIR] [--commit]       # 宣言した整形（.ndf/supervise.json の format）。0 = 通った / 1 = 直らない違反 / 2 = 宣言なし・仕組みの失敗
    supervise.py sync-title --pr N --doc <設計文書>   # 設計 PR の題を設計文書の H1 に合わせ直す（違うときだけ。常に 0）
    supervise.py review-settled --state <state.json> --step review --fix-step resolve --max-attempts N --max-cost USD --max-seconds S
        # 設計のプランの上限到達のレビュー: 0 = 未解決 0 件 / 1 = 解決のフローを続ける / 2 = 解決できない
    supervise.py example            # 計画の例を出す

new <種別> --help は、その種別の書き出すステップの並び・要る宣言・引数だけを出す。
new / queue / wait / advance / note / sync-check / format / sync-title / review-settled の結果は lib/step_result.py の形の 1 行の JSON（status を見る）。


プランの形と実行の決まり（ステップの型ごとの鍵・承認ゲート・遅れの見張り）は supervise_lib/plan.py の docstring にある。
中身は supervise_lib/ にある（分け方は issues/issue-1142-design-modules.md の supervise_lib の節）。

最後に `## フェーズの報告` を標準出力と `<state-dir>/report.md` へ書く。conductor はこの
スクリプトを背景の Bash で起動し、終わりの通知で報告を読む。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import deps  # noqa: E402

deps.require(
    "md", "mdtable", "schema", "procs", "locks", "durable", "versions", "waits"
)  # 課題の本文（state → sprint_mvv → md）・表（pr・commands）・宣言の形（decl）・claude -p の打ち切り（claude）・アカウントの排他（claude_accounts）・耐久の記録（flow）
from supervise_lib import commands, design_merge, design_stage, sprint, sprint_routes, new_args, queue, queue_resume, templates  # noqa: E402
from supervise_lib import advance  # noqa: E402
from supervise_lib import pr as pr_step  # noqa: E402
from supervise_lib.decl import DeclError, apply_decls  # noqa: E402
from supervise_lib.plan import EXAMPLE  # noqa: E402
import handoff_doc  # noqa: E402
import legacy_names  # noqa: E402
from step_result import emit  # noqa: E402  supervise_lib が lib/ を sys.path へ足す

MAIN_EPILOG = """フェーズごとの種別（new <種別> --help で、書き出すステップの並び・要る設定・引数を出す）:
  フェーズ                                 種別
  設計 → 設計承認ゲート→ 実装 → 検査（スプリント）  new sprint（リリースの形に雛形があればリリースまで）
  実装（1 課題の Pull Request）              new impl
  即時修正（worker の実装なしで Pull Request）  new fix
  検査（1 本の Pull Request か前回からの差分）   new check
  リリース（開発版か本番）                   new release
  スプリントの終わり                         new close
書き出したプランは run で 1 本、queue で並べて流し、wait で終わりか attention まで待つ。"""


def build_parser(sub) -> None:
    """副命令の引数を登録の順に足す。"""
    add_plan_run_parsers(sub)
    new_args.add_new_parsers(sub, MAIN_EPILOG)
    add_queue_parsers(sub)
    add_design_parsers(sub)
    add_tool_parsers(sub)


def add_plan_run_parsers(sub) -> None:
    """副命令 run・history・expected・example の引数を足す。"""
    r = sub.add_parser(
        "run",
        help="プランを 1 本流す",
        description="プランを 1 本流す。ステップを順に進め、報告を標準出力へ出す（終了コードはフェーズの結果 phase-result.json の status から: 完了 = 0 / 承認ゲート = 10 / 止まった = 1）",
        epilog="状態は --state-dir（省けば <プラン>-state/）に progress.jsonl・報告・ステップの出力として残る。"
        "途中で止まったら --from <ステップの id> で続きから流す",
    )
    r.add_argument("plan", help="プランの JSON")
    r.add_argument("--state-dir", help="状態の置き場（既定は <プラン>-state/）")
    r.add_argument("--from", dest="start", help="このステップから始める（途中から再開するとき）")
    r.add_argument(
        "--slow",
        action="append",
        default=[],
        metavar="K=V",
        help="遅れの見張りの設定を上書きする（計画と .ndf/supervise.json の slow より先に効く。繰り返せる）",
    )
    hp = sub.add_parser("history", help="ステップの所要の履歴（遅れの見張りの想定の材料）")
    hs = hp.add_subparsers(dest="hcmd", required=True)
    hi = hs.add_parser("import", help="既存の progress.jsonl のステップの所要を履歴へ取り込む")
    hi.add_argument("progress", nargs="+")
    hi.add_argument("--history", help="履歴のファイル（既定は <git の共通ディレクトリ>/ndf/step-history.jsonl）")
    ex = sub.add_parser("expected", help="計画のステップごとの想定時間と根拠を出す")
    ex.add_argument("plan")
    ex.add_argument("--history")
    ex.add_argument("--slow", action="append", default=[], metavar="K=V")
    sub.add_parser("example")


def add_queue_parsers(sub) -> None:
    """副命令 queue・wait・advance の引数を足す。"""
    q = sub.add_parser(
        "queue",
        help="プランを同時に --max 本まで順に流す",
        description="プランを同時に --max 本まで順に流す。空いた枠へ順に流し、worktree は起動の前に 1 本ずつ作る",
        epilog="--then のプランは前のプランがすべて完了のときだけ続けて流す（実装の queue の後のリリースなど）。"
        "--then を繰り返すとステージになる。終わると結果の JSON を --done へ書き、始めに流すプランの"
        "一覧を <done>.plans.json へ書く",
    )
    q.add_argument("plans", nargs="*", help="流すプランの JSON（--resume と併せて渡さない）")
    q.add_argument("--max", type=int, help="同時に流す本数（既定 3。並列の計画のレーンならレーンのプランの本数。--resume は前の起動の値）")
    q.add_argument("--resume", metavar="DONE", help="前の起動の done。止まったプランを終えたステップから続け、後続のステージも流す")
    q.add_argument("--from", dest="from_", action="append", default=[], metavar="PLAN=STEP", help="--resume: 続けるプランの開始のステップ")
    q.add_argument("--poll", type=float, default=5.0, help="終わりを見る間隔（秒）")
    q.add_argument(
        "--then",
        nargs="+",
        action="append",
        default=[],
        metavar="PLAN",
        help="前の計画がすべて完了したときだけ続けて流す計画（例: リリースプラン）。繰り返すとステージになり、"
        "ステージは前のすべてのステージが完了のときだけ流れる",
    )
    q.add_argument(
        "--done",
        help="終わったときに結果の JSON を書く所（省けば最初の計画の状態ディレクトリの queue-done.json）。待つ側は wait <このパス> で待つ",
    )
    w = sub.add_parser(
        "wait",
        help="queue の終わりか attention の行まで待つ（done = 0 / attention = 20 / 上限 = 3）",
        description="queue の終わり（done）か、queue が流すプランの attention の行まで待つ。出力は要約の 1 行と結果の JSON",
        epilog="終了コード: done = 0 / attention = 20 / 上限 = 3。attention の後にもう一度打つと、その続きから待つ",
    )
    w.add_argument("done", help="queue の --done のパス（省いた queue なら <最初の計画>-state/queue-done.json）")
    w.add_argument("--timeout", type=float, default=10800.0, help="待つ上限（秒）")
    w.add_argument("--poll", type=float, default=5.0, help="見る間隔（秒）")
    av = sub.add_parser(
        "advance",
        help="判断の要らない NDF の手順を続けて打ち、gate か stopped で止まる（手順の連続実行。#2909）",
        epilog="終了コード: すべて ok = 0 / 子が gate = 子の終了コード / 子が stopped = 1 / 打てない手順・--from の誤り = 2",
    )
    av.add_argument("--step", action="append", default=[], required=True, help="打つ手順（NDF のスクリプトのコマンド。順に打つ）")
    av.add_argument("--from", dest="start", type=int, default=1, help="1 から数えた番号。これより前の手順を打たない")


def add_design_parsers(sub) -> None:
    """副命令 design-glossary・design-results・design-approved の引数を足す。"""
    g = sub.add_parser(
        "design-glossary",
        help="設計のプランの入口: 用語集が無ければ worktree の中で起こしてコミットする",
        description="glossary.py gate が通れば何もしない。宣言か用語集が無ければ init と candidates を打ち、"
        "起こしたファイルをコミットし、候補の語を --out へ書く（pr のステップが PR 本文へ足す）",
    )
    g.add_argument("--mode", default="standard", help="モード（設計の工程の入口で用語集を見るモードか）")
    g.add_argument("--root", default=".", help="worktree の根")
    g.add_argument("--out", required=True, help="候補の語を書く Markdown のパス")
    d = sub.add_parser(
        "design-results",
        help="設計の結果のステージ: マージした設計 PR の設計の結果を読み、実装のステージのプランを書き換える",
        description="design/issue-<番号> のマージした PR が変えた issues/*.md の「設計の結果」の表を読み、実装のプランへ "
        "触るファイル・取り込んだ課題・実行の条件を書く。design-results.json を manifest の隣へ書く",
        epilog="終了コード: 書いた = 0（読めなかった設計の課題があっても 0）/ manifest を読めない・取り込み先が無い・扱いが食い違う = 2",
    )
    d.add_argument("--manifest", required=True, help="スプリントの計画の sprint.json")
    d.add_argument("--base", required=True, help="設計 PR をマージしたベースブランチ")
    d.add_argument("--root", default=".", help="リポジトリの根")
    d.add_argument("--design", type=int, nargs="+", required=True, help="設計の課題")
    da = sub.add_parser(
        "design-approved",
        help="設計 PR のマージのプランの入口: 承認の証跡（承認ラベルか設計承認ゲートの利用者の承認の記録）を確かめる",
        epilog="終了コード: 証跡あり = 0（記録だけならラベルを付ける）/ 証跡なし・PR が 0 = 10 / gh が読めない = 2",
    )
    da.add_argument("--pr", type=int, required=True, help="設計 Pull Request")
    da.add_argument("--state", help="スプリントの状態（sprint-state.py のファイル）")


def add_tool_parsers(sub) -> None:
    """副命令 note・sync-check・format・sync-title・review-settled の引数を足す。"""
    t = sub.add_parser("note", help="報告から引継ぎ文書の表へ 1 行を足す")
    t.add_argument("doc")
    t.add_argument("--report", required=True)
    t.add_argument("--next", default="")
    t.add_argument("--section", default=handoff_doc.PROGRESS_SECTION)
    c = sub.add_parser("sync-check", help="宣言した同期とチェック（.ndf/supervise.json の sync_checks）")
    c.add_argument("--root", default=".")
    c.add_argument("--commit", action="store_true", help="同期で変わったファイルをコミットする")
    fm = sub.add_parser(
        "format",
        help="宣言した整形（.ndf/supervise.json の format）",
        epilog="終了コード: 通った = 0 / 整形で直らない違反 = 1 / 宣言が無い・形が違う・コマンドの 0 と 1 以外・コミットの失敗 = 2",
    )
    fm.add_argument("--root", default=".")
    fm.add_argument("--commit", action="store_true", help="整形で変わったファイル（整形の前に変更の無かったもの）をコミットする")
    st = sub.add_parser(
        "sync-title",
        help="設計 PR の題を設計文書の H1 に合わせ直す",
        description="設計文書の囲みの外の最初の H1 を読み、PR の題と違うときだけ書き直す。H1 を読めないときは書き直さない",
        epilog="終了コード: 常に 0（gh の失敗は標準エラーへ出し、承認ゲートを止めない）",
    )
    st.add_argument("--pr", type=int, required=True, help="題を合わせる Pull Request")
    st.add_argument("--doc", required=True, help="題を読む設計文書（作業ディレクトリからの相対パス）")
    rs = sub.add_parser(
        "review-settled",
        help="上限到達で終わったレビューの未解決の件数と、解決のフローを続けるかを実行の記録から決める",
        description="実行の記録（state.json）の log から、--step の最後の結果（終了コード 6）の未解決の件数と、--fix-step の"
        "回数・費用・秒を読む。上から順に照らす: 読めない（U1）/ 未解決 0 件 / 回数（U2）/ 件数が減らない（U3）/ 費用（U4）/ 時間（U5）",
        epilog="終了コード: 未解決 0 件 = 0 / 解決を続ける = 1 / 解決できない（U1〜U5）= 2",
    )
    rs.add_argument("--state", required=True, help="実行の記録（<状態ディレクトリ>/state.json）")
    rs.add_argument("--step", required=True, help="未解決の件数を読むステップの id")
    rs.add_argument("--fix-step", required=True, help="解決のフローの直しのステップの id（回数と費用を数える）")
    rs.add_argument("--max-attempts", type=int, required=True, help="直しの成功の回数の上限")
    rs.add_argument("--max-cost", type=float, required=True, help="直しの費用の合計の上限（ドル）")
    rs.add_argument("--max-seconds", type=float, required=True, help="最初の直しから後の、直しとレビューの秒の合計の上限")


def outside_plan(a: argparse.Namespace) -> dict | None:
    """計画の外のセッションの起動の判定（`lib/sprint_loop_cli.outside_plan`）。止めるなら結果（`exit` つき）、通すなら None。"""
    import parallel_guard

    return parallel_guard.entry_check(a.worktree, getattr(a, "outside_plan", None), "supervise.py new sprint")


def _cmd_new(ap: argparse.ArgumentParser, a: argparse.Namespace) -> "NoReturn":  # noqa: F821
    new_args.fill_new_defaults(a)
    if a.kind in ("sprint", "close"):
        new_args.check_sprint(ap, a)
        if a.kind == "sprint" and (blocked := outside_plan(a)) is not None:  # 何かを書く前に判定する（#2733 の I9）
            emit(blocked, blocked.pop("exit"))
        try:
            apply_decls(a)
            sprint_routes.apply_routes(a)
            if a.kind == "close":
                emit(sprint.cmd_new_sprint(a, sprint.close_waves(a)))
            res = sprint.cmd_new_sprint(a)
            emit(res, 1 if res["status"] == "stopped" else None)
        except DeclError as e:
            ap.error(str(e))
    new_args.check_new(ap, a)
    try:
        apply_decls(a)
        emit(templates.cmd_new(a))
    except DeclError as e:
        ap.error(str(e))


def _cmd_queue(ap: argparse.ArgumentParser, a: argparse.Namespace) -> "NoReturn":  # noqa: F821
    if a.resume:
        if a.plans or a.then or a.done:
            ap.error("queue --resume は位置引数のプラン・--then・--done と併せて渡さない")
        emit(queue_resume.cmd_queue_resume(a.resume, a.from_, a.max and max(1, a.max), a.poll, queue.cmd_queue))
    if not a.plans or a.from_:
        ap.error("queue にはプランが要る（--from は --resume と使う）")
    emit(queue.cmd_queue(a.plans, max(1, a.max or default_max()), a.poll, a.then, a.done))


def _cmd_wait(ap: argparse.ArgumentParser, a: argparse.Namespace) -> "NoReturn":  # noqa: F821
    summary, res, code = queue.cmd_wait(a.done, a.timeout, a.poll)
    print(summary, flush=True)
    emit(res, code)


def _cmd_example(ap: argparse.ArgumentParser, a: argparse.Namespace) -> int:
    print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
    return 0


# 副命令 → 処理。emit で終わるものは戻らない。run だけが終了コードを返す
COMMANDS = {
    "run": lambda ap, a: commands.cmd_run(a.plan, a.state_dir, a.slow, a.start),
    "history": lambda ap, a: emit(commands.cmd_history_import(a.progress, a.history)),
    "expected": lambda ap, a: emit(*commands.cmd_expected(a.plan, a.history, a.slow)),
    "example": _cmd_example,
    "new": _cmd_new,
    "queue": _cmd_queue,
    "wait": _cmd_wait,
    "advance": lambda ap, a: emit(*advance.cmd_advance(a.step, a.start)),
    "design-glossary": lambda ap, a: emit(*commands.cmd_design_glossary(a.root, a.mode, a.out)),
    "design-results": lambda ap, a: emit(*design_stage.cmd_design_results(a)),
    "design-approved": lambda ap, a: emit(*design_merge.cmd_design_approved(a.pr, a.state)),
    "note": lambda ap, a: emit(commands.cmd_note(a.doc, a.report, a.next, a.section)),
    "sync-check": lambda ap, a: emit(commands.sync_check(a.root, a.commit)),
    "format": lambda ap, a: emit(*commands.cmd_format(a.root, a.commit)),
    "sync-title": lambda ap, a: emit(pr_step.sync_title(".", a.pr, a.doc)),
    "review-settled": lambda ap, a: emit(
        *commands.cmd_review_settled(a.state, a.step, a.fix_step, a.max_attempts, a.max_cost, a.max_seconds)
    ),
}


def default_max(cwd: str | None = None) -> int:
    """`queue` の `--max` を省いたときの本数。並列の計画のレーンならレーンのプランの本数、そうでなければ 3（#2436）。"""
    import parallel_lane

    return parallel_lane.queue_max(cwd)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], epilog=MAIN_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    build_parser(sub)
    a = ap.parse_args(legacy_names.rewrite_argv("supervise.py", sys.argv[1:]))
    return COMMANDS[a.cmd](ap, a)


if __name__ == "__main__":
    sys.exit(main())
