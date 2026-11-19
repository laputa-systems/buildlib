"""Whole-change selection and strict mutmut result accounting."""

# The gate reports its inventory and verdict on stdout.
# ruff: noqa: T201

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRITICAL = frozenset({'src/buildlib/elf.py', 'src/buildlib/macho.py',
                      'src/buildlib/locks.py', 'src/buildlib/release.py'})
GATE = frozenset({'Makefile', '.github/workflows/ci.yml', '.github/workflows/lint.yml',
                  'pyproject.toml', 'uv.lock', '.mutants-gate.toml',
                  'scripts/mutants_gate.py', 'scripts/test_mutants_gate.py', '.gitignore',
                  '.python-version', 'pytest.ini', 'tox.ini', 'setup.cfg', 'setup.py',
                  'MANIFEST.in', '.coveragerc'})


class GateError(Exception):
    pass


def parse_changes(data):
    fields = data.split(b'\0')
    if not fields or fields[-1] != b'':
        raise GateError('unterminated git diff')
    result = {}
    i = 0
    while i < len(fields) - 1:
        try:
            status = fields[i].decode('ascii')
            i += 1
            if not status or status[0] not in 'AMDTURC':
                raise ValueError('unknown status')
            if status[0] in 'RC':
                old = fields[i].decode('utf-8')
                i += 1
                result[old] = 'D' if status[0] == 'R' else 'M'
            path = fields[i].decode('utf-8')
            i += 1
            if not path:
                raise ValueError('empty path')
            result[path] = 'A' if status[0] in 'RC' else status[0]
        except (IndexError, UnicodeError, ValueError) as exc:
            raise GateError('malformed git diff') from exc
    return result


def classify(path, status):
    if (path in GATE or path.startswith('.github/') or path == 'src/buildlib/py.typed' or
            ('/' not in path and path.startswith('requirements') and path.endswith(('.in', '.txt')))):
        return 'configuration'
    if path.startswith(('tests/', 'fixtures/')):
        return 'tests or fixtures'
    if path.endswith('.md') or path == 'LICENSE':
        return 'irrelevant'
    if path.startswith('src/buildlib/') and path.endswith('.py'):
        return 'deleted production' if status == 'D' else 'production'
    return 'unclassified'


def select(changes):
    if any(status == 'U' for status in changes.values()):
        raise GateError('unmerged path')
    classes = {p: classify(p, s) for p, s in changes.items()}
    for path, kind in sorted(classes.items()):
        print(f'{kind}: {path}', flush=True)
    if 'unclassified' in classes.values():
        raise GateError('unclassified changes')
    production = {p for p, kind in classes.items() if kind == 'production'}
    if any(kind in {'configuration', 'tests or fixtures', 'deleted production'} for kind in classes.values()):
        production.update(CRITICAL)
    return production


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def load_json(path):
    return json.loads(path.read_text(), object_pairs_hook=unique_pairs)


def read_results(meta_path, spans_path, prefix, pending=frozenset()):
    try:
        meta = load_json(meta_path)
        spans = load_json(spans_path)
        if spans['version'] != 1 or not isinstance(spans['spans'], dict):
            raise ValueError('unsupported spans')
        names = {prefix + k for k in spans['spans'] if '__mutmut_' in k and not k.endswith('__mutmut_orig')}
        results = meta['exit_code_by_key']
        if not isinstance(results, dict) or not names or set(results) != names:
            raise ValueError('missing, empty, or mismatched identities')
        # pytest internal errors (3), skipped and no-test results are not kills.
        classes = {0: 'survived', 1: 'killed', 36: 'timeout', -24: 'timeout', 24: 'timeout', 152: 'timeout', 255: 'timeout'}
        if not pending <= names:
            raise ValueError('unexpected pending identity')
        if any((value is None and name not in pending) or
               (value is not None and (type(value) is not int or value not in classes))
               for name, value in results.items()):
            raise ValueError('incomplete or unsupported result')
        return {name: 'pending' if value is None else classes[value] for name, value in results.items()}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GateError(f'unreadable or incomplete result: {meta_path}: {exc}') from exc


def git_changes(base):
    try:
        common = subprocess.check_output(['git', 'merge-base', base, 'HEAD'], cwd=ROOT).strip().decode('ascii')
        data = subprocess.check_output(['git', 'diff', '--name-status', '-M', '-z', common], cwd=ROOT)
        changes = parse_changes(data)
        untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT)
        for name in untracked.split(b'\0'):
            if name:
                changes[name.decode('utf-8')] = 'A'
        return changes
    except (subprocess.CalledProcessError, UnicodeError) as exc:
        raise GateError(f'git selection failed: {exc}') from exc


def run_command(args, directory, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GateError('budget exhausted')
    env = os.environ.copy()
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env.pop('MUTANT_UNDER_TEST', None)
    env.pop('PYTHONPATH', None)
    def cancel(signum, _frame):
        raise GateError(f'interrupted by signal {signum}')

    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {sig: signal.signal(sig, cancel) for sig in signals}
    proc = None
    try:
        proc = subprocess.Popen(args, cwd=directory, env=env, start_new_session=True)
        try:
            code = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise GateError('budget exhausted') from exc
        if code:
            raise GateError(f'command failed ({code}): {args}')
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)



def write_config(directory, multiplier):
    (directory / 'pyproject.toml').write_text(
        '[tool.mutmut]\nsource_paths = ["src"]\n'
        'pytest_add_cli_args_test_selection = ["tests"]\n'
        f'timeout_constant = {5 * multiplier}.0\n'
        f'timeout_multiplier = {15 * multiplier}.0\n'
        '[tool.pytest.ini_options]\npythonpath = ["tests"]\n'
    )


def qualified_survivors(path, mutant_path, spans_path, prefix, record):
    allowed = set()
    if hashlib.sha256(path.read_bytes()).hexdigest() != record.get('source_sha256'):
        return allowed
    expected = dict(item.rsplit('=', 1) for item in record.get('survivors', []))
    indexes = load_json(spans_path)['spans']
    mutant_source = mutant_path.read_bytes().splitlines(keepends=True)
    for name, digest in expected.items():
        function = name.removeprefix(prefix)
        if function not in indexes:
            raise GateError(f'qualified mutant missing: {name}')
        start, end = indexes[function]
        if not (type(start) is int and type(end) is int and 1 <= start <= end <= len(mutant_source)):
            raise GateError(f'invalid mutant span: {name}')
        actual = hashlib.sha256(b''.join(mutant_source[start - 1:end])).hexdigest()
        if actual == digest:
            allowed.add(name)
    return allowed


def verify_trial_results(
    checkout: Path, source: str, prefix: str, record: Mapping[str, object],
) -> dict[str, int]:
    if Path(source).is_absolute():
        raise GateError(f'expected a source path relative to the checkout: {source}')
    root = checkout.resolve(strict=True)
    paths = [root / source, *(root / 'mutants' / (source + suffix) for suffix in ('', '.meta', '.spans'))]
    for path in paths:
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise GateError(f'expected a regular file in the checkout: {path}')
    original, generated, meta, spans = paths
    outcomes = read_results(meta, spans, prefix)
    allowed = qualified_survivors(original, generated, spans, prefix, record)
    unqualified = [
        f'{outcome}: {name}'
        for name, outcome in sorted(outcomes.items())
        if outcome != 'killed' and not (outcome == 'survived' and name in allowed)
    ]
    counts = dict(sorted(Counter(outcomes.values()).items()))
    if unqualified:
        raise GateError('unqualified mutation outcomes:\n' + '\n'.join(unqualified) + f'\ncounts: {counts}')
    return counts


def confirm_timeouts(directory, meta, spans, prefix, results, deadline):
    timed_out = [name for name, outcome in results.items() if outcome == 'timeout']
    if timed_out:
        write_config(directory, 3)
    for index, name in enumerate(timed_out):
        data = load_json(meta)
        data['exit_code_by_key'][name] = None
        meta.write_text(json.dumps(data))
        run_command([sys.executable, '-m', 'mutmut', 'run', '--max-children', '1', name], directory, deadline)
        retry = read_results(meta, spans, prefix, frozenset(timed_out[index + 1:]))[name]
        results[name] = 'confirmed hang' if retry == 'timeout' else retry
        print(f'timeout confirmation: {name}: {results[name]}', flush=True)


def run_module(source, config, deadline):
    path = ROOT / source
    if path.is_symlink() or not path.is_file():
        raise GateError(f'missing or unsafe production source: {source}')
    module = path.stem
    if not module.isidentifier() or module == '__init__':
        raise GateError(f'cannot establish selection for {source}')
    scratch = ROOT / '.work/tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mutants-gate-', dir=scratch) as temp:
        directory = Path(temp)
        for tree in ('src', 'tests'):
            for item in (ROOT / tree).rglob('*'):
                if item.is_symlink():
                    raise GateError(f'symlink in {tree}: {item}')
            shutil.copytree(ROOT / tree, directory / tree,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        write_config(directory, 1)
        prefix = f'buildlib.{module}.'
        run_command([sys.executable, '-m', 'mutmut', 'run', '--max-children', '1', prefix + '*'], directory, deadline)
        meta = directory / 'mutants' / (source + '.meta')
        spans = directory / 'mutants' / (source + '.spans')
        results = read_results(meta, spans, prefix)
        print(f'source sha256: {hashlib.sha256(path.read_bytes()).hexdigest()}', flush=True)
        print(f'inventory {source}: {len(results)}', flush=True)
        for name, outcome in sorted(results.items()):
            print(f'{outcome}: {name}', flush=True)
        confirm_timeouts(directory, meta, spans, prefix, results, deadline)
        record = config.get('qualified', {}).get(module, {})
        allowed = qualified_survivors(path, directory / 'mutants' / source, spans, prefix, record)
        missed = [name for name, outcome in results.items() if outcome == 'survived' and name not in allowed]
        print('counts: ' + ', '.join(f'{kind}={list(results.values()).count(kind)}' for kind in
              ('killed', 'survived', 'timeout', 'confirmed hang')), flush=True)
        if missed:
            raise GateError('new surviving mutants: ' + ', '.join(missed))


def main():
    if len(sys.argv) != 3:
        raise GateError('usage: mutants_gate.py BASE SHARD')
    shard = sys.argv[2]
    shards = {'elf', 'macho', 'locks', 'release', 'other'}
    if shard not in shards:
        raise GateError('unknown shard')
    version = importlib.metadata.version('mutmut')
    if version != '3.8.1':
        raise GateError(f'unsupported mutmut version: {version}')
    config_path = ROOT / '.mutants-gate.toml'
    config_bytes = config_path.read_bytes()
    config = tomllib.loads(config_bytes.decode('utf-8'))
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    print(f'base: {sys.argv[1]}; head: {head}; host: {platform.platform()}; mutmut: {version}; '
          f'python: {sys.version}; config sha256: {hashlib.sha256(config_bytes).hexdigest()}', flush=True)
    budget = config['budget_seconds']
    if type(budget) is not int or not 1 <= budget <= 1200:
        raise GateError('invalid budget')
    deadline = time.monotonic() + budget
    selected = select(git_changes(sys.argv[1]))
    if not selected:
        print('irrelevant change: no mutants selected')
        return
    print('selected union:', ', '.join(sorted(selected)), flush=True)
    own = [p for p in sorted(selected) if (Path(p).stem if p in CRITICAL else 'other') == shard]
    if not own:
        print('shard has no selected production files')
        return
    for source in own:
        run_module(source, config, deadline)


if __name__ == '__main__':
    try:
        main()
    except (GateError, OSError) as exc:
        print(f'gate failure: {exc}', file=sys.stderr)
        sys.exit(1)
