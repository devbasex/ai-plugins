---
name: install-wrapper
description: "Install, remove, or inspect the NDF relay for Claude Code. 明示指示のみで実行する。Use when setting up or removing the relay that restarts claude at section breaks（ラッパーを入れる・ラッパーを外す・ラッパーの状態）."
argument-hint: "install | uninstall | status"
disable-model-invocation: true
allowed-tools:
  - Bash
---

# ラッパーの導入・取り外し・状態

**ラッパー（中継）を使うかは利用者が決める。** この Skill が、ラッパーの起動の部品を利用者の環境へ置く
唯一のエントリポイントである。SessionStart hook は置かない。ラッパーの振る舞いは
`development-workflow` の [references/relay.md](../development-workflow/references/relay.md) にある。

例: ラッパーを初めて入れる。

1. claude の中で `/ndf11:install-wrapper` を打つ
2. `ndf relay install` が、起動の部品の複製を `~/.claude/ndf/` の下（隣の `relay.root` がプラグインの根を指す）に、
   PATH のエントリポイントを `~/.local/bin/ndf-relay` に置く
3. 次に開いたシェルで `ndf-relay run` と打つと、ラッパーを通って claude が起動する

## 引数

| 引数 | 副命令 | すること |
| --- | --- | --- |
| 無し・`install` | `ndf relay install` | 複製と PATH のエントリポイントを置く（あれば今の版で置き直す）。旧い NDF の複製があれば残して警告する。旧いラッパーを止めた後に `ndf relay install --replace-old` で置き換える |
| `uninstall` | `ndf relay uninstall` | 置いた複製と PATH のエントリポイントを消す。利用者が置いた同じ名前のファイルは消さない |
| `status` | `ndf relay status` | 複製と PATH のエントリポイントの有無と状態を示す。今のセッションがラッパーの直接の子かは `ndf relay notice` の `metrics.inside` で示す。何も書かない |

それ以外の引数では、この表を示して何もしない。

## 手順

副命令を **1 回だけ** 実行して、結果 JSON の `summary`・`items`・`warnings` と終了コードをそのまま示す。
`<副命令>` は上の表で決めた値に置き換える。

```bash
command -v ndf >/dev/null 2>&1 || { echo "ndf が PATH に無い" >&2; exit 3; }   # development-workflow/references/scripts-lookup.md
ndf relay <副命令>
echo "exit=$?"
```

| 終了コード | 意味 | 伝えること |
| ---: | --- | --- |
| 0 | 置いた・外した・既にそうなっていた | `summary` と `warnings`（PATH の追加が要るなど）のとおり |
| 3 | 前提が無い（複製がプラグインを見つけられない・`--replace-old` で旧いラッパーが動いている）。何も変えていない | `summary` の理由。旧いラッパーなら止めてから打ち直す |

**実行前の確認は置かない。** 明示指示でだけ動き、置くのは `~/.claude/ndf/` と `~/.local/bin/` の自分の印のあるファイルだけで、`uninstall` で戻せる。
シェルの設定ファイルは書き換えない。`~/.local/bin` が PATH に無いときは、`warnings` の 1 行を利用者へ示す。

## 置き場所

| もの | パス |
| --- | --- |
| 複製と根の指し先 | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/` の下の複製と `relay.root` |
| PATH のエントリポイント | `${XDG_BIN_HOME:-~/.local/bin}/ndf-relay` |

**devbase では `~/.claude` が同じアカウントグループのコンテナで共有される。** `install` と
`uninstall` の効果は、同じグループの全コンテナに及ぶ。
