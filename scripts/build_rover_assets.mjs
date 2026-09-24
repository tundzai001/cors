import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const frontend = 'cors_dashboard/frontend';
const esbuild = process.platform === 'win32' ? 'npx.cmd' : 'npx';
const output = `${frontend}/css/dist`;

mkdirSync(resolve(root, output), { recursive: true });

const builds = [
    [`${frontend}/css/ops-ui.css`, '--bundle', `--outfile=${output}/ops-ui.min.css`],
    [`${frontend}/css/rover-page.css`, '--bundle', `--outfile=${output}/rovers-global.min.css`],
    [`${frontend}/js/rovers-global.js`, '--format=iife', `--outfile=${frontend}/js/dist/rovers-global.min.js`],
];

for (const args of builds) {
    const result = spawnSync(esbuild, ['--yes', 'esbuild@0.25.8', ...args, '--target=es2018', '--charset=utf8', '--minify'], {
        cwd: root,
        stdio: 'inherit',
        shell: process.platform === 'win32',
    });
    if (result.error) throw result.error;
    if (result.status !== 0) process.exit(result.status || 1);
}
