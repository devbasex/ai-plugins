# 用語集: NDF の開発ループと MVV（`ndf-loop`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

開発ループがスプリントを続けて選び、pace と MVV 判定で承認ゲートを通すときの語。選定・予算・止まる条件・出し直しの指摘・MVV

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| レッドライン | — | MVV 判定が「従う」でも利用者の承認を省かない操作。NDF の共通原則の `C<番号>`（NDF が持つ）・プロジェクト MVV の `P<番号>`（プロジェクトが足す）・スプリント MVV の `R<番号>` の 3 層 | 越えない線 | — | `docs/specifications/ndf-design-ai-decisions-and-mvv-gate.md` |
| 流出不具合 | — | マージ済みの変更に見つかった不具合。直した Pull Request が触った領域を記録し、トリガーに数える | 逃げた不具合 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検査の記録 | `check_log` | check-trigger.py が検査の事象（eval・check・escape）を 1 行ずつ追記する jsonl（checks/<owner>__<repo>.jsonl）。トリガーの判定と stats が読む | — | — | `docs/specifications/ndf-ci-only-tests.md` |
| 重点領域 | — | `pace: fast` の領域のうち、触った Pull Request の点数を重くするもの | 共通層 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| pace | `pace` | モードとは別の軸で、工程をどう通すかを決める。ウォーターフォールで各工程を検証し人の承認を取る normal、normal の承認だけを MVV 判定にする auto、実践投入の中で検証しながら MVV で自動に進める fast の 3 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| pace: auto | `auto` | 進め方の 1 つ。工程は normal と同じで、設計承認ゲート・リリース承認ゲートだけを MVV 判定で自動にする。「従う」でレッドラインが無いときだけ通し、ほかは利用者へ戻す | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| 実践投入 | — | pace: fast の検証の場所。実装の Pull Request がベースブランチへ入り、承認前の試用（開発版のデプロイ・版上げの PR の head からのインストールなど）で使われること。fast の検査はその後にトリガーで通る | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| MVV | — | Mission / Vision / Value。プロジェクト MVV とスプリント MVV の 2 層があり、下位は上位の範囲で具体化する | — | — | `docs/specifications/ndf-mvv-current-and-stage-inputs.md` |
| MVV 判定 | `mvv_validation` | 承認ゲートの手前で、LLM が PR の本文と差分を NDF の共通原則・プロジェクト MVV と突き合わせる判定（ndf records mvv gate）。どの進め方でも、承認済みの MVV と PR の資料を取得できれば判定する。結果は開示範囲に合わせて表示し、PR のコメントには判定・理由・レッドライン・レビューの保留を載せ、承認資料では理由や保留の件数と全文の参照先を示す。fast・auto では設計承認ゲートを省くかの材料にもなる。結論は従う・従わない・判定できないのどれかで、判定結果を得られなければ verdict が null になり、判定できなかった理由の種類を stopped_by に置く | 照合 | — | `docs/specifications/ndf-design-ai-decisions-and-mvv-gate.md` |
| レビューの保留 | — | 対象の PR の cross-review の記録（mvv-validation.jsonl の mvv-review の行）に残る、直っていないレッドラインの指摘と、現在のプロジェクト MVV のバージョンで修正を見送り、利用者の判断を待っているレビュー指摘。1 件でもあれば承認ゲートを省かない。MVV 判定の出力 holds に載る | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| 助言の MVV 判定 | — | 承認ゲートの記録（by: mvv）は書かないが、mvv-gate.jsonl へ判定の行（pace 付き）を書き、判定・理由・根拠の項目を承認資料へ載せる MVV 判定（mvv-gate.py check --advise）。normal の承認ゲートの前に走り、承認するのは人である | — | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| 上位の原則 | — | 「人を守り、人の発展を支える」。NDF の共通原則の最上位の 1 文 | — | — | `plugins/ndf/skills/development-workflow/references/project-mvv.md` |
| プロジェクト MVV | — | プロジェクト全体の Mission / Vision / Value と固有の必ず承認が要る操作（`P<番号>`）。`.ndf/` に定義し、利用者が承認する。判断の基準の上位 | — | — | `docs/specifications/ndf-mvv-current-and-stage-inputs.md` |
| MVV のバージョン | — | プロジェクト MVV の承認のたびに 1 ずつ上がる番号。改訂の理由と前のバージョンとの差分を伴う | — | — | `docs/specifications/ndf-mvv-current-and-stage-inputs.md` |
| MVV のスナップショット | — | ndf records mvv current と ndf cross-review run が、ベースブランチで承認された MVV の本文と NDF の共通原則を保存したファイル。工程の担当と cross-review の相手役・修正担当は、作業場所の .ndf/mvv.md でなくこのファイルを読む | — | — | `plugins/ndf/skills/development-workflow/references/project-mvv.md` |
| MVV の改訂 | — | プロジェクト MVV の本文を変え、利用者の承認で新しいバージョンにすること | — | — | `docs/specifications/ndf-sprint-mvv-vet-reuse.md` |
| 根拠の項目 | — | 判断の記録と設計の決定の記録に残す MVV の項目の番号（Mission / Vision / Value 3 / C4 / P1 / R2）。スプリント MVV の項目は頭に「スプリント」を付ける（スプリント Value 4。R の番号はそのまま）。MVV が無ければ「MVV なし」、返されなければ「根拠なし」 | — | — | `plugins/ndf/skills/design/references/design-template.md` |
| 覆し | — | 承認ゲートで人の回答が直前の MVV 判定と食い違ったこと。「反する疑い」「判定できない」を人が通すと override_pass、「従う」を人が変更を要求すると override_reject。MVV の改訂の兆候に数える | — | — | `docs/specifications/ndf-gate-rejections-and-filer-assumptions.md` |
| NDF の共通原則 | — | NDF を使うすべてのプロジェクトに効く原則。NDF が持ち、利用側は上書きできない。上位の原則・優先順位・AI の行動の 2 択・判断の記録・人と AI の対話・必ず承認が要る操作 C1〜C8 を含む | — | — | `docs/specifications/ndf-mvv-current-and-stage-inputs.md` |
| 改訂の兆候 | — | 人が AI の判断を覆した回数（「従う」を退けた・「反する疑い」を通した）・「判定できない」の回数・流出不具合の件数。現行のバージョンのもとで数え、設定の閾値を超えたら改訂を提案する | — | — | `docs/specifications/ndf-mvv-current-and-stage-inputs.md` |
| トリガー | `trigger` | fast でリファクタリングとコードレビューを流す条件。点数・行数・流出不具合・経過時間・最終の 5 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 開発ループ | — | スプリントを 1 つ終えるたびに、棚卸しの結果から次のスプリントを選んで続けて始める進め方。引数で切り替え、止まる条件に当たるまで繰り返す | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| ループの 1 回 | — | 開発ループの 1 回分。次のスプリントの選定・スプリント 1 つ・棚卸しと止まる条件の判定からなり、スプリントと 1 対 1 | 周回 | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 選定 | — | 開発ループの 1 回で、実装する課題を棚卸しの順位と記録から選ぶ処理。選ばなかった課題には落選の理由を残す | — | — | `docs/specifications/ndf-loop-select-prerequisites.md` |
| 前提の課題 | — | ある課題が依存する、別の open の課題。棚卸しが記録した依存と並列の組の依存から順位の算出が合成した辺の先にある。閉じるまで、依存する課題は選定で選ばれない | — | — | `docs/specifications/ndf-loop-select-prerequisites.md` |
| ループの予算 | — | 開発ループが使ってよい量の上限。ループの回数・費用・経過時間で数える。どの単位も設定に数を書いたときだけ効き、書かない・null は無制限である | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 止まる条件 | — | 開発ループを止める、機械で判定できる条件。予算を超えた・同じ失敗が続いた・着手できる課題が無い・MVV の外の課題しか残らない・利用者が止めた | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| ループの記録 | — | 開発ループの 1 回ごとの選定・承認ゲートの判定・バージョン・費用と秒・止まる条件の判定を持つ記録。メインディレクトリの .ndf/loop/<名>/loop.json が SSOT で、laps.md はそこから描画する | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| スプリント MVV | — | スプリント単位の MVV。プロジェクト MVV の範囲での具体化 | ミッション MVV | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 予算の既定 | — | 開発ループの予算の単位ごとの既定の値。リポジトリの .ndf/pace.json の loop.budget の値で、設定が無い単位は無制限。既定へ戻す指示を受けた単位はこの値へ戻る | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 既定へ戻す指示 | — | 開発ループの予算の単位に、数の代わりに渡す値 default（`--budget-usd default`）。NDF が反映の時点の予算の既定でその単位を上書きする | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 予算の履歴 | — | 開発ループの記録が持つ、予算の上書きの記録（`budget_history`）。予算が変わったときだけ、時刻・前の値・後の値の 1 行を足す | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 人の判断待ちの行 | — | 開発ループの記録が持つ、人の回答（承認・変更要求・直して出し直す）を待つ課題の行（`waiting`）。`sprint-state.py loop answer` で回答を書く。着手の前の課題の状態を指す「人の判断待ち」とは別の語 | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 出し直しの指摘 | — | 設計承認ゲートで人が「直して出し直す」と回答したときに `--note` に渡したファイルの全文。レビューの担当が書く指摘ファイルとは別のもの | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 出し直しの指摘の履歴 | — | 課題の本文に置く、出し直しの指摘を回ごとに追記するセクション。人の判断待ちのセクションを書き直しても消えない | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 指摘への対応の表 | — | 出し直しの設計 PR の本文に置く表。出し直しの指摘の項目ごとに、直した / 直せない、直した箇所か直せない理由を 1 行で持つ | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 設計のプラン | — | 開発ループの 1 回が課題ごとに組む、設計のフェーズのプラン（`sprint/<番号>-design-<課題>.json`）。要求・設計・レビュー・設計承認ゲートのステップを持つ | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 回答の種類 | — | 人が承認ゲートや質問に返す回答の区分。承認・直して出し直す・やらない・予算・止めるの 5 つ | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 自動の出し直し | — | 出し直しの設計が出し直しの指摘の項目を指摘への対応の表に示していないとき、人へ問わずに開発ループが自分で付ける「直して出し直す」の回答。届いていない項目を名指しする。人の出し直しの 1 回につき 1 回まで | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 出し直しの指摘の入力 | — | 開発ループがループの 1 回ごとに、同じリポジトリのすべてのループの記録から、課題の全回の出し直しの指摘を書き出したファイル。回はループの名前と回数の組で識別する。設計のプラン・MVV 判定・指摘への対応の表の突き合わせが読む | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| マージのコミット | — | 起点ブランチを設計 PR のブランチへマージするマージコミット。これだけで先頭が動いた設計 PR は、承認のリクエストを送り直さない | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| ループの 1 回の費用の内訳 | — | ループの 1 回の費用を、プランの claude -p・同じプランの中の別の起動・USD に換算できない起動に分けた記録。ループの 1 回の費用の和は、使用量の使用量ログの和とスプリントの報告の和の大きい方とする | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 実行環境の調査 | — | 動く場所・CPU のコア数・メモリ・同時に動くレーンとプランの数を調べ、ループの 1 回で選ぶ課題の数の候補を出す工程 | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| MVV 判定の入力 | — | 1 回の MVV 判定で LLM へ渡す全体。判定の指示・共通原則・プロジェクト MVV・判定の決まり・本文の種類の名前・MVV 判定する本文からなる。1 字でも違えば別の入力である | — | — | `docs/specifications/ndf-sprint-mvv-vet-reuse.md` |
| MVV 判定の記録 | — | MVV 判定のたびに 1 行を足す JSONL のファイル（project-mvv.jsonl）。機械ごとに 1 つある | — | — | `docs/specifications/ndf-sprint-mvv-vet-reuse.md` |
| 触る場所の見込み | — | 設計の前に、課題の変更が触ると見込んだリポジトリの中のパスの集合。確定した触るファイルは設計の結果が持つ | — | — | `docs/specifications/ndf-loop-selection-touch-estimate.md` |
| 見込みの出所 | — | 触る場所の見込みをどこから出したか（設計の結果・課題グループの表・課題の本文・見込みなし） | — | — | `docs/specifications/ndf-loop-selection-touch-estimate.md` |
| 同時に動く本数 | — | スプリントの実装のステージの経過のあいだに、同時に動いていた実装のプランの本数の時間平均 | — | — | `docs/specifications/ndf-loop-selection-touch-estimate.md` |
| 順位の逆転 | — | 開発ループの選定で、順位の高い課題を触る場所の見込みの重なりを理由に後ろへ回し、順位の低い課題を先に選ぶこと | — | — | `docs/specifications/ndf-loop-selection-touch-estimate.md` |
| 逆転の幅 | — | 開発ループの選定で、残りのスロットの数を超えて並べ替えの対象にする候補の件数。0 なら順位の逆転は起きない | — | — | `docs/specifications/ndf-loop-selection-touch-estimate.md` |
