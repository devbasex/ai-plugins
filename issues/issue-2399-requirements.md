# #2399: Agent の ndf:supervisor が worker の起動指示に「読む Skill」を置かず、設計の工程の ndf:worker に document-style が届かない

正は課題の本文（#2399）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

ndf v10.27.10 を導入した状態（`claude -p --plugin-dir <導入先の 10.27.10>`）で、conductor の役から Agent の `ndf:supervisor` を設計のフェーズ（通す工程は「設計」だけ）で起動し、設計文書 1 つを `ndf:worker` に書かせた。2 回起動し、2 回とも次のとおりだった。

| 起動 | 起動指示の形 | supervisor が `stage-skills.py lookup 設計` を打ったか | worker の起動指示の「読む Skill」の欄 | worker が `document-style` の `SKILL.md` を読んだか |
| --- | --- | --- | --- | --- |
| 1 回目（2026-11-02 15:33 JST） | 依頼の文だけ（10 項目の形でない） | 打っていない | 無い | 読んでいない |
| 2 回目（2026-11-02 15:35 JST） | `agent-layers.md` の「conductor → supervisor」の 10 項目と守る規則 14 個、`$SCRIPTS` の絶対パス | 打っていない | 無い | 読んでいない |

supervisor は `agent-layers.md` を読まずに worker の起動指示を自分で組んだ。worker の起動指示は「作業・置き場所・形の制約・報告」だけで、6 項目の形（作業・入力・手順・読む Skill・返す形・置き場所）になっていなかった。

一方、プランの worker（`supervise.py run`）は同じ版で、工程の対応表どおりの Skill を受け取った（5 ステップを実起動し、`progress.jsonl` の `kind: skills` の行と答えが一致した）。

## どこで見つけたか

- #2376 のリリース後テスト（配布の記録は Pull Request #2395、スプリントの Pull Request は #2392）
- 手順の正本: `plugins/ndf/skills/development-workflow/references/agent-layers.md` の「supervisor → worker」の「読む Skill」の行
- supervisor の定義: `plugins/ndf/agents/supervisor.md`（「規約の正本は `references/agent-layers.md` の『conductor → supervisor』にある」とだけ書き、supervisor → worker の形を読む指示が無い）。conductor の起動指示の守る規則 14 個にも、worker の起動指示の形と `stage-skills.py` は入っていない

## なぜこの変更の範囲外なのか

#2376 はリリース済みで、リリース後の不合格は新しい変更として通常の工程で直す（`release-verification` の「不合格を即時修正しない」）。原因は supervisor が手順の正本を読む経路が無いことで、直す場所（supervisor の定義か conductor の起動指示の規則）と直し方は設計を要し、即時修正の 4 条件のうち「原因が分かっている」以外を確かめていない。根拠: Value 1・Value 6（MVV 版 4）

## 直さないと何が起きるか

Agent の 3 層で回すフェーズ（プランの雛形が無いフェーズ）では、設計・Pull Request・振り返りなど文書を書く工程の `ndf:worker` に `document-style` の規則が届かない。#2376 で `worker.md` と `agent-layers.md` から `document-style` を名指す文を外したため、#2372 の止血の前と同じ状態に戻っている（#2376 の受け入れ条件 13 の「退行しない」に反する）。プランの worker は影響を受けない。

## 由来

issue #2376

## 依頼（原文）

> supervisor は `agent-layers.md` を読まずに worker の起動指示を自分で組んだ。worker の起動指示は「作業・置き場所・形の制約・報告」だけで、6 項目の形（作業・入力・手順・読む Skill・返す形・置き場所）になっていなかった。

> Agent の 3 層で回すフェーズ（プランの雛形が無いフェーズ）では、設計・Pull Request・振り返りなど文書を書く工程の `ndf:worker` に `document-style` の規則が届かない。#2376 で `worker.md` と `agent-layers.md` から `document-style` を名指す文を外したため、#2372 の止血の前と同じ状態に戻っている（#2376 の受け入れ条件 13 の「退行しない」に反する）。プランの worker は影響を受けない。

（書き手: 起票者（#2376 のリリース後テストを行った AI）、日付: 2026-11-02。上の「何を見つけたか」〜「由来」の節が原文である（原文の「起票者の推定」の 2 行は、下の表の S1・S2 に写した）。利用者の指示の原文は含まない）

> 人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-11-02）

## 起票者の推定

承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 原因は、supervisor が `agent-layers.md` の「supervisor → worker」の節を読む経路が無いことである | 2 回の実起動の記録で、supervisor の Tool の呼び出しに `agent-layers.md` の Read と `stage-skills.py` が無い。`plugins/ndf/agents/supervisor.md` の本文 | 前提 1。受け入れ条件は原因を問わず、起動指示と worker の振る舞いで判定する（受け入れ条件 1〜4） |
| S2 | 直す場所は、supervisor の定義か conductor の起動指示の規則のどちらかである | S1 の推定 | 採らない（場所を 2 つに絞らない）。直す場所は設計で決める（未決 1） |
| S3 | 2 回の実起動の結果から、起動指示の形（依頼の文だけ / 10 項目の形）に依らず再現する | 本文「何を見つけたか」の表 | 受け入れ条件 1（両方の形で確かめる） |

## 目的

- Agent の 3 層で回すフェーズで、supervisor が起動する `ndf:worker` に、いま通している工程の規則の Skill（Skill の対応表が挙げるもの）が届く状態に戻す。conductor が起動指示をどの形で書いても届く
- #2376 の受け入れ条件 13（設計・ドキュメント再構成・Pull Request・振り返りの工程の worker に `document-style` の規則が届く）を、Agent の 3 層でも満たす

## 前提

- 前提 1: 原因は S1 のとおり、supervisor が worker の起動指示の形（6 項目と「読む Skill」の決め方）を受け取る経路を持たないことである。設計の段階で実起動の記録を読み直し、この前提が外れたら設計 PR で示す
- 前提 2: プランの worker（`supervise.py` が起動する worker）への届け方と、Skill の対応表（`plugins/ndf/scripts/lib/stage-skills.json`）の中身はこの課題で変えない
- 前提 3: 「読む Skill」の欄の決め方（`stage-skills.py lookup <工程>` を打ち、出力の名前とパスを並べる）と、worker の規則 6（挙がった `SKILL.md` だけを作業の前に読む）は、`agent-layers.md` の今の規約のまま使う。変えるのは、supervisor がその規約に従う経路である
- 前提 4: 実起動での確かめは、2 回の再現と同じ条件（`claude -p --plugin-dir <導入先>` で conductor の役を起動し、設計のフェーズの supervisor に設計文書 1 つを worker へ書かせる）で行う。言語モデルの振る舞いは起動ごとに揺れるため、起動指示の 2 つの形でそれぞれ 2 回、計 4 回を確かめの回数とする

## 対象範囲

含む:
- supervisor が `ndf:worker` の起動指示を組むときに、6 項目の形と「読む Skill」の決め方を受け取る経路（supervisor の定義・conductor の起動指示の規則・機械の検査のどれに置くかは設計で決める）
- `ndf:supervisor` と `ndf:supervisor-waits` の両方の定義（本文は同じに保つ）
- 起動指示の「読む Skill」の欄が無い `ndf:worker` の起動を、機械で検出して止めるかどうかの判断（未決 2）

含まない:
- Skill の対応表の行の追加・削除（工程と Skill の組の変更）
- プランの worker・PR 本文の書き手への届け方
- `ndf:worker` の定義の変更（規則 6 と「読む Skill」だけを読む振る舞いは #2376 のまま）
- supervisor の起動指示のほかの項目（10 項目・守る規則 14 個）の見直し。6 項目の形のうち「読む Skill」以外の欄の欠け（本文「何を見つけたか」の「入力」「手順」の欠け）は、同じ経路で直るかを設計で確かめ、直らなければ別の課題へ起こす

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | conductor が supervisor を起動した | フェーズの開始 | 起動指示が 10 項目の形でない（依頼の文だけ）。この場合も E3 以降が成り立つこと（受け入れ条件 1） | — |
| E2 | supervisor が worker の起動指示の形を得た | supervisor の起動（定義・起動指示・検査のどれで得るかは設計） | 形を得られないまま worker を起動する。これが今の不具合である | E1 |
| E3 | supervisor が `stage-skills.py lookup <工程>` を打った | 工程の中で worker を起動する直前 | 終了コードが 0 以外なら、欄を空にせず `結果: 判断が要る` で返す（今の規約） | E2。工程の名前は起動指示の「フェーズ」から取る |
| E4 | supervisor が「読む Skill」の欄つきで `ndf:worker` を起動した | E3 の出力 | 欄の無い起動指示で起動した。機械で検出して止めるかは未決 2 | E3 |
| E5 | worker が挙がった `SKILL.md` を読んだ | 作業の開始 | パスのファイルが無いときは `結果: 判断が要る` で返す（今の規約） | E4 |
| E6 | worker が規則に従って文書を書き、作業の報告を返した | E5 | — | E5 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 読む Skill | supervisor が `ndf:worker` の起動指示に置く欄。作業の前に読む規則の Skill の名前と `SKILL.md` の絶対パスを並べる |
| Skill の対応表 | 工程の名前から、その工程の worker に届ける規則の Skill の一覧を引く表 |

## 受け入れ条件

- [ ] 1. conductor の役が設計のフェーズ（通す工程は「設計」だけ）の `ndf:supervisor` を起動し、設計文書 1 つを `ndf:worker` に書かせたとき、`ndf:worker` を起動する Agent の呼び出しの `prompt` に「読む Skill」の欄があり、`document-style` の名前と、`stage-skills.py lookup 設計` が返すのと同じ `SKILL.md` の絶対パスが並ぶ。conductor の起動指示が依頼の文だけのときと、`agent-layers.md` の 10 項目の形のときの両方で成り立つ
- [ ] 2. 受け入れ条件 1 の起動で、`ndf:worker` が作業の前に、欄に挙がった `document-style` の `SKILL.md` を Read する（実起動の記録の Tool の呼び出しで確かめる）
- [ ] 3. 受け入れ条件 1 の起動で、supervisor の Tool の呼び出しに `stage-skills.py lookup 設計` の実行がある。「読む Skill」の名前とパスを、対応表を引かずに書いていない
- [ ] 4. Skill の対応表が空を返す工程（例: 作業場所の用意）と、工程に入る前の作業の `ndf:worker` の起動指示では、「読む Skill」の欄が「無し」である（欄そのものは省かれない）
- [ ] 5. `ndf:supervisor` と `ndf:supervisor-waits` の定義の本文は、frontmatter を除いて同じである（退行しない）
- [ ] 6. 工程と Skill の組（`document-style` などの規則の Skill の名前と工程の名前の組）は、Skill の対応表のほかに増えない。`grep -rn "document-style" plugins/ndf/agents plugins/ndf/skills/development-workflow/references/agent-layers.md` の当たりが 0 件のままである（2026-11-02 の `develop` で 0 件）
- [ ] 7. プランの worker への届け方は変わらない。`plugins/ndf/scripts/tests` の既存テストがすべて通る（退行しない）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | supervisor の起動 1 回あたりに増える費用と秒を、設計 PR で実測値として示す。上限は Skill の対応表の増分の上限（起動 1 回あたり費用 $0.10・秒 +2 秒。`docs/specifications/ndf-worker-stage-skills.md`）と同じ値を置き、超えるときは設計で示して承認ゲート 1 で決める |
| 運用・保守性 | 「読む Skill」の決め方の正本は `agent-layers.md` の「supervisor → worker」の 1 か所のままにし、supervisor の定義や conductor の規則へ同じ規約を写さない（Value 7「正本は 1 か所に置き、写さずに指す」）。写さずに経路を作れないときは、設計で理由を示す |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（Agent の定義名・Skill の対応表・`stage-skills.py` の入出力は今のまま） |
| データ | 無し |
| 既存の振る舞い | Agent の 3 層の supervisor が `ndf:worker` を起動するときの起動指示の中身が変わる。機械の検査を足す場合（未決 2）は、「読む Skill」の欄の無い `ndf:worker` の起動が止まる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`。機械の検査を足す場合は、その検査の単体テストを足す |
| 静的解析 | `claude plugin validate .`、`python3 scripts/check-skill-frontmatter.py`、`python3 plugins/ndf/scripts/instructions-check.py --root .` |
| 受け入れ条件 5・6 | `diff <(awk 'f>=2{print} /^---$/{f++}' plugins/ndf/agents/supervisor.md) <(awk 'f>=2{print} /^---$/{f++}' plugins/ndf/agents/supervisor-waits.md)` の終了コードが 0。受け入れ条件 6 の `grep` が 0 件（2026-11-02 の `develop` で 0 件） |
| 手動確認 | 受け入れ条件 1〜4 は、前提 4 の条件で `claude -p` の実起動を起動指示の 2 つの形で 2 回ずつ行い、実起動の記録（stream-json）の Agent の呼び出しの `prompt` と Tool の呼び出しで判定する。リリース後テストで確かめる |
| 手動確認（マージ前） | 受け入れ条件 1・2 を、実装の worktree のプラグインのディレクトリ（`--plugin-dir`）で 1 回ずつ確かめる（リリース前に退行の有無を知るため） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「マーケットプレイスの構造」。Agent の定義は `plugins/ndf/agents/`、3 層の規約は `development-workflow` の `references/agent-layers.md` |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`.md` の文言を照合するテストは書かない |
| テスト戦略 | 振る舞いは実起動の記録で確かめる（手動確認）。機械の検査を足す場合は、その判定を単体テストで確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`supervisor.md` と `supervisor-waits.md` の本文を同じに保つ |
| 確認してから行う | hook の判定を足す・変える（承認ゲート 1・2 で人が見る） |
| 行わない | Skill の対応表の行の変更、プランの worker への届け方の変更、`ndf:worker` の定義の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. supervisor が worker の起動指示の形を得る経路をどこに置くか（supervisor の定義から正本の節を読ませる・conductor の起動指示の規則に足す・起動時に正本の節を渡す など） | 設計（`design`）。承認ゲート 1 で利用者が確かめる | 設計 PR |
| 2. 「読む Skill」の欄の無い `ndf:worker` の起動を、hook などの機械の検査で止めるか（Value 4「決まった手順はスクリプトに」と、検査が誤って止める危険の比較） | 設計（`design`）。承認ゲート 1 で利用者が確かめる | 設計 PR |
