#!/usr/bin/env python3
"""Owner-run live v3 evidence gate. Only wayland-info is invoked, never an IME/app.
This helper was prepared but not run during package acquisition.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,os,re,stat,subprocess,sys
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--prefix',type=Path,required=True)
p.add_argument('--profile',type=Path,required=True)
p.add_argument('--display',required=True)
p.add_argument('--xauthority',type=Path)
p.add_argument('--wayland-display',required=True)
p.add_argument('--evidence-dir',type=Path,required=True)
a=p.parse_args()
if Path(a.wayland_display).name!=a.wayland_display or a.wayland_display in {'.','..',''}:
    raise SystemExit('Use the owner-verified new socket basename')
socket=a.profile/'xdg-runtime'/a.wayland_display
st=socket.stat()
if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=os.getuid():raise SystemExit('Private Wayland socket owner/type mismatch')
if not a.evidence_dir.is_dir():raise SystemExit('Owner must create the private evidence directory')
wrapper=Path(__file__).resolve().parent/'prefix-env.py'
info=a.prefix/'usr/bin/wayland-info'
cmd=[sys.executable,str(wrapper),'--prefix',str(a.prefix),'--profile',str(a.profile),'--display',a.display,'--wayland-display',a.wayland_display]
if a.xauthority:cmd+=['--xauthority',str(a.xauthority)]
cmd+=['--',str(info)]
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
stdout=a.evidence_dir/('wayland-info-'+stamp+'.stdout.txt')
stderr=a.evidence_dir/('wayland-info-'+stamp+'.stderr.txt')
proc=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
stdout.write_text(proc.stdout);stderr.write_text(proc.stderr)
interfaces=sorted(set(re.findall(r"interface:\s*['\"]([^'\"]+)['\"]",proc.stdout)))
passed=proc.returncode==0 and 'zwp_text_input_manager_v3' in interfaces
report={'scope':'Live v3 compositor-global gate only; no IME or application qualification','command':cmd,'wayland_socket':str(socket),'wayland_info_sha256':hashlib.sha256(info.read_bytes()).hexdigest(),'returncode':proc.returncode,'interfaces':interfaces,'text_input_v3':passed,'input_method_v2_present':'zwp_input_method_manager_v2' in interfaces,'virtual_keyboard_v1_present':'zwp_virtual_keyboard_manager_v1' in interfaces,'stdout':str(stdout),'stderr':str(stderr),'ime_or_application_launched':False}
result=a.evidence_dir/('v3-gate-'+stamp+'.json');result.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'passed':passed,'report':str(result),'scope':report['scope']},indent=2))
sys.exit(0 if passed else 1)
