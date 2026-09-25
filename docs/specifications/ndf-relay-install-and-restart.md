# ラッパーの明示の導入・再起動のコマンド・関門を越えない守り

ラッパー（`plugins/ndf/scripts/relay.py`）を、利用者が明示に打つ `/ndf:install-wrapper` でだけ導入し、
`/ndf:restart` で好きな時点に区間を切り替えられるようにする。あわせて、ラッパーが子の端末へ書く
`/exit` が質問（`AskUserQuestion`）の答えにならない守りを入れ、2 つ目以降の区間へ最初の区間の
起動の方針の引数を引き継ぐ。Claude Code だけが対象である（#928・#936）。

**ラッパーの本体の契約（素通し・静まり・上限・空回り・印・記録）は
[ndf-relay-segment-restart.md](ndf-relay-segment-restart.md) が持つ。** この文書は、導入・再起動・
守り・引数の引継ぎの契約と、それぞれをそう決めた理由を残す。

| 何を読むか | 正本 |
| --- | --- |
| 導入・取り外し・状態の表示の手順と終了コードの伝え方 | `plugins/ndf/skills/install-wrapper/SKILL.md` |
| 再起動の手順・再開用のコマンドの決め方・ラッパーの外での示し方 | `plugins/ndf/skills/restart/SKILL.md` |
| 利用者向けの始め方・外し方・戻し方・守り・区間をまたいで設定を保つ方法 | `plugins/ndf/skills/development-workflow/references/relay.md` |

## 目的

- **利用者のシェル設定を書き換えるのを、利用者の明示の操作だけにする。** 導入・更新・取り外しを
  1 つのコマンドが持つ
- **10.17.4〜10.17.6 が黙って足した囲みを、利用者が知って選べる状態にする。** 知らせずに残すことも、知らせずに
  消すこともしない
- **再起動を 1 つのコマンドに畳む。** プラグインの更新の反映・文脈を切りたいときにも、区間の切れ目と
  同じ経路で切り替える
- **ラッパーが関門（`AskUserQuestion`）の答えを代わりに送らない。** 親から子へ入力を送る経路の可否と条件を
  実測で残し、送ってよい範囲を不変条件にする
- **devbase の `alias claude` が足す引数（`--dangerously-skip-permissions`）を、2 つ目以降の区間でも
  落とさない**（#936）

**例: 10.17.4〜10.17.6 を入れた利用者・新しい利用者・devbase の利用者。**

| 順 | 誰が | 何をする |
| ---: | --- | --- |
| 1 | 10.17.4〜10.17.6 の利用者 | 次の版へ上げて claude を起動する。SessionStart hook の `relay.py startup` が、10.17.4〜10.17.6 が置いた旧い写し（`~/.local/share/ndf/relay.py`）を今の版で置き直し、`rc-added` に載った `~/.bashrc` に囲みが残っているのを読んで、「10.17.4〜10.17.6 が自動で足したもの…」の 1 行を 1 度だけ出す。シェルの設定は書かない |
| 2 | 同じ利用者 | そのまま `claude` と打つ。囲みの alias が、置き直された旧い写しのラッパーを起こす |
| 3 | 新しい利用者 | claude の中で `/ndf:install-wrapper` を打つ。写しを `~/.claude/ndf/relay.py`、`claude` の関数を `~/.claude/ndf/shellrc` に置き、`~/.bashrc`（macOS の bash では `~/.bash_profile`）をバックアップしてから、`shellrc` を読む 1 行の囲みを足す。次に開いたシェルから効く |
| 4 | どちらの利用者も | ラッパーの下で `/ndf:restart` を打つ。claude が再開用のコマンドを `ndf-next` のブロックで出して応答を終え、ラッパーが静まり（5 秒）の後に `/exit` → 更新 → 起動を行う |
| 5 | 外したい利用者 | `/ndf:install-wrapper uninstall`。`~/.bashrc`・`~/.bash_profile`・`~/.zshrc` の囲みを外し（バックアップの後）、`shellrc`・写し・旧い写しを消す |
| 6 | devbase の利用者（devbasex/devbase#253 の後） | `/ndf:install-wrapper` を打つ。`DEVBASE_SHELLRC_DIR` があるので、`~/.bashrc` ではなく `$DEVBASE_SHELLRC_DIR/ndf-relay.sh` に読み込みの 1 行を置く。コンテナを作り直しても写し・`shellrc`・`ndf-relay.sh` は `/persistent/group` に残る |

## 用語

| 用語 | 意味 |
| --- | --- |
| 囲み | シェルの設定の `# >>> ndf relay >>>` から `# <<< ndf relay <<<` までの行 |
| 自動の囲み | 10.17.4〜10.17.6 の SessionStart hook が足した囲み。状態の親の記録 `rc-added` に載り、`rc-user` に載らないパスの、今もある囲み |
| 写し | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/relay.py`。ラッパーの rc の関数が起こすラッパーの本体 |
| 写しの版 | 写しの横の `relay.version`。写しを置いたプラグインの版の 1 行 |
| ラッパーの rc | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/shellrc`。`claude` の関数を定義する |
| 読み込み | ラッパーの rc を、在るときだけ読む 1 行。読み込み先のファイル（`$DEVBASE_SHELLRC_DIR/ndf-relay.sh`）か、シェルの設定の囲みの中に置く |
| 旧い写し | `${XDG_DATA_HOME:-~/.local/share}/ndf/relay.py`。10.17.4〜10.17.6 が置いた写しで、10.17.4〜10.17.6 の囲みの alias が指す |
| 状態の親 | `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/`。記録（`rc-added` など）と作業ディレクトリを持つ |
| 再開用のコマンド | 再起動した claude へ最初の入力として渡す中身（`ndf-next` のブロックの中身と同じ扱い） |
| 質問の印 | 作業ディレクトリの `question`。質問が表示されているあいだ在る |
| 送り込み | ラッパーが子の擬似端末へ、`/exit` 以外の入力を書くこと。実装は #931 |
| 起動の方針の引数 | 最初の区間の `claude` の引数のうち、会話ごと・区間ごとに変わらないもの（`--dangerously-skip-permissions`・`--model` など） |

区間・切れ目・印・静まり・素通し・落ちるは [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md) の用語のとおり。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| SessionStart hook から導入を外し、在る写しの置き直しと自動の囲みの知らせにすること | 送り込みの実装（#931。実測と不変条件はこの文書） |
| `/ndf:install-wrapper` と `relay.py` の `install` / `uninstall` / `status` | Codex / Kiro / agy でのラッパー・再起動（Skill も hook も配らない） |
| 写しとラッパーの rc の置き場所（Claude Code の設定の親）と、devbase の読み込み先のファイル | bash / zsh 以外のシェル（fish など）への導入 |
| `/ndf:restart` と再開用のコマンドの作り方 | 既存の `claude` の alias / 関数を上書きすること |
| 関門を越えない守り（G1〜G3）を既存の `/exit` に入れること | 既存の定義の判定を、シェルの設定から読み込まれる別ファイルへ広げること |
| 2 つ目以降の区間への起動の方針の引数の引継ぎ（#936） | 静まりの秒数・上限・空回り・素通し・`mark` の印の判定の変更 |

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| SessionStart hook はシェルの設定を書かない。導入は明示指示専用の `/ndf:install-wrapper` だけにする（`disable-model-invocation: true`） | 利用者のシェル設定を黙って書き換えない。モデルが自分の判断で書き換えることも frontmatter で止める。書く前にバックアップを取り `uninstall` で戻せるので、実行前の確認は置かない |
| Skill の名前は `install-wrapper` にする | 利用者の指示の `install_wrapper` は Agent Skills 仕様の名前の規約（小文字英数とハイフンのみ）と `check-skill-frontmatter.py` に反する。語を残して区切りだけ替えた名前が、覚えた名前から最も遠くない |
| 導入・取り外し・状態の表示を 1 つの Skill の引数で分ける | `statusline` の `status \| set \| restore` の前例に合わせる。別の Skill にすると同じ囲みと記録を 2 つの本文が説明し、片方だけが古くなる |
| 10.17.4〜10.17.6 の自動の囲みは残し、1 度だけ知らせる | hook が消すのはまた黙った書き換えになり、ラッパーを使い始めた利用者の `claude` が版の更新で変わる。リリースノートだけでは読まない利用者が知る手段が無い |
| 知らせには、`/ndf:install-wrapper` で入れ直せば別のファイルの `alias claude` が戻ることも書く（#936） | 10.17.4〜10.17.6 の囲みの alias は、devbase の `alias claude='claude --dangerously-skip-permissions'` を上書きしている。入れ直すと囲みの中が読み込みの 1 行になり、関数が先の alias と組み合わさる。hook が囲みを書き換える動きは足さない |
| 写しの置き直しは SessionStart hook で、写しが在るときだけ行う | 10.17.4〜10.17.6 の写しの `run` は新しい版の処理を持たず、`run` に追従を置いても届かない。新しい版の処理を確実に走らせられるのは新しい版のプラグインが呼ぶ hook である。無い写しは作らない。キャッシュのパスを直に指す案は #895 の決定（古い版に固定される）のとおり採らない |
| 写しとラッパーの rc を Claude Code の設定の親（`${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/`）に置く | devbase で永続化されるのは各 AI CLI の設定の親だけである（`~/.claude` は `/persistent/group/.claude` への symlink。2026-09-23 に `readlink -f` と `findmnt` で実測）。`~/.local/share`・`~/.local/state`・`~/.bashrc` はコンテナの層にあり、作り直しで消える。`/persistent/...` を直に指すと devbase の配置に依る |
| `claude` の定義はラッパーの rc に書き、シェルの設定には読む 1 行だけを置く | 定義の形が変わっても書き換えるのは ndf が持つラッパーの rc だけで済む（利用者の判断。2026-09-23） |
| 定義は alias でなく関数にし、呼んだ時点で写しが無ければ素の `claude` を起こす | alias は写しの有無をシェルの起動時にしか見ない。共有する別のコンテナの `uninstall` が写しを消すと、開いたままのシェルの `claude` が失敗する |
| 関数は `function claude { ... }` の形で書く | 先に `alias claude=...` があると `claude() { ... }` の定義の行が alias で展開されて壊れる。`function` の後の名前は展開されず、先の alias は呼び出しの時に展開されて引数が関数へ渡る（bash 5 で確かめた） |
| パスは `install` の時点で決め、`${CLAUDE_CONFIG_DIR:-...}` をファイルに残さない | 残すと、隔離した `CLAUDE_CONFIG_DIR` で `claude` を打ったときに写しを見失う |
| 旧い写しと状態の親は動かさない | 旧い写しを動かすと 10.17.4〜10.17.6 の囲みの alias が壊れる。状態の親の記録はこのコンテナの `~/.bashrc` のパスを持つので、シェルの設定と同じく作り直しで消える場所に置けば食い違わない |
| devbase では、devbase が読み込む汎用のディレクトリへ `ndf-relay.sh` を置く（devbasex/devbase#253 で依頼） | devbase の `~/.bashrc` はイメージの層にあり、ndf の側だけでは作り直しで消えない読み込みを置けない。devbase が ndf の内部のパスを知らずに済み、ほかのツールも同じ場所を使える。場所は環境変数 `DEVBASE_SHELLRC_DIR` で見つける（対話でないシェルからも見える） |
| devbase 以外（変数が無い）では `install` がシェルの設定へ囲みを足す。作り直しのたびの再導入の案内は採らない | 明示の操作なので案内だけにしない。再導入の案内は作り直すたびに打ち直しを求め、`uninstall` した利用者にも「外した」印が消えて出る |
| `uninstall` は写しとラッパーの rc も消す | 関数は呼んだ時点で写しを見るので、共有する別のコンテナの `claude` を壊さない。残すと `startup` が使われない写しを置き直し続ける |
| `uninstall` は `$SHELL` に依らず bash と zsh の両方の設定を見る | 10.17.4〜10.17.6 は起動した時点の `$SHELL` で足した。囲みの行は固有なので、両方を見ても関係の無い行を外さない |
| 写しの横に版を書き、新しい版の写しを古い版の `startup` は置き直さない | 写しの親はコンテナの間で共有される。中身の違いだけで置き直すと、古い版のコンテナが守り（G1〜G3）の無い写しへ戻す。版は `plugin.json` が既に持つので、`relay.py` に版の定数を持たせない |
| 明示の `install` は版を比べずに置く | 過去の版へ戻した利用者は、`/ndf:install-wrapper` を打ち直せば写しも戻せる |
| 明示の `install` は「消したら足し直さない」「案内は 1 度だけ」を持たない。触れたパスは `rc-added` に残して `rc-user` に足す | 明示の起動は利用者の意図そのものなので、打たれるたびに同じ判定をする。`rc-added` から除くと、10.17.6 以前へ戻した利用者が囲みを手で消したとき 10.17.4〜10.17.6 の hook が足し直す。自動か明示かは `rc-user` で分ける |
| `NDF_RELAY_AUTO` を読まない | hook の自動の導入を止める変数で、意味が無くなった。明示の `install` に効かせると、置いたままの利用者が打っても何も起きない |
| 再起動は `ndf-next` のブロックと既存のラッパーの経路で行い、ラッパーに「今すぐ再起動」の副命令を足さない | 同じ経路なら、答え待ち・背景の処理・静まり・上限・空回りと守りがそのまま効く。Skill の Bash からラッパーへ直接送ると、応答を終える前に `/exit` が入る |
| `/ndf:restart` はモデルも起動できる | ラッパーの下で起きることは conductor が切れ目で出すブロックと同じで、新しい権限を足さない |
| ラッパーの下かは `relay.py notice` の 1 行目（`is-child` と同じ判定）で決める。`NDF_RELAY_DIR` の有無だけでは決めない | conductor が Bash から起こした `claude -p` も `NDF_RELAY_DIR` を継ぐ。`mark` と判定をそろえ、ブロックを出したのに切り替わらない状態を作らない |
| ラッパーの外の `/ndf:restart` は、シェルの 1 行（`claude "..."`）ではなく貼り付ける中身を示す | 再開用のコマンドは引用符・`$(...)`・改行を含みうる。シェルへ貼ると引用が壊れて別のコマンドが動きうる |
| 再開用のコマンドに承認・同意・判断の結果を書かない | 次の区間の claude はそれを人の入力として読むため、関門を越える経路になる。承認は課題の本文と Pull Request から次の区間が読み直す |
| 関門を越えない守り（G1〜G3）は、送り込みを待たずに既存の `/exit` に入れる | 質問の表示中にラッパーが書いた `\r` が選択肢 1 を決めると実測で分かった。10.17.4〜10.17.6 の切れ目の `/exit` にも経路があり、`/ndf:restart` はそれを利用者が打てる形で増やす |
| 質問は画面の文言で読まず、`PreToolUse` の hook の印と会話の記録の行で見る | 文言は Claude Code の版で変わる（#895 と同じ理由）。`Notification` は表示から約 6 秒後で、2 秒で答えると来ない。`PreToolUse` は表示と同時に来る |
| `/exit` と改行を 1 回の write にする。`NDF_RELAY_EXIT_GAP` を廃止する | 1 秒あけると、あいだに出た質問で `/exit` は捨てられ `\r` が選択肢を決める。短い入力は 1 回でも間ありでも同じに働いた |
| 確かめ直しと write を、質問の hook と同じロック（`question.lock`）で排他にする | 質問は `PreToolUse` の hook が終わるまで描かれないので、hook に同じロックを取らせれば書き終えるまで質問の始まりを止められる。書いた後に検出する形は取り消せない |
| ロックが取れない質問の hook は、印を作らず質問を拒否する | 印を作って描かせると、ロックを持つラッパーの `/exit\r` が質問に届く。拒否ならモデルが呼び直すだけで関門を越えない |
| 質問の印は `mark`（Stop）でも消す | `Esc` で取り消すと `PostToolUse` が来ず印が残り、切り替えが永久に止まる。Stop が起きたなら質問は表示されていない |
| 2 つ目以降の区間へ、最初の区間の引数から会話ごと・区間ごとのものを除いた残りを引き継ぐ（#936） | 全部引き継ぐと `--resume` や最初のプロンプトで前の会話へ戻る。何も引き継がないと devbase の alias の `--dangerously-skip-permissions` が落ちる。対話シェルに alias を聞く案は、利用者の rc が走り手で付けた引数を拾えない |
| 知らない選択肢も値を取る規則のまま引き継ぐ | 落とすより付けるほうが起動の方針を失わない |
| 送り込みは実測と不変条件までをこの文書で決め、実装は #931 に分ける | 許可の一覧・判定・モデルからの要求の形を持ち、導入の配布を送り込みのレビューで待たせない |

## 仕様

### 常に成り立つ条件

- **`startup` はシェルの設定・ラッパーの rc・読み込み先のファイルを書かない。** 書くのは在る写し・写しの版・
  旧い写しの置き直しと、状態の親の `rc-noticed` だけで、例外も含めて終了コード 0 で終わる
- **`install` / `uninstall` は囲みの外を 1 バイトも変えず、書く前に `<ファイル>.ndf-bak-<UTC の %Y%m%dT%H%M%SZ>`
  へバックアップを取る。** ロックが取れなければ何も変えない
- **共有の写しを、古い版の `startup` が置き直さない**
- **ラッパーは、質問の印がある間・印の後に応答の行がある間は、子の端末へ何も書かない**
- **`question open` はラッパーの直接の子と判定した後に失敗したら、質問を拒否する**（印の無いまま質問を
  描かせない）。どの場合も終了コード 0 で終わる
- **動いているラッパーは入れ替えない。** 守りと引数の引継ぎが効くのは、ラッパーを起動し直した後（`/exit` で
  ラッパーを抜けて `claude` と打ち直した後）からである

### 置き場所

`<親>` は `install` の時点の `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/ndf` である。

| ファイル | パス | 誰が置くか | devbase での永続性（2026-09-23 の実測） |
| --- | --- | --- | --- |
| 写し | `<親>/relay.py`（`0755`） | 明示の `install`。在れば `startup` が置き直す | 残る（`/persistent/group` は名前付きボリュームの mount） |
| 写しの版 | `<親>/relay.version` | `install`・`startup` | 残る |
| ラッパーの rc | `<親>/shellrc`（`0644`） | `install` | 残る |
| 読み込み先のファイル | `$DEVBASE_SHELLRC_DIR/ndf-relay.sh`（`0644`） | `install`（変数が在るディレクトリを指すとき） | 残る（devbasex/devbase#253 の後） |
| 読み込みの囲み | `~/.bashrc`・`~/.bash_profile`（macOS の bash）・`${ZDOTDIR:-$HOME}/.zshrc` | `install`（変数が無いとき） | 消える |
| 旧い写し | `${XDG_DATA_HOME:-$HOME/.local/share}/ndf/relay.py` | 10.17.4〜10.17.6 の hook（新しい版は置かない） | 消える |
| 状態の親 | `${XDG_STATE_HOME:-$HOME/.local/state}/ndf/relay/` | 変えない | 消える |
| `copy.lock` | `<親>/` | `install`・`uninstall`・`startup` | 残る（共有） |

状態の親の記録（1 行 1 パス）:

| 記録 | 書く側 | 意味 |
| --- | --- | --- |
| `rc-added` | 10.17.4〜10.17.6 の `install`、今の `install` / `uninstall` | 囲みを足した・触れたパス。今の版は除かない（10.17.4〜10.17.6 の hook が足し直さないため） |
| `rc-user` | `install` / `uninstall` | 利用者が明示に導入か取り外しをしたパス。自動の囲みとして扱わない |
| `rc-noticed` | `startup`（`uninstall` が外したパスを除く） | 自動の囲みを知らせたパス |
| `rc-skipped` | 10.17.4〜10.17.6 の `install`（`uninstall` が外したパスを除く） | 既存の定義で足さなかったパス |

**devbase では `/persistent/group` が同じアカウントグループのコンテナで共有される**（devbasex/devbase#116）。
`install` と `uninstall` の効果は同じグループの全コンテナに及ぶ。

**ラッパーの rc と読み込みの中身**（`$HOME` の下なら `"$HOME/..."`、それ以外は `"<絶対パス>"` で書く）:

```sh
# <親>/shellrc
# ndf のラッパー。/ndf:install-wrapper が書き、/ndf:install-wrapper uninstall が消す
function claude {
  if [ -f "$HOME/.claude/ndf/relay.py" ]; then python3 "$HOME/.claude/ndf/relay.py" run "$@"
  else command claude "$@"; fi
}

# 読み込み先のファイルの中身、または囲みの中の 2 行
# ndf のラッパー（/ndf:install-wrapper uninstall で外れる）
[ -f "$HOME/.claude/ndf/shellrc" ] && . "$HOME/.claude/ndf/shellrc"
```

10.17.4〜10.17.6 の囲みは alias を直に持ち、旧い写しを指す。

```bash
# >>> ndf relay >>>
# ndf の中継（区間の切れ目で claude を自動で起動し直す）。消せば元に戻る。
alias claude='python3 "${XDG_DATA_HOME:-$HOME/.local/share}/ndf/relay.py" run'
# <<< ndf relay <<<
```

### `relay.py` の副命令（この文書が持つもの）

| 副命令 | 引数 | 終了コード | 出力 |
| --- | --- | --- | --- |
| `install` | 無し | 0: 読み込みを置いた・既に在った / 1: 置かなかった（引用できないパス・bash と zsh 以外で読み込み先のファイルも無い・既存の `claude` の定義・閉じの無い囲み）/ 3: ロックが取れない・書けない | 標準出力へ `ndf-relay: ` で始まる行 |
| `uninstall` | 無し | 0: 外した・外すものが無かった / 1: 閉じの無い囲みがあり何も変えなかった / 3: ロックが取れない・書けない | 同上 |
| `status` | 無し | 0 | 同上。何も書かない |
| `startup` | 無し | 常に 0 | 知らせるときだけ `{"systemMessage": "<1 行>"}` |
| `question` | `open` / `close`（標準入力の hook の JSON は読み捨てる） | 常に 0 | 拒否するときだけ `PreToolUse` の `permissionDecision: deny` |

**明示の導入は `NDF_RELAY_AUTO` を見ない。** 引用できない文字は `'`・`"`・`\`・`$`・`` ` ``・`!`・改行である。

### `install`

| # | 段 | すること | 出す行（`ndf-relay: ` の後） |
| ---: | --- | --- | --- |
| E0 | パス | 写し・ラッパーの rc・読み込み先のファイルのパスに引用できない文字があれば、何も書かず 1 | `<パス> は引用できない文字を含むため置かない` |
| E1 | 読み込み先 | `DEVBASE_SHELLRC_DIR` が在るディレクトリを指せば読み込み先のファイル。無ければ `$SHELL` の名前が `bash` なら `~/.bashrc`（macOS では `~/.bash_profile`。無ければ作る）、`zsh` なら `${ZDOTDIR:-$HOME}/.zshrc` の囲み。どちらでもなければ何も書かず 1 | `<シェル> には足さない。使うなら次の 1 行を設定へ置く: <読み込みの行>` |
| E2 | 既存の定義 | 囲みの外に行頭の `alias claude=` / `function claude` / `claude()` があれば何も書かず 1。見るのは `$SHELL` が bash なら `~/.bashrc`・`~/.bash_aliases` とログインシェルの設定（`~/.bash_profile`・`~/.bash_login`・`~/.profile`）、zsh なら `.zshrc`、どちらでもない（読み込み先のファイルを使う）ときは全部 | `<ファイル> に claude の定義があるため足さない。使うなら次の 1 行を自分で置く: <読み込みの行>` |
| E2b | 囲みの形 | `~/.bashrc`・`~/.bash_profile`・`.zshrc` のどれかに閉じの無い囲みがあれば、何も書かず 1 | `<ファイル> の囲みに閉じが無い。直してから打ち直す` |
| E2c | ログインシェル | macOS の bash で読み込み先のファイルを使わず、`~/.bash_profile` が無く `~/.bash_login` か `~/.profile` があれば、何も書かず 1。`~/.bash_profile` を作るとログインシェルが元のファイルを読まなくなるため | `<~/.bash_profile> が無く、ログインシェルは <読まれている先> を読んでいる。…使うなら <~/.bash_profile> を作り、<読まれている先> を読む行と次の 1 行を置く: <読み込みの行>` |
| — | ロック | `<親>` を `0700` で作り、`install.lock` → `copy.lock` を 2 秒ずつ待つ。取れなければ何も変えず 3 | `ほかの導入が動いている。少し待ってから打ち直す`（書けなければ `書けない（<理由>）`） |
| E3 | 写し | 中身が違うか無ければ一時ファイルに書いて置き換える。写しの版に自分の版を書く。**版は比べない** | — |
| E4 | ラッパーの rc | 中身が違うか無ければ置き換える | — |
| E5 | 読み込み | 両方のシェルの設定に残った囲みの中が今の 2 行と違えば、バックアップの後に中だけを置き換える。読み込み先のファイルを使うなら中身を置く。囲みを使うなら、無ければバックアップの後に末尾の改行を整え、既存の中身があれば空行 1 つを挟んで追記する | — |
| E6 | 報告 | 囲みを足すか置き換えたパスを `rc-added`（無ければ）と `rc-user` に足す | `<読み込み先> から <ラッパーの rc> を読むようにした（バックアップ <パス>）。次に開くシェルから効く` と `ラッパーの本体 <写し>（版 <版>）` |

**報告にシェルへ貼るコマンドを含めない。** パスを引用し直して示すと、引用の誤りで別のコマンドが動きうる。

### `uninstall`

| # | 段 | すること |
| ---: | --- | --- |
| U1 | 形を見る | `~/.bashrc`・`~/.bash_profile`・`${ZDOTDIR:-$HOME}/.zshrc` を調べ、閉じの無い囲みが 1 つでもあれば何も変えずに 1（`<ファイル> の囲みに閉じが無い。何も変えていない。直してから打ち直す`） |
| U2 | ロック | `install.lock` を取る。`<親>` が在るときだけ `copy.lock` も取る（`<親>` を作らない）。取れなければ 3 |
| U3 | 外す | 囲み（いくつあってもすべて）があるファイルは、バックアップの後に囲みの行だけを除き、元の権限で置き換える |
| U4 | 消す | 読み込み先のファイル・ラッパーの rc・写しの版・写し・旧い写しを消す（無いものは飛ばす） |
| U5 | 記録 | `rc-skipped` / `rc-noticed` から外したパスを除き、`rc-added` と `rc-user` に足す |
| U6 | 報告 | 外したファイルとバックアップ、消したファイルを 1 行ずつ。最後に `開いているシェルでは unset -f claude で外れる`（囲みが直の alias なら `unalias claude`）。設定の親（`${CLAUDE_CONFIG_DIR:-~/.claude}`）が symlink か、読み込み先のファイルを使うときは `同じ設定を共有する環境でも、ラッパーを通らなくなる（開いたままのシェルは素の claude へ落ちる）` を足す。外すものが無ければ `外すものが無い` |

### `status`

読み込み先（読み込み先のファイルとその有無か、どのシェルの設定の囲みか）、各シェルの設定の囲みの有無と
形（読み込みの行か直の alias か、自動の囲みか、閉じが無いか）、ラッパーの rc の有無、写しと旧い写しそれぞれが
今のプラグインの `relay.py` と同じか違うか無いか、写しの版を 1 行ずつ示す。`~/.bash_profile` の囲みが無い行は
macOS でだけ示す。何も書かない。

macOS の bash で読み込み先のファイルを使わず、囲みが `~/.bashrc` にしか無く、ログインシェルが読むファイル
（`~/.bash_profile`・`~/.bash_login`・`~/.profile` のうち在る最初の 1 つ）が囲みを持たず `~/.bashrc` も読まない
ときは、`警告:` で始まる行を最後に足す。終了コードは 0 のまま。

### `startup`（SessionStart）

hook の定義は `matcher: startup|resume` の 1 件（`timeout` 5・`continueOnError: true`）。`rc-added`・
旧い写し・写しのどれも無ければ `python3` を起こさない。`install` は SessionStart から外した。

```sh
sh -c 'R="${XDG_STATE_HOME:-$HOME/.local/state}/ndf/relay/rc-added"; C="${XDG_DATA_HOME:-$HOME/.local/share}/ndf/relay.py"; N="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/ndf/relay.py"; [ -f "$R" ] || [ -f "$C" ] || [ -f "$N" ] || exit 0; ROOT="${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-}}"; [ -n "$ROOT" ] || exit 0; python3 "$ROOT/scripts/relay.py" startup; exit 0'
```

| # | 条件 | すること |
| ---: | --- | --- |
| 0a | 写しが在り、中身が自分と違う | `copy.lock` の中で読み直し、写しの版が自分の版より新しければ置き直さない。自分の版が読めなければ置き直さない。写しの版が無い・読めなければ置き直す。置き直したら写しの版に自分の版を書く。**無い写しは作らない** |
| 0b | 旧い写しが在り、中身が自分と違う | 版を見ずに置き直す（コンテナごとの場所で共有されない）。無ければ作らない |
| 1 | 自動の囲みのうち `rc-noticed` に無いパスがある | そのパスを `rc-noticed` に足して知らせる。無ければ何も出さない |

知らせの 1 行: `ndf-relay: <パス>（2 つなら・で並べる） の alias claude は 10.17.4〜10.17.6 が自動で足したもの。
使い続けるなら何もしなくてよい。外すなら /ndf:install-wrapper uninstall。` に、別のファイルの
`alias claude`（devbase の `--dangerously-skip-permissions` など）を上書きしていることと
`/ndf:install-wrapper` で入れ直せばその alias が戻ることを続ける。`DEVBASE_SHELLRC_DIR` があれば
`コンテナを作り直した後も使うなら /ndf:install-wrapper` を足す。

| ロック | 置き場所 | 中で行うこと | 待ち |
| --- | --- | --- | --- |
| `install.lock` | 状態の親（コンテナごと） | 0b・1 と、`install` / `uninstall` のシェルの設定と記録 | `startup` は 1 秒（取れなければ何もせず終わる）、`install` / `uninstall` は 2 秒 |
| `copy.lock` | `<親>`（共有） | 0a と、`install` / `uninstall` の写しと写しの版 | `startup` は 1 秒（取れなければ 0a だけ飛ばす）、`install` / `uninstall` は 2 秒 |

**取る順はどこでも `install.lock` → `copy.lock` である。** `startup` の待ちは合わせて 2 秒以内で、hook の
`timeout` 5 秒に 3 秒を残す。同じ HOME の 2 つの起動が同時に来ても、知らせは 1 度だけになる。

**版の比べ方:** 版は `<プラグインのルート>/.claude-plugin/plugin.json` の `version`（ルートは `relay.py` の
2 つ上）。形は `X.Y.Z`・`X.Y.Z-dev.N`・`X.Y.Z-rc.N` で、`X`・`Y`・`Z` を数として比べ、同じ `X.Y.Z` では
`-dev.N` < `-rc.N` < 接尾辞なし、同じ接尾辞では `N` を数として比べる（`-dev.2` < `-dev.10`）。

### `/ndf:install-wrapper` と `/ndf:restart`

| Skill | frontmatter | 振る舞い |
| --- | --- | --- |
| `install-wrapper` | `argument-hint: "install \| uninstall \| status"`・`disable-model-invocation: true`・`allowed-tools: [Bash]` | 無し・`install` → `install`、`uninstall`・`status` はそのまま。それ以外は使い方を示して何もしない。`relay.py` を 1 回だけ実行し、出力と終了コードをそのまま示す |
| `restart` | `argument-hint: "[再開用のコマンド]"`・`allowed-tools: [Bash]`（モデルも起動できる） | 下の表 |

`/ndf:restart` は、ブロックを出す前に必ず `python3 <root>/scripts/relay.py notice` を 1 回実行し、1 行目でラッパーの下かを決め、
2 行目を告知として示す（契約は [ndf-relay-segment-notice.md](ndf-relay-segment-notice.md)）。1 行しか出ない（`relay.py` を呼べない）ときはラッパーの外として扱う。

| 判定 | すること |
| --- | --- |
| ラッパーの下 | `notice` の 2 行目を言い換えずに示し、応答の最後に `ndf-next` のブロックを **ちょうど 1 つ** 置いて応答を終える。背景の処理を起こさない。`/goal` の判定が応答を続けさせたら、続いた応答の最後に同じブロックを出し直す |
| ラッパーの外 | ブロックを出さず、`notice` の 2 行目（無ければ「`/exit` してから `claude` を起動し、下の中身を最初の入力として貼り付ける（`/ndf:install-wrapper` でラッパーを入れると自動になる）」）の 1 行と、中身を情報文字列 `text` の囲みで示す |

**再開用のコマンド**は引数があればそのまま（利用者の入力として扱い書き換えない）。無ければ、`/goal` の
目標の入力をそのまま（引継ぎ文書の名指しがあれば「<文書> の続きから」を足す）、次に課題番号・作業ツリーの
パス・Pull Request の URL だけを差し込む定型の 1 文、どちらも無ければ引数を求める 1 行を示してブロックを
出さずに終える。ラッパーの側に再起動のための変更は無い。

### 関門を越えない守り

**質問の印 `question`**（作業ディレクトリのファイル。`next.json` の形は変えない）:

| 書く側 | 条件 | すること |
| --- | --- | --- |
| `question open`（`PreToolUse`・`AskUserQuestion`） | `NDF_RELAY_DIR` があり、ラッパーが動いていて、hook の親をたどって最初に当たる claude が `child.pid` と一致する | `question.lock` を起動から `NDF_RELAY_QUESTION_WAIT` 秒（既定 3）まで待って取り、`0600` の空のファイルを作ってから放す。取れない・作れない・例外なら質問を拒否する |
| `question close`（`PostToolUse`・`AskUserQuestion`） | 同じ | 消す。失敗しても何も出さない（印が残るのは安全な側） |
| `mark`（Stop） | 直接の子の判定を通った | 印の判定の前に消す |
| `run` | 区間を起動する | 印（`next.json`）と一緒に消す |

拒否は標準出力の `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
"permissionDecisionReason": "ndf-relay: ラッパーが入力を書いている最中だったため、質問を出さなかった。利用者へ返さずに、同じ AskUserQuestion を今すぐもう一度呼ぶ"}}`
である。ラッパーの下でない・直接の子でないときは何も出さない。hook の定義は `PreToolUse` と `PostToolUse` に
`matcher: AskUserQuestion` の 1 件ずつで、`timeout` 10・`continueOnError: true`:

```sh
sh -c '[ -n "${NDF_RELAY_DIR:-}" ] || exit 0; ROOT="${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-}}"; [ -n "$ROOT" ] || exit 0; exec python3 "$ROOT/scripts/relay.py" question open'
```

**hook の `timeout` で打ち切られると、拒否も印も残らずに質問が描かれる。** そのため `question open` は
自分で期限を持ち（待ち 3 秒と印の作成 1 秒以内）、`timeout` 10 秒との差を 6 秒持つ。

**静まりを待つ条件に 2 つを足す**（本体の (1)〜(3) の後）。

| # | 条件 | 外れたとき |
| ---: | --- | --- |
| (4) | 作業ディレクトリに `question` が無い（G1） | 待ち続ける |
| (5) | 印の `transcript_path` に、`timestamp` が印の `written_at` より後で `type` が `assistant` か `user` の行が無い（G2）。`attachment`（目標の判定）と `system` の行は数えない | 待ち続ける。次の Stop が印を書き直すか消す |

**終わらせる段（G3）。** (1)〜(5) がそろい、停止の印・`count.lock`・1 日の上限・空回りを通った後:

| 順 | ラッパーがすること |
| ---: | --- |
| 1 | そろった時点の印の `written_at` と会話の記録の大きさ・更新時刻を控える（記録を全行読むのはこの前だけ） |
| 2 | `question.lock` を待たずに試す。取れなければ `count.lock` を放して静まりを待つへ戻る |
| 3 | ロックの中で、`question` が無いこと・印の `written_at`・記録の大きさと更新時刻が 1 の控えと同じことだけを確かめる。外れたら両方のロックを放して戻る |
| 4 | `/exit\r` を **1 回の write** で書く |
| 5 | `NDF_RELAY_EXIT_HOLD` 秒（既定 1）入出力を流してから放す |

**書いた後に `question` が現れたら、`count.lock` を放し、`question` が消えるまで `NDF_RELAY_EXIT_WAIT` の
秒を数えない。** 書いた `/exit` が応答の待ち行列に入り、質問の答えの後に働く場合に、質問を SIGTERM で
消さない。この経路で子が終わったら印を読み直す。

| 読み直した印 | すること |
| --- | --- |
| 無い | 次の区間を起動しない。`end`（`no-mark`）を書き、子の終了コードで終わる |
| ある | `count.lock` を 5 秒まで待って取り直し、1 日の上限と空回りを判定し直してから、読み直した印の `command` と `cwd` で起動する。取れなければ `count-lock`、上限・空回りならその理由で `stop` の行を書き、`次の区間を起動できない（<理由>）。次のコマンド:` と中身を出して終了コード 2 |

### 起動の方針の引数の引継ぎ（#936）

2 つ目以降の区間は `[*引き継ぐ引数, ブロックの中身]` で起動し、`start` の行の `carried` に引き継いだ
引数を残す（1 つ目の区間の行には無い）。引数は `run` が受けた最初の区間の引数から 1 度だけ選ぶ。

**区切りは `claude` の CLI（commander）と同じ規則で読む**（選択肢の集合は Claude Code 2.1.281 の
`claude --help` から写す）。

| 語 | 読み方 |
| --- | --- |
| `--` | 以後はすべて位置引数として落とす |
| `-` で始まらない語 | 位置引数（最初のプロンプト）として落とす |
| `--opt=value`・`-nfoo` | 1 語で完結する |
| 値を取らない選択肢（`BOOL_FLAGS`） | 1 語 |
| 必須の値を取る選択肢（`REQUIRED_FLAGS`。`--model`・`--settings`・`--system-prompt` など） | 次の語を、`-` で始まっても値として取る |
| 可変長の選択肢（`VARIADIC_FLAGS`。`--add-dir`・`--mcp-config`・`--allowedTools` など） | 後続の `-` で始まらない語をすべて取る |
| それ以外（知らない選択肢を含む） | 次の語が `-` で始まらなければ値として 1 つ取る |

**引き継がない選択肢（値ごと）:** `-c`/`--continue`・`-r`/`--resume`・`--session-id`・`--fork-session`・
`--from-pr`・`--teleport`・`--cloud`・`-n`/`--name`・`--bg`/`--background`・`--tmux`・`-w`/`--worktree`。
前の会話を指す・会話に名前を付ける・起動の形を変えるもので、付けると前の会話へ戻るか、同じ ID を 2 度
使うか、区間の起動が端末に出ないか、前の区間の作業ツリーの中にもう 1 つ作る。**残りはすべて引き継ぐ。**

devbase の `alias claude='claude --dangerously-skip-permissions'` の後にラッパーの rc を読むと、alias の展開で
`relay.py run --dangerously-skip-permissions` と渡り、すべての区間に付く。

### 送り込みの実測と不変条件

実測は 2026-09-23、Claude Code 2.1.281（`--model haiku`）を Linux の擬似端末の子として、`HOME`・
`CLAUDE_CONFIG_DIR`・`XDG_*` を一時ディレクトリへ隔離して 31 回行った。

| 子の状態 | 擬似端末のマスタへ書いた入力の受け付け |
| --- | --- |
| 入力待ち | `/clear`・`/compact` は働く。`/cost`・`/status`・`/plugin` は対話が開き `Esc` で閉じる。`/model sonnet` は確認の対話が出て、`\r` で**利用者の設定へ既定として保存される**。スラッシュコマンドでは `UserPromptSubmit` も Stop も発火しない |
| 応答の生成中 | 待ち行列に入り、応答の Stop の直後に働く |
| 質問の表示中 | `\r` は選択肢 1 を決める（`/clear\r`・`承認します\r` でも答えは選択肢 1）。数字は `\r` なしで即決する。`PreToolUse` は表示と同時、`PostToolUse` は答えたときだけ（`Esc` の取り消しでは来ない）、Stop は表示中に来ない |
| `PreToolUse` の hook の実行中 | 質問は hook の終了の 0.05〜0.11 秒後に初めて描かれる。書いた入力は答えにならない（`/exit\r` は約 0.4 秒で claude を終わらせ、質問は失われる） |
| 長い入力 | 125 バイト + `\r` の 1 回の write は送られ、635 バイトは送られず後の入力と混ざった。ブラケットペーストで括れば送られる |

| # | 不変条件 | 実現 |
| --- | --- | --- |
| G1 | 質問の表示中は、子の端末へ何も書かない | 質問の印（上の「関門を越えない守り」） |
| G2 | 印の後に応答が再開していたら、その印では書かない | 静まりの条件 (5) |
| G3 | G1・G2 の確かめ直しと write を、質問の始まりと排他にする | `question.lock`・1 回の write・1 秒の保持 |
| G4 | 人の入力を装える自由な文を送らない（自由な文は人の入力と区別されない） | 送り込みは許可の一覧のスラッシュコマンドだけ。改行・制御文字を含む中身は捨てる（#931） |
| G5 | 対話の UI を開くコマンドを送らない（閉じる `Esc` は質問も取り消す） | `/cost`・`/status`・`/plugin`・`/model` を許可の一覧に入れない（#931） |
| G6 | 送ったコマンドの終わりを Stop で待たない | `SessionStart`（`clear` / `compact`）の hook か会話の記録で見る（#931） |
| G7 | 長い入力を 1 回の write で送らない | 送り込みの中身は 100 バイトまで（#931） |

G1〜G3 はこの課題で既存の `/exit` に入れた。G4〜G7 は送り込みの実装（#931）が守る。

## 運用

- **移行:** 10.17.4〜10.17.6 の囲みは旧い写しを指したまま動き、hook は囲みの中を書き換えない（向け直すのは明示の
  `install` だけ）。囲みの開きと閉じの行・状態の親のファイル名は 10.17.4〜10.17.6 と同じなので、それらの版へ戻しても
  新しい版の囲みは「既にある」と読まれる
- **過去の版へ戻したら `/ndf:install-wrapper` を打ち直す。** `startup` は新しい版の写しを古い版で置き直さない。
  **`/ndf:install-wrapper` を持たない 10.17.6 以前へ戻すときは、戻す前に `uninstall` を打つ**（戻した後なら
  囲みと `~/.claude/ndf/` を手で消す）。読み込み先のファイルだけで入れた利用者が 10.17.6 以前へ戻すと、10.17.4〜10.17.6 の
  hook が `~/.bashrc` に自動の囲みを足しうる
- **devbasex/devbase#253 の前の devbase** では `install` は囲みを使い、コンテナを作り直すと囲みが消えて写しと
  ラッパーの rc だけが残る（知らせは出さない）。打ち直すか、#253 の後に打てば読み込み先のファイルへ移る
- **`NDF_RELAY_AUTO` と `NDF_RELAY_EXIT_GAP` は読まない。** 置いたままでも害は無い
- **費用:** `startup` は `rc-added` も写しも旧い写しも無ければ `python3` を起こさない。置き直しは起動ごとに
  最大 2 組のファイルの読み比べだけである

**確かめていないこと:**

| 項目 | 内容 |
| --- | --- |
| コンテナの間の `flock` | 名前付きボリューム上の `copy.lock` が 2 つのコンテナの間で効くかは確かめていない。効かなければ後退を防ぐのは版の比較と `rename` の原子性だけになる |
| zsh・macOS | `function claude` と先の alias の組み合わせは bash でだけ、`uninstall` の置き換えと権限の保持は Linux でだけ確かめた |
| devbase の読み込み先の名前 | `DEVBASE_SHELLRC_DIR` は devbasex/devbase#253 で出した案である。devbase が別の名前を選べば `relay.py` を合わせる |
| `/ndf:restart` の判定の手順 | haiku が判定の Bash を飛ばしてブロックだけを出した（2026-09-24）。ラッパーの外では `mark` が抜けるので害は無い。Skill の本文で「必ず実行する」と強めた |
| 質問の拒否の後 | 質問は描かれず理由がモデルへ返るが、haiku は呼び直さずに利用者へ返した（2026-09-24・2.1.281）。関門は越えないが質問は失われうる |
| 再開用のコマンドの中身 | 定型に限るのはモデルで、機械の検査は無い |
| 書いた入力を TUI が読む時間 | 1 秒の保持で読み終えるかは 1 度見ただけである |

## テスト観点

単体テストは `plugins/ndf/scripts/tests/test_relay.py`（一時の HOME・`XDG_*`・`CLAUDE_CONFIG_DIR`・`SHELL`・
`ZDOTDIR` の下でだけ動かし、本物の `~/.bashrc` を変えない）。`.md` の文言は固定しない（#885）。

| 観点 | 確かめ方 |
| --- | --- |
| SessionStart に `install` が無く、`startup\|resume` の 1 件であること。`AskUserQuestion` の 2 件の `timeout` が 10 であること。Codex / Kiro / agy の hook の定義に差分が無いこと | `test_hook_definition_no_install`、`git diff` |
| 空の HOME で `startup` の hook が何も書かず何も出さないこと | `test_startup_hook_writes_nothing_on_clean_home` |
| `install` が bash / zsh（`ZDOTDIR`）で写し・写しの版・ラッパーの rc・囲み・バックアップを作り、2 回目は足さず、空の HOME・末尾の改行が無い設定・`rc-added` の記録がある状態からも足すこと | `test_install_bash_first_then_idempotent` ほか `test_install_*` |
| 10.17.4〜10.17.6 の囲みの中だけが置き換わり、囲みの外がバイトで同じこと | `test_install_rewrites_old_block_inner_only`・`test_install_devbase_rewrites_old_block` |
| 既存の定義（`~/.bash_aliases` とログインシェルの設定を含む）・fish・閉じの無い囲み・引用できないパスで何も書かず 1、ロックの保持・書けない親で何も変えず 3 | `test_install_existing_definition_skips` ほか |
| 写しを消すと関数が素の `claude` を起こし、先の alias の引数がラッパーへ渡ること | `test_install_function_falls_back_when_copy_removed`・`test_install_function_receives_alias_args` |
| `DEVBASE_SHELLRC_DIR` で `ndf-relay.sh` ができ、シェルの設定が変わらないこと | `test_install_devbase_loader`・`test_install_devbase_loader_with_non_bash_zsh_shell` |
| `uninstall` が両方の囲みを外して囲みの外を変えず、ファイルと記録を表のとおりにし、閉じの無い囲みで何も変えず、10.17.4〜10.17.6 の状態（`<親>` が無い）から `<親>` を作らずに外すこと | `test_uninstall_*` |
| `status` が各状態を示し何も書かないこと | `test_status_reports_and_writes_nothing` |
| macOS の bash（`sys.platform` を `darwin` に差し替え）で `~/.bash_profile` へ足して `uninstall` で両方の囲みを外し、`~/.profile` を隠す `~/.bash_profile` を作らず、`~/.bashrc` にしか無い囲みを `status` が警告し、Linux の bash は `~/.bashrc` のままであること | `test_mac_*`・`test_bash_definition_in_login_file_is_not_overridden`・`test_linux_bash_still_adds_to_bashrc` |
| 自動の囲みの知らせが 1 度だけ、同時の 2 起動でも 1 つで、devbase の案内が付き、囲みが無い・明示に触れたパスでは出ないこと | `test_startup_notices_auto_block_once`・`test_startup_notice_*`・`test_startup_no_notice` |
| 在る写しと旧い写しだけを置き直し、版を後退させず、新旧の同時の `startup` で新しい版が残り、明示の `install` は戻せ、`uninstall` の後は写しを作らないこと | `test_startup_refreshes_existing_copies_only`・`test_startup_never_downgrades`・`test_startup_concurrent_new_wins`・`test_explicit_install_can_downgrade`・`test_startup_after_uninstall_creates_nothing` |
| 版の順序（`-dev.2` < `-dev.10`、`-dev` < `-rc` < 正式版） | `test_version_key` |
| 質問の印がある間・印の後に `assistant` の行がある間は `/exit` が届かず、`attachment` の行では止まらないこと。`mark` が印を消すこと | `test_guard_question_blocks_exit`・`test_guard_reply_after_mark_blocks`・`test_guard_attachment_row_does_not_block`・`test_guard_mark_clears_question` |
| 届いたバイトが `/exit\r` の 1 回で、ロックの保持中に `question open` が待ち、放された後に印を作ること | `test_guard_single_write`・`test_guard_lock_held_then_question_appears`・`test_guard_question_open_waits_for_relay_lock` |
| `/exit` の後の質問のあいだ SIGTERM までの秒を数えず、印が無ければ起動せず、書き直された印なら新しい中身で起動すること | `test_guard_exit_queued_behind_question`・`test_guard_exit_queued_then_no_mark`・`test_guard_exit_wait_resumes_after_question` |
| `question` がラッパーの外で何もせず、ロックの保持・書けないときに拒否し、`close` が消すこと | `test_question_*` |
| 引数の選び分けと、alias → 関数 → ラッパーを通した 2 つ目の区間の引数 | `test_carried_args`・`test_run_carries_policy_args_through_shell_function` |
| Skill の配布と frontmatter（`install-wrapper` に `disable-model-invocation: true`、`restart` に無い。`claude-skills.txt` にだけ載る） | `python3 scripts/check-skill-frontmatter.py` と manifests |
| 本物の Claude Code での `/ndf:restart` の通し | 隔離した HOME・`CLAUDE_CONFIG_DIR` で 1 度通し、記録を実装の Pull Request に残した |

## 関連リンク

- [#928](https://github.com/devbasex/ai-plugins/issues/928)（設計は [PR #932](https://github.com/devbasex/ai-plugins/pull/932)、実装は [PR #944](https://github.com/devbasex/ai-plugins/pull/944)。#936 も同じ PR）
- [#936](https://github.com/devbasex/ai-plugins/issues/936) — 2 つ目以降の区間へ起動の方針の引数を引き継ぐ
- [#931](https://github.com/devbasex/ai-plugins/issues/931) — 送り込みの実装（G4〜G7 を入力にする）
- [devbasex/devbase#253](https://github.com/devbasex/devbase/issues/253) — 永続化されたシェルの設定の読み込み
- [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md) — ラッパーの本体（#895）
