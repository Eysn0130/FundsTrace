#!/usr/bin/env node
// Export the approved raster master; never redraw the application mark.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const brand = path.join(root, 'docs/brand');
const require = createRequire(import.meta.url);
const sharp = require(process.env.BRAND_SHARP_PATH || 'sharp');
const { GlobalFonts, convertSVGTextToPath } = require(process.env.BRAND_CANVAS_PATH || '@napi-rs/canvas');
const options = {
  '--latin-font': '/System/Library/Fonts/Avenir Next.ttc',
  '--cjk-font': '/System/Library/Fonts/Hiragino Sans GB.ttc',
};
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index];
  const value = process.argv[index + 1];
  if (!(key in options) || !value || value.startsWith('--')) {
    throw new Error('Usage: render.mjs [--latin-font FILE] [--cjk-font FILE]');
  }
  options[key] = value;
}
for (const [key, alias] of [['--latin-font', 'Brand Latin'], ['--cjk-font', 'Brand CJK']]) {
  if (!GlobalFonts.registerFromPath(options[key], alias)) throw new Error('Could not register ' + key);
}
for (const dir of ['docs/brand/icons', 'desktop/assets', 'static', 'docs/media']) {
  await fs.mkdir(path.join(root, dir), { recursive: true });
}
const master = path.join(brand, 'source/app-icon-master.png');
const meta = await sharp(master).metadata();
if (!meta.hasAlpha || meta.width !== meta.height) throw new Error('Master must be square RGBA.');
const sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512, 1024];
for (const size of sizes) {
  await sharp(master).resize(size, size, { kernel: 'lanczos3' }).png({ compressionLevel: 9 })
    .toFile(path.join(brand, 'icons/icon-' + size + '.png'));
}
// PNG payloads retain alpha. Include common Windows display-scaling sizes.
const icoSizes = sizes.filter(size => size <= 256);
const payloads = await Promise.all(icoSizes.map(size => fs.readFile(path.join(brand, 'icons/icon-' + size + '.png'))));
const header = Buffer.alloc(6);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(icoSizes.length, 4);
let offset = 6 + 16 * icoSizes.length;
const entries = icoSizes.map((size, index) => {
  const entry = Buffer.alloc(16);
  entry[0] = size === 256 ? 0 : size;
  entry[1] = size === 256 ? 0 : size;
  entry.writeUInt16LE(1, 4);
  entry.writeUInt16LE(32, 6);
  entry.writeUInt32LE(payloads[index].length, 8);
  entry.writeUInt32LE(offset, 12);
  offset += payloads[index].length;
  return entry;
});
const ico = Buffer.concat([header, ...entries, ...payloads]);
await fs.writeFile(path.join(brand, 'icons/icon.ico'), ico);
for (const target of ['desktop/assets/icon.ico', 'static/favicon.ico']) {
  await fs.writeFile(path.join(root, target), ico);
}
for (const [size, target] of [[1024, 'desktop/assets/icon.png'], [256, 'static/logo.png'], [32, 'static/favicon-32.png']]) {
  await fs.copyFile(path.join(brand, 'icons/icon-' + size + '.png'), path.join(root, target));
}
const label = (value, x, y, size, color = '#102E50', cjk = false) =>
  '<text x="' + x + '" y="' + y + '" font-family="' + (cjk ? 'Brand CJK' : 'Brand Latin') + '" font-size="' + size + '" fill="' + color + '">' + value + '</text>';
async function presentation(file, width, height, body, placements) {
  // Outline text before raster export; composite the unmodified icon PNG.
  const source = '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height + '">' + body + '</svg>';
  const outlined = convertSVGTextToPath(source);
  const layers = await Promise.all(placements.map(async ([size, left, top]) => ({
    input: await sharp(master).resize(size, size).png().toBuffer(), left, top,
  })));
  await sharp(outlined).composite(layers).png({ compressionLevel: 9 }).toFile(path.join(root, file));
}
await presentation('docs/brand/brand-board.png', 1600, 1040,
  '<rect width="1600" height="1040" fill="#F3F5F8"/>'
  + label('FundsTrace', 80, 103, 54)
  + label('统一应用图标', 82, 150, 24, '#536479', true)
  + '<rect x="696" y="220" width="824" height="400" rx="32" fill="#102E50"/>'
  + label('Windows · Web · GitHub', 80, 974, 26)
  + label('1024 / 512 / 256 / 128 / 96 / 64 / 48 / 40 / 32 / 24 / 20 / 16', 698, 734, 22, '#536479')
  + label('同一母版 · 透明外边缘 · 多尺寸 ICO', 698, 781, 24, '#536479', true),
  [[480, 72, 240], [320, 754, 254], [128, 1132, 342], [64, 1332, 374], [32, 1444, 390]]);
await presentation('docs/media/fundstrace-social.png', 1280, 640,
  '<rect width="1280" height="640" fill="#F3F5F8"/>'
  + label('FundsTrace', 534, 265, 78)
  + label('让资金往来，清晰可见。', 538, 339, 34, '#102E50', true)
  + label('资金流水追踪与关联分析', 538, 403, 25, '#536479', true)
  + label('本机运行  /  追踪研判  /  整理导出', 538, 465, 23, '#536479', true),
  [[416, 64, 112]]);
console.log('Exported approved icon: 12 PNG sizes, 10-frame ICO, runtime copies, brand board and social preview.');
