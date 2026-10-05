import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('baseline', Path(__file__).resolve().parents[1] / 'scripts/baseline.py')
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / 'source'
        self.source.mkdir()
        def git(*args):
            return baseline.command(['git', '-C', str(self.source), *args])
        self.git = git
        git('init', '-q')
        (self.source / 'license').write_text('preserved\n')
        git('add', 'license')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
        self.lock = {'commit': git('rev-parse', 'HEAD'), 'files_sha256': {'license': hashlib.sha256(b'preserved\n').hexdigest()}}

    def test_exact_clean_source(self):
        baseline.verify_source(self.source, self.lock)

    def test_wrong_revision_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            baseline.verify_source(self.source, dict(self.lock, commit='0' * 40))

    def test_modified_license_preserved_and_rejected(self):
        (self.source / 'license').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'dirty'):
            baseline.verify_source(self.source, self.lock)
        self.assertEqual((self.source / 'license').read_text(), 'changed')

    def test_untracked_source_rejected(self):
        (self.source / 'extra').write_text('keep me')
        with self.assertRaisesRegex(ValueError, 'dirty'):
            baseline.verify_source(self.source, self.lock)
        self.assertTrue((self.source / 'extra').exists())

    def test_wrong_recorded_hash_rejected(self):
        with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
            baseline.verify_source(self.source, dict(self.lock, files_sha256={'license': '0' * 64}))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / 'evidence'
        self.environment = {'tools': {'cargo': {'path': '/cargo'}}, 'packages': {}, 'free_disk_bytes': 30 * 1024**3}

    def run_build(self):
        with patch.object(baseline, 'verify_source'), patch.object(baseline, 'inspect_environment', return_value=self.environment), \
                patch.object(baseline.shutil, 'disk_usage', return_value=type('Disk', (), {'free': self.environment['free_disk_bytes']})()):
            result = baseline.build(Path('/fixture'), self.output, 2, 30)
        return result, json.loads((self.output / 'build.json').read_text())

    def test_missing_dependencies_block_without_starting_build(self):
        self.environment['packages']['alsa'] = None
        with patch.object(baseline, 'run_bounded') as run:
            code, record = self.run_build()
        run.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(record['baseline_build'], 'blocked')
        self.assertEqual(record['editor_smoke'], 'not_run')

    def test_low_disk_preserves_work(self):
        self.environment['free_disk_bytes'] = 1
        with patch.object(baseline, 'run_bounded') as run:
            _, record = self.run_build()
        run.assert_not_called()
        self.assertEqual(record['baseline_build'], 'blocked')

    def test_build_failure_is_not_smoke_success(self):
        with patch.object(baseline, 'run_bounded', return_value=17):
            code, record = self.run_build()
        self.assertEqual(code, 1)
        self.assertEqual(record['exit_code'], 17)
        self.assertEqual(record['baseline_build'], 'failed')
        self.assertEqual(record['editor_smoke'], 'not_run')
        self.assertEqual(record['same_window_island'], 'not_implemented')

    def test_timeout_recorded(self):
        with patch.object(baseline, 'run_bounded', side_effect=TimeoutError('build exceeded 30 seconds')):
            code, record = self.run_build()
        self.assertEqual(code, 2)
        self.assertEqual(record['baseline_build'], 'failed')
        self.assertIn('exceeded', record['error'])

    def test_evidence_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / 'build.json'
        sentinel.write_text('old evidence')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_build()
        self.assertEqual(sentinel.read_text(), 'old evidence')


if __name__ == '__main__':
    unittest.main()
