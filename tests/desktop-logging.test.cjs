'use strict'
const {test} = require('node:test')
const assert = require('node:assert/strict')
const {spawnSync} = require('node:child_process')
const path = require('node:path')

// Exercise actual terminal streams, including Electron's current console event shape.
test('terminal logs retain layers and severity while redacting session tokens', () => {
  const modulePath = path.resolve(__dirname,'../desktop/logging.cjs')
  const result = spawnSync(process.execPath, ['-e', `
    const {EventEmitter} = require('node:events')
    const {writeLog, forwardRenderer} = require(${JSON.stringify(modulePath)})
    writeLog('electron:main', 'info', 'startup')
    writeLog('python', 'stdout', 'first line\\nsecond line')
    writeLog('python', 'stderr', 'traceback')
    writeLog('electron:renderer', 'error', 'at http://localhost/app.js?token=secret-value:42:7)')
    const contents = new EventEmitter()
    forwardRenderer(contents)
    for (const level of ['info','debug','warning','error']) {
      contents.emit('console-message', {
        level, message:'request http://127.0.0.1/?token=secret-value&frame=3',
        sourceId:'http://127.0.0.1/app.js?token=secret-value', lineNumber:12
      })
    }
  `], {encoding:'utf8'})
  assert.ifError(result.error)
  assert.equal(result.status,0,result.stderr)
  assert.match(result.stdout,/\[electron:main\] INFO startup/)
  assert.match(result.stdout,/\[python\] STDOUT first line\n\[python\] STDOUT second line/)
  assert.match(result.stderr,/\[python\] STDERR traceback/)
  assert.match(result.stderr,/token=\[redacted\]:42:7\)/)
  for (const level of ['INFO','DEBUG']) assert.match(result.stdout,new RegExp(`\\[electron:renderer\\] ${level}`))
  for (const level of ['WARNING','ERROR']) assert.match(result.stderr,new RegExp(`\\[electron:renderer\\] ${level}`))
  assert.match(result.stdout,/token=\[redacted\]&frame=3 \(\/app.js:12\)/)
  assert.doesNotMatch(result.stdout + result.stderr,/secret-value/)
})
