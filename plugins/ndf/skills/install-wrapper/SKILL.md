---
name: install-wrapper
description: "Install, remove, or inspect the NDF relay for Claude Code. 明示指示のみで実行する。Use when setting up or removing the relay that restarts claude at section breaks（ラッパーを入れる・ラッパーを外す・ラッパーの状態）."
argument-hint: "install | uninstall | status"
disable-model-invocation: true
allowed-tools:
  - Bash
---

# ラッパーの導入・取り外し・状態

**ラッパー（`relay.py`）を使うかは利用者が決める。** この Skill が、利用者のシェル設定を書き換える
唯一の入口である。SessionStart hook はシェル設定を書かない。ラッパーの振る舞いは
`development-workflow` の [references/relay.md](../development-workflow/references/relay.md) にある。

例: ラッパーを初めて入れる。

1. claude の中で `/ndf:install-wrapper` を打つ
2. コピーを `~/.claude/ndf/relay.py`、`claude` の関数を `~/.claude/ndf/shellrc` に置き、`~/.bashrc`（macOS の bash では
   `~/.bash_profile`）をバックアップしてから、`shellrc` を読む 1 行を管理ブロック（`# >>> ndf relay >>>` 〜 `# <<< ndf relay <<<`）で足す
3. 次に開いたシェルで `claude` と打つと、ラッパーを通って起動する

## 引数

| 引数 | 副命令 | すること |
| --- | --- | --- |
| 無し・`install` | `install` | コピー・コピーの版・ラッパーの rc を置き（あれば今の版で置き直す）、読み込みの 1 行を置く。10.17.4〜10.17.6 が足した管理ブロック（alias を直に持つ）は、中だけを読み込みの行へ置き換える |
| `uninstall` | `uninstall` | `~/.bashrc`・`~/.bash_profile`・`~/.zshrc` の管理ブロックを外し（バックアップの後）、読み込み先のファイル・ラッパーの rc・コピー・10.17.4〜10.17.6 のコピーを消す |
| `status` | `status` | 読み込み先・管理ブロックの有無と形・コピーの有無と版と、今のセッションがラッパー経由か（ラッパー経由・通らずに起動・ラッパーは終わっている・直接の子ではない・判定できない）を示す。通らずに起動で読み込みの行があるときは、読み込みの前に開いたシェルか IDE からの起動と添える。macOS の bash で読み込みの行が `~/.bashrc` にしか無く、ログインシェルが読むファイルが `~/.bashrc` を読まないときは警告を出す。何も書かない |

それ以外の引数では、この表を示して何もしない。

## 手順

プラグインのルートを決め、`relay.py` の副命令を **1 回だけ** 実行して、出力と終了コードを
そのまま示す。`<副命令>` は上の表で決めた値に置き換える。

```bash
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'
case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac
RELAY="$PLUGIN_ROOT/scripts/relay.py"
[ -n "$PLUGIN_ROOT" ] && [ -f "$RELAY" ] || { echo "ndf のプラグインのルートを解決できない" >&2; exit 1; }
python3 "$RELAY" <副命令>; echo "exit=$?"
```

| 終了コード | 意味 | 伝えること |
| ---: | --- | --- |
| 0 | 置いた・外した・既にそうなっていた | 出力のとおり |
| 1 | 何も変えていない（既存の `claude` の定義・bash と zsh 以外のシェル・閉じの無い管理ブロック・引用できないパス） | 出力の理由と、示された 1 行 |
| 3 | 別の導入が動いている・書けない。何も変えていない | 少し待って打ち直す |

**実行前の確認は置かない。** 明示指示でだけ動き、書く前にバックアップを取り、`uninstall` で戻せる。
**シェルへ貼るコマンドを報告へ足さない**（パスを引用し直すと、誤りで別のコマンドが動きうる）。

## 置き場所

| もの | パス |
| --- | --- |
| コピー・コピーの版・ラッパーの rc | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/relay.py`・`relay.version`・`shellrc` |
| 読み込みの 1 行 | `DEVBASE_SHELLRC_DIR` がディレクトリを指せば `$DEVBASE_SHELLRC_DIR/ndf-relay.sh`、無ければ `$SHELL` の設定（bash は `~/.bashrc`（macOS では `~/.bash_profile`）、zsh は `${ZDOTDIR:-~}/.zshrc`）の管理ブロック |
| 記録 | `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/`（`rc-added`・`rc-user` など） |

**devbase では `~/.claude` が同じアカウントグループのコンテナで共有される。** `install` と
`uninstall` の効果は、同じグループの全コンテナに及ぶ。
