> **注記（本リポジトリによる追記。以下は ChatGPT の回答の生ログであり、本文は改変していない）**
>
> - 生ログとしての記録価値を保つため、誤りが含まれていても本文は書き換えていない。
> - 脚注 `[1]` のリンクテキスト `"Cat's Cradle"` は誤り。正しくは Eric S. Raymond,
>   *The Art of Unix Programming*（URL `https://www.catb.org/esr/writings/taoup/html/ch01s06.html`
>   自体は正しい）。`"Cat's Cradle"` はカート・ヴォネガットの小説であり、Rule of
>   Representation の出典ではない。
> - 検証済みの事実関係は [`issue-38-coding-skill-plan.md`](issue-38-coding-skill-plan.md) の
>   「1.4 事実確認の記録」にまとめてある。本ファイルではなくそちらを参照すること。

## 調査結果

あります。しかも、草稿の中心にある **「判断や知識をコードではなくデータへ移し、処理系を単純にする」** という発想には、かなり明確な理論的系譜があります。

ただし、草稿全体と一対一で一致する単一の開発理論は見当たりませんでした。実態としては、次の理論や設計手法を統合したものです。

| 既存の理論・手法                                     | 草稿との近さ | 対応する主張                            |
| -------------------------------------------- | -----: | --------------------------------- |
| **Rule of Representation**                   |  非常に近い | 知識・判断をコードではなくデータへ移す               |
| **Data-Oriented Programming**                |  非常に近い | コードとデータを分離し、データ変換として処理を構成する       |
| **Table-Driven Design / Decision Tables**    |  非常に近い | `if` や `switch` を表・ルール・マッピングで表現する |
| **Policy as Code / Rules Engine / DMN**      |     近い | ビジネス判断を独立したポリシーとして管理する            |
| **Declarative Programming**                  |     近い | 「どう処理するか」より「何を実現するか」を記述する         |
| **Functional Core, Imperative Shell**        |     近い | 中核を純粋なデータ変換にし、副作用を境界へ追い出す         |
| **Array Programming / Dataflow / MapReduce** |     近い | 明示的なループを減らし、一括・並列処理可能にする          |
| **Observability-Driven Development**         |     近い | 判断、結果、失敗、環境を構造化データとして計測する         |
| **Event Sourcing**                           | 部分的に近い | アプリケーションの状態変化や挙動をイベントとして保存する      |

---

## 1. もっとも直接的なのは「Rule of Representation」

Unix 哲学には、次の原則があります。

> “Fold knowledge into data, so program logic can be stupid and robust.”

つまり、**知識をデータへ畳み込み、プログラムロジックを単純で堅牢にする**という考え方です。草稿の「コードは基本原則のみ」「判断やロジックは極力データとして表現する」は、この原則とほぼ同じ方向を向いています。([Cat's Cradle][1])

> 注記: 上のリンクテキスト `Cat's Cradle` は誤り。出典は Eric S. Raymond, *The Art of Unix Programming*（リンク先 URL は正しい）。冒頭の注記を参照。

たとえば、

```typescript
if (customer.rank === "gold") {
  discount = 0.2;
} else if (customer.rank === "silver") {
  discount = 0.1;
} else {
  discount = 0;
}
```

を、

```typescript
const discountRates = {
  gold: 0.2,
  silver: 0.1,
  bronze: 0,
} as const;

const discount = discountRates[customer.rank];
```

とするのは、まさに **プログラムに埋め込まれた知識をデータ構造へ移す**設計です。

この原則は、草稿全体の「祖先」と呼んでよいくらい近いです。

---

## 2. 理論名として最も近いのは Data-Oriented Programming

Yehonathan Sharvit が整理している **Data-Oriented Programming（DOP）** は、次の原則を掲げています。

* コードとデータを分離する
* データを第一級の値として扱う
* 汎用的なデータ構造を利用する
* データを不変として扱う
* データ表現とデータスキーマを分離する

JavaScript、Pythonなど特定の言語に依存しない考え方として説明されており、今回の language-agnostic な skill と相性がよいです。([Yehonathan Sharvit][2])

ただし、DOPでいう「データ」は、単にすべてを辞書やJSONにすることではありません。重要なのは、

```text
データ
  ↓
汎用的で予測可能な変換処理
  ↓
新しいデータ
```

という形にシステムを近づけることです。

草稿はDOPよりさらに、

* 分岐のデータ化
* ループの排除
* 並列化可能性
* 実行時の観測可能性
* リファクタリング規範

まで含んでいます。そのため、**DOPを基礎に、ほかの設計理論を合成したもの**と考えるのが適切です。

なお、似た名前の **Data-Oriented Design** は、ゲーム開発や高性能計算の文脈で、メモリ配置、キャッシュ効率、データアクセスパターンを重視する意味でも使われます。今回の思想には重なる部分がありますが、名称としては Data-Oriented Programming の方が近いです。([Data-Oriented Design][3])

---

## 3. 「分岐をデータとして表現する」は Table-Driven Design

`if` や `switch` の連鎖を、表、マッピング、決定表として表現する方法は、一般に次の名前で扱われます。

* Table-Driven Programming
* Table-Driven Design
* Decision Table
* Dispatch Table
* Rules Engine

LLVMの **TableGen** も、命令セットなどの大量のドメイン知識を宣言的なレコードとして記述し、そこからコードや各種出力を生成する仕組みです。LLVM自身も、手書きコードでは保守困難になる情報をレコードとして保持することを目的の一つに挙げています。([LLVM][4])

ビジネスルールについては、OMGの **Decision Model and Notation（DMN）** がかなり近いです。DMNでは、業務上の判断を決定表として表現し、非エンジニアにも読める形にしながら、検証・実行できることを目指しています。([OMG][5])

また、Open Policy Agentは、認可や運用ルールなどの「ポリシー」をアプリケーション本体から分離し、独立して読取り、分析、版管理、配布できるようにします。([Open Policy Agent][6])

したがって、草稿の分岐に関する考え方は、次のように整理できます。

| 分岐の種類           | 適切な表現                           |
| --------------- | ------------------------------- |
| 値から値への単純な対応     | `dict`、`Map`、lookup table       |
| 処理方式の切り替え       | dispatch table、handler registry |
| 条件の組み合わせが多い業務判断 | decision table、DMN、rules engine |
| 認可、制約、組織ポリシー    | policy engine                   |
| 時系列で状態が変化する処理   | state machine、statechart        |
| 閉じた少数の型分岐       | 型付き `switch`、pattern matching   |

特に注文状態、審査状態、ワークフローのような時間的な振る舞いは、単なる関数マップよりも state machine や statechart の方が適しています。Statechartsは、階層、並行状態、イベント通信を含む複雑な状態遷移を表現するために設計されています。([Weizmann Institute of Science][7])

---

## 4. 「ループを減らす」は Array Programming と Dataflow Programming

ループを直接書く代わりに、配列や行列全体への演算として問題を表現する思想は、APLなどに代表される **Array Programming** にあります。

APLを提唱した Kenneth Iverson の考え方では、ベクトル、行列、高次元配列に対する演算を使い、個々の要素を逐次操作する詳細を表面から消していきます。

また、MapReduceでは、利用者は `map` と `reduce` に相当する処理を記述し、分割、スケジューリング、通信、障害処理、並列実行をランタイムへ委ねます。これは草稿の「並列化しない場合も、並列化へ切り替え可能なロジック」という方向に近いです。

ただし、草稿の次の記述は修正した方がよいです。

> ベクトル、行列の問題として対応する（`.map()`、`.apply()`など）

`.map()` や `.apply()` は、必ずしもベクトル演算でも並列処理でもありません。

JavaScriptの `Array.prototype.map()` は同期的にコールバックを順番に適用するAPIであり、それ自体はCPU並列処理ではありません。([MDN Web Docs][8])

また、NumPyの `np.vectorize()` も、公式ドキュメント上「主として利便性のため」であり、実装は本質的にループで、性能目的のベクトル化ではないと説明されています。([NumPy][9])

skillでは、少なくとも次を区別する必要があります。

| 種類          | 例                          | 性質                   |
| ----------- | -------------------------- | -------------------- |
| 高階反復        | JS `map`、Python `map`      | ループの記述方法を変えただけ       |
| ネイティブベクトル演算 | NumPy ufunc、broadcasting   | C、SIMD、GPU等で一括実行可能   |
| バッチ処理       | SQL、bulk API               | 呼出し回数やI/Oをまとめる       |
| 並行処理        | `Promise.all`、async task   | 待ち時間を重ねる。CPU並列とは限らない |
| 並列処理        | multiprocessing、worker、JAX | 複数CPU/GPUなどで同時実行     |
| 分散データフロー    | Beam、Spark、MapReduce       | 分割、再試行、集約を実行基盤が管理    |

したがって、目指すべきなのは「for文を見つけたらmapへ置き換える」ことではありません。

より正確には、

> 同種の独立したデータ変換については、逐次制御として書く前に、バッチ演算、ベクトル演算、データフロー、並行・並列実行として表現できないか検討する。

となります。

---

## 5. 「振る舞いをデータとして表現する」は Observability と Event Sourcing

OpenTelemetryでは、システムから発生する telemetry を、システムの振る舞いを示すデータとして扱い、主に traces、metrics、logs へ整理します。Semantic Conventionsを使うことで、異なるコードやサービスの間でも、属性名や意味をそろえて相関・集約できるようにします。([OpenTelemetry][10])

草稿の、

> アプリ自体の挙動、周辺環境、ユーザーなどが常に計測可能であること

という主張は、単なる「ログをたくさん出す」ではなく、次のような **構造化された判断記録** として設計すると強くなります。

```json
{
  "event_name": "discount_decided",
  "customer_id": "cus_123",
  "input": {
    "rank": "gold",
    "purchase_amount": 120000
  },
  "decision": {
    "discount_rate": 0.2,
    "rule_id": "customer-rank-discount",
    "rule_version": "2026-08-01",
    "reason": "customer rank is gold"
  },
  "execution": {
    "code_version": "a13fd82",
    "duration_ms": 4,
    "trace_id": "..."
  }
}
```

OpenTelemetryのイベント設計でも、属性はフィルタ、グループ化、集計、相関に使えるよう設計することが推奨されています。([OpenTelemetry][11])

さらに状態変更そのものをイベント列として保存する **Event Sourcing** は、アプリケーションの状態変化をデータ化する考え方です。過去のイベントから状態を再構築したり、過去時点を再現したりできます。([martinfowler.com][12])

ただし、Event Sourcingは通常の監査ログよりはるかに大きなアーキテクチャ上の選択です。すべてのアプリへ適用するのではなく、意思決定の再現や履歴が重要な領域に限定する方がよいでしょう。

---

## 草稿の独自性

調査した範囲では、以下を一つにまとめた広く定着した理論名は見当たりませんでした。

1. ルールや判断をデータへ移す
2. 処理をデータ変換として構成する
3. 逐次ループよりバッチ・ベクトル・データフローを優先する
4. 並列化可能性を保つ
5. 判断理由、結果、失敗を構造化データとして観測する
6. これを実装だけでなくリファクタリング規範として利用する

したがって、思想自体の各部品は既存ですが、**それらを言語横断のコード生成・レビュー・リファクタリングskillとして統合する部分には、十分な独自性があります**。

名称を付けるなら、次のあたりが内容を比較的正確に表します。

* **Observable Data-Oriented Development**
* **Data-First Observable Programming**
* **Policy and Data-Oriented Programming**
* **Observable Declarative Development**

個人的には **Observable Data-Oriented Development（観測可能なデータ指向開発）** が最もしっくりきます。

---

## ただし「if・for・enum禁止」にしてはいけない

ここはかなり重要です。

草稿をそのままskillへ入れると、AIエージェントが表面的に、

* `if` を辞書アクセスへ置き換える
* `for` を `.map()` へ置き換える
* enumを文字列へ置き換える
* 設定ファイルへ大量のロジックを押し込む

というリファクタリングをし始める可能性があります。

これは、むしろコードを分析しにくくする場合があります。

### `if` や `switch` は、それ自体が悪いわけではない

次のようなガード節は、動的なhandler mapより明確です。

```typescript
function withdraw(account: Account, amount: number): Result {
  if (amount <= 0) {
    return { ok: false, reason: "amount_must_be_positive" };
  }

  if (account.balance < amount) {
    return { ok: false, reason: "insufficient_balance" };
  }

  return executeWithdrawal(account, amount);
}
```

また、TypeScriptのdiscriminated unionと `never` を使った `switch` は、すべてのケースが処理されているかをコンパイル時に検証できます。これを動的な文字列辞書に置き換えると、静的解析能力が下がることがあります。([TypeScript][13])

したがって、

> `if`、`switch`を避ける

ではなく、

> 変化頻度の高い業務判断や多数の条件組み合わせを、ネストした制御構文へ埋め込まない。小さく閉じた型分岐、ガード節、不変条件の検査には明示的な分岐を使用してよい。

とするのが適切です。

### データ化しすぎると、設定ファイルがプログラミング言語になる

条件、優先順位、参照、式、継承、デフォルト、再試行などを設定データへ追加していくと、やがて独自DSLになります。

LLVMのTableGenのドキュメントでも、DSLやカスタムバックエンドが複雑化し、初見の開発者にとって理解困難になる問題が指摘されています。([LLVM][4])

判断をデータ化する場合は、少なくとも次が必要です。

* スキーマ
* 型またはバリデーション
* バージョン
* 変更履歴
* 競合・到達不能ルールの検出
* テスト
* マイグレーション
* 誰が何を変更したかという監査
* 実行時に適用されたルールIDと理由の記録

データへ移動しただけで、複雑性が消えるわけではありません。**複雑性を検査・可視化・変更可能な形式へ移す**ことが目的です。

---

## 定数・enumについては分類が必要

「コード中の定数、enumを極力使用しない」は、やや広すぎます。

Pythonのenumは、値へ意味のあるシンボル名を割り当てる仕組みです。適切に使えば、コード値を直接書くより意味を明確にできます。([Python documentation][14])

定数は、次のように分類するのがよいです。

| 値の性質          | 置き場所                      |
| ------------- | ------------------------- |
| 数学的・技術的な不変条件  | コード内の定数                   |
| 閉じた状態集合・プロトコル | enum、literal union、schema |
| 頻繁に変わる業務ルール   | decision table、policy、設定  |
| 環境ごとに変わる値     | deployment config、環境変数    |
| ユーザーが管理するカテゴリ | DBのマスタ・参照テーブル             |
| 表示名・文言        | i18n、コンテンツデータ             |
| セキュリティ上の絶対制約  | コード、型、schema、policyの複数層   |

特に、

```typescript
status === 3
```

のような不透明なコード値は避けるべきですが、

```typescript
type OrderStatus =
  | "pending"
  | "confirmed"
  | "shipped"
  | "cancelled";
```

のような型付きの意味的識別子まで排除する必要はありません。

「文字列にすればデータになる」というわけでもなく、型のない文字列は typo や未処理ケースを実行時まで発見できない危険があります。

---

## 基本方針は、こう直すと理論的に強くなります

草稿の中核を、次のように再定義することをおすすめします。

> **変化する判断や業務知識は、検証可能・版管理可能なデータまたはポリシーとして表現する。安定した機構、不変条件、型、境界はコードとスキーマとして表現する。処理は明示的な入力から出力へのデータ変換として構成し、依存関係、順序、副作用を明らかにする。各判断、結果、失敗は、理由、適用ルール、バージョン、実行コンテキストを伴う構造化データとして観測・再現可能にする。**

「コードよりもデータ」だけだと、何でも設定ファイルへ追い出す誤解が起きます。

より正確には、

> **変化する知識はデータへ。安定した機構と不変条件はコードへ。両方をスキーマと計測で接続する。**

という原則です。

これはUnixのRule of Representation、Data-Oriented Programming、宣言的プログラミング、Policy as Code、Observabilityの長所を、かなりきれいに統合しています。

---

## skillへ落とす際のルール案

禁止規則ではなく、`MUST / SHOULD / MAY` に分けると運用しやすくなります。

### MUST

* 外部化したルール、設定、マスタデータにはスキーマとバージョンを持たせる
* 判断結果には `rule_id`、`rule_version`、`reason` を記録できるようにする
* バッチ処理では、成功件数だけでなく全失敗の件数・種類・対象を報告する
* 外部I/O、副作用、時刻、乱数は境界として明示する
* 不変条件やセキュリティ制約を、変更可能な設定だけに依存させない
* 閉じた状態集合は、型またはスキーマによって網羅性を検査する

### SHOULD

* 条件の組み合わせが多い業務判断はdecision tableまたはpolicyとして表現する
* 純粋なデータ変換と、副作用を伴う処理を分離する
* 同種の大量処理では、逐次ループよりバッチ、ベクトル、データフローを検討する
* 並行化する処理は、冪等性、順序依存、再試行、タイムアウト、キャンセルを明示する
* ネストした条件分岐は、ガード節、状態機械、決定表、dispatch registryへの変更を検討する

### MAY

* 単純なガード節に `if` を使う
* 閉じた型の網羅的処理に `switch` やpattern matchingを使う
* 逐次依存、早期終了、ストリーム、メモリ制約のある処理に明示的なループを使う
* 不変条件、プロトコル、閉じた状態集合に定数やenumを使う

この形なら、思想を保ちながら、AIが機械的に「ifを辞書へ」「forをmapへ」置換する事故を防げます。

全体としては、単なるコードスタイルというより、**「業務知識・実行・観測をすべて構造化データで接続する開発方式」**としてまとめると、かなり筋の通った理論になります。次の段階では、この調査結果をもとに、実際の `skill.md` 用の規範、判定フロー、Python／TypeScriptの良い例・悪い例へ落とすのが自然です。

[1]: https://www.catb.org/esr/writings/taoup/html/ch01s06.html?utm_source=chatgpt.com "Basics of the Unix Philosophy"
[2]: https://blog.klipse.tech/dop/2022/06/22/principles-of-dop.html "Principles of Data-Oriented Programming | Yehonathan Sharvit"
[3]: https://www.dataorienteddesign.com/dodbook/node2.html?utm_source=chatgpt.com "It's all about the data - Data-oriented design"
[4]: https://llvm.org/docs/TableGen/ "TableGen Overview - LLVM"
[5]: https://www.omg.org/dmn/ "Decision Model and Notation™ (DMN™) | Object Management Group"
[6]: https://openpolicyagent.org/docs/philosophy "Philosophy | Open Policy Agent"
[7]: https://weizmann.elsevierpure.com/en/publications/statecharts-a-visual-formalism-for-complex-systems/?utm_source=chatgpt.com "Statecharts: a visual formalism for complex systems"
[8]: https://developer.mozilla.org/en-US/docs/Glossary/Callback_function?utm_source=chatgpt.com "Callback function - Glossary - MDN Web Docs - Mozilla"
[9]: https://numpy.org/doc/stable/reference/generated/numpy.vectorize?utm_source=chatgpt.com "numpy.vectorize — NumPy v2.5 Manual"
[10]: https://opentelemetry.io/docs/concepts/observability-primer/?utm_source=chatgpt.com "Observability primer"
[11]: https://opentelemetry.io/docs/specs/semconv/general/events/?utm_source=chatgpt.com "Semantic conventions for events"
[12]: https://martinfowler.com/eaaDev/EventSourcing.html?utm_source=chatgpt.com "Event Sourcing"
[13]: https://www.typescriptlang.org/docs/handbook/2/narrowing.html?utm_source=chatgpt.com "Documentation - Narrowing"
[14]: https://docs.python.org/ja/3/library/enum.html?utm_source=chatgpt.com "enum --- 列挙型のサポート"
