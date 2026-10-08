from __future__ import annotations

from pathlib import Path
import unittest

from socket_path_preflight import abstract_socket, build_socket_budget, filesystem_socket


class SocketPathBudgetTests(unittest.TestCase):
    def test_filesystem_socket_counts_trailing_nul(self):
        capacity = 108
        path_107 = Path("/" + "a" * 106)
        path_108 = Path("/" + "a" * 107)
        self.assertEqual(len(str(path_107).encode()), 107)
        self.assertTrue(filesystem_socket("fits", path_107, capacity)["fits"])
        self.assertEqual(filesystem_socket("fits", path_107, capacity)["required_sun_path_bytes"], 108)
        self.assertFalse(filesystem_socket("too-long", path_108, capacity)["fits"])

    def test_abstract_socket_counts_leading_nul_without_trailing_nul(self):
        self.assertTrue(abstract_socket("exact", b"\0" + b"a" * 107, 108)["fits"])
        self.assertFalse(abstract_socket("too-long", b"\0" + b"a" * 108, 108)["fits"])

    def test_profile_budget_uses_real_linux_capacity_and_passes_short_root(self):
        report = build_socket_budget(Path("/" + "t" * 32))
        self.assertEqual(report["schema"], "mzed-unix-socket-path-budget-v2")
        self.assertEqual(report["sun_path"]["capacity_bytes"], 108)
        self.assertEqual(report["status"], "pass")
        self.assertTrue(all(entry["fits"] for entry in report["unix_socket_candidates"]))

    def test_rejects_relative_run_root(self):
        with self.assertRaises(ValueError):
            build_socket_budget(Path("relative/run"))


if __name__ == "__main__":
    unittest.main()
