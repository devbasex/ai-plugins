# Skill 執筆規約

`plugins/ndf/skills/` は 3 ランタイム（Claude Code / Codex / Kiro）へ配布する Skill の
編集元である。ここでは frontmatter の書き方を規約として定める。本文の書き方は各 `SKILL.md`
に委ね、規約は発動と配布に関わる部分だけを扱う。

本規約のうち機械的に判定できる項目は `scripts/check-skill-frontmatter.py` が検査し、
継続的インテグレーションで実行する（`scripts/build-runtime-plugins.sh --check` /
`scripts/validate-runtime-plugins.sh` / `scripts/check-markdown-links.py` と同じワークフロー）。

```bash
python3 scripts/check-skill-frontmatter.py           # 検査
python3 scripts/check-skill-frontmatter.py --report  # 実測値の一覧
```

判定が本質的に近似になる項目（`description` 先頭のトリガ語、`when_to_use` の追加トリガ、
既知の外部 Skill 名との衝突）は警告にとどまり、`--strict` を付けたときだけ失敗する。

利用実績と維持・統合・削除の判定は
[docs/specifications/ndf-skill-inventory.md](../../../docs/specifications/ndf-skill-inventory.md)
に記録する。

## `description` を単一の真実とする

3 ランタイムに共通する土台は Agent Skills 仕様の 6 項目（`name` / `description` / `license` /
`compatibility` / `metadata` / `allowed-tools`）である。ただし共通に効く度合いは項目ごとに異なる。

| 項目 | 3 ランタイムでの扱い |
| --- | --- |
| `name` / `description` | いずれも解釈する。発動判定に効くのは `description` |
| `license` / `compatibility` / `metadata` | 解釈されるが発動には関与しない |
| `allowed-tools` | 仕様上 experimental。**利用制限ではなく事前承認**（下記「`allowed-tools` の意味と付け方」）。Claude Code は解釈するが、Kiro は frontmatter 一覧に載せておらず解釈は保証されない。事前承認の扱いは実装差がある前提で書く |

`when_to_use` は Claude Code 独自の項目で、Codex と Kiro は文書化していない。仕様は未知の項目を
無視すると定めているため壊れはしないが、**両ランタイムでは `description` だけで発動が判定される**。

したがって **発動判定に必要な情報はすべて `description` に入れる**。Claude Code 独自項目は
その上乗せとして扱う。

```yaml
---
name: pr-review
description: "Review a PR or local branch diff and post an approve/changes verdict. Use when asked to review a PR, check a diff before merge, or self-review a branch (PRレビュー / PR確認 / マージ前チェック)."
when_to_use: "Claude Code 向けの追加トリガのみ。description で足りるなら付けない"
---
```

規則:

- `description` は「何をするか」と「いつ使うか」の両方を書く
- **主要な用途とトリガ語を最初の 1 文に置く。** Claude Code は一覧の 1 項目あたり 250 文字を
  超えた分を切り詰め、Codex は初期一覧が予算を超えると `description` を先頭から残して短縮する。
  どちらも後半へ置いたトリガ語は暗黙起動の判定に届かない
- `description` は二重引用符で囲む。`:` に空白が続く文字列を未引用で書くと YAML はマッピングと
  解釈して構文エラーになり、Kiro はその Skill を検出対象から落とす
  （[kirodotdev/Kiro#8329](https://github.com/kirodotdev/Kiro/issues/8329)）
- frontmatter に `<` と `>` を含めない。Agent Skills 仕様がシステムプロンプトへの注入リスクと
  して警告している
- `when_to_use` は Claude Code 向けの**追加**トリガが要る Skill にだけ付ける。`description` の
  言い換えにしない。未設定であること自体は不備ではない

### トリガ語の書式

トリガ語は **`Use when` の文末の全角丸括弧に `・` 区切りで並べる**。

```yaml
description: "Delete merged branches and worktrees after listing them for approval, then update main. Use when a PR was merged（マージ後の後片付け・ブランチを整理・worktreeを削除）."
```

**旧書式（`Triggers: 'a', 'b'` / `明示トリガ:`）は廃止した。** 残っていると
`scripts/check-skill-frontmatter.py` が失敗する。ラベルと引用符の分だけ長いうえ、実測では
`description` 末尾の列挙は暗黙起動へ届きにくかった（1 Skill あたり 50〜100 文字の差、
`merged` 241 → 144 文字）。

書式を変えても暗黙起動は落ちない。実測は
[docs/specifications/ndf-skill-inventory.md](../../../docs/specifications/ndf-skill-inventory.md)
「トリガ書式の変更の実測」に記録している。

規則:

- トリガ語は 2〜4 個。`Use when` の条件と重複する語は入れない
- 括弧内は**日本語のみ**にする。英語のトリガ語は `Use when` の条件文へ埋め込む
  （検査は「末尾の全角括弧かつ日本語を含む」ものだけをトリガ宣言と見なす。英語の補足
  `(Codex/Gemini)` をトリガと誤認しないための条件）
- 括弧は全角 `（）`。半角丸括弧は本文の補足に使うため、宣言と区別できなくなる

## `allowed-tools` の意味と付け方

**`allowed-tools` は利用制限ではなく、事前承認（確認プロンプトのスキップ）である。**

> Tools Claude can use without asking permission when this skill is active.
> — [Claude Code 公式リファレンス](../../../docs/claude-code-skills-official-reference.md)

したがって、外すと「その Skill が使えるツールが減る」のではなく、**Skill の手順の途中で
利用者に確認を求める回数が増える**。ファイル編集を伴う Skill から `Write` / `Edit` を外すと、
手順のたびに承認が要る。

規則:

- **その Skill の手順が実際に使うツールを列挙する。** 使わないツールを足さない（事前承認の
  範囲が広がるだけで、利用者が意図しない操作まで無確認になる）
- `Bash` はコマンドが限られるならコマンド単位で絞る（`Bash(python *)` / `Bash(gh *)`）
- MCP ツールは **1 つずつ列挙する**。`mcp__playwright` のようなサーバ名の指定が個別ツール
  （`mcp__playwright__browser_navigate` 等）へ前方一致するかは仕様上自明でなく、確認できて
  いない。前方一致しなければ MCP ツールが 1 つも事前承認されない
- **取り消しの難しい操作を無確認にしない。** 事前承認は確認プロンプトを飛ばす仕組みなので、
  破壊的操作を持つ Skill は「対象を提示して同意を取る」手順を本文へ必ず置く（frontmatter で
  守れるのは承認プロンプトの有無だけで、Kiro と Codex には対応する項目がない）

量が問題になるのは MCP ツールを多用する Skill だけである（`playwright-authoring` は 43 個の
列挙で 1,916 文字）。この種の Skill は本体プラグインからの分離を検討する。

## 発動制御の 4 分類

| 分類 | Claude Code | Codex | Kiro | 対象 |
| --- | --- | --- | --- | --- |
| 自動発動（既定） | 追加トリガがあれば `when_to_use` を併記 | 既定で暗黙起動可 | 自動ロード | 知識・判断基準・ワークフロー |
| パス限定自動発動 | 上記 + `paths` | `paths` 無効 | `paths` 無効 | 特定ディレクトリでのみ意味を持つもの |
| 明示指示専用 | `disable-model-invocation: true`（引数を取るなら + `argument-hint`） | `agents/openai.yaml` の `policy.allow_implicit_invocation: false`。加えて `description` に明示指示専用である旨を記載する | 制御手段なし。`description` に「利用者が明示的に指示したときのみ実行する」と記載 | 取り消しが難しく、かつ明示起動が定着している操作 |
| 常時注入のみ | `user-invocable: false` | 相当機能なし | 相当機能なし | `ndf-policies` |

- Codex の相当機能は `<Skill 名>/agents/openai.yaml` の `policy.allow_implicit_invocation: false`
  である。`scripts/build-runtime-plugins.sh` が `disable-model-invocation: true` の Skill に対して
  このファイルを自動生成するため、共通編集元では frontmatter だけを書けばよい
  （[棚卸の計画](../../../issues/old/ndf-development-skills/07-tasks.md) の Task 0-8）
- 「常時注入のみ」に相当する機能は Codex と Kiro にない。両ランタイムは `user-invocable: false`
  を解釈せず、この分類の Skill も通常の Skill として扱う。唯一の対象である `ndf-policies` は
  3 ランタイムすべてへ配布している（`plugins/ndf/manifests/`）ため、Codex では暗黙起動
  されうる。したがってこの分類の Skill は `description` に**「知識として参照する。手順として
  実行しない」旨を明記する**。Kiro は Skill として配らず `.kiro/steering/` へ常時指示として
  置き換えることで回避する（[棚卸の計画](../../../issues/old/ndf-development-skills/07-tasks.md)
  の Task 0-9）。`description` の書き換えは同計画の Task 0-10 で行う
- **Claude Code では** `disable-model-invocation: true` の Skill は `description` がコンテキスト
  へ載らず、`user-invocable: false` は載る。Codex と Kiro にはこのキーがなく `description` は
  常に読まれるため、明示指示専用にする Skill は `description` 自体へ「利用者が明示的に指示した
  ときのみ実行する」と書き残す
- `disable-model-invocation: true` と `user-invocable: false` を同時に指定しない。誰も起動
  できなくなる
- 「引数を取るなら + `argument-hint`」の引数の有無は `scripts/check-skill-frontmatter.py` が
  `SKILL.md` から機械的に判定する。frontmatter の `arguments`、本文の `$ARGUMENTS`、本文の
  「引数」への言及（節見出しでも散文でもよい）のいずれかがあれば引数を取るとみなす。
  引数を取るのに本文がそれに一切触れていないと判定から漏れるため、引数の説明は本文に書く

### 取り消しの難しい操作をどちらで守るか

破壊的操作・外部への書き込みを含む Skill の守り方は 2 つある。**取り消しの難しさだけでは
決まらない。** 明示指示専用は「その Skill が使われなくなる」副作用を持つため、
**利用者がどう依頼しているかの実測**で選ぶ。

| 守り方 | 選ぶ条件 | 実装 |
| --- | --- | --- |
| 明示指示専用 | 取り消しが難しく、**かつ**利用者が `/ndf:<name>` で明示起動する運用が定着している（自然文での依頼がほぼない） | `disable-model-invocation: true` + `description` に明示指示専用と明記（Codex の `openai.yaml` はビルドで自動生成） |
| 自動発動 + 実行前確認 | 取り消しは難しいが、**日常的に自然文で依頼される**。明示指示専用にすると Skill が使われず、エージェントが独自手順で同じ操作を実行してしまう | 暗黙起動を許し、取り消しの難しい手順の**直前に対象の一覧提示と利用者の同意**を必須手順として `SKILL.md` 本文へ固定する。`description` にも確認を取る旨を書く |

判断材料は
[棚卸台帳](../../../docs/specifications/ndf-skill-inventory.md)の実測起動数と、
そのうち明示起動が占める割合である。明示起動がほぼ全数なら前者、自然文の依頼が多い、あるいは
Skill を使わず独自手順で実行された形跡があるなら後者を選ぶ。

- 後者では安全性の担保を frontmatter ではなく **本文の手順と `description`** に置く。Codex と
  Kiro は `disable-model-invocation` を解釈せず、Claude Code でもそれは発動制御であって
  実行前確認ではないため、そもそも frontmatter だけでは守れない
- 実行前確認では、**何を消すか / 何を外部へ書き込むか**を一覧で提示する。対象を示さない
  「実行してよいですか」は同意になっていない

現時点の適用:

| Skill | 守り方 | 取り消しの難しい操作 | 判断根拠（棚卸台帳の実測） |
| --- | --- | --- | --- |
| `deploy` | 明示指示専用 | 本番デプロイ | 明示起動の運用が定着 |
| `cherry-pick-pr` | 明示指示専用 | 環境ブランチへの push | 同上 |
| `statusline` | 明示指示専用 | ユーザー設定ファイルの書き換え | 同上 |
| `merged` | 自動発動 + 実行前確認 | worktree / ローカル・リモートブランチ削除 | 起動 248（明示 248 / 自動 0）。統合元の `clean` は起動 0 / 機会 251 |
| `pr` | 自動発動 + 実行前確認 | commit / push / PR 作成 | 起動 173（明示 171 / 自動 2）。自然文の依頼では Skill を通らず独自手順で実行されていた |
| `official-skills-autoloader` | 自動発動 + 実行前確認 | 外部リポジトリの clone と `~/.claude/skills/` への symlink 作成 | 起動 0 / 機会 97。台帳の判定は「発動改善」で、明示指示専用は判定と逆行する |

## 命名の規則

Skill 名は、自動発動（トリガ語）とは別に **明示起動の入力コスト**を決める。利用者が
`/` メニューで名前の一部を打つと、その語を含む候補がすべて並ぶためである。

### 外部 Skill 名の末尾要素にしない

**Skill 名を、ランタイム組み込みや主要プラグインの Skill 名の末尾要素にしない。**
末尾要素になっていると、その語を打ったとき外部側の候補に埋もれて選べない。

実例（2026-08-12 実測、[#83](https://github.com/devbasex/ai-plugins/issues/83)）:

| NDF の Skill 名 | 同じ語を末尾に持つ外部 Skill（`/` メニューでの表示名 → Skill 名） | 結果 |
| --- | --- | --- |
| 旧 `review` | `code-review`（組み込み）→ `code-review`、`security-review`（組み込み）→ `security-review`、`coderabbit:code-review` → `code-review`、`coderabbit:coderabbit-review` → `coderabbit-review`、`superpowers:requesting-code-review` → `requesting-code-review`、`superpowers:receiving-code-review` → `receiving-code-review` | `/review` では候補に埋もれ、`/ndf:` から辿るしかなかった |
| `fix` | なし | `/fix` で一意に決まる |

表の左側が `/` メニューでの表示名、右側が名前空間（`coderabbit:` などのプラグイン接頭辞）を
除いた Skill 名である。

`review` は v6.0.0 で **`pr-review`** へ改名して解消した。`/pr-rev` まで打てば一意に決まり、
`pr` / `pr-tests` / `pr-review` と接頭辞も揃う。

逆に、外部名を**末尾に含む**のは問題ない。`cross-review` は `code-review` の末尾要素では
ないため、`/cross` の時点で一意に決まる。

`scripts/check-skill-frontmatter.py` が既知の外部名との衝突を警告する。検査が突き合わせる
一覧（`KNOWN_EXTERNAL_SKILL_NAMES`）は**名前空間を除いた Skill 名**で持つ。組み込み Skill は
そもそも名前空間を持たず、また `/` メニューで埋もれるかどうかを決めるのは名前空間ではなく
Skill 名の部分だからである。ただし配布先に何が入っているかは検査時点で分からないため、
**一覧は手で更新する best-effort** であり網羅ではない。新しい Skill を足すときは、実際に
`/` メニューで名前を打って確認する。

### 名前と親ディレクトリ名を一致させる

`name` と親ディレクトリ名を揃える（[上限値](#上限値)の表を参照）。改名するときは
ディレクトリ・`name`・3 つの manifest・各 `plugin.json` を同時に直す。

## トリガ語の規則

### 一意であること

トリガ語は Skill 間で重複させない。重複すると、同じ依頼に対して複数の Skill が起動を競い、
どちらが選ばれるかが依頼文の細部に左右される。

重複を見つけたら、次のどちらかで解消する。

1. 機能が重複しているなら Skill 自体を統合する
2. 機能が異なるなら、区別できるところまでトリガ語を具体化する

### 検査できるのは NDF 内の重複だけ

`scripts/check-skill-frontmatter.py` の重複検査は NDF の Skill 同士しか見ない。
**ランタイム組み込みの Skill や他プラグインとの競合は検査できない。** 配布先の環境に
何が入っているかに依存するためである。

実例: 旧 `review`（現 `pr-review`）は `disable-model-invocation` を外して自動発動できる
ようにしたが、Claude Code 組み込みの `code-review` が同じ用途を持つため、「レビューして」の
ような依頼では常に組み込み側が選ばれる（実測は
[08-verification.md](../../../issues/old/ndf-development-skills/08-verification.md)
「自然文からの発動の実測」）。同名・同用途の組み込み Skill があるランタイムでは、
`description` を差別化しても勝てないことがある。明示起動で使う前提に切り替えるか、
用途が重ならない名前へ寄せる。この Skill は後者を採り、v6.0.0 で `pr-review` へ改名した。

### 広すぎるトリガを置かない

ほぼ全セッションに一致するトリガ語は、その Skill を発動させる代わりに他 Skill の発動を
埋もれさせる。実測では次の例が問題になった。

| Skill | 問題のあるトリガ | 機会 | 具体化した例 |
| --- | --- | ---: | --- |
| `python-execution` | `python`、`スクリプト` | 1,207 | `uv run`、`venv が見つからない` |
| `git-gh-operations` | `git add`、`git commit` | 1,840 | `fatal:`、`non-fast-forward` |
| `investigation-rules` | `調査` | 1,271 | `調査レポートを書く` |

目安として、**その語が出たときに必ずその Skill を使ってほしいか**を基準にする。「使うことも
ある」程度の語はトリガにしない。

## 上限値

| 項目 | 上限 | 根拠 |
| --- | --- | --- |
| `name` | 64 文字。小文字英数とハイフンのみ。先頭末尾ハイフン不可、連続ハイフン不可 | Agent Skills 仕様（必須） |
| `name` と親ディレクトリ名 | 一致させる | プロジェクト規約。仕様上は任意（Claude Code は `name` 省略時にディレクトリ名を使う） |
| `description` | 1,024 文字。運用目標は 300 文字以内 | 仕様上限 / 運用目標 |
| `description` + `when_to_use` | 1,536 文字 | Claude Code。超えると一覧で切り詰められる |
| `compatibility` | 500 文字 | Agent Skills 仕様 |
| `SKILL.md` 行数 | 500 行。超えるものは補助ファイルへ分割 | 仕様の推奨、コンパクション対策 |
| `SKILL.md` 本文 | 5,000 トークン | 仕様の推奨 |
| Claude Code の初期 Skill 一覧の合計 | **25,000 文字**（10,000 トークン = コンテキストの 1%）。1 項目は `description` + `when_to_use` を合わせて 1,536 文字で切り詰め | Claude Code 公式ドキュメント。既定モデル Opus 5 のコンテキスト 1,000,000 から算出 |
| Codex の初期 Skill 一覧の合計 | **13,600 文字**（5,440 トークン = コンテキストの 2%） | Codex 公式ドキュメント。既定モデル gpt-5.6-sol のコンテキスト 272,000 から算出 |
| Kiro の初期 Skill 一覧の合計 | **規定なし** | 公式ドキュメントに一覧予算の記述がない。計測のみ行い判定しない |
| 全 Skill の frontmatter 合計 | 12,400 文字（**警告のみ**） | リポジトリ固有の目安。ランタイムが課す制約ではないため、超過しても警告にとどめる。**plugin family をまたいだ合計** |

### 予算はトークンで効く

比率が掛かるのは**コンテキスト長（トークン）**であって文字数ではない。Claude Code で
実測して確かめた（2026-08-15）。

```console
$ claude -p "/context" --output-format json                    # 既定予算
| Skills | 5.1k | 0.5% |
$ SLASH_COMMAND_TOOL_CHAR_BUDGET=200000 claude -p "/context"   # 予算を 20 倍
| Skills | 5.1k | 0.5% |                                       # 変わらない
$ SLASH_COMMAND_TOOL_CHAR_BUDGET=1000 claude -p "/context"     # 予算を絞る
| Skills | 2.3k | 0.2% |                                       # 切り詰められる
```

予算を上げても増えないので、**既定の予算では全量が載っており切り詰めが起きていない**。
このとき一覧の全量は 5.1k トークン（本書の文字数計測で 16,000 文字相当）で、
文字数として 1% = 10,000 を当てると超過するはずだが切り詰めは起きていない。

そこで**トークンで予算を持ち、文字数へ換算して判定する**。

### 換算比は Claude Code の実測を基準にする

**Skill 単位のトークン数を出せるのは Claude Code だけ**である。Codex の `/skills` は一覧
だけで使用量を出さず、Kiro の `/context show` は 4 区分（Context files / Tools /
Kiro responses / Your prompts）までで Skill 単位に割れない。実測できる唯一のランタイムに
合わせるのが最も確からしい。

```bash
python3 scripts/check-skill-frontmatter.py --calibrate
```

`claude -p "/context"` を実行し、**Skill 名が一致するものだけ**で実測トークンと文字数計測を
突き合わせて比を求め、`scripts/skill-listing-calibration.json` に保存する。以後の検査は
その値を使う。実測環境と本リポジトリで Skill の版や構成が違っても、名前が一致する分だけを
使うので影響しない。較正していない環境では安全側の既定（2.5 文字/トークン）を使う。

Skill を増減したときや、既定モデルが変わったときに再実行する。

### 初期一覧の予算は Codex が最も厳しい

| ランタイム | 既定モデル | コンテキスト | 規定 | 予算（トークン） | 予算（文字換算） |
| --- | --- | ---: | --- | ---: | ---: |
| Claude Code | Opus 5 | 1,000,000 | 1% | 10,000 | 27,799 |
| Codex | gpt-5.6-sol | 272,000 | 2% | 5,440 | 15,123 |
| Kiro | auto | 1,000,000 | 規定なし | — | — |

文字換算は較正値（2026-08-15 実測で 2.78 文字/トークン）による。`--calibrate` を実行すると
この値は更新される。

**コンテキストが最も長い Claude Code ではなく、Codex が全体の制約になる。** 比率が 2 倍でも
コンテキストが 1/3.7 のため、予算は Claude Code の約半分にしかならない。Skill を増やすときは
Codex の 5,440 文字を基準に考える。

Claude Code 側は `skillListingBudgetFraction` 設定または `SLASH_COMMAND_TOOL_CHAR_BUDGET`
環境変数で予算を引き上げられるが、**配布先の環境に依存する設定に頼らない**。

### 一覧に載るものはランタイムで違う

| ランタイム | `name` | `description` | `when_to_use` | ファイルパス |
| --- | :-: | :-: | :-: | :-: |
| Claude Code | ○ | ○ | ○（`description` と合算して 1,536 文字で切り詰め） | **×** |
| Codex | ○ | ○ | × | **○** |
| Kiro | ○ | ○ | × | 規定なし（多い側で見積もる） |

パスを載せるのは Codex だけである（*"In Codex, the initial list also includes each skill's
file path."*）。Claude Code の公式記述は *"a listing of skill names and descriptions"* で
パスに触れていない。**パスは 1 Skill あたり 30 文字前後あり、30 個なら 900 文字に達する**ので、
どちらで数えるかで結論が変わる。

### 予算は plugin family をまたいだ合計で判定する

利用者の環境には複数のプラグインが同時に入るため、family 単位で見ると超過を見逃す。
ただし片方しか入れない利用者もいるので、`--report` は family 別の内訳も出す。
Skill ごとの実測値は [`issues/old/skill-frontmatter-by-runtime.csv`](../../../issues/old/skill-frontmatter-by-runtime.csv)
にある。

運用目標の 300 文字は仕様上限より厳しい。全 Skill 分の `description` が常時注入されるため、
仕様上限は 1 個で使い切ってよい量ではない。

Codex は起動時に全 Skill の `name` と `description` とファイルパスを一覧として読み込み、この
一覧に総量予算を設けている。予算を超えると Codex はまず `description` を短縮し、それでも
収まらない場合は一部の Skill を一覧から省略して警告を表示する。**300 文字は 1 個あたりの
上限であって、全 Skill に一律で使ってよい枠ではない。** 総量が予算へ収まることを条件に配分し、
超過分を逃がす。

逃がし先は配布先で選ぶ。Claude Code だけに配布する Skill は `when_to_use` へ移してよい。
一方 `when_to_use` は Codex と Kiro では読まれないため、この 2 ランタイムへも配布する Skill の
トリガ語を `when_to_use` へ移すと、そのランタイムでは一覧に載らず暗黙起動に効かなくなる。
3 ランタイムへ配布する Skill はトリガ語を `description` に残したまま要約し、手順の説明を
本文へ逃がす。

Claude Code のコンパクション後は、呼び出し済み Skill の先頭 5,000 トークンのみが再添付され、
全体で 25,000 トークンの共通予算を新しい順に消費する。480 行級の Skill は圧縮後に後半が
失われるため、500 行上限は推奨ではなく必須条件として扱う。

## 項目の使い分け

| 項目 | 使いどころ |
| --- | --- |
| `paths` | 特定ディレクトリを扱うときだけ意味を持つ Skill。Claude Code 独自 |
| `context: fork` + `background: false` | 長時間実行をメインコンテキストから隔離したい Skill。`background: false` がないと結果が非同期で返る。組み込みの `/goal` と併用する Skill には使わない（セッション単位の Stop フックとして動く評価器が分離実行では働かない） |
| `effort: high` | 判断の質が結果を大きく左右する設計レビュー系 |
| `arguments` | `argument-hint` を持つ Skill。引数の手動解析を名前付き引数に置き換える |
| `license` / `metadata` | 上流の Skill を参考にした場合に、参照元名・固定コミット・ライセンスを記録する |

## 配布先を広げる際の制約

`when_to_use` / `argument-hint` / `arguments` / `disable-model-invocation` / `user-invocable` /
`paths` は Claude Code 独自である。仕様準拠のランタイムは未知の項目を無視するが、claude.ai への
アップロードや Skills API 経由では `Unexpected key(s) in SKILL.md frontmatter` のエラーになる。
現在の配布先 3 種では問題にならないが、配布先を広げる際の制約として記録する。

## 参照

- [Agent Skills Specification](https://agentskills.io/specification)
- [Claude Code — Skills](https://code.claude.com/docs/en/skills)
- [Codex — Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Kiro — Skills](https://kiro.dev/docs/skills/)
