import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createDashboardServer } from './server.mjs';
import { main as runWorker } from './bin/dai-group-sync.mjs';
import { launchDashboard } from './scripts/launch-dashboard.mjs';

async function main() {
  const packaged = process.argv.includes('--server') || process.argv.includes('--worker');
  const root = packaged
    ? resolve(process.execPath, '..')
    : resolve(fileURLToPath(new URL('.', import.meta.url)));
  const workerIndex = process.argv.indexOf('--worker');

  if (workerIndex >= 0) {
    process.exitCode = await runWorker(process.argv.slice(workerIndex + 1));
    return;
  }
  if (!process.argv.includes('--server')) {
    await launchDashboard();
    return;
  }
  const port = Number(process.env.PORT || 4173);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('PORT 必须是 1 到 65535 之间的整数');
  }
  const server = createDashboardServer({ root });
  server.listen(port, '127.0.0.1', () => {
    console.log(`D-AI 同名分组同步控制台已启动：http://127.0.0.1:${port}/`);
  });
  let shuttingDown = false;
  const shutdown = () => {
    if (shuttingDown) return;
    shuttingDown = true;
    server.syncController.stop().finally(() => server.close());
  };
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
}

main().catch((error) => {
  console.error(`EXE 无法启动：${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
