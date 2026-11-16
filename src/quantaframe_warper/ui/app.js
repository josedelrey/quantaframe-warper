'use strict'
const $ = id => document.getElementById(id)
const token = new URLSearchParams(location.search).get('token')
let project = {version:1,video:'',assets:{},placements:[]}
let info, selected = -1, current = 0, playing = false, busy = false
let revision = 0, pending = false, rendering = false, drag = null, dirty = false
const controls = ['start','duration','x','y','scale','rotation','strength']
const canvas = $('preview'), context = canvas.getContext('2d')
function status(text) { $('status').textContent = text }
async function api(method, data) {
  const response = await fetch(`/api/${method}`, {method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:JSON.stringify(data)})
  const result = await response.json()
  if (!response.ok) throw Error(result.error || 'Request failed')
  return result
}
async function choose(kind) {
  if (window.desktop) return window.desktop.choose(kind)
  const path = prompt(`Enter an absolute local ${kind === 'assets' ? 'PNG path (one at a time)' : kind + ' path'}`)
  return path ? [path] : []
}
async function action(work) {
  if (busy) return
  busy = true
  stop()
  document.querySelectorAll('header button').forEach(b => { b.disabled = true })
  try { await work() } catch (error) { status(error.message) }
  finally { busy = false; document.querySelectorAll('header button').forEach(b => { b.disabled = false }) }
}
function stop() { playing = false; $('play').textContent = 'Play' }
function sync() {
  $('timeline').value = current
  $('frame').value = current
  $('time').textContent = `/ ${info ? info.frames - 1 : 0}`
  const p = project.placements[selected]
  for (const key of controls) { $(key).disabled = !p || busy; $(key).value = p ? p[key] : '' }
  $('layers').replaceChildren()
  project.placements.forEach((p,index) => {
    const button = document.createElement('button')
    button.textContent = `${index + 1}. ${p.asset} @ ${p.start}`
    button.className = selected === index ? 'selected' : ''
    button.onclick = () => { if (busy) return; stop(); selected = index; current = p.start; sync(); requestRender() }
    $('layers').append(button)
  })
}
async function library() {
  $('library').replaceChildren()
  for (const [name,path] of Object.entries(project.assets)) {
    const result = await api('asset',{path})
    const image = document.createElement('img')
    image.src = result.url; image.title = name; image.alt = name; image.draggable = true
    image.ondragstart = event => { event.dataTransfer.setData('text/plain',name) }
    $('library').append(image)
  }
}
async function prepare() {
  status('Preparing codec vectors. This decodes the entire clip and may take a while…')
  info = await api('prepare',{project})
  canvas.width = info.width; canvas.height = info.height
  $('empty').hidden = true
  $('timeline').max = $('frame').max = info.frames - 1
  current = 0; selected = -1
  await library(); sync(); requestRender()
  status(`${info.width} × ${info.height} · ${info.frames} frames · ${info.fps.toFixed(3)} fps · ${info.vectors} codec vectors`)
}
function requestRender() {
  if (!info) return
  revision++; pending = true
  if (!rendering) void renderLoop()
}
async function renderLoop() {
  rendering = true
  while (pending) {
    pending = false
    const ticket = revision
    try {
      const result = await api('render',{project,frame:current})
      const image = new Image()
      image.src = result.url
      await image.decode()
      if (ticket === revision) context.drawImage(image,0,0)
    } catch (error) { status(error.message); stop() }
  }
  rendering = false
}
function seek(value) {
  if (!info || busy) return
  stop(); current = Math.max(0, Math.min(info.frames - 1, Math.round(Number(value) || 0))); sync(); requestRender()
}
$('timeline').oninput = event => seek(event.target.value)
$('frame').onchange = event => seek(event.target.value)
$('back').onclick = () => seek(current - 1)
$('next').onclick = () => seek(current + 1)
$('play').onclick = async () => {
  if (!info || busy) return
  if (playing) { stop(); return }
  playing = true; $('play').textContent = 'Pause'
  while (playing && current < info.frames - 1) {
    const start = performance.now()
    if (!rendering) { current++; sync(); requestRender() }
    await new Promise(resolve => setTimeout(resolve, Math.max(15, 1000 / info.fps - (performance.now() - start))))
  }
  stop()
}
$('video').onclick = () => action(async () => {
  const [path] = await choose('video'); if (!path) return
  const previous = project
  project = {version:1,video:path,assets:project.assets,placements:[]}
  try { await prepare(); dirty = true } catch (error) { project = previous; throw error }
})
$('assets').onclick = () => action(async () => {
  for (const path of await choose('assets')) {
    await api('asset',{path})
    const base = path.split(/[\\/]/).pop()
    let name = base, suffix = 2
    while (name in project.assets) name = `${base} (${suffix++})`
    project.assets[name] = path; dirty = true
  }
  await library()
})
$('open').onclick = () => action(async () => {
  const [path] = await choose('open'); if (!path) return
  const previous = project
  project = await api('load',{path})
  try { await prepare(); dirty = false } catch (error) { project = previous; throw error }
})
$('save').onclick = () => action(async () => {
  if (!project.video) throw Error('Import a video first')
  const [path] = await choose('save'); if (!path) return
  await api('save',{path,project}); dirty = false; status(`Saved ${path}`)
})
$('export').onclick = () => action(async () => {
  if (!info) throw Error('Import a video first')
  const [path] = await choose('export'); if (!path) return
  status('Exporting through Python. Please wait…')
  await api('export',{path,project}); status(`Exported ${path}`)
})
$('remove').onclick = () => {
  if (busy || selected < 0) return
  project.placements.splice(selected,1); selected = -1; dirty = true; sync(); requestRender()
}
for (const key of controls) $(key).onchange = () => {
  if (busy || selected < 0) return
  const p = project.placements[selected], value = Number($(key).value)
  if (!Number.isFinite(value) || (['start','duration'].includes(key) && !Number.isInteger(value)) ||
      (key === 'start' && (value < 0 || value >= info.frames)) ||
      (key === 'duration' && value < 1) || (key === 'scale' && (value < .001 || value > 100)) ||
      (key === 'strength' && Math.abs(value) > 20)) { sync(); return }
  p[key] = value; current = p.start; dirty = true; stop(); sync(); requestRender()
}
function point(event) {
  const rect = canvas.getBoundingClientRect()
  // object-fit:contain can letterbox within the CSS canvas box.
  const scale = Math.min(rect.width / canvas.width, rect.height / canvas.height)
  return {x:(event.clientX - rect.left - (rect.width - canvas.width * scale) / 2) / scale,
          y:(event.clientY - rect.top - (rect.height - canvas.height * scale) / 2) / scale}
}
canvas.ondragover = event => { if (info && !busy) event.preventDefault() }
canvas.ondrop = event => {
  event.preventDefault(); if (!info || busy) return
  const asset = event.dataTransfer.getData('text/plain'); if (!(asset in project.assets)) return
  const {x,y} = point(event)
  stop(); project.placements.push({asset,start:current,duration:120,x,y,scale:1,rotation:0,strength:1})
  selected = project.placements.length - 1; dirty = true; sync(); requestRender()
}
canvas.onpointerdown = event => {
  if (busy || selected < 0) return
  const p = project.placements[selected]
  if (current !== p.start) { seek(p.start); status('Returned to placement start. Drag again to transform.'); return }
  stop(); const xy = point(event)
  drag = {xy,x:p.x,y:p.y,rotation:p.rotation,rotate:event.shiftKey}
  canvas.setPointerCapture(event.pointerId)
}
canvas.onpointermove = event => {
  if (!drag || busy) return
  const p = project.placements[selected], xy = point(event)
  if (drag.rotate) p.rotation = drag.rotation + (xy.x - drag.xy.x) * .5
  else { p.x = drag.x + xy.x - drag.xy.x; p.y = drag.y + xy.y - drag.xy.y }
  dirty = true; sync(); requestRender()
}
canvas.onpointerup = canvas.onpointercancel = () => { drag = null }
canvas.onwheel = event => {
  if (busy || selected < 0) return
  event.preventDefault(); stop()
  const p = project.placements[selected]; current = p.start
  p.scale = Math.max(.001,Math.min(100,p.scale * Math.exp(-event.deltaY * .001)))
  dirty = true; sync(); requestRender()
}
window.addEventListener('keydown', event => {
  if (['INPUT','TEXTAREA'].includes(event.target.tagName)) return
  if (event.key === 'ArrowRight') { event.preventDefault(); seek(current + 1) }
  if (event.key === 'ArrowLeft') { event.preventDefault(); seek(current - 1) }
})
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = '' } })
sync()
