'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electron', {
  send: (channel, data) => {
    const allowed = ['app:quit', 'resize-popover', 'open-external'];
    if (allowed.includes(channel)) ipcRenderer.send(channel, data);
  },
  on: (channel, callback) => {
    const allowed = ['app:message'];
    if (allowed.includes(channel)) {
      ipcRenderer.on(channel, (_event, ...args) => callback(...args));
    }
  },
  resizePopover: (height) => ipcRenderer.send('resize-popover', height),
  openExternal: (url) => ipcRenderer.send('open-external', url),
});
