---
name: estimate
description: "Estimate how much a task costs when AI agents do it, from similar past runs instead of human-era effort. Use when sizing an issue or task before starting it（工数を見積もる・工数算出・作業の大きさを見積もる）."
argument-hint: "[課題番号 | 作業の説明]"
allowed-tools:
  - Bash
  - Read
  - Grep
---

# AI エージェントが実施する前提で工数を見積もる

課題番号か作業の説明を受け取り、近い条件の実績から A（AI の実行量）・H（人の作業時間）・L（完了までの時間）・
C（利用費用）の見込みを、値ごとに根拠を付けて返す。**人の工数の基準で見積もらない。人日や AI の倍率から換算しない。**
4 つの値を足して 1 つの「工数」にしない。定義と規則の SSOT は [references/rules.md](references/rules.md) にあり、
この手順は写さずにそれを指す。

## 例: 課題 #1856 を実装の完了まで見積もる

AI が本文を読んで着手前の 6 項目を判定し、同じ種類の変更をした閉じた課題（例: #1601・#1631）の実績を GitHub から読む。

```bash
gh issue view 1601 --json number,title,createdAt,closedAt,closedByPullRequestsReferences \
  --jq '{number,createdAt,closedAt,prs:[.closedByPullRequestsReferences[].number]}'
gh pr view <上で出た PR の番号> --json number,createdAt,mergedAt,commits,reviews,additions,deletions \
  --jq '{number,createdAt,mergedAt,commits:(.commits|length),reviews:(.reviews|length),additions,deletions}'
```

比べた課題ごとの値を並べ、A・H・L・C の見込みを、マーカー（`実績` / `未校正` / `材料なし`）と根拠（比べた課題と実測）と
ともに表にして示す。

## 手順

1. **対象を読む。** 課題番号なら `gh issue view <番号>` で本文を読む。作業の説明なら、その文と、触るファイルの
   見当を手元のリポジトリで確かめる
2. **完了条件を決める。** `設計`・`実装`・`検査`・`開発版のデプロイ`・`リリース` のどこまでを見積もるか
   （規約の「見積もりの完了条件」）。利用者が言わなければ `実装` にし、そう決めたことを回答に書く
3. **着手前の 6 項目を判定する。** 規約の「着手前の 6 項目」の表で `軽`・`重`・`判定できない` を付ける。
   着手前に分かることだけで判定し、分からない項目は `判定できない` のままにする
4. **近い課題を選ぶ。** 同じ種類の変更をした閉じた課題を、`gh issue list --state closed --search "<語>"` などで探し、
   6 項目の判定が近いものを選ぶ（規約の「類似の実績の選び方」）
5. **実績を読む。** 選んだ課題と、それを閉じた PR を上の例の形で読む。L は課題の作成から完了条件に届いた時点
   （PR のマージなど）までの経過、手戻りの材料は PR のコミット数とレビューの回数である。C の材料は、その課題の
   作業だけが動いた期間があれば `ndf records usage report --since <日付> --until <日付>` のトークンの合計から取る
   （期間の合計なので、ほかの作業と重なる期間は使わない）
6. **実績が無い値を扱う。** 比べる実績の無い値は、想定の手順と既知のテストの所要から暫定の範囲を作れるときだけ
   `未校正` のマーカーで示す。作れなければ空のまま示す。人日や倍率で埋めない
7. **回答する。** 値ごとに見込み（通常 / 上振れ、または P50 / P80）・マーカー・根拠を表にし、構成の違う実績が
   あれば別に示す。`未校正` と `材料なし` を隠さない。6 項目のうち `重` のものを、見込みが膨らむ理由
   として添える

## 関連

- 棚卸しの Size は `backlog-refinement` が同じ規約の見込みから決める（`references/ranking.md` の「大きさ」）
