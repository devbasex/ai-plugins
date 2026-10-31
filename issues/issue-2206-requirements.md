# #2206: hook: 承認ゲートの外の問い合わせを止めて AI に判断させ、費用がかかる判断だけを人へ回す

正は課題の本文（#2206）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

親: #2168（Neural Development Factory。ランナーは NDF をそのまま動かすため、プラグインの hook が Factory にも効く）

## 依頼（原文、2026-10-31）

> 承認ゲート以外のユーザー問い合わせは原則hookで止めてAIが判断するように強制する必要があります。
> どうしても判断が不可能な場合(金銭的コストがかかるなど)の場合のみ、AskUserQuestionを出して判断を求めます。

## 何が困っているか

- 今の NDF には、承認ゲート（設計 PR のマージ・本番系へ届く操作）の外で出る `AskUserQuestion` を止める仕組みが無い。`hook.py` は `AskUserQuestion` を判定していない
- Factory（#2186 のランナー）では、問い合わせ 1 件ごとに実行ジョブが終わり、人の答えを待って再開する（確定仕様の I8）。AI が決められる問いで止まると、その分だけスプリントが止まり、利用者の手間も増える

## 何をするか

- `AskUserQuestion` を PreToolUse の hook で止め、AI に判断させる。止めたときは、止めた理由と「自分で決め、理由を記録して続ける」案内を返す
- 人へ回してよい問い合わせの区分（下の基準）を決め、プロジェクトごとに `.ndf/ask.json` で変えられるようにする
- AI が代わりに判断した問いと答えを記録に残す。Factory では詳細の画面に「AI が代わりに判断した問い合わせ」として見せる
- Factory の判断（`gate.reached` の種類「判断」）は、hook が通した問い合わせだけになる

## 人へ回す基準（2026-10-31 の利用者の依頼「大まかな基準を決めておく」）

**原則: AI が自分で決める。** 人へ回すのは、AI が決めてよい範囲の外にあるものだけにする。迷った・情報が足りない・好みが分かれるは、人へ回す理由にならない。その場合は根拠を集めて推奨の案を選び、理由を記録して進む。

### 人へ回す区分

| 区分（`metadata.source`） | 当たるもの | 外せるか |
| --- | --- | --- |
| `ndf-gate:1`・`ndf-gate:2` | 承認ゲート（設計 PR のマージ・本番系へ届く操作） | 外せない（承認ゲートは 2 つのまま） |
| `ndf-ask:cost` | お金が出ていく: 有料の資源の起動・有料の外部 API・購入・プランの変更。しきい値（`cost.threshold_usd`）以下は AI が決める | 外せる |
| `ndf-ask:irreversible` | 取り消せない操作のうち承認ゲートに載らないもの: 外部の人への送信（メール・チャット・Issue への言及）、外部の系のデータの削除、公開範囲を広げる操作 | 外せる |
| `ndf-ask:human-action` | 人にしかできない操作: 対話のログイン・二要素認証・権限の付与・秘密の発行と登録 | 外せる |
| `ndf-ask:legal` | 規約・契約・ライセンス・個人情報の扱いへの同意 | 外せる |
| `ndf-ask:<プロジェクトが足した名前>` | プロジェクトが `extra` に書いた区分 | — |

### 人へ回さないもの（AI が決める例）

- 設計の選択・道具とライブラリの選択・命名・ファイルの置き場
- テストの方針・範囲・順序、実装の順序、並列の数
- 範囲外の課題の扱い（`out-of-scope` で起票して進む）、課題を分ける・まとめる
- レビューの指摘を直すか退けるか（理由を記録する）
- 曖昧な依頼の解釈（最も筋の通る解釈を選び、選んだ理由を記録する）

## 判定の仕組み

- hook は質問の中身を読んで区分を当てない（当て推量になる）。AI が `AskUserQuestion` の `metadata.source` に区分を付け、hook はそれが許された区分かどうかだけを見る
- 区分が無い・許されていない問い合わせは止める（`deny` と理由）
- 区分を付けて通した問い合わせも記録に残し、振り返りで区分の使い方を見直せるようにする

## `.ndf/ask.json`（プロジェクトごとの宣言）

```json
{
  "version": 1,
  "mode": "enforce",
  "allow": ["cost", "irreversible", "human-action", "legal"],
  "cost": {"threshold_usd": 0},
  "extra": [
    {"name": "customer-contact", "description": "顧客の担当者へ連絡するかどうか"}
  ]
}
```

| 項目 | 空・無いとき | 意味 |
| --- | --- | --- |
| `version` | 必須（`1`） | 宣言の形の版 |
| `mode` | `enforce` | `enforce`（止める）/ `advise`（止めずに案内だけ出す）/ `off`（hook を働かせない） |
| `allow` | 4 つすべて | 人へ回してよい組み込みの区分。承認ゲートは書かなくても通る |
| `cost.threshold_usd` | `0` | これ以下の費用は AI が決める |
| `extra` | 無し | プロジェクトが足す区分。`name` は `ndf-ask:<name>` で使う |

- **ファイルが無いリポジトリでも `enforce` で働く**（依頼の「原則」に合わせる）。止めたくないリポジトリは `mode: "off"` を書く
- Factory はリポジトリの `.ndf/ask.json` をランナーの中の NDF がそのまま読む。サービスの画面には入力欄を持たず、読んだ値を表示だけする（確定仕様の決定 8 と同じ扱い）

## 受け入れ条件（案）

1. 承認ゲートの外で、`metadata.source` が無い・許されていない `AskUserQuestion` は hook が止め、AI は止めた理由を受け取って自分で判断して続ける
2. `ndf-gate:1`・`ndf-gate:2` の `AskUserQuestion` は、`.ndf/ask.json` の内容によらず止めない
3. `allow` と `extra` にある区分の `AskUserQuestion` は止めない。`allow` から外した区分は止める
4. `.ndf/ask.json` が無ければ既定（`enforce`・4 区分）で働き、`mode: "off"` なら止めない、`advise` なら止めずに案内を出す
5. 止めた問い・通した問いと区分・AI の答えが記録に残る
6. 4 つのランタイムでの扱い（hook が無いランタイムで何をするか）を設計で決める

## 関連

- 画面モック（判断の画面と「AI への任せ方」の設定）: https://claude.ai/artifact/MYL6CPBTsm6JFTNgtiwY2J
- #2186（ランナー）・#2173（判断の通知と答え）はこの課題の振る舞いを前提にする

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 要求と受け入れ条件

この節が要求の正である。上の「何をするか」から「受け入れ条件（案）」までは起票時の案で、条件は下の「起票者の推定」の表で扱いを決め、受け入れ条件は下の AC1〜AC15 で置き換える。

### 依頼（原文）

> 承認ゲート以外のユーザー問い合わせは原則hookで止めてAIが判断するように強制する必要があります。
> どうしても判断が不可能な場合(金銭的コストがかかるなど)の場合のみ、AskUserQuestionを出して判断を求めます。

（書き手: 利用者、日付: 2026-10-31）

> 大まかな基準を決めておく

（書き手: 利用者、日付: 2026-10-31。本文の「人へ回す基準」の見出しにある引用。基準の中身は起票者が書いたもので、下の「起票者の推定」で扱う）

### 起票者の推定

題・本文のうち、利用者の原文に無く起票者が推して足した条件。承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 題の「費用がかかる判断だけを人へ回す」 | 原文の「金銭的コストがかかるなど」 | 採らない（原文は「など」で費用以外も含む。区分は S2 のとおり） |
| S2 | 人へ回す組み込みの区分を `cost`・`irreversible`・`human-action`・`legal` の 4 つにする | 本文「人へ回す区分」 | AC3・AC4 |
| S3 | 区分を AI が `AskUserQuestion` の `metadata.source` に付け、hook は中身を読まずに区分だけを見る | 本文「判定の仕組み」 | AC1・AC3。前提 1 |
| S4 | `irreversible`・`human-action`・`legal`・`cost` は `.ndf/ask.json` で外せる | 本文「人へ回す区分」の「外せるか」の列 | 一部を採らない。共通原則の C1〜C8 とプロジェクトの P の行に当たる問い合わせは外せない区分で通す（AC5）。共通原則が「AI の解釈で緩めない」とし、プロジェクトが外せないと定めているため |
| S5 | `.ndf/ask.json` の形（`version`・`mode`・`allow`・`cost.threshold_usd`・`extra`）と既定値 | 本文「`.ndf/ask.json`」 | AC6・AC7・AC8 |
| S6 | ファイルが無くても `enforce` で働く | 本文「`.ndf/ask.json`」の箇条 | AC7 |
| S7 | `mode` に `advise`・`off` を置く | 本文「`.ndf/ask.json`」の表 | AC8 |
| S8 | `cost.threshold_usd` 以下の費用は AI が決める（既定 0） | 本文「人へ回す区分」の `ndf-ask:cost` の行 | AC6。比べる額を出すのは AI（前提 3） |
| S9 | AI が代わりに判断した問いと答えを記録し、Factory の詳細の画面に見せる | 本文「何をするか」 | 記録は AC9・AC10。画面に見せるのは含まない（#2186・#2173） |
| S10 | 区分を付けて通した問い合わせも記録する | 本文「判定の仕組み」 | AC9 |
| S11 | Factory の判断（`gate.reached` の種類「判断」）は hook が通した問い合わせだけになる | 本文「何をするか」 | 含まない（Factory 側の振る舞い。この課題の hook が前提を作る） |
| S12 | Factory はサービスの画面に入力欄を持たず、`.ndf/ask.json` を表示だけする | 本文「`.ndf/ask.json`」の箇条 | 含まない（#2186・#2173） |
| S13 | 4 つのランタイムでの扱いを設計で決める | 本文「受け入れ条件（案）」の 6 | AC12 |

### 目的

- 承認ゲートの外で AI が人へ問って止まる回数を減らし、人が手を止めるのを承認ゲートと、AI が決めてよい範囲の外にある判断だけに寄せる（プロジェクト MVV の Vision・Value 2）
- Factory（#2186 のランナー）では問い合わせ 1 件ごとに実行ジョブが終わるため、AI が決められる問いでスプリントを止めない

### 前提

- 前提 1: Claude Code の `AskUserQuestion` は入力に `metadata.source`（任意の文字列。利用者には表示されない）を持つ。2.1.314 の本体で説明文 `Optional identifier for the source of this question` を確かめた（2026-10-31）
- 前提 2: PreToolUse の hook が `permissionDecision: "deny"` と理由を返すと、`AskUserQuestion` は出ずに、理由が AI へ返る（既存の token の guard が同じ形で止めている）
- 前提 3: hook は費用の額を知らない。`cost.threshold_usd` は、AI が区分 `ndf-ask:cost` を付けるかどうかを決めるときの基準として AI へ渡す（hook は額を比べない）
- 前提 4: 質問の本文で問いを書いてターンを終える回り道は、この課題の hook では止めない（中身を読まない方針と同じ理由。下の「含まない」）
- 前提 5: 区分の綴り（`ndf-gate:1`・`ndf-gate:2`・`ndf-ask:<名前>`）は起票時の案のまま使う。共通原則の行に当たる区分の名前は設計で決める

### 対象範囲

含む:
- Claude Code の `AskUserQuestion` を PreToolUse の hook で判定し、許されない区分を止める仕組み
- `.ndf/ask.json` の形・既定値・壊れたときの扱い
- 止めた問い・通した問いの記録と、止めた後に AI が下した判断の記録の手段
- NDF 自身の Skill・エージェントの本文のうち、承認ゲートの外で利用者へ問う手順の見直し（区分を付ける・AI が決める）
- relay の差し戻しの文（`relay_lib/mark.py` の `WAIT_REASON`）が区分の付け方を案内すること
- Codex・Kiro CLI・agy での扱いを決めて書くこと

含まない:
- Factory の画面（判断の画面・「AI が代わりに判断した問い合わせ」の表示・`.ndf/ask.json` の表示）。#2186・#2173 が持つ
- 本文で問いを書いて終える応答を検出して止めること（前提 4）
- 質問の中身から区分を推し量ること
- 承認ゲートの数と置き場所の変更（`development-workflow` が持つ）
- `pace` の承認の委任（`.ndf/pace.json`）の変更

### ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | AI が `AskUserQuestion` を呼んだ | AI が人の判断が要ると考えた | — | — |
| E2 | hook が `.ndf/ask.json` を読んだ | E1 の PreToolUse | ファイルが無い: 既定で判定する（AC7）。壊れている: 既定で判定し、壊れた箇所を案内に添える（AC7） | E1 |
| E3 | hook が問い合わせを通した | 区分が許されている、または `mode` が `advise` / `off` | 記録に失敗しても通す（AC11） | E2 |
| E4 | hook が問い合わせを止めた | 区分が無い・許されていない、かつ `mode` が `enforce` | hook の内部の失敗は通す側へ倒す（AC11） | E2 |
| E5 | hook が判定を記録に書いた | E3 か E4 | 書けなくても判定は変えない（AC11） | E3 か E4 |
| E6 | AI が止めた理由を受け取り、自分で判断した | E4 の理由と案内 | AI が同じ問いを区分を付け替えて出し直す: hook は区分だけを見て判定する（区分を付けた問いは E9 で数える） | E4 |
| E7 | AI が判断と理由を記録した | E4 の案内にある記録の手段 | 記録しないまま進む: E5 の「止めた」記録だけが残り、振り返りで数えられる | E6 |
| E8 | 利用者が通った問い合わせに答えた | E3 | — | E3 |
| E9 | 振り返りで区分の使い方を見直した | 記録の集計 | — | E5・E7 |

### 用語

| 用語 | 意味 |
| --- | --- |
| 問い合わせの区分 | `AskUserQuestion` の `metadata.source` に AI が付ける値。`ndf-gate:<番号>` か `ndf-ask:<名前>` |
| 問い合わせの宣言 | `.ndf/ask.json`。人へ回してよい区分と hook の働き方をプロジェクトごとに書く |
| 代わりの判断 | hook が問い合わせを止めた後に AI が自分で下した判断。問い・選んだ答え・理由を記録する |

### 受け入れ条件

hook の判定（AC1〜AC8）は、PreToolUse の入力 JSON を `hook.py` へ標準入力で渡したときの標準出力で確かめる。

- [ ] AC1: `.ndf/ask.json` が `mode: "enforce"`（または無い）とき、`metadata.source` が無い `AskUserQuestion` の入力に対し、hook は `permissionDecision: "deny"` を返し、理由に「止めた理由」「自分で決めて理由を記録し、続ける」案内「記録の手段（コマンド）」「人へ回してよい区分の一覧」が入る
- [ ] AC2: `metadata.source` が `ndf-gate:1` か `ndf-gate:2` の入力は、`.ndf/ask.json` の `mode` が `enforce` で `allow` が空配列でも、`deny` を返さない
- [ ] AC3: `metadata.source` が `ndf-ask:<名前>` で、`<名前>` が `allow` か `extra[].name` にあれば `deny` を返さない。`allow` から外した組み込みの区分（例: `allow: ["cost"]` のときの `ndf-ask:legal`）と、どちらにも無い名前は `deny` を返す
- [ ] AC4: `allow` を省いたとき、`cost`・`irreversible`・`human-action`・`legal` の 4 区分が許される
- [ ] AC5: 共通原則の C1〜C8 とプロジェクトの固有の行（ai-plugins では P1）に当たる操作の承認を問う区分は、`allow` に書かなくても、`allow` から外しても `deny` を返さない。`mode: "enforce"` のどの宣言でもこの区分を止められない
- [ ] AC6: `deny` の理由と、通した問い合わせの案内には `cost.threshold_usd` の値（既定 `0`）が入り、「この額以下の費用は AI が決める」と読める
- [ ] AC7: `.ndf/ask.json` が無いとき、AC1〜AC4 の既定（`enforce`・4 区分）で判定する。JSON として読めない・`version` が `1` でないときも既定で判定し、理由か案内に「`.ndf/ask.json` を読めなかった」ことと箇所が入る
- [ ] AC8: `mode: "off"` のとき hook は `AskUserQuestion` に何も返さない（既存の待ちの Slack 通知と relay の `question open` は今のまま動く）。`mode: "advise"` のとき `deny` を返さず、AC1 で `deny` になる入力には `additionalContext` に同じ案内を返す
- [ ] AC9: hook が判定するたびに、記録へ 1 件が追記され、時刻・セッション・作業ディレクトリ・区分（無ければ空）・判定（`deny` / `allow` / `advise`）・問いの文（`questions[].question`）が入る。記録の置き場と形は設計で決め、Factory のランナーが読める場所に置く
- [ ] AC10: AC1 の案内にある記録の手段を AI が実行すると、記録へ「代わりの判断」が 1 件追記され、問い・選んだ答え・理由が入り、AC9 の止めた記録と対応づけられる
- [ ] AC11: `.ndf/ask.json` の読み込み・記録の書き込み・判定の内部の例外のどれが起きても、hook は終了コード 0 で返り、`deny` を出さない（既存の hook の「どの失敗も通す」規則と同じ）。締め切り（`hook_lib/deadline.py`）を超えたときも同じ
- [ ] AC12: Codex・Kiro CLI・agy のそれぞれで、問い合わせの Tool があるか・hook で止められるかを確かめた結果と、各ランタイムでの扱い（止める / 案内だけ / 対象外）が利用者向けの文書（`plugins/ndf/README.md` か `docs/ndf-plugin-reference.md`）に書かれている
- [ ] AC13: NDF の Skill・エージェントの本文で、承認ゲートの外で利用者へ問う手順（`AskUserQuestion`・「利用者に確認する」「利用者へ示す」など）の一覧を設計で作り、各箇所が「区分を付けて問う」か「AI が決めて理由を記録する」のどちらかに書き換わっている。既定の宣言で、その手順どおりに動いて hook に止められ続けて先へ進めない箇所が残らない
- [ ] AC14: relay の差し戻しの文（本文で承認待ちのまま終えた応答を `AskUserQuestion` へ戻す文）に、承認ゲートなら `metadata.source` に `ndf-gate:<番号>` を付けること、区分の無い問いは自分で決めて続けることが入る
- [ ] AC15: 退行しない: `AskUserQuestion` 以外の Tool の判定（worktree の guard・token の guard）と、待ちの Slack 通知・relay の `question open` / `question close` の振る舞いが変わらない（既存のテストが通る）

### 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | `AskUserQuestion` の判定は hook の既存の締め切りの中で終わる。`.ndf/ask.json` の読み込みと記録の追記を足しても、既存の締め切りの値を変えない |
| 運用・保守性 | 記録は追記だけで、振り返りで区分ごと・判定ごとに件数を数えられる形にする（E9） |
| セキュリティ | 記録には問いの文と答えだけを書き、hook の入力の他の欄（環境変数・トランスクリプトの中身）を写さない |
| システム環境 | 判定は hook の環境の python（`lib/hook_python.py`）で動き、新しい依存のパッケージを足さない |

### 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`.ndf/ask.json` を新たに読む（無ければ既定で働く）。既定で承認ゲートの外の `AskUserQuestion` が止まるようになる。止めたくない利用者は `mode: "off"` で今の振る舞いへ戻せる（互換の経路あり） |
| データ | 問い合わせの記録を新しく作る。既存の記録の形は変えない |
| 既存の振る舞い | 区分の無い `AskUserQuestion` が止まる。承認ゲートの問いは `metadata.source` を付けないと止まるため、承認ゲートの手順（`development-workflow` の承認の出し方・`approval-request.md`）が区分を付ける |

### 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`（hook の判定は入力 JSON と標準出力のテスト） |
| 静的解析・型検査 | CI の既存のジョブ（`python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/instructions-check.py --root .`） |
| 起動 | `claude plugin validate .` |
| 手動確認 | リリース後テストで、Claude Code の対話のセッションで区分の無い `AskUserQuestion` が出ずに AI が続けること、`ndf-gate:1` の問いが出ることを見る |

### 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | hook の判定は `plugins/ndf/scripts/hook.py` のエントリポイントから `hook_lib/` の 1 モジュールとして呼ぶ（既存の guard と同じ形）。`.ndf/` の宣言の読み方は `project_lib` の既存の流儀に合わせる |
| コーディング規約 | `AGENTS.md` のポリシー。Skill の frontmatter は `plugins/ndf/skills/AUTHORING.md` |
| テスト戦略 | hook の判定は入力 JSON → 出力の単体テスト。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

### 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、hook の失敗を通す側へ倒すこと、記録への追記 |
| 確認してから行う | 承認ゲートの手順の文面の変更、`.ndf/ask.json` の形の確定（承認ゲート 1・2 で人が見る） |
| 行わない | 質問の中身から区分を推すこと、Factory の画面の実装、承認ゲートの数の変更、共通原則の C1〜C8・P1 の問いを宣言で外せるようにすること |

### 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 共通原則の C1〜C8・P1 に当たる問いの区分の名前（例: `ndf-ask:redline`）と、`irreversible`・`human-action` との分け方 | 設計（承認ゲート 1 で人が確かめる） | 設計 PR |
| 記録の置き場と形（AC9・AC10）。Factory のランナーが読む位置 | 設計 | 設計 PR |
| 代わりの判断を記録する手段（専用のコマンドか既存のスクリプトの副命令か） | 設計 | 設計 PR |
| Codex・Kiro CLI・agy の扱い（AC12） | 設計（確かめた結果で決める） | 設計 PR |
| AC13 の一覧の各箇所を「区分を付ける」と「AI が決める」のどちらにするか | 設計 | 設計 PR |
