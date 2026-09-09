# 工程が止まる 2 件を直す（#518 / #524）

**マイルストーン**: 02 cross-review の証拠ベース化（主題の外から入れたもの）
**モード**: `standard`
**対象 issue**: #518（`cross-refactoring` の起動が 2 か所で止まる） / #524（push が credential helper の不全で落ちる環境の退避）

## 目的

**工程を通そうとした担当が、書かれたとおりに実行して止まる状態をなくす。** 2 件はどちらも
`priority: high` の bug で、止まる位置が違う。

| issue | どこで止まるか | 止まると何が起きるか |
| --- | --- | --- |
| #518 | `cross-refactoring` の起動（提案フェーズの手前 / `init` の関門） | 構造改善の工程に入れない |
| #524 | `pr` / `fix` / `cross-refactoring` の push | `cross-refactoring` は適用と検証が済んだ後に中断する |

## 依頼の原文

issue の本文をそのまま基準とする（要約しない）。判断の根拠になった実測は次の 3 つである。

- #518-1: `crf-loop.sh: line 28: RUNTIMES: unbound variable`
- #518-2: `❌ --scope にテストの置き場所が含まれていません（指定: plugins/ndf/skills/development-workflow）。`
- #524: `fatal: Authentication failed for 'https://github.com/devbasex/ai-plugins.git/'`（`gh auth status` は認証済み）

## この変更で確かめた事実

**#518-2 は手元で再現した。**

```console
$ python3 -c "...; scope.is_test_location('plugins/ndf/skills/development-workflow')"
False          # 実体として tests/ を持つが、名前だけの判定では False
```

**#518-1 は骨組みの写し方によって現れ方が変わる。** `rf_eval` の `local out rc` は
`eval` した代入をローカルにしないため（実測）、`init` から始めれば `RUNTIMES` は定義される。
`RUNTIMES` を返すのは `init` だけで、`start-round` は返さない。**繰り返しの中で使う値を
繰り返しの外の 1 回だけが返す構造**であるため、`init` を飛ばして再開する経路と、骨組みを
抜粋して写す経路の両方で未定義になる。

## 受け入れ条件

### #518-1: 繰り返しが参照する値を、繰り返しの中で得られる

- [ ] `refactor.py start-round` が提案・レビューの母集合（`RUNTIMES` / `RUNTIMES_CSV`）を返す
- [ ] `SKILL.md` の「実行」節の骨組みが参照する変数はすべて、**その行より前に実行する
      コマンドのいずれかが返す**。これを機械で検査する
- [ ] 提案フェーズの起動が母集合の全員に対して行われる（`REVIEWERS` で代用しない）

### #518-2: テストの実体を持つ親ディレクトリを関門が通す

- [ ] `--scope` に `plugins/ndf/skills/development-workflow` を渡したとき、`init` の関門で
      止まらない（同ディレクトリは `tests/` を実体として持つ）
- [ ] 実体を持たず名前にも当たらない `--scope`（`src/services` だけ）は、これまでどおり止まる
- [ ] まだ存在しないテストの置き場所を名前で渡す経路（`tests/services`）は、これまでどおり通る

### #524: helper の不全で止まらない

- [ ] `pr` / `fix` / `cross-refactoring` の手順書に、helper が応答しない環境での退避の手が
      書かれている
- [ ] `cross-refactoring` の進行側の push が helper の不全で落ちたとき、**退避して再試行する**。
      再試行も落ちたときは、原因が認証の未実施ではないことを出力へ残す
- [ ] 退避が働いたことは出力から分かる（黙って別の経路を使わない）

## 対象範囲

### 含む

- `plugins/ndf/skills/cross-refactoring/`（`SKILL.md` / `refactor_lib/` / テスト）
- `plugins/ndf/skills/pr/SKILL.md` と `plugins/ndf/skills/fix/SKILL.md` の push の手順
- 上の変更に対応するテスト

### 含まない

- `cross-review` の証拠ベース化（#156。同じマイルストーンの主題だが、触る対象が重ならない）
- `git push` を呼ぶその他の Skill の全数調査（#524 が挙げた 3 本に絞る）
- credential helper 自体の修復（利用者の環境の問題であり、こちらで直す対象ではない）
- クラス図（触る対象は module 関数だけで、追加・変更する型が無い）
- データ構造の変更（状態ファイルの `runtimes` を読むだけで、保存する形は変えない）

## 検証手段

| 何を確かめるか | コマンド |
| --- | --- |
| 単体テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| Skill の frontmatter | `python3 scripts/check-skill-frontmatter.py` |
| 説明文書の古さ | `python3 scripts/check-doc-staleness.py` |
| 自リポジトリ前提 | `python3 scripts/check-skill-repo-assumptions.py` |

## 前提

- 前提 1: helper の不全は利用者の環境ごとに現れ方が違うため、**退避は `gh` の認証が
  通っていることを条件とする**。`gh` も未認証なら退避しても通らず、その場合は失敗として扱う
- 前提 2: #518-1 の直しは `start-round` の出力を増やす方向で行う。骨組みの側だけを直すと、
  `init` を飛ばして再開する経路が残る

## 境界

```text
常に行う        … 既存テストの実行、変更箇所へのテスト追加
確認してから行う … push の既定の経路そのものの変更、他 Skill への波及
行わない        … 範囲外のリファクタリング、生成物の手編集
```
