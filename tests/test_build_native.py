import os
from pathlib import Path
from pathlib import PureWindowsPath
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_native


class MsvcTargetTests(unittest.TestCase):
    def test_tool_paths_reveal_target_architecture(self):
        root = PureWindowsPath(r'C:\VS\VC\Tools\MSVC\14.44.35207\bin')
        self.assertEqual(build_native._msvc_tool_target_arch(root / 'HostX64/x86/cl.exe'), 'x86')
        self.assertEqual(build_native._msvc_tool_target_arch(root / 'HostX64/x64/cl.exe'), 'x64')
        self.assertEqual(build_native._msvc_tool_target_arch(root / 'HostX86/x64/lib.exe'), 'x64')
        self.assertIsNone(build_native._msvc_tool_target_arch(root / 'x86/cl.exe'))

    @unittest.skipUnless(os.name == 'nt', 'VsDevCmd environment activation is Windows-only')
    def test_x86_target_tools_force_x64_vscmd_activation(self):
        root = PureWindowsPath(r'C:\VS\VC\Tools\MSVC\14.44.35207\bin')
        x86 = root / 'HostX64/x86'
        x64 = root / 'HostX64/x64'
        environment = dict(os.environ)
        environment['PATH'] = str(x86) + ';' + environment.get('PATH', '')

        def which(program, path=None):
            lowered = (path or '').casefold()
            if str(x86).casefold() in lowered:
                return str(x86 / program)
            if str(x64).casefold() in lowered:
                return str(x64 / program)
            return None

        def run(argv, **kwargs):
            script = Path(argv[-1]).read_text(encoding='ascii')
            self.assertIn('-arch=x64 -host_arch=x64', script)
            return SimpleNamespace(stdout='PATH=' + str(x64) + ';C:\\Windows\r\n')

        with patch.object(build_native.os, 'environ', environment), \
                patch.object(build_native.shutil, 'which', side_effect=which), \
                patch.object(build_native, '_vsdevcmd', return_value=Path(r'C:\VS\VsDevCmd.bat')) as devcmd, \
                patch.object(build_native.subprocess, 'run', side_effect=run):
            activated = build_native.load_msvc_environment()

        devcmd.assert_called_once_with()
        self.assertEqual(build_native._msvc_tool_target_arch(which('cl.exe', activated['PATH'])), 'x64')
        self.assertEqual(build_native._msvc_tool_target_arch(which('lib.exe', activated['PATH'])), 'x64')


if __name__ == '__main__':
    unittest.main()
