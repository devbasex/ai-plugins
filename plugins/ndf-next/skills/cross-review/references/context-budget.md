# メイン context 節約の工夫

## 「メイン」が指すもの

**この Skill の「メイン」は、収束ループを駆動している supervisor を指す。** `state.py` の
スケルトンを回している層のことで、人間と対話しているセッション（conductor）はメインに当たらない。
3 層で通すとき `cross-review` を回すのは設計と検査のフェーズの supervisor である（3 層の
責務は `development-workflow` の `references/agent-layers.md` にある）。3 層へ出さない進行
では、スケルトンを回している会話そのものがこれに当たる。

**修正は worker（`general-purpose` のサブエージェント）で行う。**
**supervisor の context window に diff を載せない。** 収束の判定は supervisor が持つ。

`cross-review` は 1 回の進行で外部 AI を何度も起動し、そのたびに差分・投稿の本文・
エラー出力が生まれる。これらをメインの会話へ載せると、ラウンドを重ねるほど本来の
レビューに使える余地が減る。設計は次の 5 つでこれを抑えている。

1. **大きいファイルはメイン context に載せない**: payload / err.log / diff は
   すべて `$TMP_DIR/` (= `state.py _tmp_dir()` の解決先) に置き、メインは
   state.json と result.json だけ読む
2. **サブエージェント分離**: 修正は別 context window で実行
3. **PR ローテーション**: 1 PR あたりの会話履歴を抑える
4. **投稿はプロセスの中で組み立てる**: 投稿の本文は担当が書いたファイルから取り込み
   （`state.py read-result` / `merge-fix`）のプロセスの中だけを通り、メインの応答には件数・
   参照・状態だけが載る
5. **state.json で再開可能**: メインが落ちても次回起動時に続きから

手順を変えるときは、この 5 つのどれかを崩していないかを確かめる。特に 1 と 4 は、
担当の出力をメインが読んで整形する形や、本文を部分命令の引数で渡す形へ戻すと簡単に崩れる。
