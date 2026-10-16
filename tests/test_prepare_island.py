"""Regression coverage for patch-owned native sources in a derived Zed checkout."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_island
from build_native import GPUI_COMMIT


class PrepareIslandTests(unittest.TestCase):
    def make_fixture(self, directory, *, drift=None):
        harness = directory / 'harness'
        source = directory / 'source'
        source.mkdir()
        subprocess.run(['git', '-C', str(source), 'init', '--quiet'], check=True)

        (harness / 'patches').mkdir(parents=True)
        (harness / 'upstream.lock.json').write_text('{"commit": "fixture-upstream"}\n')
        (harness / 'native/island.mbt').parent.mkdir(parents=True)
        (harness / 'native/island.mbt').write_text('// fixture\n')
        (source / 'Cargo.lock').write_text('# fixture\n')
        fence = source / 'crates/gpui_linux/src/linux/wayland/ime_fence.rs'
        fence.parent.mkdir(parents=True)
        fence.write_text('// fixture\n')

        hunks = []
        for template, path in prepare_island.PATCHED_NATIVE_FILES:
            native_file = harness / template
            native_file.parent.mkdir(parents=True, exist_ok=True)
            native_file.write_text(f'// {template}\n')
            patch_line = f'// modified {template}' if drift == path else f'// {template}'
            hunks.append(
                f'diff --git a/{path} b/{path}\n'
                'new file mode 100644\n'
                '--- /dev/null\n'
                f'+++ b/{path}\n'
                '@@ -0,0 +1 @@\n'
                f'+{patch_line}\n'
            )
        (harness / 'patches/native-island.patch').write_text(''.join(hunks))
        return harness, source

    def prepare_fixture(self, harness, source):
        with patch.object(prepare_island, 'ROOT', harness), \
             patch.object(prepare_island, 'verify_source'), \
             patch.object(prepare_island, 'ensure_no_default_linux_collision',
                          return_value='ctrl-alt-shift-z'):
            return prepare_island.prepare(source)

    def test_patch_created_directory_and_files_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            harness, source = self.make_fixture(Path(directory))
            result = self.prepare_fixture(harness, source)

            self.assertTrue((source / 'crates/zed/src/zed/native_island').is_dir())
            for template, path in prepare_island.PATCHED_NATIVE_FILES:
                self.assertEqual((source / path).read_bytes(), (harness / template).read_bytes())
            self.assertEqual(result['palette_shortcut'], 'ctrl-alt-shift-z')
            self.assertTrue((source / 'mzed-derived.json').is_file())
            self.assertEqual(json.loads((source / 'mzed-derived.json').read_text()), result)

    def test_patch_and_template_divergence_fails_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            mismatched = 'crates/zed/src/zed/native_island/protocol.rs'
            harness, source = self.make_fixture(Path(directory), drift=mismatched)

            with self.assertRaisesRegex(ValueError, 'patched native source mismatch'):
                self.prepare_fixture(harness, source)

            self.assertEqual((source / mismatched).read_text(),
                             '// modified native/protocol.rs\n')
            self.assertFalse((source / 'mzed-derived.json').exists())

    def test_native_workflow_checks_out_compiler_pinned_gpui(self):
        workflow = (ROOT / '.github/workflows/native-island.yml').read_text()
        self.assertIn(f'ref: {GPUI_COMMIT}', workflow)


if __name__ == '__main__':
    unittest.main()
