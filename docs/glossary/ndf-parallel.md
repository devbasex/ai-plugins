# 用語集: NDF の並列の計画（`ndf-parallel`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

並列の計画がレーンを同時に進め、オーガナイザーがレーンのリクエストへ答え、エピックを進めるときの語。レーン・一時レーン・計画のジャーナル・マシンの上限

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 実行計画 | — | スプリントの課題を、バンドル・工程ごとの依存・触る箇所・着手できる時点で並べた表。オーケストレーターが持つ | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| バンドル | `bundle` | 1 本の設計 Pull Request で決める課題の集合 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 課題グループ | — | マイルストーンの説明に書く、触る場所の見込みと依存で分けた課題の集合。実行計画のバンドルの初期値 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 変更重複 | — | 2 つの Pull Request が同じファイルを触ること。セクション（見出し）・関数の単位で程度を分ける | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 並行度 | — | 対象の Pull Request のうち、2 本以上が同時に開いていた時間の割合 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 予備メモリ | — | 同じ VM に常駐する他のプロセスの揺れのために空けておくメモリ。既に動いているオーケストレーターの本体と担当は MemAvailable から引かれているため含めない | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| エピック | `epic` | 複数のスプリント・レーン・バージョンにまたがる作業のまとまり。並列の計画 1 つか、続く複数の並列の計画で進める | — | — | — |
| エピックのアーキテクチャ設計書 | — | エピックの全レーンが設計と実装の前に読む、1 か所に置く文書（Architecture Doc）。用語・データ構造と状態の保存先・入出力のコントラクト・設定ファイルと環境変数などの必須セクションを持つ。Issue ごとの設計文書とは別の文書 | — | — | — |
| エピックの完了条件 | — | エピックが終わったと判定する、観測できる項目の一覧。定義はアーキテクチャ設計書の表に置き、並列の計画は番号で指す。status が進捗を出す。Issue ごとの条件は受け入れ条件と呼び分ける | — | — | — |
| 規模の判定 | — | Issue かプロンプトで渡された作業をスプリント 1 つで進めるか、エピックとして進めるかを決める判定。モード判定の手前で行い、判定結果に規模の行を出す | — | — | — |
| エピックのフロー | — | 規模の判定がエピックと出た作業が進む工程。アーキテクチャ設計書のガイドを読み、ndf epic init から始める。プログラムのエントリポイントとは呼び分ける | — | — | — |
| アーキテクチャ設計書の変更通知 | — | エピックのアーキテクチャ設計書の変更を、触るファイルの重なるレーンへ、計画のワークフロー状態（用語集のステートマシン）のイベントとして渡すもの。レーンが既読フラグを書くまで未読で残る | — | — | — |
| 完了条件待ち | — | `epic` フィールドを持つ並列の計画で、全レーンの行が終わり、エピックのリクエストのほかにリクエスト（用語集のリクエスト）が無く、完了を止める条件のどれかが残っている状態（完了条件の項目が残る・最後の行の後に判定し直せていない・判定の作業ディレクトリが残る）。計画の状態の値として持たず、行とイベントから導く。導いた結果は `ndf lanes status` の結果 JSON の `metrics.epic_state`（値 `waiting_done`）に出し、Factory はこの値を読む | — | — | — |
| 完了条件待ちのリクエスト | — | 完了条件待ちに入ったときに計画の耐久ワークフローが開き、完了条件の項目をすべて満たしたときに同じ耐久ワークフローが閉じるリクエスト（用語集のリクエスト）。残りの完了条件の番号の並びが変わったときは、閉じて新しい並びで開き直す。エピックのリクエストの 1 つで、完了をオーガナイザーへ通知するためのもの。計画の終了判定では回答していないリクエストに数えない | — | — | — |
| エピックのリクエスト | — | 完了を止める原因（完了条件の項目が残る・判定し直せない回が続く・アーキテクチャ設計書に無い番号・判定の作業ディレクトリが残る）をオーガナイザーへ通知するために、計画の耐久ワークフローが開き、原因が解消したときに同じ耐久ワークフローが閉じるリクエスト（用語集のリクエスト）。中身に原因ごとの意味のキー `epic_key` を持ち、同じキーでは開き直さない。完了を止めるのはリクエストではなく原因の条件で、計画の終了判定では回答していないリクエストに数えない | — | — | — |
| 1 本の重さ | — | 担当 1 つ（worktree 1 つ分）が使う cgroup の anon の量。実測（per_lane_observed_mib）と、本数の判定に使った値（per_lane_used_mib）を分けて出す | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| anon の起点 | — | 実行計画の開始時（動いている本数が 0）に測った cgroup の anon。1 本の重さを測る差の基準 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 判定の区分 | — | parallel-measure.py capacity が本数を決めた理由の区分。grow（足してよい）/ hold（今の本数を超えて足さない）/ shrink（1 本減らす） | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| ランナー | — | 作業を動かす 1 台。スタンドアロンではマシンそのもの、Factory を使う形では実行を受け取る手元のマシンか ECS のタスク。Factory の実行ジョブの中のプログラムはランナーのエージェントと呼ぶ | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 並列の計画 | — | 計画の保存先のリポジトリに置き、同時に進めるレーンの一覧と、レーンに共通の決まりを書いた 1 つの文書（レーンは別のリポジトリでもよい）。オーガナイザーが読む。名前（計画名）を持つ | — | — | `plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md` |
| レーン | — | 並列の計画の下で、名前つきのラッパー 1 本が進める開発の流れ。最初の入力は開発ループかスプリントの 1 つ。レーンのリポジトリは計画の保存先と違ってもよい（別のリポジトリのレーン）。名前は役割を含めず、役割は計画の設定の入力で読む。物理層のラッパーとフロー層の流れの組 | ライン | — | `plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md` |
| オーガナイザー | — | --parallel <計画名> で起動した conductor の役割。レーンの承認待ちをリクエストとして受けて問うか自分で決め、回答を送る（設計の承認ではレーンのリポジトリで設計 Pull Request をマージする）。レーンの起動・再起動・監視とリリースのまとめは耐久ワークフローが行う。人との窓口は計画ごとにオーガナイザー 1 つ。リレーハブとは別。並列の計画のまとめ役はオーケストレーターと呼ばない。開発者ごとの手元のマシンでは手元で起動しない | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| リリースの組み方 | — | リリースのリクエストを受けたときに、まとめたリリースをどう扱うか。組む・組まない・組めないの 3 つがあり、設定から決まる | 配布の組み方 | — | — |
| 組み直しのリクエスト | — | 組めなかったリリースのリクエストを、人が「直してから組み直す」と回答した後に耐久ワークフローが受けているリクエストへ入れ直したもの。元のリクエストの回答は変えない | — | — | — |
| 一時レーン | — | オーナーのいない共有の作業（まとめたリリースプランの実行・その失敗の対応・止まったレーンの保留したループの 1 回の残り・released の後のバージョンの要るまとめ）のために耐久ワークフローが起動するラッパー（<計画名>-rel<連番>、役割 helper）。同時に 1 本までで、作業を終えると自分で止まる | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| 別のリポジトリのレーン | — | 並列の計画の置かれたリポジトリと違うリポジトリで動く、計画のレーン。質問と承認はオーガナイザーを通し、リリースは自分のリポジトリのチャネルへ出す | — | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| リクエストした側 | — | 作業の途中で、別のリポジトリの改修が先に要ると分かったレーンと、そのレーンが進めている課題 | — | — | `plugins/ndf/skills/development-workflow/references/parallel-cross-repo.md` |
| 相手のリポジトリ | — | リクエストした側が改修を必要とする、リクエストした側と違うリポジトリ。改修はそのリポジトリの規約（課題・承認ゲート・漏れの検査）で進む | — | — | `plugins/ndf/skills/development-workflow/references/parallel-cross-repo.md` |
| 完全な形の参照 | — | <所有者>/<リポジトリ>#<番号> の形で課題を指す書き方。リポジトリをまたいで課題を対に結ぶときに使い、番号だけの #<番号> は使わない | — | — | `plugins/ndf/skills/development-workflow/references/parallel-cross-repo.md` |
| 代わりの手順 | — | 別のリポジトリの改修が要るときの既定の手順（別のリポジトリのレーンの起動）を使えないときに取る、out-of-scope の別のリポジトリへの起票 | — | — | `plugins/ndf/skills/development-workflow/references/parallel-cross-repo.md` |
| 相手のレーン | — | リクエストした側が並列の計画へ足す、相手のリポジトリのクローンを repo にした別のリポジトリのレーン。相手の課題を最初の入力に持つ | — | — | `plugins/ndf/skills/development-workflow/references/parallel-cross-repo.md` |
| 計画の外のセッション | — | 動いている計画があるマシンで、計画のレーンでもオーガナイザーでもないのに開発ループかスプリントを始めるセッション。基本不許可で、必要ならレーン数の上限を変えて計画へレーンを足す | — | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| レーン数の上限 | — | 同時に動かせる名前つきのラッパーの数。値はマシンの設定（<状態の根>/relay/hub/limits.json。relay.py cap が書く）→ NDF_RELAY_MAX_NAMED → 既定 3 の順で決まり、リレーハブと計画の検査が同じ値を読む。オーガナイザーは数えず、一時レーンは 1 本だけ超えられる | — | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| 計画の保存先 | — | 並列の計画を作ったリポジトリのメインディレクトリ（計画ごとのファイルはその下の .ndf/parallel/<計画名>/ に置く）。計画の所在の一覧は、このメインディレクトリの絶対パスで持つ。リクエスト・回答の記録のキーは計画の保存先と計画名から求め、その値は <計画の保存先>/.ndf/parallel/<計画名> の絶対パスのまま変えない | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| レーンのリポジトリ | — | レーンが開発するリポジトリのメインディレクトリ。計画の設定のレーンの repo で指し、省くと計画の保存先のリポジトリ | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 計画の所在の一覧 | — | マシンにある計画の保存先を指す一覧（$XDG_STATE_HOME/ndf/parallel/homes/）。別のリポジトリのセッションが、レーンの判定と計画の外のセッションの判定に使う | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 例外の起動 | — | 計画の外のセッションが、理由を添えた明示の指定（--outside-plan <理由>）で開発ループかスプリントを始めること。理由はマシンの記録に残る | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 保留したループの 1 回 | — | 並列の計画のループのレーンで、回答を待たずに締めたループの 1 回（リリース待ち・質問待ち）。課題は締め直すまで選定から外し、回答の後に残りの工程を流して 1 回だけ締め直す | — | — | `plugins/ndf/skills/development-workflow/references/loop.md` |
| 質問待ち | — | 作業の途中の質問を質問の hook がオーガナイザーへ送り、止まったプランとステップを残して締めたループの 1 回の結果 | — | — | `plugins/ndf/skills/development-workflow/references/loop.md` |
| 回答の後の整理 | — | オーガナイザーが回答を送った後、待つ間に届いたリクエストを取り直し、除くものを除いて残りをまとめて問う手順 | — | — | — |
| 設計の承認のリクエスト | — | 並列の計画のレーンが、設計承認ゲートの判断をオーガナイザーへ求めて送るリクエスト。設計 PR の先頭の SHA を持つ | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 前のループ | — | 同じリポジトリの開発ループのうち、並列の計画のレーンでないもの。その回答がまだの人の判断待ちは、オーガナイザーの質問の一覧に出す | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 当たらなかった回答 | — | オーガナイザーの回答のうち、オーナーのループの記録の行へ当てられなかったもの。回答は耐久の記録に残し、区分 unmatched の質問としてオーガナイザーの質問の一覧に出す | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 一覧の失敗の質問 | — | 状態の一覧の失敗が停滞の区切りより長く続いたときに、並列の計画のオーガナイザーの耐久ワークフローが自分で立てる質問（kind は observe）。一覧の終了コードと標準エラーの末尾 1 行を持ち、一覧が成功に戻れば外れる | — | — | `docs/specifications/ndf-shared-venv-and-relay-ls-failure.md` |
| 状態の一覧 | — | ラッパーの relay.py ls --json が返す、名前つきのラッパーごとの状態（running・awaiting_approval・stopped など）と最後に動いた時刻の一覧。オーガナイザーはこれでレーンの状態を観測する | — | — | `docs/specifications/ndf-shared-venv-and-relay-ls-failure.md` |
| リリース待ち | — | リリースのリクエストを送り、回答を待たずに締めたループの 1 回の結果。まとめはまだ流していない | 配布待ち | — | `plugins/ndf/skills/development-workflow/references/loop.md` |
| リリースのリクエスト | — | レーンがリリース承認ゲートの手前で、リリースをオーガナイザーへ頼むために送る 1 件のメッセージ（parallel.py request）。テンプレートで出すリポジトリのレーンだけが送る | 配布の依頼 | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| 課題の差し込み | — | リリースのリクエストのプランのコマンドが、課題の番号の代わりに持つ `{plan_issues}`。ステップを流す時点に、プランの `課題` の空白区切りで埋まる | — | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| まとめたリリース | — | オーガナイザーが複数のレーンの Pull Request を 1 つのバージョンとして出すリリース。提示のプランとリリースのワークフローからなる | まとめた配布 | — | — |
| 回答待ちのリクエスト | — | オーガナイザーに届いて、まだ回答していないリクエスト。設計の承認・リリース承認ゲート・承認ゲートの外の質問・耐久ワークフローが自分で立てた質問の 4 種類 | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 計画の閾値 | — | 計画全体の回答待ちのリクエストの件数の閾値。既定は 8 で、これを超えると計画のすべてのレーンを止める | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| レーンの閾値 | — | 1 つのレーンが送った回答待ちのリクエストの件数の閾値。既定は 3 で、これを超えるとそのレーンを止める | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 回答待ちの停止 | — | 回答待ちのリクエストが閾値を超えたため、レーンに新しい承認を生む作業を始めさせない状態。件数が閾値以下へ戻ると解ける | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 止める指示のファイル | — | 耐久ワークフローが今の回答待ちの停止の範囲を書き、レーンが区切りで読むファイル（answer-hold.json）。停止が無ければファイルも無い | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 再開のマーカー | — | 回答待ちの停止が解けたら起動するレーンを示すファイル（resume/<レーン>.mark.json）。レーンが止まるとき、または停止の間に回答を受けたオーナーへ置く | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 物理層 | — | 並行開発の実行の入れ物の軸。マシン → ラッパー → セッション → プロセスの順に入れ子になる | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| ロール層 | — | 並行開発の役割の軸。オーガナイザー → conductor → supervisor → worker の順に仕事を渡す | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| フロー層 | — | 並行開発の仕事の流れの軸。レーン → ループ → スプリント → フェーズ → 作業の順に入れ子になる | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 今の起動 | — | 一時レーンでは、耐久ワークフローが最後に打った起動。通常のレーンでは、ラッパーが最後に起動した回 | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| レーンの再起動 | — | 止まった、または動かないレーンを `relay.py restart` で起動し直すこと。質問の選択肢の「再起動する」がこれに当たる | — | — | — |
| 停滞の質問 | — | 動かないレーンについて耐久ワークフローが立てる質問（`kind: stall`）。一覧の時刻が区切りより前のとき、開発ループが止まったとき、再起動を断られてレーンが止まったときに立つ | — | — | — |
| 途中停止の質問 | — | リリースの途中で止まった一時レーンについて耐久ワークフローが立てる質問（`kind: helper`） | — | — | — |
| 質問の取り下げ | — | 耐久ワークフローが、回答を待たずに自分の質問を質問の一覧から外すこと | — | — | — |
| 再起動の試み | — | レーンの再起動を 1 回打ったこと。起動できた回も断られた回も 1 回と数え、リレーハブの記録に時刻と結果を残す | — | — | — |
| 計画のジャーナル | — | 1 つの並列の計画を続けるのに要る状態を、計画の保存先に持つ記録。受けた行を足す ledger.jsonl と、ジャーナルのチェックポイントからなる | — | — | `docs/specifications/ndf-parallel-plan-ledger.md` |
| 耐久ワークフローの作り直し | — | 失われた計画で、耐久ワークフローを新しく始めて計画のジャーナルから状態を戻すこと | — | — | `docs/specifications/ndf-parallel-plan-ledger.md` |
| ジャーナルのチェックポイント | — | 耐久ワークフローが自分の状態（回答したリクエスト・リリース記録・起動したレーン）と読み終えた位置を書く、計画の保存先の 1 ファイル。書くたびに全体を置き換える | — | — | `docs/specifications/ndf-parallel-plan-ledger.md` |
| 失われた計画 | — | 閉じていないのに、耐久ワークフローが動いていない計画。ERROR か、耐久の記録が無い状態を指す | — | — | `docs/specifications/ndf-parallel-plan-ledger.md` |
| 耐久の記録の退避 | — | 失われた計画の耐久の記録のファイルを、別の名前へ移すこと。同じ計画の新しい記録を同じ保存先で開くために行う | — | — | `docs/specifications/ndf-parallel-plan-ledger.md` |
| 業務時間 | — | 利用者が承認ゲートへ回答できる時間帯。進め方の設定に、時刻帯・曜日・始まりと終わりの時刻で書く。時間外は承認のリクエストを溜め、業務時間の始めにまとめて示す | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 新しい承認を生む作業 | — | 設計のステップ（設計承認ゲートのリクエストを生む）を含む作業。ループのレーンではループの 1 回のプランに設計承認ゲートを含む作業、1 スプリントのレーンでは開始のステップから設計を書くステップへ行けるプラン。回答待ちの停止の範囲に入ったレーンは始めない | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 承認の要らない作業 | — | 新しい承認を生む作業以外の作業。開発ループの中では承認した設計の実装と回答の届いた保留したループの 1 回の残り（ループは設計を承認していない課題にモードに依らず設計のステップを置く）、1 スプリントのレーンでは設計 PR のマージ・実装・検査・リリースのプランがこれに当たる | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 次に始めるプラン | — | 1 スプリントのレーンのスプリントで、ステージの並びの先頭から見て、結果が完了でも承認ゲートでもないプランを持つ最初のステージの、そのプランの集まり。回答待ちの停止の範囲で回答を受けたレーンを起動するかを決める | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 溜めたリクエスト | — | 業務時間外にオーガナイザーが利用者へ問わずに持っておく、設計の承認と質問のリクエスト。業務時間の始めにまとめて示す | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 立ち上がりの確認 | — | 耐久ワークフローが起動かレーンの再起動を打った後に、レーンの状態の一覧でそのレーンが動いている（または打った時刻より後に動いた）と読めるまで、決まった間隔で一覧を読むこと。上限は既定 120 秒 | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| 起動の再試行 | — | 立ち上がりの確認で立ち上がらなかった起動かレーンの再起動を、耐久ワークフローがもう 1 回だけ打つこと。2 回目も立ち上がらなければ区分 spawn の質問を立てる | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| 手順どおりの停止 | — | 一時レーンが、リリース承認ゲートのリクエストか settle を送った後に自分のラッパーを止めること。区分 helper の質問の対象にしない | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| 長い待ち | — | オーガナイザーが次の知らせまで待つ見込みの時間が長く、状況報告を続けるよりセッションを切り替えた方が入力の費用が小さいと parallel.py wait が判定する待ち | — | — | `docs/specifications/ndf-parallel-organizer-long-wait.md` |
| 状況報告 | — | parallel.py wait が上限で戻ったときに、オーガナイザーがレーンごとの様子を利用者へ 1 行ずつ出す報告 | — | — | `docs/specifications/ndf-parallel-organizer-long-wait.md` |
| 切り替えの判定 | — | parallel.py wait が上限で戻るときに出す「切り替える」「報告して待つ」の判定と、その材料（metrics.switch） | — | — | `docs/specifications/ndf-parallel-organizer-long-wait.md` |
| プロンプトキャッシュの寿命 | — | 会話の入力がプロンプトキャッシュに残る時間。最後に使われた時点から数える | — | — | `docs/specifications/ndf-parallel-organizer-long-wait.md` |
| 出し直しの提示 | — | まとめたリリースで、承認の回答の時点でベースブランチが承認したコミットより進んでいたとき、加わった範囲を添えて同じリリース承認ゲートをもう 1 回出す提示（represent: true）。承認したコミットのまま出すバージョンより前に始めた計画の記録の打ち直しでだけ現れ、新しく始めた計画では出さない | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 加わった範囲 | — | まとめたリリースで、提示 1 で承認したコミットから承認の回答の時点のベースブランチの先頭までに入った変更。新しく始めた計画では今のリリースに入らず、次のリリースの範囲に入る | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 文書だけの範囲 | — | 加わった範囲のファイルを設定 docs で分けて、コードのファイルと振る舞いを決める文書が 0 件、文書のファイルが 1 件以上の範囲。出し直しの提示を省く条件 | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 出し直しを省く | — | 加わった範囲が文書だけの範囲のとき、出し直しの提示を出さず、新しい範囲で確定してリリースへ進むこと | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 確かめ直しの範囲 | — | まとめたリリースで出し直しを省いたとき、提示 1 のコミットから提示 2 の承認資料を作った時点のコミットまでに入った変更。判定の後にベースブランチが進めば加わった範囲より広くなる。引き継いだ承認を記録する前にこの範囲が文書だけかを確かめる | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 引き継いだ承認 | — | まとめたリリースで出し直しを省いたとき、提示 1 への承認を提示 2 の承認として回答の記録に書いたもの。何を引き継いだかを記録の what に残す | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 課題のまとまり | — | 並列の計画を作る時点で、似た課題を、1 本の実装の Pull Request でレビューするためにまとめたもの。スプリントのレーンの入力に書き、まとまり 1 つが実装の Pull Request 1 本になる。1 件の課題だけのまとまりもある | — | — | `docs/specifications/ndf-parallel-issue-bundles.md` |
| 代表の課題 | — | 課題のまとまりの先頭の課題。まとまりの設計文書と実装の Pull Request はこの課題の番号で作り、ほかの課題の受け入れ条件をマージする | — | — | `docs/specifications/ndf-parallel-issue-bundles.md` |
| 疎なまとまり | — | 触る場所の見込みが互いに重ならない、課題のまとまりの組 | — | — | `docs/specifications/ndf-parallel-issue-bundles.md` |
| まとまりの単位のレビュー | — | 1 つの課題のまとまりの課題が変えた差分だけを範囲にした cross-review | — | — | `docs/specifications/ndf-parallel-issue-bundles.md` |
| マシンのプランの本数 | — | 同じマシンで動いている並列の計画の、動いているレーン（承認の回答を待って保留で止まったレーンを含む）の設定の plans の合計。プランの本数の上限と比べる | — | — | `docs/specifications/ndf-parallel-machine-plan-limit.md` |
| 起動の待ち | — | 依存がそろったレーンを、マシンのプランの本数に空きができるまで耐久ワークフローが起動しないこと | — | — | `docs/specifications/ndf-parallel-machine-plan-limit.md` |
| プランの本数の上限 | — | 同じマシンで同時に流すプランの本数の合計の上限。マシンの設定（<状態の根>/relay/hub/limits.json の max_plans。relay.py cap --plans が書く）→ NDF_PARALLEL_MAX_PLANS → 既定 6 の順で決まり、計画の検査と耐久ワークフローの起動の判定が同じ関数で読む | — | — | `docs/specifications/ndf-parallel-machine-plan-limit.md` |
| 起動の排他 | — | 同じマシンの耐久ワークフローが、マシンのプランの本数を数えてからレーンを起動するまでを 1 つずつ行うための排他（<状態の根>/parallel/admit.lock） | — | — | `docs/specifications/ndf-parallel-machine-plan-limit.md` |
| 1 スプリントのレーン | — | 並列の計画のレーンのうち、最初の入力が課題の番号（`/goal /ndf:development-workflow #<課題>`）のもの。開発ループのレーン・一時レーンと区別する | — | — | `docs/specifications/ndf-parallel-lane-end-and-verify-close.md` |
| まとめ | — | リリースの後に流す作業のまとまり（`supervise.py new close` のプラン）。確定仕様化・課題を閉じる・リリース後テスト・振り返り・棚卸しを持つ | — | — | `docs/specifications/ndf-parallel-lane-end-and-verify-close.md` |
| バージョンの要らないまとめ | — | まとめのうち、出したバージョンが無くても流せる作業（確定仕様化・振り返り・棚卸し）。1 スプリントのレーンはリリースの回答を待たずに流す | — | — | `docs/specifications/ndf-parallel-lane-end-and-verify-close.md` |
| バージョンの要るまとめ | — | まとめのうち、出したバージョンの上でしか流せない作業（リリース後テストと、その結果で課題を閉じる作業） | — | — | `docs/specifications/ndf-parallel-lane-end-and-verify-close.md` |
| レーンの終わり | — | 1 スプリントのレーンがバージョンの要らないまとめを流し切った時点。終わりの記録として耐久ワークフローが読める保存先に残り、耐久ワークフローがそのレーンのラッパーを止める | — | — | `docs/specifications/ndf-parallel-lane-end-and-verify-close.md` |
| 終わりの記録 | — | レーンの終わりを示す記録。耐久ワークフローはこの記録のある止まったレーンだけを終わったと数え、無いまま止まったレーンは再開のマーカーで起動する | — | — | `docs/specifications/ndf-parallel-lane-end-and-verify-close.md` |
| 止められなかったレーンの質問 | — | 終わりの記録のあるレーンを耐久ワークフローが止められなかったときに立てる質問（kind は halt）。止める 1 行を持ち、レーンが止まって観えるまで消えない | — | — | `docs/specifications/ndf-parallel-lane-end-and-verify-close.md` |
| 終わりの記録の無い停止の質問 | — | 1 スプリントのレーンが終わりの記録も回答待ちも無いまま停滞の区切りより長く止まって観えたときに、耐久ワークフローが立てる質問（kind は unended）。「終わったと数える」で終わりの記録を書く | — | — | `docs/specifications/ndf-parallel-lane-end-and-verify-close.md` |
| 中身の無いリクエスト | — | リリースのリクエストのうち、載せた Pull Request がすべてマージされずに閉じたもの。耐久ワークフローはリリースを組まずに、バージョンなしのリリース済みと理由（閉じた Pull Request の番号）で回答して外す | — | — | `docs/specifications/ndf-parallel-empty-requests-and-settled-gates.md` |
| 処理済みのリリース承認ゲート | — | 計画のジャーナルで、同じ連番の確定か締めの行がそれより後にあるリリース承認ゲートの行。回答待ちのリクエストに数えない | — | — | `docs/specifications/ndf-parallel-empty-requests-and-settled-gates.md` |
| 確定の後の承認ゲート | — | 並列の計画で、リリース承認ゲートの承認（確定）を受けた後に、リリースのワークフローが承認ゲート（終了コード 10）で終わること | — | — | `docs/specifications/ndf-parallel-helper-confirmed-gate-and-launch-wait.md` |
| リクエスト | `request` | 並列の計画のレーンか一時レーンがオーガナイザーへ送る質問・承認の求め。ジャーナルに 1 行で残り、id を持つ | 依頼 | — | `docs/specifications/ndf-parallel-helper-confirmed-gate-and-launch-wait.md` |
| 同じ質問 | — | 並列の計画で、一時レーンの区分つきの質問のうち、同じ一時レーンの名前・同じ区分・同じ質問の対象（ブランチなら HEAD のコミットの 40 桁まで）を持つもの。言い回しの違いは数えない。1 スプリントのレーンと開発ループのレーンの質問は比べず、質問の対象が空の質問はどの質問とも同じ質問にならない | — | — | `docs/specifications/ndf-parallel-sprint-design-gate-and-helper-reused-answers.md` |
| 起き直し | — | 止まった一時レーンを、耐久ワークフローが同じ名前で起動し直すこと | — | — | `docs/specifications/ndf-parallel-helper-confirmed-gate-and-launch-wait.md` |
| 質問の対象 | — | 並列の計画で、区分つきの質問が操作する対象を表す 1 つの文字列。`<ブランチ名>@<HEAD のコミットの 40 桁>` か `#<課題か PR の番号>`。`request-ask` が質問の文の対象の行から読み、ブランチの HEAD のコミットは質問を受けた時点に求めて足し、計画のジャーナルのリクエストの行の `target` に残す | — | — | `docs/specifications/ndf-parallel-sprint-design-gate-and-helper-reused-answers.md` |
| 対象の行 | — | 区分つきの質問の最初の質問の文に置く `対象: <ブランチ名>` か `対象: #<番号>` の 1 行。`request-ask` が質問の対象を読む唯一の場所で、複数あれば最後の行を使う | — | — | `docs/specifications/ndf-parallel-sprint-design-gate-and-helper-reused-answers.md` |
| 承認の行 | — | 1 スプリントのレーンが設計 PR のマージの承認を求める区分 `ndf-gate:1` の質問の、最初の質問の文に置く `承認: #<設計 PR の番号>` の 1 行。`request-ask` が設計承認のリクエストに組む質問を見分ける唯一のマーカーで、質問の文のほかの場所で PR を指しても設計承認のリクエストにならない | — | — | `docs/specifications/ndf-parallel-sprint-design-gate-and-helper-reused-answers.md` |
| 回答の引き継ぎ | — | 起き直した一時レーンの同じ質問へ、前の起動で送ったリクエストの回答を当てるか、回答待ちのリクエストの id を返すこと。どちらの場合も新しいリクエストの行を書かず、計画のジャーナルに `reused` の行で残る | — | — | `docs/specifications/ndf-parallel-sprint-design-gate-and-helper-reused-answers.md` |
| 一時レーンの空き待ち | — | 一時レーンの起動が、別の一時レーンが動いているために断られた後、耐久ワークフローが前の一時レーンが止まるまで区分 `spawn` の質問を立てずに待つこと。上限は既定 3,600 秒 | — | — | `docs/specifications/ndf-parallel-helper-confirmed-gate-and-launch-wait.md` |
| 設計承認のリクエスト | — | 並列の計画のレーン（開発ループのレーンと 1 スプリントのレーン）が設計承認ゲートでオーガナイザーへ送る、設計 PR の承認のリクエスト。計画のジャーナルの `kind: gate1` の行で、`answer --approved` が設計 PR をマージし、MVV 判定と古いリクエストの整理が掛かる | — | — | `docs/specifications/ndf-parallel-sprint-design-gate-and-helper-reused-answers.md` |
| 区分つきの質問 | — | `metadata.source` に区分（`ndf-gate:<番号>`・`ndf-ask:<名前>`）を持つ `AskUserQuestion`。並列の計画のレーンと一時レーンが出すと、質問の hook が `parallel.py request-ask` でオーガナイザーへ送り、計画のジャーナルの `kind: ask` の行になる。1 スプリントのレーンの区分 `ndf-gate:1` の質問が、承認の行で承認ゲートで終わった設計 PR を 1 本だけ指し、その PR が開いていて先頭のコミットを読めたときは `kind: gate1` の行（設計承認のリクエスト）になる | — | — | `docs/specifications/ndf-parallel-sprint-design-gate-and-helper-reused-answers.md` |
| マシンの設定 | — | 同時に動かすレーンとラッパーの数の上限を、マシンごとに状態の保存先へ置く値。リレーハブと耐久ワークフローが判定のたびに環境変数と上限の設定より先に読む | — | — | — |
| 稼働中のレーン | — | 実行中の行を持つか、ラッパーが動いているレーン。承認ゲート・質問の回答を待つ行を持つレーンを含む。マシン全体の同時実行の上限はこの数とほかの名前つきのラッパーを 1 本ずつ数える | — | — | — |
| 起動の許可 | — | 耐久ワークフローが行を始める起動の前に、マシンの起動の排他の中で稼働中の数を数え、上限に収まるときだけ起動させる判定。収まらなければ起動の待ちに置く | — | — | — |
