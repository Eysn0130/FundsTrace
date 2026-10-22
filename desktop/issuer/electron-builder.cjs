'use strict';
const path = require('node:path');
const windowsIcon = path.join(__dirname, '../assets/icon.ico');
module.exports = {
  appId: 'com.eysn.fundstrace.issuer', productName: 'FundsTrace Issuer',
  directories: { output: '../build/desktop/issuer' },
  files: ['package.json', 'issuer/main.cjs', 'issuer/core.cjs', 'issuer/policy.cjs', 'issuer/preload.cjs', 'issuer/renderer.js', 'issuer/index.html', 'issuer/style.css', 'assets/icon.png', 'assets/icon.ico',
    { from: path.resolve(__dirname, '../..'), to: '.', filter: ['LICENSE'] }],
  extraMetadata: { main: 'issuer/main.cjs', name: 'funds-trace-issuer', productName: 'FundsTrace Issuer', description: 'Offline FundsTrace maintainer license issuer' },
  asar: true,
  extraResources: [
    { from: '../LICENSE', to: 'LICENSE' },
    { from: '../THIRD_PARTY_NOTICES.md', to: 'THIRD_PARTY_NOTICES.md' },
    { from: '../LICENSES/MIT.txt', to: 'LICENSES/MIT.txt' },
    { from: '../docs/licensing.md', to: 'docs/licensing.md' },
    { from: '../docs/licensing.en.md', to: 'docs/licensing.en.md' },
  ],
  win: { icon: windowsIcon, target: [{ target: 'nsis', arch: ['x64'] }], signExecutable: false },
  nsis: { oneClick: false, perMachine: false, allowToChangeInstallationDirectory: true, deleteAppDataOnUninstall: false, installerIcon: windowsIcon, uninstallerIcon: windowsIcon, artifactName: 'FundsTrace-Issuer-${version}-windows-${arch}-unsigned.${ext}' },
  publish: null,
};
