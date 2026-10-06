# 兆候・手法・観点の呼び名

**このファイルが呼び名を持つ唯一の場所である。** 兆候・手法・観点は、`refactoring` を単独で使う
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
| `test_coupled_to_internals` | テストが内部の詳細に依存している |
| `test_bypasses_module_boundary` | テストが 1 つの入口から全部を引く |
| `mock_targets_implementation_detail` | モックの対象が実装の詳細 |
| `divergent_change` | 変更の理由が 1 つに定まらない |
| `hidden_dependency` | 隠れた依存 |
| `data_clump` | いつも一緒に渡る値の組 |

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
| `extract_class` | クラスの抽出 |
| `split_module` | モジュールの分割 |
| `inline` | インライン化 |
| `change_signature` | シグネチャの変更 |
| `extract_test_helper` | テストの共通化 |

## 観点

**観点は兆候を探す入口であって、兆候そのものではない。** 提案の `smell` は兆候から選ぶ。観点の識別子を
`smell` に書いた提案は、代表の兆候へ写して扱う（重複排除の鍵を兆候に揃えるため）。識別子は兆候・手法と
重ならない。

| 識別子 | 説明 | 代表の兆候 |
| --- | --- | --- |
| `duplicated_knowledge` | 重複 — 同じ知識・同じ手順が 2 か所以上にある | `duplication` |
| `mixed_responsibility` | 責務の混在 — 1 つの関数・クラスが別々の理由で変わる | `divergent_change` |
| `branching` | 分岐の表し方 — 同じ条件の分岐が散らばる・種類ごとの分岐が伸び続ける | `conditional_chain` |
| `naming` | 名前 — 名前が中身と食い違う・同じものを別の名前で呼ぶ | `inconsistent_naming` |
| `dependency_direction` | 依存の向き — 下の層が上の層を読む・循環する・知りすぎる | `circular_dependency` |
| `testability` | テストの書きにくさ — 外部への依存や隠れた状態のせいで単体で試せない | `hidden_dependency` |
| `data_shape` | データの形 — 基本型の羅列・いつも一緒に渡る引数の組 | `data_clump` |
| `size` | 大きさ — 長すぎる関数・大きすぎるクラス・長い引数の列 | `long_method` |
