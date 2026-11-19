# The gate selftests intentionally use unittest without pytest plugins.
# ruff: noqa: PT009, PT027
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mutants_gate import (
    CRITICAL,
    GateError,
    confirm_timeouts,
    parse_changes,
    qualified_survivors,
    read_results,
    run_command,
    select,
    verify_trial_results,
)


class SelectionTests(unittest.TestCase):
    def test_union_test_deletion_and_other_production(self):
        changes = parse_changes(b'D\0tests/test_release.py\0M\0src/buildlib/targets.py\0')
        self.assertEqual(select(changes), CRITICAL | {'src/buildlib/targets.py'})

    def test_renamed_production_and_docs(self):
        self.assertEqual(parse_changes(b'R100\0src/buildlib/a.py\0src/buildlib/b.py\0'),
                         {'src/buildlib/a.py': 'D', 'src/buildlib/b.py': 'A'})
        self.assertEqual(select({'docs/a.md': 'M'}), set())
        with self.assertRaises(GateError):
            select({'unknown.file': 'M'})
        self.assertEqual(select({'src/buildlib/old.py': 'D'}), CRITICAL)
        with self.assertRaises(GateError):
            select({'src/buildlib/elf.py': 'U'})

    def test_malformed_git(self):
        for data in (b'M\0', b'M\0x', b'R100\0x\0', b'Z\0x\0'):
            with self.subTest(data=data), self.assertRaises(GateError):
                parse_changes(data)


class ResultsTests(unittest.TestCase):
    def test_identity_and_result_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            meta = Path(directory) / 'a.meta'
            spans = Path(directory) / 'a.spans'
            spans.write_text(json.dumps({'version': 1, 'spans': {'x_f__mutmut_orig': [1, 2], 'x_f__mutmut_1': [3, 4]}}))
            meta.write_text(json.dumps({'exit_code_by_key': {'buildlib.a.x_f__mutmut_1': 1}}))
            self.assertEqual(read_results(meta, spans, 'buildlib.a.'), {'buildlib.a.x_f__mutmut_1': 'killed'})
            for result in (None, 3, 5, 34):
                meta.write_text(json.dumps({'exit_code_by_key': {'buildlib.a.x_f__mutmut_1': result}}))
                with self.subTest(result=result), self.assertRaises(GateError):
                    read_results(meta, spans, 'buildlib.a.')
            meta.write_text(json.dumps({'exit_code_by_key': {'buildlib.a.x_other__mutmut_1': 1}}))
            with self.assertRaises(GateError):
                read_results(meta, spans, 'buildlib.a.')
            meta.write_text(json.dumps({'exit_code_by_key': {'buildlib.a.x_f__mutmut_1': 36}}))
            self.assertEqual(read_results(meta, spans, 'buildlib.a.'), {'buildlib.a.x_f__mutmut_1': 'timeout'})
            meta.write_text(json.dumps({'exit_code_by_key': {'buildlib.a.x_f__mutmut_1': None}}))
            self.assertEqual(read_results(meta, spans, 'buildlib.a.', frozenset({'buildlib.a.x_f__mutmut_1'})),
                             {'buildlib.a.x_f__mutmut_1': 'pending'})
            with self.assertRaises(GateError):
                read_results(meta, spans, 'buildlib.a.')
            meta.write_text('{"exit_code_by_key": {}, "exit_code_by_key": {}}')
            with self.assertRaises(GateError):
                read_results(meta, spans, 'buildlib.a.')
            meta.write_text(json.dumps({'exit_code_by_key': {'buildlib.a.x_f__mutmut_1': 1}}))
            spans.write_text(json.dumps({'version': 1, 'spans': {}}))
            with self.assertRaises(GateError):
                read_results(meta, spans, 'buildlib.a.')
            spans.write_text('not json')
            with self.assertRaises(GateError):
                read_results(meta, spans, 'buildlib.a.')
            spans.write_text(json.dumps({'version': 1, 'spans': {'x_f__mutmut_1': [3, 4]}}))
            meta.unlink()
            with self.assertRaises(GateError):
                read_results(meta, spans, 'buildlib.a.')

    def test_qualified_survivor_requires_same_source_and_mutant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            mutant = root / 'mutant'
            spans = root / 'spans'
            source.write_bytes(b'original')
            mutant.write_bytes(b'changed mutant\n')
            name = 'buildlib.a.x_f__mutmut_1'
            spans.write_text(json.dumps({'spans': {'x_f__mutmut_1': [1, 1]}}))
            record = {'source_sha256': hashlib.sha256(b'original').hexdigest(),
                      'survivors': [name + '=' + hashlib.sha256(b'changed mutant\n').hexdigest()]}
            self.assertEqual(qualified_survivors(source, mutant, spans, 'buildlib.a.', record), {name})
            mutant.write_bytes(b'different mutant\n')
            self.assertEqual(qualified_survivors(source, mutant, spans, 'buildlib.a.', record), set())
            source.write_bytes(b'different source')
            self.assertEqual(qualified_survivors(source, mutant, spans, 'buildlib.a.', record), set())

    def test_trial_requires_regular_receipts_and_current_mutation_bodies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'buildsys' / 'probe.py'
            generated = root / 'mutants' / 'buildsys' / 'probe.py'
            meta = generated.with_suffix('.py.meta')
            spans = generated.with_suffix('.py.spans')
            source.parent.mkdir()
            generated.parent.mkdir(parents=True)
            source.write_bytes(b'original')
            generated.write_bytes(b'changed mutant\n')
            name = 'buildsys.probe.x_f__mutmut_1'
            meta.write_text(json.dumps({'exit_code_by_key': {name: 0}}))
            spans.write_text(json.dumps({'version': 1, 'spans': {'x_f__mutmut_1': [1, 1]}}))
            record = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                      'survivors': [name + '=' + hashlib.sha256(generated.read_bytes()).hexdigest()]}
            args = (root, 'buildsys/probe.py', 'buildsys.probe.', record)
            self.assertEqual(verify_trial_results(*args), {'survived': 1})
            with self.assertRaisesRegex(GateError, 'relative to the checkout'):
                verify_trial_results(root, str(source), 'buildsys.probe.', record)
            generated.write_bytes(b'altered mutant\n')
            with self.assertRaisesRegex(GateError, 'survived: buildsys.probe.x_f__mutmut_1'):
                verify_trial_results(*args)
            meta.rename(root / 'copied.meta')
            meta.symlink_to(root / 'copied.meta')
            with self.assertRaisesRegex(GateError, 'expected a regular file'):
                verify_trial_results(*args)

    def test_timeouts_require_fresh_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta, spans = root / 'a.meta', root / 'a.spans'
            first, second = 'buildlib.a.x_f__mutmut_1', 'buildlib.a.x_f__mutmut_2'
            spans.write_text(json.dumps({'version': 1, 'spans': {'x_f__mutmut_1': [1, 1], 'x_f__mutmut_2': [2, 2]}}))
            meta.write_text(json.dumps({'exit_code_by_key': {first: 36, second: 36}}))
            results = {first: 'timeout', second: 'timeout'}

            def rerun(args, _directory, _deadline):
                data = json.loads(meta.read_text())
                name = args[-1]
                self.assertIsNone(data['exit_code_by_key'][name])
                data['exit_code_by_key'][name] = 1 if name == first else 36
                meta.write_text(json.dumps(data))

            with patch('mutants_gate.run_command', side_effect=rerun):
                confirm_timeouts(root, meta, spans, 'buildlib.a.', results, time.monotonic() + 60)
            self.assertEqual(results, {first: 'killed', second: 'confirmed hang'})
            meta.write_text(json.dumps({'exit_code_by_key': {first: 36, second: 1}}))
            with patch('mutants_gate.run_command'), self.assertRaises(GateError):
                confirm_timeouts(root, meta, spans, 'buildlib.a.', {first: 'timeout'}, time.monotonic() + 60)

    def test_budget_exhaustion_fails(self):
        with self.assertRaisesRegex(GateError, 'budget exhausted'):
            run_command(['unused'], Path('.'), time.monotonic() - 1)


if __name__ == '__main__':
    unittest.main()
