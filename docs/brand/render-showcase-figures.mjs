#!/usr/bin/env node
/**
 * Render only the README architecture and workflow figures.
 *
 * Requires @napi-rs/canvas (text outlines) and sharp (PNG export). Example:
 *   SHOWCASE_CANVAS_PATH=/path/to/node_modules/@napi-rs/canvas \
 *   SHOWCASE_SHARP_PATH=/path/to/node_modules/sharp \
 *   node docs/brand/render-showcase-figures.mjs \
 *     --latin-font /path/to/latin.ttf --cjk-font /path/to/cjk.ttf
 *
 * macOS system-font defaults are provided for local rebuilding. Font files are
 * never copied into the repository. Final SVGs contain outlined text and an
 * embedded icon PNG, with no scripts or remote resources. This script does not rebuild brand icons,
 * application resources or any other media.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { GlobalFonts, convertSVGTextToPath } = require(process.env.SHOWCASE_CANVAS_PATH || '@napi-rs/canvas');
const sharp = require(process.env.SHOWCASE_SHARP_PATH || 'sharp');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const media = path.join(root, 'docs/media');
const brandIcon = (await fs.readFile(path.join(root, 'docs/brand/icons/icon-128.png'))).toString('base64');

const options = {
  '--latin-font': '/System/Library/Fonts/Avenir Next.ttc',
  '--cjk-font': '/System/Library/Fonts/Hiragino Sans GB.ttc',
};
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index];
  const value = process.argv[index + 1];
  if (!(key in options) || !value || value.startsWith('--')) {
    throw new Error('Usage: render-showcase-figures.mjs [--latin-font FILE] [--cjk-font FILE]');
  }
  options[key] = value;
}
for (const [key, alias] of [['--latin-font', 'Showcase Latin'], ['--cjk-font', 'Showcase CJK']]) {
  await fs.access(options[key]);
  if (!GlobalFonts.registerFromPath(options[key], alias)) {
    throw new Error(`Could not register ${key}`);
  }
}

const C = {
  graphite: '#151A1E',
  jade: '#087D65',
  paper: '#F5F7F5',
  white: '#FFFFFF',
  secondary: '#52635E',
  muted: '#778982',
  line: '#CCD8D0',
  tint: '#E8F1EC',
  frame: '#EDF2EE',
  light: '#D6E3DC',
};

const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const rect = (x, y, w, h, fill, stroke = 'none', radius = 0, width = 2) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="${width}"/>`;
const line = (x1, y1, x2, y2, color = C.line, width = 2) =>
  `<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="${color}" stroke-width="${width}"/>`;
const route = (d, color = C.jade, width = 3) =>
  `<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"/>`;
const circle = (x, y, radius, fill, stroke = 'none', width = 2) =>
  `<circle cx="${x}" cy="${y}" r="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="${width}"/>`;
const text = (value, x, y, size = 26, color = C.graphite, { weight = 400, align = 'start', latin = false, spacing = 0 } = {}) =>
  `<text x="${x}" y="${y}" font-family="${latin ? 'Showcase Latin' : 'Showcase CJK'}" font-size="${size}" font-weight="${weight}" text-anchor="${align}" letter-spacing="${spacing}" fill="${color}">${escape(value)}</text>`;
const arrow = (x, y, color = C.jade, size = 9) =>
  `<path d="M${x - size} ${y - size / 1.3}L${x} ${y}L${x - size} ${y + size / 1.3}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;

function brandHeader(title, category) {
  return text('FundsTrace', 120, 66, 29, C.graphite, { weight: 600, latin: true })
    + text(category, 1376, 64, 19, C.secondary, { align: 'end', latin: true, weight: 500, spacing: 2 })
    + text(title, 64, 126, 40, C.graphite, { weight: 600 })
    + line(64, 158, 1376, 158, C.line, 1.5);
}

function graphIcon(x, y, scale = 1) {
  return `<g transform="translate(${x} ${y}) scale(${scale})">`
    + route('M2 29 21 3 42 19 29 45 2 29', C.jade, 2.5)
    + route('M21 3 29 45', C.light, 2)
    + circle(2, 29, 5, C.jade)
    + circle(21, 3, 5, C.jade)
    + circle(42, 19, 5, C.graphite)
    + circle(29, 45, 5, C.white, C.jade, 2.5)
    + '</g>';
}

function panelIcon(x, y) {
  return rect(x, y, 45, 39, 'none', C.jade, 4, 2.5)
    + line(x, y + 11, x + 45, y + 11, C.jade, 2.5)
    + line(x + 15, y + 11, x + 15, y + 39, C.jade, 2.5)
    + line(x + 23, y + 22, x + 36, y + 22, C.muted, 2.5)
    + line(x + 23, y + 29, x + 36, y + 29, C.muted, 2.5);
}

function database(x, y) {
  return `<g fill="${C.white}" stroke="${C.jade}" stroke-width="2.5">`
    + `<path d="M${x} ${y + 17}V${y + 84}C${x} ${y + 106} ${x + 168} ${y + 106} ${x + 168} ${y + 84}V${y + 17}"/>`
    + `<ellipse cx="${x + 84}" cy="${y + 17}" rx="84" ry="17"/>`
    + `<path d="M${x} ${y + 50}C${x} ${y + 72} ${x + 168} ${y + 72} ${x + 168} ${y + 50}" fill="none"/>`
    + '</g>';
}

function folder(x, y) {
  return `<path d="M${x} ${y + 16}V${y + 3}Q${x} ${y - 3} ${x + 7} ${y - 3}H${x + 66}L${x + 82} ${y + 13}H${x + 160}Q${x + 168} ${y + 13} ${x + 168} ${y + 21}V${y + 88}Q${x + 168} ${y + 96} ${x + 160} ${y + 96}H${x + 8}Q${x} ${y + 96} ${x} ${y + 88}V${y + 16}Z" fill="${C.white}" stroke="${C.jade}" stroke-width="2.5"/>`
    + route(`M${x + 1} ${y + 30}H${x + 167}`, C.jade, 2.5)
    + route(`M${x + 25} ${y + 51}H${x + 91}M${x + 25} ${y + 66}H${x + 68}`, C.line, 3);
}

function architecture() {
  const width = 1440;
  const height = 880;
  let body = rect(0, 0, width, height, C.paper)
    + brandHeader('本机桌面架构', 'ARCHITECTURE')
    + rect(64, 190, 1312, 614, C.frame, C.line, 18, 1.5)
    + text('本机运行', 96, 230, 24, C.secondary, { weight: 500 });

  // Electron owns its windows, sidecar lifecycle and exact-origin request auth.
  body += rect(96, 264, 386, 476, C.white, C.line, 12)
    + '<path d="M108 264H470Q482 264 482 276V364H96V276Q96 264 108 264Z" fill="' + C.graphite + '"/>'
    + text('Electron 桌面宿主', 118, 307, 30, C.paper, { weight: 600 })
    + text('Main', 118, 344, 26, C.paper, { latin: true, weight: 600 })
    + text('进程管理 · 请求认证', 195, 343, 24, '#D6E3DC')
    + graphIcon(121, 400, 0.95)
    + text('G6 图谱工作台', 184, 429, 28, C.graphite, { weight: 600 })
    + text('图谱 · 追踪 · 布局', 118, 474, 24, C.secondary)
    + line(118, 504, 460, 504, C.line, 1.5)
    + panelIcon(120, 541)
    + text('React 试点', 184, 572, 28, C.graphite, { weight: 600 })
    + text('案件 · 导入 · 流水核对', 118, 618, 24, C.secondary)
    + text('两个界面入口', 118, 706, 23, C.muted);

  // The two UI surfaces join one local HTTP service; Main manages its process.
  body += route('M482 316H622', C.secondary, 2.5)
    + arrow(620, 316, C.secondary, 8)
    + text('启动 / 退出', 552, 292, 22, C.secondary, { align: 'middle' })
    + route('M482 443H622M482 583H552V443', C.jade, 3)
    + circle(552, 443, 4.5, C.jade)
    + arrow(620, 443)
    + text('本机 HTTP', 552, 417, 22, C.jade, { align: 'middle' });

  // The sidecar is a separate Python process; service names describe behavior,
  // rather than claiming an unimplemented module migration.
  body += rect(622, 264, 388, 476, C.white, C.line, 12)
    + text('Python sidecar', 646, 309, 32, C.graphite, { latin: true, weight: 600 })
    + text('独立后端进程', 646, 345, 24, C.secondary)
    + rect(646, 397, 340, 92, C.tint, C.jade, 7, 2)
    + text('FastAPI', 816, 454, 34, C.jade, { latin: true, weight: 600, align: 'middle' })
    + route('M816 489V520M724 520H908M724 520V548M908 520V548', C.line, 2.5)
    + rect(646, 548, 156, 102, C.paper, C.line, 7, 1.5)
    + rect(830, 548, 156, 102, C.paper, C.line, 7, 1.5)
    + text('导入服务', 724, 609, 28, C.graphite, { align: 'middle', weight: 500 })
    + text('分析服务', 908, 609, 28, C.graphite, { align: 'middle', weight: 500 });

  // Data and original uploads are deliberately distinct storage destinations.
  body += route('M1010 528H1062V372H1123M1062 528V600H1123', C.jade, 2.5)
    + circle(1062, 528, 4.5, C.jade)
    + arrow(1121, 372, C.jade, 8)
    + arrow(1121, 600, C.jade, 8)
    + database(1124, 319)
    + text('DuckDB', 1208, 464, 32, C.graphite, { align: 'middle', latin: true, weight: 600 })
    + text('案件 · 流水 · 图谱', 1208, 501, 24, C.secondary, { align: 'middle' })
    + folder(1124, 551)
    + text('原件文件', 1208, 694, 30, C.graphite, { align: 'middle', weight: 600 })
    + text('独立文件存储', 1208, 731, 24, C.secondary, { align: 'middle' })
    + text('界面共用本地服务，数据库与原件分开存储。', 64, 851, 24, C.secondary);

  return {
    width, height, body,
    title: 'FundsTrace 本机桌面架构',
    description: 'Electron Main 管理进程与请求认证。G6 工作台和 React 案件、导入、流水核对试点，共用本机 Python sidecar 的 FastAPI、导入和分析服务；DuckDB 与原件文件分开存储。',
  };
}

function importIllustration() {
  return '<g>'
    + '<path d="M138 292H248L277 321V478H138Z" fill="' + C.white + '" stroke="' + C.graphite + '" stroke-width="2.5" stroke-linejoin="round"/>'
    + route('M248 292V321H277', C.graphite, 2.5)
    + text('CSV', 161, 350, 27, C.jade, { latin: true, weight: 600 })
    + route('M161 378H244M161 398H244M161 418H210', C.line, 4)
    + rect(233, 365, 184, 137, C.white, C.jade, 6, 2.5)
    + '<path d="M239 365H411Q417 365 417 371V395H233V371Q233 365 239 365Z" fill="' + C.jade + '"/>'
    + rect(234, 396, 49, 105, C.tint)
    + line(283, 395, 283, 501, C.light, 1.5)
    + line(350, 395, 350, 501, C.line, 1.5)
    + line(233, 430, 417, 430, C.line, 1.5)
    + line(233, 465, 417, 465, C.line, 1.5)
    + route('M246 412H269M296 412H333M364 412H402M246 447H269M296 447H333M364 447H402M246 482H269M296 482H333M364 482H402', C.muted, 3)
    + '</g>';
}

function traceIllustration() {
  const points = [[605, 435], [685, 324], [791, 369], [845, 466], [690, 496], [591, 325]];
  let body = route('M591 325 685 324 690 496 605 435 591 325M690 496 791 369M690 496 845 466', C.light, 2.5)
    + route('M605 435 685 324 791 369 845 466', C.jade, 5);
  points.forEach(([x, y], index) => {
    body += circle(x, y, index === 0 ? 18 : 14, index < 4 ? C.jade : C.white, index < 4 ? C.jade : C.muted, 2.5);
    if (index === 0) body += circle(x, y, 26, 'none', C.jade, 2);
    if (index === 3) body += circle(x, y, 5, C.paper);
  });
  return body;
}

function exportIllustration() {
  return rect(1154, 314, 150, 190, C.tint, C.light, 5, 2)
    + rect(1128, 298, 150, 190, C.white, C.line, 5, 2)
    + '<path d="M1067 278H1206L1246 318V478H1067Z" fill="' + C.white + '" stroke="' + C.graphite + '" stroke-width="2.5" stroke-linejoin="round"/>'
    + route('M1206 278V318H1246', C.graphite, 2.5)
    + route('M1092 367 1126 337 1161 365 1194 344', C.jade, 3)
    + circle(1092, 367, 6, C.jade)
    + circle(1126, 337, 6, C.jade)
    + circle(1161, 365, 6, C.jade)
    + circle(1194, 344, 6, C.graphite)
    + route('M1092 402H1219M1092 423H1219M1092 444H1170', C.line, 4)
    + '<path d="M1298 429V475M1277 454L1298 475 1319 454M1274 493H1322" fill="none" stroke="' + C.jade + '" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>';
}

function workflow() {
  const width = 1440;
  const height = 780;
  let body = rect(0, 0, width, height, C.paper)
    + brandHeader('从流水，到可复查的结果', 'WORKFLOW');
  const steps = [
    { x: 96, number: '01', title: '导入数据', verb: '预览并核对', output: '带来源的案件流水' },
    { x: 536, number: '02', title: '分析追踪', verb: '筛选并追踪', output: '关系图谱与追踪流水' },
    { x: 976, number: '03', title: '整理导出', verb: '标注并导出', output: '图谱 / Excel / 案件包' },
  ];
  for (const step of steps) {
    body += text(step.number, step.x, 228, 27, C.jade, { latin: true, weight: 600 })
      + text(step.title, step.x + 61, 229, 33, C.graphite, { weight: 600 })
      + line(step.x, 253, step.x + 368, 253, C.line, 1.5)
      + text(step.verb, step.x, 582, 28, C.graphite, { weight: 500 })
      + text('产出', step.x, 628, 24, C.jade, { weight: 500 })
      + text(step.output, step.x + 68, 628, 24, C.secondary);
  }
  body += importIllustration()
    + traceIllustration()
    + exportIllustration()
    + route('M462 402H526', C.jade, 2.5)
    + arrow(524, 402, C.jade, 9)
    + route('M902 402H966', C.jade, 2.5)
    + arrow(964, 402, C.jade, 9)
    + line(64, 690, 1376, 690, C.line, 1.5)
    + text('在同一案件中，从原始流水走向可复用成果。', 64, 737, 24, C.secondary);
  return {
    width, height, body,
    title: 'FundsTrace 导入、追踪与导出工作流',
    description: '导入数据：预览并核对，得到带来源的案件流水。分析追踪：筛选并追踪，形成关系图谱与追踪流水。整理导出：标注并导出图谱、Excel 或案件包。',
  };
}

await fs.mkdir(media, { recursive: true });
for (const [name, figure] of [['fundstrace-architecture', architecture()], ['fundstrace-workflow', workflow()]]) {
  const source = `<svg xmlns="http://www.w3.org/2000/svg" width="${figure.width}" height="${figure.height}" viewBox="0 0 ${figure.width} ${figure.height}">${figure.body}</svg>`;
  let outlined = convertSVGTextToPath(source).toString('utf8');
  // Skia serializes geometry only. Restore intrinsic sizing and accessibility
  // without retaining font dependencies in the delivered SVG.
  outlined = outlined.replace(/<svg\b([^>]*)>/, `<svg$1 viewBox="0 0 ${figure.width} ${figure.height}" role="img" aria-labelledby="title desc"><title id="title">${escape(figure.title)}</title><desc id="desc">${escape(figure.description)}</desc>`);
  if (/<(?:text|script|image|foreignObject)\b/i.test(outlined) || /(?:href|url\()\s*=?\s*["']?https?:/i.test(outlined)) {
    throw new Error(`Unexpected dependency in ${name}.svg`);
  }
  outlined = outlined.replace('</svg>', `<image x="58" y="24" width="54" height="54" href="data:image/png;base64,${brandIcon}"/></svg>`);
  const svgPath = path.join(media, `${name}.svg`);
  const pngPath = path.join(media, `${name}.png`);
  await fs.writeFile(svgPath, outlined);
  await sharp(Buffer.from(outlined), { density: 144 }).png({ compressionLevel: 9 }).toFile(pngPath);
  const [svgStat, pngStat] = await Promise.all([fs.stat(svgPath), fs.stat(pngPath)]);
  console.log(`${name}: SVG ${figure.width}×${figure.height}, ${svgStat.size} bytes; PNG ${figure.width * 2}×${figure.height * 2}, ${pngStat.size} bytes`);
}
