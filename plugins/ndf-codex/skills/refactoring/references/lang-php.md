# PHP での手段

[data-representation.md](data-representation.md) で選んだ表現を、PHP の機能へ対応付ける。
以下は **8.1 以降**を前提とする（8.0 以下は最終節）。

| 判定 | 手段 |
| --- | --- |
| 値から値への対応 | 連想配列 / `match` |
| 処理方式の切り替え | first-class callable 構文 `foo(...)` |
| 閉じた状態集合 | backed enum |
| 網羅性の静的検査 | PHPStan `match.unhandled` |
| 不変性 | `readonly` プロパティ |
| スキーマ検証 | JSON Schema / Valinor |
| 一括処理 | **一括入出力**（標準に一括演算の基盤はない） |
| 失敗の集計 | 失敗を集めて返す |

8.1 で backed enum・`readonly` プロパティ・first-class callable 構文が入り、**データ化しても
静的解析が効く**範囲が広がった。

## 網羅性の静的検査

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

**この表が測っているのは、型情報を伴わずに実行時ロードした場合である**（3 行目は
`array<string, string>` という幅の広い型で対応表を受け取る形）。この形にすると、変化する業務
ルールは定義ごと静的解析の視界から外れるため、「外部化してよい条件」（スキーマ・
バリデーション・版・テスト）が必須になる。**静的検査で守れなくなった分を、スキーマ検証で
埋める。**

一方、スキーマから backed enum や定数クラスを**生成**してビルド時に取り込めば、外部化しても
`match.unhandled` の検査は残る。ただし**データを実行時にロードするなら、型を生成していても
ロード境界の検証は要る**。`json_decode` の戻り値に `@var GeneratedShape` を付けるだけでは、
静的解析が信じるだけで実体は検査されない。`Valinor` などのマッパか JSON Schema 検証を通す。

値が固定で外部化する理由がないなら、`match` のまま置くほうが分析可能性は高い。

`default` を書くとケース追加漏れが検出されなくなる。**閉じた列挙に対する `match` に
`default` を置かない**（未知の入力を扱う必要があるなら、それは閉じた列挙ではない）。

## 処理方式の切り替え

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

## 一括処理は入出力の問題である

**PHP 標準に一括演算の基盤はない。** `array_map` / `array_column` はいずれも高階反復であり、
「反復の種類」表のどの行にも移動しない。数値計算向けの拡張やライブラリを導入すれば一括演算に
移れる場合はあるので、採用するなら**計測して効果を確かめてから選ぶ**。

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

大量データを扱うときは、`yield` による逐次生成でメモリを一定に保つ。これは「逐次依存・
メモリ制約下の明示的なループ」にあたり、**そのままでよい**。

## 不変性

```php
final class Discount {
    public function __construct(
        public readonly string $ruleId,
        public readonly string $ruleVersion,
        public readonly float  $rate,
    ) {}
}
```

判断の結果を `readonly` の値として持つと、記録に必要なルールの識別子と版を持ち回れる。

## 8.0 以下

backed enum・`readonly`・first-class callable 構文がいずれも使えない。代替は次のとおりで、
**いずれも静的検査は弱くなる**。

| 8.1+ | 8.0 以下の代替 |
| --- | --- |
| backed enum | クラス定数 + 値オブジェクト |
| `match` の網羅性検査 | `switch` + `default` で例外を投げる（実行時検出のみ） |
| `readonly` プロパティ | `private` + getter のみ |
| `foo(...)` | `Closure::fromCallable('foo')` |

閉じた状態集合は 8.1 以降と同じくコード側に置く。変わるのは**外部化した業務ルールの守り方**で、
生成した定数クラスに対する網羅性検査が効かないぶん、実行時のスキーマ検証への依存が高くなる。

## 出典

- [PHP 8.1 リリースアナウンス](https://www.php.net/releases/8.1/en.php)
- [PHPStan `match.unhandled`](https://phpstan.org/error-identifiers/match.unhandled)
