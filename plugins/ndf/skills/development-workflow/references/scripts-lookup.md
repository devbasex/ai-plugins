# `$SCRIPTS` を決める

プラグインの `scripts/` の位置は 4 ランタイムで別々である。候補を順に試し、最初に当たった
ものを絶対パスで採る。

**この手順は盤面の記録だけが使う値ではない。** `worktree` は `worktree-setup.sh` /
`worktree-localenv.sh` / `worktree-testenv.sh` の 3 本を呼ぶ。盤面の説明の中に置くと、
盤面と無関係な読み手がその文書を開くことになる。

## 候補の並び

| 順 | 何を指すか | 手がかり |
| --- | --- | --- |
| 1 | **開発中のリポジトリ** | 現在地の git のトップの直下に `plugins/ndf/scripts/projects-sync.sh` がある |
| 2 | Claude Code の配布物（`~/.claude/plugins/cache/<マーケットプレイス>/ndf/<版>/scripts`） | `SKILL.md` の `${CLAUDE_PLUGIN_ROOT}` が絶対パスへ置き換わる |
| 3 | Kiro CLI がインストーラで指したプラグインの `scripts` | `.kiro/skills/<Skill名>` がプラグインの `skills/<Skill名>` への symlink |
| 4 | Codex のマーケットプレイスの控え（`~/.codex/.tmp/marketplaces/<名前>/plugins/ndf/scripts`） | マーケットプレイス名だけが導入元で変わる |
| 5 | agy が複製した実体（`~/.gemini/config/plugins/ndf/scripts`） | 導入時にプラグインのディレクトリ全体をここへ複製する。取得元の登録が無いため位置は固定 |
| 6 | 現在地からの相対（`plugins/ndf/scripts`） | git のトップを取れない場合の受け皿 |

**開発中のリポジトリを先頭に置くのは、手元で直したスクリプトが実行されない状態を無くす
ためである。** 配布済みの控えが先に当たると、直したはずの不具合が再現し、実行しているのが
配布済みの版であることは出力からは分からない。判定は「現在地の git のトップが
`plugins/ndf/scripts` を持つか」であるため、**配布物を使う利用者の側では当たらない**。

**Codex が導入した実体（`~/.codex/plugins/cache/<取得元>/ndf/<版>/scripts`）は候補に
入れない。** 版ごとにディレクトリが分かれ、`*` で受けると辞書順になって `10.10.0` が
`10.2.0` より前に来る。版の比較をこの手順へ持ち込むと、手順そのものが読めない長さになる。

```bash
# **開発中のリポジトリを先に見る。** 現在地の git のトップが `plugins/ndf/scripts` を
# 持つなら、そこが手元で直している実体である。持たないリポジトリでは当たらない。
DEV_ROOT=
if TOP=$(git rev-parse --show-toplevel 2>/dev/null) && \
   [ -f "$TOP/plugins/ndf/scripts/projects-sync.sh" ]; then
  DEV_ROOT=$TOP/plugins/ndf
fi

# Claude Code は SKILL.md 内の ${CLAUDE_PLUGIN_ROOT} をプラグインルートの絶対パスへ置き換えて
# から渡す。シングルクォートで囲むのは、置き換えられなかったときにシェルへ展開させないため
# である。Codex と Kiro CLI は置き換えないため、両者はそれぞれの配置から探す。
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'
case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac

# Kiro CLI のインストーラは `.kiro/skills/<Skill名>` を、プラグインの `skills/<Skill名>` への
# symlink として張る。`.kiro/skills` 自体は実体のディレクトリなので、そこから 1 つ上をたどっても
# プラグインへは戻れない。symlink の指す先を読み、その 2 つ上をプラグインルートとして採る。
#
# 同じ `skills` に別のプラグインの symlink が並ぶことがある。インストーラが消すのは NDF 配下を
# 指すリンクだけで、他は残す。最初のリンクで打ち切ると、その別のプラグインを採ってしまうため、
# 指す先の 2 つ上に `scripts/projects-sync.sh` があるまで候補を調べ続ける。
#
# `readlink -f` は使わない。BSD 系（macOS）の `readlink` の `-f` は書式の指定であり、続く語を
# 書式として読む。指す先を読むだけの `readlink <パス>` は GNU と BSD の両方で同じに働く。
# 相対パスで張られたリンクのために、リンクのある位置からの相対として組み立てる。
KIRO_ROOT=
for link in .kiro/skills/*/ "${HOME:-}/.kiro/skills/"*/; do
  [ -L "${link%/}" ] || continue
  target=$(readlink "${link%/}") || continue
  case "$target" in
    /*) ;;
    *) target="$(dirname "${link%/}")/$target" ;;
  esac
  [ -f "$target/../../scripts/projects-sync.sh" ] || continue
  KIRO_ROOT="$target/../.."
  break
done

# Codex はマーケットプレイスのスナップショットの下へプラグインを展開する。名前は導入元で
# 変わるため `*` で受ける。
#
# agy は取得元の登録を持たず、導入の操作がプラグインのディレクトリ全体を
# `~/.gemini/config/plugins/<plugin.json の name>/` へ複製する。名前は `ndf` で固定であり、
# `dev.agy/scripts` の symlink は実体へ解決されて複製される。
SCRIPTS=
for candidate in \
  ${DEV_ROOT:+"$DEV_ROOT/scripts"} \
  ${PLUGIN_ROOT:+"$PLUGIN_ROOT/scripts"} \
  ${KIRO_ROOT:+"$KIRO_ROOT/scripts"} \
  "${HOME:-}/.codex/.tmp/marketplaces/"*/plugins/ndf/scripts \
  "${HOME:-}/.codex/marketplaces/"*/plugins/ndf/scripts \
  "${HOME:-}/.gemini/config/plugins/ndf/scripts" \
  "plugins/ndf/scripts"
do
  [ -f "$candidate/projects-sync.sh" ] || continue
  SCRIPTS="$(cd "$candidate" && pwd)"
  break
done
[ -n "$SCRIPTS" ] || SCRIPTS=  # 見つからなければ記録を飛ばす
```

見つからない場合は記録を飛ばす。**進行管理が理由で工程を止めない。**

## シェルが変わったら決め直す

**この値はシェルをまたいで持ち越されない。** コマンドの実行ごとにシェルが分かれる環境では、
決めた値は次の実行に残らない。続けて実行するときは 1 つのブロックへまとめ、分かれるなら
その先頭で決め直す。

手順書がこの値を受け取る場合は、「この文書が受け取る値」の表で宣言する（`worktree` の
`SKILL.md` がその形である）。

この bash はそのままテストの対象になっている。`development-workflow/tests/test_projects_scripts_lookup.py`
と `worktree/tests/test_scripts_reference.py` がこの節の bash のコードブロックを読み出し、
4 ランタイムの配置を作った上で実行する。手順の側だけが変わって解決が外れる状態にならない。
**候補を足すときは両方のテストへ配置を足す。** 片方だけを直すと、もう片方が前のランタイムの
数のまま通り続ける。
