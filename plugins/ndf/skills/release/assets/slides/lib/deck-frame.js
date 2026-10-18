"use strict";
// ページの枠組み（汎用テンプレート）。pptxgenjs で 16:9（10 × 5.625 インチ）の pptx を作る。
//
// - 座標は FRAME の定数だけが持つ。トーン＆マナー（theme.json）は座標を持たない
// - 書体と色の値は DEFAULT_THEME と theme.json だけが持つ。関数に渡す色は theme.colors の項目名で受ける
// - 台本（script.md）を渡すと、ページを作るたびに同じ番号の読み上げをスピーカーノートへ付け、
//   保存の前に台本のページ数と作ったページ数を照合する
//
// 資料のディレクトリへ写して使う。写した後に書き換えない（プロジェクトの値は theme.json へ書く）。

const fs = require("fs");
const path = require("path");

const EXIT_INPUT = 1; // 引数・theme.json・ページ数の照合の失敗
const EXIT_SCRIPT = 2; // 台本の形の失敗

class DeckError extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
  }
}

// ---- 座標（インチ）。トーン＆マナーに依らない ----
const FRAME = Object.freeze({
  slide: { w: 10, h: 5.625 },
  title: { x: 0.26, y: 0.15, w: 8.29, h: 0.63 },
  rule: { x: 0.24, y: 0.8, w: 9.52 },
  logo: { x: 8.78, y: 0.24, w: 0.96, h: 0.23 },
  lead: { x: 0.26, y: 0.9, w: 9.5, h: 0.34 },
  copyright: { x: 0.26, y: 5.18, w: 3.2, h: 0.3 },
  pageNo: { x: 9.27, y: 5.18, w: 0.5, h: 0.3 },
  note: { x: 0.26, y: 4.88, w: 9.5, h: 0.28 },
  body: { x: 0.26, y: 1.34, w: 9.48 },
  callout: { x: 0.26, w: 9.48, padX: 0.22, headH: 0.28 },
  section: {
    band: { x: 0, y: 0, w: 0.36, h: 5.625 },
    title: { x: 0.9, y: 1.9, w: 8.4, h: 0.8 },
    accent: { x: 0.92, y: 2.82, w: 1.2, width: 4 },
    sub: { x: 0.9, y: 3.0, w: 8.4, h: 0.4 },
    copyright: { x: 0.56, y: 5.18, w: 3.2, h: 0.3 },
  },
  cover: {
    band: { x: 0, y: 0, w: 10, h: 0.36 },
    logo: { x: 0.26, y: 0.6, w: 0.96, h: 0.23 },
    title: { x: 1.02, y: 1.3, w: 8.2, h: 0.72 },
    accent: { x: 1.04, y: 2.1, w: 1.2, width: 4 },
    sub: { x: 1.02, y: 2.26, w: 8.2, h: 0.62 },
    meta: { x: 1.02, y: 3.55, w: 8.2, h: 0.4 },
  },
});

// ---- 既定のトーン＆マナー（会社色を持たない）。正本はここ ----
const DEFAULT_THEME = Object.freeze({
  font: "Noto Sans JP",
  colors: Object.freeze({
    accent: "2B6CB0",
    accentSoft: "E3EDF7",
    text: "000000",
    ink: "303030",
    mute: "7F7F7F",
    rule: "D9D9D9",
    panel1: "F3F3F3",
    panel2: "EEEEEE",
    warn: "F4CCCC",
    caution: "FFF2CC",
    header: "303030",
    onHeader: "FFFFFF",
    background: "FFFFFF",
  }),
  sizes: Object.freeze({
    title: 20,
    lead: 12,
    body: 10,
    table: 10,
    note: 8,
    footer: 9,
    calloutHead: 11.5,
    calloutBody: 9.5,
    sectionTitle: 32,
    sectionSub: 14,
    coverTitle: 28,
    coverSub: 16,
    coverMeta: 12,
  }),
  copyright: "",
  logo: null,
});

const THEME_KEYS = ["font", "colors", "sizes", "copyright", "logo"];
const HEX = /^[0-9A-Fa-f]{6}$/;

// theme.json の値を既定値へ重ねる。colors と sizes は項目ごと、ほかは値ごと置き換える。
function mergeTheme(override, baseDir) {
  const t = {
    font: DEFAULT_THEME.font,
    colors: { ...DEFAULT_THEME.colors },
    sizes: { ...DEFAULT_THEME.sizes },
    copyright: DEFAULT_THEME.copyright,
    logo: DEFAULT_THEME.logo,
  };
  if (override == null) return t;
  if (typeof override !== "object" || Array.isArray(override)) {
    throw new DeckError("theme.json はオブジェクトでなければならない", EXIT_INPUT);
  }
  const unknown = Object.keys(override).filter((k) => !THEME_KEYS.includes(k));
  if (unknown.length) throw new DeckError(`theme.json に知らない項目がある: ${unknown.join(", ")}`, EXIT_INPUT);
  if ("font" in override) {
    if (typeof override.font !== "string" || !override.font) throw new DeckError("theme.json の font は空でない文字列", EXIT_INPUT);
    t.font = override.font;
  }
  for (const group of ["colors", "sizes"]) {
    if (!(group in override)) continue;
    const g = override[group];
    if (typeof g !== "object" || g == null || Array.isArray(g)) throw new DeckError(`theme.json の ${group} はオブジェクト`, EXIT_INPUT);
    const bad = Object.keys(g).filter((k) => !(k in DEFAULT_THEME[group]));
    if (bad.length) throw new DeckError(`theme.json の ${group} に知らない項目がある: ${bad.join(", ")}`, EXIT_INPUT);
    for (const [k, v] of Object.entries(g)) {
      if (group === "colors" && !(typeof v === "string" && HEX.test(v))) {
        throw new DeckError(`theme.json の colors.${k} は 6 桁の 16 進（# なし）: ${v}`, EXIT_INPUT);
      }
      if (group === "sizes" && !(typeof v === "number" && v > 0)) {
        throw new DeckError(`theme.json の sizes.${k} は正の数（pt）: ${v}`, EXIT_INPUT);
      }
      t[group][k] = group === "colors" ? v.toUpperCase() : v;
    }
  }
  if ("copyright" in override) {
    if (typeof override.copyright !== "string") throw new DeckError("theme.json の copyright は文字列", EXIT_INPUT);
    t.copyright = override.copyright;
  }
  if ("logo" in override) {
    if (override.logo === null) {
      t.logo = null;
    } else if (typeof override.logo === "string" && override.logo) {
      const p = path.resolve(baseDir || process.cwd(), override.logo);
      if (!fs.existsSync(p)) throw new DeckError(`theme.json の logo のファイルが無い: ${p}`, EXIT_INPUT);
      t.logo = p;
    } else {
      throw new DeckError("theme.json の logo は PNG の相対パスか null", EXIT_INPUT);
    }
  }
  return t;
}

function loadTheme(theme) {
  if (theme == null) return mergeTheme(null);
  if (typeof theme === "object") return mergeTheme(theme, process.cwd());
  if (!fs.existsSync(theme)) return mergeTheme(null);
  let data;
  try {
    data = JSON.parse(fs.readFileSync(theme, "utf8"));
  } catch (e) {
    throw new DeckError(`theme.json を JSON として読めない: ${theme}: ${e.message}`, EXIT_INPUT);
  }
  return mergeTheme(data, path.dirname(path.resolve(theme)));
}

// ---- 台本（script.md）の読み込み ----
// `## <番号>. <見出し>` がページの区切り。見出しの末尾の `（<秒>秒）` は配分秒数。
// 区切りの中の `>` で始まる行が読み上げ。`>` だけの行は段落の区切り。ほかの行は読まない。
const HEAD = /^##\s+(\d+)\.\s*(.*?)\s*$/;
const SECONDS = /[（(]\s*(\d+)\s*秒\s*[）)]\s*$/;

function plain(s) {
  return s.replace(/\*\*/g, "").replace(/`/g, "");
}

function parseScript(text, name) {
  const pages = [];
  let cur = null;
  let para = [];
  const flush = () => {
    if (cur && para.length) cur.paragraphs.push(para.join(""));
    para = [];
  };
  for (const raw of text.split(/\r?\n/)) {
    const m = raw.match(HEAD);
    if (m) {
      flush();
      let title = m[2];
      let seconds = null;
      const sm = title.match(SECONDS);
      if (sm) {
        seconds = Number(sm[1]);
        title = title.slice(0, sm.index).trim();
      }
      cur = { no: Number(m[1]), title, seconds, paragraphs: [] };
      pages.push(cur);
      continue;
    }
    if (/^#{1,2}\s/.test(raw)) {
      flush();
      cur = null; // 番号の無い見出しの下は読まない
      continue;
    }
    if (!cur) continue;
    const q = raw.match(/^>\s?(.*)$/);
    if (q) {
      const body = q[1].trim();
      if (body) para.push(plain(body));
      else flush();
    } else {
      flush();
    }
  }
  flush();
  if (!pages.length) throw new DeckError(`台本にページの区切り（## <番号>. <見出し>）が無い: ${name}`, EXIT_SCRIPT);
  const seen = new Map();
  for (const p of pages) seen.set(p.no, (seen.get(p.no) || 0) + 1);
  const dup = [...seen].filter(([, c]) => c > 1).map(([n]) => n);
  if (dup.length) throw new DeckError(`台本のページの番号が重なる: ${dup.join(", ")}（${name}）`, EXIT_SCRIPT);
  const max = Math.max(...pages.map((p) => p.no));
  const missing = [];
  for (let i = 1; i <= max; i++) if (!seen.has(i)) missing.push(i);
  if (missing.length) throw new DeckError(`台本のページの番号が欠ける: ${missing.join(", ")}（${name}）`, EXIT_SCRIPT);
  pages.sort((a, b) => a.no - b.no);
  for (const p of pages) p.reading = p.paragraphs.join("\n");
  return pages;
}

function readScript(file) {
  if (!fs.existsSync(file)) throw new DeckError(`台本が無い: ${file}`, EXIT_INPUT);
  return parseScript(fs.readFileSync(file, "utf8"), file);
}

// ---- 表のセル ----
const H = (t, extra) => ({ text: t, options: Object.assign({ fill: { color: "header" }, color: "onHeader", bold: true, align: "center" }, extra || {}) });
const C = (t, extra) => ({ text: t, options: Object.assign({ align: "center" }, extra || {}) });
const B = (t, extra) => ({ text: t, options: Object.assign({ bold: true }, extra || {}) });

// ---- 資料 ----
function createDeck(opts) {
  const o = opts || {};
  const PptxGenJS = require(require.resolve("pptxgenjs", { paths: [o.baseDir || process.cwd(), __dirname] }));
  const theme = loadTheme(o.theme);
  const script = o.script ? readScript(o.script) : null;
  const p = new PptxGenJS();
  p.layout = "LAYOUT_16x9";
  if (o.title) p.title = o.title;
  if (o.author) p.author = o.author;
  if (o.title) p.subject = o.title;
  const F = theme.font;
  const logo = theme.logo ? "image/png;base64," + fs.readFileSync(theme.logo).toString("base64") : null;
  let pageNo = 0;

  function color(name) {
    if (!(name in theme.colors)) throw new DeckError(`色は theme.colors の項目名で指す: ${name}`, EXIT_INPUT);
    return theme.colors[name];
  }
  function size(v) {
    if (typeof v === "number") return v;
    if (!(v in theme.sizes)) throw new DeckError(`文字の大きさは pt か theme.sizes の項目名で指す: ${v}`, EXIT_INPUT);
    return theme.sizes[v];
  }
  // 色の項目名を値へ置き換える（color・fill.color・line.color・border.color）
  function resolve(options) {
    if (!options || typeof options !== "object") return options;
    const r = Array.isArray(options) ? options.map(resolve) : { ...options };
    if (Array.isArray(r)) return r;
    if (typeof r.color === "string") r.color = color(r.color);
    for (const k of ["fill", "line", "border"]) {
      if (r[k] && typeof r[k] === "object" && !Array.isArray(r[k]) && typeof r[k].color === "string") r[k] = { ...r[k], color: color(r[k].color) };
      if (Array.isArray(r[k])) r[k] = r[k].map((b) => (b && typeof b.color === "string" ? { ...b, color: color(b.color) } : b));
    }
    if (r.fontSize !== undefined) r.fontSize = size(r.fontSize);
    return r;
  }
  function runs(t) {
    if (!Array.isArray(t)) return t;
    return t.map((x) => (x && typeof x === "object" ? { ...x, options: resolve(x.options) } : x));
  }

  function newSlide() {
    const s = p.addSlide();
    pageNo++;
    s.background = { color: color("background") };
    if (script) {
      const sp = script[pageNo - 1];
      if (sp && sp.reading) s.addNotes(sp.reading);
    }
    return s;
  }
  function footer(s, cx, withNo, textColor) {
    if (theme.copyright) {
      s.addText(theme.copyright, { ...cx, fontFace: F, fontSize: theme.sizes.footer, color: color(textColor || "mute"), valign: "middle", margin: 0 });
    }
    if (withNo) {
      s.addText(String(pageNo), { ...FRAME.pageNo, fontFace: F, fontSize: theme.sizes.footer, color: color("mute"), align: "right", valign: "middle", margin: 0 });
    }
  }
  function putLogo(s, at) {
    if (logo) s.addImage({ data: logo, ...at });
  }

  // 文字を置く。既定は本文の書体・大きさ・色（ink）。色は項目名で、大きさは pt か項目名で受ける
  function text(s, t, opt) {
    s.addText(runs(t), resolve(Object.assign({ fontFace: F, fontSize: theme.sizes.body, color: "ink", margin: 0, valign: "top" }, opt || {})));
  }

  function cover(title, sub, meta) {
    const s = newSlide();
    const c = FRAME.cover;
    s.addShape(p.ShapeType.rect, { ...c.band, fill: { color: color("accent") }, line: { type: "none" } });
    putLogo(s, c.logo);
    s.addText(title, { ...c.title, fontFace: F, fontSize: theme.sizes.coverTitle, bold: true, color: color("text"), valign: "bottom", margin: 0, fit: "shrink" });
    s.addShape(p.ShapeType.line, { x: c.accent.x, y: c.accent.y, w: c.accent.w, h: 0, line: { color: color("accent"), width: c.accent.width } });
    if (sub) s.addText(sub, { ...c.sub, fontFace: F, fontSize: theme.sizes.coverSub, bold: true, color: color("ink"), valign: "top", margin: 0 });
    if (meta) s.addText(meta, { ...c.meta, fontFace: F, fontSize: theme.sizes.coverMeta, color: color("ink"), margin: 0 });
    footer(s, FRAME.copyright, false);
    return s;
  }

  function page(title, lead) {
    const s = newSlide();
    s.addText(title, { ...FRAME.title, fontFace: F, fontSize: theme.sizes.title, bold: true, color: color("text"), valign: "middle", margin: 0, fit: "shrink" });
    putLogo(s, FRAME.logo);
    s.addShape(p.ShapeType.line, { x: FRAME.rule.x, y: FRAME.rule.y, w: FRAME.rule.w, h: 0, line: { color: color("rule"), width: 1 } });
    if (lead) s.addText(lead, { ...FRAME.lead, fontFace: F, fontSize: theme.sizes.lead, bold: true, color: color("ink"), valign: "middle", margin: 0 });
    footer(s, FRAME.copyright, true);
    return s;
  }

  function section(title, sub) {
    const s = newSlide();
    const c = FRAME.section;
    s.addShape(p.ShapeType.rect, { ...c.band, fill: { color: color("accent") }, line: { type: "none" } });
    putLogo(s, FRAME.logo);
    s.addText(title, { ...c.title, fontFace: F, fontSize: theme.sizes.sectionTitle, bold: true, color: color("text"), margin: 0 });
    s.addShape(p.ShapeType.line, { x: c.accent.x, y: c.accent.y, w: c.accent.w, h: 0, line: { color: color("accent"), width: c.accent.width } });
    if (sub) s.addText(sub, { ...c.sub, fontFace: F, fontSize: theme.sizes.sectionSub, bold: true, color: color("ink"), margin: 0 });
    footer(s, c.copyright, true);
    return s;
  }

  function box(s, x, y, w, h, fill, line) {
    s.addShape(p.ShapeType.rect, { x, y, w, h, fill: { color: color(fill) }, line: line === false ? { type: "none" } : { color: color(line || "rule"), width: 1 } });
  }

  // 箱に文字を中央寄せで載せる（図の部品）
  function chip(s, x, y, w, h, t, fill, opt) {
    box(s, x, y, w, h, fill, false);
    text(s, t, Object.assign({ x, y, w, h, align: "center", valign: "middle", bold: true, color: "text" }, opt || {}));
  }

  // 矢印（x, y から w, h だけ進む）
  function arrow(s, x, y, w, h, lineColor) {
    s.addShape(p.ShapeType.line, { x, y, w, h, line: { color: color(lineColor || "mute"), width: 1.5, endArrowType: "triangle" } });
  }

  function note(s, t) {
    s.addText(runs(t), { ...FRAME.note, fontFace: F, fontSize: theme.sizes.note, color: color("mute"), valign: "middle", margin: 0 });
  }

  function tbl(s, rows, opt) {
    const r = rows.map((row) => row.map((cell) => (cell && typeof cell === "object" ? { ...cell, options: resolve(cell.options) } : cell)));
    s.addTable(r, resolve(Object.assign({ fontFace: F, fontSize: theme.sizes.table, color: "ink", valign: "middle", border: { type: "solid", color: "rule", pt: 1 }, autoPage: false, margin: [2, 4, 2, 4] }, opt || {})));
  }

  function callout(s, y, h, fill, head, body) {
    const c = FRAME.callout;
    box(s, c.x, y, c.w, h, fill, false);
    s.addText(head, { x: c.x + c.padX, y: y + 0.05, w: c.w - 2 * c.padX, h: c.headH, fontFace: F, fontSize: theme.sizes.calloutHead, bold: true, color: color("text"), valign: "middle", margin: 0 });
    if (body) s.addText(runs(body), { x: c.x + c.padX, y: y + 0.05 + c.headH, w: c.w - 2 * c.padX, h: h - c.headH - 0.1, fontFace: F, fontSize: theme.sizes.calloutBody, color: color("ink"), valign: "top", margin: 0 });
  }

  async function save(out) {
    if (!out || !/\.pptx$/i.test(out)) throw new DeckError("出力のパスは .pptx で終える", EXIT_INPUT);
    if (script && script.length !== pageNo) {
      throw new DeckError(`ページ数が台本と合わない: 台本 ${script.length} ページ / 作ったページ ${pageNo}`, EXIT_INPUT);
    }
    await p.writeFile({ fileName: out });
    return { out, pages: pageNo };
  }

  return {
    pptx: p,
    theme,
    frame: FRAME,
    script,
    get pageNo() {
      return pageNo;
    },
    color,
    text,
    cover,
    page,
    section,
    box,
    chip,
    arrow,
    note,
    tbl,
    callout,
    H,
    C,
    B,
    save,
  };
}

// 生成スクリプトの入口。`node build-deck.js <出力>.pptx [<theme.json>]`
// build(deck) でページを作り、保存まで行う。失敗は DeckError の code で終える
function run(dir, opts, build) {
  const [out, themeArg] = process.argv.slice(2);
  (async () => {
    if (!out) throw new DeckError("usage: node build-deck.js <出力>.pptx [<theme.json>]", EXIT_INPUT);
    const theme = themeArg ? path.resolve(themeArg) : path.join(dir, "theme.json");
    if (themeArg && !fs.existsSync(theme)) throw new DeckError(`theme.json が無い: ${theme}`, EXIT_INPUT);
    const deck = createDeck({ ...opts, theme, baseDir: dir });
    await build(deck);
    const r = await deck.save(out);
    console.log(`${r.out}: ${r.pages} ページ`);
  })().catch((e) => {
    console.error(e.message);
    process.exit(e instanceof DeckError ? e.code : 1);
  });
}

module.exports = { createDeck, run, parseScript, readScript, mergeTheme, DeckError, FRAME, DEFAULT_THEME, H, C, B, EXIT_INPUT, EXIT_SCRIPT };
