import { cp, mkdir, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const dist = join(root, 'dist');
const nodeExe = process.execPath;
const stagingExe = join(dist, 'upstream-price-adjuster.exe');
const blob = join(dist, 'sea-prep.blob');
const postjectCli = resolve(root, 'node_modules/postject/dist/cli.js');
const esbuildCli = resolve(root, 'node_modules/esbuild/bin/esbuild');
const seaEntry = join(dist, 'sea-entry.cjs');
const files = ['index.html', 'pricing.html', 'styles.css', 'sync-console.css', 'dai-sync.config.example.json'];
const readme = `D-AI 上游倍率自动调价器 Windows EXE\n\n启动：双击 upstream-price-adjuster.exe\n配置：复制 dai-sync.config.example.json 为 dai-sync.config.json 后填写本机配置。\n浏览器会话：保存在 runtime\\，不会内置到 EXE。\n依赖：本机需要 Google Chrome 或 Microsoft Edge；不需要 Node.js。\n安全：默认 dry-run；apply、生命周期创建、分组停用、恢复均需要独立安全门。\n`;

function run(command, args) {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(command, args, { cwd: root, stdio: 'inherit', shell: false, windowsHide: false });
    child.once('error', rejectPromise);
    child.once('close', (code) => code === 0 ? resolvePromise() : rejectPromise(new Error(`${command} 退出代码 ${code}`)));
  });
}

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await run(nodeExe, [esbuildCli, 'packaging-entry.mjs', '--bundle', '--platform=node', '--format=cjs', '--external:playwright-core', '--external:chromium-bidi/*', `--outfile=${seaEntry}`]);
await run(nodeExe, ['--experimental-sea-config', 'sea-config.json']);
await cp(nodeExe, stagingExe);
await run(nodeExe, [postjectCli, stagingExe, 'NODE_SEA_BLOB', blob, '--sentinel-fuse', 'NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2']);
for (const file of files) await cp(join(root, file), join(dist, file));
await writeFile(join(dist, 'README-EXE.txt'), readme, 'utf8');
await cp(join(root, 'src'), join(dist, 'src'), { recursive: true });
await cp(join(root, 'node_modules', 'playwright-core'), join(dist, 'node_modules', 'playwright-core'), { recursive: true });
await mkdir(join(dist, 'runtime'), { recursive: true });
await rm(blob, { force: true });
await rm(seaEntry, { force: true });
if (!existsSync(stagingExe)) throw new Error('EXE 构建产物不存在');
console.log(`已生成 ${stagingExe}`);
