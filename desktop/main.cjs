'use strict'
const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const readline = require('node:readline')
let child, window, backendOrigin
let quitting = false
app.setName('Quantaframe Warper')
ipcMain.handle('choose', async (event, kind) => {
  if (!window || event.sender !== window.webContents || new URL(event.senderFrame.url).origin !== backendOrigin) throw Error('Untrusted sender')
  if (kind === 'save' || kind === 'export') {
    const result = await dialog.showSaveDialog(window, {
      defaultPath: kind === 'save' ? 'project.yaml' : 'warped.mp4',
      filters: [{ name: kind, extensions: [kind === 'save' ? 'yaml' : 'mp4'] }]
    })
    return result.canceled ? [] : [result.filePath]
  }
  const extensions = {video:['mp4','mov','mkv','avi','webm'],assets:['png'],open:['yaml','yml']}[kind]
  if (!extensions) throw Error('Unknown dialog')
  const result = await dialog.showOpenDialog(window, {
    properties: kind === 'assets' ? ['openFile','multiSelections'] : ['openFile'],
    filters: [{name:kind,extensions}]
  })
  return result.canceled ? [] : result.filePaths
})
app.whenReady().then(() => {
  const root = path.resolve(__dirname, '..')
  const python = process.env.QUANTAFRAME_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  child = spawn(python, ['-m', 'quantaframe_warper.ui.server'], {cwd:root,stdio:['ignore','pipe','pipe']})
  let errors = ''
  child.stderr.on('data', data => { errors = (errors + data).slice(-8000) })
  const timer = setTimeout(() => { dialog.showErrorBox('Backend timeout', errors || 'Python backend did not start'); app.quit() }, 30000)
  child.on('error', error => { clearTimeout(timer); dialog.showErrorBox('Backend failed', error.message); app.quit() })
  child.on('exit', () => { clearTimeout(timer); if (!quitting) { dialog.showErrorBox('Backend stopped', errors); app.quit() } })
  readline.createInterface({input:child.stdout}).once('line', async line => {
    try {
      const {url} = JSON.parse(line)
      backendOrigin = new URL(url).origin
      clearTimeout(timer)
      window = new BrowserWindow({width:1400,height:900,minWidth:800,minHeight:550,webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}})
      window.webContents.on('will-prevent-unload', event => {
        const choice = dialog.showMessageBoxSync(window, {type:'question',buttons:['Keep editing','Discard changes'],defaultId:0,cancelId:0,message:'Close without saving project changes?'})
        if (choice === 1) event.preventDefault()
      })
      window.webContents.setWindowOpenHandler(() => ({action:'deny'}))
      window.webContents.on('will-navigate', (event, target) => { if (new URL(target).origin !== backendOrigin) event.preventDefault() })
      await window.loadURL(url)
      if (process.env.QUANTAFRAME_SMOKE === '1') {
        console.log(await window.webContents.executeJavaScript("JSON.stringify({title:document.title,canvas:!!document.querySelector('canvas'),bridge:!!window.desktop})"))
        app.quit()
      }
    } catch (error) { dialog.showErrorBox('Startup failed', error.message); app.quit() }
  })
})
app.on('window-all-closed', () => app.quit())
app.on('will-quit', () => { quitting = true; child?.kill('SIGINT') })
