"""Install Base IP blocking in the Windows Nginx configuration, then test/reload."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime


def patch_config(source, prefix='C:/nginx-1.28.0'):
    if any(char in prefix for char in ('"', '\n', '\r')):
        raise ValueError('Invalid Nginx prefix')
    geo = ('\n    # BEGIN BASE SECURITY GEO\n'
           '    geo $cors_blocked_ip {\n        default 0;\n'
           f'        include "{prefix}/conf/cors-blocked-ips.conf";\n    }}\n'
           '    # END BASE SECURITY GEO\n')
    if '# BEGIN BASE SECURITY GEO' in source:
        source = re.sub(r'\n    # BEGIN BASE SECURITY GEO.*?# END BASE SECURITY GEO\n', lambda m:geo, source, count=1, flags=re.S)
    else:
        source, count = re.subn(r'(\bhttp\s*\{)', lambda m:m[0]+geo, source, count=1)
        if not count:
            raise ValueError('Nginx http block not found')
    # Apply only to this product, in both HTTP and HTTPS servers, before any proxy location.
    pattern = r'(server_name\s+(?:cors\.aitogy\.com|base\.aitogy\.com)(?:\s+(?:cors\.aitogy\.com|base\.aitogy\.com))*\s*;)(?!\s*# BASE SECURITY DROP)'
    source, count = re.subn(pattern, r'\1\n        # BASE SECURITY DROP\n        if ($cors_blocked_ip) { return 444; }', source)
    if not count and '# BASE SECURITY DROP' not in source:
        raise ValueError('Base virtual host not found')
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', default='C:/nginx-1.28.0')
    parser.add_argument('--backend', required=True, help='Deployed cors_dashboard/backend directory')
    args = parser.parse_args()
    prefix = Path(args.prefix).resolve()
    if not (Path(args.backend) / 'server.py').is_file():
        parser.error('--backend must point to the deployed backend directory containing server.py')
    config = prefix / 'conf/nginx.conf'
    original = config.read_text(encoding='utf-8-sig')
    patched = patch_config(original, prefix.as_posix())
    executable = prefix / 'nginx.exe'
    backup = config.with_name('nginx.conf.backup-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(config, backup)
    blocklist = prefix / 'conf/cors-blocked-ips.conf'
    if not blocklist.exists():
        blocklist.write_text('# Managed by Base security; exact client IPs only.\n', encoding='utf-8')
    config.write_text(patched, encoding='utf-8')
    try:
        for action in (['-t'], ['-s','reload']):
            subprocess.run([str(executable), '-p', prefix.as_posix()+'/', '-c', 'conf/nginx.conf', *action], check=True, timeout=20)
    except Exception:
        config.write_text(original, encoding='utf-8')
        raise
    from dotenv import set_key
    set_key(str(Path(args.backend) / '.env'), 'NGINX_SECURITY_PREFIX', prefix.as_posix())
    set_key(str(Path(args.backend) / '.env'), 'BACKEND_HOST', '127.0.0.1')
    print('Nginx configuration tested; reload requested. Restart the backend for automatic ban synchronization.')
    print('Backup:', backup)


if __name__ == '__main__':
    main()
