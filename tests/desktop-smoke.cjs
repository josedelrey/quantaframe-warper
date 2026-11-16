'use strict'
// Run with Electron after scripts/make_demo.py. All outputs stay in outputs/.
const {app,dialog} = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const root = path.resolve(__dirname,'..')
const demo = path.join(root,'outputs/demo')
let opens = 0
let saves = 0
let finished = false
dialog.showOpenDialog = async () => ({canceled:false,filePaths:[path.join(demo, ['motion.mp4','eye.png','ui-project.yaml'][opens++])]})
dialog.showSaveDialog = async () => ({canceled:false,filePath:path.join(demo,saves++ === 0 ? 'ui-project.yaml' : 'ui-export.mp4')})
dialog.showErrorBox = (title,message) => { console.error(title,message); app.exit(1) }
app.on('browser-window-created', (_, window) => {
  window.webContents.on('console-message', (_event, ...args) => console.log(...args))
  window.webContents.once('did-finish-load', async () => {
    try {
      const result = await window.webContents.executeJavaScript(`(async () => {
        const wait = async (predicate) => {
          const end = Date.now() + 30000
          while (!predicate()) { if (Date.now() > end) throw Error('Timeout: ' + document.querySelector('#status').textContent); await new Promise(r => setTimeout(r,30)) }
        }
        const click = id => document.getElementById(id).click()
        click('video'); await wait(() => !busy && !!info && !rendering)
        if (info.frames !== 72) throw Error('Wrong frame count')
        click('assets'); await wait(() => !busy && Object.keys(project.assets).length === 1)
        const rect = canvas.getBoundingClientRect()
        const transfer = new DataTransfer(); transfer.setData('text/plain','eye.png')
        canvas.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:transfer,clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2}))
        await wait(() => !rendering && project.placements.length === 1)
        const originalCapture = canvas.setPointerCapture.bind(canvas); canvas.setPointerCapture = () => {}
        canvas.dispatchEvent(new PointerEvent('pointerdown',{pointerId:1,clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2}))
        canvas.dispatchEvent(new PointerEvent('pointermove',{pointerId:1,clientX:rect.left+rect.width/2+10,clientY:rect.top+rect.height/2+5}))
        canvas.dispatchEvent(new PointerEvent('pointerup',{pointerId:1}))
        canvas.setPointerCapture = originalCapture
        if (project.placements[0].x <= info.width/2) throw Error('Pointer drag did not move placement')
        for (const [key,value] of [['scale',.7],['rotation',15],['strength',1.2],['duration',60]]) {
          document.getElementById(key).value = value
          document.getElementById(key).dispatchEvent(new Event('change'))
        }
        await wait(() => !rendering)
        const before = canvas.toDataURL()
        document.getElementById('frame').value = 20
        document.getElementById('frame').dispatchEvent(new Event('change'))
        await wait(() => !rendering)
        if (before === canvas.toDataURL()) throw Error('Scrubbing did not change preview')
        click('save'); await wait(() => !busy && !dirty)
        click('open'); await wait(() => !busy && !!info && !rendering)
        if (project.placements[0].rotation !== 15) throw Error('Project did not restore transform')
        click('export'); await wait(() => !busy && document.querySelector('#status').textContent.startsWith('Exported'))
        document.querySelector('#layers button').click(); await wait(() => !rendering)
        document.getElementById('frame').value = 20
        document.getElementById('frame').dispatchEvent(new Event('change')); await wait(() => !rendering)
        return {frames:info.frames,placements:project.placements.length,bridge:!!window.desktop,status:document.querySelector('#status').textContent}
      })()`)
      fs.writeFileSync(path.join(demo,'ui-smoke.png'),(await window.webContents.capturePage()).toPNG())
      if (!fs.statSync(path.join(demo,'ui-export.mp4')).size) throw Error('Empty export')
      console.log(JSON.stringify(result))
      finished = true
      window.destroy()
      app.quit()
    } catch (error) { console.error(error); window.destroy(); app.exit(1) }
  })
})
setTimeout(() => { if (!finished) { console.error('Desktop smoke timed out'); app.exit(1) } },60000).unref()
require('../desktop/main.cjs')
