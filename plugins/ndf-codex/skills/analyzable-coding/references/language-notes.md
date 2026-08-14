# 言語ごとの手段

`SKILL.md` の判定表で選んだ表現を、実際の言語機能へ対応付ける。**ここに無い言語でも判定は
変わらない。** 判定表の行から、その言語で同じ性質を持つ機能を自分で対応付ける。

## 対応表

| 判定 | Python | TypeScript | PHP（8.1+） |
| --- | --- | --- | --- |
| 値から値への対応 | `dict` / `Mapping` | `Record` + `as const` | 連想配列 / `match` |
| 処理方式の切り替え | 関数を値に持つ `dict` | ハンドラの `Record` | first-class callable 構文 `foo(...)` |
| 閉じた状態集合 | `Enum` / `Literal` | 判別可能ユニオン | backed enum |
| 網羅性の静的検査 | mypy + `assert_never` | `never` への代入 | PHPStan `match.unhandled` |
| 不変性 | `@dataclass(frozen=True)` | `readonly` / `as const` | `readonly` プロパティ |
| スキーマ検証 | pydantic / jsonschema | zod / JSON Schema | JSON Schema / Valinor |
| 一括処理 | NumPy / pandas（一括演算） | 一括 API / `Promise.all`（並行） | **一括入出力**（一括演算の基盤はない） |
| 失敗の集計 | 失敗を集めて返す | 結果型に集約 | 失敗を集めて返す |

## 共通: そのままでよいもの

3 言語に共通して、次は書き換えの対象ではない。判定表の「データ化しない」行にあたる。

```text
前提条件の検査（ガード節）        → 早期リターンのまま
閉じた型に対する網羅的な分岐      → 静的検査が効いている限りそのまま
```

**網羅性の静的検査が効いている分岐を、動的な対応表へ移さない。** 移した瞬間、ケースの
追加漏れが実行時まで発見できなくなる。これは 3 言語すべてで起きる。

## Python

### 網羅性の静的検査

```python
from enum import Enum
from typing import assert_never  # 3.11+（それ以前は typing_extensions）

class Rank(Enum):
    GOLD = "gold"
    SILVER = "silver"
    BRONZE = "bronze"

def label(rank: Rank) -> str:
    match rank:
        case Rank.GOLD:   return "ゴールド"
        case Rank.SILVER: return "シルバー"
        case Rank.BRONZE: return "ブロンズ"
        case _:           assert_never(rank)   # ケース追加漏れを mypy が検出
```

`Rank` に階級を足すと mypy が `assert_never` の行で型エラーを出す。**この検査が効いている
分岐は、`dict` へ移さない。**

### 一括演算とその反例

Python は一括演算の基盤（NumPy）を持つ数少ない対象言語である。ただし**「ループを消したこと」
と「一括演算になったこと」は別**である。

```python
# ❌ 逐次。要素ごとに Python のバイトコードを実行する
result = [x * 1.08 for x in prices]

# ❌ 高階反復。上と実行の実体は変わらない
result = list(map(lambda x: x * 1.08, prices))

# ❌ np.vectorize も同じ。公式が明言している
#    "provided primarily for convenience, not for performance.
#     The implementation is essentially a for loop."
result = np.vectorize(lambda x: x * 1.08)(prices)

# ✅ 一括演算。C 側で一括実行される
result = prices * 1.08
```

pandas の `.apply()` も、Python の関数を要素・行・列のいずれかの単位で呼ぶ経路である限りは
一括演算ではない（ufunc を渡すなど、内部で一括実行に落ちる経路もある）。**置き換えたら
計測する。** 速くならないなら、判定表の行を移動できていない。

### 失敗の集計

```python
def import_rows(rows):
    ok, failures = [], []
    for i, row in enumerate(rows):
        try:
            ok.append(parse(row))
        except ValueError as e:
            failures.append({"index": i, "reason": type(e).__name__, "id": row.get("id")})
    return ok, failures          # 呼び出し側が件数・種類・対象を報告できる
```

例外を握りつぶさず、最初の失敗で打ち切らない。ここは明示的なループでよい（逐次依存では
ないが、失敗の収集がある）。

## TypeScript

### 網羅性の静的検査

```typescript
type Shape =
  | { kind: "circle"; r: number }
  | { kind: "rect"; w: number; h: number };

function area(s: Shape): number {
  switch (s.kind) {
    case "circle": return Math.PI * s.r ** 2;
    case "rect":   return s.w * s.h;
    default: {
      const _exhaustive: never = s;   // ケース追加漏れをコンパイル時に検出
      return _exhaustive;
    }
  }
}
```

```typescript
// ❌ 対応表へ移すと、この検査が消える
const handlers: Record<string, (s: any) => number> = { circle: ..., rect: ... };
```

`Record<string, ...>` は任意の文字列を受けるため、ケースの追加漏れも綴り誤りも実行時まで
分からない。**判別可能ユニオンに対する分岐は、そのまま残す。**

### 変化する値の対応表

一方、**変化する業務ルール**は対応表が適する。キーを閉じた型に固定すれば静的検査も残る。

```typescript
const DISCOUNT_RATES = {
  gold: 0.2, silver: 0.1, bronze: 0,
} as const satisfies Record<Rank, number>;   // Rank に追加すると欠落を検出

const rate = DISCOUNT_RATES[rank];
```

### 並行と並列を混同しない

`Promise.all` は待ち時間を重ねるだけで、計算時間は減らない。CPU を使う処理を並列化するには
worker が要る。判定表の「並行処理」と「並列処理」は別の行である。

## PHP（8.1 以降）

8.1 で backed enum・`readonly` プロパティ・first-class callable 構文が入り、**データ化しても
静的解析が効く**範囲が広がった。以下は 8.1 以降を前提とする。

### 網羅性の静的検査

```php
enum Rank: string {
    case Gold = 'gold';
    case Silver = 'silver';
    case Bronze = 'bronze';
}

function label(Rank $rank): string {
    return match ($rank) {
        Rank::Gold   => 'ゴールド',
        Rank::Silver => 'シルバー',
        Rank::Bronze => 'ブロンズ',
    };
}
```

`match` は一致する腕がなければ実行時に `\UnhandledMatchError` を投げる。さらに **PHPStan が
`match.unhandled`（"Match expression does not handle remaining value"）として静的に検出する**。
`Rank` にケースを足すと、`default` を書いていない `match` が解析で落ちる。

```php
// ❌ 連想配列へ移すと、検査は弱くなる
$labels = ['gold' => 'ゴールド', 'silver' => 'シルバー'];
return $labels[$rank->value];
```

どこまで弱くなるかは、**対応表が静的に見えているか**で決まる（PHPStan 2.2 で実測）。

| 書き方 | level 5 | level max |
| --- | --- | --- |
| `match`（`default` なし） | `match.unhandled` で検出 | 同左 |
| その場に書いた連想配列 | 検出なし | `offsetAccess.notFound` で検出 |
| 外部から渡した対応表（`array<string, string>`） | 検出なし | **検出なし** |

3 行目が重要である。**変化する業務ルールを外部化すると、その対応表は定義ごと静的解析の
視界から外れる。** これは外部化の失敗ではなく、外部化とはそういうものである。だからこそ
`SKILL.md` の「データ化の前提」（スキーマ・バリデーション・版・テスト）が必須になる。
**静的検査で守れなくなった分を、スキーマ検証で埋める。**

逆に、値が固定で外部化する理由がないなら、`match` のまま置くほうが分析可能性は高い。

`default` を書くとケース追加漏れが検出されなくなる。**閉じた列挙に対する `match` に
`default` を置かない**（未知の入力を扱う必要があるなら、それは閉じた列挙ではない）。

### 処理方式の切り替え

```php
// first-class callable 構文。静的解析が参照先を追える
$handlers = [
    'csv'  => $this->importCsv(...),
    'json' => $this->importJson(...),
];
($handlers[$format] ?? throw new UnsupportedFormat($format))($payload);
```

文字列やコールバック配列（`'importCsv'` / `[$this, 'importCsv']`）で書くと解析が追えない。
**データ化しても分析可能性を落とさない書き方を選ぶ。**

### 一括処理は入出力の問題である

**PHP 標準に一括演算の基盤はない。** `array_map` / `array_column` はいずれも高階反復であり、
判定表のどの行にも移動しない。数値計算向けの拡張やライブラリを導入すれば一括演算に移れる
場合はあるので、採用するなら**計測して効果を確かめてから選ぶ**。

```php
// ❌ ループを array_map に変えても、実行の実体は変わらない
$totals = array_map(fn($o) => $o->amount * 1.08, $orders);
```

PHP で効くのは**一括入出力**、すなわち往復回数の削減である。

```php
// ❌ N+1。1 行ごとに問い合わせる
foreach ($orderIds as $id) { $rows[] = $repo->find($id); }

// ✅ 一括取得。往復が 1 回になる
$rows = $repo->findByIds($orderIds);

// ✅ 一括挿入。件数に比例した往復をなくす
$repo->insertMany($rows);          // 大量件数は chunk して分割する
```

大量データを扱うときは、`yield` による逐次生成でメモリを一定に保つ。これは判定表の
「逐次依存・メモリ制約下の明示的なループ」にあたり、**そのままでよい**。

### 不変性

```php
final class Discount {
    public function __construct(
        public readonly string $ruleId,
        public readonly string $ruleVersion,
        public readonly float  $rate,
    ) {}
}
```

判断の結果を `readonly` の値として持つと、記録に必要な `rule_id` / `rule_version` を
持ち回れる。

### 8.0 以下

backed enum・`readonly`・first-class callable 構文がいずれも使えない。代替は次のとおりで、
**いずれも静的検査は弱くなる**。

| 8.1+ | 8.0 以下の代替 |
| --- | --- |
| backed enum | クラス定数 + 値オブジェクト |
| `match` の網羅性検査 | `switch` + `default` で例外を投げる（実行時検出のみ） |
| `readonly` プロパティ | `private` + getter のみ |
| `foo(...)` | `Closure::fromCallable('foo')` |

このため 8.0 以下では、閉じた状態集合をデータ化する利点が小さい。**コード側に残す判断が
妥当になりやすい。**

## 出典

- [numpy.vectorize — 性能目的ではないという公式注記](https://numpy.org/doc/stable/reference/generated/numpy.vectorize.html)
- [PHP 8.1 リリースアナウンス](https://www.php.net/releases/8.1/en.php)
- [PHPStan `match.unhandled`](https://phpstan.org/error-identifiers/match.unhandled)
- [TypeScript Handbook — Narrowing](https://www.typescriptlang.org/docs/handbook/2/narrowing.html)
