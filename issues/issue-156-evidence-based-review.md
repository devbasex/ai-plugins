# cross-review を証拠ベースの合議制へ広げる（#156）

**マイルストーン**: 02 cross-review の証拠ベース化（主題）
**モード**: `standard`
**対象 issue**: #156

## 目的

**指摘の採否を、票の数ではなく検証できる根拠で決める。** 現行の `cross-review` は担当ごとの
判定（`APPROVE` / `REQUEST_CHANGES`）と重要度の自己申告を入力にしており、次の 2 つが起きる。

| 現象 | 実測 |
| --- | --- |
| 重要度の自己申告と判定が対応しない | PR #157 の round 6 で minor 2 件のみの `REQUEST_CHANGES` |
| 却下した論点が次のラウンドへ渡らず、同じ指摘が戻る | #69 で同じ論点が round 1〜5 の 5 回、#70 で 3 回 |

## 依頼の原文

issue #156 の本文を基準とする。提案は 5 つ（独立発見 / 重複統合 / 反証 / 実行検証 /
証拠ベース集約）で、MVP として 8 項目が挙がっている。

## この変更で確かめた事実

**却下の理由は現在も残らない。**

```console
$ sed -n '2807p' plugins/ndf/skills/cross-review/scripts/state.py
            "rejected": _count(fix.get("rejected")),
```

**却下した指摘の位置も残らない。** `fix` の戻り値の `rejected[]` は `comment_id` /
`summary` / `reason_for_rejection` の 3 つだけを持ち、同じファイルの `deferred[]` が持つ
`path` / `line` / `severity` を持たない（`plugins/ndf/skills/fix/SKILL.md:383-386`。
`deferred[]` は同じファイルの 378-382 行にある）。

## 受け入れ条件

### 記録の器（提案 2 / 3 / 5 の前提）

- [ ] 却下した指摘が、位置（`path` / `line`）・重要度・理由とともに状態ファイルへ残る
- [ ] 却下の記録がラウンドをまたいで読める（次のラウンドの入力になる）
- [ ] 既存の `deferred_nits` の蓄積と同じ形で読める

### 独立発見と重複統合（提案 1 / 2）

- [ ] 各担当は、自分の指摘を出し終えるまで他の担当の結果を参照しない
- [ ] 指摘は自由文に加えて、根拠（`evidence`）と反証条件（`falsification`）を持つ
- [ ] 重複を統合した後も、提案した担当の一覧（`origin_runtimes`）が残る
- [ ] 同じ指摘を出した担当の数と、その担当が独立かどうかを区別して数えられる

### 反証と実行検証（提案 3 / 4）

- [ ] 提案した担当以外が、各指摘へ `support` / `refute` / `insufficient_evidence` /
      `duplicate` / `out_of_scope` のいずれかを返す
- [ ] 実行できる検証手順（`suggested_check`）を持つ指摘は、担当の再評価より先に実行する
- [ ] 実行で再現した指摘は、支持した担当が少数でも棄却されない
- [ ] 実行で再現せず根拠も無い指摘は、支持が多くても修正必須にならない

### 証拠ベース集約（提案 5）

- [ ] 出力が `verified_blocking` / `verified_non_blocking` / `needs_human_judgment` /
      `rejected` / `insufficient_evidence` の 5 つへ分かれる
- [ ] 棄却した指摘に、棄却の理由が残る
- [ ] 収束の判定が、担当の判定（event）ではなく指摘ごとの検証結果を見る

### 効果の測定

- [ ] 同じ Pull Request 群に対して `single` / `majority` / `proposed` / `oracle` の 4 つを
      同じデータから計算できる
- [ ] 追加でかかった時間と実行の量を、方式ごとに並べられる
- [ ] 振動の検知が働いた回数と、上限に達して終わった回数を、変更の前後で比べられる

## 対象範囲

### 含む

- `plugins/ndf/skills/cross-review/`（`scripts/state.py` / 手順書 / レビュープロンプト）
- `plugins/ndf/skills/fix/`（`rejected[]` の形）
- 収束ループの共通層（`plugins/ndf/scripts/lib/`）のうち、この変更が触る部分

### 含まない

- `cross-refactoring` の投票と候補の選び方（#155 の設計に属する）
- 既知の不具合を仕込んだ比較用のリポジトリの作成（測定の枠組みだけを用意する）
- 収束の上限（`--max-rounds`）と振動の閾値（0.5）の変更

## 前提

- 前提 1: 実行できる検証手順は、**リポジトリが既に持つコマンド**（テスト・静的解析）に限る。
  新しい実行系は導入しない
- 前提 2: 担当の背後のモデルの系列は、実測で取れるのが claude だけであるため、
  **指定値を系列の代わりに使う**（`cross-refactoring` の集計と同じ扱い）
- 前提 3: 手順書の分量の上限（500 行）に収めるため、加筆は `docs/` の側へ置く

## 検証手段

| 何を確かめるか | コマンド |
| --- | --- |
| 単体テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 手順書の分量 | `python3 scripts/check-doc-line-limit.py` |
| 自リポジトリ前提 | `python3 scripts/check-skill-repo-assumptions.py` |

## 境界

```text
常に行う        … 既存テストの実行、状態ファイルの後方互換の確認
確認してから行う … 収束の判定そのものの変更、レビュープロンプトの構造の変更
行わない        … 上限値の調整、cross-refactoring への波及
```
