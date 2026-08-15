# TypeScript での手段

[data-representation.md](data-representation.md) で選んだ表現を、TypeScript の機能へ対応付ける。
型注釈を持たない JavaScript は [lang-javascript.md](lang-javascript.md) を読む。

| 判定 | 手段 |
| --- | --- |
| 値から値への対応 | `Record` + `as const` |
| 処理方式の切り替え | ハンドラの `Record` |
| 閉じた状態集合 | 判別可能ユニオン |
| 網羅性の静的検査 | `never` への代入 |
| 不変性 | `readonly` / `as const` |
| スキーマ検証 | zod / JSON Schema |
| 一括処理 | 一括 API / `Promise.all`（並行） |
| 失敗の集計 | 結果型に集約 |

## 網羅性の静的検査

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

## 変化する値の対応表

一方、**変化する業務ルール**は対応表が適する。キーを閉じた型に固定すれば静的検査も残る。

```typescript
const DISCOUNT_RATES = {
  gold: 0.2, silver: 0.1, bronze: 0,
} as const satisfies Record<Rank, number>;   // Rank に追加すると欠落を検出

const rate = DISCOUNT_RATES[rank];
```

この表を実行時にロードするなら、型を生成していてもロード境界の検証は要る。

```typescript
// ❌ 型生成をスキーマ検証の代わりにしている。実体は何も検査されない
const rates = JSON.parse(raw) as Record<Rank, number>;

// ✅ ロード境界で検証してから使う（zod / JSON Schema）
const rates = RatesSchema.parse(JSON.parse(raw));
```

## 並行と並列を混同しない

`Promise.all` は待ち時間を重ねるだけで、計算時間は減らない。CPU を使う処理を並列化するには
worker が要る。「反復の実行方式」表の「並行処理」と「並列処理」は別の行である。

## 出典

- [TypeScript Handbook — Narrowing](https://www.typescriptlang.org/docs/handbook/2/narrowing.html)
