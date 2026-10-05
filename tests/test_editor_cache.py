import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import editor_cache


class CacheTests(unittest.TestCase):
    def fixture(self, root):
        cache = root / 'cache'
        cache.mkdir()
        binary = cache / 'zed'
        binary.write_bytes(b'unit-test-payload-not-an-editor')
        binary.chmod(0o755)
        checksum = hashlib.sha256(binary.read_bytes()).hexdigest()
        record = {'schema': 1, 'key': 'expected', 'binary_sha256': checksum,
            'binary_bytes': binary.stat().st_size, 'system_libraries': {},
            'build_record': {'build': 'passed', 'harness_commit': 'original', 'upstream_commit': 'upstream', 'binary_sha256': checksum}}
        (cache / 'manifest.json').write_text(json.dumps(record))
        return cache

    def test_exact_cache_preserves_origin_and_materializes_only_after_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = self.fixture(root)
            source, output = root / 'source', root / 'evidence'
            with patch.object(editor_cache, 'command', return_value='candidate'):
                editor_cache.restore(source, output, cache, {'key': 'expected', 'inputs': {'source_commit': 'upstream'}})
            record = json.loads((output / 'build.json').read_text())
            self.assertEqual(record['harness_commit'], 'candidate')
            self.assertEqual(record['binary_origin_harness_commit'], 'original')
            self.assertTrue(record['cache_reused'])
            self.assertEqual((source / 'target/mzed-island/debug/zed').read_bytes(), (cache / 'zed').read_bytes())

    def test_stale_key_never_creates_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'):
                editor_cache.restore(root / 'source', root / 'evidence', cache, {'key': 'changed'})
            self.assertFalse((root / 'source').exists())

    def test_corrupt_bytes_never_pass_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = self.fixture(root)
            (cache / 'zed').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
                editor_cache.verify(cache, {'key': 'expected', 'inputs': {'source_commit': 'upstream'}})

    def test_symlink_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = self.fixture(root)
            (cache / 'zed').rename(root / 'old')
            (cache / 'zed').symlink_to(root / 'old')
            with self.assertRaisesRegex(ValueError, 'invalid cached executable'):
                editor_cache.verify(cache, {'key': 'expected', 'inputs': {'source_commit': 'upstream'}})

    def test_existing_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = self.fixture(root)
            target = root / 'source/target/mzed-island'
            target.mkdir(parents=True)
            sentinel = target / 'keep'
            sentinel.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'preserve existing'):
                editor_cache.restore(root / 'source', root / 'evidence', cache, {'key': 'expected', 'inputs': {'source_commit': 'upstream'}})
            self.assertEqual(sentinel.read_text(), 'keep')

class FingerprintTests(unittest.TestCase):
    def test_application_and_environment_changes_invalidate_but_harness_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, native, moon = root / 'source', root / 'built', root / 'moon'
            for filename in editor_cache.BUILD_FILES + ['native/island.mbt', 'native/protocol.rs',
                    'native/island_view.rs', 'scripts/smoke_island.py']:
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('public source')
            source.mkdir()
            (source / 'added.rs').write_text('derived code')
            native.mkdir()
            (native / 'libmzed_native.a').write_bytes(b'native image')
            (native / 'build.json').write_text(json.dumps({'schema': 1, 'native_library': '/ignored/location', 'mode': 'normal'}))
            for filename in ['bin/moonc', 'lib/runtime/runtime.c', 'include/moonbit.h',
                             'lib/core/_build/native/release/bundle/core.core',
                             'lib/core/_build/native/release/bundle/abort/abort.core']:
                path = moon / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'pinned toolchain')
            def command(argv):
                if 'rev-parse' in argv:
                    return 'pinned'
                if 'ls-files' in argv:
                    return 'added.rs'
                return 'verified command input ' + argv[0]
            with patch.object(editor_cache, 'ROOT', root), patch.object(editor_cache, 'LOCK', {'commit': 'pinned'}), \
                    patch.object(editor_cache, 'command', side_effect=command), \
                    patch.object(editor_cache.platform, 'system', return_value='Linux'), \
                    patch.dict(editor_cache.os.environ, {'MOON_HOME': str(moon), 'CFLAGS': ''}):
                original = editor_cache.fingerprint(source, native)['key']
                (root / 'scripts/smoke_island.py').write_text('new harness only')
                self.assertEqual(editor_cache.fingerprint(source, native)['key'], original)
                (root / 'native/island.mbt').write_text('different application')
                self.assertNotEqual(editor_cache.fingerprint(source, native)['key'], original)
                (root / 'native/island.mbt').write_text('public source')
                for filename in ['native/protocol.rs', 'native/island_view.rs']:
                    path = root / filename
                    path.write_text('new fault-enabled application source')
                    self.assertNotEqual(editor_cache.fingerprint(source, native)['key'], original)
                    path.write_text('public source')
                with patch.dict(editor_cache.os.environ, {'CFLAGS': '-O0'}):
                    self.assertNotEqual(editor_cache.fingerprint(source, native)['key'], original)
                (native / 'libmzed_native.a').write_bytes(b'changed native runtime')
                self.assertNotEqual(editor_cache.fingerprint(source, native)['key'], original)
