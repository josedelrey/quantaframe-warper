'use strict'

function redactSession(text) {
  return String(text).replace(/([?&]token=)[^\s&#"'<>):]+/g, '$1[redacted]')
}

function writeLog(layer, level, message) {
  const output = ['error', 'warning', 'stderr'].includes(level) ? process.stderr : process.stdout
  for (const line of redactSession(message).split(/\r?\n/)) {
    output.write(`[${layer}] ${level.toUpperCase()} ${line}\n`)
  }
}

function forwardRenderer(webContents) {
  webContents.on('console-message', ({level, message, sourceId, lineNumber}) => {
    // Keep source locations useful without printing the session-bearing URL query.
    let source = sourceId || ''
    try { source = new URL(source).pathname } catch {}
    writeLog('electron:renderer', level, `${message}${source ? ` (${source}:${lineNumber})` : ''}`)
  })
}

module.exports = {writeLog, forwardRenderer}
