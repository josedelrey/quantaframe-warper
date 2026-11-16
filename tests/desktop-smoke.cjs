'use strict'
// Run with Electron after scripts/make_demo.py. All outputs stay in outputs/.
const {app,dialog} = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const root = path.resolve(__dirname,'..')
const demo = path.join(root,'outputs/demo')
let opens = -1
let finished = false
dialog.showOpenDialog = async () => ({canceled:false,filePaths:[path.join(demo, opens < 0 ? (opens++, 'missing.mov') : ['motion.mp4','eye.png'][opens++])]})
dialog.showSaveDialog = async () => ({canceled:false,filePath:path.join(demo,'ui-export.mp4')})
dialog.showErrorBox = (title,message) => { console.error(title,message); app.exit(1) }
app.on('browser-window-created', (_, window) => {
  window.webContents.once('did-finish-load', async () => {
    try {
      const result = await window.webContents.executeJavaScript(`(async () => {
        const wait = async (predicate) => {
          const end = Date.now() + 30000
          while (!predicate()) { if (Date.now() > end) throw Error('Timeout: ' + document.querySelector('#status').textContent); await new Promise(r => setTimeout(r,30)) }
        }
        console.info('renderer logging smoke: info')
        console.warn('renderer logging smoke: warning')
        console.error('renderer logging smoke: error')
        console.debug('renderer logging smoke: debug')
        console.info('session redaction smoke: ' + location.href)
        const click = id => document.getElementById(id).click()
        click('video'); await wait(() => !busy && document.querySelector('#empty').textContent.includes('Could not load'))
        if (document.querySelector('#empty').hidden || info) throw Error('Import failure was hidden')
        click('video'); await wait(() => !busy && !!info && !rendering)
        if (info.frames !== 72) throw Error('Wrong frame count')
        const realFetch = window.fetch
        const played = []
        window.fetch = async (url, options) => {
          if (url === '/api/playback') {
            played.push(JSON.parse(options.body).frame)
            if (played.length === 1) await new Promise(resolve => setTimeout(resolve, 240))
          }
          return realFetch(url, options)
        }
        click('play'); await wait(() => !playback && !rendering)
        window.fetch = realFetch
        if (current !== info.frames - 1 || played.length < 2 || played[1] <= played[0] + 1) {
          throw Error('Playback did not catch up after a slow frame')
        }
        seek(0); await wait(() => !rendering)
        click('assets'); await wait(() => !busy && Object.keys(project.assets).length === 1)
        const rect = canvas.getBoundingClientRect()
        const transfer = new DataTransfer(); transfer.setData('text/plain','eye.png')
        canvas.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:transfer,clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2}))
        await wait(() => !rendering && project.placements.length === 1)
        const originalCapture = transform.setPointerCapture.bind(transform); transform.setPointerCapture = () => {}
        transform.dispatchEvent(new PointerEvent('pointerdown',{pointerId:1,clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2}))
        transform.dispatchEvent(new PointerEvent('pointermove',{pointerId:1,clientX:rect.left+rect.width/2+10,clientY:rect.top+rect.height/2+5}))
        transform.dispatchEvent(new PointerEvent('pointerup',{pointerId:1}))
        if (project.placements[0].x <= info.width/2) throw Error('Pointer drag did not move placement')
        const box = transform.getBoundingClientRect()
        const corner = transform.querySelector('.se')
        corner.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:2,clientX:box.right,clientY:box.bottom}))
        transform.dispatchEvent(new PointerEvent('pointermove',{pointerId:2,clientX:box.right+20,clientY:box.bottom+20}))
        transform.dispatchEvent(new PointerEvent('pointerup',{pointerId:2}))
        if (project.placements[0].scale <= 1) throw Error('Corner did not scale placement')
        const rotate = transform.querySelector('.rotate'), box2 = transform.getBoundingClientRect()
        const cx = box2.left + box2.width/2, cy = box2.top + box2.height/2
        rotate.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:3,clientX:cx,clientY:box2.top-24}))
        transform.dispatchEvent(new PointerEvent('pointermove',{pointerId:3,clientX:box2.right+24,clientY:cy}))
        transform.dispatchEvent(new PointerEvent('pointerup',{pointerId:3}))
        if (Math.abs(project.placements[0].rotation) < 10) throw Error('Handle did not rotate placement')
        transform.setPointerCapture = originalCapture
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
