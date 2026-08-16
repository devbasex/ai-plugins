# レビュー指摘の修正（cross-refactoring / $RF_RUNTIME / 提案ラウンド $RF_ROUND）

あなたは **$RF_RUNTIME**（モデル: $RF_MODEL）として、このラウンドに付いた
**未解決の指摘をまとめて修正**し、返信と解決まで行います。

## 必須コンテキスト

- リポジトリ: $RF_REPO / Pull Request #$RF_PR
- 作業ディレクトリ: `$RF_WORKDIR`（**ここでだけ作業する**）
- ブランチ: `$RF_HEAD_BRANCH`
- テストコマンド: `$RF_BASELINE_TEST`

## 手順書

$RF_SKILL_BLOCK

## このラウンドの改善項目

```json
$RF_ITEMS
```

## やること

1. `gh api` で Pull Request の**未解決レビュースレッド**を取得する
2. 各指摘について、**修正するか・しないか**を決める
   - 修正する: 1 手ずつ直し、その都度テストを実行してコミットする
   - 修正しない: 根拠を返信する。**黙って閉じない**
3. すべての対応が終わったら push する
4. 対応したスレッドに返信し、`resolveReviewThread` で解決する

指摘のうち、**振る舞いを変えないと直せないもの**は修正しないでください。
その場合は「この改善項目自体を見送るべき」と返信し、解決しないまま残します。
修正ラウンドの上限に達すると、その項目は自動で取り消されます。

## コミットの規約

適用と同じ基準で検証します。**トレーラー 4 つが揃い、各コミットでテストが成功して
いること**が必要です。満たさないコミットは取り込まれず、その改善項目の指摘は
解決済みになりません（修正ラウンドの上限に達すると項目ごと取り消されます）。

判定はオーケストレータが **git と実際のテスト実行**から行います。結果ファイルに
何と書いても検査結果は変わりません。

```text
Fix: レビュー指摘の反映 — src/foo/bar.py#BarService.handle

（何をどう直したか）

Item-Id: R1-002
Round: $RF_ROUND
Impl-Runtime: $RF_RUNTIME
Impl-Model: $RF_MODEL
```

## 守ること

- **`git push --force` と `--no-verify` を使わない**
- 作業ディレクトリの外を触らない
- **対象範囲（`$RF_SCOPE`）の外にあるファイルを 1 つも変更しない。** 範囲外を触った
  修正コミットがあると、その修正ラウンドの範囲ごと取り消されます
- **生成物・配布物の同期をしない。** 同期は進行側が収束後にまとめて行います
- 指摘に無い箇所を「ついでに」直さない。ラウンドの差分が膨らみ、
  どの変更がどの指摘に対応するのか追えなくなる

## 提出形式

結果を **`$RF_STEM-result.json`** に次の形で書いてください。

```json
{
  "elapsed_seconds": 133,
  "resolved_thread_ids": ["PRRT_xxxxx"],
  "unresolved": [
    {"thread_id": "PRRT_yyyyy", "reason": "振る舞いを変えずには直せない"}
  ],
  "commits": [
    {
      "sha": "def5678",
      "test_status": "pass",
      "trailers": {
        "Item-Id": "R1-002",
        "Round": "$RF_ROUND",
        "Impl-Runtime": "$RF_RUNTIME",
        "Impl-Model": "$RF_MODEL"
      }
    }
  ]
}
```

- `resolved_thread_ids` には**実際に解決したスレッド**だけを入れてください。
  申告は GitHub の `reviewThreads` の `isResolved` と突き合わせます。
  **`resolveReviewThread` を実行していないスレッドを書いても解決済みにはなりません。**
