# 貢献の手引き

このリポジトリは、Claude Code / Codex / Kiro CLI / agy の 4 つのランタイムへ同じプラグインを
配布するマーケットプレイスです。参加してくださる方が最初に読む 1 ファイルとして、この文書を
置いています。

手順の本体が別の文書にあるものは、そこへのリンクで示します。同じ規則を 2 か所に書くと、
片方だけが古くなるためです。

## 歓迎するもの

| 種類 | 例 |
| --- | --- |
| 不具合の報告 | Skill の手順どおりに実行して失敗した、フックが意図しない案内を出す |
| Skill の追加・改善 | 新しい工程の手順、既存 Skill の記述の誤りの修正 |
| ランタイム対応 | 4 つの CLI のいずれかでの挙動の違いへの対処 |
| 実機での検証 | 手元の環境で導入し、書かれたとおりに動くかを確かめた結果の報告 |
| 文書の改善 | 説明の不足の補い、用語の統一、リンク切れの修正 |

**実機での検証は、コードの変更と同じくらい役に立ちます。** 開発者が持っていないランタイムや
OS の組み合わせは、報告がないと分かりません。

## 開発環境の用意

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
```

必要なものは次のとおりです。検証の対象にするランタイムの CLI だけがあれば着手できます。

| 用途 | 必要なもの | 備考 |
| --- | --- | --- |
| テストの実行 | Python 3 と [uv](https://docs.astral.sh/uv/) | `python3 -m pip install --upgrade uv` |
| プラグイン定義の検証 | Node.js 22 と `@anthropic-ai/claude-code` | `npm install -g @anthropic-ai/claude-code` |
| 検証したいランタイム | `claude` / `codex` / `kiro-cli` / `agy` のいずれか | 導入手順は [README.md](./README.md) の「利用方法」 |

## 開発の進め方

**規則の本体は [AGENTS.md](./AGENTS.md) の「Git運用ルール」にあります。** 要点だけを挙げます。

- Pull Request の宛先は `develop` です。既定ブランチが `main` のため、`gh pr create` には
  `--base develop` を付けます
- `main` と `develop` へ直接コミットしません
- ブランチ名は `feature/<名前>` / `fix/<名前>` / `docs/<名前>` の形にします
- 変更は `.worktrees/<ブランチ名>` の worktree の中で行います。clone したディレクトリを
  編集対象から外すことで、複数の作業が同じ場所で混ざらなくなります

worktree の用意は次のコマンドで行えます。

```bash
git fetch origin
git worktree add -b feature/<名前> .worktrees/feature/<名前> origin/develop
cd .worktrees/feature/<名前>
```

## 手元での検証

**Pull Request を出す前に、次の 5 つをこの順で実行します。** 継続的統合が実行するものと同じ
チェックです。リポジトリの根から実行します。

```bash
# 1. テスト（継続的統合と同じ範囲を 1 回の起動で回す。-n auto でコア数だけ並列に回す）
uv run --frozen --project . --all-extras pytest . -q -n auto

# 2. Skill の frontmatter の規約
python3 scripts/check-skill-frontmatter.py

# 3. 説明文書に書かれた版数・Skill 数の整合
python3 scripts/check-doc-staleness.py

# 4. 文書間のリンク
python3 scripts/check-markdown-links.py --root .

# 5. formatter と静的解析（ruff format --check・ruff check・shellcheck -S warning）
bash scripts/check-lint.sh
```

**5 は git が追跡する Python と sh の全体を検査します。** 版と設定は根の `pyproject.toml`
（`[tool.ruff]` と `[dependency-groups] lint`）と `uv.lock` が固定し、継続的統合の `lint.yml` も
同じコマンドを呼びます。違反があれば 1、`uv` が無いなど検査の仕組みが落ちたら 2 で終わります。
`bash scripts/check-lint.sh --fix` は、ruff が自動で直せる違反を直して整形してから検査します
（sh は直しません）。`bash scripts/install-dev-hooks.sh` を入れた clone では、`git push` の前に
`.githooks/pre-push` が同じコマンドを走らせます。

抑止には理由を添えます。行の抑止は同じ行か直前の行に、設定の除外は `pyproject.toml` のコメントに書きます。

```python
# 次の 5 つは、他のモジュールが monitor.monitor_proc のように使う再公開の import である（F401 の抑止の理由。#1323）
import monitor_proc  # noqa: E402,F401
```

（`plugins/ndf/scripts/lib/monitor.py` から）

プラグイン定義そのものを変えたときは、あわせて次を実行します。**引数にプラグインのパスが
要ります。**

```bash
claude plugin validate plugins/ndf
bash scripts/build-runtime-plugins.sh --check
bash scripts/validate-runtime-plugins.sh
```

`plugins/ndf/skills/` だけを触った場合は 1〜5 で足ります。

## 一括の整形と git blame

**Python の全体を一度だけ整形し直しました（#1323）。** 一括の自動修正（`ruff check --fix`）と
一括の整形（`ruff format`）の 2 つのコミットは、根の `.git-blame-ignore-revs` に載っています。
GitHub の blame の画面はこのファイルを自動で読みます。手元の `git blame` に読ませるには、clone ごとに
1 回設定します。

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

この設定のまま、`.git-blame-ignore-revs` の無い版（一括の整形より前のタグやブランチ）を checkout した
作業ツリーで `git blame` を打つと、`fatal: could not open object name list` で落ちます。そのときは
1 回だけ設定を外して打ちます。

```bash
git -c blame.ignoreRevsFile= blame <ファイル>
```

### 一括の整形の前から続くブランチを取り込む

そのまま `develop` を取り込むと、触ったファイルが整形の差で衝突します。次の順で取り込むと、衝突は
自分の変更だけに残ります。

```bash
git fetch origin
AUTOFIX=$(git show origin/develop:.git-blame-ignore-revs | grep -E '^[0-9a-f]{40}$' | sed -n 1p)
FORMAT=$(git show origin/develop:.git-blame-ignore-revs | grep -E '^[0-9a-f]{40}$' | sed -n 2p)

# 1. 一括の自動修正の直前まで取り込む（検査の設定と scripts/check-lint.sh が入る）
git merge "$AUTOFIX^"

# 2. 自分のブランチへ同じツールを掛けてコミットする。直せない違反が残ると 1 で終わるが、ここでは続ける
bash scripts/check-lint.sh --fix
git commit -am "Refactor: 一括の整形（#1323）に合わせて整形する"

# 3. 一括の整形を取り込む。両側とも同じツールの出力なので、衝突は自分の側を採る
git merge -X ours "$FORMAT"

# 4. 残りの develop をふつうに取り込む（ここで出る衝突は、ふつうの衝突として解く）
git merge origin/develop

# 5. 自分の変更に残る違反を直して、0 で終わるまで繰り返す
bash scripts/check-lint.sh
```

**`-X ours` は 3 の 1 回だけに使います。** 4 で使うと、一括の整形の後に `develop` へ入った手の修正を、
衝突した箇所で捨てます。

## コミットメッセージ

日本語で書きます。先頭に種別を置きます。

| 接頭辞 | 用途 |
| --- | --- |
| `Add:` | 追加 |
| `Update:` | 既存のものの更新 |
| `Fix:` | 不具合の修正 |
| `Docs:` | 文書の更新 |
| `Refactor:` | 振る舞いを変えない構造の変更 |
| `Test:` | テストの追加・修正 |

```text
Fix: 作業ツリーの書き込み先の判定が case のフォールスルーを取り違える

リダイレクトの左に添えたファイル記述子の番号を書き込み先として拾っていた。
```

## Skill を追加・変更するとき

**Skill の実体は `plugins/ndf/skills/` の 1 か所だけです。** ランタイムごとの複製は置きません。

| 決めること | どこで決まるか |
| --- | --- |
| どのランタイムへ配るか | `plugins/ndf/manifests/<ランタイム>-skills.txt` に名前を 1 行書く |
| frontmatter の書き方 | [plugins/ndf/skills/AUTHORING.md](./plugins/ndf/skills/AUTHORING.md) の規約 |
| 文章の書き方 | `plugins/ndf/skills/markdown-writing/SKILL.md` |
| 新しいプラグインそのものを足すとき | [docs/plugin-development-guide.md](./docs/plugin-development-guide.md) の「新しいプラグインの追加」 |

frontmatter は `python3 scripts/check-skill-frontmatter.py` がチェックします。`description` の
長さには上限があり、4 つのランタイムのうち最も厳しいものに合わせています。上限と根拠は
Skill 執筆規約の「上限値」にあります。

## Pull Request を出す

1. 手元での検証の 5 つを通します
2. `--base develop` を付けて Pull Request を作ります
3. 本文に、変更の要約・関連する issue・検証した手段と結果・影響するランタイムを書きます
4. 継続的統合の結果を確かめます

**未完成のまま意見を求めたいときは draft で出してください。** 方向を早く確かめられます。

## レビューの流れ

誰が見るか、どう決まるかは [GOVERNANCE.md](./GOVERNANCE.md) にあります。

- 現在のメンテナーは 1 人です。返信までに数日かかることがあります
- 変更を求めた場合は、その理由と、どう直すと通るかを書きます
- 議論が長くなりそうなときは、Pull Request ではなく issue で先に方針を決めます

**急ぎのときは issue にその旨を書いてください。** 期限のある検証や、公開中の不具合の修正は
先に見ます。

## AI エージェントを使う場合

このリポジトリは AI エージェント向けの指示書を持っています。

| ファイル | 対象 |
| --- | --- |
| [AGENTS.md](./AGENTS.md) | 4 ランタイム共通。開発の規則の本体 |
| [CLAUDE.md](./CLAUDE.md) | Claude Code 固有の設定 |
| [KIRO.md](./KIRO.md) | Kiro CLI 固有の設定 |

**AI エージェントを使うことは求めません。** 使う場合は、これらの指示書に従うようにしてください。
生成された変更でも、内容の責任は Pull Request を出した方にあります。手元での検証は必ず通して
ください。

## そのほか

| 内容 | 参照先 |
| --- | --- |
| 参加するうえで守ること | [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) |
| 役割と決め方 | [GOVERNANCE.md](./GOVERNANCE.md) |
| 脆弱性の報告 | [SECURITY.md](./SECURITY.md) |
| 使い方の質問 | [SUPPORT.md](./SUPPORT.md) |
| ライセンス | [LICENSE](./LICENSE)（MIT） |

貢献された内容は MIT ライセンスで公開されます。
