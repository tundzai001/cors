"""Display-only device labels; never use User-Agent as authentication evidence."""


def _device_os_name(ua, fallback):
    if 'iphone' in ua or 'ipod' in ua:
        return 'iPhone · iOS' if 'iphone' in ua else 'iPod · iOS'
    if 'ipad' in ua:
        return 'iPad · iPadOS'
    if 'android' in ua:
        return 'Android'
    if 'windows nt 10.0' in ua or 'windows nt 11.0' in ua:
        return 'Windows 10/11'
    if 'windows' in ua:
        return 'Windows'
    if 'macintosh' in ua or 'mac os x' in ua:
        # iPad desktop UA can be identical to Mac; preserve the client's touch hint.
        return 'iPad · iPadOS' if (fallback or '').startswith('iPad') else 'macOS'
    if 'linux' in ua:
        return 'Linux'
    return None


def _device_browser_name(ua, fallback):
    if any(token in ua for token in ('edg/', 'edgios/', 'edga/')):
        return 'Edge'
    if any(token in ua for token in ('opr/', 'opera/', 'opios/')):
        return 'Opera'
    if 'brave/' in ua or (('chrome/' in ua or 'crios/' in ua) and (fallback or '').endswith(' · Brave')):
        return 'Brave'
    if 'crios/' in ua or 'chrome/' in ua:
        return 'Chrome'
    if 'fxios/' in ua or 'firefox/' in ua:
        return 'Firefox'
    if 'safari/' in ua:
        return 'Safari'
    return 'Trình duyệt'


def client_device_name(user_agent: str, fallback: str = '') -> str:
    ua = (user_agent or '').lower()
    os_name = _device_os_name(ua, fallback)
    if not os_name:
        return fallback or 'Thiết bị'
    return f'{os_name} · {_device_browser_name(ua, fallback)}'
