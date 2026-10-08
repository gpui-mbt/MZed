import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('build_island', ROOT / 'scripts/build_island.py')
build_island = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_island)


class BuildEnvironmentTests(unittest.TestCase):
    def test_private_native_and_proxy_environment_is_forwarded(self):
        inherited = {
            'PATH': '/private/bin',
            'PKG_CONFIG_PATH': '/private/pkgconfig',
            'LD_LIBRARY_PATH': '/private/lib',
            'LIBRARY_PATH': '/private/lib',
            'LIBCLANG_PATH': '/private/clang',
            'CC': '/private/clang/bin/clang',
            'CXX': '/private/clang/bin/clang++',
            'CFLAGS': '-fPIC',
            'CXXFLAGS': '-std=c++20',
            'LDFLAGS': '-L/private/lib',
            'HTTP_PROXY': 'http://proxy.invalid:1234',
            'HTTPS_PROXY': 'http://proxy.invalid:1234',
            'ALL_PROXY': 'socks5://proxy.invalid:1234',
            'NO_PROXY': 'localhost,127.0.0.1',
            'https_proxy': 'http://lowercase.invalid:1234',
        }
        with patch.dict(os.environ, inherited, clear=True):
            result = build_island.build_environment(Path('/private/native'), Path('/private/target'))

        for name, value in inherited.items():
            self.assertEqual(result[name], value)
        self.assertEqual(result['CARGO_TARGET_DIR'], '/private/target')
        self.assertEqual(result['MZED_NATIVE_LIB_DIR'], '/private/native')

    def test_build_records_jobs_and_environment_names_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            native = root / 'native'
            output = root / 'evidence'
            source.mkdir()
            native.mkdir()
            (source / 'mzed-derived.json').write_text('{}\n')
            (native / 'build.json').write_text('{}\n')
            disk = type('Disk', (), {'free': 30 * 1024**3})()
            system_path = os.environ.get('PATH', '')
            with patch.object(build_island.shutil, 'disk_usage', return_value=disk), \
                    patch.dict(os.environ, {
                        'PATH': system_path,
                        'PKG_CONFIG_PATH': '/private/pkgconfig',
                        'HTTPS_PROXY': 'http://secret-proxy.invalid',
                    }, clear=True), \
                    patch.object(build_island, 'run_bounded', return_value=17) as run:
                self.assertEqual(build_island.build(source, native, output, jobs=1), 17)

            record = json.loads((output / 'build.json').read_text())
            self.assertEqual(record['jobs'], 1)
            self.assertIn('PKG_CONFIG_PATH', record['inherited_environment_names'])
            self.assertIn('HTTPS_PROXY', record['inherited_environment_names'])
            self.assertNotIn('secret-proxy.invalid', json.dumps(record))
            self.assertIn('--jobs', record['command'])
            self.assertEqual(record['command'][record['command'].index('--jobs') + 1], '1')
            self.assertEqual(run.call_args.args[2]['PKG_CONFIG_PATH'], '/private/pkgconfig')


if __name__ == '__main__':
    unittest.main()
