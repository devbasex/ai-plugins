# リファクタリング適用（cross-refactoring / $RF_RUNTIME / 提案ラウンド $RF_ROUND）

あなたは **$RF_RUNTIME**（モデル: $RF_MODEL）として、採用された改善項目を
**優先度順に 1 件ずつ適用**します。

## 必須コンテキスト

- リポジトリ: $RF_REPO / Pull Request #$RF_PR
- 作業ディレクトリ: `$RF_WORKDIR`（**ここでだけ作業する**）
- ブランチ: `$RF_HEAD_BRANCH`（base は `$RF_BASE_BRANCH`）
- テストコマンド: `$RF_BASELINE_TEST`

## 手順書

$RF_SKILL_BLOCK

## 適用する改善項目

```json
$RF_ITEMS
```

## やること

配列の順に、**1 項目ずつ**次を行います。前の項目が終わるまで次に進まないでください。

1. `test_gap` が真なら、**先に現状固定テストを追加してコミットする**。
   これは省略できません。振る舞いが変わっていないことを示す手段が無いまま
   構造を変えるのは、構造改善ではなく単なる編集です
2. `plan` の手順を **1 手ずつ**適用する
3. 1 手ごとに `$RF_BASELINE_TEST` を実行する。落ちたら**直前の 1 手を戻す**
4. 通ったらコミットする（**1 手 = 1 コミット**）
5. 項目が終わったら push する

## コミットの規約

コミットメッセージの本文末尾に、**git のトレーラー形式**で実行主体を残します。
4 つすべてが揃っていないコミットは、その項目ごと失敗として扱われます。

```text
Refactor: extract_method — src/foo/bar.py#BarService.handle

（変更の説明）

Item-Id: R1-001
Round: $RF_ROUND
Impl-Runtime: $RF_RUNTIME
Impl-Model: $RF_MODEL
```

- **`Item-Id` は必ずその項目のものにする。** 複数の項目を 1 コミットへまとめると、
  取り消し範囲が項目単位で決まらなくなり、失敗として扱われます
- `Impl-Model` には**実際に使ったモデル名**を書く。分からなければ `default`

## 守ること

- **`git push --force` と `--no-verify` を使わない**
- 作業ディレクトリの外を触らない
- **機能変更を混ぜない。** 振る舞いを変える修正が必要だと分かったら、その項目は
  適用せず `status` を `skipped` にして理由を書く
- 提案された手順の範囲を超えない。ついでの整理をしない
- 1 項目が失敗しても**残りの項目は続ける**。全体を止めない

## 提出形式

結果を **`$RF_STEM-result.json`** に次の形で書いてください。

```json
{
  "base_sha": "着手前の HEAD",
  "head_sha": "終了時の HEAD",
  "elapsed_seconds": 461,
  "items": [
    {
      "item_id": "R1-001",
      "status": "applied",
      "diff_lines": 38,
      "commits": [
        {
          "sha": "abc1234",
          "test_status": "pass",
          "characterization_test": true,
          "trailers": {
            "Item-Id": "R1-001",
            "Round": "$RF_ROUND",
            "Impl-Runtime": "$RF_RUNTIME",
            "Impl-Model": "$RF_MODEL"
          }
        }
      ]
    }
  ]
}
```

- `diff_lines` は**その項目のコミット群の追加 + 削除行数**。
  見積りの 2 倍を超えると範囲の逸脱として失敗になります
- `characterization_test` は、そのコミットが**現状固定テストの追加**なら真。
  `test_gap` が真の項目では、**最初のコミット**が真である必要があります
- `test_status` は `pass` / `fail`。1 つでも `fail` があればその項目は失敗です
- 適用しなかった項目も `status` と理由を添えて必ず配列へ入れてください
