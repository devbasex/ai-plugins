# `$SCRIPTS` を決める

プラグインの `scripts/` の位置は 4 ランタイムで別々である。**候補を順に試す処理は
`scripts/resolve.sh`（解決の入口）1 本が持つ。** Skill はこの入口を探す 1 段だけを持ち、
あとは入口に尋ねる。

```bash
SCRIPTS=$(bash "$R/scripts/resolve.sh" scripts)              # プラグインルート直下の scripts/
SKILL_DIR=$(bash "$R/scripts/resolve.sh" skill cross-review)  # skills/<Skill名>/
SCRIPTS=$(bash "$R/scripts/resolve.sh" scripts fix)           # skills/<Skill名>/scripts/
```

見つからなければ理由を標準エラーへ書き、終了コード 3 で終わる（引数の誤りは 2）。

**この手順はボードの記録だけが使う値ではない。** `worktree` は `worktree-setup.sh` /
`worktree-localenv.sh` / `worktree-testenv.sh` の 3 本を呼ぶ。ボードの説明の中に置くと、
ボードと無関係な読み手がその文書を開くことになる。

## 候補の並び

入口が次の順に試し、`scripts/projects-sync.sh` を持つ最初のものを物理的な絶対パスで採る。

| 順 | 何を指すか | 手がかり |
| --- | --- | --- |
| 1 | **開発中のリポジトリ** | 現在地の git のトップの直下に `plugins/ndf/scripts/projects-sync.sh` がある |
| 2 | Claude Code が読み込んだプラグイン | Claude Code の中（`CLAUDECODE` がある）でだけ見る。環境変数 `CLAUDE_PLUGIN_ROOT`、別ランタイムの控えを通らずに届いた入口自身、`~/.claude/plugins/installed_plugins.json` の `ndf` の `installPath` の順 |
| 3 | Kiro CLI がインストーラで指したプラグインの `scripts` | `.kiro/skills/<Skill名>` がプラグインの `skills/<Skill名>` への symlink |
| 4 | Codex のマーケットプレイスの控え（`~/.codex/.tmp/marketplaces/<名前>/plugins/ndf/scripts`） | マーケットプレイス名だけが導入元で変わる |
| 5 | agy が複製した実体（`~/.gemini/config/plugins/ndf/scripts`） | 導入時にプラグインのディレクトリ全体をここへ複製する。取得元の登録が無いため位置は固定 |
| 6 | 現在地からの相対（`plugins/ndf/scripts`） | git のトップを取れない場合の受け皿 |

どれも当たらなければ、入口自身が置かれたプラグインを採る。

**開発中のリポジトリを先頭に置くのは、手元で直したスクリプトが実行されない状態を無くす
ためである。** 配布済みの控えが先に当たると、直したはずの不具合が再現し、実行しているのが
配布済みの版であることは出力からは分からない。判定は「現在地の git のトップが
`plugins/ndf/scripts` を持つか」であるため、**配布物を使う利用者の側では当たらない**。

**2 で入口自身の位置を条件付きで採るのは、参照ファイルの `${CLAUDE_PLUGIN_ROOT}` が
置き換わらないためである**（#590）。Claude Code が置き換えるのは `SKILL.md` の本文だけで、
参照ファイルを読んだ bash では下の 1 段が Codex の控えの入口を拾うことがある。入口は
自分へ届いた道が `~/.codex` / `~/.gemini` / `.kiro/skills` / `~/.claude/plugins/cache` を
通ったかを見て、通っていれば自分を採らず Claude Code の導入の記録へ戻る。通っていなければ
`claude --plugin-dir <パス>` で読み込んだ実体として採る。

**Codex が導入した実体（`~/.codex/plugins/cache/<取得元>/ndf/<版>/scripts`）は候補に
入れない。** 版ごとにディレクトリが分かれ、`*` で受けると辞書順になって `10.10.0` が
`10.2.0` より前に来る。版の比較をこの手順へ持ち込むと、手順そのものが読めない長さになる。

## 入口を探す 1 段

入口はプラグインルート直下の `scripts/` に置く。Skill の下に置くと、その Skill を配らない
配布先（agy）で届かない（`scripts/lib/README.md` の「プラグインルート直下に置く理由」）。
入口を探す段は、上の候補から「入口が 1 つあればよい」ところまで削った形である。どの入口が
当たっても、順序は入口の側が決め直す。

```bash
# 解決の入口を探す。Claude Code は SKILL.md の ${CLAUDE_PLUGIN_ROOT} を絶対パスへ置き換える。
# シングルクォートは、置き換わらなかったときにシェルへ展開させないためである。
for R in '${CLAUDE_PLUGIN_ROOT}' "$(git rev-parse --show-toplevel 2>/dev/null)/plugins/ndf" \
  ~/.claude/plugins/cache/*/ndf/* .kiro/skills/*/../.. ~/.kiro/skills/*/../.. \
  ~/.codex/{.tmp/,}marketplaces/*/plugins/ndf ~/.gemini/config/plugins/ndf plugins/ndf; do
  [ -f "$R/scripts/resolve.sh" ] && break; R=
done
SCRIPTS=$([ -n "$R" ] && bash "$R/scripts/resolve.sh" scripts) || SCRIPTS=
```

`.kiro/skills/*/../..` の `..` はカーネルが symlink の指す先から解くため、Kiro CLI の
リンクからでも実体のプラグインルートへ届く。`~/.claude/plugins/cache/*/ndf/*` はどの版が
当たってもよい。入口は cache を通って届いた自分を採らず、`installed_plugins.json` の記録へ
戻る（参照ファイルを読んで置き換わらなかったときの受け皿）。

Skill の手順では、最後の行を「見つからなければ止まる」形にする。

```bash
[ -n "$R" ] || { echo "NDF の scripts/resolve.sh が見つからない" >&2; exit 3; }
SKILL_DIR=$(bash "$R/scripts/resolve.sh" skill fix) || exit 3
```

見つからない場合は記録を飛ばす。**進行管理が理由で工程を止めない。**

## シェルが変わったら決め直す

**この値はシェルをまたいで持ち越されない。** コマンドの実行ごとにシェルが分かれる環境では、
決めた値は次の実行に残らない。続けて実行するときは 1 つのブロックへまとめ、分かれるなら
その先頭で決め直す。

手順書がこの値を受け取る場合は、「この文書が受け取る値」の表で宣言する（`worktree` の
`SKILL.md` がその形である）。

「入口を探す 1 段」の bash はそのままテストの対象になっている。
`development-workflow/tests/test_projects_scripts_lookup.py` と
`worktree/tests/test_scripts_reference.py` がこの節の bash のコードブロックを読み出し、
4 ランタイムの配置を作った上で実行する。入口そのものの順序は
`scripts/tests/test_resolve.py` が 4 ランタイム × 開発中 / 配布済みの組み合わせで確かめる。
**候補を足すときは入口とこの 1 段の両方を直し、3 つのテストへ配置を足す。**
