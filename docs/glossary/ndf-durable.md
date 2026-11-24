# 用語集: NDF の実行の基盤（`ndf-durable`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

耐久ワークフロー・ステートマシン・実行の核が、実行を記録し、止まった理由と次のルートを決めるときの語。結果 JSON・停止理由・判断の関数

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 結果 JSON | `step_result` | 手順のスクリプトが返す 1 行の JSON。status と終了コードで読み、成功は ok だけ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 耐久ワークフロー | `durable_workflow` | DBOS の @DBOS.workflow の関数の 1 回の実行。ID を持ち、落ちた後の起動が記録から続ける。プラン 1 本・キュー 1 本・drive 1 回・投稿の項目 1 件がそれぞれ 1 つ | — | — | `docs/specifications/ndf-rebuild-libraries.md` |
| 耐久ステップ | `durable_step` | 耐久ワークフローの中で出力を SQLite へ記録する単位（DBOS の @DBOS.step）。記録のある耐久ステップは続けるときに流し直さない。プランのステップとは別の語 | — | — | `docs/specifications/ndf-rebuild-libraries.md` |
| 耐久キュー | `durable_queue` | DBOS の register_queue で作る、同時の本数と分割を持つ待ち行列。supervise.py queue（キュー）とは別の語 | — | — | `docs/specifications/ndf-rebuild-libraries.md` |
| 耐久の記録 | `durable_store` | 1 回の起動が持つ DBOS の SQLite のファイル（~/.local/state/ndf/dbos/<種類>-<キー>.sqlite） | — | — | `docs/specifications/ndf-rebuild.md` |
| 実行のキー | `run_key` | 起動を識別する文字列（run-<プランのパスの sha256 の先頭 12 字> など）。耐久の記録のファイル名と executor_id に使う | — | — | `docs/specifications/ndf-rebuild-libraries.md` |
| 実行の回 | `attempt` | 同じ実行のキーの中で、耐久ワークフローを頭から流した 1 回。ID の末尾の番号 | — | — | `docs/specifications/ndf-rebuild-libraries.md` |
| 試行 | — | 同じステップを流し直したときの 1 回ごとの実行と、その結果 JSON。プランの実行の記録の NN-<ステップ>.out の 1 つが 1 試行に当たる | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 欠けの残る結果 | — | 手順は終わったが、出すはずの材料・記録の一部が無いか、目標（収束など）に届かなかった結果。stopped と終了コード 6 と gaps で返し、ok で返さない | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 結果の保存先 | — | 読む側が結果 JSON や材料を探す場所（プランの実行の記録・記録のファイル）。保存先が無いことと、材料が 0 件であることを分けて扱う | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 欠け | `gaps` | 結果 JSON の gaps の 1 件。出すはずだったのに出せなかった材料・記録、届かなかった目標を、種類・名前・理由で書く | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 過程の項目 | `process` | 結果 JSON の process。スクリプトが実行したこと（コマンド・操作）と、その結果の要旨を順に並べる | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 判断の項目 | `decisions` | 結果 JSON の decisions。スクリプトが決めたことと、その根拠を並べる | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 実行の核 | `run_core` | エントリポイント・ステップが必ず通る共通の実行の仕組み。入力の解決・記録・lease・型の確認・再試行と上限・結果の変換を引き受け、ステップは仕事の中身だけを書く（#2335） | — | — | — |
| 実行の識別子 | `run_id` | 1 回のエントリポイントの実行に実行の核が振る識別子。記録・オーナーの記録・耐久の記録をつなぐ（#2335） | — | — | — |
| 実行の文脈 | `run_context` | 実行の核が本体へ渡す 1 つの値。解決した入力・実行の識別子・期限・記録の書き口・再試行・型の読み書きを持つ（#2335） | — | — | — |
| 実行の記録 | `run_log` | 1 回の起動（子の実行を含む）の始まりから終わりまでを、実行の識別子つきで書く JSON Lines の行の並び。既定の出力先はファイル（#2335・#2338） | — | — | — |
| 起点の実行 | — | 親の実行の識別子を持たない実行。実行の記録のファイル名になる（#2338） | — | — | — |
| 子の実行 | — | 実行の核を通るエントリポイントが子プロセスとして起動した、核を通るエントリポイントの実行。親の実行の識別子を持ち、親と同じ実行の記録へ書く（#2338） | — | — | — |
| 診断 | — | スクリプトが人へ向けて標準エラーへ出す文（警告・進み具合・失敗の理由）。結果 JSON とは別の出力で、実行の記録にも残す（#2338） | — | — | — |
| 出力先 | — | 実行の記録の行を書く先。既定はファイルで、1 つの設定で別の出力先（Factory の CloudWatch Logs など）へ差し替える（#2338） | — | — | — |
| 全文ファイル | — | 実行の記録の 1 行の上限を超える文字列を、切らずに全文で置くファイル。実行の記録と同じ日付のディレクトリに置き、行にはパス・文字数・sha256 を残す（#2338） | — | — | — |
| 上限の設定 | `limits_declaration` | 上限の秒・再試行の回数・同じ原因の上限を、方針の名前ごとに 1 ファイルで持つデータ（plugins/ndf/contracts/limits/。#2335） | — | — | — |
| 方針の名前 | — | 上限の設定の 1 行を指す名前。エントリポイントのコントラクトと部品の呼び出しが、値でなくこの名前で上限を引く（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 原因のキー | — | 失敗が前回と同じ原因かを見る値。例外の型と、数字・時刻・パスを伏せた失敗の理由の文から作る（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 再試行 | — | 失敗した同じ呼び出しを、同じプロセスの中で間隔を置いて打ち直すこと。条件が揃うまで繰り返し確かめる待ちとは別である（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| ステップの打ち直し | — | プランの判定役（judge）が、失敗したステップを同じステップのまま流し直すと返すこと（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 眠りのループ | — | 本体（入れ子の関数を除く）で sleep を呼ぶ for / while のループ。再試行と待ちの両方が当たり、lib/waits.py の外では構造チェックが落とす（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 最後の照会 | — | 待ちの次の眠りが上限を超えるとき、残りの秒だけ眠った後に上限の時刻に打つ 1 回の照会（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 本体 | — | エントリポイントの仕事の中身を持つ関数。実行の核が呼び、結果 JSON か終了コードを返す（#2337） | — | — | — |
| 核の対象外 | — | 実行の核を通さないエントリポイントのマーカー。hook の定義から起動されるか、ランタイムが直接起動して標準出力をそのまま使うもの。コントラクトの一覧の core に理由と根拠を書く（#2337） | — | — | — |
| ステートマシン | `machine_id` | 工程の並びと、それぞれの状態を持つ表。計画のステートマシン・レーンのステートマシン・スプリントのステートマシンの 3 つの階層がある。今の `sprint.json` を置き換える | 進行表 | — | — |
| スプリントのステートマシン | — | スプリント 1 つの工程の行と状態を持つステートマシン。スプリント 1 つにつき 1 つ | — | — | — |
| レーンのステートマシン | — | レーン 1 つの中のスプリントの順番表を持つステートマシン。レーンの conductor が持つ | — | — | — |
| 計画のステートマシン | — | 開発計画 1 つのレーンの配列と、レーンをまたぐ決まりを持つステートマシン。オーガナイザーが持つ | — | — | — |
| 終了のマーカー | — | 工程の出力が入出力規約に合ったときに、ステートマシンの行へ付ける「終了」の記録 | — | — | — |
| 入出力規約 | — | 工程とステップが受け取る入力と、終わったときに返す出力の JSON の形 | — | — | — |
| 工程の定義 | — | モードの工程の並びと、工程ごとのアクティビティの並び・入出力規約の参照・順序の属性を持つ、Serverless Workflow の書式の JSON 文書 | — | — | — |
| 組み換え | — | スプリントのまとめの振り返りの後に、次以降のスプリントの順番表を必要なときだけ変える工程 | — | — | — |
| アクティビティ | — | 工程の中の実行の単位。DBOS のステップ 1 つで流し、入力の範囲と出力の規約を持つ。プラン 1 本の実行・承認ゲートの回答の待ち・記録の 3 種類がある。プランのステップとは別 | — | — | — |
| ステートマシンのワークフロー | — | スプリントのステートマシン 1 つを、登録した工程の定義に沿って流す DBOS のワークフロー。実行のたびに ID が替わり、ステートマシンの ID は変わらない | — | — | — |
| 進行の事象 | — | ステートマシンへの変更 1 件の記録。誰が・いつ・何を・理由と変更後の状態を持ち、追記だけで残す。行の今の状態は最新の事象から引く | — | — | — |
| ステートマシンの DB | — | 計画・レーン・スプリントのステートマシンと進行の事象を置く 1 つのデータベース。URL で指し、ローカルは SQLite、Factory は PostgreSQL。DBOS の記録はローカルでは実行のキーごとのファイルに分け、置かない（#1142 の決定 81） | — | — | `docs/specifications/ndf-rebuild.md` |
| 定義の改訂 | — | スプリントのステートマシンの定義のスナップショットの番号（`rev`）。作成で 1 になり、範囲とモードの変更で 1 ずつ上がる。工程の行は定義の改訂と工程の名前の組で決まる。定義の文書のバージョン（`document.version`）と MVV の改訂とは別 | — | — | — |
| 停止理由の種類 | — | 手順のスクリプトとプランが停止理由を、閉じた語彙の 1 語で表したもの。結果 JSON の `cause.kind` に入る。人の読む文（`cause.detail`）とは別で、ルートはこれだけで決まる | — | — | `docs/specifications/ndf-rebuild-scripted-flow.md` |
| 停止理由の表 | — | 停止理由の種類ごとに、意味・既定のルート・再試行の方針・標準の誤りの型を持つデータ（`plugins/ndf/contracts/causes/`） | — | — | `docs/specifications/ndf-rebuild-scripted-flow.md` |
| ルート | `route` | 止まったステップや工程の次に進む先。プランのステップ・`retry`（待って打ち直す）・判断の呼び出し・人へのどれか | 行き先 | — | `docs/specifications/ndf-rebuild-scripted-flow.md` |
| 判断の呼び出し | — | スクリプトが決めた時点で、選択肢か JSON のスキーマを渡して AI を 1 回呼び、回答をスキーマで確かめる操作。今の `type: judge` のステップを置き換える | — | — | `docs/specifications/ndf-rebuild-scripted-flow.md` |
| 回答ごとのルート | — | 承認ゲートと人への質問の結果に添える、回答の値から次に打つコマンドへの表（結果 JSON の `routes`） | — | — | `docs/specifications/ndf-rebuild-scripted-flow.md` |
| 遷移の判断 | — | プランの次のステップと結果の確定を決める判断（上限との比較・遅れの再試行を数えない・知らないステップで止まる）。耐久ステップの呼び出しとは別に置く | — | — | — |
| DBOS を起動しない実行器 | — | 耐久ワークフローの判断の関数を、DBOS を起動せずに回すテスト用の実行の経路 | — | — | — |
| 判断の関数 | — | 状態と入力を受けて次の手を返す関数。耐久ワークフロー・DBOS・プロセス・ファイルを扱わない。保存先のモジュールの名前は _decide.py で終わる | — | — | — |
| 耐久の層 | — | 判断の関数が返した手を耐久ステップで実行し、結果を判断の関数へ返す薄い層。次の手を自分で選ばない | — | — | — |
| 判断のループ | — | 判断の関数と、手を実行する関数を受けて回す共通の部品（lib/decide_loop.py）。実行する関数に耐久ステップを渡すと耐久の層になり、ただの関数を渡すと DBOS を起動しない実行器になる | — | — | — |
| 実行の一覧 | `durable_runs` | PostgreSQL のときに起動した実行のキーを書き、片付けで消す表（ndf_runs） | 台帳 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 工程の出力の確かめ | — | 工程の出力の JSON を、ステートマシンへ書かずに ndf progress finish と同じ規則で確かめること（ndf progress check） | — | — | `docs/specifications/ndf-progress-check-stage-output.md` |
| 書き込みの順番の記録 | `write-gate` | NDF の GitHub への書き込みがマシン全体で順番を取るための記録（<OS のユーザーのホーム>/.local/state/ndf-github/write-gate.json）。次の書き込みの時刻・内容を作る書き込みの時刻の一覧・待ちの終わりを持つ | — | — | `docs/specifications/ndf-github-write-gate.md` |
| 内容を作る書き込み | `content` | REST の POST と、GraphQL の create・add で始まる mutation。マシン全体で 1 分 80 件・1 時間 500 件のウィンドウで数え（github.content-window.minute・github.content-window.hour）、上限に達したときだけウィンドウが空くまで待つ | — | — | `docs/specifications/ndf-github-write-gate.md` |
| 二次レート制限 | — | GitHub が一次の上限とは別に、短い間の書き込みの多さ・同時の要求の多さで断る上限。403 か 429 と本文の secondary rate limit で返る | — | — | `docs/specifications/ndf-github-write-gate.md` |
