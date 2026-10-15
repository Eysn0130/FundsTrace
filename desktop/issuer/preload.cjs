'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('issuer', Object.freeze(Object.fromEntries(
  ['getState', 'createKeys', 'loadKey', 'exportPublic', 'issue', 'clear', 'copyCode'].map(name => [name, input => ipcRenderer.invoke(`issuer:${name}`, input)])
)));
