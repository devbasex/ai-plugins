# 兆候と手法の呼び名

**このファイルが呼び名を持つ唯一の場所である。** 兆候と手法は、`refactoring` を単独で使う
ときにも、`cross-refactoring` が複数の CLI へ提案させるときにも同じ名前で指す。

**識別子を持つのは、複数の CLI が同じ箇所を同じ名前で呼ぶためである。** 呼び名が揃わないと、
同じ提案が別物として残り、重複排除が効かない。

**説明はここに書かない。** 兆候の説明は [code-smells.md](code-smells.md)、手法の説明は
[refactoring-catalog.md](refactoring-catalog.md) にある。説明は言語や事例で伸びるが、
呼び名は固定である。

## 兆候

| 識別子 | 日本語の名前 |
| --- | --- |
| `long_method` | 長すぎるメソッド |
| `large_class` | 肥大したクラス |
| `duplication` | 重複 |
| `long_parameter_list` | 長い引数リスト |
| `feature_envy` | 他クラスへの過度な関心 |
| `primitive_obsession` | 基本型への固執 |
| `magic_value` | マジックナンバー・文字列 |
| `deep_nesting` | 深いネスト |
| `dead_code` | デッドコード |
| `circular_dependency` | 過度な相互依存 |
| `inconsistent_naming` | 一貫しない命名 |
| `swallowed_exception` | 例外の飲み込み |
| `conditional_chain` | 条件分岐の連鎖 |
| `scattered_config` | 設定の散在 |
| `embedded_business_rule` | 業務ルールの埋め込み |
| `one_by_one_iteration` | 一件ずつの反復 |
| `unvalidated_externalization` | 検証のない外部化 |

## 手法

| 識別子 | 日本語の名前 |
| --- | --- |
| `extract_method` | メソッドの抽出 |
| `rename` | 変数・関数・クラスの改名 |
| `introduce_parameter_object` | 引数オブジェクトの導入 |
| `introduce_value_object` | 値オブジェクトの導入 |
| `flatten_conditional` | 条件分岐の平坦化 |
| `replace_conditional_with_polymorphism` | 多態による分岐の置き換え |
| `replace_with_lookup_table` | 対応表への置き換え |
| `replace_with_bulk_operation` | 一括処理への置き換え |
| `extract_strategy` | 戦略の切り出し |
| `move_responsibility` | 責務の移動 |
| `fix_dependency_direction` | 依存の向きを整える |
| `split_into_pipeline` | 処理の連鎖への分解 |
| `remove_dead_code` | 死んだコードの削除 |
| `consolidate_duplication` | 重複の共通化 |
| `introduce_named_constant` | 名前付き定数・列挙の導入 |
| `propagate_exception` | 呼び出し元へ伝える |
| `centralize_configuration` | 定義を 1 箇所へ寄せる |
| `validate_at_boundary` | スキーマと版を与え、読み込み境界で検証する |

## 手法ごとの差分予算の倍率

**手法によって固定費が違う。** 新しい定義を作って呼び出し側を書き換える手法は、抽出した
本体に加えて呼び出し側の書き換え・取り込みの追加・引数の受け渡しが固定費として乗る。
提案の時点では見えにくい。

**倍率は見積に掛ける。** 予算超過として落ちた 4 件はいずれも抽出で、見積の 2.03〜2.31 倍
だった。範囲の逸脱ではなく、倍率 2 をわずかに超えただけである。範囲外を触った実測例は
見積の 4 倍であるため、倍率 3 でも逸脱を取り逃がさない。

| 識別子 | 倍率 |
| --- | ---: |
| `extract_method` | 3 |
| `rename` | 2 |
| `introduce_parameter_object` | 3 |
| `introduce_value_object` | 3 |
| `flatten_conditional` | 2 |
| `replace_conditional_with_polymorphism` | 2 |
| `replace_with_lookup_table` | 2 |
| `replace_with_bulk_operation` | 2 |
| `extract_strategy` | 3 |
| `move_responsibility` | 3 |
| `fix_dependency_direction` | 2 |
| `split_into_pipeline` | 3 |
| `remove_dead_code` | 2 |
| `consolidate_duplication` | 3 |
| `introduce_named_constant` | 2 |
| `propagate_exception` | 2 |
| `centralize_configuration` | 2 |
| `validate_at_boundary` | 2 |
