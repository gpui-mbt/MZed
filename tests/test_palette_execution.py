"""The execution oracle rejects missing, duplicate and misbound evidence."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from palette_interactions import verify_execution


class ExecutionEvidenceTests(unittest.TestCase):
    dispatch = 'MZed palette dispatch token=0:7:1:4:3 action=mzed.open-settings-file window=WindowId(1)\n'
    opened = 'MZed palette settings-opened token=0:7:1:4:3 window=WindowId(1)\n'

    def test_one_matching_command_and_completion_are_required(self):
        self.assertEqual(verify_execution(self.dispatch + self.opened)['dispatch_count'], 1)

    def test_missing_or_duplicated_events_fail(self):
        for log in ['', self.dispatch, self.opened,
                    self.dispatch * 2 + self.opened, self.dispatch + self.opened * 2]:
            with self.subTest(log=log), self.assertRaises(RuntimeError):
                verify_execution(log)

    def test_wrong_window_request_generation_or_action_fails(self):
        for opened in [self.opened.replace('WindowId(1)', 'WindowId(2)'),
                       self.opened.replace('0:7:1:4:3', '0:8:1:4:3'),
                       self.opened.replace('0:7:1:4:3', '0:7:1:5:3')]:
            with self.subTest(opened=opened), self.assertRaises(RuntimeError):
                verify_execution(self.dispatch + opened)
        with self.assertRaises(RuntimeError):
            verify_execution(self.dispatch.replace('open-settings-file', 'toggle-full-screen') + self.opened)
