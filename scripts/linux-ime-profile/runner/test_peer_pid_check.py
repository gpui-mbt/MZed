import unittest

from peer_pid_check import inspect_peer_log, inspect_peer_log_file


MARKER = "MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID="


class PeerPidCheckTests(unittest.TestCase):
    def inspect(self, text, **kwargs):
        return inspect_peer_log(
            text,
            expected_peer_pid=321,
            fcitx_pid=700,
            emitter_pid_hint=700,
            **kwargs,
        )

    def test_exact_peer_record_passes(self):
        result = self.inspect(f"noise\n{MARKER}321\n")
        self.assertTrue(result["admitted"])
        self.assertEqual(result["observed_peer_pids"], [321])
        self.assertTrue(result["emitter_pid_matches_fcitx"])

    def test_multiple_same_peer_reconnects_pass(self):
        result = self.inspect(f"{MARKER}321\n{MARKER}321\n")
        self.assertTrue(result["admitted"])
        self.assertEqual(result["record_count"], 2)

    def test_different_peer_fails_closed(self):
        result = self.inspect(f"{MARKER}322\n")
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reason"], "authenticated-peer-does-not-match-owned-child")

    def test_any_mismatched_reconnect_fails_closed(self):
        result = self.inspect(f"{MARKER}321\n{MARKER}322\n")
        self.assertFalse(result["admitted"])

    def test_missing_record_is_pending_only_before_gate(self):
        pending = self.inspect("ordinary Fcitx startup log\n", require_record=False)
        self.assertTrue(pending["pending"])
        self.assertEqual(pending["reason"], "authenticated-peer-record-pending")
        rejected = self.inspect("ordinary Fcitx startup log\n")
        self.assertFalse(rejected["admitted"])
        self.assertEqual(rejected["reason"], "authenticated-peer-record-missing")

    def test_malformed_marker_fails_closed(self):
        for text in (f"{MARKER}\n", f"prefix {MARKER}321\n", f"{MARKER}+321\n", f"{MARKER}0\n", f"{MARKER}99999999999999999999\n"):
            with self.subTest(text=text):
                self.assertFalse(self.inspect(text)["admitted"])

    def test_emitter_must_be_retained_fcitx_pid(self):
        result = inspect_peer_log(
            f"{MARKER}321\n",
            expected_peer_pid=321,
            fcitx_pid=700,
            emitter_pid_hint=701,
        )
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reason"], "emitter-pid-does-not-match-retained-fcitx-child")

    def test_missing_log_does_not_hide_emitter_mismatch(self):
        result = inspect_peer_log_file(
            "/path/that/does/not/exist",
            expected_peer_pid=321,
            fcitx_pid=700,
            emitter_pid_hint=701,
            require_record=False,
        )
        self.assertFalse(result["pending"])
        self.assertEqual(result["reason"], "emitter-pid-does-not-match-retained-fcitx-child")

    def test_nonpositive_emitter_inputs_fail_closed(self):
        for value in (0, -1, True, "700"):
            with self.subTest(value=value):
                result = inspect_peer_log(
                    f"{MARKER}321\n",
                    expected_peer_pid=321,
                    fcitx_pid=700,
                    emitter_pid_hint=value,
                )
                self.assertFalse(result["admitted"])


if __name__ == "__main__":
    unittest.main()
