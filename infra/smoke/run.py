"""Build/run a uniquely named disposable stack; never reads local .env or real CSV/LLM keys."""
from pathlib import Path
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def main():
    output = ROOT / '.local'
    output.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='smoke-', dir=output))
    project = 'bi-smoke-' + secrets.token_hex(5)
    # --env-file prevents reading repository .env; strip interpolation overrides as well.
    env = os.environ.copy()
    settings = {line.split('=', 1)[0] for line in (ROOT / '.env.example').read_text().splitlines()
                if '=' in line and not line.startswith('#')}
    for key in list(env):
        if key in settings or key.startswith(('COMPOSE_', 'LLM_', 'AGENT_', 'MATERIALS_', 'BACKEND_')):
            env.pop(key)
    env.update(POSTGRES_PASSWORD=secrets.token_hex(24), BACKEND_DEMO_TOKEN=secrets.token_hex(24),
               DATA_DIR=str(run / 'csv'), BACKEND_DEMO_PERSON_ID='F_1')
    empty_env = run / 'empty.env'
    base_compose = ['docker', 'compose', '--env-file', str(empty_env), '-p', project,
                    '-f', str(ROOT / 'compose.yaml')]
    compose = base_compose + ['-f', str(ROOT / 'infra/compose.smoke.yaml')]
    log = run / 'commands.log'

    def command(args, *, ok=True):
        result = subprocess.run(args, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with log.open('a') as stream:
            stream.write('\n$ ' + ' '.join(args) + '\n' + result.stdout)
        if ok and result.returncode:
            raise RuntimeError(f'Command failed ({result.returncode}); inspect {log}')
        return result

    def dc(*args, **kw):
        return command(compose + list(args), **kw)

    report = {'mode': 'synthetic-scripted-ML-no-live-calls', 'project': project, 'checks': []}
    try:
        command([sys.executable, str(ROOT / 'infra/smoke/prepare.py'), '--output-dir', str(run)])
        empty_env.touch(mode=0o600)
        dc('config', '--quiet')
        report['checks'].append('compose-config')
        print(f'Building disposable stack {project}; log: {log}', flush=True)
        dc('build')
        report['checks'].append('build-all-images')
        # Exercise the real materials CLI/overlay using only a new saved synthetic record.
        for folder in ('materials', 'pdf', 'review'):
            (run / folder).mkdir()
        env.update(MATERIALS_STORE=str(run / 'materials'), PDF_DIR=str(run / 'pdf'),
                   MATERIALS_REVIEW_DIR=str(run / 'review'),
                   LOCAL_UID=str(os.getuid()), LOCAL_GID=str(os.getgid()))
        (run / 'review/news.jsonl').write_text(json.dumps({
            'url': 'https://www.cbr.ru/press/synthetic-smoke-only',
            'title': '[SYNTHETIC SMOKE] Rate fixture', 'text': 'Synthetic rate test, not a real news item.',
            'published_at': '2026-09-11T00:00:00Z', 'retrieved_at': '2026-09-11T00:00:00Z'
        }) + '\n')
        material = base_compose + ['-f', str(ROOT / 'infra/compose.materials.yaml')]
        command(material + ['run', '--rm', '--no-deps', '--build', 'materials', 'news',
                            '--store', '/app/materials', '--saved', '/review/news.jsonl'])
        pointer = (run / 'materials/current.json').read_bytes()
        command(material + ['run', '--rm', '--no-deps', 'materials', 'news',
                            '--store', '/app/materials', '--saved', '/review/news.jsonl'])
        assert pointer == (run / 'materials/current.json').read_bytes()
        command(material + ['run', '--rm', '--no-deps', 'materials', 'check', '--store', '/app/materials'])
        command(material + ['run', '--rm', '--no-deps', 'ml', 'python', '-c',
            'from insight_agent.api import build_agent; from insight_agent.config import Settings; '
            'a=build_agent(Settings()); assert a.corpus_snapshot.revision and len(a.news.events)==1; '
            'print("PASS: production ML loads prepared synthetic snapshot without LLM calls")'])
        report['checks'].append('material-job-repeat-check-production-ml-load')
        print('Starting PostgreSQL, importer, ML, backend and frontend…', flush=True)
        dc('up', '-d', '--wait', '--wait-timeout', '240')
        address = dc('port', 'frontend', '80').stdout.strip()
        base = 'http://' + address
        report['url'] = base
        # Run checker inside ML container for private ML safety marker + public gateway HTTP.
        # Copy only generated synthetic event fixture, no credentials or real data.
        dc('cp', str(run / 'backend-event.json'), 'ml:/tmp/backend-event.json')
        dc('cp', str(run / 'synthetic-smoke.json'), 'ml:/tmp/synthetic-smoke.json')
        checker = dc('exec', '-T', '-e', 'BACKEND_DEMO_TOKEN', 'ml', 'python', '/app/smoke/check.py', '--base-url', 'http://frontend',
                     '--ml-url', 'http://127.0.0.1:8000', '--backend-url', 'http://backend:8080', '--fixtures-dir', '/tmp')
        report['checks'].append('http-end-to-end-scripted-ml')
        command([sys.executable, str(ROOT / 'infra/smoke/check_gateway.py'), base])
        report['checks'].append('gateway-security-boundary')
        print(checker.stdout.strip(), flush=True)

        # Identity import must be a no-op and altered inputs must not replace current data.
        repeated = dc('run', '--rm', '--no-deps', 'import-data')
        assert "'status': 'unchanged'" in repeated.stdout, repeated.stdout[-300:]
        report['checks'].append('identical-import-unchanged')
        csv = next((run / 'csv').glob('*.csv'))
        original = csv.read_bytes()
        csv.write_bytes(original + b'\n')
        try:
            refused = dc('run', '--rm', '--no-deps', 'import-data', ok=False)
            assert refused.returncode and 'Different dataset already loaded' in refused.stdout
        finally:
            csv.write_bytes(original)
        report['checks'].append('changed-import-refused')

        tables = ['operation', 'event', 'insight_state', 'feedback', 'action_intent']
        digest_sql = ' UNION ALL '.join(
            f"SELECT '{table}', md5(string_agg(to_jsonb(t)::text, '' ORDER BY to_jsonb(t)::text)) FROM backend_api.{table} t"
            for table in tables)
        query = ['exec', '-T', 'postgres', 'psql', '-U', 'business_insights', '-d', 'business_insights', '-Atc',
                 digest_sql]
        before = dc(*query).stdout.strip()
        dc('restart', 'postgres', 'backend', 'ml')
        dc('up', '-d', '--wait', '--wait-timeout', '240')
        after = dc(*query).stdout.strip()
        assert before and before == after
        # nginx must resolve recreated/restarted services and frontend remains available.
        with urlopen(base + '/api/v1/companies?limit=1', timeout=15) as response:
            assert response.status == 200 and json.load(response)
        report['checks'].append('restart-preserves-results-and-proxy')
        report['status'] = 'passed'
        print('PASS: isolated full stack, repeat import, changed-data refusal, restart persistence. No live LLM.', flush=True)
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        dc('logs', '--no-color', '--tail', '100', ok=False)
        raise
    finally:
        # Names are generated here, never accepted from user/env. Delete only this smoke project.
        dc('down', '--volumes', '--remove-orphans', ok=False)
        (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(f'Smoke report: {run / "report.json"}', flush=True)


if __name__ == '__main__':
    main()
