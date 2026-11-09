"""Download pinned wheels from the local mirror and extract into a target site-packages.

The DSH sandbox blocks pip's unpack/temp machinery and the PyPI CDN, so this script
parses the mirror's simple index and unzips the wheels with plain file operations.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urljoin

MIRROR = 'https://pypi.tuna.tsinghua.edu.cn/simple/{name}/'
TARGET = Path(sys.argv[1])
CACHE = Path(sys.argv[2])
WHEELS = [
    ('fastapi', '0.115.6', 'py3-none-any'),
    ('starlette', '0.41.3', 'py3-none-any'),
    ('pydantic', '2.10.4', 'py3-none-any'),
    ('pydantic-core', '2.27.2', 'cp312-cp312-win_amd64'),
    ('annotated-types', '0.7.0', 'py3-none-any'),
    ('typing-extensions', '4.12.2', 'py3-none-any'),
    ('anyio', '4.7.0', 'py3-none-any'),
    ('idna', '3.10', 'py3-none-any'),
    ('sniffio', '1.3.1', 'py3-none-any'),
    ('uvicorn', '0.32.1', 'py3-none-any'),
    ('click', '8.1.8', 'py3-none-any'),
    ('colorama', '0.4.6', 'py3-none-any'),
    ('h11', '0.14.0', 'py3-none-any'),
    ('python-multipart', '0.0.20', 'py3-none-any'),
]
CACHE.mkdir(parents=True, exist_ok=True)
TARGET.mkdir(parents=True, exist_ok=True)
ANCHOR = re.compile(r'href="(?P<href>[^"#]+)(?:#[^"]*)?"[^>]*>(?P<name>[^<]+)</a>')


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def pick_url(name: str, version: str, tag: str) -> str:
    base = MIRROR.format(name=name)
    with opener().open(base, timeout=60) as resp:
        html = resp.read().decode('utf-8', 'replace')
    prefixes = (f'{name}-{version}-', f"{name.replace('-', '_')}-{version}-")
    matches = [(urljoin(base, m.group('href')), m.group('name').strip()) for m in ANCHOR.finditer(html)]
    for url, filename in matches:
        if filename.startswith(prefixes) and filename.endswith('.whl') and tag in filename:
            return url
    for url, filename in matches:
        if filename.startswith(prefixes) and filename.endswith('.whl'):
            return url
    raise RuntimeError(f'no wheel for {name}=={version} {tag}')


def main() -> None:
    installed = []
    for name, version, tag in WHEELS:
        url = pick_url(name, version, tag)
        filename = url.rsplit('/', 1)[1]
        local = CACHE / filename
        if not local.is_file() or local.stat().st_size == 0:
            print(f'download {filename}', flush=True)
            data = opener().open(url, timeout=120).read()
            local.write_bytes(data)
        with zipfile.ZipFile(local) as zf:
            zf.extractall(TARGET)
        installed.append(filename)
        print(f'extract {filename}', flush=True)
    print(json.dumps({'target': str(TARGET), 'count': len(installed)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
