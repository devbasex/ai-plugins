// 発表資料「AI が回すソフトウェア工場 — NDF から Neural Development Factory へ」の生成スクリプト
// （台本の 31 ページと、章扉 3 つ・中締めの扉 1 つの 35 ページ）。
//
//   node build-deck.js <出力>.pptx [<theme.json>]
//
// 枠組みは lib/（NDF の release の assets/slides/lib/ の写し。書き換えない）、
// トーン＆マナーは theme.json（暗い地にシアンとバイオレット）、スピーカーノートは ../script.md（台本）から入る。
// 色は theme.json の項目名で指す。図形を直接置くときは d.color(<項目名>) で値を引く。
// 後半の画面（25〜29）は Factory の画面モックを、スライドの図形で描き直したもの。
const path = require("path");
const { run } = require("./lib/deck-frame");

const SCRIPT = path.join(__dirname, "..", "script.md");
const REPO = "https://github.com/devbasex/ai-plugins";

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

// 箇条書き（"- " で始まる項目はインデントを 1 つ下げる）
function bullets(items, size, color) {
  return items.map((t, i) => {
    const sub = t.startsWith("- ");
    const o = { bullet: { indent: 12 }, indentLevel: sub ? 1 : 0, fontSize: size || 10, breakLine: i < items.length - 1 };
    if (color) o.color = color;
    return { text: sub ? t.slice(2) : t, options: o };
  });
}

run(__dirname, { script: SCRIPT, title: "AI が回すソフトウェア工場", author: "NDF" }, (d) => {
  const P = d.pptx;
  // スピーカーノートは台本の改行と空行を残す（lib は段落を改行 1 つでつなぎ、段落の中の改行を消すため、ここで読み直す）
  const fs = require("fs");
  const notesByNo = new Map();
  let no = null;
  for (const line of fs.readFileSync(SCRIPT, "utf8").split(/\r?\n/)) {
    const h = line.match(/^##\s+(\d+)\./);
    if (h) { no = Number(h[1]); notesByNo.set(no, []); continue; }
    if (/^#{1,2}\s/.test(line)) { no = null; continue; }
    const q = no != null && line.match(/^>\s?(.*)$/);
    if (q) notesByNo.get(no).push(q[1].trim().replace(/\*\*/g, "").replace(/`/g, ""));
  }
  d.script.forEach((sp) => {
    const ls = notesByNo.get(sp.no);
    if (ls) sp.reading = ls.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  });
  let s;
  let chapter = 0;

  // ---- 部品 ----
  // 角の丸い箱。line に項目名を渡すと枠線を引く
  function rr(sl, x, y, w, h, fill, line, radius) {
    sl.addShape(P.ShapeType.roundRect, {
      x, y, w, h, rectRadius: radius == null ? 0.08 : radius,
      fill: { color: d.color(fill) },
      line: line ? { color: d.color(line), width: 1 } : { type: "none" },
    });
  }
  function dot(sl, x, y, r, fill, transparency) {
    sl.addShape(P.ShapeType.ellipse, { x, y, w: r, h: r, fill: { color: d.color(fill), transparency: transparency || 0 }, line: { type: "none" } });
  }
  // 丸い札（状態の表示）
  function pill(sl, x, y, w, h, t, fill, color, size) {
    rr(sl, x, y, w, h, fill, null, h / 2);
    d.text(sl, t, { x, y, w, h, fontSize: size || 8, bold: true, color: color || "text", align: "center", valign: "middle" });
  }
  // ページ。見出しの罫線の左端にアクセントの線を重ねる
  // size を渡すと、その見出しだけ文字の大きさを変える（長い見出しを 1 行に収めるため）
  function pg(title, lead, size) {
    const sl = d.page(size ? [{ text: title, options: { fontSize: size } }] : title, lead);
    d.box(sl, 0.24, 0.785, 1.1, 0.035, "accent", false);
    return sl;
  }
  // 章扉。右に大きな章番号を薄く置く
  function sec(title, sub) {
    chapter++;
    const sl = d.section(title, sub);
    d.text(sl, String(chapter).padStart(2, "0"), { x: 6.6, y: 0.15, w: 3.2, h: 1.6, fontSize: 110, bold: true, color: "panel2", align: "right", valign: "middle" });
    dot(sl, 8.3, 3.7, 2.4, "header", 82);
    dot(sl, 7.5, 4.3, 1.4, "accent", 85);
    return sl;
  }
  // 見出しと本文を持つ区画
  function card(sl, x, y, w, h, head, body, fill, opt) {
    const o = opt || {};
    rr(sl, x, y, w, h, fill || "panel1", o.line || null);
    if (o.band) d.box(sl, x + 0.14, y, 0.5, 0.05, o.band, false);
    const hh = o.headH || 0.34;
    d.text(sl, head, { x: x + 0.16, y: y + 0.1, w: w - 0.32, h: hh, fontSize: o.headSize || 12, bold: true, color: o.headColor || "text", valign: "middle" });
    if (body) d.text(sl, body, { x: x + 0.16, y: y + 0.14 + hh, w: w - 0.32, h: h - 0.24 - hh, fontSize: o.bodySize || 10.5, color: o.bodyColor || "ink", valign: "top", paraSpaceAfter: 3 });
  }
  // 大きな数値
  function stat(sl, x, y, w, h, value, unit, label, accent) {
    rr(sl, x, y, w, h, "panel1");
    d.box(sl, x + 0.18, y + 0.16, 0.4, 0.05, accent || "accent", false);
    d.text(sl, [
      { text: value, options: { fontSize: 34, bold: true, color: accent || "accent" } },
      { text: " " + unit, options: { fontSize: 13, bold: true, color: "text" } },
    ], { x: x + 0.18, y: y + 0.26, w: w - 0.3, h: 0.7, valign: "middle" });
    d.text(sl, label, { x: x + 0.18, y: y + 0.98, w: w - 0.3, h: h - 1.05, fontSize: 10.5 });
  }
  // 横に並べた工程。{ text } の項目は承認（琥珀の地に暗い字）、{ hi } の項目は後のページで紹介する工程（淡いシアンの地）
  function flow(sl, x, y, w, h, labels, opt) {
    const o = opt || {};
    const gap = o.gap == null ? 0.22 : o.gap;
    const cw = (w - gap * (labels.length - 1)) / labels.length;
    labels.forEach((l, i) => {
      const cx = x + i * (cw + gap);
      const hi = typeof l === "object" && l.hi != null;
      const gate = typeof l === "object" && !hi;
      rr(sl, cx, y, cw, h, gate ? "caution" : hi ? "accentSoft" : o.fill || "panel2", hi ? "accent" : null);
      d.text(sl, gate ? l.text : hi ? l.hi : l, { x: cx, y, w: cw, h, fontSize: o.size || 9.5, bold: true, color: gate ? "background" : "text", align: "center", valign: "middle" });
      if (i < labels.length - 1) d.arrow(sl, cx + cw + 0.02, y + h / 2, gap - 0.04, 0, "accent");
    });
  }
  // 丸数字の注記（モックのページの右列）
  function notes(sl, x, y, w, items) {
    items.forEach((it, i) => {
      const yy = y + i * 1.12;
      dot(sl, x, yy + 0.02, 0.34, "accent");
      d.text(sl, String(i + 1), { x, y: yy + 0.02, w: 0.34, h: 0.34, fontSize: 11, bold: true, color: "background", align: "center", valign: "middle" });
      d.text(sl, it[0], { x: x + 0.46, y: yy, w: w - 0.46, h: 0.38, fontSize: 12, bold: true, color: "text", valign: "middle" });
      d.text(sl, it[1], { x: x + 0.46, y: yy + 0.38, w: w - 0.46, h: 0.66, fontSize: 9.5, valign: "top" });
    });
  }
  // モックの画面の枠。ブラウザのバー・アプリのヘッダ・左のメニューを描き、中身の領域を返す
  function screen(sl, x, y, w, h, urlPath, active, opt) {
    const o = opt || {};
    rr(sl, x, y, w, h, "panel1", "rule", 0.06);
    // ブラウザのバー
    d.box(sl, x + 0.02, y + 0.02, w - 0.04, 0.24, "panel2", false);
    ["warn", "caution", "accent"].forEach((c, i) => dot(sl, x + 0.1 + i * 0.13, y + 0.08, 0.09, c));
    rr(sl, x + 0.55, y + 0.05, Math.min(3.2, w - 0.7), 0.16, "background", null, 0.08);
    d.text(sl, "factory.example.com/" + urlPath, { x: x + 0.62, y: y + 0.05, w: 3.0, h: 0.16, fontSize: 6.5, color: "mute", valign: "middle" });
    // アプリのヘッダ
    const hy = y + 0.3;
    rr(sl, x + 0.12, hy + 0.04, 0.3, 0.2, "accent", null, 0.04);
    d.text(sl, "NDF", { x: x + 0.12, y: hy + 0.04, w: 0.3, h: 0.2, fontSize: 6, bold: true, color: "background", align: "center", valign: "middle" });
    d.text(sl, "Neural Development Factory", { x: x + 0.48, y: hy + 0.04, w: 2.2, h: 0.2, fontSize: 7.5, bold: true, color: "text", valign: "middle" });
    if (!o.noInbox) pill(sl, x + w - 1.95, hy + 0.04, 0.82, 0.2, "対応待ち  3", "caution", "background", 6.5);
    pill(sl, x + w - 1.05, hy + 0.04, 0.93, 0.2, "新しいスプリント", "accent", "background", 6.5);
    d.box(sl, x + 0.02, hy + 0.3, w - 0.04, 0.01, "rule", false);
    // 左のメニュー
    const my = hy + 0.42;
    ["スプリント", "対応待ち", "スプリントを始める", "バックログ", "設定"].forEach((m, i) => {
      const on = m === active;
      if (on) rr(sl, x + 0.08, my + i * 0.26, 1.0, 0.22, "accentSoft", null, 0.04);
      d.text(sl, m, { x: x + 0.16, y: my + i * 0.26, w: 0.9, h: 0.22, fontSize: 6.5, bold: on, color: on ? "accent" : "ink", valign: "middle" });
    });
    return { x: x + 1.2, y: hy + 0.42, w: w - 1.32, h: h - (hy - y) - 0.52 };
  }
  // フェーズの 5 本の棒（done 本は濃く、cur 番目は薄く）
  function phaseBar(sl, x, y, w, done, cur) {
    cols(5, 0.03, x, w).forEach((c, i) => d.box(sl, c.x, y, c.w, 0.05, i < done ? "accent" : i === cur ? "accentSoft" : "rule", false));
  }
  function tx(sl, t, x, y, w, h, size, opt) {
    d.text(sl, t, Object.assign({ x, y, w, h, fontSize: size || 7, valign: "middle" }, opt || {}));
  }

  // 1. タイトル
  s = d.cover("AI が回すソフトウェア工場", "NDF から Neural Development Factory へ", "Software Factory / NDF / Neural Development Factory");
  dot(s, 6.9, 1.6, 3.6, "header", 78);
  dot(s, 7.9, 0.9, 2.2, "accent", 80);
  dot(s, 6.3, 3.6, 1.2, "warn", 82);
  d.text(s, "SOFTWARE FACTORY  ×  AI AGENTS", { x: 1.02, y: 0.86, w: 6, h: 0.3, fontSize: 11, bold: true, color: "accent", charSpacing: 3 });
  d.text(s, [{ text: REPO, options: { hyperlink: { url: REPO }, color: "accent" } }], { x: 1.02, y: 4.2, w: 6, h: 0.3, fontSize: 11 });

  // ==== 章 1 ====
  sec("Software Factory という考え方", "人は「何を作るか」と「出してよいか」を決める");

  // 2. Software Factory とは
  s = pg("Software Factory とは", "仕様を入れると、テスト済みのソフトウェアが出てくる");
  rr(s, X0, 1.62, 1.7, 1.4, "panel2");
  d.text(s, [{ text: "INPUT\n", options: { fontSize: 9, bold: true, color: "accent", charSpacing: 2 } }, { text: "Issue・仕様", options: { fontSize: 14, bold: true, color: "text" } }], { x: X0, y: 1.62, w: 1.7, h: 1.4, align: "center", valign: "middle" });
  d.arrow(s, 2.0, 2.32, 0.34, 0, "accent");
  rr(s, 2.4, 1.36, 5.2, 1.92, "panel1", "accent");
  d.text(s, "工場（AI エージェントが動かすライン）", { x: 2.56, y: 1.44, w: 4.9, h: 0.3, fontSize: 10.5, bold: true, color: "accent", valign: "middle" });
  flow(s, 2.56, 1.92, 4.88, 0.7, ["設計", "実装", "検査", "リリース"], { size: 12 });
  d.text(s, "並列に何本でも", { x: 2.56, y: 2.78, w: 4.88, h: 0.3, fontSize: 9.5, color: "mute", align: "center", valign: "middle" });
  d.arrow(s, 7.66, 2.32, 0.34, 0, "accent");
  rr(s, 8.04, 1.62, 1.7, 1.4, "panel2");
  d.text(s, [{ text: "OUTPUT\n", options: { fontSize: 9, bold: true, color: "accent", charSpacing: 2 } }, { text: "テスト済みの\nソフトウェア", options: { fontSize: 13, bold: true, color: "text" } }], { x: 8.04, y: 1.62, w: 1.7, h: 1.4, align: "center", valign: "middle" });
  cols(2).forEach((c, i) => {
    const r = [["人が決める 1", "何を作るか"], ["人が決める 2", "出してよいか"]][i];
    rr(s, c.x, 3.6, c.w, 1.0, "caution");
    d.text(s, r[0], { x: c.x + 0.2, y: 3.66, w: c.w - 0.4, h: 0.3, fontSize: 10, bold: true, color: "background", valign: "middle" });
    d.text(s, r[1], { x: c.x + 0.2, y: 3.96, w: c.w - 0.4, h: 0.54, fontSize: 20, bold: true, color: "background", valign: "middle" });
  });

  // 3. 「工場」は新しい言葉ではない
  s = pg("「工場」は新しい言葉ではない", "作業者が人から AI に替わり、50 年前の構想が現実に");
  d.box(s, 0.7, 2.6, 8.6, 0.03, "rule", false);
  [
    { x: X0, year: "1970 年前後", head: "日本のソフトウェア工場", body: ["日立・東芝・NEC・富士通", "標準工程・部品の再利用・品質管理", "作業者は人"], fill: "panel1", acc: "header" },
    { x: 5.08, year: "2026 年", head: "AI エージェントの Software Factory", body: ["StrongDM: 人はコードを書かない・読まない", "Dan Shapiro: 5 段階の最上位は「Dark Factory」", "作業者は AI"], fill: "panel1", acc: "accent" },
  ].forEach((r) => {
    dot(s, r.x + 2.18, 2.5, 0.24, r.acc);
    d.text(s, r.year, { x: r.x, y: 1.4, w: 4.66, h: 0.5, fontSize: 22, bold: true, color: r.acc, align: "center", valign: "middle" });
    d.text(s, r.head, { x: r.x, y: 1.92, w: 4.66, h: 0.4, fontSize: 13, bold: true, color: "text", align: "center", valign: "middle" });
    card(s, r.x, 2.9, 4.66, 1.7, "", bullets(r.body, 12.5), r.fill, { headH: 0.02 });
  });
  d.note(s, [
    { text: "出典: " },
    { text: "Cusumano『Japan’s Software Factories』(1991)", options: { hyperlink: { url: "https://academic.oup.com/book/52424" }, color: "accent" } },
    { text: " / " },
    { text: "StrongDM “The StrongDM Software Factory”(2026-02)", options: { hyperlink: { url: "https://www.strongdm.com/blog/the-strongdm-software-factory-building-software-with-ai" }, color: "accent" } },
    { text: " / " },
    { text: "Dan Shapiro “The Five Levels”(2026-01)", options: { hyperlink: { url: "https://www.danshapiro.com/blog/2026/01/the-five-levels-from-spicy-autocomplete-to-the-software-factory/" }, color: "accent" } },
  ]);

  // 4. 職人型から工場型へ
  s = pg("職人型から工場型へ", "人の仕事は「書く」から「判断する」へ");
  d.tbl(s, [
    [d.H(""), d.H("職人型（いまの AI コーディング）"), d.H("工場型（Software Factory）")],
    [d.B("品質を守るもの", { color: "text" }), "人が読んで直す", { text: "工程と検査のゲート", options: { bold: true, color: "accent" } }],
    [d.B("速さを決めるもの", { color: "text" }), "人の手の数", { text: "並列のラインの数", options: { bold: true, color: "accent" } }],
    [d.B("知識の置き場", { color: "text" }), "人の頭の中", { text: "ルールと手順のファイル", options: { bold: true, color: "accent" } }],
    [d.B("人の居場所", { color: "text" }), "AI の隣に座る", { text: "判断が要るときだけ", options: { bold: true, color: "accent" } }],
  ], { x: X0, y: 1.42, w: W, colW: [2.3, 3.59, 3.59], rowH: [0.42, 0.66, 0.66, 0.66, 0.66], fontSize: 14 });

  // 5. 工場に必要な 4 つの部品
  s = pg("工場に必要な 4 つの部品", "NDF は、この 4 つを Skill・スクリプト・hook にした");
  [
    ["01", "標準工程", "どの順に何をするか", "ライン"],
    ["02", "品質ゲート", "不良を次へ流さない", "検査"],
    ["03", "計測", "どこまで進んだか見える", "見える化"],
    ["04", "改善", "詰まりを工程へ戻す", "カイゼン"],
  ].forEach((r, i) => {
    const c = cols(4)[i];
    rr(s, c.x, 1.5, c.w, 2.8, "panel1");
    d.text(s, r[0], { x: c.x + 0.2, y: 1.62, w: c.w - 0.4, h: 0.6, fontSize: 30, bold: true, color: i % 2 ? "header" : "accent", valign: "middle" });
    d.text(s, r[1], { x: c.x + 0.2, y: 2.3, w: c.w - 0.4, h: 0.5, fontSize: 18, bold: true, color: "text", valign: "middle" });
    d.text(s, r[2], { x: c.x + 0.2, y: 2.84, w: c.w - 0.4, h: 0.7, fontSize: 12, valign: "top" });
    pill(s, c.x + 0.2, 3.72, 1.2, 0.3, r[3], "panel2", "accent", 9.5);
  });

  // ==== 章 2 ====
  sec("NDF — 工場のラインをプラグインに", "Claude Code・Codex・Kiro・agy で動く開発ワークフロー");

  // 6. 指示書だけでは、ラインは回らない
  s = pg("指示書だけでは、ラインは回らない", "CLAUDE.md への追記は、すぐに 4 つの壁に当たる");
  [
    ["コンテキスト", "毎回全部読む。書くほど埋もれる"],
    ["強制力", "書いても守られない"],
    ["手順", "順序は文章で伝わらない"],
    ["ポータビリティ", "環境を変えると消える"],
  ].forEach((r, i) => {
    const c = cols(2)[i % 2];
    card(s, c.x, 1.45 + Math.floor(i / 2) * 1.6, c.w, 1.4, r[0], r[1], "panel1", { band: "warn", headSize: 18, bodySize: 15, headH: 0.5 });
  });

  // 7. 実務はスクリプト、AI は規定と監視
  s = pg("実務はスクリプト、AI は規定と監視", "Skill が手順を決めて結果を見張り、スクリプトが毎回同じに実行する");
  rr(s, X0, 1.4, 5.5, 2.3, "accentSoft", "accent");
  d.text(s, [
    { text: "スクリプト", options: { fontSize: 20, bold: true, color: "accent" } },
    { text: "  ＝ 実務を担う", options: { fontSize: 14, bold: true, color: "text" } },
  ], { x: X0 + 0.2, y: 1.5, w: 5.1, h: 0.5, valign: "middle" });
  d.text(s, bullets(["実行・待ち・マージ・リリース", "決まった処理を、毎回同じ結果で", "結果は JSON で返す"], 12.5, "text"), { x: X0 + 0.2, y: 2.04, w: 3.4, h: 1.5, valign: "top", paraSpaceAfter: 4 });
  d.text(s, [
    { text: "186", options: { fontSize: 34, bold: true, color: "accent" } },
    { text: " 本", options: { fontSize: 13, bold: true, color: "text" } },
  ], { x: X0 + 3.7, y: 2.1, w: 1.7, h: 0.7, align: "center", valign: "middle" });
  d.text(s, "NDF のスクリプト", { x: X0 + 3.7, y: 2.8, w: 1.7, h: 0.3, fontSize: 10, align: "center", valign: "middle" });
  rr(s, 6.0, 1.4, 3.74, 1.42, "panel1", "header");
  d.text(s, "Skill（AI エージェント）", { x: 6.18, y: 1.48, w: 3.4, h: 0.4, fontSize: 14, bold: true, color: "header", valign: "middle" });
  d.text(s, bullets(["規定: いつ・何を・どの順で", "監視: 結果を見て、異常なら止める"], 11.5), { x: 6.18, y: 1.9, w: 3.45, h: 0.86, valign: "top", paraSpaceAfter: 3 });
  rr(s, 6.0, 2.96, 3.74, 0.74, "panel1");
  d.text(s, [
    { text: "hook", options: { fontSize: 14, bold: true, color: "caution" } },
    { text: "  ルールを強制する", options: { fontSize: 11.5, color: "ink" } },
  ], { x: 6.18, y: 2.96, w: 3.45, h: 0.74, valign: "middle" });
  rr(s, X0, 3.88, W, 0.68, "panel2");
  d.text(s, [
    { text: "プラグイン", options: { fontSize: 14, bold: true, color: "text" } },
    { text: "  まとめて配る", options: { fontSize: 11.5, color: "ink" } },
  ], { x: X0 + 0.2, y: 3.88, w: 2.9, h: 0.68, valign: "middle" });
  ["Claude Code", "Codex", "Kiro", "agy"].forEach((r, i) => {
    pill(s, 3.3 + i * 1.6, 4.04, 1.44, 0.36, r, i % 2 ? "header" : "accent", "background", 11);
  });
  d.note(s, [
    { text: "186 本は plugins/ndf の .py・.sh（テストを除く、2026-10-31）。公式: " },
    { text: "Claude Code プラグイン", options: { hyperlink: { url: "https://code.claude.com/docs/ja/plugins" }, color: "accent" } },
    { text: " / " },
    { text: "Claude Code Skills", options: { hyperlink: { url: "https://code.claude.com/docs/ja/skills" }, color: "accent" } },
    { text: " / " },
    { text: "Codex Skills", options: { hyperlink: { url: "https://developers.openai.com/codex/skills" }, color: "accent" } },
    { text: " / " },
    { text: "Agent Skills（agentskills.io）", options: { hyperlink: { url: "https://agentskills.io" }, color: "accent" } },
  ]);

  // 8. NDF: 工場のラインをプラグインに
  s = pg("NDF: 工場のラインをプラグインに", "GitHub で公開。NDF 自身も NDF のラインで開発");
  [
    ["160", "回", "リリース（2026-04〜10）", "accent"],
    ["49", "個", "Skill", "header"],
    ["4", "つ", "ランタイム", "accent"],
    ["10+", "リポジトリ", "自分以外へ導入", "header"],
  ].forEach((r, i) => {
    const c = cols(4)[i];
    stat(s, c.x, 1.5, c.w, 1.75, r[0], r[1], r[2], r[3]);
  });
  d.text(s, [{ text: REPO, options: { hyperlink: { url: REPO }, color: "accent" } }], { x: X0, y: 3.62, w: W, h: 0.4, fontSize: 16, bold: true, align: "center", valign: "middle" });
  d.note(s, "リリースは版数の数（git の履歴）、Skill は Claude Code 版のマニフェストの行数。2026-10-31 時点。導入先は 2026-10-17 時点");

  // 9. Issue をまとめて渡せば、並列に進む
  s = pg("Issue をまとめて渡せば、並列に進む", "人が判断するのは 2 回の承認だけ");
  rr(s, X0, 1.62, 1.55, 0.9, "panel2");
  d.text(s, "マイルストーン\n（複数の Issue）", { x: X0, y: 1.62, w: 1.55, h: 0.9, fontSize: 10, bold: true, color: "text", align: "center", valign: "middle" });
  d.arrow(s, 1.85, 2.07, 0.3, 0, "accent");
  rr(s, 2.19, 1.62, 1.55, 0.9, "panel2");
  d.text(s, "触る場所と依存で\nグループに分ける", { x: 2.19, y: 1.62, w: 1.55, h: 0.9, fontSize: 10, bold: true, color: "text", align: "center", valign: "middle" });
  d.arrow(s, 3.78, 2.07, 0.3, 0, "accent");
  rr(s, 4.12, 1.36, 4.0, 1.98, "panel1", "accent");
  d.text(s, "スプリント（グループごとに並列）", { x: 4.24, y: 1.4, w: 3.8, h: 0.28, fontSize: 10, bold: true, color: "accent", valign: "middle" });
  ["グループ A", "グループ B", "グループ C"].forEach((g, i) => {
    const y = 1.74 + i * 0.52;
    d.text(s, g, { x: 4.24, y, w: 0.9, h: 0.4, fontSize: 9, valign: "middle" });
    flow(s, 5.12, y, 2.9, 0.4, ["設計", "実装", "レビュー"], { gap: 0.18, size: 9 });
  });
  d.arrow(s, 8.16, 2.35, 0.3, 0, "accent");
  rr(s, 8.5, 1.9, 1.24, 0.9, "panel2");
  d.text(s, "リリース", { x: 8.5, y: 1.9, w: 1.24, h: 0.9, fontSize: 11, bold: true, color: "text", align: "center", valign: "middle" });
  cols(2).forEach((c, i) => {
    const g = [["承認 1: 設計", "設計をまとめて 1 回"], ["承認 2: 本番リリース", "開発版で確かめて 1 回"]][i];
    card(s, c.x, 3.56, c.w, 0.9, g[0], g[1], "caution", { headSize: 12.5, bodySize: 11.5, headH: 0.3, headColor: "background", bodyColor: "background" });
  });
  d.note(s, "判断の材料（決めたこと・未検証の点・根拠）はそろって届く");

  // 10. 変更の大きさで、踏む工程を変える
  s = pg("変更の大きさで、踏む工程を変える", "/ndf:development-workflow — 変更を 5 つのモードに分ける");
  d.tbl(s, [
    [d.H("モード"), d.H("対象"), d.H("通る工程")],
    [d.B("light", { color: "accent" }), "文言・設定の小さな変更", "受け入れ条件 → 実装 → クロスレビュー"],
    [d.B("operation", { color: "accent" }), "コードを変えない外部の作業", "範囲と戻し方を書く → 承認 → 実行と記録"],
    [d.B("standard", { color: "accent" }), "機能追加・バグ修正", "要件定義 → 設計 → TDD → … → 振り返り"],
    [d.B("legacy-refactor", { color: "accent" }), "テストの薄いコードの整理", "現状固定テスト → 整理 → リリース後テスト"],
    [d.B("documentation", { color: "accent" }), "提案書・報告書などの文書", "構成案 → 執筆 → 再構成 → 体裁レビュー"],
  ], { x: X0, y: 1.42, w: W, colW: [2.2, 3.4, 3.88], rowH: 0.52, fontSize: 12.5 });

  // 11. 機能追加は、この流れで 1 周
  s = pg("機能追加は、この流れで 1 周", "standard モード: スプリントごとに 1 周、中はグループごとに並列");
  [
    ["設計", [{ hi: "要件定義" }, "作業用の\nworktree", "設計", { hi: "構成の\n見直し" }, { hi: "クロス\nレビュー" }, { text: "承認 1\n設計" }]],
    ["実装", ["実装計画", { hi: "テスト\nファースト" }, { hi: "クロス\nリファクタ" }, { hi: "クロス\nレビュー" }, { hi: "完了判定\n（証跡）" }, "マージ"]],
    ["リリース", ["仕様書\n確定", "開発版\nリリース", { text: "承認 2\n本番" }, "本番\nリリース", "リリース後\nテスト", { hi: "振り返り" }, { hi: "Issue の\n棚卸し" }]],
  ].forEach((row, i) => {
    const y = 1.48 + i * 1.08;
    d.text(s, row[0], { x: X0, y, w: 0.8, h: 0.7, fontSize: 11, bold: true, color: "accent", valign: "middle" });
    const n = row[1].length;
    const g = 0.2;
    flow(s, 1.06, y, (8.68 * n + g * (n - 7)) / 7, 0.7, row[1], { gap: g, size: 9.5 });
    if (i < 2) d.arrow(s, X0 + 0.3, y + 0.76, 0, 0.26, "accent");
  });
  d.note(s, "淡いシアンの工程は、次のページ以降で紹介する。レビューの指摘は直して繰り返す。開発版は自分で使って確かめてから承認 2 へ");

  // 12. 要件定義・テストファースト: 実績のある技法を組み込む
  s = pg("要件定義・テストファースト: 実績のある技法を組み込む", "新しい発明ではない。AI が毎回守れる手順にした");
  [
    ["DDD", "ユビキタス言語とドメインモデル", ["要件定義の前に用語集", "1 語 1 意味を CI が検査", "設計はドメインモデルから", "モデルを先にレビュー"]],
    ["TDD", "テストファースト", ["失敗するテストから", "- → 実装 → 整理", "薄いコードは先にテスト"]],
    ["Agile", "スプリントと振り返り", ["マイルストーン ＝ バックログ", "スプリントごとに振り返り", "優先順位は WSJF"]],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    rr(s, c.x, 1.45, c.w, 3.1, "panel1");
    d.text(s, r[0], { x: c.x + 0.2, y: 1.55, w: c.w - 0.4, h: 0.6, fontSize: 26, bold: true, color: i === 1 ? "header" : "accent", valign: "middle" });
    d.text(s, r[1], { x: c.x + 0.2, y: 2.15, w: c.w - 0.4, h: 0.4, fontSize: 12, bold: true, color: "text", valign: "middle" });
    d.text(s, bullets(r[2], 12.5), { x: c.x + 0.2, y: 2.7, w: c.w - 0.4, h: 1.7, valign: "top", paraSpaceAfter: 4 });
  });

  // 13. 構成の見直し: コードの可読性から、日本語の可読性の時代へ
  s = pg("構成の見直し: コードの可読性から、日本語の可読性の時代へ", "/ndf:document-rebuild — 「読みやすさの研究」の成果を取り入れて文章を組み直す", 19.5);
  d.box(s, 0.6, 1.98, 8.8, 0.03, "rule", false);
  [
    ["19 世紀", "逆ピラミッド", "新聞。大事なことから", "https://helpcenter.veeam.com/docs/styleguide/tw/inverted_pyramid.html"],
    ["20 世紀前半〜", "IMRAD", "論文の決まった構成", "https://en.wikipedia.org/wiki/IMRAD"],
    ["1960 年代", "Information Mapping", "情報の型で見せ方を", "https://en.wikipedia.org/wiki/Structured_writing"],
    ["米陸軍の規程", "BLUF", "結論を最初の一文に", "https://en.wikipedia.org/wiki/BLUF_(communication)"],
    ["1990", "ミニマリズム", "書きすぎない", "https://en.wikipedia.org/wiki/Minimalism_(technical_communication)"],
    ["日本語", "日本語スタイルガイド", "可読性の式も", "https://jtca.org/learn-tc/publication/guide_jsg/"],
  ].forEach((r, i) => {
    const c = cols(6, 0.08)[i];
    const acc = i === 5 ? "caution" : i % 2 ? "header" : "accent";
    d.text(s, r[0], { x: c.x, y: 1.4, w: c.w, h: 0.4, fontSize: 11.5, bold: true, color: acc, align: "center", valign: "middle" });
    dot(s, c.x + c.w / 2 - 0.1, 1.895, 0.2, acc);
    d.text(s, [{ text: r[1], options: { hyperlink: { url: r[3] }, color: "text" } }], { x: c.x - 0.04, y: 2.14, w: c.w + 0.08, h: 0.5, fontSize: 10.5, bold: true, align: "center", valign: "middle" });
    d.text(s, r[2], { x: c.x, y: 2.62, w: c.w, h: 0.3, fontSize: 9.5, align: "center", valign: "middle" });
  });
  d.tbl(s, [
    [d.H("組み直した前後（実測）"), d.H("文書 A"), d.H("文書 B"), d.H("文書 C")],
    [d.B("結論の位置", { color: "text" }), "158 → 1 行目", "23 → 1 行目", "14 → 5 行目"],
    [d.B("平均の文の長さ", { color: "text" }), "46 → 33 字", "40 → 30 字", "88 → 31 字"],
    [d.B("一番長い文", { color: "text" }), "186 → 105 字", "95 → 75 字", "131 → 73 字"],
  ], { x: X0, y: 3.1, w: 6.1, colW: [2.2, 1.3, 1.3, 1.3], rowH: [0.36, 0.38, 0.38, 0.38], fontSize: 10.5, align: "center" });
  rr(s, 6.54, 3.1, 3.2, 1.5, "panel1", "accent");
  d.text(s, "document-rebuild の手順", { x: 6.7, y: 3.16, w: 2.9, h: 0.32, fontSize: 11.5, bold: true, color: "accent", valign: "middle" });
  d.text(s, [
    { text: "1. 測る", options: { breakLine: true } },
    { text: "2. 読み手が問う順へ並べ替える", options: { breakLine: true } },
    { text: "3. 文を整え、別の AI に読ませる", options: { breakLine: true } },
    { text: "4. 測り直して前後を並べる" },
  ], { x: 6.7, y: 3.5, w: 2.96, h: 1.04, fontSize: 10.5, valign: "top", paraSpaceAfter: 2 });
  d.note(s, [
    { text: "実測は #391 の 3 本（#389・#390・#339）。内容は足さず、並びと文だけを変えた。根拠の一覧は #391 の「参考」（ほかに " },
    { text: "jReadability", options: { hyperlink: { url: "https://jreadability.net/sys/" }, color: "accent" } },
    { text: "・Carroll『The Nurnberg Funnel』）" },
  ]);

  // 14. クロスレビュー・リファクタ: 複数の AI で見て直す
  s = pg("クロスレビュー・リファクタ: 複数の AI で見て直す", "見落としは別の AI が拾い、直すのは時間内に");
  [
    ["/ndf:cross-review — 指摘が尽きるまで", ["2 つの AI が\nレビュー", "指摘を修正", "組み替えて\n繰り返す"], "新しい指摘が出なくなったら終了", "観点は PR の中身で切り替え"],
    ["/ndf:cross-refactoring — 時間内に直す", ["全員の AI が\n提案", "1 つが既定\n30 分で実施", "壊れたら\n取り消す"], "数値は持ち時間から逆算", "採用と取り消しを報告"],
  ].forEach((r, i) => {
    const c = cols(2, 0.3)[i];
    rr(s, c.x, 1.42, c.w, 3.1, "panel1");
    d.box(s, c.x + 0.14, 1.42, 0.5, 0.05, i ? "header" : "accent", false);
    d.text(s, r[0], { x: c.x + 0.16, y: 1.54, w: c.w - 0.32, h: 0.36, fontSize: 13, bold: true, color: "text", valign: "middle" });
    flow(s, c.x + 0.16, 2.04, c.w - 0.32, 0.96, r[1], { gap: 0.2, size: 12 });
    d.text(s, bullets([r[2], r[3]], 13.5), { x: c.x + 0.16, y: 3.2, w: c.w - 0.32, h: 1.1, valign: "middle", paraSpaceAfter: 8 });
  });

  // 15. 完了判定: 「完了」も「無い」も証拠で
  s = pg("完了判定: 「完了」も「無い」も証拠で", "工場の検査工程。AI の言葉を、そのままは信じない");
  [
    ["「完了しました」", "/ndf:quality-gates — 完了の判定", "4 点がそろって完了\nコマンド・終了コード・時刻・範囲"],
    ["「該当のコードはありません」", "/ndf:investigation-rules — 調査の規則", "検索のコマンドと結果を添える\nAI は「無い」と断定しがち"],
  ].forEach((r, i) => {
    const c = cols(2)[i];
    rr(s, c.x, 1.48, c.w, 0.72, "panel2");
    d.text(s, r[0], { x: c.x, y: 1.48, w: c.w, h: 0.72, fontSize: 15, bold: true, color: "warn", align: "center", valign: "middle" });
    d.arrow(s, c.x + c.w / 2, 2.26, 0, 0.3, "accent");
    card(s, c.x, 2.62, c.w, 1.6, r[1], r[2], "panel1", { band: "accent", headSize: 12.5, bodySize: 13 });
  });

  // 16. 3 層のエージェントで止まらない進行
  s = pg("3 層のエージェントで止まらない進行", "長い作業も、止まったら続きから");
  [
    ["conductor", "指揮役。人と話し全体を進める", "accent"],
    ["supervisor", "監督役。1 つのフェーズを通す", "header"],
    ["worker", "作業役。調査や修正を行う", "panel2"],
  ].forEach((r, i) => {
    const y = 1.46 + i * 1.02;
    rr(s, X0 + i * 0.3, y, 1.75, 0.74, r[2]);
    d.text(s, r[0], { x: X0 + i * 0.3, y, w: 1.75, h: 0.74, fontSize: 13, bold: true, color: i === 0 ? "background" : "text", align: "center", valign: "middle" });
    d.text(s, r[1], { x: 2.16 + i * 0.3, y, w: 2.95, h: 0.74, fontSize: 12, valign: "middle" });
    if (i < 2) d.arrow(s, 1.1 + i * 0.3, y + 0.76, 0.2, 0.24, "accent");
  });
  card(s, 5.7, 1.46, 4.04, 1.36, "止まっても続きから", "JSON の計画をスクリプトが実行。\n進行は DBOS（耐久ワークフロー）が記録", "panel1", { band: "accent", headSize: 14, bodySize: 12.5 });
  card(s, 5.7, 3.0, 4.04, 1.48, "セッションを自動で切り替え", "次のコマンドを残し、NDF Relay が起動し直す", "panel1", { band: "header", headSize: 14, bodySize: 12.5 });
  d.note(s, [
    { text: "DBOS: " },
    { text: "dbos.dev", options: { hyperlink: { url: "https://www.dbos.dev" }, color: "accent" } },
    { text: "（Python 版 " },
    { text: "dbos-transact-py", options: { hyperlink: { url: "https://github.com/dbos-inc/dbos-transact-py" }, color: "accent" } },
    { text: "）。落ちた後に同じコマンドを打ち直すと、流れていたステップから続く" },
  ]);

  // 17. NDF Relay: コンテキストを溜めずに走り続ける
  s = pg("NDF Relay: コンテキストを溜めずに走り続ける", "会話が膨らむと判断が鈍る。区切りで新しいセッションへ移す（relay.py）");
  rr(s, X0, 1.36, 5.9, 1.5, "panel1", "warn");
  d.text(s, "会話が長くなると", { x: X0 + 0.2, y: 1.42, w: 5.5, h: 0.32, fontSize: 12.5, bold: true, color: "warn", valign: "middle" });
  d.text(s, bullets(["判断の質が落ちる（目安 100k Token、遅くとも 200k で切る）", "応答のたびに会話を丸ごと読み直し、費用が増える", "自動の要約は情報を落とし、落ちたことに気づけない"], 11.5), { x: X0 + 0.2, y: 1.76, w: 5.55, h: 1.04, valign: "top", paraSpaceAfter: 3 });
  rr(s, 6.32, 1.36, 3.42, 1.5, "panel1");
  d.text(s, [
    { text: "410k", options: { fontSize: 32, bold: true, color: "warn" } },
    { text: " Token", options: { fontSize: 12.5, bold: true, color: "text" } },
  ], { x: 6.48, y: 1.42, w: 3.1, h: 0.66, valign: "middle" });
  d.text(s, "conductor の会話の平均。200k を超えた会話は 45 件中 37 件", { x: 6.48, y: 2.1, w: 3.14, h: 0.7, fontSize: 10.5, valign: "top" });
  flow(s, X0, 3.04, W, 0.72, ["200k Token か\n工程の区切り", "引継ぎ文書と\n次のコマンドを残す", "NDF Relay が\n/exit・更新・起動", "新しいセッションで\n続きから"], { size: 11.5, gap: 0.3 });
  [
    ["承認は代わりに答えない", "caution"],
    ["利用上限は別のアカウントで続ける", "panel2"],
    ["1 日 20 回まで。空回りで止まる", "panel2"],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    pill(s, c.x, 3.96, c.w, 0.5, r[0], r[1], r[1] === "caution" ? "background" : "text", 11.5);
  });
  d.note(s, "410k は NDF 自身の開発の 30 日間の実測（#827）。導入は /ndf:install-wrapper を 1 回、Claude Code だけが対象");

  // 18. 迷ったら MVV で判断する
  s = pg("迷ったら MVV で判断する", "判断の根拠の番号をログに残す");
  [["Mission", "何のためにやるか"], ["Vision", "目指す姿"], ["Value", "判断の基準"]].forEach((r, i) => {
    const c = cols(3, 0.16, X0, 5.9)[i];
    rr(s, c.x, 1.5, c.w, 1.36, "panel1");
    d.text(s, r[0], { x: c.x, y: 1.6, w: c.w, h: 0.56, fontSize: 18, bold: true, color: i === 1 ? "header" : "accent", align: "center", valign: "middle" });
    d.text(s, r[1], { x: c.x, y: 2.16, w: c.w, h: 0.5, fontSize: 12, align: "center", valign: "middle" });
  });
  card(s, 6.4, 1.5, 3.34, 1.36, "必ず人が承認する操作", "本番デプロイなど。AI は省かない", "caution", { headSize: 13, bodySize: 12, headColor: "background", bodyColor: "background" });
  rr(s, X0, 3.1, W, 0.62, "panel2");
  d.text(s, "Value の例:「まず届ける」「処理の改善に寄与しない指摘は直さない」", { x: X0 + 0.2, y: 3.1, w: W - 0.4, h: 0.62, fontSize: 13, color: "text", valign: "middle" });
  d.text(s, "AI に任せる範囲は、事前に設定で選ぶ", { x: X0, y: 3.95, w: W, h: 0.4, fontSize: 12, bold: true, color: "accent", align: "center", valign: "middle" });

  // 19. 振り返り・棚卸し: 詰まりを工程へ戻して直す
  s = pg("振り返り・棚卸し: 詰まりを工程へ戻して直す", "工場の改善。Issue の一覧を、いつも現状に");
  [
    ["/ndf:out-of-scope — 見つけたら起票", "範囲外の問題を即 Issue に"],
    ["/ndf:retrospective — 振り返り", "リリースごとに進め方を見直す"],
    ["/ndf:backlog-refinement — 一覧の棚卸", "スプリント終了後にバックログを現状に合わせて再整備"],
  ].forEach((r, i) => {
    const y = 1.42 + i * 1.08;
    card(s, X0, y, 4.9, 0.92, r[0], r[1], "panel1", { headSize: 12, bodySize: 11, headH: 0.28, band: i === 1 ? "header" : "accent" });
    if (i < 2) d.arrow(s, 2.7, y + 0.93, 0, 0.14, "accent");
  });
  stat(s, 5.4, 1.42, 4.34, 1.44, "56", "%", "本文が現状とずれた open の Issue（43 件中 24 件）", "warn");
  stat(s, 5.4, 3.0, 4.34, 1.5, "49", "件", "本文が古かった Issue（71 件を調査）", "caution");
  d.note(s, "56% はある時点の棚卸し、49 件は 2026-10-17 の調査。放っておくと、直った Issue を直すことになる");

  // 20. 事例: ほかのプロジェクトでも動く
  s = pg("事例: ほかのプロジェクトでも動く", "言語もチームも違うプロジェクト。詰まりは次の版で直る");
  d.tbl(s, [
    [d.H("プロジェクトの種類"), d.H("結果")],
    [d.B("AWS のインフラ", { color: "text" }), "マージ PR 0.95 → 3.38 件/日（社内ツールの移行と重なる）"],
    [d.B("PHP の業務システム（20 人）", { color: "text" }), "設計の承認から 51 時間で実装 PR 31 本"],
    [d.B("機械学習のモデル", { color: "text" }), "データ欠損を発見し 1 スプリントで本番へ。相関係数 0.73 → 0.954"],
    [d.B("価格推定", { color: "text" }), "PR 数は同じ。レビュー必須の開発へ"],
  ], { x: X0, y: 1.42, w: W, colW: [2.9, 6.58], rowH: [0.38, 0.62, 0.62, 0.62, 0.62], fontSize: 12.5 });
  d.note(s, "インフラは導入前後 37 日の比較。PHP では最初に 17 件の Issue が上がり、次の版で直った。数値は 2026-10-17 時点の実測");

  // ==== 中締め（章番号を持たない扉）====
  // ここまでは実装して実際に使っている NDF、ここからは構想の Neural Development Factory
  s = d.section("ここまでは実践、ここからは構想", null);
  d.text(s, "中締め", { x: 0.9, y: 1.3, w: 3, h: 0.45, fontSize: 18, bold: true, color: "accent", valign: "middle" });
  [
    ["ここまで", "COMBAT PROVEN", "NDF は実装済み。\n実際のプロジェクトで稼働中", "accent", "panel1"],
    ["ここから", "VISION", "Neural Development Factory は\nこれから作る構想。画面はモック", "header", "panel2"],
  ].forEach((r, i) => {
    const x = 0.9 + i * 4.45;
    rr(s, x, 3.1, 3.95, 1.75, r[4], r[3]);
    d.text(s, r[0], { x: x + 0.22, y: 3.22, w: 1.4, h: 0.4, fontSize: 14, bold: true, color: "text", valign: "middle" });
    pill(s, x + 3.95 - 0.22 - 1.6, 3.27, 1.6, 0.3, r[1], r[3], "background", 10);
    d.text(s, r[2], { x: x + 0.22, y: 3.7, w: 3.5, h: 1.0, fontSize: 13, bold: true, color: "ink", valign: "middle" });
    if (i === 0) d.arrow(s, x + 3.99, 3.97, 0.42, 0, "accent");
  });

  // ==== 章 3 ====
  sec("Neural Development Factory", "工場を Web へ。CLI の前にいなくても回る");

  // 21. CLI の工場には、人が張り付く
  s = pg("CLI の工場には、人が張り付く", "ラインは自動なのに、人がラインの横から離れられない");
  d.text(s, "いま（CLI の NDF）", { x: X0, y: 1.4, w: 4.3, h: 0.34, fontSize: 12, bold: true, color: "warn", valign: "middle" });
  d.text(s, "Neural Development Factory", { x: 5.44, y: 1.4, w: 4.3, h: 0.34, fontSize: 12, bold: true, color: "accent", valign: "middle" });
  [
    ["承認のたびに、CLI の前へ", "通知が届き、Web・Slack から承認"],
    ["進捗が Issue・ボード・ログに散らばる", "一覧と詳細の 1 か所で見える"],
    ["並列のスプリントを一望できない", "複数のスプリントを同時に流して見張る"],
  ].forEach((r, i) => {
    const y = 1.84 + i * 0.95;
    rr(s, X0, y, 4.3, 0.76, "panel1", "warn");
    d.text(s, r[0], { x: X0 + 0.2, y, w: 3.9, h: 0.76, fontSize: 13, bold: true, color: "text", valign: "middle" });
    d.arrow(s, 4.64, y + 0.38, 0.72, 0, "accent");
    rr(s, 5.44, y, 4.3, 0.76, "accentSoft", "accent");
    d.text(s, r[1], { x: 5.64, y, w: 3.9, h: 0.76, fontSize: 13, bold: true, color: "text", valign: "middle" });
  });
  d.note(s, "セッションの切り替えは NDF Relay で自動になった。残るのは、人が判断する場面");

  // 22. Neural Development Factory の構想
  s = pg("Neural Development Factory の構想", "NDF を Web から操作できる Software Factory のサービスに");
  [
    ["01", "登録する", "GitHub の\nリポジトリを取り込む"],
    ["02", "始める", "複数の Issue で\nスプリントを開始"],
    ["03", "見る", "一覧と詳細で\nフェーズと工程を確認"],
    ["04", "承認する", "通知を受けて\nWeb・Slack から"],
  ].forEach((r, i) => {
    const c = cols(4, 0.3)[i];
    rr(s, c.x, 1.45, c.w, 2.1, "panel1", i === 3 ? "caution" : null);
    d.text(s, r[0], { x: c.x + 0.18, y: 1.55, w: c.w - 0.36, h: 0.5, fontSize: 22, bold: true, color: i === 3 ? "caution" : "accent", valign: "middle" });
    d.text(s, r[1], { x: c.x + 0.18, y: 2.08, w: c.w - 0.36, h: 0.46, fontSize: 17, bold: true, color: "text", valign: "middle" });
    d.text(s, r[2], { x: c.x + 0.18, y: 2.6, w: c.w - 0.36, h: 0.8, fontSize: 11, valign: "top" });
    if (i < 3) d.arrow(s, c.x + c.w + 0.03, 2.5, 0.24, 0, "accent");
  });
  cols(3).forEach((c, i) => pill(s, c.x, 3.9, c.w, 0.5, ["承認ゲートは 2 つのまま", "プラグインの NDF は OSS のまま", "スプリントを並列に"][i], "panel2", i === 0 ? "caution" : "accent", 12));

  // 23. 仕組み: 承認を待つ間はラインを止める
  s = pg("仕組み: 承認を待つ間はラインを止める", "進捗は 1 本の事象の列。画面・通知・課金はそこから作る");
  rr(s, X0, 1.45, 1.9, 0.86, "panel2");
  d.text(s, "画面\nWeb・Slack", { x: X0, y: 1.45, w: 1.9, h: 0.86, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });
  d.arrow(s, 2.2, 1.88, 0.36, 0, "accent");
  rr(s, 2.6, 1.45, 1.8, 0.86, "panel2");
  d.text(s, "API\n認証・アカウント", { x: 2.6, y: 1.45, w: 1.8, h: 0.86, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });
  d.arrow(s, 4.44, 1.88, 0.36, 0, "accent");
  rr(s, 4.84, 1.36, 2.84, 1.64, "panel1", "accent");
  d.text(s, "実行ジョブ（コンテナ 1 つ）", { x: 4.98, y: 1.42, w: 2.6, h: 0.3, fontSize: 10.5, bold: true, color: "accent", valign: "middle" });
  d.text(s, bullets(["NDF をマーケットプレイスから導入", "conductor → supervisor → worker", "版はスプリントの開始で固定"], 9.5), { x: 4.98, y: 1.76, w: 2.62, h: 1.18, valign: "top", paraSpaceAfter: 3 });
  d.arrow(s, 7.72, 1.88, 0.36, 0, "accent");
  rr(s, 8.12, 1.45, 1.62, 0.86, "panel2");
  d.text(s, "GitHub\nPR・CI", { x: 8.12, y: 1.45, w: 1.62, h: 0.86, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });
  d.arrow(s, 6.26, 3.04, 0, 0.3, "accent");
  rr(s, 4.84, 3.38, 2.84, 0.62, "header");
  d.text(s, "進捗の事象（追記だけ）", { x: 4.84, y: 3.38, w: 2.84, h: 0.62, fontSize: 12, bold: true, color: "onHeader", align: "center", valign: "middle" });
  d.arrow(s, 4.8, 3.69, -0.4, 0, "accent");
  rr(s, X0, 3.38, 4.1, 0.62, "panel2");
  d.text(s, "一覧・詳細・通知・課金", { x: X0, y: 3.38, w: 4.1, h: 0.62, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });
  d.arrow(s, 1.2, 3.34, 0, -0.98, "accent");
  flow(s, X0, 4.22, W, 0.5, [{ text: "承認待ちになる" }, "コンテナを止める（費用 0）", "答えが届く", "新しいジョブが続きから"], { size: 10.5, gap: 0.3 });

  // 24. AI をどう動かすかは 3 つから選ぶ
  s = pg("AI をどう動かすかは 3 つから選ぶ", "アカウントごとに選ぶ。どれでもスプリントは同じに流れる");
  d.tbl(s, [
    [d.H("実行の形態"), d.H("Claude をどう呼ぶか"), d.H("Token の費用")],
    [d.B("① 手元のランナー", { color: "accent" }), "自分の PC・サーバーで、自分がログインした claude\nサービスは Claude の認証情報を持たない", "自分持ち"],
    [d.B("② キーの持ち込み", { color: "accent" }), "自分の Anthropic API キーか、自分の AWS の Bedrock", "自分持ち"],
    [d.B("③ サービスの Bedrock", { color: "accent" }), "サービスの AWS の Bedrock", "使った分をサブスクリプションに上乗せ"],
  ], { x: X0, y: 1.42, w: W, colW: [2.3, 4.88, 2.3], rowH: [0.38, 0.74, 0.56, 0.56], fontSize: 11.5 });
  d.text(s, "プラン", { x: X0, y: 3.96, w: 0.8, h: 0.44, fontSize: 12, bold: true, color: "text", valign: "middle" });
  cols(4, 0.14, 1.1, 8.64).forEach((c, i) => pill(s, c.x, 3.96, c.w, 0.44, ["Free（①・②）", "Pro", "Max", "Team（席: standard / premium）"][i], i ? "panel2" : "accentSoft", "text", 11));

  // 25. 画面: スプリント一覧
  s = pg("画面: スプリント一覧", "どのスプリントが、いまどのフェーズにいるかが一目で");
  let a = screen(s, X0, 1.3, 6.5, 3.5, "sprints", "スプリント");
  [["同時に動いている", "2 / 3"], ["あなたの対応待ち", "3"], ["今月の Token", "8.4M / 20M"]].forEach((k, i) => {
    const c = cols(3, 0.08, a.x, a.w)[i];
    rr(s, c.x, a.y, c.w, 0.5, "panel2", i === 1 ? "caution" : null, 0.05);
    tx(s, k[0], c.x + 0.08, a.y + 0.03, c.w - 0.16, 0.18, 6.5, { color: "mute" });
    tx(s, k[1], c.x + 0.08, a.y + 0.2, c.w - 0.16, 0.26, 11, { bold: true, color: i === 1 ? "caution" : "text" });
  });
  const ty = a.y + 0.62;
  [["スプリント", 0], ["フェーズ", 1.15], ["状態", 2.85], ["最後の動き", 4.2]].forEach((h) => tx(s, h[0], a.x + h[1], ty, 1.2, 0.18, 6.5, { color: "mute" }));
  [
    ["m2171", "devbasex/ai-plugins", 1, 1, "実装", "実行中", "accentSoft", "accent", "2 分前"],
    ["m2186", "devbasex/ai-plugins", 0, 0, "設計", "承認待ち（ゲート 1）", "caution", "background", "15 分前"],
    ["s0412", "acme/web-shop", 2, 2, "検査", "判断待ち", "warn", "text", "1 時間前"],
    ["r0077", "acme/api", 3, 3, "取り込み", "承認待ち（ゲート 2）", "caution", "background", "3 時間前"],
    ["m2170", "devbasex/ai-plugins", 5, -1, "仕上げ", "終わった", "header", "text", "10/30"],
  ].forEach((r, i) => {
    const y = ty + 0.22 + i * 0.39;
    d.box(s, a.x, y - 0.02, a.w, 0.01, "rule", false);
    tx(s, r[0], a.x, y, 1.1, 0.2, 8, { bold: true, color: "accent" });
    tx(s, r[1], a.x, y + 0.18, 1.1, 0.16, 5.5, { color: "mute" });
    phaseBar(s, a.x + 1.15, y + 0.08, 1.5, r[2], r[3]);
    tx(s, r[4], a.x + 1.15, y + 0.16, 1.5, 0.18, 6.5, { bold: true, color: "text" });
    pill(s, a.x + 2.85, y + 0.06, 1.2, 0.22, r[5], r[6], r[7], 6);
    tx(s, r[8], a.x + 4.2, y + 0.06, 0.8, 0.22, 6.5, { color: "mute" });
  });
  notes(s, 7.0, 1.4, 2.74, [
    ["フェーズが一目で", "設計 → 実装 → 検査 → 取り込み → 仕上げの 5 本の棒"],
    ["対応待ちから直行", "承認待ち・判断待ちの行から、そのまま答えへ"],
    ["使える量も見える", "同時に流せる数と、今月の Token"],
  ]);

  // 26. 画面: スプリント詳細
  s = pg("画面: スプリント詳細", "AI がどこで何を決めたかを、工程ごとに辿れる");
  a = screen(s, X0, 1.3, 6.5, 3.5, "sprints/m2171", "スプリント");
  tx(s, "m2171", a.x, a.y, 0.8, 0.24, 11, { bold: true, color: "text" });
  pill(s, a.x + 0.72, a.y + 0.03, 0.5, 0.18, "実行中", "accentSoft", "accent", 6);
  tx(s, "devbasex/ai-plugins · #2171 contracts と進捗の受け口", a.x + 1.3, a.y, 3.8, 0.24, 6.5, { color: "mute" });
  cols(5, 0.06, a.x, a.w).forEach((c, i) => {
    d.box(s, c.x, a.y + 0.34, c.w, 0.06, i === 0 ? "accent" : i === 1 ? "accentSoft" : "rule", false);
    tx(s, ["設計 完了", "実装 実行中", "検査", "取り込み", "仕上げ"][i], c.x, a.y + 0.42, c.w, 0.18, 6.5, { bold: i < 2, color: i < 2 ? "text" : "mute" });
  });
  const ly = a.y + 0.7;
  tx(s, "工程の記録", a.x, ly, 2.2, 0.2, 7.5, { bold: true, color: "text" });
  [
    ["tdd-cycle を実行中", "失敗するテストを書いた", "accent", true],
    ["implementation-plan", "触るファイル 14 本", "header"],
    ["ゲート 1 を承認した", "Web から承認", "caution"],
    ["cross-review が収束", "2 ラウンド · 未解決 0", "header"],
    ["design", "決定 12 件 · 条件 21 件", "header"],
  ].forEach((r, i) => {
    const y = ly + 0.24 + i * 0.33;
    if (r[3]) rr(s, a.x - 0.04, y - 0.02, 2.36, 0.31, "accentSoft", null, 0.04);
    dot(s, a.x + 0.02, y + 0.06, 0.1, r[2]);
    tx(s, r[0], a.x + 0.18, y - 0.01, 2.1, 0.16, 7, { bold: true, color: "text" });
    tx(s, r[1], a.x + 0.18, y + 0.14, 2.1, 0.14, 5.5, { color: "mute" });
  });
  const px = a.x + 2.45;
  const pw = a.w - 2.45;
  rr(s, px, ly, pw, 2.08, "panel2", "accent", 0.05);
  tx(s, "tdd-cycle を実行中", px + 0.1, ly + 0.05, pw - 0.2, 0.2, 8, { bold: true, color: "text" });
  [["終了コード", "—"], ["秒", "1,082"], ["費用", "$1.84"]].forEach((k, i) => {
    const c = cols(3, 0.05, px + 0.1, pw - 0.2)[i];
    tx(s, k[0], c.x, ly + 0.3, c.w, 0.14, 5.5, { color: "mute" });
    tx(s, k[1], c.x, ly + 0.43, c.w, 0.18, 8, { bold: true, color: "text" });
  });
  tx(s, "触ったファイル", px + 0.1, ly + 0.68, pw - 0.2, 0.15, 6, { bold: true, color: "mute" });
  ["factory/api/src/progress/authorize.ts  +142", "…/authorize.test.ts  +218", "…/run-job-token.ts  +96"].forEach((f, i) => tx(s, f, px + 0.1, ly + 0.84 + i * 0.16, pw - 0.2, 0.15, 6, { color: "accent" }));
  tx(s, "この工程で決めたこと", px + 0.1, ly + 1.36, pw - 0.2, 0.15, 6, { bold: true, color: "mute" });
  tx(s, "403 の本文に理由を載せるか\n→ 載せない（設計の決定 11 による）", px + 0.1, ly + 1.52, pw - 0.2, 0.5, 6.5, { color: "text", valign: "top" });
  notes(s, 7.0, 1.4, 2.74, [
    ["工程の記録を時系列で", "新しいものが上。押すと右に詳細"],
    ["触ったファイルと決定", "GitHub の差分へ飛べる。AI の判断の根拠も残る"],
    ["秒と費用まで", "終了コード・所要時間・費用を工程ごとに"],
  ]);

  // 27. 画面: 承認ゲート
  s = pg("画面: 承認ゲート", "判断の材料は、設計文書・仕様・コードまで辿れる");
  a = screen(s, X0, 1.3, 6.5, 3.5, "inbox/m2186", "対応待ち");
  pill(s, a.x, a.y, 1.9, 0.2, "承認 · ゲート 1 設計 PR のマージ", "caution", "background", 6);
  tx(s, "m2186 · 15 分待っている", a.x + 2.0, a.y, 2.0, 0.2, 6, { color: "mute" });
  tx(s, "実行の制御とランナー: 登録したリポジトリでスプリントを並列に流せる", a.x, a.y + 0.24, a.w, 0.26, 8.5, { bold: true, color: "text" });
  const gw = a.w - 1.62;
  tx(s, "決めたこと（押すと根拠とコードが開く）", a.x, a.y + 0.56, gw, 0.18, 6.5, { bold: true, color: "mute" });
  rr(s, a.x, a.y + 0.76, gw, 0.86, "panel2", "accent", 0.04);
  tx(s, "決定 1: 実行ジョブはコンテナ 1 つ、承認の依頼で終える", a.x + 0.08, a.y + 0.78, gw - 0.16, 0.18, 6.5, { bold: true, color: "text" });
  tx(s, "理由: 承認待ちは数日かかる。待つ間にコンテナの費用を払わない", a.x + 0.08, a.y + 0.98, gw - 0.16, 0.16, 5.8);
  tx(s, "依拠する仕様: 確定仕様「実行の単位と NDF の扱い」", a.x + 0.08, a.y + 1.16, gw - 0.16, 0.16, 5.8, { color: "accent" });
  tx(s, "触るコード: factory/api/src/control/start-job.ts", a.x + 0.08, a.y + 1.34, gw - 0.16, 0.16, 5.8, { color: "accent" });
  ["決定 2: NDF の版はスプリントの開始で固定する", "決定 3: 同時に流せる数を超えたら開始を断る"].forEach((t, i) => {
    rr(s, a.x, a.y + 1.68 + i * 0.26, gw, 0.22, "panel2", null, 0.04);
    tx(s, t, a.x + 0.08, a.y + 1.68 + i * 0.26, gw - 0.16, 0.22, 6.5, { color: "text" });
  });
  [["受け入れ条件", "18 件"], ["未確認", "4 件"], ["レビュー", "収束"]].forEach((k, i) => {
    const c = cols(3, 0.06, a.x, gw)[i];
    rr(s, c.x, a.y + 2.24, c.w, 0.4, "panel2", null, 0.04);
    tx(s, k[0], c.x + 0.06, a.y + 2.25, c.w - 0.12, 0.14, 5.5, { color: "mute" });
    tx(s, k[1], c.x + 0.06, a.y + 2.39, c.w - 0.12, 0.22, 9, { bold: true, color: "text" });
  });
  const ax = a.x + gw + 0.1;
  rr(s, ax, a.y + 0.56, 1.52, 2.08, "panel2", "caution", 0.05);
  tx(s, "答える", ax + 0.08, a.y + 0.6, 1.36, 0.2, 7.5, { bold: true, color: "text" });
  tx(s, "承認すると設計 PR をマージし、実装へ進む", ax + 0.08, a.y + 0.82, 1.36, 0.36, 5.5, { valign: "top" });
  rr(s, ax + 0.08, a.y + 1.2, 1.36, 0.56, "background", "rule", 0.03);
  tx(s, "差し戻す理由", ax + 0.14, a.y + 1.22, 1.2, 0.16, 5.5, { color: "mute" });
  pill(s, ax + 0.08, a.y + 1.86, 1.36, 0.28, "承認する", "accent", "background", 7);
  pill(s, ax + 0.08, a.y + 2.22, 1.36, 0.28, "差し戻す", "background", "warn", 7);
  notes(s, 7.0, 1.4, 2.74, [
    ["決定ごとに根拠を開く", "理由・依拠する仕様・触るコードの差分"],
    ["そろった材料で 1 回", "受け入れ条件・未確認の点・レビューの収束"],
    ["承認か差し戻し", "差し戻しは理由が必須。Slack から開いても同じ画面"],
  ]);

  // 28. 画面: 判断（AI が決められない問い）
  s = pg("画面: 判断（AI が決められない問い）", "費用がかかる・取り消せない問いだけが、人に届く");
  a = screen(s, X0, 1.3, 6.5, 3.5, "inbox/s0412", "対応待ち");
  pill(s, a.x, a.y, 0.9, 0.2, "判断 · 費用", "warn", "text", 6);
  tx(s, "s0412 · acme/web-shop · 検査のフェーズ", a.x + 1.0, a.y, 3.0, 0.2, 6, { color: "mute" });
  tx(s, "e2e テストのために有料の検証環境を起動しますか", a.x, a.y + 0.24, a.w, 0.26, 9, { bold: true, color: "text" });
  rr(s, a.x, a.y + 0.56, a.w, 0.42, "panel2", "warn", 0.04);
  tx(s, "AI が判断しなかった理由", a.x + 0.08, a.y + 0.58, a.w - 0.16, 0.16, 6, { bold: true, color: "warn" });
  tx(s, "費用が発生するため。費用の発生は AI が決めてよい範囲の外にある", a.x + 0.08, a.y + 0.75, a.w - 0.16, 0.2, 6.5, { color: "text" });
  [["見込みの費用", "1 日 $38"], ["使う時間", "約 3 時間"]].forEach((k, i) => {
    const c = cols(2, 0.08, a.x, a.w)[i];
    rr(s, c.x, a.y + 1.06, c.w, 0.42, "panel2", null, 0.04);
    tx(s, k[0], c.x + 0.08, a.y + 1.07, c.w - 0.16, 0.14, 5.5, { color: "mute" });
    tx(s, k[1], c.x + 0.08, a.y + 1.21, c.w - 0.16, 0.24, 10, { bold: true, color: "text" });
  });
  [
    ["unit だけで完了判定して進める", "費用 0。e2e は本番の前の検査で拾う", true],
    ["検証環境を起動して e2e も回す", "約 $5（3 時間分）", false],
    ["ここで止める", "環境を足してから始め直す", false],
  ].forEach((r, i) => {
    const y = a.y + 1.58 + i * 0.36;
    rr(s, a.x, y, a.w, 0.32, r[2] ? "accentSoft" : "panel2", r[2] ? "accent" : null, 0.04);
    dot(s, a.x + 0.08, y + 0.1, 0.12, r[2] ? "accent" : "rule");
    tx(s, r[0], a.x + 0.28, y + 0.01, 2.6, 0.16, 6.8, { bold: true, color: "text" });
    tx(s, r[1], a.x + 0.28, y + 0.16, 3.4, 0.14, 5.5, { color: "mute" });
    if (r[2]) pill(s, a.x + a.w - 0.62, y + 0.07, 0.54, 0.18, "推奨", "accent", "background", 6);
  });
  pill(s, a.x + a.w - 1.7, a.y + 2.7, 1.7, 0.26, "指示を送って再開する", "accent", "background", 7);
  notes(s, 7.0, 1.4, 2.74, [
    ["承認ゲートとは別", "ゲート外の問いは hook が止め、AI に判断させる"],
    ["人に回すのは一部だけ", "費用・取り消せない操作・人にしかできない操作"],
    ["理由と費用を先に", "推奨の選択肢つき。答えは AI への指示になる"],
  ]);

  // 29. 画面: スプリントを始める
  s = pg("画面: スプリントを始める", "AI が棚卸しの順位から、1 版に収まる Issue を選ぶ");
  a = screen(s, X0, 1.3, 6.5, 3.5, "sprints/new", "スプリントを始める");
  const nw = a.w - 1.62;
  tx(s, "1  リポジトリ", a.x, a.y, nw, 0.2, 7.5, { bold: true, color: "text" });
  [["devbasex/ai-plugins", ".ndf/ あり", true], ["acme/web-shop", "判断待ち 1", false], ["acme/api", ".ndf/ 無し（既定で動く）", false]].forEach((r, i) => {
    const y = a.y + 0.24 + i * 0.25;
    rr(s, a.x, y, nw, 0.22, r[2] ? "accentSoft" : "panel2", r[2] ? "accent" : null, 0.04);
    dot(s, a.x + 0.07, y + 0.06, 0.1, r[2] ? "accent" : "rule");
    tx(s, r[0], a.x + 0.24, y, 1.6, 0.22, 6.5, { bold: true, color: "text" });
    tx(s, r[1], a.x + 1.9, y, nw - 2.0, 0.22, 5.5, { color: "mute" });
  });
  tx(s, "2  スプリントに入れる Issue", a.x, a.y + 1.04, 2.4, 0.2, 7.5, { bold: true, color: "text" });
  pill(s, a.x + 2.0, a.y + 1.05, 0.8, 0.18, "AI が選んだ", "header", "text", 5.5);
  [
    ["#2186 実行の制御とランナー", "順位 1 · 前提がそろった", true],
    ["#2206 hook: ゲート外の問いを AI に", "順位 2 · 同じ版に収まる", true],
    ["#2173 承認の通知", "前提 #2186 が未完了", false],
    ["#2174 課金", "決済の提供元が未決", false],
  ].forEach((r, i) => {
    const y = a.y + 1.28 + i * 0.27;
    rr(s, a.x, y, nw, 0.24, r[2] ? "panel2" : "panel1", r[2] ? "accent" : "rule", 0.04);
    rr(s, a.x + 0.06, y + 0.06, 0.12, 0.12, r[2] ? "accent" : "panel1", r[2] ? null : "mute", 0.02);
    tx(s, r[0], a.x + 0.24, y, 2.4, 0.24, 6.5, { bold: true, color: r[2] ? "text" : "mute" });
    tx(s, r[1], a.x + 2.66, y, nw - 2.7, 0.24, 5.5, { color: "mute" });
  });
  const cx2 = a.x + nw + 0.1;
  rr(s, cx2, a.y, 1.52, 2.62, "panel2", "rule", 0.05);
  tx(s, "始める前の確認", cx2 + 0.08, a.y + 0.04, 1.36, 0.2, 7, { bold: true, color: "text" });
  [["同時のスプリント", "2 → 3 / 3"], ["今月の Token", "8.4M / 20M"], ["実行の形態", "③ サービスの Bedrock"], ["NDF の版", "10.26.0"]].forEach((k, i) => {
    tx(s, k[0], cx2 + 0.08, a.y + 0.3 + i * 0.4, 1.36, 0.14, 5.5, { color: "mute" });
    tx(s, k[1], cx2 + 0.08, a.y + 0.44 + i * 0.4, 1.36, 0.18, 7, { bold: true, color: "text" });
  });
  pill(s, cx2 + 0.08, a.y + 2.04, 1.36, 0.28, "スプリントを始める", "accent", "background", 6.5);
  tx(s, "止まるのは承認の 2 回だけ", cx2 + 0.08, a.y + 2.36, 1.36, 0.18, 5.5, { color: "mute", align: "center" });
  notes(s, 7.0, 1.4, 2.74, [
    ["リポジトリを選ぶ", "GitHub App で登録。.ndf/ の設定を読む"],
    ["Issue は AI が選ぶ", "棚卸しの順位から、前提がそろい 1 版に収まる組"],
    ["上限を見てから開始", "同時の数・Token・NDF の版を確認"],
  ]);

  // 30. 現在地: 子 Issue で順に作っている
  s = pg("現在地: 子 Issue で順に作っている", "このサービス自体も、NDF のラインでスプリントとして開発中");
  const ST = { done: ["完了", "header", "text"], ship: ["配布済み", "header", "text"], merged: ["実装済み", "accent", "background"], wip: ["実装へ", "caution", "background"], next: ["次", "panel2", "mute"] };
  [
    ["#2169", "置き場の分離と全体設計", "done", "v10.25.0"],
    ["#2170", "認証とプラン", "ship", "v10.26.0"],
    ["#2171", "契約と進捗の受け口", "merged", ""],
    ["#2186", "実行の制御とランナー", "wip", "設計を承認"],
    ["#2172", "スプリントの一覧と詳細", "next", ""],
    ["#2173", "承認の通知（Slack）と Web からの承認", "next", ""],
    ["#2174", "サブスクリプションと Token の課金", "next", ""],
    ["#2189", "手元のランナー", "next", ""],
  ].forEach((r, i) => {
    const col = i < 4 ? 0 : 1;
    const c = cols(2, 0.3)[col];
    const y = 1.45 + (i % 4) * 0.78;
    rr(s, c.x, y, c.w, 0.64, "panel1");
    const st = ST[r[2]];
    d.text(s, r[0], { x: c.x + 0.16, y, w: 0.8, h: 0.64, fontSize: 12, bold: true, color: "accent", valign: "middle" });
    d.text(s, r[1], { x: c.x + 0.96, y: y + 0.04, w: c.w - 2.3, h: r[3] ? 0.36 : 0.56, fontSize: 11.5, bold: true, color: "text", valign: "middle" });
    if (r[3]) d.text(s, r[3], { x: c.x + 0.96, y: y + 0.36, w: c.w - 2.3, h: 0.24, fontSize: 9, color: "mute", valign: "middle" });
    pill(s, c.x + c.w - 1.2, y + 0.17, 1.04, 0.3, st[0], st[1], st[2], 10);
  });
  d.note(s, "親 Issue: #2168。2026-10-31 時点");

  // 31. まとめ: 人は 2 つだけ決める
  s = pg("まとめ: 人は 2 つだけ決める", "あとは工場が回す");
  // Software Factory + NDF = Neural Development Factory の式
  const BW = 2.7, OW = (W - BW * 3) / 2;
  [
    ["Software Factory", "考え方。人は「何を作るか」と\n「出してよいか」だけを決める", "caution", "panel1"],
    ["NDF", "工場のラインを\nプラグインにした", "accent", "panel1"],
    ["Neural Development Factory", "そのラインを\nWeb から回すサービス", "header", "panel2"],
  ].forEach((r, i) => {
    const x = X0 + i * (BW + OW);
    rr(s, x, 1.5, BW, 2.0, r[3], r[2]);
    d.text(s, r[0], { x: x + 0.1, y: 1.6, w: BW - 0.2, h: 0.78, fontSize: 15, bold: true, color: r[2], align: "center", valign: "middle" });
    d.text(s, r[1], { x: x + 0.12, y: 2.42, w: BW - 0.24, h: 0.92, fontSize: 12, bold: true, color: "text", align: "center", valign: "middle" });
    if (i < 2) d.text(s, i === 0 ? "+" : "=", { x: x + BW, y: 1.5, w: OW, h: 2.0, fontSize: 40, bold: true, color: "text", align: "center", valign: "middle" });
  });
  d.text(s, [{ text: REPO, options: { hyperlink: { url: REPO }, color: "accent" } }], { x: X0, y: 3.86, w: W, h: 0.5, fontSize: 18, bold: true, align: "center", valign: "middle" });
});
