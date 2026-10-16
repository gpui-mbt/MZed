"""Workflow checkout pins must follow the native compiler source contract."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_native import COMPILER, GPUI_COMMIT


class NativeWorkflowPinsTests(unittest.TestCase):
    def test_supported_os_workflows_pin_compiler_source_and_core(self):
        workflows = [
            ROOT / '.github/workflows/native-island.yml',
            ROOT / '.github/workflows/macos-native-island.yml',
            ROOT / '.github/workflows/windows-island.yml',
        ]
        for path in workflows:
            with self.subTest(workflow=path.name):
                content = path.read_text()
                self.assertIn(f'ref: {GPUI_COMMIT}', content)
                self.assertIn(f'version: {COMPILER}', content)
                self.assertIn(f'core-version: {COMPILER}', content)


if __name__ == '__main__':
    unittest.main()
