#!/usr/bin/env node
/* Starts the dashboard API and the Vite dev server together.
 *
 * The UI is useless without /api, and forgetting the second terminal produced a
 * raw `ECONNREFUSED 127.0.0.1:8765` stack trace that says nothing about what to
 * do. So `npm run dev` runs both, and stopping one stops the other.
 *
 * Node's standard library only -- adding `concurrently` to look at a CSV file
 * would be the wrong trade.
 */

import { spawn } from 'node:child_process'
import { connect } from 'node:net'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const REPO = resolve(FRONTEND, '..')
const API_PORT = Number(process.env.MORX_PORT || 8765)
const PYTHON = process.env.PYTHON || 'python3'

const children = []
let shuttingDown = false

/** Is something already answering on the API port? */
function portInUse(port) {
  return new Promise((resolve_) => {
    const socket = connect({ host: '127.0.0.1', port })
    const done = (answer) => {
      socket.destroy()
      resolve_(answer)
    }
    socket.setTimeout(700)
    socket.once('connect', () => done(true))
    socket.once('timeout', () => done(false))
    socket.once('error', () => done(false))
  })
}

function run(name, command, args, options = {}) {
  const child = spawn(command, args, { stdio: 'inherit', ...options })
  children.push(child)

  child.on('error', (error) => {
    if (error.code === 'ENOENT') {
      console.error(`\n[dev] Could not start ${name}: '${command}' is not on your PATH.`)
      if (command === PYTHON) {
        console.error("[dev] Set PYTHON to your interpreter, e.g. PYTHON=./venv/bin/python npm run dev\n")
      }
    } else {
      console.error(`\n[dev] ${name} failed to start:`, error.message, '\n')
    }
    shutdown(1)
  })

  child.on('exit', (code) => {
    if (shuttingDown) return
    console.error(`\n[dev] ${name} exited (code ${code}). Stopping the other process too.\n`)
    shutdown(code ?? 1)
  })

  return child
}

function shutdown(code = 0) {
  if (shuttingDown) return
  shuttingDown = true
  for (const child of children) {
    if (!child.killed) child.kill('SIGTERM')
  }
  process.exit(code)
}

process.on('SIGINT', () => shutdown(0))
process.on('SIGTERM', () => shutdown(0))

const alreadyRunning = await portInUse(API_PORT)

if (alreadyRunning) {
  console.log(`[dev] Reusing the dashboard API already on :${API_PORT}.`)
} else {
  console.log(`[dev] Starting the dashboard API: ${PYTHON} -m morx web --port ${API_PORT}`)
  run('the dashboard API', PYTHON, ['-m', 'morx', 'web', '--port', String(API_PORT)], { cwd: REPO })
}

run('Vite', process.execPath, [resolve(FRONTEND, 'node_modules/vite/bin/vite.js')], { cwd: FRONTEND })
