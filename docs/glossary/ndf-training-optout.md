# 用語集: NDF の学習の設定（`ndf-training-optout`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

LLM のアカウントが入力を学習に使う設定かを確かめ、学習に使う設定なら claude・codex を起動する前に Off へ書き換えるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 学習の設定 | — | アカウントの入力を学習に使うかの設定。claude は「Help improve our AI models」（grove_enabled）、codex は ChatGPT の「Improve the model for everyone」（training_allowed）と Codex の環境の学習（codex_training_allowed・codex_training_allowed_v2）。true なら入力を学習に使う | — | — | — |
| 学習の設定の確認 | — | 学習の設定を読み、学習に使う設定なら書き換えて読み直し、結果を時刻つきで記録すること（ndf training check と、記録が無いか期限切れのときの起動の口） | — | — | `docs/specifications/ndf-training-optout-rewrite.md` |
| 学習の設定の書き換え | — | 確かめた結果が学習に使う設定のとき、true だったキーだけを false へ書き込み、読み直して結果を決めること。書き換えたキーごとの前後の値と時刻を記録に残す | — | — | `docs/specifications/ndf-training-optout-rewrite.md` |
| OAuth でない接続 | — | training_cache.CLAUDE_FOREIGN_AUTH の変数（ANTHROPIC_API_KEY・ANTHROPIC_AUTH_TOKEN・CLAUDE_CODE_USE_BEDROCK・CLAUDE_CODE_USE_VERTEX・CLAUDE_CODE_USE_FOUNDRY）のどれかが空でない起動。学習の設定が無く、読みも書きも送らない。ndf-relay の「従量の接続」より範囲が広い | — | — | — |
| ChatGPT でないログイン | — | codex の auth.json の auth_mode が chatgpt でない起動（API キーのログイン）。ChatGPT のアカウントの学習の設定が無く、読みを送らない | — | — | — |
