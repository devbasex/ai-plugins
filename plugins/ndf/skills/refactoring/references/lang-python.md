# Python での手段

[data-representation.md](data-representation.md) で選んだ表現を、Python の機能へ対応付ける。

| 判定 | 手段 |
| --- | --- |
| 値から値への対応 | `dict` / `Mapping` |
| 処理方式の切り替え | 関数を値に持つ `dict` |
| 閉じた状態集合 | `Enum` / `Literal` |
| 網羅性の静的検査 | mypy + `assert_never` |
| 不変性 | `@dataclass(frozen=True)` |
| スキーマ検証 | pydantic / jsonschema |
| 一括処理 | NumPy / pandas の**ベクトル化演算**（一括演算） |
| 失敗の集計 | 失敗を集めて返す |

## 網羅性の静的検査

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

外部化した値を実行時にロードするなら、型注釈を用意していてもロード境界の検証は要る。
`cast(Rates, json.load(f))` は mypy を黙らせるだけで実体を検査しないので、`pydantic` の
`TypeAdapter` や `jsonschema` を通してから使う。

## 一括演算とその反例

Python は一括演算の基盤（NumPy）を持つ数少ない言語である。ただし**「ループを消したこと」と
「一括演算になったこと」は別**である。

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
計測する。** 速くならないなら、「反復の実行方式」表の行を移動できていない。

## 失敗の集計

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

## 指標の測定

`/ndf:cross-refactoring` は提案の前に、既定で次のツールを版を固定して測る（`.ndf/code-metrics.json` で置き換えられる）。

| 指標 | ツール | 読み方 |
| --- | --- | --- |
| 認知的複雑度 | complexipy 8.0.1 | 入れ子の深さに重みを付ける。16 以上（既定の上限 15 を超える）から読みにくさを疑う |
| 循環的複雑度・分岐・文・引数・return の数 | Ruff 0.16.9（`C901` と `PLR0911` `PLR0912` `PLR0913` `PLR0915`） | mccabe はブール演算子を数えない。CC 11 以上（既定の上限 10 を超える）から分岐の多さを疑う |
| 関数とファイルの行数 | 標準ライブラリの `ast` と、空行を除いた行の数 | 長い関数は抽出の候補、長いファイルは責務の混在の候補 |
| 重複 | pylint 4.0.9 同梱の `symilar`（最小 8 行・docstring と import と関数の頭を除く） | 2 か所以上に同じ 8 行以上があれば、抽出と統合の候補 |

手で測るときは、閾値を 0 にして全関数の値を出させる。対象プロジェクトの設定は読ませない。

```bash
uvx --from ruff==0.16.9 ruff check --isolated --no-cache --exit-zero \
    --select C901,PLR0911,PLR0912,PLR0913,PLR0915 \
    --config lint.mccabe.max-complexity=0 --config lint.pylint.max-branches=0 \
    --config lint.pylint.max-returns=0 --config lint.pylint.max-args=0 \
    --config lint.pylint.max-statements=0 --output-format json src/
uvx --from complexipy==8.0.1 complexipy src/ --max-complexity-allowed 1000000 --no-ignore
uvx --from pylint==4.0.9 symilar -d 8 -i --ignore-docstrings --ignore-imports --ignore-signatures src/*.py
```

## 出典

- [numpy.vectorize — 性能目的ではないという公式注記](https://numpy.org/doc/stable/reference/generated/numpy.vectorize.html)
