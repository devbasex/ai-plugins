# #892 / #901: cross-review の母集合にホストを入れ、supervisor が worker の途中の通知で止まらないようにする — 要求と受け入れ条件

設計は [issue-892-901-design.md](issue-892-901-design.md)、決定の理由は
[issue-892-901-design-decisions.md](issue-892-901-design-decisions.md) にある。この文書は「何を満たすか」だけを扱う。

マイルストーンは「17 トークン消費の削減」。版は 10.17.x（PATCH）で出す。2 つの課題は触るファイルが重ならないが、
どちらも以後の cross-review と全持ち場に効くため、1 本の設計 Pull Request にまとめる（conductor の判定）。

## 目的

- **cross-review の輪番を働かせる（#892）。** Claude Code から起動すると、今の母集合は codex / kiro / agy の 3 者になる。agy は収束ループで止まるため外すと（#786）、毎ラウンド codex と kiro の同じ組になる。ホストを母集合に入れれば 3 者から 2 席を回せる
- **supervisor が worker の途中の通知で止まらないようにする（#901）。** 今の規約は、中間の層にも「2 回目の通知を待つ」と読ませる。supervisor が応答を終えると誰にも起こされず、conductor が `SendMessage` で続けさせるまで止まる

## 対象範囲

含む:

- cross-review の母集合の既定を、全ランタイム（ホストを含む）に変える（#892）
- 席が足りないときの埋め合わせの規則を、ホストが母集合に入る前提で決め直す（#892）
- 変更の前に始めた cross-review のループを再開したとき、担当の決め方を変えない（#892）
- cross-review の `SKILL.md`・`docs/05`・`CLAUDE.md`・仕様書の、ホストを除く前提の記述を直す（#892）
- `waiting.md` の「2 回目の通知を待つ」を conductor に限り、supervisor の手を決める（#901）
- `agent-layers.md` の supervisor の規則 4・worker の規則 5・worker の起動指示を、上の手に合わせる（#901）

含まない:

- 既定の母集合から agy を外すこと（#786）。この変更の後も、agy を外すには `--exclude agy` を渡す
- cross-refactoring の母集合（既にホストを含む。変えない）
- ラウンド数を減らす工夫（#542）、ホストが Claude Code 以外のときの代わりの手順（#888）
- サブエージェントをまったく応答を終えずに待たせる手（#656）。この変更は supervisor が worker を待つ場面だけを扱う
- worker の定義 `ndf:worker` と抜粋（#828 の実装）。この変更の規則の文は、#828 の実装が同じ節を書き換えても保てる形で書く
- 設計の成果物のうちクラス図・画面: 変えるのは既存の関数 1 つの中身・分岐 1 つ・文書で、型も画面も持たないため対象が無い

## 受け入れ条件

### 母集合（#892）

- [ ] AC1: `review_pool(host)` が、どのホスト（claude / codex / agy / kiro）でも全ランタイムの 4 者を `ALL_RUNTIMES` の順で返す
- [ ] AC2: Claude Code をホストにして `--exclude agy` で `init` すると、使える者が claude / codex / kiro の 3 者になる。ラウンド 1〜3 の席は 3 通りの組を 1 度ずつ取る
- [ ] AC3: codex・kiro・agy がホストのときも、`init` の出力の「母集合」にホストのランタイムが入る
- [ ] AC4: `--exclude <ホスト>` が受け付けられ、ホストを外した母集合で始まる（今は「母集合に無い者は外せません」で終了コード 1）
- [ ] AC5: 使える者が 2 者に満たないとき、ホストを別に確かめて埋め合わせに使わない。1 者なら `<その者>-2` で埋め、0 者なら `init` が終了コード 1 で止まる。新しく作る状態ファイルの `fallback` は空の一覧になる

### 再開の互換（#892）

- [ ] AC6: `participants` を持つ既存の状態ファイル（変更の前に `init` したもの）を再開したとき、各ラウンドの席が変更の前と同じになる。保存された `available` と `fallback` を使うためである
- [ ] AC7: `participants` を持たず `host` だけを持つ状態ファイルを再開したとき、席が変更の前と同じ（全ランタイム − ホストの 3 者からの輪番）になる。4 つのホスト × ラウンド 1〜12 をテストが固定する

### 途中の通知（#901）

- [ ] AC8: `waiting.md` の「2 回目の通知を待つ」が conductor に限られ、supervisor が worker の途中の通知を受けたときの手が同じ文書に 1 つ書かれている。その手は応答を終える前に supervisor 自身の背景の処理を起動する
- [ ] AC9: `agent-layers.md` の supervisor の規則 4 が AC8 の場面を指し、worker の規則 5 が `Monitor` で待つときも途中の通知が親へ届くことを書く。規則の数は 10 と 5 のまま
- [ ] AC10: worker の起動指示の `置き場所` の項目が、worker に `## 作業の報告` の写しをそのファイルの末尾へ書かせる。`置き場所` を「無し」にしない。supervisor は worker を起動する前に、`置き場所` のファイルを worker ごとに新しいパスで空に作る（`: > <置き場所>`）
- [ ] AC11: supervisor → worker の 2 段を Claude Code で再現する。worker は背景で 30 秒待つ。supervisor は worker の途中の通知で止まらず、worker の `## 作業の報告` を受け取ってから次の段へ進む。conductor が報告なしで続けさせた回数は 0 になる

### 全体

- [ ] AC12: 全体テストが通る

## 前提

- 前提 1: レビュー担当は CLI プロセスとして起動する。ホストと同じランタイムでも、ホストの会話の作業文脈は持ち込まれない（cross-refactoring の `SKILL.md` の「参加者」と同じ前提。#892 の「なぜ変えるか」）
- 前提 2: ホストを外した理由（自分に甘い）は、2026-09-23 の利用者の判断で取り下げられた
- 前提 3: 背景の処理を残して応答を終えたサブエージェントは、その処理の完了通知で再開する（`waiting.md` の実測、Claude Code 2.1.280）。supervisor 自身が起動した背景の Bash はこれに当たる
- 前提 4: supervisor が起動した worker は、supervisor の「背景の子」に数えられない（#901 の実例の手順 5 の注記）。worker が後で終わっても、応答を終えた supervisor は起こされない

## 非機能の条件

| 項目 | 条件 |
| --- | --- |
| 互換 | 進行中の cross-review のループの担当を変えない（AC6・AC7） |
| 費用 | supervisor の待ちは背景の起動と完了通知 1 回で済ませる。待つ間に問い合わせを繰り返さない（`waiting.md` の「禁じる待ち方」） |
| 止まり方 | 待ちに上限を置く。worker が報告を書かずに落ちても、supervisor が永久に待たない |

## 影響

| 対象 | 変わること |
| --- | --- |
| cross-review の利用者 | Claude Code から起動すると `claude -p` がレビュー担当に入る。既定の母集合が 4 者になり、agy が確認を通る環境では 4 者で回る |
| cross-review の `--include <ホスト>` | 既に母集合に入っているため、指定しても結果が変わらない（エラーにはしない） |
| 3 層で進める持ち場 | worker の途中の通知で supervisor が止まらない。conductor の「報告なしで続けさせる」が減る |
| worker | `置き場所` のファイルの末尾へ報告の写しを書く手間が 1 つ増える |

## 検証手段

| 条件 | 手段 |
| --- | --- |
| AC1〜AC7 | `plugins/ndf/scripts/tests/`・`plugins/ndf/skills/cross-review/tests/`・`plugins/ndf/skills/cross-refactoring/tests/` の pytest（`uv run ... pytest -q -n 4`） |
| AC8〜AC10 | 文書の該当の節を読んで確かめる（文言を固定するテストは書かない） |
| AC11 | `claude -p` の stream-json で 2 段を動かし、supervisor の最後の応答に worker の報告が畳まれていることと、supervisor の完了通知が 1 回で `## 作業の報告` を含むことを確かめる。実装の Pull Request に記録を残す |
| AC12 | 全体テスト |

## 境界

```text
常に行う      … 既存テストの実行、ホストを除く前提を持つ記述の洗い出し
確認してから行う … 既定の母集合から agy を外すこと（#786 の判断）
行わない      … cross-refactoring の母集合の変更、#828 の実装の先取り、#656 の一般解
```

## 依頼（原文）

#892:

> cross-review のレビュー担当の母集合からホストを除外するのをやめ、ホストのランタイムも輪番に入れる。cross-refactoring と同じ扱いにそろえる。

#901:

> supervisor が、worker の 1 回目（途中）の通知を受けて「2 回目の通知を待つ」と応答を終え、そのまま止まった。conductor には「背景の子が 1 つも残っていない状態で止まった」と届き、自動では再開されなかった。

## 用語

| 用語 | 意味 |
| --- | --- |
| 母集合 | cross-review の参加者の出発点。`review_pool(host)` が返す |
| 使える者 | 母集合 ∪ 足す者 − 外す者のうち、認証の確認を通った者（状態ファイルの `participants.available`） |
| 埋め合わせ | 使える者が 2 者に満たないときに席を埋める者（`participants.fallback`） |
| 途中の通知 | 背景の処理を残して応答を終えたサブエージェントについて、親へ届く 1 回目の通知。注記に「may be interim」と出る |
| 報告の写し | worker が `置き場所` のファイルの末尾へ書く `## 作業の報告` の節。最後の応答の報告と同じ中身 |
