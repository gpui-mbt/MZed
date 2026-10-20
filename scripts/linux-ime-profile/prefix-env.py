#!/usr/bin/env python3
"""Run one explicitly supplied ELF with the isolated private-profile environment."""
from pathlib import Path
import argparse,json,os,sys
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--prefix',type=Path,required=True)
parser.add_argument('--profile',type=Path,required=True)
parser.add_argument('--display',required=True,help='Owner-verified outer X11 DISPLAY, e.g. :0')
parser.add_argument('--xauthority',type=Path,help='Optional owner-verified read-only outer X11 authority file')
parser.add_argument('--wayland-display',default='',help='Only use the new private socket after owner verifies it')
parser.add_argument('--mzed-native-palette',action='store_true',help='Enable the approved MZed native palette for this isolated test process')
parser.add_argument('--direct-exec',action='store_true',help='Exec the target ELF directly, preserving its /proc/self/exe path for checkout-relative debug assets')
parser.add_argument('--allow-emulated-gpu',action='store_true',help='Allow the app to continue under this isolated software-rendered profile')
parser.add_argument('--mzed-runner',action='store_true',help='Pass the isolated run root/prefix into the owned MZed session runner')
parser.add_argument('--mzed-zed-binary',type=Path,help='Compiled Zed ELF used by the owned session runner')
parser.add_argument('--print-env',action='store_true',help='Describe the environment without creating state or launching a target')
parser.add_argument('command',nargs=argparse.REMAINDER)
a=parser.parse_args()
def has_symlink_component(path):
    current=path
    while current!=current.parent:
        if current.is_symlink():return True
        current=current.parent
    return False
def under_system_root(path):
    roots=(Path('/bin'),Path('/sbin'),Path('/lib'),Path('/lib64'),Path('/usr'),Path('/etc'),Path('/var'),Path('/opt'))
    return any(path==root or root in path.parents for root in roots)
if (not a.prefix.is_absolute() or not a.profile.is_absolute() or a.prefix==Path('/') or a.profile==Path('/')
        or has_symlink_component(a.prefix) or has_symlink_component(a.profile)):
    raise SystemExit('Use absolute private paths without symlinked components')
prefix=a.prefix.resolve();profile=a.profile.resolve()
if under_system_root(prefix) or under_system_root(profile):
    raise SystemExit('Private prefixes and profiles cannot use system directories')
if not prefix.is_dir() or prefix.stat().st_uid!=os.getuid():
    raise SystemExit('Private runtime prefix must exist and belong to this uid')
if not profile.is_dir() or profile.stat().st_uid!=os.getuid():
    raise SystemExit('Private profile must exist and belong to this uid')
if Path(a.wayland_display).name!=a.wayland_display or a.wayland_display in {'.','..'}:
    raise SystemExit('Wayland display must be a private socket basename')
libpath=':'.join(str(prefix/x) for x in ['usr/lib/x86_64-linux-gnu','lib/x86_64-linux-gnu','usr/lib','lib'])
# This is the genuine home directory of the isolated child process. The invoking
# shell HOME is neither overwritten nor used as a scratch-path variable.
env={
    'PATH':str(prefix/'usr/bin')+':'+str(prefix/'bin')+':/usr/bin:/bin',
    'HOME':str(profile/'home'),
    'XDG_CONFIG_HOME':str(profile/'xdg-config'),
    'XDG_CACHE_HOME':str(profile/'xdg-cache'),
    'XDG_DATA_HOME':str(profile/'xdg-data'),
    'XDG_STATE_HOME':str(profile/'xdg-state'),
    'XDG_RUNTIME_DIR':str(profile/'xdg-runtime'),
    'XDG_CONFIG_DIRS':str(profile/'xdg-config'),
    'XDG_DATA_DIRS':str(prefix/'usr/share'),
    'DISPLAY':a.display,
    'WAYLAND_DISPLAY':a.wayland_display,
    'DBUS_SESSION_BUS_ADDRESS':'unix:path='+str(profile/'xdg-runtime/bus'),
    'LANG':'C.UTF-8',
    'LC_ALL':'C.UTF-8',
    'LD_LIBRARY_PATH':libpath,
    'WLR_BACKENDS':'x11',
    'WLR_RENDERER':'pixman',
    'WLR_XWAYLAND':str(prefix/'usr/bin/Xwayland'),
    'LABWC_UPDATE_ACTIVATION_ENV':'0',
    'XKB_CONFIG_ROOT':str(prefix/'usr/share/X11/xkb'),
    'XCURSOR_PATH':str(prefix/'usr/share/icons'),
    'FONTCONFIG_FILE':str(profile/'xdg-config/fontconfig/fonts.conf'),
    'FONTCONFIG_PATH':str(profile/'xdg-config/fontconfig'),
    'FCITX_ADDON_DIRS':str(prefix/'usr/lib/x86_64-linux-gnu/fcitx5'),
    'FCITX_DATA_DIRS':str(prefix/'usr/share/fcitx5'),
    'FCITX_DATA_HOME':str(profile/'xdg-data/fcitx5'),
    'FCITX_CONFIG_HOME':str(profile/'xdg-config/fcitx5'),
    'FCITX_CONFIG_DIRS':str(profile/'xdg-config/fcitx5'),
    'SKIP_FCITX_PATH':'1',
}
if a.xauthority:
    if not a.xauthority.is_absolute() or a.xauthority.is_symlink() or not a.xauthority.is_file():raise SystemExit('Use an owner-verified absolute XAUTHORITY file')
    env['XAUTHORITY']=str(a.xauthority)
if a.mzed_native_palette:
    env['MZED_NATIVE_PALETTE']='1'
    env['ZED_UPDATE_EXPLANATION']='Private nested Wayland-v3 command-palette smoke; auto-updates disabled in this profile.'
if a.allow_emulated_gpu:
    env['ZED_ALLOW_EMULATED_GPU']='1'
if a.mzed_runner:
    if a.mzed_zed_binary is None or not a.mzed_zed_binary.is_absolute():
        raise SystemExit('--mzed-runner requires an absolute --mzed-zed-binary')
    if a.mzed_zed_binary.is_symlink() or not a.mzed_zed_binary.is_file():
        raise SystemExit('--mzed-zed-binary must be a real executable file')
    env['MZED_RUN_ROOT']=str(profile.parent)
    env['MZED_RUNTIME_PREFIX']=str(prefix)
    env['MZED_ZED_BINARY']=str(a.mzed_zed_binary)
elif a.mzed_zed_binary is not None:
    raise SystemExit('--mzed-zed-binary is only valid with --mzed-runner')
if a.print_env:
    print(json.dumps(env,indent=2));sys.exit(0)
for rel in ['home','xdg-config','xdg-cache','xdg-data','xdg-state','xdg-runtime']:
    q=profile/rel
    if not q.is_dir() or q.is_symlink():raise SystemExit('Owner must first create the private profile directory: '+str(q))
if profile.stat().st_uid!=os.getuid() or (profile/'xdg-runtime').stat().st_mode & 0o077:
    raise SystemExit('Profile must be owned by this uid and runtime must be private mode 0700')
command=a.command
if command and command[0]=='--':command=command[1:]
if not command:raise SystemExit('Provide the exact owner-approved target command')
program=Path(command[0])
if not program.is_absolute() or not program.is_file():raise SystemExit('Provide an absolute target ELF path')
with program.open('rb') as f:
    if f.read(4)!=b'\x7fELF':raise SystemExit('This wrapper accepts explicit ELF executables only')
loader=prefix/'usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
if not loader.is_file():raise SystemExit('Verified prefix loader is absent')
if a.direct_exec:
    os.execve(str(program),command,env)
os.execve(str(loader),[str(loader),'--library-path',libpath,*command],env)
