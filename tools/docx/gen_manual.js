// 《项目操作、功能说明、二次开发与调试手册》生成器（docx-js，DS-1/R1）
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  PageBreak, Header, Footer, PageNumber, NumberFormat, SectionType,
  AlignmentType, HeadingLevel, WidthType, BorderStyle, ShadingType,
  TableOfContents, TableLayoutType, VerticalAlign,
} = require("docx");
const fs = require("fs");

// ── 调色板（DS-1 Deep Sea）──
const PAL = {
  bg: "0B1C2C", accent: "529286",
  cover: { titleColor: "FFFFFF", subtitleColor: "B0B8C0", metaColor: "90989F", footerColor: "687078" },
  table: { headerBg: "529286", headerText: "FFFFFF", innerLine: "BECFCC", surface: "E8ECEB" },
};
const NB = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: NB, bottom: NB, left: NB, right: NB };
const allNoBorders = { ...noBorders, insideHorizontal: NB, insideVertical: NB };

// ── 封面工具（design-system 标准实现）──
function splitTitleLines(title, cpl) {
  const lines = []; let rest = title;
  while (rest.length > cpl) { lines.push(rest.slice(0, cpl)); rest = rest.slice(cpl); }
  if (rest) lines.push(rest);
  return lines;
}
function calcTitleLayout(title, maxWidthTwips, preferredPt = 40, minPt = 24) {
  const charsPerLine = (pt) => Math.floor(maxWidthTwips / (pt * 20));
  let titlePt = preferredPt, lines;
  while (titlePt >= minPt) {
    const cpl = charsPerLine(titlePt);
    if (cpl < 2) { titlePt -= 2; continue; }
    lines = splitTitleLines(title, cpl);
    if (lines.length <= 3) break;
    titlePt -= 2;
  }
  if (!lines || lines.length > 3) lines = splitTitleLines(title, charsPerLine(minPt));
  return { titlePt, titleLines: lines };
}
function calcCoverSpacing({ titleLineCount = 1, titlePt = 36, hasSubtitle = false,
  hasEnglishLabel = false, metaLineCount = 0, fixedHeight = 800, pageHeight = 16838 }) {
  const SAFETY = 1200;
  const usable = pageHeight - SAFETY;
  const contentHeight = titleLineCount * (titlePt * 23 + 200)
    + (hasSubtitle ? 12 * 23 + 600 : 0) + (hasEnglishLabel ? 9 * 23 + 600 : 0)
    + metaLineCount * (10 * 23 + 100) + fixedHeight + 3 * 300;
  const remaining = Math.max(usable - contentHeight, 400);
  const FOOTER_MIN = 800;
  const rawTop = Math.floor(remaining * 0.45), rawBottom = Math.floor(remaining * 0.45);
  const bottomSpacing = Math.max(rawBottom, FOOTER_MIN);
  const topSpacing = Math.max(rawTop - Math.max(0, FOOTER_MIN - rawBottom), 400);
  return { topSpacing, bottomSpacing };
}
function buildCoverR1(config) {
  const P = config.palette;
  const padL = 1200, padR = 800;
  const { titlePt, titleLines } = calcTitleLayout(config.title, 11906 - padL - padR - 300, 40, 24);
  const spacing = calcCoverSpacing({
    titleLineCount: titleLines.length, titlePt,
    hasSubtitle: !!config.subtitle, hasEnglishLabel: !!config.englishLabel,
    metaLineCount: (config.metaLines || []).length, fixedHeight: 400,
  });
  const accentLeft = { style: BorderStyle.SINGLE, size: 8, color: P.accent, space: 12 };
  const children = [new Paragraph({ spacing: { before: spacing.topSpacing } })];
  if (config.englishLabel) children.push(new Paragraph({
    indent: { left: padL, right: padR }, spacing: { after: 500 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: P.accent, space: 8 } },
    children: [new TextRun({ text: config.englishLabel.split("").join("  "), size: 18,
      color: P.accent, font: { ascii: "Calibri", eastAsia: "SimHei" }, characterSpacing: 40 })],
  }));
  for (let i = 0; i < titleLines.length; i++) children.push(new Paragraph({
    indent: { left: padL },
    spacing: { after: i < titleLines.length - 1 ? 100 : 300, line: Math.ceil(titlePt * 23), lineRule: "atLeast" },
    children: [new TextRun({ text: titleLines[i], size: titlePt * 2, bold: true,
      color: P.cover.titleColor, font: { eastAsia: "SimHei", ascii: "Arial" } })],
  }));
  if (config.subtitle) children.push(new Paragraph({
    indent: { left: padL }, spacing: { after: 800 },
    children: [new TextRun({ text: config.subtitle, size: 24, color: P.cover.subtitleColor,
      font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
  }));
  for (const line of (config.metaLines || [])) children.push(new Paragraph({
    indent: { left: padL + 200 }, spacing: { after: 80 }, border: { left: accentLeft },
    children: [new TextRun({ text: line, size: 24, color: P.cover.metaColor,
      font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
  }));
  children.push(new Paragraph({ spacing: { before: spacing.bottomSpacing } }));
  children.push(new Paragraph({
    indent: { left: padL, right: padR },
    border: { top: { style: BorderStyle.SINGLE, size: 2, color: P.accent, space: 8 } },
    spacing: { before: 200 },
    children: [
      new TextRun({ text: config.footerLeft || "", size: 16, color: P.cover.footerColor, font: { ascii: "Arial" } }),
      new TextRun({ text: "                                        " }),
      new TextRun({ text: config.footerRight || "", size: 16, color: P.cover.footerColor, font: { ascii: "Arial" } }),
    ],
  }));
  return [new Table({
    width: { size: 100, type: WidthType.PERCENTAGE }, layout: TableLayoutType.FIXED,
    borders: allNoBorders,
    rows: [new TableRow({
      height: { value: 16838, rule: "exact" },
      children: [new TableCell({
        shading: { type: ShadingType.CLEAR, fill: P.bg }, borders: noBorders,
        verticalAlign: VerticalAlign.TOP, children,
      })],
    })],
  })];
}

// ── 正文渲染助手 ──
const FONT_BODY = { eastAsia: "SimSun", ascii: "Times New Roman" };
const FONT_HEAD = { eastAsia: "SimHei", ascii: "Arial" };
const FONT_CODE = { ascii: "Consolas", eastAsia: "Microsoft YaHei" };

function H(level, text) {
  const map = { 1: [HeadingLevel.HEADING_1, 30], 2: [HeadingLevel.HEADING_2, 26], 3: [HeadingLevel.HEADING_3, 24] };
  const [h, size] = map[level];
  return new Paragraph({
    heading: h, spacing: { before: level === 1 ? 360 : 240, after: 140, line: 312 },
    children: [new TextRun({ text, bold: true, size, color: "14483C", font: FONT_HEAD })],
  });
}
function P(text) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED, spacing: { line: 312, after: 80 },
    indent: { firstLine: 480 },
    children: [new TextRun({ text, size: 21, font: FONT_BODY })],
  });
}
function PIN(text) {
  return new Paragraph({
    spacing: { line: 312, before: 60, after: 40 }, keepNext: true,
    children: [new TextRun({ text, bold: true, size: 21, font: { eastAsia: "SimHei", ascii: "Arial" } })],
  });
}
function CODE(text) {
  const lines = text.split("\n");
  return lines.map((line, i) => new Paragraph({
    spacing: { line: 260, before: i === 0 ? 80 : 0, after: i === lines.length - 1 ? 120 : 0 },
    shading: { type: ShadingType.CLEAR, fill: "F2F5F3" },
    indent: { left: 240, right: 240 },
    children: [new TextRun({ text: line.length ? line : " ", size: 18, font: FONT_CODE, color: "1F3328" })],
  }));
}
function NOTE(text) {
  return new Paragraph({
    spacing: { line: 312, before: 100, after: 120 }, indent: { left: 240, right: 240 },
    shading: { type: ShadingType.CLEAR, fill: "FBF3DC" },
    border: { left: { style: BorderStyle.SINGLE, size: 12, color: "B8860B", space: 8 } },
    children: [
      new TextRun({ text: "注意：", bold: true, size: 21, font: FONT_HEAD, color: "7A5C00" }),
      new TextRun({ text, size: 21, font: FONT_BODY }),
    ],
  });
}
function TBL({ head, rows, w }) {
  const widths = w || head.map(() => Math.floor(100 / head.length));
  const mkCell = (text, isHead, i) => new TableCell({
    children: [new Paragraph({
      spacing: { line: 276 },
      children: [new TextRun({ text: String(text), bold: isHead, size: isHead ? 20 : 20,
        color: isHead ? PAL.table.headerText : "1F2A24",
        font: { eastAsia: isHead ? "SimHei" : "SimSun", ascii: "Arial" } })],
    })],
    shading: { type: ShadingType.CLEAR, fill: isHead ? PAL.table.headerBg : "FFFFFF" },
    margins: { top: 60, bottom: 60, left: 120, right: 120 },
    width: { size: widths[i], type: WidthType.PERCENTAGE },
  });
  return [
    new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      borders: {
        top: { style: BorderStyle.SINGLE, size: 4, color: PAL.table.accentLine },
        bottom: { style: BorderStyle.SINGLE, size: 4, color: PAL.table.accentLine },
        left: NB, right: NB,
        insideHorizontal: { style: BorderStyle.SINGLE, size: 1, color: PAL.table.innerLine },
        insideVertical: { style: BorderStyle.SINGLE, size: 1, color: PAL.table.innerLine },
      },
      rows: [
        new TableRow({ tableHeader: true, cantSplit: true, children: head.map((h, i) => mkCell(h, true, i)) }),
        ...rows.map(r => new TableRow({ cantSplit: true, children: r.map((c, i) => mkCell(c, false, i)) })),
      ],
    }),
    new Paragraph({ spacing: { after: 120 }, children: [] }),
  ];
}
function render(blocks) {
  const out = [];
  for (const b of blocks) {
    if (b.h1) out.push(H(1, b.h1));
    else if (b.h2) out.push(H(2, b.h2));
    else if (b.h3) out.push(H(3, b.h3));
    else if (b.p) out.push(P(b.p));
    else if (b.pin) out.push(PIN(b.pin));
    else if (b.code) out.push(...CODE(b.code));
    else if (b.note) out.push(NOTE(b.note));
    else if (b.tbl) out.push(...TBL(b.tbl));
  }
  return out;
}

// ── 页脚 ──
function pageFooter() {
  return new Footer({ children: [new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, font: { ascii: "Arial" }, color: "666666" })],
  })] });
}
function docHeader() {
  return new Header({ children: [new Paragraph({
    alignment: AlignmentType.RIGHT,
    border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: "BECFCC", space: 4 } },
    children: [new TextRun({ text: "供需透镜 · 项目操作、功能说明、二次开发与调试手册", size: 16, color: "8A9A90", font: { eastAsia: "SimSun", ascii: "Arial" } })],
  })] });
}

// ── 组装 ──
const contentA = require("./content_a.js");
const contentB = require("./content_b.js");
const bodyChildren = render([...contentA, ...contentB]);

const cover = buildCoverR1({
  title: "项目操作、功能说明、二次开发与调试手册",
  subtitle: "供需透镜 SupplyLens · 园区再生材料供需匹配与净减排决策系统",
  englishLabel: "SUPPLYLENS TECHNICAL HANDBOOK",
  metaLines: [
    "适用版本：v27（2026-09-18）",
    "技术栈：Python 3.12+ 标准库 · SQLite · 原生前端 · 可选 OR-Tools",
    "面向读者：运行者 / 开发者 / 二次开发与维护者",
  ],
  footerLeft: "供需透镜 SupplyLens", footerRight: "离线 · 确定性 · 数据不出本机",
  palette: PAL,
});

const doc = new Document({
  creator: "SupplyLens",
  title: "项目操作、功能说明、二次开发与调试手册",
  styles: {
    default: {
      document: { run: { size: 21, font: FONT_BODY }, paragraph: { spacing: { line: 312 } } },
    },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, color: "14483C", font: FONT_HEAD },
        paragraph: { spacing: { before: 360, after: 140, line: 312 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, color: "1E5A49", font: FONT_HEAD },
        paragraph: { spacing: { before: 240, after: 120, line: 312 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, color: "1E5A49", font: FONT_HEAD },
        paragraph: { spacing: { before: 200, after: 100, line: 312 }, outlineLevel: 2 } },
    ],
  },
  sections: [
    { // 封面：无页码无页眉
      properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 0, bottom: 0, left: 0, right: 0 } } },
      children: cover,
    },
    { // 目录：独立节，不显示页码
      properties: {
        type: SectionType.NEXT_PAGE,
        page: { size: { width: 11906, height: 16838 },
                margin: { top: 1417, bottom: 1417, left: 1701, right: 1417 } },
      },
      children: [
        new Paragraph({
          alignment: AlignmentType.CENTER, spacing: { before: 480, after: 360 },
          children: [new TextRun({ text: "目  录", bold: true, size: 32, font: FONT_HEAD })],
        }),
        new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
        new Paragraph({
          spacing: { before: 200 },
          children: [new TextRun({
            text: "说明：本目录由域代码生成。编辑文档后请在目录上右键选择「更新域」以刷新页码。",
            italics: true, size: 18, color: "888888", font: FONT_BODY })],
        }),
        new Paragraph({ children: [new PageBreak()] }),
      ],
    },
    { // 正文：阿拉伯页码从 1 起
      properties: {
        type: SectionType.NEXT_PAGE,
        page: { size: { width: 11906, height: 16838 },
                margin: { top: 1417, bottom: 1417, left: 1701, right: 1417 },
                pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL } },
      },
      headers: { default: docHeader() },
      footers: { default: pageFooter() },
      children: bodyChildren,
    },
  ],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync("项目操作、功能说明、二次开发与调试手册.docx", buf);
  console.log("docx written:", buf.length, "bytes");
});
