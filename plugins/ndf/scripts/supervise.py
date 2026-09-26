#!/usr/bin/env python3
"""フェーズをスクリプトで駆動する。

supervisor（サブエージェント）の代わりに、このスクリプトがフェーズの手順を順に進める。
判断の要らないステップ（コマンドの実行・待ち・進行の記録）はスクリプトが行い、LLM は
次の 2 つのステップでだけ、毎回新しい最小構成の `claude -p` として起動する。

| ステップ | 何をするか | LLM |
| --- | --- | --- |
| run   | コマンドを実行して終わるまで待ち、出力をファイルへ残す | 使わない |
| work  | 1 つの作業（修正・調査）を worker として行わせる | Tool あり（Read/Edit/Write/Bash/Grep/Glob）。`"full": true` なら設定・プラグイン・Skill をそのまま読む claude -p で Skill を回す（cross-review など） |
| drive | 駆動（cross-review / cross-refactoring の drive.py）を run として回し、`pause` のときだけ worker に判断・修正をさせて駆動へ返す | pause のときだけ（work と同じ最小構成） |
| judge | 結果ファイルと規則の抜粋だけを渡し、次のステップを決めさせる | Tool なし |
| pr    | push して Draft の Pull Request を作る（スクリプト）。本文は材料（計画の値・コミット・変更の統計・run の結果・設計文書）から LLM が書く。`"body": "template"` なら材料をそのまま本文にする。本文には必ず「## 利用者向けの変化」の節を置く（ステップの `changes`、無ければ `summary`、それも無ければ題名から。配布の説明文の材料）。`"append": [<パス>...]`（`{state_dir}` を置き換える）のうち、あるファイルの中身を署名の前へそのまま足す。末尾の署名は本文に無いときだけ足す | 本文だけTool なし |

使い方:
    supervise.py run <plan.json> [--state-dir DIR] [--from <ステップの id>] [--slow K=V]...
    supervise.py history import <progress.jsonl>... [--history F]   # 既存のステップの所要を遅れの見張りの履歴へ取り込む
    supervise.py expected <plan.json> [--history F] [--slow K=V]...  # 計画のステップごとの想定時間と根拠を出す
    supervise.py new impl --issue N --worktree DIR --tests PATH... --title T [--files PATH...] [--changes TEXT] [--prompt-file F] [--branch B] [--out F]
    supervise.py new impl ... --escape-of <PR番号|0>   # マージの後に逃げた不具合を記録する（check-trigger.py escape）
    supervise.py new fix (--worktree DIR | --branch B) --tests PATH... --title T [--issue N] [--escape-of N] [--summary S] [--out F]
        # 即時修正: 作業場所の今のコミットを 範囲テスト → Pull Request → 全体テスト → doc-lint → ready → マージ で流す
    supervise.py new check --pr N --worktree DIR [--issue N...] [--scope PATH...] [--out F]
    supervise.py new check --since-last --id <名> --worktree <リポジトリの根> [--mission <状態>] [--final] [--since-ref R] [--out F]
        # 前回の検査からの差分を範囲にする検査（pace: fast）。実行の条件 check-trigger.py eval が立ったときだけ流れる
    supervise.py new check --since-last --review-only --id <名> --worktree <リポジトリの根> [--mission <状態>] [--since-ref R] [--out F]
        # 実装レビューだけ（開発版ごと）。前回のレビューから PR が 1 本以上で流れ、構造改善のトリガーの起点は動かさない
        # new の共通: [--base B] [--test-cmd CMD] [--test-all PATH] [--production-branch B]（宣言より先に効く。下の「宣言」）
    supervise.py new release --version V (--prs N... | --prs-from-queue) --channel dev|prod --worktree DIR
                             [--issue N...] [--prev-tag T] [--repo DIR] [--out F]
        # --prs-from-queue: queue が --then でこの計画を流す前に、先行の計画の報告の Pull Request を集めて
        # --prs に足す（--prs の固定の番号と併用できる）。prod のステップの最後は後片付け（merged-steps.py cleanup）
    supervise.py new release ... --mvv <ミッションの状態>
        # prod: 先頭に MVV 判定（mvv-gate.py）のステップを置く。dev: approval-facts のステップを gate_as_ok にする
    supervise.py new mission --name M --worktree <リポジトリの根> --issue N... --version <開発版> [--design N...] [--tests PATH...] [--out DIR]
        # 並列の設計 → 関門 1 → ミッションのブランチ → 並列の実装（ミッションのブランチへ集める）→ 検査 1 回 → 配布
        # をステージごとの計画の JSON と mission.json へ書き出す。ステージの中は queue --max 3 で流す。配布は検査の queue が --then で流す
        # 設計の計画: 用語集（無ければ worktree の中で起こしてコミットする）→ 要求と受け入れ条件（本文と写しが一致すれば
        # 飛ばす）→ 設計 → 設計 PR → cross-review → 用語チェック → 関門 1
        # release.form が無いか雛形の無い形なら配布の計画を書かず、最後のステージを「/ndf:release で行う」にする
        # --pace fast --state <ミッションの状態>: 使ってよい条件を確かめ、設計（関門 1 は MVV 判定）→ 実装（develop へ直接）
        # → 検査（実行の条件）→ 開発版 → 本番（関門 2 は MVV 判定）を書く。条件に外れれば計画を書かずに止まる
    supervise.py new close --name M --worktree <根> --issue N... --version <開発版> --prod <正式版> --state <状態> [--out DIR]
        # ミッションの終わり: 最終の検査 → 開発版 → 本番（最終の検査で変更があったときだけ）→ 確定仕様化・閉じる・振り返り
    supervise.py design-glossary --mode M --root . --out <候補の語.md>
        # 設計の計画の入口: 用語集が揃えば何もしない。無ければ init と candidates を打ってコミットし、候補の語を書く
    supervise.py queue <plan.json>... [--max 3] [--then <plan.json>...]... [--done <パス>]
        # 空いた枠へ順に流す。作業ツリーは起動の前に 1 本ずつ作る。--then の計画は前の計画がすべて完了のときだけ
        # 続けて流す（実装の queue の後の配布など）。--then を繰り返すとステージになり、ステージは前のステージがすべて完了のときだけ
        # 流れる。終わると結果の JSON を --done（省けば最初の計画の
        # <計画>-state/queue-done.json）へ書く。始めに流す計画の一覧を done の隣（<done>.plans.json）へ書く
    supervise.py wait <done のパス> [--timeout 秒] [--poll 秒]
        # queue の終わり（done）か、queue が流す計画の attention の行まで待つ。出力は要約の 1 行と結果の JSON。
        # 終了コード: done = 0 / attention = 20 / 上限 = 3。attention の後にもう一度打つと、その続きから待つ
    supervise.py note <引継ぎ文書.md> --report <report.md> [--next 次の欄] [--section 見出しの語]
    supervise.py sync-check [--root DIR] [--commit]   # 宣言した同期とチェック（.ndf/supervise.json の sync_checks）
    supervise.py example            # 計画の例を出す

new <種別> --help は、その種別の書き出すステップの並び・要る宣言・引数だけを出す。
new / queue / wait / note / sync-check の結果は lib/step_result.py の形の 1 行の JSON（status を見る）。


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
from supervise_lib import commands, mission, new_args, queue, templates  # noqa: E402
from supervise_lib.decl import DeclError, apply_decls  # noqa: E402
from supervise_lib.plan import EXAMPLE  # noqa: E402
from step_result import emit  # noqa: E402  supervise_lib が lib/ を sys.path へ足す

MAIN_EPILOG = """フェーズごとの種別（new <種別> --help で、書き出すステップの並び・要る設定・引数を出す）:
  フェーズ                                 種別
  設計 → 承認ゲート 1 → 実装 → 検査（ミッション）  new mission（リリースの形に雛形があればリリースまで）
  実装（1 課題の Pull Request）              new impl
  即時修正（worker の実装なしで Pull Request）  new fix
  検査（1 本の Pull Request か前回からの差分）   new check
  リリース（開発版か本番）                   new release
  ミッションの終わり                         new close
書き出したプランは run で 1 本、queue で並べて流し、wait で終わりか attention まで待つ。"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog=MAIN_EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="プランを 1 本流す",
                       description="プランを 1 本流す。ステップを順に進め、報告を標準出力へ出す"
                                   "（終了コード: 完了か関門 = 0 / それ以外 = 3）",
                       epilog="状態は --state-dir（省けば <プラン>-state/）に progress.jsonl・報告・ステップの出力として残る。"
                              "途中で止まったら --from <ステップの id> で続きから流す")
    r.add_argument("plan", help="プランの JSON")
    r.add_argument("--state-dir", help="状態の置き場（既定は <プラン>-state/）")
    r.add_argument("--from", dest="start", help="このステップから始める（途中から再開するとき）")
    r.add_argument("--slow", action="append", default=[], metavar="K=V",
                   help="遅れの見張りの設定を上書きする（計画と .ndf/supervise.json の slow より先に効く。繰り返せる）")
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
    new_args.add_new_parsers(sub, MAIN_EPILOG)
    q = sub.add_parser("queue", help="プランを同時に --max 本まで順に流す",
                       description="プランを同時に --max 本まで順に流す。空いた枠へ順に流し、worktree は起動の前に 1 本ずつ作る",
                       epilog="--then のプランは前のプランがすべて完了のときだけ続けて流す（実装の queue の後のリリースなど）。"
                              "--then を繰り返すとステージになる。終わると結果の JSON を --done へ書き、始めに流すプランの"
                              "一覧を <done>.plans.json へ書く")
    q.add_argument("plans", nargs="+", help="流すプランの JSON")
    q.add_argument("--max", type=int, default=3, help="同時に流す本数（既定 3）")
    q.add_argument("--poll", type=float, default=5.0, help="終わりを見る間隔（秒）")
    q.add_argument("--then", nargs="+", action="append", default=[], metavar="PLAN",
                   help="前の計画がすべて完了したときだけ続けて流す計画（例: 配布の計画）。繰り返すとステージになり、"
                        "ステージは前のすべてのステージが完了のときだけ流れる")
    q.add_argument("--done", help="終わったときに結果の JSON を書く所（省けば最初の計画の状態ディレクトリの "
                                  "queue-done.json）。待つ側は wait <このパス> で待つ")
    w = sub.add_parser("wait", help="queue の終わりか attention の行まで待つ（done = 0 / attention = 20 / 上限 = 3）",
                       description="queue の終わり（done）か、queue が流すプランの attention の行まで待つ。"
                                   "出力は要約の 1 行と結果の JSON",
                       epilog="終了コード: done = 0 / attention = 20 / 上限 = 3。attention の後にもう一度打つと、その続きから待つ")
    w.add_argument("done", help="queue の --done のパス（省いた queue なら <最初の計画>-state/queue-done.json）")
    w.add_argument("--timeout", type=float, default=10800.0, help="待つ上限（秒）")
    w.add_argument("--poll", type=float, default=5.0, help="見る間隔（秒）")
    g = sub.add_parser("design-glossary", help="設計のプランの入口: 用語集が無ければ worktree の中で起こしてコミットする",
                       description="glossary.py gate が通れば何もしない。宣言か用語集が無ければ init と candidates を打ち、"
                                   "起こしたファイルをコミットし、候補の語を --out へ書く（pr のステップが PR 本文へ足す）")
    g.add_argument("--mode", default="standard", help="モード（設計の工程の入口で用語集を見るモードか）")
    g.add_argument("--root", default=".", help="worktree の根")
    g.add_argument("--out", required=True, help="候補の語を書く Markdown のパス")
    t = sub.add_parser("note", help="報告から引継ぎ文書の表へ 1 行を足す")
    t.add_argument("doc")
    t.add_argument("--report", required=True)
    t.add_argument("--next", default="")
    t.add_argument("--section", default="今の会話の進み")
    c = sub.add_parser("sync-check", help="宣言した同期とチェック（.ndf/supervise.json の sync_checks）")
    c.add_argument("--root", default=".")
    c.add_argument("--commit", action="store_true", help="同期で変わったファイルをコミットする")
    a = ap.parse_args()
    if a.cmd == "new":
        new_args.fill_new_defaults(a)
    if a.cmd == "design-glossary":
        emit(*commands.cmd_design_glossary(a.root, a.mode, a.out))
    if a.cmd == "example":
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "new" and a.kind in ("mission", "close"):
        new_args.check_mission(ap, a)
        try:
            if a.kind == "close":
                apply_decls(a)
                emit(mission.cmd_new_mission(a, mission.close_waves(a)))
            apply_decls(a)
            res = mission.cmd_new_mission(a)
            emit(res, 1 if res["status"] == "stopped" else None)
        except DeclError as e:
            ap.error(str(e))
    if a.cmd == "new":
        new_args.check_new(ap, a)
        try:
            apply_decls(a)
            emit(templates.cmd_new(a))
        except DeclError as e:
            ap.error(str(e))
    if a.cmd == "queue":
        emit(queue.cmd_queue(a.plans, max(1, a.max), a.poll, a.then, a.done))
    if a.cmd == "wait":
        summary, res, code = queue.cmd_wait(a.done, a.timeout, a.poll)
        print(summary, flush=True)
        emit(res, code)
    if a.cmd == "note":
        emit(commands.cmd_note(a.doc, a.report, a.next, a.section))
    if a.cmd == "sync-check":
        emit(commands.sync_check(a.root, a.commit))
    if a.cmd == "history":
        emit(commands.cmd_history_import(a.progress, a.history))
    if a.cmd == "expected":
        emit(*commands.cmd_expected(a.plan, a.history, a.slow))
    return commands.cmd_run(a.plan, a.state_dir, a.slow, a.start)


if __name__ == "__main__":
    sys.exit(main())
