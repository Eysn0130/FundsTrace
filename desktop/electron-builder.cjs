'use strict';
const path = require('node:path');
const fs = require('node:fs');
const backend = path.resolve(__dirname, '../build/desktop/backend/funds-trace-backend');
module.exports = {
  appId: 'com.eysn.fundstrace', productName: 'FundsTrace',
  directories: { output: '../build/desktop/electron' },
  files: ['package.json', 'main.cjs', 'backend.cjs', 'host-policy.cjs', 'preferences.cjs', 'preload.cjs'],
  asar: true,
  electronLanguages: ['en-US', 'zh-CN', 'zh-TW'],
  extraResources: [
    { from: backend, to: 'backend', filter: ['**/*'] },
    { from: path.join(__dirname, 'licenses/Apache-2.0.txt'), to: 'licenses/Analytix-Apache-2.0.txt' }
  ],
  beforePack: async context => {
    const manifest = JSON.parse(fs.readFileSync(path.join(backend, 'build-manifest.json'), 'utf8'));
    const architectures = ['ia32', 'x64', 'armv7l', 'arm64', 'universal'];
    if (manifest.protocol !== 1 || manifest.platform !== context.electronPlatformName || manifest.arch !== architectures[context.arch]) throw new Error('Build the backend natively for the exact Electron platform and architecture first');
  },
  win: { icon: path.join(__dirname, 'assets/icon.ico'), target: [{ target: 'nsis', arch: ['x64'] }], signExecutable: false },
  nsis: { oneClick: false, perMachine: false, allowElevation: false, allowToChangeInstallationDirectory: true, deleteAppDataOnUninstall: false, include: path.join(__dirname, 'installer.nsh'), artifactName: 'FundsTrace-${version}-windows-${arch}-unsigned.${ext}' },
  publish: null
};
