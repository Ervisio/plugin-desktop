#!/usr/bin/env python3
"""Ervisio Desktop: constrained Linux desktop management bridge (Python stdlib only).

Manifest grants root only for apply/control. No shell, no caller-supplied package
names, no commands from user input. Destructive plans are hashed and verified.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

ROOT = Path('/var/lib/ervisio-desktop')
JOBS = ROOT / 'jobs'
OWNED = ROOT / 'owned.json'
BACKEND = Path(__file__).resolve()
ENV_NAMES = {'gnome': 'GNOME', 'plasma': 'KDE Plasma', 'cinnamon': 'Cinnamon',
             'xfce': 'Xfce', 'mate': 'MATE', 'lxqt': 'LXQt'}
COMPONENTS = ('files', 'terminal', 'login', 'extras')
MANAGERS = {'apt': 'apt-get', 'dnf': 'dnf', 'pacman': 'pacman', 'zypper': 'zypper'}
DMS = {'gdm': 'gdm.service', 'sddm': 'sddm.service',
       'lightdm': 'lightdm.service', 'lxdm': 'lxdm.service'}
# Component -> candidate packages (first available).  OS repositories decide
# the actual dependencies. Optional components absent in a repository are skipped.
BUNDLES = {
 'apt': {
  'gnome': {'core': [('gnome-shell',), ('gnome-session',)], 'files': [('nautilus',)], 'terminal': [('gnome-terminal',)], 'login': [('gdm3', 'gdm')], 'extras': [('gnome-tweaks',)]},
  'plasma': {'core': [('plasma-desktop', 'kde-plasma-desktop'), ('plasma-workspace',)], 'files': [('dolphin',)], 'terminal': [('konsole',)], 'login': [('sddm',)], 'extras': [('systemsettings',), ('plasma-discover',)]},
  'cinnamon': {'core': [('cinnamon',), ('cinnamon-session',)], 'files': [('nemo',)], 'terminal': [('gnome-terminal', 'xterm')], 'login': [('lightdm',)], 'extras': [('cinnamon-control-center',)]},
  'xfce': {'core': [('xfce4',)], 'files': [('thunar',)], 'terminal': [('xfce4-terminal',)], 'login': [('lightdm',)], 'extras': [('xfce4-goodies',)]},
  'mate': {'core': [('mate-desktop-environment-core', 'mate-desktop-environment')], 'files': [('caja',)], 'terminal': [('mate-terminal',)], 'login': [('lightdm',)], 'extras': [('mate-desktop-environment-extras',)]},
  'lxqt': {'core': [('lxqt-core', 'lxqt')], 'files': [('pcmanfm-qt',)], 'terminal': [('qterminal',)], 'login': [('sddm',)], 'extras': [('lxqt',)]},
 },
 'dnf': {
  'gnome': {'core': [('gnome-shell',), ('gnome-session',)], 'files': [('nautilus',)], 'terminal': [('gnome-terminal',)], 'login': [('gdm',)], 'extras': [('gnome-tweaks',)]},
  'plasma': {'core': [('plasma-desktop',), ('plasma-workspace',)], 'files': [('dolphin',)], 'terminal': [('konsole',)], 'login': [('sddm',)], 'extras': [('plasma-discover',)]},
  'cinnamon': {'core': [('cinnamon',)], 'files': [('nemo',)], 'terminal': [('gnome-terminal',)], 'login': [('lightdm',)], 'extras': [('cinnamon-control-center',)]},
  'xfce': {'core': [('xfce4-session',), ('xfdesktop',)], 'files': [('thunar',)], 'terminal': [('xfce4-terminal',)], 'login': [('lightdm',)], 'extras': [('xfce4-panel',)]},
  'mate': {'core': [('mate-session-manager',), ('mate-desktop',)], 'files': [('caja',)], 'terminal': [('mate-terminal',)], 'login': [('lightdm',)], 'extras': [('mate-panel',)]},
  'lxqt': {'core': [('lxqt-session',), ('lxqt-panel',)], 'files': [('pcmanfm-qt',)], 'terminal': [('qterminal',)], 'login': [('sddm',)], 'extras': [('lxqt-config',)]},
 },
 'pacman': {
  'gnome': {'core': [('gnome-shell',), ('gnome-session',)], 'files': [('nautilus',)], 'terminal': [('gnome-terminal',)], 'login': [('gdm',)], 'extras': [('gnome-tweaks',)]},
  'plasma': {'core': [('plasma-desktop',), ('plasma-workspace',)], 'files': [('dolphin',)], 'terminal': [('konsole',)], 'login': [('sddm',)], 'extras': [('plasma-meta',)]},
  'cinnamon': {'core': [('cinnamon',)], 'files': [('nemo',)], 'terminal': [('gnome-terminal',)], 'login': [('lightdm',)], 'extras': [('cinnamon-translations',)]},
  'xfce': {'core': [('xfce4-session',), ('xfdesktop',), ('xfce4-panel',)], 'files': [('thunar',)], 'terminal': [('xfce4-terminal',)], 'login': [('lightdm',)], 'extras': [('xfce4-settings',)]},
  'mate': {'core': [('mate-session-manager',), ('mate-desktop',), ('mate-panel',)], 'files': [('caja',)], 'terminal': [('mate-terminal',)], 'login': [('lightdm',)], 'extras': [('mate-control-center',)]},
  'lxqt': {'core': [('lxqt-session',), ('lxqt-panel',)], 'files': [('pcmanfm-qt',)], 'terminal': [('qterminal',)], 'login': [('sddm',)], 'extras': [('lxqt-config',)]},
 },
 'zypper': {
  'gnome': {'core': [('gnome-shell',), ('gnome-session',)], 'files': [('nautilus',)], 'terminal': [('gnome-terminal',)], 'login': [('gdm',)], 'extras': [('gnome-tweaks',)]},
  'plasma': {'core': [('plasma5-desktop', 'plasma6-desktop', 'plasma-desktop')], 'files': [('dolphin',)], 'terminal': [('konsole',)], 'login': [('sddm',)], 'extras': [('plasma5-workspace', 'plasma6-workspace')]},
  'cinnamon': {'core': [('cinnamon',)], 'files': [('nemo',)], 'terminal': [('gnome-terminal',)], 'login': [('lightdm',)], 'extras': [('cinnamon-control-center',)]},
  'xfce': {'core': [('xfce4-session',), ('xfce4-panel',)], 'files': [('thunar',)], 'terminal': [('xfce4-terminal',)], 'login': [('lightdm',)], 'extras': [('xfce4-settings',)]},
  'mate': {'core': [('mate-session-manager',), ('mate-panel',)], 'files': [('caja',)], 'terminal': [('mate-terminal',)], 'login': [('lightdm',)], 'extras': [('mate-control-center',)]},
  'lxqt': {'core': [('lxqt-session',), ('lxqt-panel',)], 'files': [('pcmanfm-qt',)], 'terminal': [('qterminal',)], 'login': [('sddm',)], 'extras': [('lxqt-config',)]},
 },
}
PROTECTED = {'openssh-server', 'sshd', 'sudo', 'systemd', 'dbus', 'networkmanager', 'network-manager',
             'python3', 'apt', 'apt-get', 'dnf', 'pacman', 'zypper', 'ervisio'}


def run(cmd: list[str], *, timeout: int = 20, check: bool = False, env=None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False,
                       env=env, stdin=subprocess.DEVNULL)
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout or 'Command failed')[-1600:])
    return p


def output(obj):
    print(json.dumps(obj, ensure_ascii=False, separators=(',', ':')), flush=True)


def refuse(message):
    raise ValueError(message)


def linux():
    if sys.platform != 'linux':
        refuse('Desktop is available only on Linux; Windows is not supported.')
    if not Path('/run/systemd/system').exists():
        refuse('This operation needs a Linux host running systemd.')


def distro():
    data = {}
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                data[k] = v.strip('"')
    except OSError:
        pass
    family = f"{data.get('ID', '')} {data.get('ID_LIKE', '')}".lower()
    for key, needles in (('apt', ('debian', 'ubuntu', 'linuxmint', 'pop')),
                         ('dnf', ('fedora', 'rhel', 'centos', 'rocky', 'alma')),
                         ('pacman', ('arch', 'manjaro', 'endeavour')),
                         ('zypper', ('suse', 'opensuse'))):
        if any(x in family for x in needles) and shutil.which(MANAGERS[key]):
            return {'id': data.get('ID', 'linux'), 'name': data.get('PRETTY_NAME', 'Linux'), 'manager': key}
    return {'id': data.get('ID', 'linux'), 'name': data.get('PRETTY_NAME', 'Linux'), 'manager': None}


def installed(manager: str, package: str):
    if manager == 'apt':
        p = run(['dpkg-query', '-W', '-f=${Status}', package])
        return p.returncode == 0 and 'install ok installed' in p.stdout
    if manager in ('dnf', 'zypper'):
        return run(['rpm', '-q', '--quiet', package]).returncode == 0
    if manager == 'pacman':
        return run(['pacman', '-Qq', package]).returncode == 0
    return False


def available(manager: str, package: str):
    if installed(manager, package):
        return True
    if manager == 'apt':
        p = run(['apt-cache', 'show', package])
        return p.returncode == 0 and re.search(r'^Package:\s*'+re.escape(package)+r'\s*$', p.stdout, re.M) is not None
    if manager == 'dnf':
        p = run(['dnf', '-q', 'repoquery', '--available', '--qf', '%{name}', package], timeout=35)
        if p.returncode == 0 and package in p.stdout.splitlines(): return True
        p = run(['dnf', '-q', 'info', package], timeout=35)
        return p.returncode == 0
    if manager == 'pacman':
        return run(['pacman', '-Si', package], timeout=35).returncode == 0
    if manager == 'zypper':
        p = run(['zypper', '--non-interactive', '--quiet', 'info', '--type', 'package', package], timeout=35)
        return p.returncode == 0
    return False


def groups(profile, components):
    if profile not in ('minimal', 'standard', 'full', 'custom'):
        refuse('Invalid installation profile')
    bits = components.split(',') if components else []
    if len(bits) != len(set(bits)) or any(b not in COMPONENTS for b in bits):
        refuse('Invalid custom component list')
    if profile == 'minimal': return ['core']
    if profile == 'standard': return ['core', 'files', 'terminal', 'login']
    if profile == 'full': return ['core', 'files', 'terminal', 'login', 'extras']
    return ['core', *bits]


def owner_data():
    try:
        obj = json.loads(OWNED.read_text())
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path: Path, obj, mode=0o644):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temp = path.with_name('.'+path.name+'-'+secrets.token_hex(4))
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        os.chmod(path, mode)
    finally:
        try: temp.unlink()
        except FileNotFoundError: pass


def session_files():
    rows = []
    for typ in ('xsessions', 'wayland-sessions'):
        base = Path('/usr/share') / typ
        for f in sorted(base.glob('*.desktop')) if base.is_dir() else []:
            try:
                name = next((line.split('=', 1)[1].strip() for line in f.read_text(errors='replace').splitlines()
                             if line.startswith('Name=')), f.stem)
            except OSError:
                name = f.stem
            rows.append({'id': f.stem, 'name': name, 'type': 'Wayland' if typ == 'wayland-sessions' else 'X11'})
    return rows


def sessions():
    p = run(['loginctl', 'list-sessions', '--no-legend', '--no-pager'])
    found = []
    if p.returncode:
        return found
    for line in p.stdout.splitlines()[:80]:
        columns = line.strip().split()
        if not columns or not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', columns[0]): continue
        sid = columns[0]
        props = run(['loginctl','show-session',sid,'-p','Name','-p','Type','-p','Desktop','-p','State','-p','Scope','-p','Remote'], timeout=8)
        dat = dict(x.split('=', 1) for x in props.stdout.splitlines() if '=' in x)
        scope = dat.get('Scope','')
        usage = {'memoryBytes': None, 'cpuSeconds': None}
        if scope and re.fullmatch(r'[a-zA-Z0-9_.@\\-]+\.scope', scope):
            stats = run(['systemctl','show',scope,'-p','MemoryCurrent','-p','CPUUsageNSec'], timeout=8)
            fields = dict(x.split('=', 1) for x in stats.stdout.splitlines() if '=' in x)
            if fields.get('MemoryCurrent','').isdigit(): usage['memoryBytes'] = int(fields['MemoryCurrent'])
            if fields.get('CPUUsageNSec','').isdigit(): usage['cpuSeconds'] = round(int(fields['CPUUsageNSec']) / 1e9, 1)
        found.append({'id':sid,'user':dat.get('Name',columns[1] if len(columns)>1 else '?'),
                      'desktop':dat.get('Desktop',''),'type':dat.get('Type',''), 'state':dat.get('State',''),
                      'remote':dat.get('Remote','') == 'yes', **usage})
    return found


def service_state(service):
    p = run(['systemctl','is-active',service],timeout=7)
    q = run(['systemctl','is-enabled',service],timeout=7)
    return {'active': p.stdout.strip() == 'active', 'enabled': q.stdout.strip() in ('enabled','static')}


def status():
    if sys.platform != 'linux':
        return {'supported':False,'reason':'Only Linux is supported. Windows desktops cannot be managed by this plugin.'}
    info = distro()
    sess = session_files()
    installed_envs=[]
    for key,name in ENV_NAMES.items():
        indicators = {'gnome': ('gnome-shell','gnome-session'), 'plasma': ('plasma-desktop','plasma-workspace','plasma5-desktop','plasma6-desktop'),
                      'cinnamon': ('cinnamon',), 'xfce': ('xfce4-session','xfce4'),
                      'mate': ('mate-session-manager','mate-desktop-environment'), 'lxqt': ('lxqt-session','lxqt-core')}
        has_pkg = bool(info['manager']) and any(installed(info['manager'], p) for p in indicators[key])
        related = [s for s in sess if key in (s['id']+' '+s['name']).lower() or
                   (key == 'plasma' and 'kde' in (s['id']+' '+s['name']).lower()) or
                   (key == 'xfce' and 'xfce' in (s['id']+' '+s['name']).lower())]
        if has_pkg or related:
            installed_envs.append({'id':key,'name':name,'managed':bool(owner_data().get(key)),
                                   'sessions':related,'installedByPackage':has_pkg})
    recognized = {s['id'] for e in installed_envs for s in e['sessions']}
    others = [s for s in sess if s['id'] not in recognized]
    dms={key:service_state(svc) for key,svc in DMS.items()}
    default = run(['systemctl','get-default'],timeout=8).stdout.strip()
    latest=[]
    if JOBS.is_dir():
        for f in sorted(JOBS.glob('*.json'), key=lambda x:x.stat().st_mtime, reverse=True)[:10]:
            try: latest.append(json.loads(f.read_text()))
            except (OSError,ValueError): continue
    return {'supported':True,'os':info,'environments':installed_envs,'otherSessions':others,
            'displayManagers':dms,'defaultTarget':default,'activeSessions':sessions(), 'jobs':latest,
            'managedPackages':owner_data()}


def selected_packages(manager, desktop, group_names):
    result=[]
    missing=[]
    for group in group_names:
        for variants in BUNDLES[manager][desktop].get(group, []):
            package = next((x for x in variants if available(manager, x)), None)
            if not package:
                missing.append('/'.join(variants))
            elif package not in result:
                result.append(package)
    return result,missing


def commands_for(manager, operation, packages):
    if manager=='apt':
        verb = 'install' if operation=='install' else 'purge'
        return [['apt-get','-y','--no-install-recommends',verb,*packages]]
    if manager=='dnf':
        return [['dnf','-y',operation,*packages]]
    if manager=='pacman':
        return [['pacman','--noconfirm','-S','--needed',*packages]] if operation=='install' else [['pacman','--noconfirm','-Rns',*packages]]
    if manager=='zypper':
        return [['zypper','--non-interactive','--no-recommends','install',*packages]] if operation=='install' else [['zypper','--non-interactive','remove',*packages]]
    refuse('Unsupported package manager')


def validate_request(op, desktop, profile, components, scope):
    if op not in ('install','remove') or desktop not in ENV_NAMES or scope not in ('managed','system'):
        refuse('Unsupported desktop operation')
    if op == 'install' and scope != 'managed': refuse('Installation uses managed mode only')
    return groups(profile, components)


def make_plan(op, desktop, profile, components, scope):
    linux()
    group_names = validate_request(op, desktop, profile, components, scope)
    mgr = distro()['manager']
    if not mgr: refuse('Unsupported Linux package manager. Supported: apt, dnf, pacman, zypper.')
    candidates,missing = selected_packages(mgr, desktop, group_names)
    before = [p for p in candidates if installed(mgr,p)]
    if op=='install':
        if any(m in ['/'.join(v) for v in BUNDLES[mgr][desktop]['core']] for m in missing):
            refuse('Core desktop packages unavailable: '+', '.join(missing))
        packages=[p for p in candidates if p not in before]
    else:
        owned = owner_data().get(desktop,[])
        if scope == 'managed':
            packages=[p for p in owned if installed(mgr,p)]
        else:
            # Explicit system removal covers all known components, independent of UI profile;
            # this is deliberate: no misleading "full removal" based on a minimal profile.
            candidates,missing = selected_packages(mgr,desktop,['core',*COMPONENTS])
            packages=[p for p in candidates if installed(mgr,p)]
            # Do not remove a shared display manager automatically.
            packages=[p for p in packages if p not in ('gdm','gdm3','sddm','lightdm','lxdm')]
    packages=list(dict.fromkeys(packages))
    if any(x.lower() in PROTECTED for x in packages): refuse('Protected system package detected')
    cmds=commands_for(mgr,op,packages) if packages else []
    warnings=[]
    if op=='remove':
        warnings.append('User home directories and settings are never deleted.')
        if scope=='system': warnings.append('Existing system-installed desktop packages will be removed. Shared dependencies and display managers are protected as far as the package manager permits.')
        else: warnings.append('Only packages recorded as installed by this plugin are explicitly removed; other dependencies may remain.')
    if missing: warnings.append('Unavailable optional packages: '+', '.join(missing))
    if op=='install': warnings.append('The graphical login manager is installed if selected but NOT enabled automatically. Enable it in Display managers after installation.')
    if packages and mgr=='apt':
        dry = run(['apt-get','-s','--no-install-recommends','install' if op=='install' else 'purge',*packages], timeout=90)
        if dry.returncode: refuse('APT simulation failed: '+(dry.stderr or dry.stdout)[-800:])
        removed = [m.group(1) for m in re.finditer(r'^Remv\s+(\S+)',dry.stdout,re.M)]
        if any(x.lower() in PROTECTED for x in removed): refuse('APT plans to remove a protected system package: '+', '.join(removed))
        if op=='install' and removed: refuse('APT installation would remove existing packages: '+', '.join(removed))
        warnings.append(f'APT preview: {len(re.findall(r"^Inst\s", dry.stdout, re.M))} installs; {len(removed)} removals.')
        if removed: warnings.append('APT also plans to remove: '+', '.join(removed[:25]))
    if packages and mgr=='pacman' and op=='remove':
        dry=run(['pacman','-Rnp',*packages],timeout=60)
        if dry.returncode: refuse('Pacman could not preview removal: '+(dry.stderr or dry.stdout)[-700:])
    result={'op':op,'desktop':desktop,'profile':profile,'components':components,'scope':scope,
            'manager':mgr,'packages':packages,'alreadyInstalled':before,'commands':cmds,
            'warnings':warnings,'missing':missing,'requiresConfirmation':bool(packages)}
    # Stable proof of exact plan and options (no arbitrary shell arguments).
    result['planHash'] = hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result


def require_root():
    if os.geteuid()!=0: refuse('Administrator privileges required')


def job_path(jobid):
    if not re.fullmatch('[a-f0-9]{24}',jobid): refuse('Invalid job')
    return JOBS/(jobid+'.json')


def start_job(args):
    require_root()
    plan = make_plan(*args[:5])
    if plan['planHash']!=args[5]: refuse('The system changed since preview; refresh the plan before applying.')
    if not plan['packages']: return {'ok': True, 'alreadyDone': True}
    ROOT.mkdir(mode=0o755,parents=True,exist_ok=True)
    JOBS.mkdir(mode=0o755,parents=True,exist_ok=True)
    # Prevent multiple overlapping package-manager transactions. Only queued/active jobs matter.
    for f in JOBS.glob('*.json'):
        try:
            if json.loads(f.read_text()).get('status') in ('queued','running'):
                refuse('Another desktop operation is in progress.')
        except (OSError,ValueError): continue
    jobid = secrets.token_hex(12)
    state={'id':jobid,'status':'queued','created':int(time.time()),'desktop':args[1],
           'operation':args[0],'scope':args[4],'packages':plan['packages'],
           'before':plan['alreadyInstalled'],'command':plan['commands'][0],'error':None,
           'log':[]}
    atomic_json(job_path(jobid),state)
    p=run(['systemd-run','--unit=ervisio-desktop-'+jobid,'--collect','--property=Type=exec',
           '/usr/bin/python3','-B',str(BACKEND),'worker',jobid],timeout=20)
    if p.returncode:
        state.update(status='failed',error=(p.stderr or p.stdout)[-800:])
        atomic_json(job_path(jobid),state)
        refuse('Could not start package operation: '+state['error'])
    return {'ok':True,'id':jobid}


def worker(jobid):
    require_root()
    path=job_path(jobid)
    state=json.loads(path.read_text())
    if state.get('status')!='queued': refuse('Job already handled')
    with open(ROOT/'operation.lock','a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        state['status']='running'; state['started']=int(time.time()); atomic_json(path,state)
        manager=distro()['manager']
        cmd=state['command']
        if not isinstance(cmd,list) or not cmd or cmd[0]!=MANAGERS.get(manager): refuse('Invalid job command')
        if cmd!=commands_for(manager,state['operation'],state['packages'])[0]: refuse('Job command verification failed')
        try:
            pre={p:installed(manager,p) for p in state['packages']}
            environ=os.environ.copy()
            environ.update({'DEBIAN_FRONTEND':'noninteractive','NEEDRESTART_MODE':'a','LC_ALL':'C'})
            proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                                  bufsize=1,env=environ,stdin=subprocess.DEVNULL)
            lines=[]
            for line in proc.stdout or []:
                lines.append(line.rstrip()[:500])
                if len(lines)>80: lines=lines[-80:]
                if len(lines)%8==0:
                    state['log']=lines[-55:]; atomic_json(path,state)
            rc=proc.wait(timeout=2700)
            state['log']=lines[-70:]; state['exitCode']=rc
            if rc: raise RuntimeError('Package manager exited with code '+str(rc))
            after={p:installed(manager,p) for p in state['packages']}
            owner=owner_data()
            old=set(owner.get(state['desktop'],[]))
            if state['operation']=='install':
                old.update(p for p in state['packages'] if after[p] and not pre[p])
            else:
                old.difference_update(p for p in state['packages'] if not after[p])
            if old: owner[state['desktop']]=sorted(old)
            else: owner.pop(state['desktop'],None)
            atomic_json(OWNED,owner,0o644)
            state['status']='done'
        except Exception as exc:
            state['status']='failed';state['error']=str(exc)[-900:]
        finally:
            state['ended']=int(time.time());atomic_json(path,state)


def control(action,arg):
    linux();require_root()
    if action in ('graphical','text'):
        if arg!='-': refuse('Unexpected argument')
        target = 'graphical.target' if action=='graphical' else 'multi-user.target'
        run(['systemctl','set-default',target],check=True)
        return {'ok':True,'message':'Next boot target: '+target+' (no immediate session termination)'}
    if action.startswith('dm-'):
        if arg not in DMS: refuse('Unknown display manager')
        verb=action[3:]
        if verb not in ('enable','disable','start','stop'): refuse('Invalid display manager action')
        if verb in ('enable','start') and not any(installed(distro()['manager'],p) for p in
           ({'gdm':['gdm3','gdm'],'sddm':['sddm'],'lightdm':['lightdm'],'lxdm':['lxdm']}[arg])):
            refuse('Display manager not installed')
        if verb=='enable':
            # Display-manager.service is frequently an alias, so enable -f allows a clean switch.
            run(['systemctl','enable','--force',DMS[arg]],check=True)
        else:
            run(['systemctl',verb,DMS[arg]],check=True)
        return {'ok':True,'message':f'{DMS[arg]}: {verb} completed'}
    if action=='session-end':
        if not re.fullmatch('[A-Za-z0-9_.-]{1,32}',arg): refuse('Invalid session ID')
        if arg not in [s['id'] for s in sessions()]: refuse('Session no longer exists')
        run(['loginctl','terminate-session',arg],check=True)
        return {'ok':True,'message':'Session '+arg+' terminated'}
    refuse('Invalid control action')


def main():
    try:
        if len(sys.argv)<2: refuse('Usage: desktop_bridge.py status|plan|apply|worker|control')
        mode,*args=sys.argv[1:]
        if mode=='status' and not args: result=status()
        elif mode=='plan' and len(args)==5: result=make_plan(*args)
        elif mode=='apply' and len(args)==6: result=start_job(args)
        elif mode=='worker' and len(args)==1:
            worker(args[0]);return
        elif mode=='control' and len(args)==2: result=control(*args)
        else: refuse('Invalid command or arguments')
        output(result)
    except Exception as exc:
        output({'ok':False,'error':str(exc)[-1200:]})
        sys.exit(1)

if __name__=='__main__': main()
