import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'

// Starts the real admin API on a fixed local port and waits until it answers.
const PORT = 18431
let child: ChildProcess | undefined

// ADMIN_PYTHON may name a Python that has the project and uvicorn; otherwise uv provides both.
function command(script: string): [string, string[]] {
  if (process.env.ADMIN_PYTHON) return [process.env.ADMIN_PYTHON, [script, String(PORT)]]
  return ['uv', ['run', '--with', 'uvicorn', 'python', script, String(PORT)]]
}

export async function setup() {
  const root = resolve(__dirname, '..', '..')
  const [cmd, args] = command(resolve(__dirname, 'support/admin_server.py'))
  child = spawn(cmd, args, {
    cwd: root,
    env: { ...process.env, PYTHONPATH: resolve(root, 'src') },
    stdio: ['ignore', 'inherit', 'inherit'],
  })
  const deadline = Date.now() + 30000
  for (;;) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/admin/status`)
      if (r.status === 401) return
    } catch {
      /* not up yet */
    }
    if (Date.now() > deadline) throw new Error('The admin API for the tests did not start. Is the Python environment set up (see tests/README.md)?')
    await new Promise((r) => setTimeout(r, 200))
  }
}

export async function teardown() {
  child?.kill()
}
