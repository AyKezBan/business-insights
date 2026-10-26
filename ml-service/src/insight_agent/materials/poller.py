"""Optional process supervisor for the existing materials sync CLI; no ingestion here."""
import argparse
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time


@dataclass(frozen=True)
class PollerConfig:
    store: Path
    interval_seconds: float = 900
    max_backoff_seconds: float = 3600
    cycle_timeout_seconds: float = 600
    shutdown_grace_seconds: float = 10

    def __post_init__(self):
        object.__setattr__(self, 'store', Path(self.store))
        for value in (self.interval_seconds, self.max_backoff_seconds,
                      self.cycle_timeout_seconds, self.shutdown_grace_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Poller timing values must be finite and positive')
        if not self.interval_seconds <= self.max_backoff_seconds <= 3600:
            raise ValueError('Backoff must cover the interval and be at most 3600 seconds')


def _terminate(child, grace):
    """Reap only the child we created, escalating after a bounded grace period."""
    try:
        child.terminate()
    except ProcessLookupError:
        pass
    try:
        return child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        child.kill()
        return child.wait(timeout=grace)


def _cycle(config, stop, popen, monotonic):
    started = monotonic()
    command = [sys.executable, '-m', 'insight_agent.materials.cli', 'sync',
               '--sources', 'cbr_press,fns_news,eia_today', '--limit-per-source', '5',
               '--store', str(config.store)]
    try:
        # Inherit only the CLI's metadata output; the supervisor never reads articles.
        child = popen(command, close_fds=True)
    except OSError:
        return 1, 'launch_failed', monotonic() - started
    try:
        while True:
            if stop.is_set():
                code = _terminate(child, config.shutdown_grace_seconds)
                return code, 'stopped', monotonic() - started
            remaining = config.cycle_timeout_seconds - (monotonic() - started)
            if remaining <= 0:
                _terminate(child, config.shutdown_grace_seconds)
                return 124, 'timeout', monotonic() - started
            try:
                code = child.wait(timeout=min(1, remaining))
            except subprocess.TimeoutExpired:
                continue
            status = {0: 'healthy', 2: 'needs_review', 3: 'partial'}.get(code, 'failed')
            return code, status, monotonic() - started
    except BaseException:
        _terminate(child, config.shutdown_grace_seconds)
        raise


def _record(config, report):
    path = config.store / 'poller-status.json'
    report['report_path'] = str(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=config.store,
                                         prefix='.poller-status-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(json.dumps(report, sort_keys=True), flush=True)


def run_poller(config, stop=None, *, popen=subprocess.Popen, monotonic=time.monotonic):
    """Run until stopped, keeping a separate nonblocking lock for the process lifetime."""
    stop = stop if stop is not None else threading.Event()
    config.store.mkdir(parents=True, exist_ok=True)
    # Never unlink this file: competing processes must always lock the same inode.
    with (config.store / '.poller.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status': 'lock_busy', 'exit_code': 1,
                              'report_path': str(config.store / 'poller-status.json')}), flush=True)
            return 1
        failure_delay = config.interval_seconds
        try:
            while not stop.is_set():
                code, status, duration = _cycle(config, stop, popen, monotonic)
                if code == 0:
                    failure_delay = config.interval_seconds
                elif status != 'stopped' and code != 3:
                    failure_delay = min(config.max_backoff_seconds, failure_delay * 2)
                delay = config.interval_seconds if code in (0, 3) else failure_delay
                if stop.is_set():
                    delay = 0
                _record(config, {'status': status, 'exit_code': code,
                                 'duration_seconds': round(duration, 3),
                                 'next_delay_seconds': delay})
                if stop.is_set() or stop.wait(delay):
                    break
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--interval-seconds', type=float, default=900)
    parser.add_argument('--max-backoff-seconds', type=float, default=3600)
    parser.add_argument('--cycle-timeout-seconds', type=float, default=600)
    parser.add_argument('--shutdown-grace-seconds', type=float, default=10)
    args = parser.parse_args(argv)
    try:
        config = PollerConfig(**vars(args))
    except ValueError as exc:
        parser.error(str(exc))
    stop = threading.Event()
    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        return run_poller(config, stop)
    except OSError:
        # Paths, external error strings and environment values are not logged.
        print(json.dumps({'status': 'poller_io_failed', 'exit_code': 1}), flush=True)
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    raise SystemExit(main())
