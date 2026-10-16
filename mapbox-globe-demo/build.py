"""Export a static demo using the public token from .env."""
from pathlib import Path
import json
import shutil
from server import ROOT, public_token


def build():
    token = public_token()
    target = ROOT / 'dist'
    target.mkdir(exist_ok=True)
    for name in ('index.html', 'style.css', 'app.js', 'motion.js'):
        shutil.copy2(ROOT / name, target / name)
    shutil.copytree(ROOT / 'vendor', target / 'vendor', dirs_exist_ok=True)
    (target / 'config.js').write_text('export default ' + json.dumps(token) + ';\n', encoding='utf-8')
    print('Static demo ready in mapbox-globe-demo/dist (no .env files).')


if __name__ == '__main__':
    build()
