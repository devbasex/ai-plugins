# 用語集: NDF の学習の設定（`ndf-training-optout`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

LLM のアカウントが入力を学習に使う設定かを確かめ、NDF のセッションの開始で Off にするときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 学習の設定 | — | アカウントの入力を学習に使うかの設定。claude は「Help improve our AI models」（grove_enabled）、codex は ChatGPT の「Improve the model for everyone」（training_allowed）と Codex の環境の学習（codex_training_allowed・codex_training_allowed_v2）。true なら入力を学習に使う | — | — | — |
| 学習の設定の確認 | — | 学習の設定を読み、1 行の JSON で返すこと（check）。書き換えない | — | — | — |
| 学習の設定の書き換え | — | NDF のセッションの開始で、true の学習の設定を false にすること | — | — | — |
| 書き換えの無効化 | — | 利用者が環境変数 NDF_TRAINING_OPTOUT=0 で学習の設定の書き換えを止めること。確認（check）は止めない | — | — | — |
| OAuth でない接続 | — | claude_accounts.FOREIGN_AUTH_ENV の変数（ANTHROPIC_API_KEY・ANTHROPIC_AUTH_TOKEN・CLAUDE_CODE_USE_BEDROCK・CLAUDE_CODE_USE_VERTEX・CLAUDE_CODE_USE_FOUNDRY）のどれかが空でない起動。学習の設定が無く、読みも書きも送らない。ndf-relay の「従量の接続」より範囲が広い | — | — | — |
| ChatGPT でないログイン | — | codex の auth.json の auth_mode が chatgpt でない起動（API キーのログイン）。ChatGPT のアカウントの学習の設定が無く、読みを送らない | — | — | — |
