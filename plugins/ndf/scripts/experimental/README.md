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
認証が無い・応答の形が違う・HTTP の失敗・`unsupported`）。0 のときだけ渡す。claude 以外は `unsupported` を返す。

supervise のプランでは、LLM を使うステップの前に `run` のステップを置く（0 以外なら止まる）。

```json
{"id": "optout", "type": "run", "timeout": 60,
 "cmd": "python3 <ai-plugins>/plugins/ndf/scripts/experimental/training-optout.py check --runtime claude",
 "next": "impl"}
```

Claude Code の hook では、`.claude/settings.json` の `SessionStart` から呼び、0 以外なら利用者へ知らせる。

```json
{"hooks": {"SessionStart": [{"hooks": [{"type": "command",
  "command": "python3 <ai-plugins>/plugins/ndf/scripts/experimental/training-optout.py check >/dev/null || echo '学習に使わない設定を確かめられない。/privacy-settings を開く' >&2"}]}]}}
```
