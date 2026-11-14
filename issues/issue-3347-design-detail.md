# #3347 の詳細設計: 子の表・P1 の分け方・名前と番号・依存の版を、子の受け入れ条件と本文の直しへ下ろす

設計文書 [issue-3347-design.md](issue-3347-design.md) を、子の表・子へ足す受け入れ条件・Issue の本文の直し・足した帯へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。

**この文書の「実装計画」「流す順番」「レーンの割り当て」は、[issue-1142-design-next-design-detail.md](issue-1142-design-next-design-detail.md) の同じ名前の 3 つの節を置き換える。** 置き換えた節には帯を足した。#3344 の差分（PR #3353）が足す C1+ の行は、この表にも同じ中身で載せた。どちらが先にマージされても表が食い違わないようにするためである。

## #3347 の受け入れ条件との対応

| 受け入れ条件（#3347） | 解き方 | 置き場 |
| --- | --- | --- |
| P1 #3092 が P3・P4・S1 の中身を抱え、順番 4 で満たせない。旧い名前が残る | 設計の決定 98（P1a・P1b に分け、13〜17 を P3、18〜21 を P4、11・22 を S1 へ返す）・子の受け入れ条件（P1a・P1b） | 「P1 の本文の分け方」 |
| 書かれていない依存（C2・M3 → T1、M3・M7 → AD2、M1 → C3・C5 の 6 組） | 設計の決定 97（先に要るものを import の持ち主から引く。引き直すと 6 組を含めて 10 組）・不変条件 IG1・子の受け入れ条件（X1+ の `child-deps`） | 「実装計画」の「足した依存」の列 |
| T1・C4・C5・AD2 を先に要るものとして名指す子が無い | 決定 97 で引き直した表（T1: AD1・AD2・AD3・C2・M3・M4・M5、C4: M1、C5: M1・S1、AD2: M3・M7） | 「実装計画」 |
| クリティカルパスが直列で偏る（lr-rf の P1〜P4、lrebuild の順番 3〜7 の空き） | 設計の決定 99（P3 を P2 の前へ・P4 を lrebuild へ・lrebuild の空きに A・B と X1+・X2）・不変条件 IG2 | 「クリティカルパス」「レーンの割り当て」 |
| プラグイン名 `ndf-next` が中継の `ndf-next` ブロックと同じ語 | 設計の決定 100（`ndf11`。判断 (n)）・子の受け入れ条件（X1+） | 「#3347 で子へ足す受け入れ条件」 |
| I1 の当たり方が入れ替えの前後で逆転 | 設計の決定 101（照合の 2 つの集合・`FIXED_WORDS`・名前を `ndf` にした一時コピーで検査とテスト）・不変条件 IG3 | 同上 |
| 決定番号 73〜75 が 2 系統。子の記号も文書間で重なる | 設計の決定 102（系統の名前空間・新しい番号は最大の次）・不変条件 IG6・帯 | 「足した帯」 |
| 捨てた決定 27・28 が材料に生きている。3 文書が置き換わった「実装計画」を指す | 設計の決定 103（帯） | 「足した帯」 |
| 根拠の原文が git の外 | 設計の決定 104（[issue-3347-source-note.md](issue-3347-source-note.md) へコピーし、`design-next.md` の根拠の行を向ける） | 「足した帯」「承認の後に直す Issue の本文」 |
| 要求と設計の食い違い（`requirements.md:316`・`:361`・`:362`・E6） | 設計の決定 105（#1142 の本文に取り消し線。承認の後） | 「本文の取り消し線」 |
| 依存ライブラリが最新の安定版より古い（14 本） | 設計の決定 106・107・108・不変条件 IG4・IG5・子の受け入れ条件（X2） | 「#3347 で子へ足す受け入れ条件」 |

## 実装計画

**状態の列は 2026-11-14 の時点である。** 「足した依存」は、決定 97 で引き直して足した先に要るもの（理由つき）である。順番は「先に要るものの順番の最大 + 1」で求めた。

| 子 | Issue | 中身 | 先に要るもの | 足した依存（理由） | 順番 | レーン | 状態 |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| X0 | #3282 | 初期構成 | 無し | — | 1 | lrebuild | 閉じた |
| X1 | #3283 | 検査 1 本 | X0 | — | 2 | lrebuild | 閉じた |
| D1 | #3284 | 契約と読み手 | X0 | — | 2 | lr-data | 閉じた |
| C1 | #3285 | 判断のループ・DBOS の殻 | X0 | — | 2 | lr-flow | 閉じた |
| T1 | #3286 | テスト基盤 | X0 | — | 2 | lr-fix-b | 閉じた |
| X2 | 承認の後に起票 | 依存を最新の安定版へ・`plugin-deps.py`・週 1 回の検査 | X0 | — | 2 | lrebuild | 未着手 |
| D2 | #3287 | 領域の表 | D1 | — | 3 | lr-data | 閉じた |
| C1+ | #3285 の追補（#3344） | DBOS の殻の直し・DBOS 3.5.0 | C1 | — | 3 | lr-flow | 設計 PR #3353 |
| X1+ | #3283 の追補（#3347） | I1 の名前の集合・一時コピーの検査・`child-deps`・名前 `ndf11` | X1 | — | 3 | lrebuild | 未着手 |
| AD1 | #3288 | GitHub・git・CI のアダプター | D1・T1 | T1（テストの隔離と揺れない待ちを使う） | 3 | lr-fix-a | 進行中 |
| AD2 | #3289 | テキストと通知のアダプター | X0・T1 | T1（同上） | 3 | lr-data | 進行中 |
| AD3 | #3290 | LLM と外部 CLI のアダプター | D1・T1 | T1（同時実行の枠で外部 CLI を数える） | 3 | lr-fix-b | 進行中 |
| C2 | #3291 | プランの実行 | C1・C1+・D1・T1 | T1（揺れない待ちで再開を確かめる） | 4 | lr-flow | 未着手 |
| P1a | 承認の後に起票 | 工程の定義・進行表の型と保存 | D1・D2・C1+・T1 | C1+（進行表の DB の置き場と `store_lock`）・T1 | 4 | lr-rf | 未着手 |
| M3 | #3297 | 中継と hook | D2・AD2・AD3・T1 | AD2（中継のブロックを Markdown のアダプターで読む・通知）・T1 | 4 | lr-fix-b | 未着手 |
| C3 | #3292 | 行き先の選び方とプランの雛形 | C2 | — | 5 | lr-flow | 未着手 |
| C4 | #3293 | 参照の部品 | C2・AD1 | — | 5 | lr-fix-a | 未着手 |
| C5 | #3294 | worker の規則文と受け渡し | C2・AD3 | — | 5 | lr-fix-b | 未着手 |
| P1b | #3092 | `next`・`answer`・工程から作業を呼ぶ層 | P1a・C2・AD3 | AD3（`skill` の種類の作業で LLM を起動する） | 5 | lr-rf | 未着手 |
| P3 | #3097 | 計画とレーンの進行表 | P1a | P2 を外した（決定 99） | 5 | lr-rf | 未着手 |
| M2 | #3296 | リリース | P1a・AD1 | P1 を P1a に絞った（工程の定義を読むだけ） | 5 | lr-fix-a | 未着手 |
| M6 | #3301 | 棚卸し | P1a・AD1 | AD1（Issue の一覧を GitHub のアダプターで読む） | 5 | lr-data | 未着手 |
| P2 | #3295 | 承認ゲート | P1b・P3 | P3（外した課題を順番表の次の行へ入れる操作を呼ぶ） | 6 | lr-rf | 未着手 |
| M1 | #3300 | 開発ループとスプリント | P1b・M2・C3・C4・C5 | C3（止まった後の行き先）・C4（起動の外の成果の参照）・C5（worker への受け渡し） | 6 | lr-fix-a | 未着手 |
| M4 | #3298 | cross-refactoring | C3・AD3・T1 | T1 | 6 | lr-data | 未着手 |
| M5 | #3299 | cross-review | C3・AD1・AD3・T1 | T1 | 6 | lr-flow | 未着手 |
| M7 | #3302 | 記録 | P1b・P2・AD2 | AD2（用語集と確定仕様の Markdown・通知） | 7 | lr-fix-b | 未着手 |
| P4 | #3098 | 組み換えの工程 | P3・M1 | — | 7 | lrebuild | 未着手 |
| S1 | #3303 | Skill 本文・エージェント定義・hook のエントリポイント | P4・M1〜M7・C5 | C5（エージェント定義が worker の規則文を引く） | 8 | lrebuild | 未着手 |
| S2 | #3304 | 入れ替え | S1 | — | 9 | lrebuild | 未着手 |

**閉じた子の順番は記録のために残す。** 着手の可否は、先に要るものがすべて閉じたかで決まる（順番の数字は待つ相手を示すだけである）。

## 流す順番

| 順番 | 子 | 同時の本数 | 2026-11-14 の時点 |
| ---: | --- | ---: | --- |
| 1 | X0 | 1 | 済み |
| 2 | X1・D1・C1・T1・X2 | 5 | X2 だけ残り、すぐ着手できる |
| 3 | D2・C1+・X1+・AD1・AD2・AD3 | 6 | C1+ は #3353 の承認待ち。X1+ はすぐ着手できる |
| 4 | C2・P1a・M3 | 3 | C2・P1a は C1+ を待つ。M3 は AD2・AD3 を待つ |
| 5 | C3・C4・C5・P1b・P3・M2・M6 | 7 | |
| 6 | P2・M1・M4・M5 | 4 | |
| 7 | M7・P4 | 2 | |
| 8 | S1 | 1 | |
| 9 | S2 | 1 | |

## クリティカルパス

**最も長い鎖は、閉じていない子で数えて 7 段（C1+ から S2 まで。閉じた X0・C1 を含めると 9 段）で、閉じていない子の並びとしては下の 9 本である。** 「実装計画」の先に要るものを、順番が最大の先に要るものへ辿って数えた（2026-11-14）。辿ると 13 本になり、残りの 4 本は 6〜9 の C1+ の代わりに閉じた D2 から P1a へ入る形で、閉じていない子の並びは 6〜9 の後ろ 6 段と同じである。

| # | 鎖（括弧はレーン） |
| ---: | --- |
| 1 | C1+（lr-flow）→ C2（lr-flow）→ P1b（lr-rf）→ P2（lr-rf）→ M7（lr-fix-b）→ S1 → S2（lrebuild） |
| 2 | C1+（lr-flow）→ C2（lr-flow）→ P1b（lr-rf）→ M1（lr-fix-a）→ P4 → S1 → S2（lrebuild） |
| 3 | C1+（lr-flow）→ C2（lr-flow）→ C3（lr-flow）→ M1（lr-fix-a）→ P4 → S1 → S2（lrebuild） |
| 4 | C1+（lr-flow）→ C2（lr-flow）→ C4（lr-fix-a）→ M1（lr-fix-a）→ P4 → S1 → S2（lrebuild） |
| 5 | C1+（lr-flow）→ C2（lr-flow）→ C5（lr-fix-b）→ M1（lr-fix-a）→ P4 → S1 → S2（lrebuild） |
| 6 | C1+（lr-flow）→ P1a（lr-rf）→ P1b（lr-rf）→ P2（lr-rf）→ M7（lr-fix-b）→ S1 → S2（lrebuild） |
| 7 | C1+（lr-flow）→ P1a（lr-rf）→ P1b（lr-rf）→ M1（lr-fix-a）→ P4 → S1 → S2（lrebuild） |
| 8 | C1+（lr-flow）→ P1a（lr-rf）→ M2（lr-fix-a）→ M1（lr-fix-a）→ P4 → S1 → S2（lrebuild） |
| 9 | C1+（lr-flow）→ P1a（lr-rf）→ P3（lr-rf）→ P2（lr-rf）→ M7（lr-fix-b）→ S1 → S2（lrebuild） |

**1 つのレーンが続けて持つ段は 3 つまでである。** #1142 の詳細設計の前の表（[issue-1142-design-next-design-detail.md](issue-1142-design-next-design-detail.md)）では、lr-rf が P1 → P2 → P3 → P4 の 4 段を続けて持っていた。3 段が続くのは次の 3 か所で、どれも理由がある。

| レーン | 続く 3 段 | 短くしない理由 |
| --- | --- | --- |
| lrebuild | P4 → S1 → S2 | S1 は M1〜M7・P4 のすべての後、S2 は S1 の後で、どのレーンに置いても直列に並ぶ |
| lr-rf | P1a → P1b → P2・P1a → P3 → P2 | P1a・P1b・P2 は同じ進行表の型を読み書きし、別のレーンへ渡すと引継ぎの読み直しが毎回要る |
| lr-flow | C1+ → C2 → C3 | 同じ（DBOS の殻とプランの実行と雛形） |

**前の表で空いていた順番と、この表で持つもの**:

| レーン | 前の表で空いていた順番 | この表で持つもの |
| --- | --- | --- |
| lrebuild | 3〜7 | 2: X2、3: X1+、4〜6: 設計レビューの分類 A #3341・B #3342 の設計の差分（子ではない）、7: P4、8: S1、9: S2 |
| lr-rf | 1〜3 | 4: P1a、5: P1b・P3、6: P2 |

## レーンの割り当て

| レーン | 子 | after |
| --- | --- | --- |
| lrebuild | X0・X1・X2・X1+・P4・S1・S2（と A #3341・B #3342 の設計の差分・順番の監視） | 無し |
| lr-flow | C1・C1+・C2・C3・M5 | 無し |
| lr-data | D1・D2・AD2・M6・M4 | 無し |
| lr-rf | P1a・P1b・P3・P2 | 無し |
| lr-fix-a | AD1・C4・M2・M1 | 無し |
| lr-fix-b | T1・AD3・M3・C5・M7 | 無し |

**1 つのレーンが同じ順番に持つ子は 2 本以下である**（IG2。lr-rf の順番 5 の P1b・P3、lr-fix-a の順番 5 の C4・M2 が 2 本）。レーンの定義の書き直し（P4 を lrebuild へ・P1a を lr-rf へ足す）は、承認の後にオーガナイザーが `parallel.py lane p6 set`（p6 は #1142 の 6 本のレーンを持つ並列の計画の名前） で当てる。

## P1 の本文の分け方

**承認の後、#3092 の本文を P1b の形へ直し、P1a を新しく起票する。** この PR では本文を変えない。

### 受け入れ条件の行き先

| #3092 の条件 | 行き先 | 直し方 |
| --- | --- | --- |
| 1（進行表に工程表の全行が並ぶ） | P1a | `progress.py new` → `ndf progress new`（副命令 `progress` は P1a が `cli.py` の表に足す） |
| 2（行の状態と終了の印） | P1a | そのまま |
| 3（有効・無効の宣言） | P1a | `templates.CROSS_REFACTORING_SUSPENDED` → 「今の NDF の `templates.CROSS_REFACTORING_SUSPENDED` に当たるコードの定数を置かない」 |
| 4（1 つのコマンドで次の工程） | P1b | `supervise.py next <進行表>` → `ndf progress next` |
| 5（状態を変える操作） | P1a | そのまま |
| 6（工程表・PR の「通した工程」を終了の印から作る） | P1b | そのまま |
| 7（まとめの行を流す） | P1b | そのまま |
| 8（入出力の規約） | 規約と検査は P1a、出力の照合は P1b | `plugins/ndf/contracts/` → `plugins/ndf-next/contracts/workflow/`。条件を 2 行に分ける |
| 9（SQLite と PostgreSQL） | P1a | 「進行表と DBOS の記録」→「進行表」（DBOS の記録の置き場は #3344 の決定 81） |
| 10（CRUD） | P1a | そのまま |
| 11（遷移を決めるものが進行表のほかに残らない） | S1 #3303 | 取り消し線を引き「S1 #3303 へ移した（#3347 の決定 98）」。S1 の本文へ写す |
| 12（2 段と順序の決まり） | P1a | そのまま |
| 13〜17（計画とレーンの進行表） | P3 #3097 | 取り消し線を引き「P3 #3097 の条件で満たす（#3347 の決定 98）」。#3097 の本文に同じ条件がある |
| 18〜21（組み換え） | P4 #3098 | 取り消し線を引き「P4 #3098 の条件で満たす（#3347 の決定 98）」 |
| 22（工程の並びが工程表と一致） | S1 #3303 | 11 と同じ |
| 非機能の「システム環境」（DBOS は 3.1.0 の系列のまま） | P1a | 取り消し線を引き「DBOS は 3.5.0（#3344 の決定 87）。依存の版は #3347 の決定 106」 |

**作り直しで足した受け入れ条件**（#3092 の本文の「作り直しで足す受け入れ条件」）の行き先:

| 条件 | 行き先 |
| --- | --- |
| 担当のファイルとテストだけを書く・CI を通す・`plugins/ndf/` を変えない・closing keywords で閉じる | P1a・P1b の両方 |
| PostgreSQL をいつ使うかを `progress/store.py` のテストの条件に書く | P1a |
| 1 つの SQLite に 6 本のレーンが同時に書いたときの詰まりを測る | P1b（プランの実行と進行表が同時に書くのは `next` が動いてから。#3344 の差分で DBOS の記録が鍵ごとに分かれた後は、進行表の DB だけを測る） |
| 工程から作業を呼ぶ層を作る | P1b |
| `adopt` と互換を作らない | P1b |

### 取り込む旧い子と解消する Issue の行き先

| 旧い子・Issue | 行き先 | 理由 |
| --- | --- | --- |
| DC7 #3232 | P1a | 工程・フェーズ・モード・承認ゲートを表から引く（`contracts/workflow/`） |
| SF5 #3224 | P1a | 設計の工程の定義に最後の判定を置かない（工程の定義） |
| SF8 #3234 | P1b | 工程の失敗の種類から行き先を選ぶ（`run.py`） |
| SF10 #3208 | P1b | 手順 0 の確かめを 1 回の結果 JSON にまとめる（工程から作業を呼ぶ層） |
| #1516・#2935（DC7 から） | P1a | 表の値で直る |
| #2444・#2853・#2932（SF5 から） | P1b | 再現の手順が `next` で工程を流すことを要る |

### P1a の Issue の本文（起票の形）

- 題: `#1142 P1a: 工程の定義と、進行表の型と保存を作る（contracts/workflow・progress/model.py・progress/store.py）`
- ラベル: `rebuild-1142`。#1142 の sub-issue に結ぶ
- 作るファイル: `plugins/ndf-next/contracts/workflow/`・`src/ndf/progress/model.py`・`src/ndf/progress/store.py`・`tests/progress/`
- 先に要るもの: D1 #3284・D2 #3287・C1+（#3285 の追補）・T1 #3286。順番 4。レーン lr-rf
- 受け入れ条件: 上の表で P1a へ行く条件（#3092 からコピーし、旧い名前を直す）と、作り直しで足した条件
- 材料: #3092 の設計 4 本の文書と、#3344 の詳細設計の `progress/store.py` の関数の仕様

## #3347 で子へ足す受け入れ条件

### X1+（#3283 の追補。lrebuild。PR は `Refs #3283 #3347`）

- [ ] `plugin.json` 2 本の `name` を `ndf11` にする（決定 100）。ディレクトリ `plugins/ndf-next/` は動かさない
- [ ] I1 の照合を 2 つに分ける。完全一致は `plugin.json` の `name` と入れ替えの後の名前 `ndf`、パスとプレフィックス（`/<名前>`・`<名前>/`・`<名前>:`）は前の 2 つとディレクトリ名（決定 101）
- [ ] 入れ替えの後の名前 `ndf` は `identity.py` の定数 `FINAL_NAME` に置き、I1 はそこから読む。宣言の置き場 `.ndf` は `identity.py` の表 `FIXED_WORDS` に置き、I1 はこの表の定義の行だけを通す
- [ ] Python のパッケージ名 `ndf` の形（`src/ndf/` と、`ndf.` で始まるモジュール名）は照合から外す（決定 101）。パッケージ名はプラグイン名から切り離した固定の名前である
- [ ] `src/` の中で `.ndf/` を文字で書いている箇所を `identity` の表から引く形へ直す（直す前の件数と場所を PR 本文に書く）
- [ ] `.github/workflows/ndf-next.yml` に一時コピーのジョブを足す。`plugins/ndf-next/` を一時ディレクトリの `plugins/ndf/` へ写し、`plugin.json` の `name` を `ndf` にして、X1 の検査とテストを打つ。I1 の指摘の集合（プラグインの根からの相対パスと行）が元と一致し、テストが通ることを確かめる
- [ ] 詳細設計の「実装計画」を `src/ndf/checks/children.json` に写す（子ごとに `packages`（持つサブパッケージ）・`needs`・`order`・`lane`）。S2 の手順に「`children.json` と規則 `child-deps` を消す」を足す
- [ ] 検査に規則 `child-deps` を足す。子 A のサブパッケージが import する子のサブパッケージが、A の `needs` の推移の閉包に入らなければ指摘を返す（IG1）。同じレーン・同じ順番の子が 3 本以上か、`needs` の子より順番が小さい子があれば指摘を返す（IG2）
- [ ] 持ち主が `children.json` に無いサブパッケージを足した PR は、`child-deps` が指摘を返す（表を直す設計 PR を先に通す）

### X2（承認の後に起票。lrebuild。PR は `Closes #3347`）

- 題: `#1142 X2: ndf-next の依存を最新の安定版へ上げ、遅れを知らせる検査と上げる操作のスクリプトを置く`
- [ ] `plugins/ndf-next/pyproject.toml` の DBOS を除く 13 本（設計の「14 本の最新の版」の表）を上げる先の版にし（httpx は固定するため、版が変わるのは 12 本）、`uv lock` で `uv.lock` を作り直す
- [ ] 上げる先は公開から 7 日たった版に限る（決定 106）。`plugins/ndf-next/pyproject.toml` の `[tool.uv]` に `exclude-newer = "7 days"` を置き、`uv.lock` の `[options]` に `exclude-newer-span = "P7D"` が残ること、引数なしの `uv lock --check` が通ることを確かめる。`plugin-deps.py` の `target` は最初のアップロードが 7 日に満たない版を候補から外す（公開から 6 日の版と 7 日の版の 2 つの単体テスト）
- [ ] httpx は 0.28.1 に固定し、`[tool.ndf.deps.hold]` に `httpx = "githubkit 0.17.1 が httpx<1.0.0,>=0.23.0 を求める"` を書く（判断 (o)。利用者が 2026-11-14 に受け入れた）
- [ ] `scripts/plugin-deps.py` に副命令 `outdated` と `bump` を作る（下の「関数の仕様」）。結果は統一の結果 JSON で 1 行を標準出力へ出す
- [ ] `.github/workflows/plugin-deps.yml` を足す。週 1 回（と手動）で `outdated --project plugins/ndf-next` を打ち、遅れがあれば印 `<!-- plugin-deps:plugins/ndf-next -->` を持つ Issue を 1 件だけ作るか本文を書き直し、遅れが無くなれば閉じる。PR では走らせない
- [ ] `.github/workflows/ndf-next.yml` に `uv lock --check --project plugins/ndf-next` を足す（IG5）
- [ ] 上げた版で落ちる既存のコードとテストは、この PR で直す（決定 108）。直した箇所と理由を PR 本文に書く。直す箇所が動いている子の PR の触るファイルに当たるときは、その PR のマージの後に取り込み直してから入れる
- [ ] `pyproject.toml` の頭の注記に「版の選び方は #3347 の決定 106。上げるのは `scripts/plugin-deps.py bump`」を書く
- [ ] S2 の手順に「`plugin-deps.yml` の `--project` を `plugins/ndf` へ替える」を足す

### ほかの子へ足す条件

| 子 | 足す条件 |
| --- | --- |
| C1+（#3285 の追補） | DBOS を 3.5.0 へ上げる PR が X2 の後に入るなら、`uv.lock` の衝突を手で解かず `uv lock` で作り直す（決定 108） |
| P1a | 上の「P1a の Issue の本文」 |
| P1b #3092 | 上の「受け入れ条件の行き先」で P1b に残る条件だけを持つ |
| P3 #3097 | 先に要るものは P1a だけ。#3097 の条件 3 は「外した課題を順番表の次の行へ入れる操作」を作るまでとし、承認ゲートの答えから呼ぶのは P2 |
| P2 #3295 | 承認ゲートの答え「外す」で、P3 の順番表の操作を呼ぶ（#3097 の条件 3 の後半） |
| P4 #3098 | レーンを lrebuild へ移す |
| S1 #3303 | #3092 の条件 11・22 を持つ。根拠の原文は [issue-3347-source-note.md](issue-3347-source-note.md) を指す |
| M1・M3・M7・C2・M4・M5・M6・AD1・AD2・AD3 | 「実装計画」の「足した依存」のとおり、本文の先に要るものを直す |

## 本文の取り消し線

**承認の後、#1142 の本文の次の箇所を直す（取り消し線を引いて置き換えの文を添えるか、取り消し線だけを引くか、文を足す）。** 表の 316〜本文 554 の 8 行は #3347 の決定 105、232〜532 の 16 行は #3395 の決定 116 による。 `issues/issue-1142-requirements.md` は本文を直した後に `spec-copy.py write 1142 issues/issue-1142-requirements.md` で作り直す**直すのは #1142 の本文である。** 下の表の行番号は、文を探す目印としてコピー（`issues/issue-1142-requirements.md`。2026-11-14）の行番号を書いた。本文の行番号はこれより 4 小さい。コピーは設計レビューの節より前に作られたため、最後の 2 行の文はコピーに無く、本文の行番号を書いた。

表の「前提 1」は #1142 の前提 1（エントリポイント・プランの JSON・状態ファイルを変えない。2026-11-13 に「後方互換を意識しない」へ改めた）で、決定 73〜79 は系統 `next` の決定である。

| コピーの行 | 本文の節 | 取り消す文 | 置き換えの文 |
| ---: | --- | --- | --- |
| 316 | 受け入れ条件 | 「エントリポイントを起動する形（`python3 <パス>`）は変わらず」 | 「エントリポイントは `bin/ndf` の 1 本で、副命令で呼び分ける（作り直しの決定 76・X0）」 |
| 361 | 影響の「公開インタフェース」 | 「エントリポイントは変えない（前提 1）。変えるエントリポイントは設計が挙げ、移行の間は古い形も動く」 | 「エントリポイントは `bin/ndf` の副命令へ変え、古い形は残さない（前提 1 を 2026-11-13 に改めた・決定 76）」 |
| 362 | 影響の「データ」 | 「状態ファイル（…）の形は変えない。変えるなら設計が読み替えを持つ」 | 「状態の置き場と形は作り直す。読み替えは持たず、旧い置き場は退避する（決定 79・#3344 の決定 85）」 |
| 273 | ドメインイベントの E6 | 「エントリポイントの形が変わらないことを確かめた」「古いエントリポイントを残して移行ステップを直す」 | 「入れ替えの前に、新しいエントリポイントで主要な Skill が動くことを確かめた（S1 の振る舞いの確認）」「S1 の PR を直す」 |
| 354 | 非機能の条件の「移行性」 | 「途中の版を導入した利用者の手順が変わらない。どの移行ステップでも 1 つ前へ戻せる」 | 「入れ替えは 1 本の PR で、revert 1 回で戻せる（決定 73）。入れ替えの前に、今の NDF で動いているスプリントを終わらせる（決定 76）」 |
| 531 | スプリント 2c の非機能の「移行性」 | 「途中の版を導入した利用者の手順が変わらない（前提 1）」 | 「入れ替えの前に、今の NDF で動いているスプリントを終わらせる（決定 76）」 |
| —（本文 552） | 設計レビューの表の G の行 | （取り消さない） | 「設計 PR <この PR の番号> で解いた」を足す |
| —（本文 554） | 依存ライブラリの方針 | （取り消さない） | 「規則と例外は #3347 の決定 106」を足す |
| 232 | 前提の「前提 2」 | 「各スプリントは版を 1 つ出し（10.17.x）、途中の版でも利用者の手順は変わらない」 | 「子の PR は develop へマージし、利用者へ届くのは入れ替え（S2）の版 11.0.0 からである。それまで main へ出さない（#3378・#3395 の決定 122）」 |
| 247 | 対象範囲の「含まない」 | 「（エントリポイントのパスが変わるところの追随と、分岐と例外の判断をスクリプトへ移した箇所の書き換えだけを含む）」 | 「（エントリポイントのパスの追随・スクリプトへ移した分岐の書き換え・役割を conductor と worker の 2 つにする書き換えだけを含み、S1a・S1b が持つ）」 |
| 250 | 対象範囲の「含まない」 | 「ただし計測のスクリプト（`token-usage.py` ほか）の不足 f は含む」 | 「ただし計測のスクリプト（`scripts/measure/`・`scripts/token-usage.py`）を今の形と新しい形の両方で読めるようにする変更（M7）と、入れ替えで切れる `plugins/ndf` への参照の向け直し（S2）は含む」 |
| 251 | 対象範囲の「含まない」 | 「playwright-kit と mcp-* プラグインのスクリプト」 | 「playwright-kit と mcp-* プラグインのスクリプト。ただし、リリースプランがそれらの版を上げること（不足 c、M2）と、入れ替えでマーケットプレイスの定義を直すこと（S2）は含む」 |
| 260 | 進め方の 3 | 「設計の順に、触るファイルが重ならない単位で並列に移す（6 本まで）。既存のテストで振る舞いが変わらないことを確かめる」 | 「`plugins/ndf-next/` に子 Issue を流す順番で作り、触るファイルが重ならない子を 6 本のレーンで並べる。振る舞いは子ごとの新しいテストと、入れ替えの前の試走（T2）で確かめる」 |
| 274 | ドメインイベントの E7 | 「開発版を導入して確かめた」「各スプリントの開発版」「導入の不一致はリリースプランを直す」 | 「入れ替えの前に、`plugins/ndf-next/` を `--plugin-dir` で読む試走で確かめた」「T2 の試走」「不一致は持ち主の子の追補で直す」 |
| 363 | 影響の「既存の振る舞い」 | 「不足 a〜f のエントリポイントと記録が増える。ほかは変わらない」 | 「エントリポイント・状態・結果 JSON は作り直しの形へ変わる（前提 1 を 2026-11-13 に改めた）。今の機能の行き先は X3 の対応表が持つ」 |
| 387 | 境界の「常に行う」 | 「既存のテストの実行、リリース対象の同期、移行ステップごとの PR」 | 「子ごとの新しいテストの実行（`.github/workflows/ndf-next.yml`）、子ごとの PR。リリース対象の同期は入れ替え（S2）から行う」 |
| 389 | 境界の「行わない」 | 「Skill の本文の書き直し（エントリポイントの追随を除く）」 | 「Skill の本文の書き直し（対象範囲の「含まない」の 247 行が挙げる書き換えを除く）」 |
| 496 | スプリント 2c の対象範囲の「含まない」 | 「プランの JSON の形・テンプレートの中身（ステップの並び）の変更」 | 取り消し線だけを引く（作り直しで形を変える。前提 1 を 2026-11-13 に改めた） |
| 497 | スプリント 2c の対象範囲の「含まない」 | 「`supervise.py` の副命令の追加・削除」 | 取り消し線だけを引く（エントリポイントは `bin/ndf` の副命令になる。決定 76） |
| 509〜512 | スプリント 2c の受け入れ条件 C-3a〜C-3d | 4 行の全文 | 取り消し線だけを引く（作らない行。[issue-1142-design-review-routing.md](issue-1142-design-review-routing.md) の「作らない要求の行」） |
| 522 | スプリント 2c の受け入れ条件 C-7 | 「`dbos` が `plugins/ndf/pyproject.toml` の 1 つの extra と `uv.lock` に版を固定して載り、`python3 plugins/ndf/scripts/supervise.py ...` の起動の形は変わらない」 | 「`dbos` が `plugins/ndf-next/pyproject.toml` の依存の一覧と `uv.lock` に版を固定して載り、起動は `bin/ndf` の副命令で行う（決定 76・78）」 |
| 305 | 受け入れ条件の「構造」 | 「500 行を超えるファイルが無い」 | 「1,000 行を超えるファイルが無い（X1 の `checks/rules.json` の `structure.max_lines`。ファイルを分ける基準は責務で、行数は上限として残す。#2991）」 |
| 355 | 非機能の条件の「システム環境」 | 「Python 3.10 以上・bash 3.2（macOS 既定）で動く」 | 「Linux の Python 3.11 以上で動く（3.10 は 2026-10-01 に保守を終えた。macOS で確かめることは #3388）」 |
| 532 | スプリント 2c の非機能の「システム環境」 | 「Python 3.10 以上」 | 「Python 3.11 以上」 |

**#3092 の本文**: 非機能の「システム環境」の「DBOS は 3.1.0 の系列のまま据え置く（前提 7）」に取り消し線を引く（前提 7 は #3092 の、今の DBOS の版と依存のまま作る前提）（置き換えの文は「受け入れ条件の行き先」の表の最後の行）。

## 承認の後に直す Issue の本文

| Issue | 直すこと |
| --- | --- |
| #3092 | 「P1 の本文の分け方」のとおり P1b の形へ直す。取り消し線を引いた条件に行き先を添える |
| 新しい P1a | 起票する（「P1a の Issue の本文」） |
| 新しい X2 | 起票する（「X2」の題と受け入れ条件）。ラベル `rebuild-1142` |
| #3283（X1） | 閉じたまま、コメントで X1+ の受け入れ条件を書く（C1+ と同じ扱い） |
| #3097・#3295・#3098・#3303・#3300・#3297・#3302・#3291・#3298・#3299・#3301 | 「先に要るもの・流す順番・レーン」の節を「実装計画」の行に合わせる |
| #3303・#3290 | 根拠の原文への参照（`.ndf/handoff/lrebuild-note-rebuild-1142-label.md`）を [issue-3347-source-note.md](issue-3347-source-note.md) の節「ndf-next を試す起動の手順」へ向ける |
| #1142 | 「本文の取り消し線」の 8 か所。設計レビューの表の G の行に設計 PR を添える |

## ファイル配置

触るファイルは設計文書の「設計の結果」の行と同じである。子の PR ごとの分け方は「#3347 で子へ足す受け入れ条件」にある。

| パス | 種類 | 役割 1 文 | 作る子 |
| --- | --- | --- | --- |
| `scripts/plugin-deps.py` | 新設 | 副命令 `outdated` と `bump`（決定 107） | X2 |
| `scripts/tests/test_plugin_deps.py` | 新設 | `plugin-deps.py` の単体テスト（PyPI の応答は記録した JSON） | X2 |
| `.github/workflows/plugin-deps.yml` | 新設 | 週 1 回 `outdated` を打ち、遅れの Issue を 1 件だけ作るか書き直す | X2 |
| `.github/workflows/ndf-next.yml` | 変更 | 一時コピーの検査（X1+）と `uv lock --check`（X2）のジョブを足す | X1+・X2 |
| `plugins/ndf-next/pyproject.toml` | 変更 | 上げる先の版と `[tool.ndf.deps.hold]` | X2 |
| `plugins/ndf-next/uv.lock` | 変更 | `pyproject.toml` から `uv lock` で作り直す | X2 |
| `plugins/ndf-next/.claude-plugin/plugin.json` | 変更 | `name` を `ndf11` にする（決定 100） | X1+ |
| `plugins/ndf-next/.codex-plugin/plugin.json` | 変更 | 同上 | X1+ |
| `plugins/ndf-next/src/ndf/identity.py` | 変更 | 表 `FIXED_WORDS` を置く（決定 101） | X1+ |
| `plugins/ndf-next/src/ndf/checks/invariants.py` | 変更 | I1 の名前の集合を広げる（決定 101） | X1+ |
| `plugins/ndf-next/src/ndf/checks/children.json` | 新設 | 子の表の写し（決定 97） | X1+ |
| `plugins/ndf-next/src/ndf/checks/child_deps.py` | 新設 | 規則 `child-deps`（IG1・IG2） | X1+ |
| `plugins/ndf-next/tests/checks/test_child_deps.py` | 新設 | `child_deps` の単体テスト | X1+ |
| `plugins/ndf-next/tests/checks/test_invariants.py` | 変更 | I1 の名前の集合の単体テスト | X1+ |

## クラスと関数の構造

| 置き場 | モジュール | 最上位の関数 |
| --- | --- | --- |
| `scripts/plugin-deps.py` | `plugin-deps` | `target`・`fetch`・`outdated`・`bump`（新設） |
| `plugins/ndf-next/src/ndf/checks/child_deps.py` | `checks.child_deps` | `load_children`・`owner`・`closure`・`check`（新設） |
| `plugins/ndf-next/src/ndf/checks/invariants.py` | `checks.invariants` | I1 の照合の名前の集合（変更） |

## 関数の仕様

### `scripts/plugin-deps.py`（X2）

| 関数 | 種類 | 入力 | 出力 | 副作用 | 前提と後条件 |
| --- | --- | --- | --- | --- | --- |
| `target(releases, now)` | 純粋な関数 | 版 → 最初のアップロードの日時の辞書・今の時刻 | 上げる先の版の文字列 | 無し | 決定 106 の表の上 4 行。`STALE_DAYS = 365`。最初のアップロードが `now` から `COOLDOWN_DAYS = 7` 日に満たない版・ファイルの無い版・PEP 440 で読めない版は数えない |
| `fetch(name)` | 外部呼び出し | パッケージ名 | `releases` の辞書 | PyPI の JSON API を 1 回読む | 失敗は `warnings` に載せ、その行を「分からない」にする |
| `outdated(project, now)` | 外部呼び出し | プロジェクトのディレクトリ | 行ごとに `name`・`current`・`target`・`state`（`latest`・`behind`・`held`・`unknown`）・`hold_reason` | 無し | `held` は `[tool.ndf.deps.hold]` に名前がある行。固定させている相手の制約を外した版で `uv lock --dry-run` が解決できたら `behind` にする |
| `bump(project, names)` | 書き込み | プロジェクト・名前の列（空なら `behind` のすべて） | 上げた行と、解決できずに戻した行 | `pyproject.toml` の `==` の版を書き換え、`uv lock` を打つ | `uv lock` が失敗したら `pyproject.toml` と `uv.lock` を元へ戻し、`uv` の出力から固定させている相手を結果に載せる |

### `checks/child_deps.py`（X1+）

| 関数 | 種類 | 入力 | 出力 | 副作用 | 前提と後条件 |
| --- | --- | --- | --- | --- | --- |
| `load_children(path)` | 読み取り | `children.json` | 子の表 | 無し | 形が違えば `CheckError` |
| `owner(module, children)` | 純粋な関数 | `ndf.<…>` のモジュール名 | 持ち主の子か `None` | 無し | 最も長く一致した `packages` の子 |
| `closure(child, children)` | 純粋な関数 | 子 | `needs` の推移の閉包 | 無し | 輪があれば `CheckError` |
| `check(scope, children)` | 純粋な関数 | 検査（`ndf check`）が作る構文木の集まり | `Finding` の列 | 無し | IG1（import の向き）・IG2（順番とレーン）・持ち主の無いサブパッケージ |

## 異常系の処理

| 関数 | 異常 | 扱い |
| --- | --- | --- |
| `fetch(name)` | PyPI に届かない | その行を `unknown` にし、`warnings` に載せる。週 1 回のジョブは Issue を書かずに終える |
| `bump(project, names)` | `uv lock` が解決できない | 宣言と lock を元へ戻し、終了コード 1。結果に固定させている相手を載せる |
| 週 1 回のジョブ | 印を持つ Issue が 2 件以上ある | 最も古い 1 件を書き直し、ほかを閉じる |
| 一時コピーのジョブ | I1 の指摘の集合が元と違う | 差分（元だけ・コピーだけ）を出して落ちる |
| `child-deps` | `children.json` が無い・形が違う | 検査を止める（止まった理由 `config_broken`） |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `target(releases, now)` | 最新の安定版が 365 日以内（ちょうど 365 日を含む） | 最新の安定版 |
| `target(releases, now)` | 最新の安定版が 365 日より前で、より新しいプレリリースがある | 最新（プレリリースを含む） |
| `target(releases, now)` | 最新の安定版が 365 日より前で、より新しいプレリリースが無い | 最新の安定版 |
| `target(releases, now)` | 安定版が 1 つも無い | 最新 |
| `outdated(project, now)` | `[tool.ndf.deps.hold]` に名前があり、固定させている相手の制約がまだある | `held` |
| `outdated(project, now)` | 今の版が上げる先と同じ | `latest` |
| `outdated(project, now)` | 今の版が上げる先より古い | `behind` |
| `outdated(project, now)` | `fetch` が失敗した | `unknown` |

- 繰り返しの終わる条件: 宣言の行を 1 回ずつ読み終えたとき
- 計算量の注意: PyPI の JSON API を宣言の行の数（今は 24 本）だけ読む。週 1 回のジョブだけが打つ

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `STALE_DAYS` | 定数 | `scripts/plugin-deps.py` | `365`（決定 106・判断 (r)） |
| `COOLDOWN_DAYS` | 定数 | `scripts/plugin-deps.py` | `7`（決定 106。利用者の決定 2026-11-14） |
| `[tool.uv] exclude-newer` | 設定値 | `plugins/ndf-next/pyproject.toml` | `"7 days"`（決定 106。`COOLDOWN_DAYS` と同じ値） |
| `state` | 型（文字列の列挙） | `scripts/plugin-deps.py` の結果 JSON | `latest`・`behind`・`held`・`unknown` |
| `[tool.ndf.deps.hold]` | 設定値 | `plugins/ndf-next/pyproject.toml` | `httpx = "githubkit 0.17.1 の httpx<1.0.0"`（決定 106） |
| Issue の印 | 定数 | `.github/workflows/plugin-deps.yml` | `<!-- plugin-deps:<dir> -->`（決定 107） |
| 子の表の写し | 型 | `plugins/ndf-next/src/ndf/checks/children.json` | 子ごとに `packages`・`needs`・`order`・`lane` |
| `FIXED_WORDS` | 定数 | `plugins/ndf-next/src/ndf/identity.py` | `.ndf` の 1 語（決定 101） |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 週 1 回のジョブの 2 回の実行 | 印を持つ遅れの Issue | 印で 1 件を探して書き直す。2 件以上あれば最も古い 1 件を残す（「異常系の処理」） | 同じ遅れなら同じ本文になる。途中で止まっても次の実行が書き直す |
| X2 の PR と C1+ の PR | `pyproject.toml`・`uv.lock` | 衝突した `uv.lock` を手で解かず、取り込んだ宣言から `uv lock` で作り直す（決定 108） | `uv lock` は同じ宣言から同じ lock を作る |
| `bump` と動いている子の PR | 上げた版で直す既存のファイル | 直す箇所が子の PR の触るファイルに当たるときは、その PR のマージの後に取り込み直してから入れる（決定 108） | `bump` は解決できなければ宣言と lock を元へ戻す |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `outdated(project, now)` | 行ごとの `state` と上げる先、取得できなかった行の `warnings` | 結果 JSON を標準出力へ 1 行 | 秘密（PyPI は認証なしで読む） |
| `bump(project, names)` | 上げた行と、戻した行の固定させている相手 | 結果 JSON を標準出力へ 1 行 | `uv` の出力の全文（相手の名前だけを載せる） |
| 週 1 回のジョブ | 遅れの一覧 | 印を持つ Issue の本文 | `GITHUB_TOKEN` |
| `check(scope, children)` | `child-deps` の指摘 | 検査（`ndf check`）の `Finding` | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `target(releases, now)` | `packaging.version.Version.is_prerelease`（STD20） | PEP 440 と packaging の文書 | 使う。安定版の判定を自分で書かない |
| `outdated(project, now)` | `uv tree --outdated`（LIB21）・Dependabot（PRD20） | 外部の調査（決定 107） | 使えない。安定版が古い依存を遅れとして出さない |
| `bump(project, names)` | `uv lock` | 外部の調査（LIB21） | 使う。lock を自分で書かない |
| `check(scope, children)` | 検査（X1 の `ndf check`）の構文木 | `plugins/ndf-next/src/ndf/checks/` | 使う。構文木を作り直さない（決定 97） |

## 足した帯

**この PR が #1142 の既存の文書に足したのは、次の帯と 1 行の向け直しだけである。** 中身は変えていない。#3344・#3345 の差分（PR #3353・#3355）が書き換える行とは離した。

| 文書 | 場所 | 帯の中身 |
| --- | --- | --- |
| [issue-1142-design-next.md](issue-1142-design-next.md) | 「あるべき姿の根拠」の 1 行目 | 原文の置き場を [issue-3347-source-note.md](issue-3347-source-note.md) へ向けた |
| 同上 | 決定 73 の後 | 決定 73〜75 は系統 `next` で、`fixes` の 73〜75 と別。I1 と子の表は #3347 の差分が上書きする |
| [issue-1142-design-next-design-detail.md](issue-1142-design-next-design-detail.md) | 頭 | 「実装計画」「流す順番」「レーンの割り当て」はこの文書が置き換える |
| [issue-1142-design-fixes.md](issue-1142-design-fixes.md) | 決定 73 の前 | 決定 73〜75 は系統 `fixes` で、`next` の 73〜75 と別 |
| [issue-1142-design-decisions.md](issue-1142-design-decisions.md) | 頭 | 系統 `decisions`。決定 65 は `scripted-flow` の 65 と別。記号は前の企画のもの |
| [issue-1142-design-scripted-flow.md](issue-1142-design-scripted-flow.md) | 決定 65 の見出しの後 | 系統 `scripted-flow` の 65 で、`decisions` の 65 と別 |
| [issue-1142-design-dbos.md](issue-1142-design-dbos.md) | 頭 | I18・「承認ゲート」の行（`--from`）・決定 27 は作り直しで採らない。決定 28 の扱いは #3344 の差分。記号は前の企画のもの |
| 同上 | 決定 27 の見出しの後 | 作り直しでは採らない（決定 77（next）・#3092 の決定 7） |
| [issue-1142-design-data.md](issue-1142-design-data.md) | 規則の表（R4）の後・表の形の表（`version`）の後 | 「決定 28 と同じく」の決定 28 は系統 `dbos` のもので、作り直しでの扱いは #3344 の差分 |
| [issue-1142-design-data-design-detail.md](issue-1142-design-data-design-detail.md) | `VERSION` の行を持つ表の後 | 同上 |
| [issue-1142-design-base-design-detail.md](issue-1142-design-base-design-detail.md)・[issue-1142-design-fixes-design-detail.md](issue-1142-design-fixes-design-detail.md)・[issue-1142-design-labels.md](issue-1142-design-labels.md) | 頭の段落の後 | 指している「実装計画」は置き換わった。今の表は [issue-3347-design-detail.md](issue-3347-design-detail.md) |
| [issue-1142-design-libraries.md](issue-1142-design-libraries.md) | 頭 | 依存の版の選び方は #3347 の決定 106。記号は前の企画のもの |
| [issue-1142-design-migration.md](issue-1142-design-migration.md)・[issue-1142-design-modules.md](issue-1142-design-modules.md) | 頭 | 記号（X1・S1・C1〜C5・D1・D2）は前の企画のもので、作り直しの子とは別 |

## 単体テストの観点

| 対象 | 観点 |
| --- | --- |
| `plugin-deps.target` | 上げる先の 4 つの場合（安定版が新しい・古くてプレリリースあり・古くてプレリリースなし・安定版なし）と境界の 365 日ちょうど（「以内」に入る）で上げる先が決まる |
| `plugin-deps.outdated` | `hold` の行が `held` になり、固定させている相手の制約が外れた記録の応答では `behind` になる |
| `plugin-deps.bump` | 解決できない上げ方で `pyproject.toml` と `uv.lock` が元のまま残る |
| `child_deps.check` | 閉包の外の import・持ち主の無いサブパッケージ・同じレーンと順番に 3 本・順番の逆転を、それぞれ指摘する。閉包の中の推移の import は指摘しない |
| I1 | 名前を `ndf11` にした後、`"ndf-next"` の文字列は落ちず、`".ndf/"`・`"/ndf:"`・`"ndf11"` は落ちる。`FIXED_WORDS` の定義の行だけは落ちない |
