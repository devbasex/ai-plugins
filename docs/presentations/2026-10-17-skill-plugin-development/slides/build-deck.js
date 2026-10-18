// 発表資料「独自プラグイン開発の薦め」の生成スクリプト（台本の 23 ページと章扉 6 つの 29 ページ）。
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

run(__dirname, { script: SCRIPT, title: "独自プラグイン開発の薦め", author: "NDF" }, (d) => {
  let s;

  // 1. タイトル
  d.cover("独自プラグイン開発の薦め", "スキルを作り、プラグインで配る\nNDF を例に、自分の開発スタイルを AI に持たせる", "github.com/devbasex/ai-plugins");

  // ==== 章 1 ====
  d.section("1. なぜプラグインか", "CLAUDE.md に書くだけでは届かない");

  // 2. AI に同じことを何度も言っていませんか
  s = d.page("同じ指摘を、何度もしていませんか", "指摘の回数 ＝ AI にまだ伝わっていない量");
  cols(3).forEach((c, i) => {
    const q = ["「PR は develop 宛てで」", "「テストを先に書いて」", "「無いと言う前に\n検索結果を見せて」"][i];
    d.chip(s, c.x, 1.5, c.w, 1.0, q, "panel1", { fontSize: 15 });
  });
  d.arrow(s, 4.99, 2.62, 0, 0.42);
  d.callout(s, 3.16, 0.5, "caution", "セッションが切れると、AI は忘れる", null);

  // 3. CLAUDE.md への追記だけでは足りない理由
  s = d.page("CLAUDE.md への追記では足りない", "追記はすぐに 4 つの壁に当たる");
  [
    ["コンテキスト", "毎回全部読む。書くほど埋もれる"],
    ["強制力", "書いても守られない"],
    ["手順", "順序は文章で伝わらない"],
    ["ポータビリティ", "環境を変えると消える"],
  ].forEach((r, i) => {
    const c = cols(2)[i % 2];
    card(d, s, c.x, 1.5 + Math.floor(i / 2) * 1.5, c.w, 1.3, r[0], r[1], "panel1", { band: "accent", headSize: 18, bodySize: 16, headH: 0.46 });
  });

  // 4. スキルとプラグイン
  s = d.page("スキルとプラグイン", "スキルで作り、プラグインで配る");
  card(d, s, X0, 1.4, 5.5, 0.92, "スキル（Skill）＝ 作る単位", "手順書（いつ・何を・どの順で）\n必要なときだけ読む", "panel1", { band: "accent", headSize: 14, bodySize: 12 });
  card(d, s, X0, 2.44, 5.5, 1.62, "プラグイン ＝ 配る単位", bullets([
    "スキル",
    "スクリプト（決まった処理を確実に）",
    "hook（ルールを強制）",
    "サブエージェント（役割ごとの AI）",
  ], 12), "panel1", { band: "accent", headSize: 14 });
  d.text(s, "同じものを 4 つのランタイムで", { x: X0, y: 4.14, w: 5.5, h: 0.28, fontSize: 11, bold: true, color: "text", valign: "middle" });
  cols(4, 0.1, X0, 5.5).forEach((c, i) => d.chip(s, c.x, 4.46, c.w, 0.5, ["Claude Code", "Codex", "Kiro", "agy"][i], "accentSoft", { fontSize: 11 }));
  // 公式ドキュメントへのリンク（呼び方は Anthropic と OpenAI の公式に合わせる）
  d.box(s, 5.92, 1.4, 3.82, 3.56, "panel1", false);
  d.box(s, 5.92, 1.4, 3.82, 0.06, "accent", false);
  d.text(s, "公式ドキュメント", { x: 6.06, y: 1.5, w: 3.54, h: 0.32, fontSize: 12, bold: true, color: "text", valign: "middle" });
  const OFFICIAL = [
    ["Anthropic（Claude Code）"],
    ["Claude Code: プラグイン", "https://code.claude.com/docs/ja/plugins"],
    ["Claude Code: Skills", "https://code.claude.com/docs/ja/skills"],
    ["プラグインマーケットプレイス", "https://code.claude.com/docs/ja/plugin-marketplaces"],
    ["Agent Skills の紹介（Engineering Blog）", "https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills"],
    ["公式 Skill の例（anthropics/skills）", "https://github.com/anthropics/skills"],
    ["OpenAI（Codex）"],
    ["Codex: Skills", "https://developers.openai.com/codex/skills"],
    ["Codex: Plugins", "https://developers.openai.com/codex/plugins"],
    ["規格"],
    ["Agent Skills（agentskills.io）", "https://agentskills.io"],
  ];
  d.text(s, OFFICIAL.map(([label, url], i) => ({
    text: label,
    options: Object.assign(
      url ? { hyperlink: { url }, color: "accent", bullet: { indent: 12 } } : { bold: true, color: "text", paraSpaceBefore: i ? 6 : 0 },
      { breakLine: i < OFFICIAL.length - 1 },
    ),
  })), { x: 6.06, y: 1.86, w: 3.54, h: 3.0, fontSize: 11.5, valign: "top", paraSpaceAfter: 3 });

  // ==== 章 2 ====
  d.section("2. NDF の全体像", "Issue の束を渡すと、リリースまで進む");

  // 5. NDF: 1 人の開発スタイルをプラグインにした
  s = d.page("NDF: 自分のやり方をプラグインに", "GitHub で公開。ほぼ 1 人と AI で開発");
  [
    ["102", "回", "リリース（半年）"],
    ["48", "個", "Skill"],
    ["4", "つ", "ランタイム"],
    ["10", "リポジトリ", "自分以外へ導入"],
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
      ["承認 1: 設計", "設計をまとめて 1 回"],
      ["承認 2: 本番リリース", "開発版で確かめて 1 回"],
    ][i];
    card(d, s, c.x, 3.52, c.w, 0.86, g[0], g[1], "caution", { headSize: 12, bodySize: 11, headH: 0.3 });
  });
  d.text(s, "判断の材料はそろって届く", { x: X0, y: 4.46, w: W, h: 0.3, fontSize: 11, bold: true, color: "text", valign: "middle" });
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
    ["DDD: ユビキタス言語", ["要件定義の前に用語集", "1 語 1 意味", "580 語を CI が検査"]],
    ["テストファースト", ["失敗するテストから", "- → 実装 → 整理", "薄いコードは先にテスト"]],
    ["アジャイル", ["マイルストーン ＝ バックログ", "スプリントごとに振り返り", "優先順位は WSJF"]],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    card(d, s, c.x, 1.5, c.w, 2.1, r[0], bullets(r[1], 14), "panel1", { band: "accent", headSize: 14 });
  });

  // 10. /ndf:requirements-design と /ndf:design
  s = d.page("コードの前に「何を」「どう」", "実装の前に、要件と設計の 2 つの文書を書く");
  [
    ["/ndf:requirements-design — 完了の条件", ["確かめられる受け入れ条件", "「速く」を測れる条件へ"]],
    ["/ndf:design — 作り方", ["構造・入出力・流れ・理由", "AI が変わっても同じ構成"]],
  ].forEach((r, i) => {
    const c = cols(2)[i];
    card(d, s, c.x, 1.5, c.w, 1.5, r[0], bullets(r[1], 15), "panel1", { band: "accent", headSize: 13 });
  });
  d.callout(s, 3.2, 0.72, "caution", "設計は PR で出す", "主題ごとに並列、承認 1 でまとめて");

  // 11. /ndf:document-restructuring
  s = d.page("結論から読める順に並べ替える", "/ndf:document-restructuring — 書き上げた文書の再構成");
  d.text(s, "AI が書いた順", { x: X0, y: 1.4, w: 4.3, h: 0.3, fontSize: 12, bold: true, color: "text" });
  flow(d, s, X0, 1.76, 4.3, 0.56, ["問題に気づく", "測る", "結論"], { fill: "panel2", size: 11 });
  d.arrow(s, 4.66, 2.04, 0.5, 0, "accent");
  d.text(s, "読み手が知りたい順", { x: 5.32, y: 1.4, w: 4.42, h: 0.3, fontSize: 12, bold: true, color: "text" });
  flow(d, s, 5.32, 1.76, 4.42, 0.56, ["結論", "根拠", "経緯"], { size: 11 });
  d.text(s, "手順（内容は足さない）", { x: X0, y: 2.62, w: W, h: 0.3, fontSize: 12, bold: true, color: "text" });
  flow(d, s, X0, 2.98, W, 0.62, ["測る", "問いの順に並べる", "文を分ける", "対比は表に", "測り直す"], { size: 11 });
  d.callout(s, 3.86, 0.5, "panel1", "レビューの前に通す → 構成と内容の指摘を分ける", null);

  // 12. /ndf:cross-review
  s = d.page("2 つの AI で、指摘が尽きるまで", "/ndf:cross-review — 別々の AI が交互にレビュー");
  d.text(s, "同じ AI は、同じ見落としをする", { x: X0, y: 1.38, w: 5.0, h: 0.3, fontSize: 12, bold: true, color: "text" });
  flow(d, s, X0, 1.76, 5.0, 0.7, ["AI を 2 つ選ぶ\nclaude・codex・kiro", "レビュー", "指摘を修正"], { size: 10 });
  d.arrow(s, 2.9, 2.54, 0, 0.3);
  d.chip(s, 1.5, 2.9, 2.8, 0.56, "新しい指摘は？", "panel2", { fontSize: 11 });
  d.text(s, "出た → 組み替えて繰り返す\n出ない → 終了", { x: X0, y: 3.56, w: 5.0, h: 0.56, fontSize: 11 });
  card(d, s, 5.6, 1.38, 4.14, 1.26, "別の AI が見落としを拾う", "2 つの AI を入れ替えながら\n新しい指摘が出なくなるまで", "panel1", { band: "accent", headSize: 14, bodySize: 14, headH: 0.38 });
  card(d, s, 5.6, 2.86, 4.14, 1.26, "観点は PR の中身で切り替え", "レビューの基準は\nリポジトリごとに設定", "panel1", { band: "accent", headSize: 14, bodySize: 14, headH: 0.38 });

  // 13. /ndf:cross-refactoring
  s = d.page("全員で提案、1 人が時間内に直す", "/ndf:cross-refactoring — 複数の AI でリファクタリング");
  flow(d, s, X0, 1.5, W, 0.86, ["全員の AI が提案", "1 つを選ぶ", "既定 30 分で\n収まる分を実施", "テストが壊れたら\n取り消す"], { size: 11 });
  d.text(s, "実施: 計画 → テスト追加 → 実装 → 検証", { x: 5.0, y: 2.48, w: 4.74, h: 0.3, fontSize: 11, align: "center" });
  card(d, s, X0, 3.0, 4.66, 1.1, "終わりが見える", "すべて持ち時間から逆算", "panel1", { band: "accent", headSize: 15, bodySize: 15, headH: 0.38 });
  card(d, s, 5.08, 3.0, 4.66, 1.1, "結果が追える", "採用と取り消しを報告", "panel1", { band: "accent", headSize: 15, bodySize: 15, headH: 0.38 });

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
  card(d, s, 5.7, 1.46, 4.04, 1.3, "止まっても続きから", "JSON の計画をスクリプトが実行", "panel1", { band: "accent", headSize: 13, bodySize: 12.5 });
  card(d, s, 5.7, 2.96, 4.04, 1.5, "セッションを自動で切り替え", "次のコマンドを残して新しいセッションへ", "panel1", { band: "accent", headSize: 13, bodySize: 12.5 });

  // 15. 判断基準: MVV と 2 つの承認ゲート
  s = d.page("迷ったら MVV で判断する", "判断の根拠の番号をログに残す");
  [
    ["Mission", "何のためにやるか"],
    ["Vision", "目指す姿"],
    ["Value", "判断の基準"],
  ].forEach((r, i) => {
    const c = cols(3, 0.16, X0, 5.9)[i];
    card(d, s, c.x, 1.5, c.w, 1.3, r[0], r[1], "accentSoft", { headSize: 15, bodySize: 13 });
  });
  d.text(s, "Value の例: 「まず届ける」「処理の改善に寄与しない指摘は直さない」", { x: X0, y: 2.96, w: W, h: 0.34, fontSize: 12.5, valign: "middle" });
  card(d, s, 6.4, 1.5, 3.34, 1.3, "必ず人が承認する操作", "本番デプロイなど\nAI は省かない", "caution", { headSize: 13, bodySize: 12 });
  d.callout(s, 3.5, 0.4, "panel1", "AI に任せる範囲は、事前に設定で選ぶ", null);

  // 16. /ndf:quality-gates と /ndf:investigation-rules
  s = d.page("「完了」も「無い」も証拠で", "AI の言葉を、そのままは信じない");
  [
    ["「完了しました」", "/ndf:quality-gates — 完了の判定", "4 点がそろって完了\nコマンド・終了コード・時刻・範囲"],
    ["「該当のコードはありません」", "/ndf:investigation-rules — 調査の規則", "検索のコマンドと結果を添える"],
  ].forEach((r, i) => {
    const c = cols(2)[i];
    d.chip(s, c.x, 1.48, c.w, 0.72, r[0], "panel2", { fontSize: 15 });
    d.arrow(s, c.x + c.w / 2, 2.26, 0, 0.3);
    card(d, s, c.x, 2.62, c.w, 1.4, r[1], r[2], "panel1", { band: "accent", headSize: 12.5, bodySize: 13 });
  });
  d.text(s, "AI は「無い」と断定しがち", { x: X0, y: 4.3, w: W, h: 0.34, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });

  // 17. 自分を直すループ（導入先のプロジェクトの Issue の一覧を整理し続ける）
  s = d.page("やるべきことを整理し続ける", "導入したプロジェクトの Issue の一覧を、いつも現状に");
  [
    ["/ndf:out-of-scope — 見つけたら起票", "範囲外の問題を即 Issue に"],
    ["/ndf:retrospective — 振り返り", "リリースごとに見直す"],
    ["/ndf:backlog-refinement — 一覧の手入れ", "スプリント末に一覧を現状へ"],
  ].forEach((r, i) => {
    const y = 1.42 + i * 1.08;
    card(d, s, X0, y, 4.9, 0.92, r[0], r[1], "accentSoft", { headSize: 12, bodySize: 11, headH: 0.28 });
    if (i < 2) d.arrow(s, 2.7, y + 0.93, 0, 0.14);
  });
  stat(d, s, 5.4, 1.42, 4.34, 1.44, "56", "%", "本文が現状とずれた open の Issue\n（43 件中 24 件）");
  stat(d, s, 5.4, 3.0, 4.34, 1.5, "49", "件", "本文が古かった Issue\n（71 件を調査。2 件は修正済み）");
  d.note(s, "56% はある時点の棚卸し、49 件は 2026-10-17 の調査。放っておくと、直った Issue を直すことになる");

  // 18. その他の特徴的な機能
  s = d.page("そのほかの特徴的な機能", "見積もり・リリース・AI の操作の強制停止");
  d.tbl(s, [
    [d.H("機能"), d.H("何をするか"), d.H("要点")],
    [d.B("/ndf:estimate"), "AI 前提の見積もり", "人日でなく、時間と費用の 4 指標"],
    [d.B("/ndf:release"), "成果物を同じ骨格でリリース", "パッケージ〜スライド・文書"],
    [d.B("hook"), "AI のやりがちな操作を止める", "直接編集・sleep の待ち・読み直し"],
  ], { x: X0, y: 1.42, w: W, colW: [2.1, 3.5, 3.88], rowH: [0.42, 0.72, 0.72, 0.72], fontSize: 14 });

  // ==== 章 5 ====
  d.section("5. 事例", "ほかのプロジェクトでも動くか");

  // 19. 事例: 他のプロジェクトでも動く
  s = d.page("事例: ほかのプロジェクトでも動く", "言語もチームも違う 4 つのプロジェクト");
  d.tbl(s, [
    [d.H("プロジェクトの種類"), d.H("結果")],
    [d.B("AWS のインフラ"), "マージ PR 0.95 → 3.38 件/日\n※ 社内ツールの移行と重なる"],
    [d.B("PHP の業務システム\n（20 人で開発）"), "設計の承認から 51 時間で実装 PR 31 本"],
    [d.B("機械学習のモデル"), "データ欠損を発見し 1 スプリントで本番へ\n相関係数 0.73 → 0.954"],
    [d.B("価格推定"), "PR 数は同じ。レビュー必須へ"],
  ], { x: X0, y: 1.42, w: W, colW: [2.3, 7.18], rowH: [0.34, 0.74, 0.6, 0.74, 0.5], fontSize: 12 });
  d.note(s, "インフラは導入前後 37 日の比較。プロジェクトは種類で呼ぶ。数値は 2026-10-17 時点の実測");

  // 20. 事例から分かったこと
  s = d.page("事例から分かった 2 つのこと", "詰まりは次の版で直り、ルールはプロジェクトごと");
  card(d, s, X0, 1.42, 4.66, 2.48, "1. 最初は詰まり、次の版で直る", bullets([
    "詰まりが Issue になり、次の版で直る",
    "PHP では 17 件",
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
  d.text(s, "他人のルールはノイズ → フォークする", { x: X0, y: 3.36, w: W, h: 0.34, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });
  cols(3).forEach((c, i) => d.chip(s, c.x, 3.84, c.w, 0.72, ["指摘が資産になる", "ランタイムに依存しない", "計測して改善できる"][i], "panel1", { fontSize: 13 }));

  // 22. 始め方と育て方
  s = d.page("始め方と育て方", "フォークする → 削る → 育てる");
  [
    ["1. フォークする", ["名前を自分のものに", "自分のリポジトリから導入"]],
    ["2. 削る", ["使わない Skill は外す", "残すかは呼び出し回数で"]],
    ["3. 育てる", ["同じ指摘を 2 回したら形に", "手順 → Skill", "決まった処理 → スクリプト", "必ず守らせる → hook"]],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    card(d, s, c.x, 1.42, c.w, 1.9, r[0], bullets(r[1], 13.5), "accentSoft", { headSize: 14 });
  });
  d.callout(s, 3.5, 1.04, "caution", "気をつける点", bullets([
    "description（Skill の説明文）は最小限",
    "思いつきは experimental で試す",
    "開発版を使ってから正式版に",
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
