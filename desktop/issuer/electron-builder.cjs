'use strict';
module.exports = {
  appId: 'com.eysn.fundstrace.issuer', productName: 'FundsTrace Issuer',
  directories: { output: '../build/desktop/issuer' },
  files: ['package.json', 'issuer/main.cjs', 'issuer/core.cjs', 'issuer/policy.cjs', 'issuer/preload.cjs', 'issuer/renderer.js', 'issuer/index.html', 'issuer/style.css'],
  extraMetadata: { main: 'issuer/main.cjs', name: 'funds-trace-issuer', productName: 'FundsTrace Issuer', description: 'Offline FundsTrace maintainer license issuer' },
  asar: true,
  extraResources: [
    { from: '../LICENSE', to: 'FundsTrace-LICENSE' },
    { from: '../THIRD_PARTY_NOTICES.md', to: 'THIRD_PARTY_NOTICES.md' },
  ],
  win: { target: [{ target: 'nsis', arch: ['x64'] }], signAndEditExecutable: false },
  nsis: { oneClick: false, perMachine: false, allowToChangeInstallationDirectory: true, deleteAppDataOnUninstall: false, artifactName: 'FundsTrace-Issuer-${version}-windows-${arch}-unsigned.${ext}' },
  publish: null,
};
