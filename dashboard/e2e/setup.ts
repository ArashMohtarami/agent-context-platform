import { spawn } from 'node:child_process';
import { existsSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

export default async function setup() {
  const descriptor = process.env.V6_BROWSER_DESCRIPTOR;
  if (!descriptor) throw new Error('Fixture descriptor path missing');
  if (existsSync(descriptor)) {
    if (JSON.parse(readFileSync(descriptor, 'utf8')).fixture !== 'acp-phase9-disposable') throw new Error('Refusing unknown browser environment');
    return;
  }
  const repository = path.resolve(import.meta.dirname, '../..');
  const evidence = process.env.V6_REPORT_DIR ?? path.join(repository, 'dashboard/test-results/infrastructure');
  await mkdir(evidence, {recursive: true});
  const listener = createServer();
  await new Promise<void>(resolve => listener.listen(0, '127.0.0.1', resolve));
  const address = listener.address(); if (!address || typeof address === 'string') throw new Error('No loopback port');
  const port = address.port;
  await new Promise<void>((resolve, reject) => listener.close(error => error ? reject(error) : resolve()));
  const log = openSync(path.join(evidence, 'fixture.log'), 'w', 0o600);
  const child = spawn('bash', ['scripts/test-v6-isolation.sh'], {cwd: repository,
    env: {...process.env, V6_TEST_MONGODB_MEMORY_LIMIT: '3g', V6_OIDC_TESTS: '1', V6_DASHBOARD_BROWSER_TESTS: '1', V6_BROWSER_SERVE_ONLY: '1', V6_BROWSER_TEST_ORIGIN: `https://127.0.0.1:${port}`, V6_REPORT_DIR: evidence},
    stdio: ['ignore', log, log]});
  const stop = descriptor.replace(/\.[^.]+$/, '.stop');
  async function cleanup() {
    writeFileSync(stop, 'stop', {mode: 0o600});
    for (let count = 0; count < 200 && child.exitCode === null; count++) await delay(100);
    if (child.exitCode === null) child.kill('SIGTERM');
    await rm(stop, {force: true});
  }
  try {
    for (let count = 0; count < 2400; count++) {
      if (existsSync(descriptor)) return cleanup;
      if ((child.exitCode !== null || child.signalCode !== null)) throw new Error(`Fixture exited ${child.exitCode}; inspect ${evidence}/fixture.log`);
      await delay(100);
    }
    throw new Error(`Fixture did not become ready; inspect ${evidence}/fixture.log`);
  } catch (error) {await cleanup(); throw error;}
}
