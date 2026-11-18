# `ndf` を確かめる

NDF の機能は `bin/ndf` のサブコマンドである。Skill とエージェントの本文は `ndf <サブコマンド>` を名前で呼び、プラグインの保存先やスクリプトのパスを探さない。

- Claude Code は、有効なプラグインの `bin/` を Bash の `PATH` へ足す
- Codex と端末は、`PATH` 上に置いた `bin/ndf` へのシンボリックリンクで呼ぶ（`bin/ndf` の冒頭の注記）

使えるサブコマンドと引数は [commands.md](../../../docs/contracts/commands.md) が正であり、ここに無いサブコマンドは無い。引数の細部は各サブコマンドの `--help` で確かめる。

## 確かめる 1 行

手順の先頭で次を打つ。`ndf` が見つからなければ、理由を標準エラーへ書いて終了コード 3 で止まる。

```bash
command -v ndf >/dev/null 2>&1 || { echo "ndf が PATH に無い: プラグインを有効にするか、bin/ndf へのシンボリックリンクを PATH に置く" >&2; exit 3; }
```

この 1 行は `tests/skills/test_scripts_lookup.py` が取り出し、`ndf` を含まない `PATH` で打って終了コード 3 を確かめる。

conductor が直に打つサブコマンドは [conductor-entrypoints.md](conductor-entrypoints.md) にある。
