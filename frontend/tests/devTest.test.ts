import { spawn, type ChildProcess } from 'node:child_process'
import net from 'node:net'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

// The one-command test mode (npm run dev:test) must never collide with the tunnel to a real server.
const SCRIPT = resolve(__dirname, '..', 'scripts', 'dev-test.mjs')
const PORT = 18433
const running: ChildProcess[] = []

function start(env: Record<string, string> = {}) {
  const child = spawn('node', [SCRIPT, '--server-only'], { env: { ...process.env, DEV_TEST_PORT: String(PORT), ...env }, stdio: ['ignore', 'pipe', 'pipe'] })
  running.push(child)
  let out = ''
  child.stdout.on('data', (d) => (out += d))
  child.stderr.on('data', (d) => (out += d))
  const exited = new Promise<number | null>((done) => child.on('exit', (c) => done(c)))
  return { child, exited, output: () => out }
}

async function until(check: () => boolean | Promise<boolean>, what: string) {
  const deadline = Date.now() + 40000
  while (!(await check())) {
    if (Date.now() > deadline) throw new Error(`timeout: ${what}`)
    await new Promise((r) => setTimeout(r, 150))
  }
}

const answers = () => fetch(`http://127.0.0.1:${PORT}/admin/status`).then((r) => r.status === 401, () => false)

afterEach(async () => {
  for (const c of running.splice(0)) c.kill()
  await until(async () => !(await answers()), 'test server stopped')
})

describe('test mode', () => {
  it('starts a real admin API on its own port and stops it again', async () => {
    const a = start()
    await until(answers, 'test server up')
    await until(() => a.output().includes('bleibt unberührt'), 'banner')
    expect(a.output()).toContain(`Port ${PORT}`)
    expect(a.output()).toContain('8431')
    a.child.kill('SIGTERM')
    await a.exited
    await until(async () => !(await answers()), 'test server stopped')
  })

  it('refuses to start on the port of the real tunnel', async () => {
    const a = start({ DEV_TEST_PORT: '8431' })
    expect(await a.exited).toBe(1)
    expect(a.output()).toContain('echten Server')
  })

  it('refuses to start when the port is already taken, and leaves the other program alone', async () => {
    const taken = net.createServer()
    await new Promise<void>((ok) => taken.listen(PORT, '127.0.0.1', ok))
    const a = start()
    expect(await a.exited).toBe(1)
    expect(a.output()).toContain(`Port ${PORT} ist schon belegt`)
    expect(taken.listening).toBe(true)
    await new Promise((ok) => taken.close(ok))
  })

  it('is not the port of the real tunnel by default', () => {
    expect(PORT).not.toBe(8431)
  })
})
