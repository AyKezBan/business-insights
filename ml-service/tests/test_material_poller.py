"""Exercise the supervisor loop; only the external CLI process and time are faked."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def poller():
    try:
        return importlib.import_module('insight_agent.materials.poller')
    except ModuleNotFoundError:
        pytest.fail('The optional materials poller is not implemented')


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Stop:
    def __init__(self, clock, cycles=1, at=None):
        self.clock, self.cycles, self.at = clock, cycles, at
        self.delays = []
        self.stopped = False

    def is_set(self):
        return self.stopped or (self.at is not None and self.clock.now >= self.at)

    def wait(self, seconds):
        self.delays.append(seconds)
        self.clock.now += seconds
        self.stopped = len(self.delays) >= self.cycles
        return self.is_set()


class Process:
    def __init__(self, clock, code, duration=0, stubborn=False):
        self.clock, self.code = clock, code
        self.ends = clock.now + duration
        self.stubborn = stubborn
        self.terminated = self.killed = False

    def wait(self, timeout):
        if self.killed:
            return -9
        if self.terminated and not self.stubborn:
            return -15
        if self.clock.now >= self.ends:
            return self.code
        self.clock.now += min(timeout, self.ends - self.clock.now)
        if self.clock.now < self.ends:
            raise subprocess.TimeoutExpired('fixture-cli', timeout)
        return self.code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class CLI:
    def __init__(self, clock, codes, duration=0, stubborn=False):
        self.clock, self.codes = clock, iter(codes)
        self.duration, self.stubborn = duration, stubborn
        self.commands, self.children = [], []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        child = Process(self.clock, next(self.codes), self.duration, self.stubborn)
        self.children.append(child)
        return child


def run(tmp_path, codes, *, duration=0, cycles=None, at=None, stubborn=False, **config):
    module = poller()
    clock = Clock()
    stop = Stop(clock, cycles=cycles or len(codes), at=at)
    cli = CLI(clock, codes, duration, stubborn)
    result = module.run_poller(module.PollerConfig(store=tmp_path, **config), stop,
                               popen=cli, monotonic=clock)
    return result, stop, cli, json.loads((tmp_path / 'poller-status.json').read_text())


def test_sync_uses_frozen_cli_and_waits_default_interval(tmp_path):
    result, stop, cli, report = run(tmp_path, [0, 0])
    assert result == 0
    assert stop.delays == [900, 900]
    assert cli.commands == [[sys.executable, '-m', 'insight_agent.materials.cli', 'sync',
                             '--sources', 'cbr_press,fns_news,eia_today',
                             '--limit-per-source', '5', '--store', str(tmp_path)]] * 2
    assert report['exit_code'] == 0
    assert report['next_delay_seconds'] == 900
    assert report['report_path'] == str(tmp_path / 'poller-status.json')
    assert (tmp_path / '.poller.lock').exists()


def test_failed_and_review_cycles_back_off_to_cap_and_success_resets(tmp_path):
    _, stop, _, _ = run(tmp_path, [1, 2, 1, 0, 1])
    assert stop.delays == [1800, 3600, 3600, 900, 1800]


def test_partial_keeps_diagnostic_and_normal_interval(tmp_path):
    _, stop, _, report = run(tmp_path, [3])
    assert stop.delays == [900]
    assert report['exit_code'] == 3
    assert report['status'] == 'partial'


def test_stop_before_start_does_not_launch_child(tmp_path):
    module = poller()
    clock = Clock()
    stop = Stop(clock)
    stop.stopped = True
    cli = CLI(clock, [])
    assert module.run_poller(module.PollerConfig(store=tmp_path), stop, popen=cli, monotonic=clock) == 0
    assert cli.children == []


def test_stop_during_cycle_terminates_only_own_child_without_retry(tmp_path):
    result, stop, cli, report = run(tmp_path, [0], duration=500, at=2)
    assert result == 0
    assert len(cli.children) == 1
    assert cli.children[0].terminated and not cli.children[0].killed
    assert stop.delays == []
    assert report['status'] == 'stopped'


def test_timeout_kills_stubborn_child_and_backs_off(tmp_path):
    _, stop, cli, report = run(tmp_path, [0], duration=100, cycle_timeout_seconds=3,
                               shutdown_grace_seconds=2, stubborn=True)
    assert cli.children[0].terminated and cli.children[0].killed
    assert report['status'] == 'timeout'
    assert report['duration_seconds'] <= 5
    assert stop.delays == [1800]


def test_second_process_lock_prevents_overlap_and_is_reusable(tmp_path):
    module = poller()
    ready = tmp_path / 'ready'
    lock = tmp_path / '.poller.lock'
    holder = subprocess.Popen([sys.executable, '-c',
        'import fcntl, pathlib, sys; '
        'f=open(sys.argv[1], "a"); fcntl.flock(f, fcntl.LOCK_EX); '
        'pathlib.Path(sys.argv[2]).write_text("ready"); sys.stdin.read()', str(lock), str(ready)],
        stdin=subprocess.PIPE)
    try:
        import time
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        clock = Clock()
        cli = CLI(clock, [])
        assert module.run_poller(module.PollerConfig(store=tmp_path), Stop(clock),
                                 popen=cli, monotonic=clock) == 1
        assert cli.children == []
        inode = lock.stat().st_ino
    finally:
        holder.communicate(timeout=5)
    result, _, _, _ = run(tmp_path, [0])
    assert result == 0
    assert lock.stat().st_ino == inode


@pytest.mark.parametrize('config', [dict(interval_seconds=0), dict(interval_seconds=-1),
    dict(interval_seconds=float('nan')), dict(interval_seconds=float('inf')),
    dict(cycle_timeout_seconds=0), dict(shutdown_grace_seconds=-1),
    dict(max_backoff_seconds=0), dict(interval_seconds=4000)])
def test_invalid_config_is_rejected_before_child_launch(tmp_path, config):
    module = poller()
    with pytest.raises(ValueError):
        module.PollerConfig(store=tmp_path, **config)


def test_process_start_failure_is_reported_and_backed_off(tmp_path):
    module = poller()
    clock = Clock()
    stop = Stop(clock)
    def unavailable(*args, **kwargs):
        raise OSError('fixture process launch failure with sensitive detail')
    assert module.run_poller(module.PollerConfig(store=tmp_path), stop,
                             popen=unavailable, monotonic=clock) == 0
    report = json.loads((tmp_path / 'poller-status.json').read_text())
    assert report['status'] == 'launch_failed'
    assert stop.delays == [1800]
    assert 'sensitive detail' not in json.dumps(report)


@pytest.mark.parametrize('command,expected', [
    ('materials-watch', ['-f', 'compose.yaml', '-f', 'infra/compose.materials.yaml',
                         '--profile', 'jobs', 'up', '-d', '--build', '--no-deps', 'materials-poller']),
    ('materials-up', ['-f', 'compose.yaml', '-f', 'infra/compose.materials.yaml',
                      'up', '-d', '--build', '--wait', '--wait-timeout', '240']),
    ('down', ['-f', 'compose.yaml', '-f', 'infra/compose.materials.yaml', '--profile', 'jobs', 'down']),
    ('status', ['-f', 'compose.yaml', '-f', 'infra/compose.materials.yaml', '--profile', 'jobs', 'ps', '-a']),
    ('logs', ['-f', 'compose.yaml', '-f', 'infra/compose.materials.yaml', '--profile', 'jobs', 'logs', '--tail', '100']),
])
def test_stack_optional_start_and_stop_commands(tmp_path, command, expected):
    root = Path(__file__).resolve().parents[2]
    docker = tmp_path / 'docker'
    capture = tmp_path / 'arguments'
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    docker.chmod(0o755)
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'CAPTURE': str(capture)}
    completed = subprocess.run([str(root / 'stack'), command], cwd=tmp_path, env=env,
                               capture_output=True, text=True)
    assert completed.returncode == 0
    assert capture.read_text().splitlines() == ['compose', *expected]
