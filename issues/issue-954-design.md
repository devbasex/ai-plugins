# #954: supervisor が長い待ちの後に文脈の全体を書き直す — 設計

**この文書は「どう作るか」だけを扱う。** 他の 3 つは次のとおりである。

| 文書 | 中身 |
| --- | --- |
| [issue-954-requirements.md](issue-954-requirements.md) | 要求と受け入れ条件 |
| [issue-954-design-decisions.md](issue-954-design-decisions.md) | 決定の理由と、候補の数値の比較 |
| [issue-954-design-tests.md](issue-954-design-tests.md) | テスト設計と未確認 |

## 例: 設計の持ち場を、変えた後の形で通すと

1. conductor が `設計: #954` を **`ndf:supervisor`**（寿命 5 分）で起動する
2. supervisor が要求・設計・再構成を通し、設計 Pull Request を出す。文脈は 30 万を超えている
3. supervisor が `cross-review` を起動しようとする。hook が自分の記録を読み、今の文脈が最初の
   呼び出しの文脈の 2.5 倍以上なので 1 度止める。理由の欄に区切りの返し方が出る
4. supervisor が `結果: 区切り`・`次の工程: ドキュメントレビュー` で返す
5. conductor が同じ `設計: #954` を **`ndf:supervisor-waits`**（寿命 1 時間）で起動し直す。
   起動指示の「前の持ち場の報告」に 4 の報告を入れる
6. 新しい supervisor は前提（約 43k）から `cross-review` を回す。ラウンドの待ちが 60 分以内なら、
   起きたときの文脈は読み込みで済む

## 機能一覧

| # | 機能 | 誰が使うか | 候補 |
| --- | --- | --- | --- |
| F1 | supervisor の定義を 2 つにし、待ちを抱える区間だけ寿命を 1 時間にする | conductor（起動） | 3 |
| F2 | 収束ループの前で持ち場を区切り、新しい supervisor に続けさせる | supervisor（返す）・conductor（起動し直す） | 2 |
| F3 | 区切りの条件を hook が守らせる | Claude Code | 2 |
| F4 | 待ちの後の書き直しの量を集計する | 測定（AC7〜AC9） | — |
| F5 | 待ちを drive が持ち、判断の地点で新しい文脈を起こす契約 | #870・#827 の実装 | 1 |

F1〜F4 を #954 で実装する。F5 は契約だけをこの設計に置き、実装は #870・#827 が持つ（決定 5）。

## 基準の実測

`docs/metrics/ndf-token-usage/2026-09-24.json` の `per_role` から、10.16.0 以降の supervisor を
持ち場ごとに足した。量は入力のトークン数（換算前）。「待ちの後」は直前の間隔が 5 分を超えた
書き直しで、量は書き直しの量を回数の比で按分した推定である（集計に量の列が無い。AC6 で足す）。

| 持ち場 | 起動 | 書き直し | うち待ちの後 | 待ちの後の量 | 1 回の平均 | 1 起動あたり待ちの後の量 | それ以外の書き込み |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 設計 | 13 | 66 | 49 | 16.29M | 333k | 1.25M | 10.40M |
| 検査 | 12 | 49 | 40 | 4.74M | 118k | 0.40M | 2.91M |
| 実装 | 16 | 11 | 9 | 2.19M | 244k | 0.14M | 4.43M |
| 取り込み | 11 | 5 | 3 | 0.41M | 137k | 0.04M | 1.77M |
| 仕上げ | 8 | 5 | 3 | 0.27M | 90k | 0.03M | 1.25M |

**待ちの後の書き直しは設計が最も多く（量で検査の 3.4 倍）、次が検査である。** どちらも収束ループ
（設計は `cross-review`、検査は `cross-refactoring` と `cross-review`）を持つ。`cross-review` は
`bg-wait.sh wait` を 1 回最長 540 秒で呼び直すため、呼び直すたびに 5 分を超えて書き直す。

## 構成要素

| 要素 | 変更 | 責務 |
| --- | --- | --- |
| `plugins/ndf/agents/supervisor.md` | 新規 | 寿命 5 分の supervisor の定義。本文は規約の正本（`agent-layers.md`）を指すだけ |
| `plugins/ndf/agents/supervisor-waits.md` | 新規 | 同じ定義に `experimental: { cacheTtl: 1h }` を足したもの |
| `plugins/ndf/.claude-plugin/plugin.json` | 変更 | `agents` に 2 つを足す |
| `agent-layers.md` | 変更 | 起動指示の `subagent_type`（区間の先頭の工程で選ぶ）、持ち場の報告の `結果: 区切り` と `次の工程`、conductor の受け方、supervisor の規則 11（区切り） |
| `context-window.md` | 変更 | 切れ目 2 を「収束ループの前後」へ広げる（ドキュメントレビューの前を含める） |
| `waiting.md` | 変更 | 待ちの費用の節に、待ちの後の書き直しと区切り・寿命への参照を 1 段落 |
| `plugins/ndf/scripts/token-guard.sh` | 変更 | 判定「区切り」を足す（F3） |
| `scripts/token-usage.py` | 変更 | `rewrite_tokens_after_5m` を足す（F4） |
| テスト | 新規・変更 | 定義の frontmatter、hook、集計 |

```mermaid
flowchart LR
  C[conductor] -->|Agent: subagent_type| S1[supervisor<br/>5 分]
  C -->|Agent: subagent_type| S2[supervisor-waits<br/>1 時間]
  S1 -->|Skill cross-review| G[token-guard.sh<br/>判定: 区切り]
  G -->|記録を読む| R[(subagents/agent-ID.jsonl<br/>meta.json)]
  G -->|止める| S1
  S1 -->|結果: 区切り| C
  S2 -->|Skill cross-review| G
  T[token-usage.py] -->|読む| R
```

## 入出力の契約

### F1: supervisor の定義

```markdown
---
name: supervisor-waits
description: NDF の 3 層の supervisor（待ちを抱える区間）。持ち場を通し、持ち場の報告で返す
experimental:
  cacheTtl: 1h
---
```

- `supervisor.md` は `experimental` を持たない。**どちらも `tools` / `disallowedTools` を書かない**
  （supervisor は Skill と Agent を使う。`general-purpose` と同じ道具を持つ）
- 本文は worker の定義と同じ形で、規約の正本（`agent-layers.md` の「conductor → supervisor」）を
  指し、守る規則は起動指示が渡すと書く。**規則を定義へ写さない**（起動指示と二重になる）
- `model` は書かない（conductor と同じモデル。`agent-layers.md` の「モデルを選ぶ」）

**起動指示の `subagent_type` は、その supervisor が最初に通す工程で決める。**

| 最初に通す工程 | `subagent_type` | 当たる起動 |
| --- | --- | --- |
| 構造改善・実装レビュー・ドキュメントレビュー（収束ループ） | `ndf:supervisor-waits` | 検査の持ち場の起動、区切りの後の設計・検査の起動 |
| それ以外 | `ndf:supervisor` | 設計・実装・取り込み・仕上げの持ち場の起動 |

モードで検査が構造改善を含まない（`documentation` / `light`）ときは、検査の最初の工程が実装
レビューになり、同じく `supervisor-waits` に当たる。

### F2: 持ち場の報告に足すもの

| 項目 | 値 | 空のとき |
| --- | --- | --- |
| 結果 | `完了` / `関門` / `止まった` / **`区切り`** | 空にしない |
| 次の持ち場 | `区切り` のときは**同じ持ち場の名前** | — |
| **次の工程** | 工程表の行名。`区切り` のときだけ書く | `無し` |

conductor の受け方（`agent-layers.md` の表へ 1 行足す）:

| 見出しの有無 | `結果` | conductor の動き |
| --- | --- | --- |
| ある | `区切り` | 同じ持ち場を、`次の工程` から通す supervisor として起動し直す。`subagent_type` は上の表で選び、「前の持ち場の報告」に区切りの報告を入れる。関門を問わない |

supervisor の規則に 11 を足す:

> 11. 収束ループの工程（構造改善・実装レビュー・ドキュメントレビュー）の Skill を起動して hook に
>     止められたら、同じ起動をやり直さずに `結果: 区切り`・`次の工程: <その工程>` で返す。起動の前に
>     済ませること（Pull Request を出す・進行を記録する）は、起動の前に済ませておく

**区切るかを supervisor に判断させない。** 規則 11 は hook の判定（F3）に従うだけで、自分から区切らない。
文脈が短い（C < 2.5P）ときは hook が通し、そのまま続ける。hook の無いランタイムでは区切らない。

**区切りは持ち場の語彙を増やさない。** `description` は同じ `<持ち場>: <課題>` のままで、
測定（`skill-stats --agents`・`token-usage.py`）の持ち場の判定は変わらない。「持ち場の一覧」では、
区切りの後の起動を別の行にする（同じ持ち場を工程の途中から起動し直した行）。

### F3: hook の判定「区切り」

`token-guard.sh` の PreToolUse `Skill` に判定を 1 つ足す。

| 項目 | 値 |
| --- | --- |
| 対象 | 入力に `agent_id` があり、`<記録のディレクトリ>/<セッション>/subagents/agent-<agent_id>.meta.json` の `agentType` が `ndf:supervisor` か `ndf:supervisor-waits` で、Skill の名前が `cross-review` / `cross-refactoring`（`ndf:` の有無を問わない） |
| 読む記録 | **supervisor 自身の記録** `${transcript_path%.jsonl}/subagents/agent-<agent_id>.jsonl`。入力の `transcript_path` はサブエージェントの中でも親（conductor）の記録を指すので、そのまま読まない（`token-guard.sh` の既存の注記、`statusline.sh` の組み立てと同じ） |
| P | 読む記録の最初の assistant 呼び出しの文脈（`input + cache_read + cache_creation`） |
| C | 読む記録の最後の assistant 呼び出しの文脈 |
| 止める条件 | `C ≥ 比 × P`。比の既定は 2.5（決定 4） |
| 止め方 | `permissionDecision: deny`。理由の欄に規則 11 の返し方（`結果: 区切り`・`次の工程`）を出す |
| 1 度だけ通す | 止めた直後に同じ supervisor が同じ Skill を同じ引数で起動したら通す（conductor の判定と同じ控えの形） |
| 止めない | `agent_id` が無い（conductor）・`agentType` が supervisor でない（worker・`general-purpose`）・記録か meta が読めない・P か C が読めない |
| 変える | `NDF_SUPERVISOR_CUT_RATIO`（既定 2.5）。`NDF_SUPERVISOR_CUT_GUARD=0` でこの判定を無効にする |

**`agentType` で見分けるので、`general-purpose` で起動した supervisor（conductor が古い起動の形を
使った場合）は止めない。** その supervisor は区切らずに続ける（今と同じ費用）。

### F4: 集計の列

`token-usage.py` の `per_role` と `external` に `rewrite_tokens_after_5m`（直前の間隔が 5 分を超えた
書き直しの量の合計）を足す。md の「持ち場ごとの呼び出しとキャッシュ」の表に列を 1 つ足す。
間隔の判定は今の `rewrites_after_5m` と同じ（`CACHE_5M = 300`）。

### F5: 候補 1 の契約（#870・#827 が実装する）

**待ちを持つのは drive で、drive を起動するのは conductor である。** conductor は寿命 1 時間で、
文脈が supervisor より小さい。drive の待ちの間、LLM は誰も文脈を抱えない。

```bash
drive <PR> [init の引数] [--on-pause <起こす側>]
# --on-pause なし: LLM の要る地点で止まり、1 行の JSON を出して終わる（#870 の形）
#   {"pause":"fix","prompt_file":"...","result_file":"...","round":3}
# --on-pause あり: 止まる代わりに <起こす側> を pause の種類とファイルを引数に起動し、
#   result_file ができたら続ける。起こす側が 0 以外で終わった pause だけ、止まって JSON を出す
```

| pause の種類（#870） | 起こす相手 | 前提 |
| --- | --- | --- |
| `fix` / `sweep` | CLI の worker（`claude -p`、道具は Read・Edit・Bash。#760・#859） | 修正の抜粋と PR 固有の穴埋め |
| rotate の title・body / 振動の裁定 | 最小構成の `claude -p`（#827 の 7.3k） | 規則の抜粋と結果ファイル |
| 収束しない・範囲外の課題が要る・最終ゲートで落ちた | 起こさない（止まって JSON を出す） | conductor が supervisor を起動し、JSON を渡す |

持ち場の報告には `待ち`（drive のコマンド 1 行。`無し` が既定）を足す。conductor の動きは次の 2 段である。

1. `結果: 区切り` で `待ち` があれば、そのコマンドを `run_in_background` で起動する
2. 完了の通知で `次の工程` の supervisor を起動する。「前の持ち場の報告」に drive の結果ファイルのパスを添える**`待ち` の項目は
drive ができた時点で #870 が足す。** #954 では足さない（使う相手が無い）。

## スクリプトにする範囲

収束ループの工程で supervisor が今している仕事を、器ごとに分けた。器の名前は
`work-vessels.md` の表のもの。

| 仕事 | 今 | #954 の後（つなぎ） | 候補 1 の後（#870・#827） |
| --- | --- | --- | --- |
| レビュー担当・提案者の起動、監視、取り込み、根拠の検証、反証 | スクリプト（supervisor が 1 段ずつ呼ぶ） | 同じ | スクリプト（drive が続けて呼ぶ） |
| 待ち | supervisor が `bg-wait.sh wait` を呼び直す | 同じ。寿命 1 時間の supervisor が呼び直す | drive の中。LLM は待たない |
| 収束の判定・上限の打ち切り | スクリプト（`state.py judge` の終了コード）を supervisor が読む | 同じ | drive が読む |
| 区切るかの判定 | — | hook（比の規則） | drive の pause の種類 |
| 指摘の修正・最終スイープ | worker（サブエージェント） | 同じ | CLI の worker |
| rotate の title・body、振動の裁定 | supervisor | 同じ | 最小構成の `claude -p` |
| 範囲外の起票・収束しないときの判断・持ち場の報告 | supervisor | 同じ | supervisor（drive が止まったときと、drive の後の区間） |
| `--scope` / `--baseline-test` / `--sync-command` を決める | supervisor | 同じ | supervisor（drive の前の区間） |
| 進行の記録 | supervisor が記録のコマンドを打つ | 同じ | 同じ（drive は記録を呼ばない。#870） |

**LLM に残るのは、表の最後の 3 行の判断と、修正・裁定の中身だけである。** 待ち・判定・起動の組み立ては
すべてスクリプトが持つ。

## 処理の流れ

```mermaid
sequenceDiagram
  participant C as conductor（1 時間）
  participant S1 as supervisor（5 分）
  participant H as token-guard.sh
  participant S2 as supervisor-waits（1 時間）
  C->>S1: 設計: #954（ndf:supervisor）
  S1->>S1: 要求・設計・再構成・pr
  S1->>H: Skill cross-review
  H-->>S1: deny（C ≥ 2.5P。区切りで返す）
  S1-->>C: 結果: 区切り / 次の工程: ドキュメントレビュー
  C->>S2: 設計: #954（ndf:supervisor-waits、前の報告）
  S2->>H: Skill cross-review
  H-->>S2: 通す（C < 2.5P）
  S2->>S2: ラウンドの待ち（60 分以内は読み込み）
  S2-->>C: 結果: 関門
```

supervisor の結果の遷移（`区切り` は同じ持ち場へ戻る）:

```mermaid
stateDiagram-v2
  [*] --> 持ち場
  持ち場 --> 持ち場: 区切り（次の工程から起動し直す）
  持ち場 --> 次の持ち場: 完了
  持ち場 --> 承認待ち: 関門
  承認待ち --> 次の持ち場: 承認
  持ち場 --> [*]: 止まった
```

## 既存の規則への当てはめ

`結果` の値の集合へ `区切り` を、`subagent_type` の値へ 2 つの定義を足す。そこで、今の値を前提にした
規則を集めて当てはめた。当てはまらないものだけを挙げる。

| 集め方 | 範囲 |
| --- | --- |
| 検索した語 | `結果`・`完了`・`関門`・`止まった`・`general-purpose` |
| 検索した文書 | `agent-layers.md`・`SKILL.md`・`context-window.md`・`relay.md`・`waiting.md` |
| 検索したスクリプト | `scripts/` の `.py` / `.sh`。`結果` を読むものは無い |

| 規則 | どこ | 変える内容 |
| --- | --- | --- |
| conductor の受け方の表が 3 値だけを扱う | `agent-layers.md`「持ち場の報告」 | `区切り` の行を足す（F2） |
| `次の持ち場` は「持ち場の語彙のどれか」で、同じ持ち場を想定していない | 同じ表 | `区切り` のときは同じ持ち場と書く |
| 起動指示の `subagent_type` は「省く（`general-purpose`）」 | `agent-layers.md`「conductor → supervisor」 | F1 の表へ置き換える |
| 切れ目 2 は「構造改善と実装レビューの前後」だけで、ドキュメントレビューの前を含まない | `context-window.md` | 収束ループの前後へ広げる |
| 中断の点検の手順 3 は「`最後に記録した工程` の頭から、同じ持ち場の名前で起動し直す」で、`subagent_type` を言わない | `agent-layers.md`「conductor の中断の点検」 | 起動し直すときも F1 の表で選ぶ、と足す |
| 測定の層の判定は `spawnDepth` と `description` で、`agentType` を見ない | `token-usage.py` | 変えない。`description` の形を保つので当てはまる |

## 非機能

**費用が下がらないときに戻せること。** 1 時間の寿命は定義の 1 行で、区切りの hook は環境変数で
止められる。AC9 の再計算で損益分岐を満たさない区間は、起動の表で `ndf:supervisor` へ戻す。
