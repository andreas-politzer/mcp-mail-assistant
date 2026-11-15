// One command for trying the app against a throw-away test server:
//   npm run dev:test
// It starts a real admin API with a temporary database on its own port, waits until it answers, and then
// starts the app pointed at it. The tunnel to your real server (port 8431) is never touched, so the two
// can run at the same time without getting in each other's way. Closing the app stops the test server.
//
//   npm run dev:test -- --server-only    only the test server (used by the automated test)
//
// ADMIN_PYTHON may name a Python that has the project and uvicorn; otherwise uv provides both.
import { spawn } from 'node:child_process'
import net from 'node:net'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const REAL_TUNNEL_PORT = 8431
const PORT = Number(process.env.DEV_TEST_PORT ?? 18432)
const PASSPHRASE = 'test passphrase for the front end'
const serverOnly = process.argv.includes('--server-only')

const here = dirname(fileURLToPath(import.meta.url))
const frontend = resolve(here, '..')
const root = resolve(frontend, '..')

const fail = (text) => {
  console.error(`\nAbbruch: ${text}\n`)
  process.exit(1)
}

if (PORT === REAL_TUNNEL_PORT) fail(`Port ${REAL_TUNNEL_PORT} gehört dem Tunnel zu deinem echten Server. Der Testserver nimmt einen anderen Port.`)

// A port counts as free only if it is free for both address families; "localhost" may mean either.
function freeOn(host) {
  return new Promise((done) => {
    const probe = net.createServer()
    probe.once('error', (e) => done(e.code === 'EADDRNOTAVAIL' || e.code === 'EAFNOSUPPORT'))
    probe.once('listening', () => probe.close(() => done(true)))
    probe.listen(PORT, host)
  })
}
if (!(await freeOn('127.0.0.1')) || !(await freeOn('::1'))) {
  fail(`Port ${PORT} ist schon belegt. Vermutlich läuft ein früherer Testserver noch. Beende ihn und starte neu.`)
}

const script = resolve(frontend, 'tests/support/admin_server.py')
const [cmd, args] = process.env.ADMIN_PYTHON
  ? [process.env.ADMIN_PYTHON, [script, String(PORT)]]
  : ['uv', ['run', '--with', 'uvicorn', 'python', script, String(PORT)]]
const server = spawn(cmd, args, { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'src') }, stdio: ['ignore', 'inherit', 'inherit'] })
let app

const stop = (code = 0) => {
  app?.kill()
  server.kill()
  process.exit(code)
}
process.on('SIGINT', () => stop(0))
process.on('SIGTERM', () => stop(0))
server.on('exit', (code) => {
  if (code) fail('Der Testserver hat sich beendet. Siehe die Meldung darüber.')
})

const deadline = Date.now() + 60000
for (;;) {
  try {
    if ((await fetch(`http://127.0.0.1:${PORT}/admin/status`)).status === 401) break
  } catch {
    /* not up yet */
  }
  if (Date.now() > deadline) {
    server.kill()
    fail('Der Testserver ist nicht angesprungen.')
  }
  await new Promise((r) => setTimeout(r, 200))
}

console.log(`\nTestserver läuft auf Port ${PORT}. Passphrase: ${PASSPHRASE}`)
console.log(`Der Tunnel zu deinem echten Server (Port ${REAL_TUNNEL_PORT}) bleibt unberührt.\n`)

if (!serverOnly) {
  app = spawn('npx', ['tauri', 'dev'], {
    cwd: frontend,
    env: { ...process.env, VITE_ADMIN_BASE: `http://127.0.0.1:${PORT}` },
    stdio: 'inherit',
  })
  app.on('exit', (code) => stop(code ?? 0))
}
