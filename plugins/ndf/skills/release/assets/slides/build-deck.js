// 生成スクリプト（汎用テンプレートの見本）。表紙・章扉・本編（表と注）の 3 ページを作る。
//
//   node build-deck.js <出力>.pptx [<theme.json>]
//
// 写した後はページの中身だけを書き換える。書体と色は theme.json で差し替え、ここに値を書かない。
// 色は theme.colors の項目名（accent・ink・panel1 など）で指す。
const path = require("path");
const { run } = require("./lib/deck-frame");

// 資料のディレクトリでは台本を直下の script.md に置き、ここを path.join(__dirname, "..", "script.md") にする
const SCRIPT = path.join(__dirname, "script.md");

run(__dirname, { script: SCRIPT, title: "<資料の題>", author: "<作成者>" }, (d) => {
  // 1. 表紙
  d.cover("<資料の題>", "<副題>", "<日付>　<作成者>");

  // 2. 章扉
  d.section("現状", "数値で見る前の期間との違い");

  // 3. 本編（表と注）
  const s = d.page("現状の数値", "件数は増え、時間は短くなった。費用だけが増えた");
  d.tbl(s, [
    [d.H("指標"), d.H("前の期間"), d.H("今の期間"), d.H("差")],
    [d.B("件数"), d.C("120"), d.C("150"), d.C("+25%")],
    [d.B("かかる時間"), d.C("40 分"), d.C("32 分"), d.C("−20%")],
    [d.B("費用", { fill: { color: "caution" } }), d.C("10 万円"), d.C("13 万円"), d.C("+30%", { bold: true })],
  ], { x: 0.26, y: 1.34, w: 9.48, colW: [2.4, 2.36, 2.36, 2.36], rowH: 0.36 });
  d.callout(s, 3.0, 0.9, "panel1", "費用の増え方が件数の増え方を上回る", "1 件あたりの費用は 833 円から 867 円に上がった。理由は次のページで示す。");
  d.note(s, "出典: <数値の出所>");
});
