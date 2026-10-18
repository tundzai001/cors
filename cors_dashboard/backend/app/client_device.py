"""Display-only device labels; never use User-Agent as authentication evidence."""


def client_device_name(user_agent: str, fallback: str = '') -> str:
    ua = (user_agent or '').lower()
    if 'iphone' in ua or 'ipod' in ua:
        os_name = 'iPhone · iOS' if 'iphone' in ua else 'iPod · iOS'
    elif 'ipad' in ua:
        os_name = 'iPad · iPadOS'
    elif 'android' in ua:
        os_name = 'Android'
    elif 'windows nt 10.0' in ua or 'windows nt 11.0' in ua:
        os_name = 'Windows 10/11'
    elif 'windows' in ua:
        os_name = 'Windows'
    elif 'macintosh' in ua or 'mac os x' in ua:
        # iPad desktop UA can be identical to Mac; preserve the client's touch hint.
        os_name = 'iPad · iPadOS' if (fallback or '').startswith('iPad') else 'macOS'
    elif 'linux' in ua:
        os_name = 'Linux'
    else:
        return fallback or 'Thiết bị'

    if any(token in ua for token in ('edg/', 'edgios/', 'edga/')):
        browser = 'Edge'
    elif any(token in ua for token in ('opr/', 'opera/', 'opios/')):
        browser = 'Opera'
    elif 'crios/' in ua or 'chrome/' in ua:
        browser = 'Chrome'
    elif 'fxios/' in ua or 'firefox/' in ua:
        browser = 'Firefox'
    elif 'safari/' in ua:
        browser = 'Safari'
    else:
        browser = 'Trình duyệt'
    return f'{os_name} · {browser}'
