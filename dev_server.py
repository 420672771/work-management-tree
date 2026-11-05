#!/usr/bin/env python3
"""Opt-in backend reloader for the pilot service. Run under its existing unit."""
import argparse
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
EXCLUDED = {'scripts', 'tests', 'static', 'data', 'logs', 'artifacts', 'bin', '__pycache__', 'node_modules'}


def sources(root):
    """Hash Python runtime source only; data, frontend and tests never restart it."""
    result = {}
    for path in sorted(root.rglob('*.py')):
        relative = path.relative_to(root)
        if any(part.startswith('.') or part in EXCLUDED for part in relative.parts):
            continue
        result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def validate(root, snapshot, probe=True):
    if 'app.py' not in snapshot:
        raise RuntimeError('没有找到项目后端 app.py')
    for name in snapshot:
        path = root / name
        compile(path.read_bytes(), str(path), 'exec')
    if probe:
        # Check imports before stopping the old process. The probe never opens
        # the real database; importing this app does not initialize its schema.
        with tempfile.TemporaryDirectory(prefix='worktree-import-') as temporary:
            env = dict(os.environ, WMT_DB=str(Path(temporary) / 'probe.db'), PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([sys.executable, '-B', '-c', 'import app'], cwd=root,
                                    env=env, capture_output=True, text=True, timeout=15)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or '后端模块导入失败')


class ChangeDetector:
    def __init__(self, current, settle=2):
        self.current = current
        self.candidate = current
        self.changed_at = None
        self.settle = settle

    def ready(self, observed, clock):
        if observed == self.current:
            self.candidate = observed
            self.changed_at = None
            return False
        if observed != self.candidate:
            self.candidate = observed
            self.changed_at = clock
            return False
        return self.changed_at is not None and clock - self.changed_at >= self.settle

    def acknowledge(self, observed):
        self.current = observed
        self.candidate = observed
        self.changed_at = None


def stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(root, interval=0.5, settle=2, probe=True):
    running = True
    child = None
    def shutdown(*_):
        nonlocal running
        running = False
    previous = {sig: signal.signal(sig, shutdown) for sig in (signal.SIGTERM, signal.SIGINT)}
    def log(message):
        print('[工作管理树自动重载] ' + message, flush=True)
    def launch():
        # All runtime settings (PORT, WMT_DB, auth and model configuration)
        # come from the existing service environment without being printed.
        env = dict(os.environ, WMT_RELOAD_CHILD='1')
        return subprocess.Popen([sys.executable, '-B', '-u', str(root / 'app.py')], cwd=root, env=env)
    try:
        current = sources(root)
        validate(root, current, probe)
        detector = ChangeDetector(current, settle)
        child = launch()
        reported_exit = False
        log('已启用；只监听后端 Python 源码，前端文件刷新页面即可生效。')
        while running:
            time.sleep(interval)
            try:
                observed = sources(root)
            except OSError as exc:
                log('源码读取暂未完成，保留当前服务：' + str(exc))
                continue
            if detector.ready(observed, time.monotonic()):
                try:
                    validate(root, observed, probe)
                except (OSError, SyntaxError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    log('检查未通过，保留当前服务：' + str(exc))
                    detector.acknowledge(observed)
                    continue
                # Never reload a version that changed during its import probe.
                if sources(root) != observed:
                    continue
                if not running:
                    break
                log('后端源码检查通过，正在重载。')
                stop(child)
                child = launch()
                reported_exit = False
                detector.acknowledge(observed)
            elif child.poll() is not None and not reported_exit:
                log('后端进程已退出，继续监听源码；修正代码后会自动重新启动。')
                reported_exit = True
        return 0
    finally:
        stop(child)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--settle', type=float, default=2)
    parser.add_argument('--check', action='store_true', help='仅检查源码与导入，不启动服务')
    args = parser.parse_args()
    if args.interval <= 0 or args.settle < 0:
        parser.error('interval 必须大于 0，settle 不能小于 0')
    root = args.root.resolve()
    if args.check:
        validate(root, sources(root))
        print('后端源码语法与导入检查通过。')
    else:
        return run(root, args.interval, args.settle)


if __name__ == '__main__':
    raise SystemExit(main())
