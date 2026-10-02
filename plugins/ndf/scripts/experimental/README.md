# 実験版のスクリプト

**ここに置くものは、呼んだときだけ働く。** 既定の振る舞い（hook・Skill 本文の手順・他のスクリプト）から
参照しない。参照すると `tests/test_experimental.py` が落ちる。

- 実践の中で試すための置き場である。課題を起票せず、その場で実装して使い始める
- 台帳はリポジトリの `docs/ndf-experiments.md` にある。足したら 1 行を書き、使うたびに結果を足す
- 効いたものは安定版の経路（設計・レビュー・リリース）で本体へ移す。使われなかったものは消す
- 結果の形は `lib/step_result.py` に従う（他のスクリプトから読めるようにするため）

## `training-optout.py`: LLM へ渡す前に「学習に使わない設定」かを確かめる

利用規約が AI の学習への利用を禁じるデータを扱うリポジトリで、確かめた LLM にだけ入力を渡すための
エントリポイントである。終了コードは 0（すべて学習に使わない）・1（学習に使う設定がある）・3（確かめられない。
認証が無い・トークンの期限切れ・応答の形が違う・HTTP の失敗・OAuth 以外の接続・`unsupported`）。0 のときだけ渡す。
claude 以外は `unsupported` を返す。

確かめられるのは OAuth のアカウントだけである。次の接続は確かめられないとして 3 を返す。

- 起動時の環境に OAuth より優先される認証の変数（`ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_USE_BEDROCK`・
  `CLAUDE_CODE_USE_VERTEX`）がある
- 従量の接続の宣言（`NDF_SUPERVISE_CLAUDE_FALLBACK`、無ければ登録アカウントの置き場の `metered.json`）がある。supervise と
  ラッパーは利用上限でこの接続へ同じ入力を送り直す。宣言を外して流すには `NDF_SUPERVISE_CLAUDE_FALLBACK=` を空で定義して
  起動する（保存した `metered.json` も読まなくなる）

HTTP は `lib/notify.py`（httpx）が呼ぶ。httpx・slack_sdk・python-dotenv が import できない python3 で起動すると、
起動のたびに `lib/deps.py` が uv の環境（`~/.cache/ndf/venv/<版>/`）へ起動し直す。手元の実測では、環境ができていれば
python3 で 63〜111 ms、その環境の `bin/python` を直に指すと 28〜34 ms（HTTP を除く 3 回ずつ）。環境が無い初回は作るのに
数秒かかり、uv も無ければ `~/.local/bin` へ入れる。直に指すパスは NDF の版が上がると変わる。

supervise のプランでは、LLM を使うステップの前に `run` のステップを置く（0 以外なら止まる）。**supervise は利用上限で
登録アカウントを切り替える**ため、登録アカウントがあるときは、その設定ディレクトリを `--config-dir` へすべて並べる
（起動時の環境のアカウントに加えて確かめる）。並べないと、確かめていないアカウントの claude へ入力が渡る。置き場は
`${NDF_ACCOUNTS_DIR:-<共有の設定ディレクトリ>/ndf/accounts}/<名前>/` である。プランの途中で登録したアカウントは確かめない。
supervise は `NDF_SUPERVISE_CLAUDE_FALLBACK=` を空で定義して起動する（`NDF_SUPERVISE_CLAUDE_FALLBACK= python3 <ai-plugins>/plugins/ndf/scripts/supervise.py ...`）。
定義しないと、宣言があるときに `run` のステップが 3 で止まる。

このスクリプトはトークンを更新しない（認証ファイルを書かない）。動いていない登録アカウントのアクセストークンは
期限が切れていることが多く、そのアカウントが 3（`トークンの期限切れ`）で止まる。その都度、
`CLAUDE_CONFIG_DIR=<アカウントの設定ディレクトリ> claude` を 1 度起動して更新してから流し直す。

```json
{"id": "optout", "type": "run", "timeout": 60,
 "cmd": "python3 <ai-plugins>/plugins/ndf/scripts/experimental/training-optout.py check --runtime claude --config-dir ~/.claude/ndf/accounts/*/",
 "next": "impl"}
```

登録アカウントが無ければ `--config-dir` を外す（`*/` が何にも当たらないと、その字面のディレクトリを確かめられずに 3 で止まる）。

Claude Code の hook では、`.claude/settings.json` の `UserPromptSubmit` から呼び、0 以外なら終了コード 2 で
入力を止める（`UserPromptSubmit` の終了コード 2 は、その入力を LLM へ渡さずに消し、標準エラーを利用者へ見せる）。
`SessionStart` では止められない（終了コードによらずセッションが続く）。入力のたびに 1 回 HTTP を呼び、python3 で起動すれば上の再起動の費用も毎回かかる。

```json
{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
  "command": "python3 <ai-plugins>/plugins/ndf/scripts/experimental/training-optout.py check >/dev/null || { echo '学習に使わない設定を確かめられない。/privacy-settings を開く' >&2; exit 2; }"}]}]}}
```
