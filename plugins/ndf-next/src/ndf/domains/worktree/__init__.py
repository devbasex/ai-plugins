"""worktree のコンテキスト（作業場所の用意と片付け）。

判断（純粋な関数。名前が `_decide`）:

- `guard_decide`: 書き込み先がメインディレクトリの許可パスの外か・案内の文
- `session_decide`: セッションを始めたときに伝える、メインディレクトリの逸脱（未コミットの変更）
- `cleanup_decide`: 開発 worktree を消すか残すかと、その理由
- `merged_decide`: スプリントブランチを残すか（開始時の記録で決める）と、メインディレクトリを取り込むか
- `localenv_decide`: ローカル環境に載っているコードの照合と、相乗りか分離かの提示
- `testenv_decide`: テスト環境の名前・空きスロット・ポート・compose へ渡す値

部品:

- `declaration`: 宣言 `.ndf/worktree.json` と個人設定 `.ndf/worktree.local.json` の読み取り
- `repo`: メインディレクトリ・worktree の中か・worktree の一覧（git は `adapters.git` を通す）
- `create`: ブランチの worktree を `.worktrees/<ブランチ>` に作る（作業場所の用意）
- `cleanup`: マージ済みのブランチの worktree を消す（PR は `adapters.github` から読む）
- `merged`: マージの後の後片付け（PR ごとに worktree とブランチを外し、メインディレクトリを fast-forward で取り込む）
- `trash`: 外す前の退避と、本番に出た退避先の回収（承認した名前だけを消す）
- `mainlock`: メインディレクトリの排他（index か HEAD を動かす git の操作を 1 本ずつにする）
- `follow`: セッションを始めたときのブランチの追従（宣言の `follow_branch: true` のとき）
- `deps`・`localenv`・`testenv`（`ledger`・`testenv_sweep`）: 宣言の各節に従う worktree の環境
- `container`: 宣言のコマンドとコンテナの実行系の起動（差し替えられる）
- `atomic`: 半端なファイルを残さない書き込み（一時ファイルから付け替える）
- `outcomes`: 後片付けと回収の結果の語（`items` の `result`）
- `write_target`: シェルのコマンドとパッチから書き込み先を推定する（guard の判断が使う）
- `setup`: 宣言の導入（`init`・`check`）
- `cli`・`cli_env`: `ndf worktree` の副命令
"""
