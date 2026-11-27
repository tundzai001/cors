"""Publish permanent application bans to the local Nginx reverse proxy."""
import asyncio
import ipaddress
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)


def blocklist_text(ips):
    addresses = set()
    for value in ips:
        address = ipaddress.ip_address(value)
        if address.is_loopback or address.is_unspecified:
            raise ValueError('Refusing to publish a loopback/unspecified proxy ban')
        addresses.add(str(address))
    return '# Managed by Base security; exact client IPs only.\n' + ''.join(f'{ip} 1;\n' for ip in sorted(addresses))


def atomic_write(path, content):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class NginxSecuritySync:
    def __init__(self, prefix=None):
        self.prefix = Path(prefix or os.getenv('NGINX_SECURITY_PREFIX', ''))
        self.enabled = bool(prefix or os.getenv('NGINX_SECURITY_PREFIX'))
        self.status = {'enabled': self.enabled, 'state': 'pending' if self.enabled else 'disabled', 'updated_at': None}
        self.requested_ips = set()
        self._last_content = None
        self._failed_content = None
        self._retry_at = 0.0
        self._last_error = None
        self._last_error_logged_at = 0.0
        self._lock = asyncio.Lock()

    def _run(self, *args):
        executable = self.prefix / ('nginx.exe' if os.name == 'nt' else 'sbin/nginx')
        result = subprocess.run([str(executable), '-p', self.prefix.as_posix() + '/', '-c', 'conf/nginx.conf', *args],
                                capture_output=True, text=True, errors='replace', timeout=15, shell=False)
        if result.returncode:
            raise RuntimeError(result.stderr[-1500:] or 'Nginx command failed')

    async def sync(self, ips):
        if not self.enabled:
            return
        async with self._lock:
            content = None
            try:
                content = blocklist_text(ips)
                if content == self._last_content:
                    return
                if content == self._failed_content and time.monotonic() < self._retry_at:
                    return
                path = self.prefix / 'conf/cors-blocked-ips.conf'
                # Installer must have provisioned the include; never silently invent a deployment.
                previous = path.read_text(encoding='utf-8')
                atomic_write(path, content)
                try:
                    await asyncio.to_thread(self._run, '-t')
                    await asyncio.to_thread(self._run, '-s', 'reload')
                except Exception:
                    atomic_write(path, previous)
                    raise
                self._last_content = content
                self._failed_content = None
                self._retry_at = 0.0
                self._last_error = None
                self.requested_ips = set(ips)
                # A successful signal is not proof that remote traffic is being dropped.
                self.status.update(state='reload_requested', updated_at=int(time.time()), error=None, next_retry_at=None)
            except Exception as exc:
                error = str(exc)
                now = time.monotonic()
                self._failed_content = content
                self._retry_at = now + 60
                self.status.update(state='error', error=error, next_retry_at=int(time.time()) + 60)
                if error != self._last_error or now - self._last_error_logged_at >= 300:
                    logger.error('Nginx security synchronization failed (retry in 60s): %s', error)
                    self._last_error_logged_at = now
                self._last_error = error


nginx_security = NginxSecuritySync()


async def sync_security_bans(restore_application=False):
    from sqlalchemy import select
    from .database import auth_session_scope
    from .models import IpSecurityLog
    from .redis_client import redis_client
    async with auth_session_scope() as db:
        result = await db.execute(select(IpSecurityLog).where(IpSecurityLog.block_type == 'permanent'))
        permanent = result.scalars().all()
    # Restore the application's gate after a restart even without Redis enabled.
    if restore_application:
        for block in permanent:
            if not await redis_client.is_ip_banned(block.ip_address):
                await redis_client.ban_ip(block.ip_address)
    ips = {block.ip_address for block in permanent} | set(await redis_client.get_banned_ips())
    await nginx_security.sync(ips)


async def nginx_security_loop():
    while True:
        try:
            await sync_security_bans()
        except Exception as exc:
            nginx_security.status.update(state='error')
            logger.exception('Security ban synchronization failed: %s', exc)
        await asyncio.sleep(10)
