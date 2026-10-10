#!/usr/bin/env python3
"""Build the one-process copied-scene ABI from pinned portable gpui.mbt sources."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
GPUI_COMMIT = 'bf965aebbeb1dfdfed26373d4a7a58bb51a5ad01'
COMPILER = '0.10.14+7d59c7ec9'


def run(argv):
    print(' '.join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), check=True)


def _vsdevcmd():
    configured = os.environ.get('MZED_VSDEVCMD')
    if configured:
        path = Path(configured)
        if path.is_file():
            return path.resolve()
        raise FileNotFoundError(f'MZED_VSDEVCMD does not exist: {path}')
    for variable in ('VSINSTALLDIR', 'VS170COMNTOOLS'):
        value = os.environ.get(variable)
        if value:
            base = Path(value)
            candidates = [base / 'VsDevCmd.bat', base / 'Common7/Tools/VsDevCmd.bat',
                          base / '../Common7/Tools/VsDevCmd.bat']
            for candidate in candidates:
                if candidate.is_file():
                    return candidate.resolve()
    vswhere_candidates = [
        Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe',
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Microsoft Visual Studio/Installer/vswhere.exe',
    ]
    for vswhere in vswhere_candidates:
        if not vswhere.is_file():
            continue
        installation = subprocess.check_output([
            str(vswhere), '-latest', '-products', '*', '-requires',
            'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath',
        ], text=True).strip()
        candidate = Path(installation) / 'Common7/Tools/VsDevCmd.bat'
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('Visual Studio 2022 x64 C++ tools were not found (install them or set MZED_VSDEVCMD)')


def _environment_value(environment, name):
    for key, value in environment.items():
        if key.casefold() == name.casefold():
            return value
    return None


def load_msvc_environment():
    """Return the current process environment with the x64 MSVC tools loaded."""
    if os.name != 'nt':
        raise RuntimeError('the MSVC environment is available only on Windows')
    current = dict(os.environ)
    current_path = _environment_value(current, 'PATH')
    if shutil.which('cl.exe', path=current_path) and shutil.which('lib.exe', path=current_path):
        return current
    devcmd = _vsdevcmd()
    with tempfile.NamedTemporaryFile('w', suffix='.cmd', encoding='ascii', newline='\r\n', delete=False) as script:
        script_path = Path(script.name)
        script.write('@echo off\r\n')
        script.write(f'call "{devcmd}" -no_logo -arch=x64 -host_arch=x64 >nul\r\n')
        script.write('if errorlevel 1 exit /b 1\r\n')
        script.write('set\r\n')
    try:
        result = subprocess.run(['cmd.exe', '/d', '/c', str(script_path)], check=True,
                                capture_output=True, text=True, encoding='mbcs')
    finally:
        script_path.unlink(missing_ok=True)
    environment = dict(os.environ)
    keys = {name.casefold(): name for name in environment}
    for line in result.stdout.splitlines():
        if '=' not in line:
            continue
        name, value = line.split('=', 1)
        # cmd.exe also prints drive-current-directory pseudo variables (for example =C:).
        if name and not name.startswith('='):
            key = keys.get(name.casefold(), name)
            environment[key] = value
            keys[name.casefold()] = key
    path = _environment_value(environment, 'PATH')
    if not shutil.which('cl.exe', path=path) or not shutil.which('lib.exe', path=path):
        raise RuntimeError('VsDevCmd did not expose both cl.exe and lib.exe')
    return environment


def build_msvc(units, output, home):
    environment = load_msvc_environment()
    path = _environment_value(environment, 'PATH')
    compiler = shutil.which('cl.exe', path=path)
    librarian = shutil.which('lib.exe', path=path)
    include = home / 'include'
    objects = []
    for unit in units:
        obj = output / (unit.stem + '.obj')
        argv = [compiler, '/nologo', '/O2', '/std:c11', '/utf-8',
                '/D_CRT_SECURE_NO_WARNINGS', '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX',
                '/I' + str(include), '/c', str(unit), '/Fo' + str(obj)]
        print(' '.join(map(str, argv)), flush=True)
        subprocess.run(argv, check=True, env=environment)
        objects.append(obj)
    archive = output / 'mzed_native.lib'
    argv = [librarian, '/NOLOGO', '/OUT:' + str(archive), *map(str, objects)]
    print(' '.join(map(str, argv)), flush=True)
    subprocess.run(argv, check=True, env=environment)
    toolset = Path(compiler).parents[3].name
    return archive, 'MSVC C compiler toolset ' + toolset


def build(source, output, mode):
    if os.name == 'nt' and mode != 'normal':
        raise ValueError('MSVC native builds support mode=normal; sanitizer modes are Linux-only')
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != GPUI_COMMIT:
        raise ValueError('gpui.mbt source does not match reviewed pin')
    if subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip():
        raise ValueError('preserve modified gpui.mbt; use a fresh checkout')
    if not (ROOT / "native/island.mbt").is_file():
        raise ValueError("native/island.mbt is required")
    output.mkdir(parents=True, exist_ok=False)
    home = Path(os.environ.get('MOON_HOME', str(Path.home() / '.moon')))
    compiler = home / 'bin/moonc'
    version = subprocess.check_output([str(compiler), '-v'], text=True, stderr=subprocess.STDOUT).strip()
    if COMPILER not in version:
        raise ValueError('MoonBit compiler does not match pinned version')
    core_version = tomllib.loads((home / 'lib/core/moon.mod').read_text())['version']
    if core_version != COMPILER:
        raise ValueError('MoonBit core does not match pinned version')
    core = home / 'lib/core/_build/native/release/bundle'
    packages = [('primitives', []), ('layout', ['primitives']), ('scene', ['primitives']),
                ('element', ['primitives', 'layout', 'scene'])]
    for package, dependencies in packages:
        files = sorted(p for p in (source / package).glob('*.mbt') if not p.name.endswith('_test.mbt'))
        argv = [compiler, 'build-package', *files, '-pkg', 'f4ah6o/gpui/' + package,
                '-pkg-type', 'library', '-target', 'native', '-std-path', core,
                '-i', str(core / 'prelude/prelude.mi') + ':prelude', '-o', output / (package + '.core')]
        for dependency in dependencies:
            argv += ['-i', str(output / (dependency + '.mi')) + ':' + dependency]
        run(argv)
    argv = [compiler, 'build-package', ROOT / 'native/island.mbt', '-pkg', 'mzed/native_island',
            '-pkg-type', 'foreign_library', '-target', 'native', '-std-path', core,
            '-i', str(core / 'prelude/prelude.mi') + ':prelude', '-o', output / 'island.core']
    for package, _ in packages:
        argv += ['-i', str(output / (package + '.mi')) + ':' + package]
    run(argv)
    run([compiler, 'link-core', core / 'abort/abort.core', core / 'core.core',
         *[output / (package + '.core') for package, _ in packages], output / 'island.core',
         '-main', 'mzed/native_island', '-target', 'native', '-o', output / 'island.c'])
    units = [output / 'island.c', ROOT / 'native/bootstrap.c']
    units += [home / 'lib/runtime' / (name + '.c') for name in ['runtime', 'env', 'backtrace', 'utf']]
    if os.name == 'nt':
        archive, c_compiler = build_msvc(units, output, home)
        target = 'x86_64-pc-windows-msvc'
    else:
        flags = ['-std=gnu11', '-O2', '-g', '-fwrapv', '-fno-strict-aliasing',
                 '-ffunction-sections', '-fdata-sections', '-I' + str(home / 'include')]
        if mode != 'normal':
            flags += ['-fsanitize=' + ('undefined' if mode == 'ubsan' else 'address,undefined'),
                      '-fno-sanitize-recover=all']
        objects = []
        for unit in units:
            obj = output / (unit.stem + '.o')
            run(['cc', *flags, '-c', unit, '-o', obj])
            objects.append(obj)
        archive = output / 'libmzed_native.a'
        run(['ar', 'crs', archive, *objects])
        target = 'native'
        c_compiler = subprocess.check_output(['cc', '--version'], text=True).splitlines()[0]
    harness_commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    (output / 'build.json').write_text(json.dumps({'schema': 1, 'gpui_commit': actual,
        'harness_commit': harness_commit,
        'compiler': version, 'core_version': core_version, 'mode': mode, 'target': target,
        'c_compiler': c_compiler, 'native_library': str(archive)}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--mode', choices=['normal', 'ubsan', 'asan_ubsan'], default='normal')
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve(), args.mode)
