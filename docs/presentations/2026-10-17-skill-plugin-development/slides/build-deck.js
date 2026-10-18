// 発表資料「独自スキルプラグイン開発の薦め」の生成スクリプト（台本の 23 ページと章扉 6 つの 29 ページ）。
//
//   node build-deck.js <出力>.pptx [<theme.json>]
//
// 枠組みは lib/（NDF の release の assets/slides/lib/ の写し。書き換えない）、
// トーン＆マナーは theme.json、スピーカーノートは ../script.md（台本）から入る。
// ページの役割と根拠、章立ては ../notes.md にある。章扉（d.section）は台本の番号を進めない。
// ページには要点だけを置く（読み上げはスピーカーノートが持つ）。1 ページ 1 メッセージ・短い句か体言止め。
const path = require("path");
const { run } = require("./lib/deck-frame");

const SCRIPT = path.join(__dirname, "..", "script.md");
const REPO = "https://github.com/devbasex/ai-plugins";

// ---- このページ群で使う部品（座標は本文の領域の中だけ） ----
const X0 = 0.26;
const W = 9.48;

// 横に n 等分した区画の x と幅
function cols(n, gap, x0, w) {
  const g = gap == null ? 0.16 : gap;
  const left = x0 == null ? X0 : x0;
  const total = w == null ? W : w;
  const cw = (total - g * (n - 1)) / n;
  return Array.from({ length: n }, (_, i) => ({ x: left + i * (cw + g), w: cw }));
}

// 見出しと本文を持つ区画
function card(d, s, x, y, w, h, head, body, fill, opt) {
  const o = opt || {};
  d.box(s, x, y, w, h, fill || "panel1", false);
  if (o.band) d.box(s, x, y, w, 0.06, o.band, false);
  d.text(s, head, { x: x + 0.14, y: y + 0.1, w: w - 0.28, h: o.headH || 0.32, fontSize: o.headSize || 12, bold: true, color: "text", valign: "middle" });
  if (body) d.text(s, body, { x: x + 0.14, y: y + 0.12 + (o.headH || 0.32), w: w - 0.28, h: h - 0.22 - (o.headH || 0.32), fontSize: o.bodySize || 10, valign: "top", paraSpaceAfter: 3 });
}

// 大きな数値の区画
function stat(d, s, x, y, w, h, value, unit, label) {
  d.box(s, x, y, w, h, "panel1", false);
  d.box(s, x, y, 0.06, h, "accent", false);
  d.text(s, [
    { text: value, options: { fontSize: 30, bold: true, color: "accent" } },
    { text: " " + unit, options: { fontSize: 13, bold: true, color: "text" } },
  ], { x: x + 0.2, y: y + 0.14, w: w - 0.3, h: 0.62, valign: "middle" });
  d.text(s, label, { x: x + 0.2, y: y + 0.8, w: w - 0.3, h: h - 0.9, fontSize: 10 });
}

// 箇条書き（"- " で始まる項目は 1 段下げる）
function bullets(items, size) {
  return items.map((t, i) => {
    const sub = t.startsWith("- ");
    return { text: sub ? t.slice(2) : t, options: { bullet: { indent: 12 }, indentLevel: sub ? 1 : 0, fontSize: size || 10, breakLine: i < items.length - 1 } };
  });
}

// 横に並べた工程（chip と矢印）
function flow(d, s, x, y, w, h, labels, opt) {
  const o = opt || {};
  const gap = o.gap == null ? 0.22 : o.gap;
  const cw = (w - gap * (labels.length - 1)) / labels.length;
  labels.forEach((l, i) => {
    const cx = x + i * (cw + gap);
    const gate = typeof l === "object";
    d.chip(s, cx, y, cw, h, gate ? l.text : l, gate ? "caution" : o.fill || "accentSoft", { fontSize: o.size || 9.5 });
    if (i < labels.length - 1) d.arrow(s, cx + cw + 0.02, y + h / 2, gap - 0.04, 0);
  });
}

run(__dirname, { script: SCRIPT, title: "独自スキルプラグイン開発の薦め", author: "NDF" }, (d) => {
  let s;

  // 1. タイトル
  d.cover("独自スキルプラグイン開発の薦め", "NDF を例に、自分の開発スタイルを AI に持たせる", "github.com/devbasex/ai-plugins");

  // ==== 章 1 ====
  d.section("1. なぜスキルプラグインか", "CLAUDE.md に書くだけでは届かない");

  // 2. AI に同じことを何度も言っていませんか
  s = d.page("同じ指摘を、何度もしていませんか", "指摘の回数 ＝ AI にまだ伝わっていない量");
  cols(3).forEach((c, i) => {
    const q = ["「PR は develop 宛てで」", "「テストを先に書いて」", "「無いと言う前に\n検索結果を見せて」"][i];
    d.chip(s, c.x, 1.5, c.w, 1.0, q, "panel1", { fontSize: 15 });
  });
  d.arrow(s, 4.99, 2.62, 0, 0.42);
  d.callout(s, 3.16, 0.76, "caution", "セッションが切れると、AI は忘れる", "毎日使うほど、同じ指摘が積み上がる");

  // 3. CLAUDE.md への追記だけでは足りない理由
  s = d.page("CLAUDE.md への追記では足りない", "追記はすぐに 4 つの壁に当たる");
  [
    ["コンテキスト", "毎回すべて読む。書くほど重く、ルールが埋もれる"],
    ["強制力", "書いても守られない"],
    ["手順", "実行の順序は文章で伝わりにくい"],
    ["ポータビリティ", "リポジトリやランタイムを変えると消える"],
  ].forEach((r, i) => {
    const c = cols(2)[i % 2];
    card(d, s, c.x, 1.42 + Math.floor(i / 2) * 1.62, c.w, 1.46, r[0], r[1], "panel1", { band: "accent", headSize: 15, bodySize: 13, headH: 0.42 });
  });

  // 4. スキルプラグインとは
  s = d.page("スキルプラグインとは", "やり方をパッケージにして、どこでも再利用");
  d.tbl(s, [
    [d.H("構成要素"), d.H("役割")],
    [d.B("Skill"), "手順書。「いつ・何を・どの順で」。必要なときだけ読む"],
    [d.B("スクリプト"), "決まった処理を確実に実行"],
    [d.B("hook"), "ルールを強制（AI の操作の直前に割り込む）"],
    [d.B("サブエージェント"), "役割ごとに分けた AI"],
  ], { x: X0, y: 1.4, w: W, colW: [2.2, 7.28], rowH: [0.34, 0.46, 0.46, 0.46, 0.46], fontSize: 13 });
  d.text(s, "1 つの配布物を、4 つのランタイムで", { x: X0, y: 3.74, w: W, h: 0.3, fontSize: 12, bold: true, color: "text" });
  cols(4).forEach((c, i) => d.chip(s, c.x, 4.1, c.w, 0.62, ["Claude Code", "Codex", "Kiro", "agy"][i], "accentSoft", { fontSize: 13 }));

  // ==== 章 2 ====
  d.section("2. NDF の全体像", "Issue の束を渡すと、リリースまで進む");

  // 5. NDF: 1 人の開発スタイルをプラグインにした
  s = d.page("NDF: 自分のやり方をプラグインに", "GitHub で公開。ほぼ 1 人と AI で開発");
  [
    ["102", "回", "リリース（開発を始めて半年）"],
    ["48", "個", "Skill（手順書）"],
    ["4", "つ", "対応するランタイム"],
    ["10", "リポジトリ", "自分以外への導入"],
  ].forEach((r, i) => {
    const c = cols(4)[i];
    stat(d, s, c.x, 1.55, c.w, 1.7, r[0], r[1], r[2]);
  });
  d.text(s, [{ text: REPO, options: { hyperlink: { url: REPO }, color: "accent" } }], { x: X0, y: 3.6, w: W, h: 0.36, fontSize: 14, bold: true, align: "center", valign: "middle" });
  d.note(s, "数値は 2026-10-17 時点の実測。開発の開始は 2026 年 4 月");

  // 6. NDF の全体像
  s = d.page("Issue の束を渡せば、並列に進む", "人が判断するのは 2 回の承認だけ");
  d.chip(s, X0, 1.62, 1.55, 0.9, "マイルストーン\n（Issue の束）", "panel2", { fontSize: 10 });
  d.arrow(s, 1.85, 2.07, 0.3, 0);
  d.chip(s, 2.19, 1.62, 1.55, 0.9, "触る場所と依存で\nグループに分ける", "panel2", { fontSize: 10 });
  d.arrow(s, 3.78, 2.07, 0.3, 0);
  d.box(s, 4.12, 1.36, 4.0, 1.98, "panel1", false);
  d.text(s, "スプリント（グループごとに並列）", { x: 4.24, y: 1.4, w: 3.8, h: 0.28, fontSize: 10, bold: true, color: "text", valign: "middle" });
  ["グループ A", "グループ B", "グループ C"].forEach((g, i) => {
    const y = 1.74 + i * 0.52;
    d.text(s, g, { x: 4.24, y, w: 0.9, h: 0.4, fontSize: 9, valign: "middle" });
    flow(d, s, 5.12, y, 2.9, 0.4, ["設計", "実装", "レビュー"], { gap: 0.18, size: 9 });
  });
  d.arrow(s, 8.16, 2.35, 0.3, 0);
  d.chip(s, 8.5, 1.9, 1.24, 0.9, "リリース", "panel2", { fontSize: 11 });
  cols(2).forEach((c, i) => {
    const g = [
      ["承認 1: 設計", "スプリントの設計をまとめて 1 回"],
      ["承認 2: 本番リリース", "開発版で確かめた後に 1 回"],
    ][i];
    card(d, s, c.x, 3.52, c.w, 0.86, g[0], g[1], "caution", { headSize: 12, bodySize: 11, headH: 0.3 });
  });
  d.text(s, "決めたこと・未検証の点・根拠がそろって届く → 1 回で決められる", { x: X0, y: 4.46, w: W, h: 0.3, fontSize: 11, bold: true, color: "text", valign: "middle" });
  d.note(s, "例: 直前のリリース — 致命的な Issue 18 件を 2 スプリントに分け、スプリントの中は 3 グループを並列に");

  // 7. /ndf:development-workflow
  s = d.page("変更の大きさで、踏む工程を変える", "/ndf:development-workflow — 変更を 5 つのモードに分ける");
  d.tbl(s, [
    [d.H("モード"), d.H("対象")],
    [d.B("light"), "文言・設定の小さな変更"],
    [d.B("operation"), "コードを変えない外部の作業"],
    [d.B("standard"), "機能追加・バグ修正"],
    [d.B("legacy-refactor"), "テストの薄いコードの整理"],
    [d.B("documentation"), "提案書・報告書などの文書"],
  ], { x: X0, y: 1.4, w: W, colW: [2.6, 6.88], rowH: 0.38, fontSize: 13 });
  cols(2).forEach((c, i) => {
    const r = [
      ["light", "実装 → レビューだけ"],
      ["standard", "要件定義 → … → 振り返り"],
    ][i];
    card(d, s, c.x, 3.9, c.w, 0.82, r[0], r[1], i ? "accentSoft" : "panel1", { headSize: 12, bodySize: 12, headH: 0.3 });
  });

  // 8. standard モードの全体の流れ
  s = d.page("機能追加は、この流れで 1 周", "standard モード: スプリントごとに 1 周、中はグループごとに並列");
  [
    ["設計", ["要件定義", "作業用の\nworktree", "設計", "構成の\n見直し", "クロス\nレビュー", { text: "承認 1\n設計" }]],
    ["実装", ["実装計画", "テスト\nファースト", "クロス\nリファクタ", "クロス\nレビュー", "完了判定\n（証跡）", "マージ"]],
    ["リリース", ["仕様書へ", "開発版\nリリース", { text: "承認 2\n本番" }, "本番\nリリース", "リリース後\nテスト", "振り返り", "Issue の\n棚卸し"]],
  ].forEach((row, i) => {
    const y = 1.48 + i * 1.08;
    d.text(s, row[0], { x: X0, y, w: 0.8, h: 0.7, fontSize: 11, bold: true, color: "text", valign: "middle" });
    const n = row[1].length;
    const g = 0.2;
    flow(d, s, 1.06, y, (8.68 * n + g * (n - 7)) / 7, 0.7, row[1], { gap: g, size: 9.5 });
    // 段の続きは左の見出しの列で示す（工程の箱の間に置くと、箱どうしの流れに読める）
    if (i < 2) d.arrow(s, X0 + 0.3, y + 0.76, 0, 0.26);
  });
  d.note(s, "レビューの指摘は直して繰り返す。開発版は自分で使って確かめてから承認 2 へ");

  // ==== 章 3 ====
  d.section("3. 組み込んだ開発技法", "実績のある技法を、AI が守れる手順に");

  // 9. 実績のある開発技法を組み込む
  s = d.page("実績のある開発技法を組み込む", "新しい発明ではない。AI が毎回守れる手順にした");
  [
    ["DDD: ユビキタス言語", ["要件定義の前に用語集", "1 つの語に 1 つの意味", "例: 「注文」は受注と配送で別", "用語集 580 語、CI が検査"]],
    ["テストファースト", ["失敗するテスト", "- → 通す最小の実装", "- → 整理", "薄いコードは", "- 先に今の振る舞いを固定"]],
    ["アジャイル", ["マイルストーン ＝ バックログ", "スプリントごとに振り返り", "バックログの整理も毎回", "優先順位は WSJF", "- 遅延コスト ÷ 大きさ"]],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    card(d, s, c.x, 1.42, c.w, 2.5, r[0], bullets(r[1], 13), "panel1", { band: "accent", headSize: 14 });
  });

  // 10. /ndf:requirements-design と /ndf:design
  s = d.page("コードの前に「何を」「どう」", "実装の前に、要件と設計の 2 つの文書を書く");
  [
    ["/ndf:requirements-design — 完了の条件", ["確かめられる受け入れ条件に", "「いい感じに」「速く」を条件へ"]],
    ["/ndf:design — 作り方", ["データ構造・入出力・流れ・理由", "担当の AI が変わっても同じ構成に"]],
  ].forEach((r, i) => {
    const c = cols(2)[i];
    card(d, s, c.x, 1.42, c.w, 1.5, r[0], bullets(r[1], 13), "panel1", { band: "accent", headSize: 13 });
  });
  d.callout(s, 3.2, 0.72, "caution", "設計は PR で出す", "主題ごとに並列に開き、承認 1 でまとめて承認");

  // 11. /ndf:document-restructuring
  s = d.page("結論から読める順に並べ替える", "/ndf:document-restructuring — 書き上げた文書の再構成");
  d.text(s, "AI が書いた順（調べた順）", { x: X0, y: 1.4, w: 4.3, h: 0.3, fontSize: 12, bold: true, color: "text" });
  flow(d, s, X0, 1.76, 4.3, 0.56, ["問題に気づく", "測る", "結論"], { fill: "panel2", size: 11 });
  d.arrow(s, 4.66, 2.04, 0.5, 0, "accent");
  d.text(s, "読み手が知りたい順", { x: 5.32, y: 1.4, w: 4.42, h: 0.3, fontSize: 12, bold: true, color: "text" });
  flow(d, s, 5.32, 1.76, 4.42, 0.56, ["結論", "根拠", "経緯"], { size: 11 });
  d.text(s, "手順（内容は足さない）", { x: X0, y: 2.62, w: W, h: 0.3, fontSize: 12, bold: true, color: "text" });
  flow(d, s, X0, 2.98, W, 0.62, ["測る", "章を読み手の\n問いの順に", "長い文を分ける", "対比は表に", "もう一度測る"], { size: 11 });
  d.callout(s, 3.86, 0.7, "panel1", "レビューの前に通す", "構成の指摘と内容の指摘が混ざらない");

  // 12. /ndf:cross-review
  s = d.page("2 つの AI で、指摘が尽きるまで", "/ndf:cross-review — 別々の AI が交互にレビュー");
  d.text(s, "同じ AI は、同じ見落としをする", { x: X0, y: 1.38, w: 5.0, h: 0.3, fontSize: 12, bold: true, color: "text" });
  flow(d, s, X0, 1.76, 5.0, 0.7, ["AI を 2 つ選ぶ\nclaude・codex・kiro", "レビュー", "指摘を修正"], { size: 10 });
  d.arrow(s, 2.9, 2.54, 0, 0.3);
  d.chip(s, 1.5, 2.9, 2.8, 0.56, "新しい指摘は？", "panel2", { fontSize: 11 });
  d.text(s, "出た → 組み合わせを替えて繰り返す\n出ない → 終了", { x: X0, y: 3.56, w: 5.0, h: 0.56, fontSize: 11 });
  d.text(s, "観点は PR の中身で切り替え。基準はリポジトリごと", { x: X0, y: 4.22, w: 5.0, h: 0.4, fontSize: 10.5 });
  d.text(s, "指摘の再現率（手元の実測）", { x: 5.6, y: 1.42, w: 4.14, h: 0.3, fontSize: 12, bold: true, color: "text" });
  [
    ["1 つの AI（低い方）", 0.21, "panel2"],
    ["1 つの AI（高い方）", 0.49, "panel2"],
    ["2 つの AI を合わせる", 0.91, "accent"],
  ].forEach((r, i) => {
    const y = 1.86 + i * 0.86;
    d.text(s, r[0], { x: 5.6, y, w: 4.14, h: 0.28, fontSize: 11 });
    d.box(s, 5.6, y + 0.3, 3.4 * r[1], 0.36, r[2], false);
    d.text(s, r[1].toFixed(2), { x: 5.66 + 3.4 * r[1], y: y + 0.3, w: 0.6, h: 0.36, fontSize: 13, bold: true, color: "text", valign: "middle" });
  });

  // 13. /ndf:cross-refactoring
  s = d.page("全員で提案、1 人が時間内に直す", "/ndf:cross-refactoring — 複数の AI でリファクタリング");
  flow(d, s, X0, 1.5, W, 0.86, ["全員の AI が\n1 回ずつ提案", "1 つの AI を選ぶ", "時間内（既定 30 分）\nに収まる分を実施", "テストが壊れたら\n取り消す"], { size: 11 });
  d.text(s, "実施: 計画 → テスト追加 → 実装 → 検証", { x: 5.0, y: 2.48, w: 4.74, h: 0.3, fontSize: 11, align: "center" });
  card(d, s, X0, 3.0, 4.66, 1.2, "終わりが見える", "時間の数値は、すべて持ち時間から逆算", "panel1", { band: "accent", headSize: 13, bodySize: 12.5 });
  card(d, s, 5.08, 3.0, 4.66, 1.2, "結果が追える", "採用と取り消しの内訳を報告に残す", "panel1", { band: "accent", headSize: 13, bodySize: 12.5 });

  // ==== 章 4 ====
  d.section("4. AI に任せ切る仕組み", "止まらない・迷わない・ごまかさない");

  // 14. 3 層のエージェント
  s = d.page("3 層のエージェントで止めない", "長い作業も、止まったら続きから");
  [
    ["conductor", "指揮役。人と話し全体を進める"],
    ["supervisor", "監督役。1 つの工程を通す"],
    ["worker", "作業役。調査や修正を行う"],
  ].forEach((r, i) => {
    const y = 1.46 + i * 1.02;
    d.chip(s, X0 + i * 0.3, y, 1.7, 0.74, r[0], i === 0 ? "accent" : "accentSoft", { fontSize: 13, color: i === 0 ? "onHeader" : "text" });
    d.text(s, r[1], { x: 2.12 + i * 0.3, y, w: 2.95, h: 0.74, fontSize: 11.5, valign: "middle" });
    if (i < 2) d.arrow(s, 1.1 + i * 0.3, y + 0.76, 0.2, 0.24);
  });
  card(d, s, 5.7, 1.46, 4.04, 1.3, "止まっても続きから", "工程を JSON の計画にし、スクリプトが実行", "panel1", { band: "accent", headSize: 13, bodySize: 12.5 });
  card(d, s, 5.7, 2.96, 4.04, 1.5, "セッションを自動で切り替え", "コンテキストが膨らんだら、次のコマンドを残して新しいセッションへ", "panel1", { band: "accent", headSize: 13, bodySize: 12.5 });

  // 15. 判断基準: MVV と 2 つの承認ゲート
  s = d.page("迷ったら MVV で判断する", "判断の根拠の番号をログに残す");
  d.text(s, "AI が迷う場面: 指摘を直すか見送るか・承認を省いてよいか", { x: X0, y: 1.38, w: W, h: 0.3, fontSize: 11.5 });
  [
    ["Mission", "何のためにやるか"],
    ["Vision", "目指す姿"],
    ["Value", "判断の基準"],
  ].forEach((r, i) => {
    const c = cols(3, 0.16, X0, 5.9)[i];
    card(d, s, c.x, 1.8, c.w, 1.2, r[0], r[1], "accentSoft", { headSize: 15, bodySize: 12.5 });
  });
  d.text(s, "Value の例: 「まず届ける」「処理の改善に寄与しない指摘は直さない」", { x: X0, y: 3.16, w: W, h: 0.34, fontSize: 12, valign: "middle" });
  card(d, s, 6.4, 1.8, 3.34, 1.2, "必ず人が承認する操作", "本番デプロイなど\nMVV に照らしても AI は省かない", "caution", { headSize: 13, bodySize: 12 });
  d.callout(s, 3.7, 0.4, "panel1", "どこまで AI に任せるかは、事前に設定で選べる", null);

  // 16. /ndf:quality-gates と /ndf:investigation-rules
  s = d.page("「完了」も「無い」も証拠で", "AI の言葉を、そのままは信じない");
  [
    ["「完了しました」", "/ndf:quality-gates — 完了の判定", "次の 4 つがそろうまで完了としない\nコマンド・終了コード・時刻・範囲"],
    ["「該当のコードはありません」", "/ndf:investigation-rules — 調査の規則", "「無い」の結論には必ず\n検索のコマンドと結果を添える"],
  ].forEach((r, i) => {
    const c = cols(2)[i];
    d.chip(s, c.x, 1.48, c.w, 0.72, r[0], "panel2", { fontSize: 15 });
    d.arrow(s, c.x + c.w / 2, 2.26, 0, 0.3);
    card(d, s, c.x, 2.62, c.w, 1.4, r[1], r[2], "panel1", { band: "accent", headSize: 12.5, bodySize: 13 });
  });
  d.text(s, "AI はもっともらしい推論で「無い」と断定しがち", { x: X0, y: 4.3, w: W, h: 0.34, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });

  // 17. 自分を直すループ（導入先のプロジェクトの Issue の一覧を整理し続ける）
  s = d.page("やるべきことを整理し続ける", "導入したプロジェクトの Issue の一覧を、いつも現状に");
  [
    ["/ndf:out-of-scope — 見つけたら起票", "作業中に見つけた範囲外の問題を、その場で Issue に"],
    ["/ndf:retrospective — 振り返り", "リリースごとに進め方を見直す"],
    ["/ndf:backlog-refinement — 一覧の手入れ", "スプリントの終わりに、Issue を現状へ合わせて並べ直す"],
  ].forEach((r, i) => {
    const y = 1.42 + i * 1.08;
    card(d, s, X0, y, 4.9, 0.92, r[0], r[1], "accentSoft", { headSize: 12, bodySize: 11, headH: 0.28 });
    if (i < 2) d.arrow(s, 2.7, y + 0.93, 0, 0.14);
  });
  stat(d, s, 5.4, 1.42, 4.34, 1.44, "56", "%", "本文が現状とずれていた open の Issue\n（43 件中 24 件・ある時点の棚卸し）");
  stat(d, s, 5.4, 3.0, 4.34, 1.5, "49", "件", "本文が古くなっていた Issue\n（今日 71 件を並列に調査・ほかに 2 件は修正済み）");
  d.note(s, "放っておくと、もう直った Issue を直すことになる");

  // 18. その他の特徴的な機能
  s = d.page("そのほかの特徴的な機能", "見積もり・リリース・AI の操作の強制停止");
  d.tbl(s, [
    [d.H("機能"), d.H("何をするか"), d.H("要点")],
    [d.B("/ndf:estimate"), "AI が作業する前提の見積もり", "人日を使わない。AI の時間・人の時間・完了まで・費用を過去の実績から"],
    [d.B("/ndf:release"), "届け方の違う成果物を同じ骨格でリリース", "パッケージ・Web サービス・スライド・文書"],
    [d.B("hook"), "AI がやりがちな操作を強制的に止める", "直接編集の警告・sleep の待ちの禁止・同じファイルの読み直しの防止"],
  ], { x: X0, y: 1.42, w: W, colW: [1.9, 3.3, 4.28], rowH: [0.36, 0.86, 0.7, 0.86], fontSize: 12 });

  // ==== 章 5 ====
  d.section("5. 事例", "ほかのプロジェクトでも動くか");

  // 19. 事例: 他のプロジェクトでも動く
  s = d.page("事例: ほかのプロジェクトでも動く", "言語もチームも違う 4 つのプロジェクト");
  d.tbl(s, [
    [d.H("プロジェクトの種類"), d.H("結果")],
    [d.B("AWS のインフラ"), "1 日のマージ PR 0.95 → 3.38（導入前後 37 日）\n※ 社内ツールの移行と重なる。すべてが NDF の効果ではない"],
    [d.B("PHP の業務システム\n（20 人で開発）"), "設計 PR 1 本の承認から 51 時間で、実装 PR 31 本"],
    [d.B("機械学習のモデル"), "4 か月分のデータ欠損を発見。修正〜本番を 1 スプリントで\n相関係数 0.73 → 0.954"],
    [d.B("価格推定"), "PR 数は同じ。レビュー必須の開発プロセスへ"],
  ], { x: X0, y: 1.42, w: W, colW: [2.3, 7.18], rowH: [0.34, 0.74, 0.6, 0.74, 0.5], fontSize: 12 });
  d.note(s, "プロジェクトは種類で呼び、名前は出さない。数値は 2026-10-17 時点の実測");

  // 20. 事例から分かったこと
  s = d.page("事例から分かった 2 つのこと", "詰まりは次の版で直り、ルールはプロジェクトごと");
  card(d, s, X0, 1.42, 4.66, 2.48, "1. 最初は詰まり、次の版で直る", bullets([
    "詰まりが NDF の Issue になる",
    "PHP では前提の違いで 17 件",
    "- 作業用の worktree に vendor/ が無い",
    "- 全テストに 64 分",
    "- マージ ＝ 本番リリース",
  ], 13), "panel1", { band: "accent", headSize: 13 });
  d.text(s, "2. どこも独自のルールを足した", { x: 5.08, y: 1.48, w: 4.66, h: 0.32, fontSize: 13, bold: true, color: "text", valign: "middle" });
  d.tbl(s, [
    [d.H("プロジェクト"), d.H("足したルール")],
    [d.B("インフラ"), "本番デプロイの承認"],
    [d.B("業務システム"), "「マージ = 本番リリース」の承認"],
    [d.B("価格推定"), "全 PR へのレビュー"],
  ], { x: 5.08, y: 1.9, w: 4.66, colW: [1.5, 3.16], rowH: 0.5, fontSize: 12 });

  // ==== 章 6 ====
  d.section("6. 始め方", "フォークして、自分用に育てる");

  // 21. だから「フォークして自分用に」
  s = d.page("だから、フォークして自分用に", "仕組みはそのまま、ルールだけ差し替える");
  card(d, s, X0, 1.42, 4.66, 1.86, "NDF も私のルールのかたまり", bullets(["承認は 2 回だけ", "文書では「道具」でなく「Tool」", "文言を照合するテストは書かない"], 12.5), "warn", { headSize: 13 });
  card(d, s, 5.08, 1.42, 4.66, 1.86, "そのまま使う仕組み", bullets(["ワークフロー", "3 層のエージェント", "クロスレビュー", "判断基準（MVV）"], 12.5), "accentSoft", { headSize: 13 });
  d.text(s, "他人のルールは自分にはノイズ → 導入よりフォーク", { x: X0, y: 3.36, w: W, h: 0.34, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });
  cols(3).forEach((c, i) => d.chip(s, c.x, 3.84, c.w, 0.72, ["指摘が資産になる", "ランタイムに依存しない", "計測して改善できる"][i], "panel1", { fontSize: 13 }));

  // 22. 始め方と育て方
  s = d.page("始め方と育て方", "フォークする → 削る → 育てる");
  [
    ["1. フォークする", ["名前を自分のものに", "- プラグイン名", "- マーケットプレイス名", "自分のリポジトリから導入"]],
    ["2. 削る", ["配布する Skill は一覧で決まる", "使わない Skill は外すだけ", "残すかは呼び出し回数で"]],
    ["3. 育てる", ["同じ指摘を 2 回したら形に", "手順 → Skill", "決まった処理 → スクリプト", "必ず守らせる → hook"]],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    card(d, s, c.x, 1.42, c.w, 2.12, r[0], bullets(r[1], 12), "accentSoft", { headSize: 13 });
  });
  d.callout(s, 3.7, 1.04, "caution", "気をつける点", bullets([
    "description（Skill の説明文）を増やしすぎない。全セッションで読まれる",
    "思いつきは experimental で試し、効いたものだけ本体へ",
    "開発版を自分で使ってから正式版に",
  ], 10.5));

  // 23. まとめ: 明日からの 3 ステップ
  s = d.page("まとめ: 明日からの 3 ステップ", "教えるのは 1 回。プラグインにして、フォークして育てる");
  [
    ["1", "ai-plugins をフォークして\n自分の名前でインストール"],
    ["2", "使わない Skill を外す"],
    ["3", "同じ指摘を 2 回したら\nSkill にする"],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    d.box(s, c.x, 1.5, c.w, 2.0, "panel1", false);
    d.text(s, r[0], { x: c.x, y: 1.6, w: c.w, h: 0.7, fontSize: 36, bold: true, color: "accent", align: "center", valign: "middle" });
    d.text(s, r[1], { x: c.x + 0.14, y: 2.36, w: c.w - 0.28, h: 1.0, fontSize: 13, bold: true, color: "text", align: "center", valign: "middle" });
  });
  d.text(s, [{ text: REPO, options: { hyperlink: { url: REPO }, color: "accent" } }], { x: X0, y: 3.86, w: W, h: 0.5, fontSize: 18, bold: true, align: "center", valign: "middle" });
});
