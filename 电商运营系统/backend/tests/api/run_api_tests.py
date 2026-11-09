"""Run the FastAPI prototype API test suite and keep a run record.

Usage (from the project root, system Python 3.12):

    python "电商运营系统/backend/tests/api/run_api_tests.py"
    python ".../run_api_tests.py" --out test_api_partial.log --pattern "test_auth.py"
    python ".../run_api_tests.py" --out test_api_full.log

The log is written to ``03_测试与验证/系统测试/runs/20261029_prototype/`` (UTF-8, with the
actual command, environment, per-test results, duration).  The official runtime
databases are hashed before and after the run and the comparison is recorded, so the
evidence shows that the tests did not modify ``电商运营系统/runtime``.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SYSTEM_ROOT = ROOT / '电商运营系统'
EVIDENCE_DIR = ROOT / '03_测试与验证' / '系统测试' / 'runs' / datetime.now().strftime('%Y%m%d_regression')
OFFICIAL_FILES = [
    SYSTEM_ROOT / 'runtime' / 'retail.sqlite3',
    SYSTEM_ROOT / 'runtime' / 'app.sqlite3',
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_state(path: Path) -> str:
    if not path.is_file():
        return '<missing>'
    stat = path.stat()
    return f'sha256={sha256(path)} size={stat.st_size}'


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description='运行 FastAPI 原型 API 自动化测试')
    parser.add_argument('--pattern', default='test_*.py', help='测试文件匹配模式')
    parser.add_argument('--out', default='test_api_full.log', help='证据日志文件名')
    parser.add_argument('--label', default='', help='本次运行的说明（写入日志头）')
    args = parser.parse_args()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = EVIDENCE_DIR / args.out
    before = {str(path): file_state(path) for path in OFFICIAL_FILES}

    python_version = sys.version.split()[0]
    import pandas  # local import: only for the environment record

    header = [
        '=' * 78,
        f'FastAPI 原型 API 自动化测试运行记录',
        f'运行时间: {datetime.now().astimezone().isoformat(timespec="seconds")}',
        f'命令: {" ".join([Path(sys.executable).name] + sys.argv)}',
        f'工作目录: {Path.cwd()}',
        f'说明: {args.label or "（未填写）"}',
        f'Python: {python_version} ({platform.platform()})',
        f'pandas: {pandas.__version__}',
        '隔离方式: 每个测试类一个 python run.py 子进程 + 临时 ECE_RUNTIME_DIR + '
        '复制的 retail.sqlite3 (ECE_ORIGINAL_DB)',
        '官方库运行前:',
    ]
    header += [f'  {path}: {state}' for path, state in before.items()]
    header.append('=' * 78)

    stream = log_path.open('w', encoding='utf-8')
    tee = Tee(sys.stdout, stream)
    tee.write('\n'.join(header) + '\n')

    started = time.monotonic()
    result = unittest.TestResult()
    try:
        suite = unittest.TestLoader().discover(str(HERE), pattern=args.pattern,
                                               top_level_dir=str(HERE))
        runner = unittest.TextTestRunner(stream=tee, verbosity=2)
        result = runner.run(suite)
    finally:
        elapsed = time.monotonic() - started
        after = {str(path): file_state(path) for path in OFFICIAL_FILES}
        tail = [
            '=' * 78,
            f'耗时: {elapsed:.2f}s',
            f'用例: 共 {result.testsRun}，失败 {len(result.failures)}，'
            f'错误 {len(result.errors)}，跳过 {len(result.skipped)}',
            '官方库运行后:',
        ]
        tail += [f'  {path}: {state}' for path, state in after.items()]
        tail.append('官方库是否被修改: ' +
                    ('否' if before == after else '是（需核查）'))
        if result.failures:
            tail.append('失败用例:')
            tail += [f'  - {test}' for test, _ in result.failures]
        if result.errors:
            tail.append('错误用例:')
            tail += [f'  - {test}' for test, _ in result.errors]
        if result.skipped:
            tail.append('跳过用例:')
            tail += [f'  - {test}: {reason.splitlines()[0] if reason else ""}'
                     for test, reason in result.skipped]
        tail.append('=' * 78)
        tee.write('\n'.join(tail) + '\n')
        stream.close()

    ok = not result.failures and not result.errors
    print(f'\n证据日志: {log_path}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
