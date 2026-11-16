'use strict'
const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('desktop', {
  choose: kind => ipcRenderer.invoke('choose', kind)
})
