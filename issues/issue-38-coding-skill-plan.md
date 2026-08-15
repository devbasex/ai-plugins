# issue-38: analyzable coding skill の調査結果と実装プラン

## 関連リンク

- 草稿: [issues/issue-38-coding-rule.md](./issue-38-coding-rule.md)
- ChatGPT による調査回答: [issues/issue-38-chatgpt-response.md](./issue-38-chatgpt-response.md)
- Skill 執筆規約: [plugins/ndf-shared/skills/README.md](../plugins/ndf-shared/skills/README.md)
- Skill 棚卸し: [docs/specifications/ndf-skill-inventory.md](../docs/specifications/ndf-skill-inventory.md)

## モード

`architecture`。根拠: Skill の追加は公開インタフェース（`/ndf:analyzable-coding` というコマンド）の追加にあたる。既存 Skill の判定ロジックと配布物の互換は壊さない。

必須工程: requirements-design（本ファイルの 2.3 / 2.5 / 2.6 に統合）→ implementation-plan（本ファイル）→ tdd-cycle（検査スクリプトを先に落として通す）→ cross-review → quality-gates → plan-to-spec

---

# 第 1 部: 調査結果

## 1.1 結論

草稿の主張と一対一で対応する単一の開発理論は存在しない。ただし**個々の主張はすべて既存理論に対応物がある**。実態は 5 系統の合成である。Claude / ChatGPT の 2 系統で独立に調査し、この結論は一致した。

| 草稿の主張 | 最も近い既存理論 | 出典 | 近さ |
| --- | --- | --- | --- |
| コードよりもデータ | Rule of Representation | Unix 哲学（Eric Raymond） | 非常に近い |
| コードよりもデータ | Data-Oriented Programming (DOP) | Yehonathan Sharvit | 非常に近い |
| 分岐をデータで表現 | Table-Driven Methods | Code Complete 18 章 | 非常に近い |
| 分岐をデータで表現 | Decision Table / DMN / Policy as Code | OMG DMN, Open Policy Agent | 近い |
| ループを避けベクトル化 | Array Programming | Kenneth Iverson (APL), NumPy | 近い |
| 並列化可能性を保つ | MapReduce / Dataflow | Spark, Beam | 近い |
| 定数を外部データへ | Magic Number 排除 / Twelve-Factor Config | 一般的リファクタリング原則 | 近い |
| 常に計測可能 | Observability 2.0 / OpenTelemetry | Charity Majors, OTel semconv | 近い |
| 状態変化をデータ化 | Event Sourcing | Martin Fowler | 部分的に近い |
| ループ後にエラーを集計報告 | Notification パターン | Martin Fowler | 非常に近い |

## 1.2 系譜ごとの整理

### (1) 「コードよりもデータ」— 最も由緒ある系譜

草稿の基本方針そのものが 40 年以上前から繰り返されている主張である。

- **Fred Brooks**（人月の神話）: 「フローチャートを見せてテーブルを隠されたら私は困惑し続ける。テーブルを見せてくれれば、フローチャートはたいてい要らない」
- **Rob Pike ルール 5**: 「Data dominates. 正しいデータ構造を選び、うまく整理していれば、アルゴリズムはほぼ自明になる」
- **Linus Torvalds**: 「悪いプログラマはコードを心配する。良いプログラマはデータ構造とその関係を心配する」
- **Eric Raymond, Rule of Representation**: "Fold knowledge into data, so program logic can be stupid and robust." — 草稿の 1 行目とほぼ同じ主張であり、**草稿の直接の祖先**と言ってよい
- **Alan Perlis のエピグラム**: 「10 種のデータ構造に 10 個の関数より、1 つのデータ構造に 100 個の関数のほうがよい」

理論名として最も近いのは Sharvit の **Data-Oriented Programming**。4 原則からなる（下記 1.4 の事実確認を参照）。

### (2) 分岐 — Table-Driven Methods

`if` / `switch` の連鎖を表・マップ・決定表に置き換える手法は **Code Complete 第 18 章**で体系化済み。「論理文で選べるものはほぼすべてテーブルでも選べる」「ロジックの連鎖が複雑になるほどテーブルが有利になる」という記述は、草稿の「特にネストや else の多段構成を避ける」という条件付けと合致する。

近縁: data-directed programming（SICP 2.4.3）、Replace Conditional with Polymorphism（Fowler）、Dispatch Table、Rules Engine、LLVM TableGen、OMG DMN、Open Policy Agent の "policy as data"。

分岐の種類ごとに適切な表現が異なる点は、skill に落とすうえで重要である。

| 分岐の種類 | 適切な表現 |
| --- | --- |
| 値から値への単純な対応 | `dict` / `Map` / lookup table |
| 処理方式の切り替え | dispatch table / handler registry |
| 条件の組み合わせが多い業務判断 | decision table / DMN / rules engine |
| 認可・制約・組織ポリシー | policy engine |
| 時系列で状態が変化する処理 | state machine / statechart |
| 閉じた少数の型分岐 | 型付き `switch` / pattern matching |

### (3) ループ — Array Programming と Dataflow

源流は **Kenneth Iverson の APL** とチューリング賞講演 "Notation as a Tool of Thought"(1979)。配列を第一級市民とし、逐次ループを表面から消す。NumPy / pandas / Polars のベクトル化はその直系の子孫。並列化可能性を保つ主張は MapReduce・純粋関数・データ並列の議論に対応する。

「ループ内エラーを終了後に集計報告可能にする」は **Martin Fowler の Notification パターン**（"Replacing Throwing Exceptions with Notification in Validations"）そのもの。最初のエラーで止めず全エラーを収集して返す。Spark の accumulator、pandas の `errors='coerce'`、dead letter queue も同系統。草稿の中で特に固有性が高い部分である。

### (4) 定数 — Magic Number 排除と設定の外部化

**Magic Number アンチパターン**と **Twelve-Factor App の Config** が対応する。ただし後述のとおり、草稿で最もリスクの高い項目でもある。

### (5) 計測可能性 — Observability 2.0 / OpenTelemetry

Charity Majors の **Observability 2.0** は「任意に幅広い構造化イベント（wide structured events）を単一の真実の源とし、他のデータ型はそこから導出する」「1 アクションにつき 1 イベントを、完了 / エラー直前に発行する」と定める。OpenTelemetry の Semantic Conventions は属性名と意味をそろえ、相関・集約可能にする。草稿の「振る舞いや結果を極力データとして表現する」を実装レベルに落とすとこれになる。

状態変化そのものをイベント列として保存する **Event Sourcing** も近いが、通常の監査ログよりはるかに大きなアーキテクチャ選択であり、全アプリに適用すべきものではない。

## 1.3 2 つの調査で一致した「危険な点」

両調査が独立に、**草稿をそのまま skill にすると害になる**と指摘した。要点は 3 つ。

### 危険 1: 「if / switch を避ける」の過剰適用

- 「if を消すことで、かえって読みにくく・デバッグしにくく・微妙に間違えやすくなる。単純な if のほうが意図をよく伝える」（Code Complete および table-driven 手法の一般的トレードオフ）
- 「多態への置換自体が、最低 1 つの条件分岐（実装選択）を必要とする」
- 8th Light の整理: **「条件分岐は悪ではない。重複した条件分岐が悪である」**
- TypeScript の discriminated union + `never` による網羅性チェックを動的な文字列辞書に置き換えると、**静的解析能力が下がる**
- 同じことが PHP にも当てはまる。PHP 8.1 の backed enum に対する `match` 式は、未処理のケースがあれば実行時に `\UnhandledMatchError` を投げ、さらに PHPStan が `match.unhandled`（"Match expression does not handle remaining value"）として**静的に**検出する。これを連想配列のディスパッチへ置き換えると、この検査が効かなくなる

→ 「`if` / `switch` を避ける」ではなく「**変化頻度の高い業務判断や多数の条件組み合わせを、ネストした制御構文へ埋め込まない。小さく閉じた型分岐・ガード節・不変条件検査には明示的な分岐を使ってよい**」とすべき。

### 危険 2: 定数の外部化しすぎ = Inner-Platform Effect

Alex Papadimoulis が名付けた **Soft Coding / Inner-Platform Effect**（The Daily WTF, 2006）は、「定数をすべて外部化した結果、プラットフォームの劣化コピーを作ってしまい、顧客どころか熟練プログラマにしか変更できなくなる」現象。条件・優先順位・参照・式・継承・デフォルト・再試行を設定データへ足していくと、やがて独自 DSL になる。LLVM TableGen のドキュメント自身も DSL 複雑化の問題に触れている。

データへ移しただけで複雑性は消えない。目的は**複雑性を検査・可視化・変更可能な形式へ移すこと**であり、外部化するなら最低限これらが要る:

スキーマ / 型・バリデーション / バージョン / 変更履歴 / 競合・到達不能ルールの検出 / テスト / マイグレーション / 監査 / 実行時に適用された rule_id と理由の記録。

### 危険 3: 「`.map()` / `.apply()` でベクトル化」は事実として誤り

草稿の「ベクトル、行列の問題として対応する（`.map()`、`.apply()` など）」は不正確。`.map()` / `.apply()` は必ずしもベクトル演算でも並列処理でもない。混同すると「for を map に置換したから速くなった / 並列化した」という誤った達成感につながる。

| 種類 | 例 | 性質 |
| --- | --- | --- |
| 高階反復 | JS `map`、Python `map`、pandas `.apply`、PHP `array_map` | ループの記述方法を変えただけ |
| ネイティブベクトル演算 | NumPy ufunc、broadcasting | C / SIMD / GPU 等で一括実行 |
| バッチ処理 | SQL、bulk API、bulk insert | 呼出し回数や I/O をまとめる |
| 並行処理 | `Promise.all`、async task、PHP Fiber | 待ち時間を重ねる。CPU 並列とは限らない |
| 並列処理 | multiprocessing、worker、JAX | 複数 CPU / GPU で同時実行 |
| 分散データフロー | Beam、Spark、MapReduce | 分割・再試行・集約を実行基盤が管理 |

→ 「for を見つけたら map へ置き換える」ではなく「**同種の独立したデータ変換は、逐次制御として書く前に、バッチ演算・ベクトル演算・データフロー・並行/並列実行として表現できないか検討する**」。

**PHP を対象に加えたことで、この論点はさらに強くなった。** PHP にはネイティブなベクトル演算基盤が標準では存在せず、`array_map` / `array_column` はいずれも高階反復にとどまる。つまり草稿の「numpy など行列演算モジュールを積極的に活用する」は Python 固有の手段であり、言語非依存の規範としては成立しない。3 言語に共通して効くのは、次の 2 段だけである。

1. **一括処理として表現できないか**（バッチ・データフロー・並行/並列への切替可能性を保つ）
2. **その言語に一括実行基盤があるなら使う**（Python なら NumPy、PHP なら bulk SQL / chunk 取得による N+1 の解消）

規範は 1 に置き、2 は言語別の参照資料へ落とす。この切り分けが、Skill を言語非依存に保ちながら PHP でも実用にするための設計上の要になる。

## 1.4 事実確認の記録

調査中に出た主張のうち、一次情報で裏を取ったもの。

| 主張 | 検証結果 |
| --- | --- |
| DOP の原則は 3 個 / 5 個という記述が両調査に出た | **正しくは 4 原則**。(1) コードとデータの分離 (2) 汎用データ構造での表現 (3) データの不変性 (4) データスキーマとデータ表現の分離。Sharvit 本人の記事で確認 |
| `np.vectorize` は性能目的ではない | **正しい**。NumPy 公式: "The vectorize function is provided primarily for convenience, not for performance. The implementation is essentially a for loop." |
| Data-Oriented Design と Data-Oriented Programming は別物 | **正しい**。前者（Mike Acton / Unity DOTS）はメモリ配置とキャッシュ効率の話。skill 名に `data-oriented` を使うと混同されるため避ける |

## 1.5 草稿の独自性

以下 6 点を 1 つにまとめた、広く定着した理論名は見つからなかった。

1. ルールや判断をデータへ移す
2. 処理をデータ変換として構成する
3. 逐次ループよりバッチ・ベクトル・データフローを優先する
4. 並列化可能性を保つ
5. 判断理由・結果・失敗を構造化データとして観測する
6. これを実装だけでなくリファクタリング規範として使う

思想の各部品は既存だが、**それらを言語横断のコード生成・レビュー・リファクタリング skill として統合する点には十分な独自性がある**。

## 1.6 言語非依存性をどう担保するか（Python / JavaScript / TypeScript / PHP）

対象言語は Python / JavaScript / TypeScript / PHP とする。ただし **Skill 自体は言語に寄らず発動し、使えること**を要件とする。これは「例を 4 言語分並べる」ことではなく、**規範の階層を分けること**で達成する。

| 階層 | 内容 | 言語依存 | 置き場所 |
| --- | --- | --- | --- |
| 原則 | 変化する知識はデータへ、安定した機構はコードへ | なし | SKILL.md |
| 判定 | 分岐/反復/定数の種類 → 適切な表現の対応 | なし | SKILL.md |
| 手段 | その表現を実現する言語機能・ライブラリ | **あり** | `references/language-notes.md` |

判定層までを言語非依存に保てば、Go や Ruby など対象外の言語でも Skill は成立する。逆に「numpy を使え」「`never` で網羅性を検査せよ」を規範本文に書くと、その言語以外では使えない Skill になる。

4 言語での手段の対応は次のとおり。

| 判定 | Python | JavaScript | TypeScript | PHP |
| --- | --- | --- | --- | --- |
| 値 → 値の対応 | `dict`、`Mapping` | `Object.freeze` のオブジェクト | `Record`、`as const` | 連想配列、`match` |
| 処理方式の切替 | 関数を値として持つ `dict` | 関数を値として持つオブジェクト | handler map | first-class callable 構文 `foo(...)`（静的解析が追える） |
| 閉じた状態集合 | `Enum`、`Literal` | 凍結した定数オブジェクト | discriminated union、literal union | backed enum（8.1+） |
| 網羅性の静的検査 | mypy の `assert_never` | **手段なし**（実行時に失敗させる） | `never` による網羅性チェック | PHPStan の `match.unhandled` |
| 不変性 | `frozen=True` の dataclass | `Object.freeze` | `readonly`、`as const` | `readonly` プロパティ（8.1+） |
| スキーマ検証 | pydantic、jsonschema | zod、JSON Schema | zod、JSON Schema | JSON Schema、Valinor 等 |
| 一括処理 | NumPy / pandas（ベクトル演算） | バッチ API、`Promise.all` | 同左 | **bulk SQL / chunk 取得**（ベクトル演算基盤はない） |
| 失敗の集計 | 例外を集めて返す / `errors='coerce'` | 失敗を集めて返す | Result 型、集約 | 例外を集めて返す（Notification パターン） |

JavaScript の列で重要なのは、手段は TypeScript と同じでありながら、**網羅性の静的検査だけが成立しない**こと。したがって JS では MAY の「静的に網羅性を検査できる分岐はそのままでよい」という条件が成立しにくく、静的検査で守れない分をスキーマ検証と実行時の即時失敗で埋める必要性が TypeScript より高い。

PHP の列で重要なのは 2 点。**first-class callable 構文と backed enum によって「データ化しても静的解析が効く」範囲が PHP でも成立する**こと。そして **一括処理の主戦場が SIMD ではなく I/O（N+1 の解消、bulk insert）である**こと。後者は 1.3 危険 3 の結論と一致する。

## 1.7 中核方針の再定義（採用案）

草稿の「コードよりもデータ」は、そのままだと「何でも設定ファイルへ追い出す」と誤解される。次の形に再定義する。

> **変化する知識はデータへ。安定した機構と不変条件はコードへ。両方をスキーマと計測で接続する。**

展開形:

> 変化する判断や業務知識は、検証可能・版管理可能なデータまたはポリシーとして表現する。安定した機構・不変条件・型・境界はコードとスキーマとして表現する。処理は明示的な入力から出力へのデータ変換として構成し、依存関係・順序・副作用を明らかにする。各判断・結果・失敗は、理由・適用ルール・バージョン・実行コンテキストを伴う構造化データとして観測・再現可能にする。

---

# 第 2 部: 実装プラン

## 2.1 目的と非目的

達成したい状態:

- Python / JavaScript / TypeScript / **PHP** でコードを書く際に、分析可能・計測可能なコードスタイルを保つための判断基準を、NDF の Skill として 3 ランタイムへ配布する
- **Skill 自体は言語に寄らず発動し、使える。** 対象言語は例示と手段の記載範囲であって、発動条件でも適用範囲の上限でもない
- 新規実装時だけでなくリファクタリング時にも参照される
- AI エージェントが「if を辞書へ」「for を map へ」と機械的置換する事故を、Skill 自身が防ぐ

やらないこと:

- 静的解析ツール（linter ルール、AST チェッカ）の実装。今回は判断基準の文書化のみ
- Observability 基盤の導入手順（OpenTelemetry のセットアップなど）。計測すべき内容の規範にとどめる
- Event Sourcing の採用指針。参照として言及するが、規範には含めない
- 既存 Skill（`safe-refactoring` / `tdd-cycle` / `logging-guidelines`）の書き換え。相互参照のリンク追加のみ
- 対象 4 言語以外（Go / Ruby / Java など）の言語別手段の記載。規範は言語非依存に保つため、これらの言語でも Skill は成立するが、`references/language-notes.md` への追記は今回の範囲外とする
- フレームワーク固有の規約（Laravel / Django / NestJS など）

## 2.2 前提

- 前提 1: Skill 名は `analyzable-coding` とする。`data-oriented` 系の名称は Mike Acton 系の Data-Oriented Design と衝突するため採用しない。→ 検証: 名称が `plugins/ndf-shared/skills/analyzable-coding/` に存在し、既知の外部 Skill 名と衝突しないこと（`check-skill-frontmatter.py --strict` の警告で確認）
- 前提 2: 3 ランタイム（Claude Code / Codex / Kiro）すべてに配布する。ランタイム固有の機能に依存しない内容のため。→ 検証: 3 つの manifest すべてに追記され、build 後に各配布物へ生成されること
- 前提 3: 規範は禁止形（MUST NOT の列挙）ではなく MUST / SHOULD / MAY の 3 段で書く。→ 検証: SKILL.md に 3 段構成が存在し、MAY 節に「明示的な `if` / `for` / enum を使ってよい場合」が含まれること
- 前提 4: SKILL.md 本文（原則層・判定層）は言語非依存に保ち、言語固有の手段は `references/language-notes.md` に分離する。→ 検証: AC-9 のとおり、SKILL.md 本文に特定言語の API 名・ライブラリ名が現れないこと
- 前提 5: PHP は 8.1 以降を前提とする（backed enum / `readonly` / first-class callable 構文がこの版から）。8.0 以下では代替手段を注記する。→ 検証: `references/language-notes.md` に必要バージョンが明記されていること

## 2.3 受け入れ条件

- [x] AC-1: `plugins/ndf-shared/skills/analyzable-coding/SKILL.md` が存在し、`python3 scripts/check-skill-frontmatter.py --strict` が成功する
- [x] AC-2: SKILL.md が MUST / SHOULD / MAY の 3 段構成を持ち、MAY 節に「単純なガード節の `if`」「閉じた型の網羅的 `switch`」「逐次依存・早期終了・ストリーム処理の明示的ループ」「不変条件・プロトコル・閉じた状態集合の定数と enum」の 4 つが明記されている
- [x] AC-3: 「分岐の種類 → 適切な表現」「反復の種類（高階反復 / ベクトル演算 / バッチ / 並行 / 並列 / 分散）」「定数の性質 → 置き場所」の 3 つの判定表が含まれる。いずれの表も、行の内容が特定言語の機能名に依存していない
- [x] AC-4: Python / JavaScript / TypeScript / PHP の 4 言語について、良い例・悪い例が最低 1 組ずつ含まれる（`references/language-notes.md` を含めた全体で判定してよい）
- [x] AC-9: **言語非依存性** — (a) `description` と `when_to_use` に言語名が発動条件として現れない (b) SKILL.md 本文（原則層・判定層）に特定言語の API 名・ライブラリ名・バージョン番号が現れず、言語固有の記述はすべて `references/language-notes.md` にある (c) 判定表の各行が、対象 4 言語のいずれにも依存しない語で書かれている
- [x] AC-5: 3 つの manifest（`claude-skills.txt` / `codex-skills.txt` / `kiro-skills.txt`）に `analyzable-coding` が追記され、`bash scripts/build-runtime-plugins.sh` 実行後に `plugins/ndf-{claude,codex,kiro}/skills/analyzable-coding/SKILL.md` が生成される
- [x] AC-6: `bash scripts/validate-runtime-plugins.sh` と `python3 scripts/check-markdown-links.py` が成功する
- [x] AC-7: README.md / CLAUDE.md の Skill 個数と開発方法論グループの記載が更新される（Skill 30 → 31、開発方法論 5 → 6、Claude 26 → 27 / Codex 24 → 25 / Kiro 25 → 26）
- [x] AC-8: 起きてはいけないこと — SKILL.md 内に「`if` を使うな」「`for` を使うな」「enum を使うな」という無条件の禁止表現が存在しない

## 2.4 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 独立 Skill `analyzable-coding` を新規追加 | **採用** | 実装時とリファクタリング時の双方から参照される横断規範であり、既存 Skill のどれにも収まらない |
| B | `safe-refactoring` に節を追加 | 不採用 | 新規実装時に発動しない。`safe-refactoring` はテストで守る手順の Skill であり、コードスタイル規範とは責務が違う |
| C | `logging-guidelines` を拡張して計測部分だけ扱う | 不採用 | 草稿の中核（分岐・ループ・定数のデータ化）が落ちる |
| D | 名称を `observable-data-oriented-development` 等にする | 不採用 | 長く、Data-Oriented Design と混同される。`analyzable-coding` のほうがトリガ語として日本語（分析可能・計測可能）に結び付けやすい |
| E | 禁止規則（MUST NOT 列挙）として書く | 不採用 | 1.3 の危険 1〜3 をそのまま踏む。AI エージェントの機械的置換を誘発する |

## 2.5 ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 分析可能（analyzable） | 実行前に、判断の根拠と網羅性を型・スキーマ・テーブルから検査できる状態 |
| 計測可能（measurable / observable） | 実行後に、どの判断がなぜ下されたかを構造化データから再現できる状態 |
| 判断（decision） | 業務ルールに基づく分岐。技術的な不変条件チェック（ガード節）とは区別する |
| 高階反復 | `map` / `apply` など、ループの記述方法を変えただけのもの。ベクトル演算ではない |

## 2.6 不変条件

- Skill は判定基準を提示するだけで、コードを書き換える手順そのものは持たない（書き換え手順は `safe-refactoring` の責務）
- モード判定は `development-workflow` の責務。この Skill は判定結果を受け取る側に徹する
- 3 ランタイムの配布物は `plugins/ndf-shared/` からの生成物であり、直接編集しない
- **SKILL.md 本文の原則層・判定層は、対象言語が増減しても書き換えなくてよい状態を保つ。** 言語の追加は `references/language-notes.md` への追記だけで完結する

## 2.7 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開 Skill 一覧 | `analyzable-coding` を追加 | 追加のみ。既存 Skill の名称・挙動は変えない |
| manifest ファイル | 3 ファイルに 1 行追記 | 追加のみ |
| プラグインバージョン | ndf v7.0.0 → v7.1.0 | Skill 追加のため minor |

## 2.8 修正対象

新規:

- `plugins/ndf-shared/skills/analyzable-coding/SKILL.md`
- `plugins/ndf-shared/skills/analyzable-coding/references/language-notes.md`（言語別の手段。Python / JavaScript / TypeScript / PHP）
- `plugins/ndf-shared/skills/analyzable-coding/references/decision-tables.md`（判定表と例が SKILL.md に収まらない場合）

変更:

- `plugins/ndf-shared/manifests/claude-skills.txt`
- `plugins/ndf-shared/manifests/codex-skills.txt`
- `plugins/ndf-shared/manifests/kiro-skills.txt`
- `plugins/ndf-shared/skills/safe-refactoring/SKILL.md`（相互参照リンク）
- `plugins/ndf-shared/skills/development-workflow/references/workflow-modes.md`（工程への組み込み）
- `plugins/ndf-claude/.claude-plugin/plugin.json` ほか各ランタイムの `plugin.json`（バージョン）
- `README.md` / `CLAUDE.md`（Skill 個数・一覧）
- `docs/specifications/ndf-skill-inventory.md`（棚卸し記録）

生成物（`scripts/build-runtime-plugins.sh` が出力。手で編集しない）:

- `plugins/ndf-{claude,codex,kiro}/skills/analyzable-coding/`

## 2.9 タスク分解

### Task 1: SKILL.md の中核（方針と MUST / SHOULD / MAY）を書く

- **対象ファイル:** `plugins/ndf-shared/skills/analyzable-coding/SKILL.md`
- **変更内容:** frontmatter（`name` / `description`。トリガ語は `Use when` 文末の全角括弧に `・` 区切り、例: `（分析可能なコード・データ駆動・分岐をデータ化・計測可能な実装）`。**言語名はトリガ語に入れない** — 入れると未記載の言語で発動しなくなる）と、1.7 の中核方針、以下の 3 段規範。
  - **MUST**: 外部化したルール・設定・マスタデータにスキーマとバージョンを持たせる / 判断結果に `rule_id`・`rule_version`・`reason` を記録できるようにする / バッチ処理では成功件数だけでなく全失敗の件数・種類・対象を報告する / 外部 I/O・副作用・時刻・乱数を境界として明示する / 不変条件とセキュリティ制約を変更可能な設定だけに依存させない / 閉じた状態集合は型またはスキーマで網羅性を検査する
  - **SHOULD**: 条件の組み合わせが多い業務判断は decision table か policy として表現する / 純粋なデータ変換と副作用を分離する / 同種の大量処理では逐次ループよりバッチ・ベクトル・データフローを検討する / 並行化する処理は冪等性・順序依存・再試行・タイムアウト・キャンセルを明示する / ネストした条件分岐はガード節・状態機械・決定表・dispatch registry への変更を検討する
  - **MAY**: 単純なガード節の `if` / 閉じた型の網羅的 `switch`・pattern matching / 逐次依存・早期終了・ストリーム・メモリ制約下の明示的ループ / 不変条件・プロトコル・閉じた状態集合の定数と enum
- **満たす受け入れ条件:** AC-1, AC-2, AC-8, AC-9
- **進め方:** 先に `python3 scripts/check-skill-frontmatter.py --strict` が落ちることを確認 → frontmatter を書いて通す → 本文を書く。書き上げたら AC-9(b) を検査するため、本文を `grep -inE "numpy|pandas|typescript|phpstan|readonly|dataclass|enum\(|array_map"` にかけ、ヒットしたら `references/` へ移す

### Task 2: 判定表 3 種を書く

- **対象ファイル:** `plugins/ndf-shared/skills/analyzable-coding/SKILL.md`（長くなる場合は `references/decision-tables.md` へ分離）
- **変更内容:** 1.2(2) の「分岐の種類 → 適切な表現」、1.3 危険 3 の「反復の種類」、および定数の分類表を収録する。定数の分類表は次のとおり。

  | 値の性質 | 置き場所 |
  | --- | --- |
  | 数学的・技術的な不変条件 | コード内の定数 |
  | 閉じた状態集合・プロトコル | enum / literal union / schema |
  | 頻繁に変わる業務ルール | decision table / policy / 設定 |
  | 環境ごとに変わる値 | deployment config / 環境変数 |
  | ユーザーが管理するカテゴリ | DB のマスタ・参照テーブル |
  | 表示名・文言 | i18n / コンテンツデータ |
  | セキュリティ上の絶対制約 | コード・型・schema・policy の複数層 |

- **満たす受け入れ条件:** AC-3
- **進め方:** 表を書く → 各行に「この行を選んだ結果どうなるか」を 1 例ずつ添えられるか確認し、添えられない行は削る

### Task 3: 言語非依存の例（判断の記録）を書く

- **対象ファイル:** `plugins/ndf-shared/skills/analyzable-coding/SKILL.md`
- **変更内容:** SKILL.md 本文に置くのは、言語に依存しない形で示せる例だけに限る。(a) 分岐のデータ化: rank → 割引率の if 連鎖 vs 対応表（擬似コードまたは表で示し、特定言語の構文に寄せない）(b) 判断の記録: `rule_id` / `rule_version` / `reason` / 実行コンテキストを含む構造化イベントの JSON 例（JSON なので言語非依存）(c) 失敗の集計: バッチ処理で全失敗の件数・種類・対象を返す形の擬似コード
- **満たす受け入れ条件:** AC-4, AC-8, AC-9
- **進め方:** 各例を「この例から言語固有の語を消せるか」で点検する。消せないものは Task 4 へ移す

### Task 4: 言語別の手段（Python / JavaScript / TypeScript / PHP）を書く

- **対象ファイル:** `plugins/ndf-shared/skills/analyzable-coding/references/language-notes.md`
- **変更内容:** 1.6 の対応表を基に、言語ごとに良い例・悪い例を 1 組以上。
  - **共通の MAY の例**（言語を問わず示す）: ガード節の `if` と、閉じた型に対する網羅的分岐を「そのままでよい例」として先に提示する
  - **Python**: 逐次ループ vs NumPy broadcasting。あわせて `np.vectorize` と `.apply` が高速化にならない反例（公式注記 "provided primarily for convenience, not for performance" を引用）。網羅性は mypy の `assert_never`
  - **TypeScript**: discriminated union + `never` による網羅性チェックを、動的な文字列辞書へ置き換えると静的解析が落ちる例
  - **JavaScript**: 手段は TypeScript と同じだが型注釈がなく網羅性の静的検査が効かないこと、その分を実行時の即時失敗とスキーマ検証で埋める例（未知のキーで `undefined` を返す悪い例 / 明示的に失敗させる良い例）
  - **PHP（8.1+）**: backed enum + `match` を PHPStan の `match.unhandled` が静的に検出する例。dispatch table は first-class callable 構文 `foo(...)` で書くと静的解析が追えること。**一括処理は N+1 の解消と bulk insert であってベクトル演算ではない**ことを明記し、`array_map` への置換が高速化ではない旨を書く。8.0 以下向けの代替（クラス定数 + `switch`）を注記
- **満たす受け入れ条件:** AC-4, AC-9
- **進め方:** 例のコードは実際に動かして確認する。Python の反例は計測して「map / `np.vectorize` への置換では速くならない」ことを数値で示す。PHP は PHPStan を実行して `match.unhandled` が実際に出ることを確認する

### Task 5: 3 ランタイムへ配布する

- **対象ファイル:** `plugins/ndf-shared/manifests/*.txt`、各 `plugin.json`
- **変更内容:** manifest 3 ファイルに `analyzable-coding` を追記、プラグインバージョンを v7.1.0 へ更新、`bash scripts/build-runtime-plugins.sh` で配布物を生成
- **満たす受け入れ条件:** AC-5, AC-6
- **進め方:** manifest 追記 → build → `bash scripts/validate-runtime-plugins.sh` と `python3 scripts/check-markdown-links.py` で検証

### Task 6: 既存 Skill・ドキュメントとの接続

- **対象ファイル:** `safe-refactoring/SKILL.md`、`development-workflow/references/workflow-modes.md`、`README.md`、`CLAUDE.md`、`docs/specifications/ndf-skill-inventory.md`
- **変更内容:** `safe-refactoring` から「構造改善の方向性の基準」として参照を追加。`workflow-modes.md` の実装工程に位置づけを追記。README / CLAUDE.md の個数と一覧を更新。inventory に新規 Skill の行を追加
- **満たす受け入れ条件:** AC-7, AC-6
- **進め方:** 参照を追加 → `python3 scripts/check-markdown-links.py` でリンク切れを検証

## 2.10 影響範囲

- 3 ランタイムの NDF 利用者全員に新しい Skill が配布される。既存 Skill の発動判定に干渉しないか、`description` のトリガ語が `safe-refactoring` / `tdd-cycle` と衝突しないかを確認する
- `safe-refactoring` から参照されるため、リファクタリング時の挙動に影響する

## 2.11 リスクと対処

| リスク | 対処 |
| --- | --- |
| AI エージェントが規範を機械的に適用し、`if` / `for` / enum を無条件に置換する | MAY 節を MUST / SHOULD と同じ重みで書き、「そのままでよい例」を先に提示する。AC-8 で禁止表現の不在を確認する |
| 設定外部化を推奨した結果、Inner-Platform Effect を招く | MUST 節に「外部化するならスキーマ・バージョン・テスト・監査が必須」を置き、これを満たせないなら外部化しないと明記する |
| `.map()` = ベクトル化という誤解が Skill 経由で広まる | Task 3(b) で反例を計測付きで示す。NumPy 公式の "provided primarily for convenience, not for performance" を引用する |
| トリガ語が既存 Skill と競合し、発動判定が不安定になる | `check-skill-frontmatter.py --strict` の警告を確認し、`docs/specifications/ndf-skill-inventory.md` の実測手順に沿って発動を確認する |
| Skill が長大化して読まれない | 判定表と例が SKILL.md を圧迫する場合、`references/` へ分離する（Task 2 に分岐を用意済み） |
| 言語別の例が本文へ流れ込み、特定言語向け Skill になる | 原則層・判定層と手段層をファイルで分離し、AC-9(b) の grep 検査で機械的に確認する |
| 対象 4 言語以外で使われたとき、手段が示されず役に立たない | 判定層までで実用に足る粒度にする。`references/language-notes.md` の冒頭に「ここに無い言語では判定表から自分で対応付ける」と明記する |
| PHP で「ベクトル化せよ」と誤解し、無意味な `array_map` 置換が起きる | Task 4 の PHP 節で「一括処理 = N+1 解消と bulk insert」と明示し、`array_map` 置換の反例を置く |

## 2.12 切り戻し手順

- Skill 追加のみで、既存の挙動を変える変更を含まない。manifest から 1 行削除し `build-runtime-plugins.sh` を再実行すれば配布から外れる
- データ移行なし。バージョンを v7.0.0 へ戻せば完全に元へ戻る

## 2.13 完了の定義

- [x] AC-1 〜 AC-9 をすべて満たし、条件ごとに検証コマンドと結果が対応している
- [x] `python3 scripts/check-skill-frontmatter.py --strict` が成功
- [x] `bash scripts/build-runtime-plugins.sh --check` が成功
- [x] `bash scripts/validate-runtime-plugins.sh` が成功
- [x] `python3 scripts/check-markdown-links.py` が成功
- [x] `claude plugin validate` が成功（`validate-runtime-plugins.sh` に含まれる）
- [ ] 3 ランタイムで実際に Skill が発動することを確認し、結果を `docs/specifications/ndf-skill-inventory.md` へ記録
- [ ] 発動確認は言語を変えて行う。**PHP のコードを対象にした依頼**と、**対象 4 言語以外（Go など）のコードを対象にした依頼**の双方で発動することを確認する（AC-9(a) の実証）

---

## 参考文献

### コードよりデータ

- [Basics of the Unix Philosophy — Rule of Representation](https://www.catb.org/esr/writings/taoup/html/ch01s06.html)
- [Rob Pike's 5 Rules of Programming](https://www.cs.unc.edu/~stotts/COMP590-059-f24/robsrules.html)
- [Linus Torvalds on data structures](https://groups.google.com/g/mechanical-sympathy/c/CHrUYiwqKIQ/m/vt_qdRi70NoJ)
- [Principles of Data-Oriented Programming — Yehonathan Sharvit](https://blog.klipse.tech/dop/2022/06/22/principles-of-dop.html)
- [Data-Oriented Programming (Manning)](https://www.manning.com/books/data-oriented-programming)
- [Data-Oriented Design（別概念・混同注意）](https://www.dataorienteddesign.com/dodbook/node2.html)

### 分岐

- [Code Complete 2nd Ed. Ch.18 Table-Driven Methods](https://www.oreilly.com/library/view/Code-Complete,-Second-Edition/0735619670/ch18.html)
- [Replace Conditional with Polymorphism — Refactoring.Guru](https://refactoring.guru/replace-conditional-with-polymorphism)
- [Conditionals Aren't Evil, Unless You Duplicate Them — 8th Light](https://8thlight.com/blog/wai-lee-chin-feman/2013/08/11/anti-anti-if.html)
- [Destroy All Ifs — John A. De Goes](https://degoes.net/articles/destroy-all-ifs)
- [TableGen Overview — LLVM](https://llvm.org/docs/TableGen/)
- [Decision Model and Notation (DMN) — OMG](https://www.omg.org/dmn/)
- [Open Policy Agent — Philosophy](https://openpolicyagent.org/docs/philosophy)
- [Statecharts: a visual formalism for complex systems — Harel](https://weizmann.elsevierpure.com/en/publications/statecharts-a-visual-formalism-for-complex-systems/)

### ループ・並列化

- [Notation as a Tool of Thought — Iverson (ACM)](https://dl.acm.org/doi/pdf/10.1145/1283920.1283935)
- [Look Ma, No For Loops: Array Programming With NumPy](https://realpython.com/numpy-array-programming/)
- [numpy.vectorize — 性能目的ではないという公式注記](https://numpy.org/doc/stable/reference/generated/numpy.vectorize.html)
- [Replacing Throwing Exceptions with Notification in Validations — Martin Fowler](https://martinfowler.com/articles/replaceThrowWithNotification.html)

### 定数・設定・型（言語別）

- [Inner-platform effect — Wikipedia](https://en.wikipedia.org/wiki/Inner-platform_effect)
- [The Inner-Platform Effect — Exception Not Found](https://exceptionnotfound.net/the-inner-platform-effect-the-daily-software-anti-pattern/)
- [TypeScript Handbook — Narrowing](https://www.typescriptlang.org/docs/handbook/2/narrowing.html)
- [Python enum](https://docs.python.org/ja/3/library/enum.html)
- [PHP 8.1 リリースアナウンス（enum / readonly / first-class callable）](https://www.php.net/releases/8.1/en.php)
- [PHP 8.1: What's New and Changed — PHP.Watch](https://php.watch/versions/8.1)
- [PHPStan `match.unhandled` エラー識別子](https://phpstan.org/error-identifiers/match.unhandled)
- [First-class Callable Syntax in PHP 8.1](https://lindevs.com/first-class-callable-syntax-in-php-8-1/)

### 計測

- [Live Your Best Life With Structured Events — charity.wtf](https://charity.wtf/2022/08/15/live-your-best-life-with-structured-events/)
- [Observability 2.0: Transforming Logging and Metrics](https://gotopia.tech/articles/336/observability-2-transforming-logging-and-metrics-in-software)
- [OpenTelemetry Observability Primer](https://opentelemetry.io/docs/concepts/observability-primer/)
- [OpenTelemetry Semantic conventions for events](https://opentelemetry.io/docs/specs/semconv/general/events/)
- [Event Sourcing — Martin Fowler](https://martinfowler.com/eaaDev/EventSourcing.html)


---

# 第 4 部: 独立 Skill から `refactoring` への統合

## 4.1 独立 Skill にしなかった理由

`analyzable-coding` を単独の Skill として配ると、**発動条件を書けない**。他の Skill は発動する
瞬間を指している（完了報告の直前、バグ修正時、調査レポートを書くとき）のに対し、この内容の
発動条件は「コードを書くとき」以外に書きようがなく、常に該当するトリガは発動判定として
働かない。加えて、読んだエージェントが何を出力し何をもって適用完了とするかも規定できて
いなかった。

内容の重複も判定を裏づけた。既存のコードスメル 14 件のうち 4 件（マジックナンバー・文字列 /
設定の散在 / 深いネスト / 例外の飲み込み / 条件分岐の連鎖）と重なっており、棚卸台帳の
判断基準「機能が他 Skill と重複するものは統合の対象とし、内容は統合先へ残す」に該当する。

## 4.2 決定

`safe-refactoring` を **`refactoring`** へ改名し、観点を統合する。発動点は「リファクタリングを
始めるとき」に定まり、既存の手順（テストで守る・1 手ずつ）に観点が組み込まれる。

| 変更 | 内容 |
| --- | --- |
| 改名 | `safe-refactoring` → `refactoring`（公開コマンドの非互換変更） |
| 版 | v7.1.0 → **v8.0.0** |
| Skill 数 | 31 → **30**（Claude 26 / Codex 24 / Kiro 25） |
| 移行対応表 | `ndf-policies` に追加（v9.0.0 で削除）。v7.0.0 の対応表は予告どおり削除 |

## 4.3 統合先の配置

| 追加先 | 内容 |
| --- | --- |
| `references/code-smells.md` | スメル 3 件追加 — 業務ルールの埋め込み / 一件ずつの反復 / 検証のない外部化 |
| `references/refactoring-catalog.md` | 手法 2 件追加 — 対応表への置き換え / 一括処理への置き換え |
| `references/data-representation.md`（新規） | 判定 3 表、手を付けないもの、改善にならない置き換え、外部化してよい条件、判断の記録 |
| `references/language-notes.md`（移設） | Python / JavaScript / TypeScript / PHP での手段 |
| `SKILL.md` | 手順 3 に「何にどう置き換えるかは data-representation.md で決める」を追加 |

MUST / SHOULD / MAY の 3 段構成は、リファクタリングの文脈では「手を付けないもの」「改善に
ならない置き換え」「外部化してよい条件」として再配置した。規範の宣言ではなく、スメルを
見つけたあとの判断材料として働く。

## 4.4 受け入れ条件の読み替え

第 2 部の受け入れ条件のうち、独立 Skill を前提とするものは次のとおり読み替える。

| 条件 | 読み替え |
| --- | --- |
| AC-1 / AC-5（新規 Skill の追加と配布） | `refactoring` の改名と配布で満たす |
| AC-2（MUST / SHOULD / MAY の 3 段） | 4.3 のとおり再配置。3 段の見出しとしては残さない |
| AC-3（判定表 3 種） | `data-representation.md` に収録 |
| AC-9（言語非依存） | `data-representation.md` 本文と `code-smells.md` / `refactoring-catalog.md` に言語固有語を持ち込まない。言語固有は `language-notes.md` のみ |
| AC-7（個数表記） | 31 → 30、Claude 27 → 26 / Codex 25 → 24 / Kiro 26 → 25 |

---

# 第 3 部: 実装と検証の記録

## 3.1 実行した検証

| 対象 | コマンド | 結果 |
| --- | --- | --- |
| frontmatter 規約 | `python3 scripts/check-skill-frontmatter.py --strict` | Skill 35 個 — エラー 0 / 警告 0 |
| 検査が新 Skill に効いているか | `description` を意図的に壊して再実行 | エラー 1 件を検出（復元後 0 件） |
| 生成物の同期 | `bash scripts/build-runtime-plugins.sh --check` | up to date |
| 配布物の妥当性 | `bash scripts/validate-runtime-plugins.sh` | passed（`claude plugin validate` を含む） |
| リンク | `python3 scripts/check-markdown-links.py` | valid |
| 予算 | `check-skill-frontmatter.py` | claude 6,041 / 8,000、codex 5,629 / 8,000、frontmatter 合計 10,782 / 11,200 |

## 3.2 例のコードの実行確認

| 言語 | 確認方法 | 結果 |
| --- | --- | --- |
| Python 3.12 | 実行 | `assert_never` を使った網羅的分岐、失敗集計の例がいずれも動作 |
| TypeScript 5 | `tsc --strict --noEmit` | 型検査を通過 |
| TypeScript 5（反例） | ケース追加・キー欠落版を型検査 | `TS2322`（`never` への代入不可）と `TS1360`（`satisfies` の欠落検出）が出ることを確認 |
| PHP 8.3 | 実行 | backed enum + `match`、first-class callable の dispatch、`readonly` が動作。`\UnhandledMatchError` の送出も確認 |
| PHP 8.3 + PHPStan 2.2 | `analyse --level 5 / max` | 下表のとおり |

### PHPStan の実測（本 PR で新たに判明した事実）

| 書き方 | level 5 | level max |
| --- | --- | --- |
| `match`（`default` なし） | `match.unhandled` で検出 | 同左 |
| その場に書いた連想配列 | 検出なし | `offsetAccess.notFound` で検出 |
| 外部から渡した対応表（`array<string, string>`） | 検出なし | **検出なし** |

当初 `references/language-notes.md` には「連想配列へ移すと検査が効かなくなる」と書いていたが、
その場に書いた連想配列は level max なら検出されるため不正確だった。実測に合わせて表へ差し替えた。

**この表が測っているのは「型情報を伴わずに実行時ロードした場合」に限られる**（3 行目は
`array<string, string>` という幅の広い型で対応表を受け取る形）。外部化一般について
「静的解析の視界から外れる」と言えるわけではない。スキーマから型・定数を生成してビルド時に
取り込む経路をとれば、外部化しても静的検査は維持できる。この限定は round 3 で反映した（3.6）。

## 3.3 受け入れ条件の判定における注記

- **AC-9(b)**: SKILL.md 本文に残った言語名は、参照節の
  「`references/language-notes.md` — Python / JavaScript / TypeScript / PHP での手段」1 行のみ。規範ではなく
  参照先の内容説明であり、条件を満たすと判断した
- **AC-8**: 検出された唯一の「使わない」表現は「不透明なコード値を使わない」であり、
  `if` / ループ / 列挙型に対する無条件の禁止ではない。本文冒頭に「この Skill は `if` / ループ /
  定数の**禁止規則ではない**」と明記している

## 3.4 cross-review round 1 での事実訂正

- **一括処理の失敗方針（SKILL.md MUST）**: 初版は「最初の失敗で打ち切らない」を全一括処理へ
  無条件に課していた。原子性を持つ処理・安全性検査・不正入力が後続へ波及する処理では継続の
  ほうが危険なため、MUST の適用範囲を「項目どうしが独立に処理できる場合」へ限定し、
  fail-fast / rollback を MAY へ追加した（打ち切り位置・理由・巻き戻し範囲の記録が条件）
- **PHP の一括演算（language-notes.md）**: 初版の「PHP に一括演算の基盤はない」は拡張・外部
  ライブラリまで否定する主張になっていた。第 1 部の調査で確認できたのは標準ランタイムに
  ネイティブな基盤がないことなので、「PHP 標準に一括演算の基盤はない」へ範囲を限定し、
  拡張を採用する場合は計測して選ぶ旨を追記した
- **配布物の版数**: `plugins/ndf-codex/.codex-plugin/plugin.json` は
  `scripts/build-runtime-plugins.sh` の生成対象外（`write_codex_mcp_manifest` は
  `plugins/mcp/codex/*` 専用で、`ndf-codex` は `skills/` のみ同期される）であり、手で維持する
  ファイルである。7.0.0 / 24 skills のまま取り残されていたため 7.1.0 / 25 skills へ更新した

## 3.5 cross-review round 2 での事実訂正

- **pandas `.apply()` の記述（language-notes.md）**: 初版は「行ごとの呼び出し」と断定していたが、
  `DataFrame.apply` は既定 `axis=0` で列単位、`axis=1` で行単位であり、ufunc を渡すなど内部で
  一括実行に落ちる経路もある。「Python の関数を要素・行・列のいずれかの単位で呼ぶ経路である
  限りは一括演算ではない」と書き換えた。この節の主題は「置き換えても実行の実体が変わらないこと
  がある」点なので、pandas の API 仕様の解説には広げていない
- **ChatGPT 生ログの脚注（`issue-38-chatgpt-response.md`）**: 脚注 `[1]` のリンクテキストが
  `"Cat's Cradle"`（カート・ヴォネガットの小説）になっているが、URL は catb.org で、正しい出典は
  Eric S. Raymond, *The Art of Unix Programming*。**生ログは記録価値のため改変しない**方針を取り、
  ファイル冒頭に注記ブロックを、該当箇所に短い注記を追加して、検証済みの事実関係は
  「1.4 事実確認の記録」を参照するよう誘導した
- **マーケットプレイス定義の版数**: `.claude-plugin/marketplace.json` の `ndf` エントリの
  `description` が `v7.0.0` / `26 focused NDF skills` のまま取り残されていた。このファイルも
  `scripts/build-runtime-plugins.sh` の生成対象ではなく手で維持するファイルで、
  `validate-runtime-plugins.sh` は JSON 妥当性と `source` の実在しか見ないため CI をすり抜けていた。
  `plugins/ndf-shared/manifests/claude-skills.txt` の実数 27 に合わせ `v7.1.0` / `27 focused NDF skills`
  へ更新した

## 3.6 cross-review round 3 での事実訂正

- **外部化と静的解析の関係（SKILL.md「データ化の前提」/ language-notes.md）**: 初版は
  「**外部化した時点で、その対応表は静的解析の視界から外れる**」と書いていたが、これは
  PHPStan の実測（`array<string, string>` を渡した 3 行目）が支える範囲を超えた一般化だった。
  実測が示すのは「型情報を伴わずに実行時ロードした場合」に限られる。スキーマから型・定数を
  **生成**してビルド時に取り込めば、外部化しても静的検査は維持できる。次のとおり直した。
  - SKILL.md: 記述を「型情報を伴わずに実行時ロードすると〜」へ限定し、選択肢を
    **(1) スキーマから型・定数を生成してビルド時に取り込む → (2) 生成できないならスキーマ検証で
    埋める → (3) どちらもできないなら外部化しない** の順に提示する形へ変更。生成が最良の選択肢
    なので先頭に置いた。表現は言語非依存の語（コード生成 / 型生成 / ビルド時）に留めている
  - language-notes.md: PHPStan 実測表の直後に「この表が測っているのは型情報を伴わずに実行時
    ロードした場合である」と明記し、生成による静的検査の維持を補記
  - 「先に読む: この Skill が禁じていないこと」の表の括弧書きも同じ限定に揃えた
- **対象言語の不整合（プラン / language-notes.md）**: 「2.1 目的」と AC-9(c) は対象を
  **Python / JavaScript / TypeScript / PHP の 4 言語**としていたのに、AC-4 と
  `references/language-notes.md` は JavaScript を欠いた 3 言語のままで完了扱いになっていた。
  **スコープを縮めるのではなく JavaScript を追加する方向で揃えた**（Skill の対象読者には JS の
  みで書くコードが多く、目的側の記述が本来の意図であるため）。
  - `references/language-notes.md`: 対応表に JavaScript 列を追加し、`## JavaScript` 節を新設。
    要点は「手段は TypeScript の節と同じだが、**型注釈がないため静的な網羅性検査が効かない**」
    こと。その帰結として (a) MAY の「静的に網羅性を検査できる分岐」の条件が成立せず未知の
    ケースで即時失敗させる必要があること (b) スキーマ検証の必要性が TypeScript より高いこと
    を、悪い例 / 良い例 1 組とあわせて簡潔に記述した
  - プラン: 1.6 の見出し・対応表・AC-4・Task 4・2.8・3.3 の言語表記を 4 言語へ揃えた

## 3.7 cross-review round 4 での事実訂正

- **型生成とスキーマ検証を排他の分岐にしていた（SKILL.md「データ化の前提」/
  language-notes.md）**: round 3 で入れた 3 段の選択肢は「(1) 型・定数を生成する →
  (2) **生成できないなら**スキーマ検証で埋める」と書いており、1 と 2 が排他に読めた。しかし
  **型定義だけをビルド時に生成し、データ実体は実行時にロードする**構成では、型生成ができて
  いてもロード境界のスキーマ検証は依然として必須である。この書き方のままでは「型を生成した
  からスキーマ検証は不要」と誤読され、ロード境界で無検証のキャストを書く誘導になりうる。
  **分岐の軸を「生成できるか」から「データを実行時にロードするか」へ組み替えた。**
  - SKILL.md: (1) **データごとビルド時に組み込める**なら型・定数を生成して取り込む →
    (2) **データを実行時にロードする**ならロード境界をスキーマ検証で守る（型を生成していても
    同じで、**型生成は実行時のスキーマ検証の代わりにならない**）→ (3) どちらも満たせないなら
    外部化しない、へ変更。語は言語非依存（ロード境界 / 実行時ロード / 型生成）に留めた
  - language-notes.md: PHP 節の「生成できないときにだけスキーマ検証で埋める」も同じ誤りを
    含んでいたため訂正し、Python / TypeScript / JavaScript / PHP の各節に「型注釈・型生成は
    実体を検査しない」ことを示す 1〜2 行の具体例（`cast` / `as` / JSDoc / `@var`）を追加した

## 3.8 cross-review round 8 での版数・Skill 数の取り残しの解消

第 4 部の決定で版を v7.1.0 から v8.0.0 へ、Skill 数を 31 個から 30 個へ変えたが、
`scripts/build-runtime-plugins.sh` の生成対象外のファイルに v7.1.0 / 旧 Skill 数が残っていた。
round 1・round 2 で同じ 2 ファイルを同じ理由で指摘されており、3 度目の再発である。

直した箇所（現在値を示すべき記述のみ）:

| ファイル | 内容 |
| --- | --- |
| `.claude-plugin/marketplace.json` | `ndf` の description を `v8.0.0` / `26 focused NDF skills` へ |
| `plugins/ndf-codex/.codex-plugin/plugin.json` | `version` と description を `8.0.0` / `24 focused NDF skills` へ |
| `plugins/ndf-codex/README.md` | プラグインキャッシュのパス例 2 箇所と `codex plugin list` の出力例を `8.0.0` へ |
| `plugins/ndf-kiro/README.md` | `.kiro/agents/ndf.json` の description 例を `v8.0.0` へ |
| `plugins/ndf-shared/skills/ndf-policies/SKILL.md` | 「v7.1.0 の `ndf-policies` を参照」を v7.0.0 へ。**v7.1.0 は配布していない中間の版**であり、参照先として成立しない |
| `plugins/ndf-{claude,codex,kiro}/README.md` | 「移行先の対応表は `ndf-policies` にある」は v8.0.0 での削除により成立しなくなったため、root README の「NDF v7.0.0 の主な変更（非互換）」へ誘導 |
| `docs/specifications/ndf-skill-inventory.md` | 「Skill 数は 30 個で変わらない」を、v7.1.0 の 31 個から 30 個へ戻る旨へ訂正。予算比較表に v7.1.0（未配布）列を追加 |

据え置いた箇所: 過去の事実を述べる記述（`ndf-policies` の `/ndf:safe-refactoring` 移行対応表、
root README の「v6.1.0 当時の名称」注記、棚卸台帳の v6.1.0 / v7.0.0 節、`skills/README.md` の
v7.0.0 時点の実測値）と、`issues/` 配下の過去の計画文書。本節より上の round 1 / round 2 の記録も
その時点の事実として残す。`docs/presentations/2026-08-06-ai-plugins-intro.md` は日付を持つ
勉強会資料で、v6.0.0 以前の Skill 名と個数を載せたまま本 PR より前から据え置かれているため触らない。

再発防止として `scripts/validate-runtime-plugins.sh` に突き合わせ検査を追加した。Claude 版
`plugin.json` の `version` を基準に、(a) Codex 版 `plugin.json` の `version`、(b) marketplace と
両 `plugin.json` の description に書かれた `(vX.Y.Z)`、(c) description の Skill 数と
`manifests/<runtime>-skills.txt` の実数、の 3 つを検査する。plugin family は既存の検出結果を
使い回すため、family を足しても検査対象から漏れない。

## 3.9 未了

- [ ] 3 ランタイムでの発動実測（`docs/specifications/ndf-skill-inventory.md` への記録）。
  配布後に利用実績が出てから測定する。台帳には「未測定」として行を追加済み
- [ ] `plan-to-spec` による確定仕様化。cross-review 通過後に実施する
