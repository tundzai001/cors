import { spawnSync } from 'node:child_process';
import { mkdirSync, readdirSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = 'cors_dashboard/src';
const publicRoot = 'cors_dashboard/frontend';
const jsx = [
    'react-components.js', 'operations-view.js', 'dashboard-list.js',
    'dashboard-live.js', 'dashboard-detail.js', 'stations-map.js',
    'homepage-enterprise.v2.js', 'enterprise-station-detail.js',
    'blog-system.js', 'api-docs.js', 'advanced-features.js',
    'base-location-map.js',
    'dashboard-widgets.js', 'satellite-sky-view.js',
];
const css = [
    'style.css', 'enterprise-theme.css', 'cinematic-cards.css',
    'cinematic-upgrade.css', 'dashboard.css', 'ops-ui.css',
    'responsive-layout.css', 'zalo-bot.css', 'api-keys.css', 'coordinates.css',
];
const jsDist = `${publicRoot}/js/dist`;
const cssDist = `${publicRoot}/css/dist`;
mkdirSync(resolve(root, jsDist), { recursive: true });
mkdirSync(resolve(root, cssDist), { recursive: true });

function build(args) {
    const executable = process.platform === 'win32' ? 'npx.cmd' : 'npx';
    const result = spawnSync(executable, ['--yes', 'esbuild@0.25.8', ...args, '--target=es2018', '--charset=utf8'], {
        cwd: root,
        stdio: 'inherit',
        shell: process.platform === 'win32',
    });
    if (result.error) throw result.error;
    if (result.status !== 0) process.exit(result.status || 1);
}

// Classic scripts share globals across files. Keep their top-level names intact.
const plain = readdirSync(resolve(root, source, 'js'))
    .filter(name => name.endsWith('.js') && name !== 'rovers-global.js' && name !== 'dashboard-forms.js' && !jsx.includes(name));
build([
    ...plain.map(name => `${source}/js/${name}`),
    `--outdir=${jsDist}`, '--out-extension:.js=.min.js',
    '--minify-syntax', '--minify-whitespace',
]);

build([
    ...jsx.map(name => `${source}/js/${name}`),
    `--outdir=${jsDist}`, '--out-extension:.js=.min.js', '--loader:.js=jsx', '--format=iife',
    '--jsx-factory=React.createElement', '--jsx-fragment=React.Fragment',
    '--minify-syntax', '--minify-whitespace',
]);
for (const name of jsx) rmSync(resolve(root, jsDist, name), { force: true });

build([
    `${source}/js/dashboard-forms.js`, `--outfile=${jsDist}/dashboard-forms.min.js`,
    '--bundle', '--loader:.js=jsx', '--format=iife',
    '--jsx-factory=React.createElement', '--jsx-fragment=React.Fragment',
    '--minify-syntax', '--minify-whitespace',
]);
rmSync(resolve(root, jsDist, 'dashboard-forms.js'), { force: true });

build([
    `${source}/js/rovers-global.js`, '--format=iife',
    `--outfile=${jsDist}/rovers-global.min.js`, '--minify',
]);

build([
    ...css.map(name => `${source}/css/${name}`),
    `--outdir=${cssDist}`, '--out-extension:.css=.min.css',
    '--bundle', '--minify',
]);
build([
    `${source}/css/rover-page.css`,
    `--outfile=${cssDist}/rovers-global.min.css`, '--bundle', '--minify',
]);
