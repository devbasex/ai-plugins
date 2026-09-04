---
name: progress-tracking
description: "Record which development stage an issue has reached, in the issue body and on the board. Use when entering a stage of the workflow（進行を記録・工程の記録・どこまで進んだか）."
argument-hint: "[issue番号] [工程名] [--mode M] [--worktree P] [--plan P]"
allowed-tools:
  - Bash
  - Read
---

# 工程の進行を記録する

いまどの工程まで終わったかを、セッションの外へ残す。**判定したモードも、通った工程も、
記録しなければ会話の中にしか残らない。** セッションが変わると引き継がれない。

**この Skill は順序を持たない。** 工程表には載らず、モードごとの要否も持たない。どの工程からも
呼ばれる（`out-of-scope` と同じ扱いである）。

## 何を記録するか

| 記録先 | 何が残るか | 要る条件 |
| --- | --- | --- |
| issue の本文 | 工程のチェックリスト・モード・作業ツリー・計画ファイル | `gh` が使えること |
| 盤面（GitHub Projects） | 工程・モード・作業ツリー・計画ファイル | リポジトリに `.ndf/projects.json` があること |

**盤面の宣言が無いリポジトリでも issue へ残る。** 宣言は盤面へ書くかどうかだけを決める。

**この記録は判定の入力ではない。** 工程の飛ばしの検知は通過工程の控えが担う
（`development-workflow` の `references/stage-completeness.md`）。ここに残すのは、**人が
セッションの外から現在地と成果物の所在をたどるためのもの**である。

## この文書が受け取る値

| 変数 | 値 | 決め方 |
| --- | --- | --- |
| `$SCRIPTS` | プラグインの `scripts/` の絶対パス | [references/scripts-lookup.md](../development-workflow/references/scripts-lookup.md)。シェルが変わったら決め直す |

## 呼び方

**工程に入った時点で 1 度呼ぶ。** 出るときではない。入った時点で記録すれば、途中で止まった
実行の現在地が残る。

```bash
bash "$SCRIPTS/progress-record.sh" <issue番号> "<工程名>" [--mode M] [--worktree P] [--plan P]
bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "<工程名>"
```

**値は引用符で囲む。** 工程名には空白を含むもの（`Pull Request`）がある。囲まないとシェルの
側で分割され、引数の検査で終了コード 2 になる。

```bash
bash "$SCRIPTS/progress-record.sh" 186 "設計" --mode standard --worktree ".worktrees/fix/issue-186"
bash "$SCRIPTS/projects-sync.sh" 186 stage "設計"
bash "$SCRIPTS/projects-sync.sh" 186 mode "standard"
```

**工程が終わりのものなら `status` も書く。** 振り返り（最後の工程）の後は盤面の
`Status` を `Done` にする。issue の側は工程のチェックリストが埋まることで表せるため、
この値は盤面にだけ書く。

```bash
bash "$SCRIPTS/projects-sync.sh" <issue番号> status "Done"
```

**工程名の一覧は工程表が持つ。** この Skill は一覧を持たない
（`development-workflow` の「モードごとに起動する Skill」の表が唯一の基準である）。一覧に
無い値を渡すと、どちらのコマンドも終了コード 2 で弾く。

## 何もしない条件

**進行管理が理由で開発の工程を止めない。** 次のいずれでも、理由を 1 行出して終了コード 0 で
終わる。

| 条件 | どちらが止まるか |
| --- | --- |
| `gh` が無い | 両方 |
| issue を取得できない | issue への記録 |
| `.ndf/projects.json` が無い | 盤面への記録 |
| 盤面への問い合わせが上限に達した | 盤面への記録（1 行知らせる） |
| 盤面にアイテムが無く、追加もできない | 盤面への記録（1 行知らせる） |

**呼び出し側の誤りだけは 2 を返す。** 知らない工程名・引数の不足がこれにあたる。黙って進むと、
綴りの違う値が入るか、書き込んだつもりの値が入らない。

## issue の本文に残る形

```markdown
## 進行

モード: standard / 作業ツリー: `.worktrees/feat/issue-243`

- [x] 作業場所の用意 — 2026-09-04 06:12
- [x] 要求と受け入れ条件 — 2026-09-04 06:40
- [ ] 設計
- [ ] 設計レビュー
```

- **節の外は書き換えない。** 更新のたびに本文を取得し、`## 進行` の見出しから次の見出しまで
  だけを差し替える。人が本文へ書いた内容は残る
- **飛ばした工程は空欄のまま残る。** チェックの穴として見える
- **判断を伴う場面だけコメントを残す。** モードを途中で上げた・工程を意図して飛ばした・
  受け入れ条件を変えたの 3 つは、本文を見ても理由が分からない

## 盤面の設定

盤面へも残す場合の宣言ファイル・フィールド・工程との対応は
[references/projects-tracking.md](../development-workflow/references/projects-tracking.md)
にある。アイテムの追加・識別子の控え・盤面が決まらないときの扱いは
[references/board.md](references/board.md) にある。

## 関連

- `/ndf:development-workflow` — 工程名の一覧（工程表）
- `/ndf:out-of-scope` — 同じく順序を持たない横断的な手順
