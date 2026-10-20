"""Evidence without a dispatch, or with duplicate dispatches, cannot qualify."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from palette_interactions import verify_dispatch_log


class PaletteEvidenceTests(unittest.TestCase):
    record = 'MZed palette dispatch=open-settings-file epoch=1 origin=EntityId(7)\n'

    def test_no_selection_is_not_success(self):
        with self.assertRaises(RuntimeError):
            verify_dispatch_log('', 1)

    def test_repeated_selection_is_not_success(self):
        with self.assertRaises(RuntimeError):
            verify_dispatch_log(self.record * 2, 1)

    def test_other_command_is_not_settings(self):
        with self.assertRaises(RuntimeError):
            verify_dispatch_log(self.record.replace('open-settings-file', 'toggle-full-screen'), 1)

    def test_cancellation_requires_zero_and_selection_requires_one(self):
        self.assertEqual(verify_dispatch_log('', 0), [])
        self.assertEqual(len(verify_dispatch_log(self.record, 1)), 1)
        with self.assertRaises(RuntimeError):
            verify_dispatch_log(self.record, 0)
