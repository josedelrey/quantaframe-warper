'use strict'
const $ = id => document.getElementById(id)
const token = new URLSearchParams(location.search).get('token')
let project = {version:1,video:'',assets:{},placements:[]}
let info, selected = -1, current = 0, playback = null, busy = false
let revision = 0, pending = null, rendering = false, drag = null, dirty = false
const controls = ['start','duration','x','y','scale','rotation','strength']
const canvas = $('preview'), context = canvas.getContext('2d'), transform = $('transform')
const assetSizes = {}
window.addEventListener('error', event => console.error('Uncaught UI error:', event.error?.stack || event.message))
window.addEventListener('unhandledrejection', event => console.error('Unhandled UI rejection:', event.reason?.stack || String(event.reason)))
function status(text) {
  if ($('status').textContent !== text) console.info(text)
  $('status').textContent = text
}
function notice(text) { $('empty').textContent = text; $('empty').hidden = false }
function failure(error) { console.error(error.stack || error.message); status(error.message); notice(`Could not load or process video: ${error.message}`) }
async function api(method, data, binary = false) {
  const response = await fetch(`/api/${method}`, {method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:JSON.stringify(data)})
  if (response.ok && binary) return response.blob()
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
  try { await work() } catch (error) { failure(error) }
  finally { busy = false; sync(); document.querySelectorAll('header button').forEach(b => { b.disabled = false }) }
}
function stop() { playback = null; $('play').textContent = 'Play' }
function syncTimeline() {
  $('timeline').value = current
  $('frame').value = current
  updateTransform()
}
function sync() {
  syncTimeline()
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
    assetSizes[name] = {width:result.width,height:result.height}
    const image = document.createElement('img')
    image.src = result.url; image.title = name; image.alt = name; image.draggable = true
    image.ondragstart = event => { event.dataTransfer.setData('text/plain',name) }
    $('library').append(image)
  }
}
async function prepare() {
  revision++; pending = null
  notice('Opening video…')
  status('Opening video…')
  // Validate thumbnails before replacing the current engine or timeline state.
  await library()
  const {id} = await api('prepare_start',{project})
  let nextInfo
  while (true) {
    const job = await api('prepare_status',{id})
    if (job.state === 'error') throw Error(job.error)
    if (job.state === 'done') { nextInfo = job.result; break }
    const phase = {opening:'Opening video',decode:'Decoding and analyzing video',vectors:'Extracting codec vectors'}[job.phase] || job.phase
    const text = `${phase} · ${job.completed}${job.total ? ' / ' + job.total : ''} frames. High-resolution clips can take several minutes.`
    status(text); notice(text)
    await new Promise(resolve => setTimeout(resolve,250))
  }
  info = nextInfo
  canvas.width = info.width; canvas.height = info.height
  canvas.style.aspectRatio = `${info.width} / ${info.height}`
  $('timeline').max = $('frame').max = info.frames - 1
  current = 0; selected = -1
  sync(); notice('Rendering first frame…'); requestRender()
  status(`${info.width} × ${info.height} · ${info.frames} frames · ${info.fps.toFixed(3)} fps · ${info.vectors} codec vectors`)
}
function requestRender(frame = current, session = null) {
  if (!info) return
  revision++; pending = {frame, session}
  if (!rendering) void renderLoop()
}
async function loadFrame(frame, session) {
  if (session) return createImageBitmap(await api('playback', {project, frame}, true))
  const result = await api('render', {project, frame})
  const image = new Image()
  image.src = result.url
  await image.decode()
  return image
}
async function renderLoop() {
  rendering = true
  while (pending) {
    const {frame, session} = pending
    pending = null
    const ticket = revision
    try {
      const image = await loadFrame(frame, session)
      if (ticket === revision && (!session || session === playback)) {
        if (canvas.width !== image.width || canvas.height !== image.height) {
          canvas.width = image.width; canvas.height = image.height
        }
        context.drawImage(image,0,0)
        $('empty').hidden = true
        current = frame
        if (session && current === info.frames - 1) stop()
        syncTimeline()
      }
      image.close?.()
    } catch (error) {
      if (ticket === revision && (!session || session === playback)) { failure(error); stop() }
    }
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
function tickPlayback(session, now) {
  if (session !== playback) return
  if (!rendering) {
    const time = info.timestamps[session.frame] + (now - session.started) / 1000
    let left = current, right = info.frames - 1
    while (left < right) {
      const mid = Math.ceil((left + right) / 2)
      if (info.timestamps[mid] <= time) left = mid
      else right = mid - 1
    }
    if (left > current) requestRender(left, session)
  }
  requestAnimationFrame(now => tickPlayback(session, now))
}
$('play').onclick = () => {
  if (!info || busy) return
  if (playback) {
    stop()
    syncTimeline()
    return
  }
  if (info.frames < 2) return
  const restart = current === info.frames - 1
  const session = {frame:restart ? 0 : current, started:performance.now()}
  playback = session
  $('play').textContent = 'Pause'
  if (restart) requestRender(0, session)
  requestAnimationFrame(now => tickPlayback(session, now))
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
$('export').onclick = () => action(async () => {
  if (!info) throw Error('Import a video first')
  const [path] = await choose('export'); if (!path) return
  const progress = $('export-progress')
  progress.hidden = false; progress.value = 0
  try {
    const {id} = await api('export_start',{path,project})
    while (true) {
      const job = await api('export_status',{id})
      if (job.state === 'error') throw Error(job.error)
      if (job.state === 'done') { progress.value = 100; status(`Exported ${path}`); break }
      const percent = job.total ? Math.floor(100 * job.completed / job.total) : 0
      progress.value = percent
      status(percent === 100 ? 'Finalizing export…' : `Exporting: ${percent}% (${job.completed} / ${job.total || '…'} frames)`)
      await new Promise(resolve => setTimeout(resolve,250))
    }
  } finally { progress.hidden = true }
})
$('remove').onclick = () => {
  if (busy || selected < 0) return
  stop()
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
function frameGeometry() {
  const rect = canvas.getBoundingClientRect()
  // object-fit:contain can letterbox within the CSS canvas box.
  const scale = Math.min(rect.width / canvas.width, rect.height / canvas.height)
  return {
    left:rect.left + (rect.width - canvas.width * scale) / 2,
    top:rect.top + (rect.height - canvas.height * scale) / 2,
    xScale:canvas.width * scale / info.width,
    yScale:canvas.height * scale / info.height
  }
}
function point(event) {
  const {left,top,xScale,yScale} = frameGeometry()
  return {x:(event.clientX - left) / xScale, y:(event.clientY - top) / yScale}
}
canvas.ondragover = event => { if (info && !busy) event.preventDefault() }
canvas.ondrop = event => {
  event.preventDefault(); if (!info || busy) return
  const asset = event.dataTransfer.getData('text/plain'); if (!(asset in project.assets)) return
  const {x,y} = point(event)
  stop(); project.placements.push({asset,start:current,duration:120,x,y,scale:1,rotation:0,strength:1})
  selected = project.placements.length - 1; dirty = true; sync(); requestRender()
}
function updateTransform() {
  const p = project.placements[selected], size = p && assetSizes[p.asset]
  transform.hidden = !info || !p || !size || current !== p.start || busy || !!playback
  if (transform.hidden) return
  const stage = $('stage').getBoundingClientRect()
  const {left,top,xScale,yScale} = frameGeometry()
  const width = size.width * p.scale * xScale, height = size.height * p.scale * yScale
  Object.assign(transform.style,{width:`${width}px`,height:`${height}px`,
    left:`${left - stage.left + p.x * xScale - width / 2}px`,
    top:`${top - stage.top + p.y * yScale - height / 2}px`,
    transform:`rotate(${p.rotation}deg)`})
}
window.addEventListener('resize', updateTransform)
transform.onpointerdown = event => {
  if (busy || selected < 0) return
  event.preventDefault(); stop()
  const p = project.placements[selected], xy = point(event)
  const dx = xy.x - p.x, dy = xy.y - p.y
  drag = {xy,x:p.x,y:p.y,scale:p.scale,rotation:p.rotation,
    distance:Math.hypot(dx,dy), angle:Math.atan2(dy,dx), mode:event.target.dataset.mode || 'move'}
  transform.setPointerCapture(event.pointerId)
}
transform.onpointermove = event => {
  if (!drag || busy) return
  const p = project.placements[selected], xy = point(event)
  if (drag.mode === 'rotate') {
    p.rotation = drag.rotation + (Math.atan2(xy.y - drag.y,xy.x - drag.x) - drag.angle) * 180 / Math.PI
  } else if (drag.mode === 'scale') {
    p.scale = Math.max(.001,Math.min(100,drag.scale * Math.hypot(xy.x - drag.x,xy.y - drag.y) / Math.max(drag.distance,1)))
  } else { p.x = drag.x + xy.x - drag.xy.x; p.y = drag.y + xy.y - drag.xy.y }
  dirty = true; sync(); requestRender()
}
transform.onpointerup = transform.onpointercancel = () => { drag = null }
window.addEventListener('keydown', event => {
  if (['INPUT','TEXTAREA'].includes(event.target.tagName)) return
  if (event.key === 'ArrowRight') { event.preventDefault(); seek(current + 1) }
  if (event.key === 'ArrowLeft') { event.preventDefault(); seek(current - 1) }
})
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = '' } })
sync()

console.info('Workbench ready')
