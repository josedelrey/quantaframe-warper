'use strict'
const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const readline = require('node:readline')
const {writeLog, forwardRenderer} = require('./logging.cjs')
const development = !app.isPackaged
const log = (level, message) => writeLog('electron:main', level, message)
process.on('uncaughtExceptionMonitor', error => log('error', error.stack || error.message))
let child, window, backendOrigin
let quitting = false
app.setName('Quantaframe Warper')
ipcMain.handle('choose', async (event, kind) => {
  if (!window || event.sender !== window.webContents || new URL(event.senderFrame.url).origin !== backendOrigin) throw Error('Untrusted sender')
  if (kind === 'export') {
    const result = await dialog.showSaveDialog(window, {
      defaultPath: 'warped.mp4',
      filters: [{ name: kind, extensions: ['mp4'] }]
    })
    return result.canceled ? [] : [result.filePath]
  }
  const extensions = {video:['mp4','mov','mkv','avi','webm'],assets:['png']}[kind]
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
  log('info', 'Starting desktop workbench')
  log('info', `Starting Python backend with ${python}`)
  child = spawn(python, ['-u', '-m', 'quantaframe_warper.ui.server'], {
    cwd:root,stdio:['ignore','pipe','pipe'],
    env:{...process.env, QUANTAFRAME_DEV:development ? '1' : '0'}
  })
  let errors = ''
  child.stderr.on('data', data => { errors = (errors + data).slice(-8000) })
  readline.createInterface({input:child.stderr}).on('line', line => writeLog('python', 'stderr', line))
  const timer = setTimeout(() => { log('error', 'Python backend startup timed out'); dialog.showErrorBox('Backend timeout', errors || 'Python backend did not start'); app.quit() }, 30000)
  child.on('error', error => { clearTimeout(timer); log('error', error.stack || error.message); dialog.showErrorBox('Backend failed', error.message); app.quit() })
  child.on('exit', (code, signal) => { log(quitting ? 'info' : 'error', `Python backend exited: code=${code}, signal=${signal}`); clearTimeout(timer); if (!quitting) { dialog.showErrorBox('Backend stopped', errors); app.quit() } })
  let backendReady = false
  readline.createInterface({input:child.stdout}).on('line', async line => {
    if (backendReady) { writeLog('python', 'stdout', line); return }
    try {
      const {url} = JSON.parse(line)
      backendOrigin = new URL(url).origin
      backendReady = true
      log('info', `Python backend ready at ${backendOrigin}`)
      clearTimeout(timer)
      window = new BrowserWindow({width:1400,height:900,minWidth:800,minHeight:550,webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}})
      if (development) forwardRenderer(window.webContents)
      window.webContents.on('preload-error', (_event, preload, error) => log('error', `Preload ${preload}: ${error.stack || error.message}`))
      window.webContents.on('did-fail-load', (_event, code, description, url) => log('error', `Page load failed (${code}): ${description} ${url}`))
      window.webContents.on('render-process-gone', (_event, details) => log('error', `Renderer stopped: ${details.reason}, code=${details.exitCode}`))
      window.webContents.on('will-prevent-unload', event => {
        const choice = dialog.showMessageBoxSync(window, {type:'question',buttons:['Keep editing','Discard changes'],defaultId:0,cancelId:0,message:'Close and discard your edits?'})
        if (choice === 1) event.preventDefault()
      })
      window.webContents.setWindowOpenHandler(() => ({action:'deny'}))
      window.webContents.on('will-navigate', (event, target) => { if (new URL(target).origin !== backendOrigin) event.preventDefault() })
      await window.loadURL(url)
      log('info', 'Editor window loaded')
      if (process.env.QUANTAFRAME_SMOKE === '1') {
        log('info', await window.webContents.executeJavaScript("JSON.stringify({title:document.title,canvas:!!document.querySelector('canvas'),bridge:!!window.desktop})"))
        app.quit()
      }
    } catch (error) { log('error', error.stack || error.message); dialog.showErrorBox('Startup failed', error.message); app.quit() }
  })
})
app.on('window-all-closed', () => app.quit())
app.on('will-quit', () => { log('info', 'Shutting down desktop workbench'); quitting = true; child?.kill('SIGINT') })
