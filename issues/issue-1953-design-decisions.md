# #1953 の設計: 決定の記録

設計文書の本体は `issues/issue-1953-design.md` にある。

## 決定の記録

### 決定 1: 帳簿の間で単位を混ぜないため、帳簿をランタイムごとのディレクトリに分ける

置き場を `<ledger_dir>/<runtime>/<owner>__<repo>.jsonl` にする。ディレクトリを読む単位がそのまま帳簿の単位になり、1 つの帳簿を読む処理が別の帳簿の行に触れない（I2・I7）。リポジトリごとのファイルの分け方（#1142）はそのまま使える。版 1 の行は直下に残し、読むときに `claude` の行とみなす。

1 つのファイルに `runtime` の列を足して混ぜる形は、読む側がすべての行を読んで絞る必要があり、絞り忘れが単位の混ざった合計を生むため採らない。ファイル名に接尾辞を付ける形（`<repo>.codex.jsonl`）は、版 1 の `read_all`（直下の `*.jsonl` をすべて読む）が他のランタイムの行を Claude の行として読んでしまうため採らない。

根拠: Value 6 / Value 3（MVV 版 2）

### 決定 2: 起動の経路を足しても書き忘れないよう、記録を起動の共有処理の 4 か所に集める

記録を書くのは `launch-cli.sh`（背景で起動する 4 ランタイム）・`supervise_lib/claude.py` の `call_claude`（同期の `claude -p`）・`lib/auth.py` のモデルの確認・`lib/jev.py` の `_post` の 4 か所だけにする。mvv-gate は自前の `subprocess.run` をやめて `call_claude` で起動し、`mvv_llm` と `ClaudeRunner.record_usage` は帳簿へ書かなくなる。今は 3 か所（`record_usage`・mvv-gate・`mvv_llm`）が別々に行を組み立てており、起動の経路を足すたびに書き手が増える。

静的な検査は、この 4 か所と除外一覧の外にある LLM の起動を落とす（受け入れ条件 10）。参加者の確認を `call_claude` に寄せる形は、codex と kiro の確認も持つため採らず、4 か所目として残す。呼び出し元ごとに書く形（cross-review の結果の読み出しで書くなど）は、起動の経路と書く場所が離れて受け入れ条件 3 の趣旨に反するため採らない。

根拠: Value 6 / Value 4（MVV 版 2）

### 決定 3: 起動の形を変えずに使用量を得るため、codex と kiro はセッションの記録から、claude と agy は JSON の stdout から読む

codex は `launch-cli.sh` の起動を変えずに、err.log の `session id:` から `$CODEX_HOME/sessions` の rollout を引き、最後の `token_count` を読む。`--json` を付けると stdout の形が変わり、結果の読み手と `monitor` の照合を直す範囲が広がる。rollout が無いときは stderr の `tokens used` の合計だけを置く（2026-10-13 の実測で stderr に出る）。

kiro は stdout に使用量を出さず、セッションの記録のトークン数の欄は 0 である（同日の実測）。`metering_usage` の credit を、作業ディレクトリと起動の時刻で 1 つに絞ったセッションの記録から読み、絞れなければ `"unknown"` にする。

agy は `--output-format json` に変える。テキストの出力には使用量が無く、使用量を得る手段は JSON の出力だけである。stdout を本文として読むのは `external-ai.py` の `read_stdout` だけで、claude と同じく JSON の 1 つの鍵（`response`）を読む形に揃える。

根拠: Value 3 / Value 1（MVV 版 2）

### 決定 4: サブエージェントを呼び出しの時点で 1 行にするため、SubagentStop の hook で帳簿へ書く

hook の入力に `agent_id`・`agent_type`・`agent_transcript_path`・親の `session_id` がある（2026-10-13 の実測）。会話の記録を読んで `message.id` ごとに使用量を 1 回足し、1 行にする。会話の記録は Claude Code の保存期間を過ぎると消えるが、帳簿の行は残る。

集計の時に会話の記録から取り込むだけの形は、保存期間を過ぎた期間の値が後から読めなくなり、同じ期間を測り直すたびに値が変わるため採らない。会話の記録からの取り込みは、hook の前の期間（要求の前提 4）を埋めるためにだけ使う。

根拠: Value 3 / Value 7（MVV 版 2）

### 決定 5: 同じ呼び出しを 2 回数えないため、`claude -p` の行は親の会話だけを持ち、中のサブエージェントは別の行か `modelUsage` の残りで数える

`claude -p` の結果の `usage` は親の会話だけで、`modelUsage` は中で起動したサブエージェントを含む（2026-10-13 の実測: 22,347 と 43,955）。`usage` を行のトークン数にし、サブエージェントは hook の行で数える。hook が効かない `claude -p` に備え、行ごとに `modelUsage` の和から `usage` と親を同じくするサブエージェントの行を引いた残りを全体に足す。残りには 5 分と 1 時間の書き込みの区別が無く、5 分として数える（`cache_writes` の既定と同じ）。

帳簿の行と会話の記録は、`claude -p` はセッション ID、サブエージェントは `agent_id` で突き合わせ、帳簿に無いものだけを会話の記録から足す。`modelUsage` だけで数える形は、5 分と 1 時間の書き込みの区別を失い受け入れ条件 4 を満たさないため採らない。

根拠: Value 3（MVV 版 2）

### 決定 6: 背景で起動した CLI の終わりを記録の書き手が知るため、`launch-cli.sh` は CLI を包みのサブシェルで起動する

包みのサブシェルは `set -m` でプロセスグループの先頭になり、CLI を前景で走らせ、終わった後（TERM を受けたときは trap）に `usage_capture.py record-cli` を打つ。`<stem>.pid` は包みを指す。止める側（`monitor_proc`）は先頭のプロセスグループへ `killpg` で送るため、包みと CLI が一緒に止まる。生死を見る側は、包みが記録を終えるまで生きていると見るが、記録はファイルを読んで 1 行を足すだけである。

`monitor.py` が終わりを見つけたときに書く形は、`external-ai.py run` のように monitor を通らない呼び出しがあり、書き手が経路ごとに分かれるため採らない。

根拠: Value 6（MVV 版 2）

### 決定 7: 呼び出し元を起動の共有処理まで届けるため、環境変数 `NDF_USAGE_SOURCE`・`NDF_USAGE_KIND`・`NDF_USAGE_PLAN`・`NDF_USAGE_STEP` で渡す

`launch-cli.sh` は Skill の包み（cross-review・cross-refactoring の起動のスクリプト・external-ai）の下で呼ばれ、引数に呼び出し元を持たない。引数を足すと 4 つの呼び手の引数の並びを変えることになる。環境変数は入れ子の起動（supervise の drive の中の cross-review）にもそのまま渡り、supervise が置いたプランとステップを、Skill が置いた `source` と組にできる。external-ai は配送の仕組みで呼び出し元になることが少ないため、無いときだけ `external-ai` を置く。

名乗りが無い行は `source`・`kind` を `unknown` にして残す（I1 を優先し、行を落とさない）。

根拠: Value 5 / Value 6（MVV 版 2）

### 決定 8: Jev の要求を別の料金の帳簿で数えるため、`jev` の帳簿を持つ

Jev は `AI_GATEWAY_API_KEY` の別のアカウント・別の料金で、呼び出し元の Claude の帳簿へ入れると帳簿の間で単位を足すことになる（要求の前提 3）。応答に使用量が入るかはコードから判断できないため、要求の数と問いの数を必ず持ち、応答に `usage` があればそのまま持つ。承認ゲート 1 で利用者が確かめる（要求の未決）。

根拠: Value 3（MVV 版 2）

### 決定 9: テストが本物の帳簿へ書いた経路を塞いで見つけるため、隔離の fixture を配布物の `plugins/ndf/conftest.py` へ移し、印の付いた行を終わりに探す

2026-10-11 の `test_*__origin.jsonl` 8 本は、プラグインのキャッシュ（`~/.claude/plugins/cache/ai-plugins/ndf/10.17.70/`）の中で pytest が走ったときに書かれた。キャッシュの `.pytest_cache` の時刻とファイル名から逆算した `tests/test_push_fix.py`・`tests/test_supervise.py` のテストが一致する。キャッシュには根の `conftest.py` が配られず、`NDF_USAGE_DIR` を一時ディレクトリへ向ける fixture が効かなかった。fixture を `plugins/ndf/` の根へ移すと、リポジトリでもキャッシュでも効く。

検査は、テストの中で書いた行に `pytest`（テストの名前）の印を付け、セッションの終わりに本物の帳簿の増えた部分に印の付いた行があれば落ちて、その名前を出す。受け入れ条件 19 の「ファイルの一覧と大きさが変われば落ちる」をそのまま採る形は、テストの間に利用者の会話のサブエージェント（決定 4 の hook）が本物の帳簿へ書くと落ちるため採らない。一覧と大きさの変化は出力に出し、落とす条件だけを印に限る。この読み替えは承認ゲート 1 で利用者が確かめる。

根拠: Value 3 / Value 6（MVV 版 2）

### 決定 10: どのリポジトリでも集計できるよう、集計を配布物の `usage-report.py` と `lib/usage_aggregate.py` に置き、スプリントの期間をマイルストーンの日時から決める

`scripts/token-usage.py` は ai-plugins の形（`/tmp/ndf-worktrees` の見分け・会話への結び付け）を持つリポジトリ固有のスクリプトで、配布先では使えない。集計の本体を `lib/` に置くと、supervise の報告（受け入れ条件 17）と同じ関数を使える。

帳簿の行はマイルストーンを持たないため、スプリントは `--milestone` のマイルストーンの `created_at` から `closed_at`（開いていれば今）の期間に読み替え、閉じた課題の数はそのマイルストーンから数える。行にマイルストーンを書く形は、起動の時点でマイルストーンを知らない呼び出し元（サブエージェント・参加者の確認）が多く、空の列が増えるため採らない。

根拠: Value 5 / Value 6（MVV 版 2）

### 決定 11: drive の中の CLI を報告に載せるため、supervise のステップの終わりに帳簿からそのステップの行を集め直す

drive.py の中で起動した CLI の結果は supervise へ戻らない。ステップの環境に置いたプランとステップ（決定 7）で帳簿の行を絞り、ランタイムごとに合計して `cur["llm_runtimes"]` に置く。`runtime` を指定した work も同じ集め方にし、Claude の行（`usage: {}`）を書かない。drive.py に使用量を標準出力で返させる形は、cross-review と cross-refactoring の 2 つの drive の出力の形を変え、記録の書き手が帳簿と別にもう 1 つ増えるため採らない。

根拠: Value 6（MVV 版 2）
