# Skill 執筆規約

`plugins/ndf-shared/skills/` は 3 ランタイム（Claude Code / Codex / Kiro）へ配布する Skill の
編集元である。ここでは frontmatter の書き方を規約として定める。本文の書き方は各 `SKILL.md`
に委ね、規約は発動と配布に関わる部分だけを扱う。

規約は `scripts/check-skill-frontmatter.py` で機械検査し、継続的インテグレーションで実行する。

利用実績と維持・統合・削除の判定は
[docs/specifications/ndf-skill-inventory.md](../../../docs/specifications/ndf-skill-inventory.md)
に記録する。

## `description` を単一の真実とする

3 ランタイムで共通に効くのは Agent Skills 仕様の 6 項目（`name` / `description` / `license` /
`compatibility` / `metadata` / `allowed-tools`）だけである。`when_to_use` は Claude Code 独自の
項目で、Codex と Kiro は文書化していない。仕様は未知の項目を無視すると定めているため壊れは
しないが、**両ランタイムでは `description` だけで発動が判定される**。

したがって **発動判定に必要な情報はすべて `description` に入れる**。Claude Code 独自項目は
その上乗せとして扱う。

```yaml
---
name: review
description: "Review a PR or local branch diff and post an approve/changes verdict. Use when asked to review a PR, check a diff before merge, or self-review a branch (レビューして / PR確認 / マージ前チェック)."
when_to_use: "Claude Code 向けの追加トリガのみ。description で足りるなら付けない"
---
```

規則:

- `description` は「何をするか」と「いつ使うか」の両方を書く
- **主要な用途とトリガ語を最初の 1 文に置く。** Codex は初期一覧が予算を超えると `description`
  を先頭から残して短縮するため、後半へ置いたトリガ語は暗黙起動の判定に届かない
- `description` は二重引用符で囲む。Kiro はコロンを含む未引用の `description` を持つ Skill を
  検出対象から落とす（[kirodotdev/Kiro#8329](https://github.com/kirodotdev/Kiro/issues/8329)）
- frontmatter に `<` と `>` を含めない。Agent Skills 仕様がシステムプロンプトへの注入リスクと
  して警告している
- `when_to_use` は Claude Code 向けの**追加**トリガが要る Skill にだけ付ける。`description` の
  言い換えにしない。未設定であること自体は不備ではない

## 発動制御の 4 分類

| 分類 | Claude Code | Codex | Kiro | 対象 |
| --- | --- | --- | --- | --- |
| 自動発動（既定） | 追加トリガがあれば `when_to_use` を併記 | 既定で暗黙起動可 | 自動ロード | 知識・判断基準・ワークフロー |
| パス限定自動発動 | 上記 + `paths` | `paths` 無効 | `paths` 無効 | 特定ディレクトリでのみ意味を持つもの |
| 明示指示専用 | `disable-model-invocation: true` + `argument-hint` | `<Skill 名>/agents/openai.yaml` の `policy.allow_implicit_invocation: false` | 制御手段なし。`description` に「利用者が明示的に指示したときのみ実行する」と記載 | 破壊的操作・外部への書き込み |
| 常時注入のみ | `user-invocable: false` | 相当機能なし | 相当機能なし | `ndf-policies` |

- `disable-model-invocation: true` の Skill は `description` がコンテキストへ載らない。
  `user-invocable: false` は載る
- 明示指示専用にしてよいのは、実行してしまうと取り消しが難しい操作に限る。日常的に自然文で
  依頼される Skill に付けると、エージェントは Skill を使わず独自手順で実行する
- `disable-model-invocation: true` と `user-invocable: false` を同時に指定しない。誰も起動
  できなくなる

## トリガ語の規則

### 一意であること

トリガ語は Skill 間で重複させない。重複すると、同じ依頼に対して複数の Skill が起動を競い、
どちらが選ばれるかが依頼文の細部に左右される。

重複を見つけたら、次のどちらかで解消する。

1. 機能が重複しているなら Skill 自体を統合する
2. 機能が異なるなら、区別できるところまでトリガ語を具体化する

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
| `name` | 64 文字。小文字英数とハイフンのみ。先頭末尾ハイフン不可、連続ハイフン不可。親ディレクトリ名と一致 | Agent Skills 仕様（必須） |
| `description` | 1,024 文字。運用目標は 300 文字以内 | 仕様上限 / 運用目標 |
| `description` + `when_to_use` | 1,536 文字 | Claude Code。超えると一覧で切り詰められる |
| `compatibility` | 500 文字 | Agent Skills 仕様 |
| `SKILL.md` 行数 | 500 行。超えるものは補助ファイルへ分割 | 仕様の推奨、コンパクション対策 |
| `SKILL.md` 本文 | 5,000 トークン | 仕様の推奨 |
| Codex の初期 Skill 一覧の合計 | コンテキストウィンドウの 2%。不明な場合は 8,000 文字 | Codex 公式ドキュメント |

運用目標の 300 文字は仕様上限より厳しい。全 Skill 分の `description` が常時注入されるため、
仕様上限は 1 個で使い切ってよい量ではない。

Codex は起動時に全 Skill の `name` と `description` とファイルパスを一覧として読み込み、この
一覧に総量予算を設けている。予算を超えると Codex はまず `description` を短縮し、それでも
収まらない場合は一部の Skill を一覧から省略して警告を表示する。**300 文字は 1 個あたりの
上限であって、全 Skill に一律で使ってよい枠ではない。** 総量が予算へ収まることを条件に配分し、
超過分は `when_to_use` と本文へ逃がす。

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
