import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('baseline', SCRIPTS / 'baseline.py')
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)
BUILD_SPEC = importlib.util.spec_from_file_location('build_island', SCRIPTS / 'build_island.py')
build_island = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(build_island)


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

    def git_checkout(self, path):
        path.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
        return path

    def git_root_for(self, executable):
        root = subprocess.check_output(['git', '-C', str(executable.parent),
                                        'rev-parse', '--show-toplevel'], text=True).strip()
        return Path(root).resolve()

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
        with patch.object(baseline, 'run_bounded', return_value=17) as run:
            code, record = self.run_build()
        self.assertEqual(run.call_args.args[2]['CARGO_TARGET_DIR'], '/fixture/target/mzed-baseline')
        self.assertEqual(run.call_args.args[5], Path('/fixture'))
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

    def test_existing_baseline_target_requires_explicit_resume(self):
        source = Path(self.directory.name) / 'source'
        target = source / 'target/mzed-baseline'
        target.mkdir(parents=True)
        env = dict(self.environment, system_name='Linux')
        with patch.object(baseline, 'verify_source'), patch.object(baseline, 'inspect_environment', return_value=env), \
                patch.object(baseline.shutil, 'disk_usage', return_value=type('Disk', (), {'free': env['free_disk_bytes']})()), \
                patch.object(baseline, 'run_bounded') as run:
            code = baseline.build(source, self.output, 2, 30)
        run.assert_not_called()
        record = json.loads((self.output / 'build.json').read_text())
        self.assertEqual(code, 2)
        self.assertEqual(record['baseline_build'], 'blocked')
        self.assertFalse(record.get('target_reused', False))

    def test_resume_reuses_only_existing_target_under_checkout(self):
        source = Path(self.directory.name) / 'source'
        target = source / 'target/mzed-baseline'
        target.mkdir(parents=True)
        env = dict(self.environment, system_name='Linux')
        with patch.object(baseline, 'verify_source'), patch.object(baseline, 'inspect_environment', return_value=env), \
                patch.object(baseline.shutil, 'disk_usage', return_value=type('Disk', (), {'free': env['free_disk_bytes']})()), \
                patch.object(baseline, 'run_bounded', return_value=17) as run:
            code = baseline.build(source, self.output, 2, 30, resume_target=True)
        self.assertEqual(code, 1)
        self.assertEqual(run.call_args.args[2]['CARGO_TARGET_DIR'], str(target))
        record = json.loads((self.output / 'build.json').read_text())
        self.assertEqual(record['target_reused'], True)

    def test_passed_baseline_build_preserves_executable_outside_reused_target(self):
        source = self.git_checkout(Path(self.directory.name) / 'ordinary-worktree' / '_build' / 'zed')
        target_binary = source / 'target/mzed-baseline/debug/zed'
        env = dict(self.environment, system_name='Linux')
        def build_fixture(_argv, build_source, _env, _log, _timeout, _disk_path):
            binary = build_source / 'target/mzed-baseline/debug/zed'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'pinned baseline executable')
            return 0
        with patch.object(baseline, 'verify_source'), patch.object(baseline, 'inspect_environment', return_value=env), \
                patch.object(baseline.shutil, 'disk_usage', return_value=type('Disk', (), {'free': env['free_disk_bytes']})()), \
                patch.object(baseline, 'run_bounded', side_effect=build_fixture):
            code = baseline.build(source, self.output, 2, 30)
        record = json.loads((self.output / 'build.json').read_text())
        self.assertEqual(code, 0)
        self.assertEqual(record['target_binary'], str(target_binary))
        preserved = Path(record['binary'])
        self.assertEqual(preserved, source / 'target/mzed-baseline-preserved/debug/zed')
        self.assertFalse(preserved.is_relative_to(target_binary.parents[1]))
        self.assertEqual(self.git_root_for(preserved), source.resolve())
        self.assertEqual(preserved.read_bytes(), b'pinned baseline executable')

    def test_existing_passed_baseline_binary_can_be_preserved(self):
        source = self.git_checkout(Path(self.directory.name) / 'external-volume' / 'Developer' / 'zed')
        original = source / 'target/mzed-baseline/debug/zed'
        original.parent.mkdir(parents=True)
        original.write_bytes(b'existing pinned baseline executable')
        target = source / 'target/mzed-baseline'
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        record = {'baseline_build': 'passed', 'target_directory': str(target),
                  'binary': str(original), 'binary_sha256': digest}
        self.output.mkdir()
        (self.output / 'build.json').write_text(json.dumps(record))
        with patch.object(baseline, 'verify_source'):
            preserved = baseline.preserve_existing_baseline_binary(source, self.output)
        self.assertEqual(preserved['target_binary'], str(original))
        runnable = Path(preserved['binary'])
        self.assertEqual(runnable, source / 'target/mzed-baseline-preserved/debug/zed')
        self.assertEqual(self.git_root_for(runnable), source.resolve())
        self.assertEqual(hashlib.sha256(runnable.read_bytes()).hexdigest(), digest)

    def test_matching_preserved_binary_is_idempotent(self):
        source = self.git_checkout(Path(self.directory.name) / 'source')
        original = source / 'target/mzed-baseline/debug/zed'
        original.parent.mkdir(parents=True)
        original.write_bytes(b'pinned baseline executable')
        target = source / 'target/mzed-baseline'
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        record = {'baseline_build': 'passed', 'target_directory': str(target),
                  'binary': str(original), 'binary_sha256': digest}
        preserved_path = source / 'target/mzed-baseline-preserved/debug/zed'
        preserved_path.parent.mkdir(parents=True)
        preserved_path.write_bytes(original.read_bytes())
        self.output.mkdir()
        (self.output / 'build.json').write_text(json.dumps(record))
        with patch.object(baseline, 'verify_source'):
            preserved = baseline.preserve_existing_baseline_binary(source, self.output)
        self.assertEqual(preserved['binary'], str(preserved_path))
        self.assertEqual(hashlib.sha256(preserved_path.read_bytes()).hexdigest(), digest)

    def test_different_preserved_binary_is_not_overwritten(self):
        source = self.git_checkout(Path(self.directory.name) / 'source')
        original = source / 'target/mzed-baseline/debug/zed'
        original.parent.mkdir(parents=True)
        original.write_bytes(b'pinned baseline executable')
        target = source / 'target/mzed-baseline'
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        record = {'baseline_build': 'passed', 'target_directory': str(target),
                  'binary': str(original), 'binary_sha256': digest}
        preserved_path = source / 'target/mzed-baseline-preserved/debug/zed'
        preserved_path.parent.mkdir(parents=True)
        preserved_path.write_bytes(b'other evidence')
        self.output.mkdir()
        (self.output / 'build.json').write_text(json.dumps(record))
        with patch.object(baseline, 'verify_source'):
            with self.assertRaisesRegex(ValueError, 'differs'):
                baseline.preserve_existing_baseline_binary(source, self.output)
        self.assertEqual(preserved_path.read_bytes(), b'other evidence')

    def test_symlinked_preserved_baseline_is_rejected(self):
        source = self.git_checkout(Path(self.directory.name) / 'source')
        original = source / 'target/mzed-baseline/debug/zed'
        original.parent.mkdir(parents=True)
        original.write_bytes(b'pinned baseline executable')
        preserved_path = source / 'target/mzed-baseline-preserved/debug/zed'
        preserved_path.parent.mkdir(parents=True)
        preserved_path.symlink_to(original)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        record = {'target_directory': str(source / 'target/mzed-baseline'), 'binary_sha256': digest}
        with self.assertRaisesRegex(ValueError, 'symlink'):
            baseline.preserve_baseline_binary(source, record)
        self.assertTrue(preserved_path.is_symlink())


class PlatformPrerequisiteTests(unittest.TestCase):
    def test_macos_requires_xcode_sdk_without_linux_pkg_config_libraries(self):
        environment = {
            'system_name': 'Darwin',
            'tools': {name: {'path': '/' + name} for name in
                      ['git', 'rustc', 'cargo', 'clang', 'cmake', 'xcodebuild', 'xcrun', 'xcode-select']},
            'packages': {'xcode_developer_dir': '/Applications/Xcode.app/Contents/Developer',
                         'macos_sdk': '/Applications/Xcode.app/Contents/Developer/SDKs/MacOSX.sdk',
                         'metal_toolchain': 'Apple metal version',
                         'metal_toolchain_identifier': 'com.apple.dt.toolchain.Metal.test',
                         'metal_toolchain_build_version': 'test'},
        }
        self.assertEqual(baseline.missing_prerequisites(environment), [])

    def test_macos_missing_sdk_is_reported(self):
        environment = {
            'system_name': 'Darwin',
            'tools': {'xcodebuild': {'path': '/usr/bin/xcodebuild'}},
            'packages': {'macos_sdk': None, 'metal_toolchain': None},
        }
        self.assertEqual(baseline.missing_prerequisites(environment),
                         ['xcode_developer_dir', 'macos_sdk', 'metal_toolchain'])

    def test_installed_metal_component_is_selected_and_executed(self):
        identifier = 'com.apple.dt.toolchain.Metal.test'
        with patch.object(baseline, 'command', side_effect=[
            json.dumps({'status': 'installed', 'toolchainIdentifier': identifier,
                        'buildVersion': 'test-build'}),
            'Apple metal version test',
        ]) as run:
            selected = baseline.select_metal_toolchain()
        self.assertEqual(selected, {'identifier': identifier, 'build_version': 'test-build',
                                    'version': 'Apple metal version test'})
        metal_call = run.call_args_list[1]
        self.assertEqual(metal_call.args[0], ['xcrun', 'metal', '--version'])
        self.assertEqual(metal_call.kwargs['env']['TOOLCHAINS'], identifier)

    def test_metal_component_metadata_alone_does_not_pass_preflight(self):
        with patch.object(baseline, 'command', side_effect=[
            json.dumps({'status': 'installed', 'toolchainIdentifier': 'com.apple.dt.toolchain.Metal.test'}),
            subprocess.CalledProcessError(1, 'xcrun'),
        ]):
            selected = baseline.select_metal_toolchain()
        self.assertIsNone(selected['version'])


class DerivedBuildEnvironmentTests(unittest.TestCase):
    def test_inherited_toolchains_survives_when_component_has_no_identifier(self):
        requested = 'com.apple.dt.toolchain.Metal.caller-selected'
        metal = {'identifier': None, 'build_version': None, 'version': 'Apple metal version test'}
        with patch.object(build_island.platform, 'system', return_value='Darwin'), \
                patch.dict(os.environ, {'PATH': '/usr/bin', 'TOOLCHAINS': requested}, clear=True), \
                patch.object(build_island.subprocess, 'check_output', return_value='/fixture/MacOSX.sdk'), \
                patch.object(build_island, 'select_metal_toolchain', return_value=metal):
            env = build_island.build_environment(Path('/native'), Path('/target'), metal)
        self.assertEqual(env['TOOLCHAINS'], requested)
        self.assertEqual(env['BINDGEN_EXTRA_CLANG_ARGS'], '--sysroot=/fixture/MacOSX.sdk')


class MacQualificationDocsTests(unittest.TestCase):
    def test_documented_derived_launch_matches_reused_target_and_lifecycle(self):
        docs = (SCRIPTS.parent / 'docs/macos.md').read_text()
        target = '_build/zed-island/target/mzed-baseline'
        preserved = target + '-preserved/debug/zed'
        self.assertIn('--reuse-target ' + target, docs)
        self.assertIn('"$PWD/' + target + '/debug/zed"', docs)
        self.assertIn('"$PWD/' + preserved + '"', docs)
        self.assertIn('executable-relative asset lookup still reaches the', docs)
        self.assertIn('correct `.git` root even when `_build` points to external storage', docs)
        for step in (
            'Click the scene twice',
            'Click `M` to hide it, then click',
            '`M` again to remount it',
            'Click the scene once',
            'then click `M` once more to disable it',
            'produce native counters `[1, 2, 1]`, one remount, and two disables',
        ):
            self.assertIn(step, docs)


if __name__ == '__main__':
    unittest.main()
