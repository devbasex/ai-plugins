# 用語集: NDF の文脈と使用量（`ndf-context`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

context window の量を測り、工程境界で会話を続けるか切るかを決め、使用量を記録するときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| context window | — | 1 回の会話が保持する文脈の全体と、その量 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| レートリミット中断 | — | 利用上限（429）で層が途中で終わること。記録の ending が rate_limit | 上限の中断 | — | `docs/specifications/ndf-context-window-metrics.md` |
| リセット時刻 | — | 利用上限が解ける時刻（quotaLimits.resetsAt、resets_at） | 解除時刻 | — | `docs/specifications/ndf-context-window-metrics.md` |
| 自動継続 | — | リセット時刻に Claude Code が conductor へ積む入力（origin.kind が auto-continuation） | 自動の継続 | — | `docs/specifications/ndf-agent-layers-unattended-run.md` |
| 親エージェント | — | その記録を起動したエージェント。.meta.json の toolUseId でたどる（parent_agent_id） | 起動元 | — | `docs/specifications/ndf-context-window-metrics.md` |
| ピーク使用量 | — | 応答ごとの入力トークンの合計の最大 | 最大充填 | — | `docs/specifications/ndf-context-window-metrics.md` |
| 合成応答 | — | API を呼ばずに Claude Code が記録へ書いた応答（message.model が <synthetic>） | 合成の応答 | — | `docs/specifications/ndf-context-window-metrics.md` |
| カットポイント | — | context window を切ってよい 4 点。3 層ではフェーズの境になる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ポーリング | — | 待つ間に、状態を確かめる呼び出しを繰り返すこと | — | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| フォアグラウンド Bash | — | run_in_background を付けずに実行する Bash。終わるまで呼び出しが返らない | 前景の Bash | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| コンテキスト量 | — | 1 回の API 呼び出しで読んだトークン数（input_tokens + cache_read_input_tokens + cache_creation_input_tokens） | 文脈量 | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 固定費 | — | 作業を始める前に context window が既に埋まっている分（指示・規約・Tool の定義）。実行を 1 つ起動するたびに掛かる | — | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| 委譲 | — | 別の文脈（サブエージェント・別の CLI プロセス）へ作業を渡し、結果だけを受け取ること | — | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| 実作業 | — | ピーク使用量から固定費を引いた分。その会話が実際に読み書きした量（work） | — | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| 使用量の使用量ログ | `usage_ledger` | NDF が起動したエージェントの呼び出し 1 回ごとに、バージョン・呼び出し元・モデル・使用量を 1 行で追記する jsonl。ランタイムごと（Claude・codex・kiro・agy・jev。jev は使用量ログだけのランタイムで、席順の ALL_RUNTIMES には入れない）のディレクトリに、リポジトリごとのファイルを置く（<使用量ログのディレクトリ>/<ランタイム>/<owner>__<repo>.jsonl） | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| 呼び出し元 | — | エージェントの呼び出しを起動した Skill またはスクリプト（使用量ログの行の source）と、そのステップの役割（kind）の組。表示は source:kind（例: supervise:work・cross-review:review） | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| 起動の共有処理 | — | LLM の CLI と Jev を起動する処理を集めた 4 か所（launch-cli.sh・supervise_lib/claude.py の call_claude・lib/auth.py のモデルの確認・lib/jev.py の _post）。起動の後に使用量の使用量ログへ 1 行を書く | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| Claude の使用量ログの全体 | — | Claude の使用量の使用量ログの claude -p の行（usage）とサブエージェントの行に、行ごとの modelUsage の和から usage と親を同じくするサブエージェントの行を引いた残り（hook の効かない claude -p の中のサブエージェント）と、会話の記録だけの claude -p の会話（セッション ID が使用量ログに無いもの）を足した値。同じ呼び出しは 1 回だけ数える。トークン削減の効果を測る主な値 | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| 会話の記録だけの呼び出し | — | 使用量の使用量ログに行が無く、Claude Code の会話の記録（~/.claude/projects/）にだけ残る claude -p の会話とサブエージェント。使用量ログができる前の分と、NDF のコードの外で打った claude -p が当たる | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| 判断開始の値 | — | これ以上になったら hook（guard `context-limit`）が conductor に利用者の入力ごとに 1 度知らせ、conductor が次の切りどころで引継ぎ文書を書いて切る文脈の量（既定 450,000。上限の設定の `context.decide-from`）。工程の途中の Tool は止めない | — | — | `docs/specifications/ndf-context-thresholds.md` |
| 運用上限 | — | これ以上になったら hook が引継ぎに要る呼び出しを除く Tool の呼び出しを止め続け、中継のラッパーが子に引継ぎを頼む文脈の量（既定 600,000。上限の設定の `context.limit`。`NDF_CONTEXT_LIMIT` で替える）。判断開始の値と組で 1 通りに働き、方針の名前を持たない | 再起動の方針、現行方式、試行方式、適用値 | — | `docs/specifications/ndf-context-thresholds.md` |
| 容量の余白 | — | 実行環境の窓の上限（または自動圧縮が始まる量）から、次の作業・ツール結果・出力と進行の保存のために空けておく量 | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 再起動の理由 | — | 会話を切った・止めたときの理由（`ndf relay context record --reason`）。容量・運用上限・費用・独立性・手動・カットポイントと、オーガナイザーの長い待ちの 7 つ。容量と費用は過去の行を読むために残り、今は書かない | — | — | `docs/specifications/ndf-context-thresholds.md` |
| 復元の期間 | — | 再起動した新しいセッションが、引継ぎ文書を読み始めてから最初の工程を起動するまで | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 復元の量 | — | 再起動した新しいセッションが「復元完了」を記録した時点の文脈の量。引継ぎ文書と、状態を戻すために読んだ文書の分を含む。会話を替える費用の見込みの式の Q | — | — | `docs/specifications/ndf-context-continue-or-cut-measurement.md` |
| 計測の条件 | — | 続けるか切るかを比べるときの 2 つの設定。「続ける」は運用上限を窓 − 容量の余白まで上げた設定、「閾値で切る」は判断開始の値と運用上限を候補の閾値にした設定。値を変えて比べる | — | — | `docs/specifications/ndf-context-continue-or-cut-measurement.md` |
| 計測の回 | — | 計測の条件 1 つで、フェーズの起動からその後の承認ゲートの回答を受けた最初の呼び出しまでを 1 回として数えたもの | — | — | `docs/specifications/ndf-context-continue-or-cut-measurement.md` |
| 待ちの前に切る規則 | — | 承認ゲートか人の判断の質問を出す直前に、文脈が決めた量を超えていれば会話を切り、新しいセッションで質問を出し直す規則 | — | — | `docs/specifications/ndf-context-continue-or-cut-measurement.md` |
| 境界の判定 | — | 工程の起動 1 回ごとに出す継続 / 再起動 / 拒否と、その帯・理由・使った値の組。1 件ずつ記録に残る | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 作業の識別子 | — | 複数のセッションを 1 つの作業として結ぶキー。起動の引数かスプリントに含まれる課題番号の組（#829 #830） | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 費用の見込み | — | 判断の帯で、続けた場合と再起動した場合の次の工程までの費用を、記録から較正した中央値で比べた値 | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 引継ぎの期間 | — | conductor が引継ぎ文書を書き始める前に記録した「引継ぎ開始」の時点から、そのセッションの最後の行まで | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 使用量ログ | `usage_log` | 応答ごとの使用量を追記する記録（usage.jsonl） | 帳簿 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| プロセス名の待ち | — | pgrep -f / pkill -f を while / until のループの条件か本体に置いて、プロセスの終わりを待つ形。待つ側のコマンド行に一致して終わらないため、hook が止める | — | — | `docs/specifications/ndf-hook-wait-guards-loop-and-process-name.md` |
