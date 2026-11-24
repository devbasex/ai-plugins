# 用語集: NDF のプランの実行（`ndf-supervise`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

supervise.py がプランをキューで流し、ステップを進めて結果を残すときの語。プラン・ステップ・ステージ・上限・進捗ログ

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| パイプライン | `pipeline` | キューが `--then` でつないだプランの列（実装 → 検査 → コードレビュー → 開発版 → 本番）。列の 1 つ分がステージ | チェイン | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| プラン | `plan` | `supervise.py` が流す 1 本の JSON（`plan.json`）。フェーズの手順をステップの列として持つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| キュー | `queue` | プランを空いたスロットへ順に流す `supervise.py queue`。終わると結果を done へ書く | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| キューの続き | — | `supervise.py queue --resume <done>`。前の起動のステージの並びを読み、完了していないプランを終えたステップから続け、後続のステージも流す | — | — | `docs/specifications/ndf-supervise-queue-resume-and-gate-evidence.md` |
| 実行条件 | — | プランを流す前に打つコマンド。`skip_code` を返せば worktree を作らずに完了とする | 実行の条件 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 進捗ログ | `progress` | プランの実行中に 1 行 1 つの JSON で追記する記録（`progress.jsonl`） | 途中の報告 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 設定を探す木 | — | supervise.py new が .ndf/ の設定を探すディレクトリの並び。--worktree のパス → --repo → --worktree のパスの /.worktrees/ より前 → 今のディレクトリの順。既定ブランチは、この並びで最初に存在する git の木から解く | — | — | — |
| ステップ | `step` | プランの steps の 1 要素。型は run / work / drive / judge / pr の 5 つ | 段（単独） | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ステージ | `pipeline_stage` | パイプラインの中のプランのグループ 1 つ。中はキューで並列に流し、前のステージがすべて完了したときだけ次のステージが流れる。new sprint はステージごとにプランを書き出す（設計・設計承認ゲート・実装・検査・開発版・本番） | 段（単独） | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| resume | `resume` | new sprint のマニフェストで then_of のステージが持つ、そのステージから最後までを流す queue のコマンド。承認ゲートで止まった後に続きを流す | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| judge のステップ | — | 結果ファイルと規則の抜粋だけを渡し、次のステップを LLM に決めさせるステップ。Tool を持たない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 決定 | — | judge のステップが返す、次に取る手 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| step / alive / worker / attention | — | 進捗ログの行の種類。ステップの切り替わり・動きの無い間の生存・worker の進み・conductor の判断が要る出来事（止まった・承認ゲート・同じ失敗の繰り返し・judge のステップで stop が出そう） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| done | — | キューが終わったときに書く結果の JSON。wait は done か attention の行まで待つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 資源のタグ | `resource_tag` | ステップが使う、共有する外部のスロットの名前（今は graphql だけ） | — | — | — |
| 資源のクォータ | `resource_limit` | 資源のタグごとの、同時に流せるステップの本数の上限（.ndf/supervise.json の queue.resources） | — | — | — |
| 重なりの組 | `overlap_pair` | 同じステージの 2 本のプランで、触るファイルに等しいファイルの設定を持つか、同じ共有の一覧に当たるもの。ディレクトリの設定は判定に使わない。同時には流さない | — | — | `docs/specifications/ndf-supervise-queue-overlap-by-file.md` |
| 偽の重なり | — | 重なりの組と判定された 2 本のプランのうち、両方の実装の PR が実際に変えたファイルに共通するものが 1 つも無い組 | — | — | `docs/specifications/ndf-supervise-queue-overlap-by-file.md` |
| ディレクトリの設定 | — | 触るファイルの 1 行で、末尾が / のもの。設計の結果のステージは作業場所の既存のディレクトリをこの形に揃える。重なりの組の判定と除外の行に使わない | — | — | `docs/specifications/ndf-supervise-queue-overlap-by-file.md` |
| 共有の一覧 | `shared_list` | 複数のプランが同じ行の並びへ書き足すファイル（索引など）。.ndf/supervise.json の queue.shared に書く | — | — | — |
| 除外の行 | `concurrent_rule` | queue が実装の担当の指示文の末尾に足す 1 行。担当を起動する時点で走っている他のプランの触るファイルのうち、ディレクトリでないものを変えないよう求める | — | — | `docs/specifications/ndf-supervise-queue-concurrent-files-and-prerequisites.md` |
| 前提のプラン | `after` | プランの JSON の `前提のプラン` に名前で挙げたプラン。その結果が完了でなければ、queue は挙げた側のプランを流さない | — | — | `docs/specifications/ndf-supervise-queue-concurrent-files-and-prerequisites.md` |
| 実装の上限 | `impl_timeout` | プランの impl のステップ（worker の実装）の経過時間の上限（秒）。.ndf/supervise.json の impl_timeout か supervise.py new の --impl-timeout に書いたときだけ効き、書かなければ無い | — | — | `docs/specifications/ndf-supervise-step-budgets.md` |
| 状態の保存先 | — | `supervise.py run` の `--state-dir`（省けば `<プラン>-state/`）。`progress.jsonl`・`report.md`・`state.json` を置き、`--from` の再開が前の実行の記録を読む | — | — | `docs/specifications/ndf-supervise-resume-pr.md` |
| ステップの上限 | — | プランの 上限。1 本の実行で流すステップの数の上限で、超えると「止まった」で終わる。ループの歯止め | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 修正のループ | — | 検査のステップが落ちて修正の work へ行き、同じ検査へ戻ってくる遷移の輪 | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 遅れの見張り | `slow` | ステップが想定の時間を超えたときに一次の調査を流し、待つ・打ち切る・再試行するを決める仕組み | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 前の実行 | — | run --from で再開したとき、今の実行より前に同じプランを流した実行。--from を付けない最後の実行の始まりより後の記録を指す | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 失敗の遷移 | — | ステップが落ちたときのルート。on_fail・on_section_missing・on_exit の 0 以外の終了コードのルート | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| ループを通らない最長の経路 | — | プランの並びで後ろのステップへ行く遷移だけをたどった、最も長い経路。数はステップの数で、流し始めるステップを含む | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| worker のセッションの記録 | — | 会話を残す claude -p（Skill を回す work のステップ）が CLAUDE_CONFIG_DIR の projects の下へ書き足していく 1 本の JSONL。遅れの見張りが大きさの伸びを進みに数える | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 戻りの輪 | — | 修正の work を含まず、kind が「戻り」のステップを含む遷移の輪。戻りの上限の回数だけ回るものとして上限に数える | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| ベースブランチのマージ | — | ベースブランチの HEAD を作業ブランチへマージのコミットでマージ、衝突を解いてから合成の上で検査を続けるプランのステップ。用語集だけの衝突は用語集のマージで解き、ほかは修正の worker へ回す | — | — | `docs/specifications/ndf-supervise-base-sync.md` |
| 補正 | — | conductor がテンプレートの書いたプランへ手で足した指示や値。プランを書き直す側は消さない | — | — | `docs/specifications/ndf-supervise-impl-prompt-format-and-transient-failures.md` |
| 整形 | — | 規則を変えずにコードの形だけを揃えるコマンド。何を打つかはプロジェクトの設定が持つ | — | — | `docs/specifications/ndf-supervise-impl-prompt-format-and-transient-failures.md` |
| 一時的な失敗 | — | GitHub や git の呼び出しが 0 以外で終わり、打ち直せば通りうる失敗（5xx・接続の切断）。打ち直しても残れば、読めなかったとして止まる（#1941） | — | — | `docs/specifications/ndf-supervise-impl-prompt-format-and-transient-failures.md` |
| 再起動 | `resume_prompt` | supervise の full のステップで、作業の結果ファイルが無いまま応答が終わったとき、同じ会話（--resume）へ続けるよう送ること。3 回まで（#2337） | — | — | `docs/specifications/ndf-worker-report-prepare.md` |
| ステップの延長 | — | drive のステップの子孫（駆動と、駆動が起動した init）が自分の見込みの秒を延長のファイル（NDF_STEP_EXTEND_FILE の指すパス）へ書き、エンジンがその試行の締め切りを with_margin を足して後ろへずらすこと。前へはずらさない | — | — | `docs/specifications/ndf-supervise-step-budgets.md` |
| 仕上げのプラン | — | `supervise.py new close` が組むプラン（記録の `フェーズ` の値は「まとめ」）。確定仕様化・リリース後テスト・振り返り・課題を閉じる・棚卸しのステップを持つ（#2914） | — | — | `docs/specifications/ndf-sprint-close-plan-cost.md` |
| 走らなかったステップ | — | 起動の判定などの終了コードで飛ばされ、プランの実行で通らなかったステップ。フェーズレポートの「通ったステップ」の後に、ステップの id と飛ばした判定の要約を 1 行で並べる（#2914） | — | — | `docs/specifications/ndf-sprint-close-plan-cost.md` |
| プランの参照 | — | プランが値の代わりに持つ、値の取り先の名前（次に空いている開発版・課題のリリース記録を置いた PR・前のプランが出した PR・承認したコミットと照らすブランチの HEAD）。流す直前に実行の核が値へ解決する（#2344） | — | — | — |
| 解決の記録 | — | 1 回の実行がプランの参照ごとに解決した値と出所。実行の記録の行として残り、プランのファイルへは書き戻さない（#2344） | — | — | — |
| 承認時の値 | — | 承認ゲートで人か MVV 判定が承認資料で見た値（承認したコミット・バージョン）。流す直前に解決した値と照らし、食い違えば流さずに人へ戻す（#2344） | — | — | — |
| 解決のスナップショット | — | 流し直しでも同じ値を使う参照（バージョン）の解決した値と決め直しの履歴を、プランの状態ディレクトリに残すファイル（`refs.json`）（#2344） | — | — | — |
| プランのハッシュ | — | プランの型で読んだ中身を、キーを並べ替えた JSON にして取った sha256。完了したプランが変わったかを測る（#2344） | — | — | — |
| プランの進行の記録 | — | supervise のプランのステップごとの終了コード・秒・ドルを 1 行ずつ残す記録（`<状態のディレクトリ>/progress.jsonl`）。開発ループのまとめのプランでは `.ndf/loop/<名>/lap-<番号>/close*/` の下にある（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| フェーズの結果 | — | supervise の 1 つのプランの実行の終わり方を結果 JSON の形で書いたファイル（状態ディレクトリの phase-result.json）。supervise.py run の終了コードはこの status から決まる（#2337） | — | — | `plugins/ndf/skills/development-workflow/references/agent-layers.md` |
| 無進捗の打ち切り | — | 実装のステップで、進みの観測が無進捗の許容時間を越えて途絶えたときだけ打ち切ること | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 無進捗の許容時間 | `impl_stall_seconds` | 実装のステップで、最後の進みの観測から無進捗の打ち切りまでに待つ秒。.ndf/supervise.json の impl_stall_seconds か supervise.py new の --impl-stall で決め、無ければ 1800 秒 | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 進みの観測 | — | 実装のステップの間に、会話の記録の伸び・進捗ログの行・コミットの増加のどれかを見つけること | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 負荷とメモリの値 | — | 進捗ログの行に載せるマシンの負荷の組。load average（1・5・15 分）、MemAvailable（MiB）、スワップの合計と空き（MiB）。進捗ログの行では host の項目に置く | — | — | `docs/specifications/ndf-supervise-progress-host-and-queue-waits.md` |
| マシンの上の pytest の数 | — | 進捗ログの行を書いた時点で、supervise と同じ PID の名前空間から見える pytest の本体のプロセスの数。起動の経路（supervise・AI の Bash・人のシェル）を区別しない。xdist の作業プロセスは別に数える | — | — | `docs/specifications/ndf-supervise-progress-host-and-queue-waits.md` |
| 待ちの理由 | — | プランが queue の中で待った原因。流れ始める前に解ける待ち（重なり（overlap）・plans・前提のプラン（deps）・計画の上限（plan_limit））と、流れている間にステップごとに起きる資源のクォータ（resource）の空きの待ちの 5 つ | — | — | `docs/specifications/ndf-supervise-progress-host-and-queue-waits.md` |
| 待ちの秒 | — | queue の結果のプランの項目に置く、待ちの理由ごとに待った秒（waits） | — | — | `docs/specifications/ndf-supervise-progress-host-and-queue-waits.md` |
| メモリの山 | — | 全体テストを流している間に、マシンの使用中のメモリ（合計 − MemAvailable）とスワップの使用量が取った最大の値 | — | — | `docs/specifications/ndf-supervise-progress-host-and-queue-waits.md` |
| 最後の検査 | — | スプリントの終わりに `supervise.py new close` が組む検査（計画名 `<スプリント>-final`）。確定仕様化の Pull Request をマージした後に流れ、スプリントに属する Pull Request のうち、どの検査もまだ見ていない差分を拾う | — | — | `docs/specifications/ndf-sprint-final-check-scope.md` |
| スプリントの検査 | — | `supervise.py new sprint` が組む、スプリントの Pull Request を対象にした検査。マージの後に検査のマーカーを進める | — | — | `docs/specifications/ndf-sprint-final-check-scope.md` |
| 検査のマーカー | — | 前回の検査が見終えた位置を持つ origin のブランチ。リファクタリングを含む検査のマーカー（`check-done/check`）とコードレビューのマーカー（`check-done/review`）の 2 本がある | — | — | `docs/specifications/ndf-sprint-final-check-scope.md` |
| 最後の検査の範囲 | — | 最後の検査が見る差分。スプリントに属する Pull Request のうち、検査済みでないもの。範囲が空か文書のファイルだけなら、最後の検査は流れない | — | — | `docs/specifications/ndf-sprint-final-check-scope.md` |
| スプリントに属する Pull Request | — | head のブランチの最後の区切りがスプリントの名前に等しい（`sprint/<名前>`・`spec/<名前>`）か、ブランチ名がスプリントの課題の番号を `issue-<番号>` の形で持つ Pull Request | — | — | `docs/specifications/ndf-sprint-final-check-scope.md` |
| まとめのステージ | — | `supervise.py new close` が組む `sprint.json` のステージのうち、仕上げのプランを流すステージ（ステージの名前は `まとめ`）。`pace: fast` / `normal` では先頭に置き、最後の検査・開発版・本番はその後ろに `then_of: まとめ` で続く | — | — | `docs/specifications/ndf-sprint-final-check-scope.md` |
| 最終の検査 | — | スプリントのまとめの先頭で流す検査（`<スプリント名>-final`）。変更が無ければ終了コード 3 で終わり、後続の開発版・本番・リリースのリクエストのプランは実行条件で飛ぶ | — | — | `docs/specifications/ndf-parallel-empty-requests-and-settled-gates.md` |
| 修正ステップ | — | 指摘や失敗を直すステップ（fix ステップ）。担う worker は修正用の worker | 直しのステップ、直しの worker | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
