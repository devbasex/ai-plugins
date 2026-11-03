# 構成メモ: AI が回すソフトウェア工場

台本（[script.md](script.md)）を、台本の 36 ページと、章扉 3 つ・中締めの扉 1 つのスライドにした構成。各ページの読み上げはスピーカーノートに入る。

2026-10-31 の版（[../2026-10-31-neural-development-factory/](../2026-10-31-neural-development-factory/README.md)）を、NDF は 2026-11-03 時点の実装、Neural Development Factory は同じ時点の Issue の構想で更新した。足したページは NDF の開発ループ（20・21）と、Factory のループの画面・オーガナイザー・UX の方針（32〜34）である。各ページの読み上げはスピーカーノートに入る。

## 構成

キーメッセージ「人は『何を作るか』と『出してよいか』を決める。あとは工場が回す」から逆算した。Software Factory の
4 つの部品（標準工程・品質ゲート・計測・改善）を冒頭で示し、NDF のページは部品の順（標準工程 6〜13 → 品質ゲート 14〜15 → 止めずに回す 16〜18 → 改善 19）に並べた。3 つ目の計測は CLI では弱く、後半の Neural Development Factory（一覧と詳細で見える）が埋める。これを前半から後半へ渡す伏線にした。各ページの読み上げは、次のページで話すことへの橋渡しで終える。
後半は「CLI では人がラインに張り付く」という課題から Neural Development Factory を導き、仕組み・実行の形態・画面・
現在地の順に置いた。2026-10-17 の資料から、フォークして育てる話（旧 19〜21）と特徴的な機能の表（旧 16）は外した。

表の番号は台本の番号で、章扉は数えない。

| # | 見出し | 役割 | 根拠 |
| ---: | --- | --- | --- |
| 1 | 表紙 | — | — |
| 2 | Software Factory とは | 入力（Issue）→ 工場（4 工程）→ 出力の図と、人が決める 2 つ | — |
| 3 | 「工場」は新しい言葉ではない | 1970 年前後の日本のソフトウェア工場と、2026 年の AI エージェントの Software Factory の対比。注記の 3 つの出典にリンクを張った | Cusumano『Japan's Software Factories』(1991)、StrongDM の Software Factory（2026-02）、Dan Shapiro の 5 段階 |
| 4 | 職人型から工場型へ | 品質・速さ・知識・人の居場所の 4 行の対比表 | — |
| 5 | 工場に必要な 4 つの部品 | 標準工程・品質ゲート・計測・改善の 4 区画 | — |
| 6 | 指示書だけでは、ラインは回らない | CLAUDE.md への追記の 4 つの壁 | 旧 3 |
| 7 | 実務はスクリプト、AI は規定と監視 | スクリプト（実務・196 本）と Skill（規定と監視）・hook（強制）・プラグイン（配る先は Claude Code・Codex、Kiro・agy は相手役） | 本数は `plugins/ndf/scripts` 直下と `plugins/ndf/skills` の .py・.sh（テストを除く。10-31 の 186 本も同じ数え方で再現する）。配布先は CHANGELOG 10.27.5（#2306） |
| 8 | NDF: 工場のラインをプラグインに | リリース 168 回・Skill 49 個・配布先 2・導入 10+ | 正式版の版数の数（plugin.json の version の git の履歴、接尾辞つきの開発版を除いた数）、`plugins/ndf/manifests/claude-skills.txt` の行数、導入先は旧 5 |
| 9 | Issue をまとめて渡せば、並列に進む | マイルストーン → グループ → スプリント → リリースの図と 2 回の承認 | 旧 6 |
| 10 | 変更の大きさで、踏む工程を変える | 5 つのモードの対象と通る工程の表 | `development-workflow` の `references/workflow-modes.md` |
| 11 | 機能追加は、この流れで 1 周 | standard の工程を 3 行で | 旧 8 |
| 12 | 要件定義・テストファースト: 実績のある技法を組み込む | DDD（ユビキタス言語とドメインモデル）・TDD・Agile の 3 列 | 旧 9 |
| 13 | 構成の見直し: コードの可読性から、日本語の可読性の時代へ | AI 以前にコードの可読性へ向けたこだわりを、日本語の可読性へ向ける時代という主張。文章の指針の年表（逆ピラミッド・IMRAD・Information Mapping・BLUF・ミニマリズム・日本語）と、組み直した 3 本の前後の実測、document-rebuild の 4 手順。11 の「構成の見直し」を取り上げる。開発技法の次に、文章にも技法があると続ける | `plugins/ndf/skills/document-rebuild/SKILL.md`、#391（参考文献と 3 本の実測 #389・#390・#339）。年代は Wikipedia の各項目で確かめ、確かめられなかった BLUF と日本語の 2 つは年を書かない |
| 14 | クロスレビュー・リファクタ: 複数の AI で見て直す | cross-review と cross-refactoring の流れ | 旧 11 |
| 15 | 完了判定: 「完了」も「無い」も証拠で | quality-gates と investigation-rules（検査工程として） | 旧 14 |
| 16 | 3 層のエージェントで止まらない進行 | conductor・supervisor・worker と再開（DBOS で耐久の記録）・切り替え（切り替えは次のページへ） | 旧 12 |
| 17 | NDF Relay: コンテキストを溜めずに走り続ける | コンテキストが膨らむ 3 つの害と実測（平均 410k Token）、区切りで起動し直す流れ、守り 3 つ。呼び名 NDF Relay は発表用（実体は `plugins/ndf/scripts/relay.py`）。「Agent Relay」は同名の製品があるため避けた | `development-workflow` の `references/context-window.md`（#827 の実測）・`references/relay.md`、`docs/specifications/ndf-relay-segment-restart.md` |
| 18 | 迷ったら MVV で判断する | MVV と必ず人が承認する操作 | 旧 13 |
| 19 | 振り返り・棚卸し: 詰まりを工程へ戻して直す | 改善の 3 つの Skill（一覧の棚卸を含む）と棚卸しの数値 | 旧 15 |
| 20 | 開発ループ: 終わったら次のスプリントを自分で選ぶ | 1 周の流れ、人に戻る 2 か所、止まる条件。実測は 5 本で計 17 周 | `development-workflow` の `references/loop.md`、手元の `.ndf/loop/*/loop.json`（2026-11-03） |
| 21 | 複数のループを並べて、1 か所で見張る | `relay.py ls` の例と、spawn・ls・attach・stop / restart | `references/relay.md` の「複数のループを並べる」、CHANGELOG 10.27.14（#2390） |
| 22 | 事例: ほかのプロジェクトでも動く | 4 つのプロジェクトの結果の表 | 旧 17・18（2026-10-17 の実測） |
| 23 | CLI の工場には、人が張り付く | CLI の 3 つの課題と Web での解き方の対 | #2168 の背景 |
| 24 | Neural Development Factory の構想 | 登録・始める・見る・承認するの 4 段と 3 つの約束 | #2168 の方針 |
| 25 | 仕組み: 承認を待つ間はラインを止める | 画面・API・実行ジョブ・GitHub・進捗の事象の図と、承認待ちでジョブを終える流れ | `docs/specifications/ndf-factory-service-boundary.md` |
| 26 | AI をどう動かすかは 3 つから選ぶ | 実行の形態 3 つの表とプラン | #2168・#2189・`docs/specifications/ndf-factory-accounts-and-plans.md` |
| 27 | 画面: スプリント一覧 | モックの描き直しと注記 3 つ | 画面モック「スプリント一覧」 |
| 28 | 画面: スプリント詳細 | 同上 | 画面モック「スプリント詳細」・#2172 |
| 29 | 画面: 承認ゲート | 同上 | 画面モック「承認ゲートで承認する」・#2173 |
| 30 | 画面: 判断（AI が決められない問い） | 同上 | 画面モック「判断に答える」・#2206 |
| 31 | 画面: スプリントを始める | 同上 | 画面モック「スプリントを始める」 |
| 32 | 画面: 開発ループ | ループの一覧のモック（周回・予算・利用上限の待ち）と注記 3 つ | #2347、CHANGELOG 10.27.14（#2393）、`docs/specifications/ndf-factory-dev-loops.md` |
| 33 | 並べても、本番の配布は 1 本にまとめる | オーガナイザーの図と、取り合いを防ぐ 4 つの決まり | #2347（配布済み）・#2439（設計を承認、実装前） |
| 34 | 画面を先に作り、AI からも同じ操作ができる | UX の 5 つの方針と、実装済み 12 画面・予定 4 画面 | #2168 の方針の表・#2382・#2383・#2384、`factory/web/src/screens.ts` |
| 35 | 現在地: 子 Issue で順に作っている | 子 Issue 12 行と状態 | #2168 の子課題の表、CHANGELOG とタグ（2026-11-03） |
| 36 | まとめ: 人は 2 つだけ決める | Software Factory + NDF = Neural Development Factory の式と、リポジトリの URL | — |

## 章立て

| 章扉 | 名前 | 一言 | ページ |
| --- | --- | --- | --- |
| 1 | Software Factory という考え方 | 人は「何を作るか」と「出してよいか」を決める | 2〜5 |
| 2 | NDF — 工場のラインをプラグインに | Claude Code・Codex で動く開発ワークフロー | 6〜22 |
| — | ここまでは実践、ここからは構想（中締め） | 章番号を持たない扉。前半の NDF は実装済みで実際に使っている（COMBAT PROVEN）、後半は構想（VISION）と分ける | 22 と 23 の間 |
| 3 | Neural Development Factory | 工場を Web へ。CLI の前にいなくても回る | 23〜36 |
