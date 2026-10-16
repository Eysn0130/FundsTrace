#!/usr/bin/env node
// Render the native SVG source. Requires sharp; see README.md for the exact command.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const brand = path.join(root, 'docs/brand');
const require = createRequire(import.meta.url);
const sharpPath = process.env.BRAND_SHARP_PATH || 'sharp';
const sharp = require(sharpPath);
await fs.mkdir(path.join(brand, 'icons'), { recursive: true });
await fs.mkdir(path.join(root, 'desktop/assets'), { recursive: true });

for (const name of ['fundstrace-wordmark-dark', 'fundstrace-wordmark-light', 'brand-board']) {
  await sharp(path.join(brand, `${name}.svg`), { density: 144 }).png().toFile(path.join(brand, `${name}.png`));
}
for (const size of [16, 32, 48, 64, 128, 256, 512, 1024]) {
  await sharp(path.join(brand, 'fundstrace-icon.svg'), { density: 1536 })
    .resize(size, size).png().toFile(path.join(brand, `icons/icon-${size}.png`));
}

// ICO directory entries carry PNG payloads. Windows ICO dimensions encode 256 as 0.
const sizes = [16, 32, 48, 64, 128, 256];
const payloads = await Promise.all(sizes.map(size => fs.readFile(path.join(brand, `icons/icon-${size}.png`))));
const header = Buffer.alloc(6);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(sizes.length, 4);
let offset = 6 + 16 * sizes.length;
const entries = sizes.map((size, index) => {
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
await fs.writeFile(path.join(root, 'desktop/assets/icon.ico'), ico);
await fs.copyFile(path.join(brand, 'icons/icon-1024.png'), path.join(root, 'desktop/assets/icon.png'));
console.log('Rendered outlined wordmarks, brand board, PNG icons and a six-size Windows ICO.');
