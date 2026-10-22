'use strict';

const CHANNEL = 'funds-trace:appearance';
const ARGUMENT = '--funds-appearance=';
const DEFAULT = Object.freeze({ version: 1, theme: 'dark', accent: 'blue', translucency: false });
const { readAppearance } = require('./preferences.cjs');

function resolveAppearance(value, systemDark = true) {
  const preference = readAppearance(JSON.stringify(value)) || DEFAULT;
  return { ...preference, theme: preference.theme === 'system' ? systemDark ? 'dark' : 'light' : preference.theme };
}

function colors(value) {
  return value.theme === 'light'
    ? { background: '#fcfcfb', caption: '#f4f3ef', foreground: '#292825' }
    : { background: '#10171f', caption: '#151d27', foreground: '#cdd7e3' };
}

module.exports = { CHANNEL, ARGUMENT, DEFAULT, readAppearance, resolveAppearance, colors };
