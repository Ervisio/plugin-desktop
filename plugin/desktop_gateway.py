#!/usr/bin/env python3
"""Ervisio Desktop browser gateway: authenticated Unix-socket -> per-user Xvnc bridge.

The socket is accessible to local users, but SO_PEERCRED binds every request to its
real Linux uid. The desktop VNC socket is per-user mode 0600 and never listens on TCP.
Desktop processes run as that uid, not root. This is NOT a connection to an existing
physical Wayland desktop; it creates separate X11 sessions.
"""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

SOCKET = Path('/run/ervisio-desktop/gateway.sock')
RUNTIME = Path('/run/ervisio-desktop')
SERVICE = Path('/etc/systemd/system/ervisio-desktop-gateway.service')
DISPLAY_START, DISPLAY_END = 160, 229
DESKTOPS = {
    'gnome': ('gnome-session', 'GNOME'),
    'plasma': ('startplasma-x11', 'KDE'),
    'cinnamon': ('cinnamon-session', 'X-Cinnamon'),
    'xfce': ('startxfce4', 'XFCE'),
    'mate': ('mate-session', 'MATE'),
    'lxqt': ('startlxqt', 'LXQt'),
}


def send_json(obj):
    print(json.dumps(obj, separators=(',', ':'), ensure_ascii=False), flush=True)


def exec_cmd(argv, timeout=600):
    p = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout, stdin=subprocess.DEVNULL,
                       env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'a'})
    if p.returncode:
        raise RuntimeError((p.stdout or 'Command failed')[-1800:])
    return p.stdout


def vnc_binary():
    return shutil.which('Xtigervnc') or shutil.which('Xvnc')


def install():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Requires Linux and administrator rights')
    if not Path('/run/systemd/system').exists():
        raise RuntimeError('systemd is required')
    if not vnc_binary():
        manager = next((x for x in ('apt-get', 'dnf', 'pacman', 'zypper') if shutil.which(x)), None)
        packages = {'apt-get': ['tigervnc-standalone-server', 'dbus-x11', 'xauth'],
                    'dnf': ['tigervnc-server', 'dbus-daemon', 'xorg-x11-xauth'],
                    'pacman': ['tigervnc', 'dbus', 'xorg-xauth'],
                    'zypper': ['tigervnc', 'dbus-1', 'xauth']}
        if not manager:
            raise RuntimeError('Install TigerVNC manually: package manager not supported')
        cmd = {'apt-get': ['apt-get', '-y', 'install'], 'dnf': ['dnf', '-y', 'install'],
               'pacman': ['pacman', '--noconfirm', '-S', '--needed'],
               'zypper': ['zypper', '--non-interactive', 'install']}[manager]
        exec_cmd([*cmd, *packages[manager]], timeout=600)
    if not vnc_binary():
        raise RuntimeError('TigerVNC executable unavailable after installation')
    if not shutil.which('xauth'):
        raise RuntimeError('xauth is required to secure X11 access')
    if not shutil.which('dbus-run-session'):
        raise RuntimeError('dbus-run-session is required')
    # The service owns only a local unix socket; no TCP listener is created.
    unit = f'''[Unit]\nDescription=Ervisio Desktop local browser gateway\nAfter=network.target\n\n[Service]\nType=simple\nExecStart=/usr/bin/python3 -B /var/lib/ervisio/plugins/desktop/desktop_gateway.py serve\nRestart=on-failure\nRestartSec=3\nNoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=read-only\nReadWritePaths=/run/ervisio-desktop /tmp\nPrivateTmp=no\nRestrictAddressFamilies=AF_UNIX\n\n[Install]\nWantedBy=multi-user.target\n'''
    # Child desktop processes must access user home; ProtectHome would block them.
    unit = unit.replace('ProtectHome=read-only\n', '')
    # Xvnc/X11 and session processes need socket access and can use AF_INET for apps.
    unit = unit.replace('RestrictAddressFamilies=AF_UNIX\n', '')
    unit = unit.replace('NoNewPrivileges=yes\n', '')  # user switch via Popen(user=...)
    unit = unit.replace('ProtectSystem=strict\n', '')  # desktop processes need host paths
    unit = unit.replace('ReadWritePaths=/run/ervisio-desktop /tmp\n', '')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(unit)
    exec_cmd(['systemctl', 'daemon-reload'], timeout=25)
    exec_cmd(['systemctl', 'enable', '--now', 'ervisio-desktop-gateway.service'], timeout=35)
    return {'ok': True, 'socket': str(SOCKET), 'vnc': vnc_binary(), 'message': 'Desktop gateway installed and started'}


def owned_run_dir(uid):
    base = RUNTIME / ('u' + str(uid))
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    user = pwd.getpwuid(uid)
    os.chown(base, uid, user.pw_gid)
    os.chmod(base, 0o700)
    return base


def user_spawn(uid, argv, env, logfile):
    u = pwd.getpwuid(uid)
    log = open(logfile, 'ab', buffering=0)
    try:
        return subprocess.Popen(argv, env=env, cwd=pwd.getpwuid(uid).pw_dir, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True, user=uid,
                                group=u.pw_gid, extra_groups=os.getgrouplist(u.pw_name, u.pw_gid))
    finally:
        log.close()


class Session:
    def __init__(self, uid, desktop, geometry, display, xvnc, child, rfb_socket):
        self.uid = uid
        self.desktop = desktop
        self.geometry = geometry
        self.display = display
        self.xvnc = xvnc
        self.child = child
        self.rfb_socket = str(rfb_socket)
        self.token = secrets.token_hex(16)
        self.started = int(time.time())
        self.conn = None
        self.send_lock = threading.Lock()
        self.conn_lock = threading.Lock()

    def live(self):
        return self.xvnc.poll() is None

    def info(self):
        memory, cpu = 0, 0.0
        try:
            for p in (self.xvnc, self.child):
                if p.poll() is not None:
                    continue
                result = subprocess.run(['ps', '-o', 'rss=', '-o', 'pcpu=', '-p', str(p.pid)],
                                        text=True, capture_output=True, timeout=3)
                for line in result.stdout.splitlines():
                    cells = line.split()
                    if len(cells) == 2:
                        memory += int(float(cells[0])) * 1024
                        cpu += float(cells[1].replace(',', '.'))
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
        return {'desktop': self.desktop, 'geometry': self.geometry, 'display': ':' + str(self.display),
                'started': self.started, 'token': self.token, 'running': self.live(),
                'memoryBytes': memory, 'cpuPercent': round(cpu, 1)}

    def disconnect(self):
        with self.conn_lock:
            old, self.conn = self.conn, None
        if old:
            try: old.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            old.close()

    def terminate(self):
        self.disconnect()
        for proc in (self.child, self.xvnc):
            if proc.poll() is None:
                try: os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError: pass


SESSIONS: dict[int, Session] = {}
SESSIONS_LOCK = threading.RLock()


def x11_sessions():
    out = {}
    for entry in Path('/usr/share/xsessions').glob('*.desktop'):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,70}', entry.stem):
            continue
        try:
            fields = dict(line.split('=', 1) for line in entry.read_text(errors='replace').splitlines()
                          if '=' in line and not line.startswith('#'))
            if fields.get('Hidden', '').lower() == 'true' or not fields.get('Exec'):
                continue
            # Session launchers are installed root-owned by system packages.
            # Never interpret Exec as a shell script: parse individual argv items.
            argv = shlex.split(fields['Exec'])
            if not argv or any('%' in part for part in argv):
                continue
            out[entry.stem] = {'name': fields.get('Name', entry.stem),
                               'exec': argv, 'desktop': fields.get('DesktopNames', entry.stem)}
        except (OSError, ValueError):
            continue
    return out


def can_start(desktop):
    found = x11_sessions()
    if desktop not in DESKTOPS:
        if not isinstance(desktop, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,70}', desktop) or desktop not in found:
            return False, 'This environment is not supported for virtual X11 sessions'
        command = found[desktop]['exec'][0]
        return (True, None) if shutil.which(command) else (False, command + ' is missing')
    binary = DESKTOPS[desktop][0]
    if not shutil.which(binary):
        return False, f'{binary} is missing'
    aliases = {'gnome': ['gnome'], 'plasma': ['plasma', 'plasmax11'],
               'cinnamon': ['cinnamon'], 'xfce': ['xfce'], 'mate': ['mate'], 'lxqt': ['lxqt']}
    if not any(alias in found for alias in aliases[desktop]):
        return False, 'No compatible X11 session is installed (Wayland-only desktops are not supported yet)'
    return True, None


def start(uid, desktop, geometry):
    if not re.fullmatch(r'(800|1024|1280|1366|1600|1920)x(600|720|768|900|1080)', geometry):
        raise ValueError('Unsupported resolution')
    good, why = can_start(desktop)
    if not good:
        raise ValueError(why)
    if not vnc_binary():
        raise ValueError('TigerVNC is not installed. Use Install gateway first.')
    with SESSIONS_LOCK:
        existing = SESSIONS.get(uid)
        if existing and existing.live():
            raise ValueError('A virtual desktop is already running for this account')
        if existing:
            existing.terminate()
        folder = owned_run_dir(uid)
        logfile = folder / 'desktop.log'
        user = pwd.getpwuid(uid)
        generic = x11_sessions().get(desktop, {})
        session_binary = DESKTOPS[desktop][0] if desktop in DESKTOPS else generic['exec'][0]
        session_args = [session_binary] if desktop in DESKTOPS else generic['exec']
        current_desktop = DESKTOPS[desktop][1] if desktop in DESKTOPS else generic.get('desktop', desktop)
        base_env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': user.pw_dir,
                    'USER': user.pw_name, 'LOGNAME': user.pw_name,
                    'LANG': os.environ.get('LANG', 'C.UTF-8'),
                    'XDG_SESSION_TYPE': 'x11', 'XDG_CURRENT_DESKTOP': current_desktop}
        runtime = Path('/run/user') / str(uid)
        if runtime.is_dir():
            base_env['XDG_RUNTIME_DIR'] = str(runtime)
        for n in range(DISPLAY_START, DISPLAY_END):
            if (Path('/tmp/.X11-unix') / ('X' + str(n))).exists():
                continue
            sock = folder / ('rfb-' + str(n) + '.sock')
            try: sock.unlink(missing_ok=True)
            except OSError: pass
            if not shutil.which('xauth'):
                raise RuntimeError('xauth is required; install the desktop remote runtime again')
            xauth = folder / ('authority-' + str(n))
            xauth.touch(mode=0o600)
            os.chown(xauth, uid, user.pw_gid)
            os.chmod(xauth, 0o600)
            local = subprocess.run(['xauth', '-f', str(xauth), 'add', ':' + str(n), '.', secrets.token_hex(16)],
                                   capture_output=True, text=True, timeout=10,
                                   user=uid, group=user.pw_gid, extra_groups=os.getgrouplist(user.pw_name, user.pw_gid),
                                   env=base_env)
            if local.returncode != 0:
                raise RuntimeError('Cannot secure Xauthority: ' + local.stderr[-240:])
            session_env = {**base_env, 'DISPLAY': ':' + str(n), 'XAUTHORITY': str(xauth)}
            xvnc = user_spawn(uid, [vnc_binary(), ':' + str(n), '-geometry', geometry,
                  '-depth', '24', '-rfbport', '-1', '-SecurityTypes', 'None',
                  '-rfbunixpath', str(sock), '-rfbunixmode', '0600', '-auth', str(xauth)],
                  session_env, logfile)
            for _ in range(60):
                if sock.exists() and xvnc.poll() is None:
                    break
                if xvnc.poll() is not None:
                    break
                time.sleep(0.1)
            if not sock.exists() or xvnc.poll() is not None:
                if xvnc.poll() is None: xvnc.terminate()
                continue
            session_cmd = ['dbus-run-session', '--', *session_args]
            child = user_spawn(uid, session_cmd, session_env, logfile)
            session = Session(uid, desktop, geometry, n, xvnc, child, sock)
            SESSIONS[uid] = session
            return session.info()
        raise RuntimeError('TigerVNC could not start. Check runtime log and X11 support.')


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return  # A browser closed its desktop viewer.
        return super().handle_error(request, client_address)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'ErvisioDesktop/0.1'
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        return

    def user_id(self):
        if not hasattr(socket, 'SO_PEERCRED'):
            raise RuntimeError('Linux peer credentials unavailable')
        _, uid, _ = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        pwd.getpwuid(uid)
        if uid < 1000 or uid == 65534:
            raise PermissionError('Use a regular non-root Linux account')
        return uid

    def respond(self, code, data):
        content = json.dumps(data, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(content)

    def authorized_session(self, token):
        uid = self.user_id()
        with SESSIONS_LOCK:
            sess = SESSIONS.get(uid)
            if not sess or not sess.live() or not secrets.compare_digest(sess.token, token):
                raise PermissionError('No authorized desktop session')
            return sess

    def body(self, max_bytes=64 * 1024):
        length = int(self.headers.get('Content-Length', '-1'))
        if length < 0 or length > max_bytes:
            raise ValueError('Request body is missing or too large')
        return self.rfile.read(length)

    def action(self):
        path = urlparse(self.path).path
        if path == '/v1/status' and self.command == 'GET':
            uid = self.user_id()
            with SESSIONS_LOCK:
                session = SESSIONS.get(uid)
                return self.respond(200, {'ok': True, 'gateway': True, 'vnc': bool(vnc_binary()),
                  'session': session.info() if session and session.live() else None,
                  'available': {k: can_start(k)[0] for k in [*DESKTOPS, *x11_sessions()]},
                  'unavailableReasons': {k: can_start(k)[1] for k in [*DESKTOPS, *x11_sessions()]},
                  'customSessions': {k: v['name'] for k, v in x11_sessions().items() if k not in DESKTOPS}})
        if path == '/v1/session' and self.command == 'POST':
            uid = self.user_id()
            data = json.loads(self.body(2048))
            return self.respond(200, {'ok': True, 'session': start(uid, data.get('desktop'), data.get('geometry', '1280x720'))})
        if path == '/v1/session' and self.command == 'DELETE':
            uid = self.user_id()
            with SESSIONS_LOCK:
                sess = SESSIONS.pop(uid, None)
                if sess: sess.terminate()
            return self.respond(200, {'ok': True})
        match = re.fullmatch('/v1/(stream|input)', path)
        if match:
            mode, token = match.group(1), self.headers.get('X-Desktop-Token', '')
            if not re.fullmatch('[a-f0-9]{32}', token):
                raise PermissionError('Invalid desktop viewer token')
            sess = self.authorized_session(token)
            if mode == 'stream' and self.command == 'GET':
                return self.stream(sess)
            if mode == 'input' and self.command == 'POST':
                payload = self.body(256 * 1024)
                with sess.send_lock:
                    if not sess.conn: raise PermissionError('Viewer not connected')
                    sess.conn.sendall(payload)
                return self.respond(200, {'ok': True})
        return self.respond(404, {'error': 'Unknown desktop gateway endpoint'})

    def stream(self, sess):
        wire = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        wire.connect(sess.rfb_socket)
        sess.disconnect()
        with sess.conn_lock:
            sess.conn = wire
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        try:
            while sess.live():
                chunk = wire.recv(32768)
                if not chunk: break
                self.wfile.write(('%x\r\n' % len(chunk)).encode() + chunk + b'\r\n')
                self.wfile.flush()
        except (OSError, BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with sess.conn_lock:
                if sess.conn is wire: sess.conn = None
            wire.close()
            try: self.wfile.write(b'0\r\n\r\n')
            except OSError: pass

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def dispatch(self):
        try:
            self.action()
        except PermissionError as e:
            self.close_connection = True
            self.respond(403, {'error': str(e)})
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self.close_connection = True
            self.respond(400, {'error': str(e)})
        except Exception as e:
            self.close_connection = True
            self.respond(503, {'error': str(e)[:700]})


def serve():
    if os.geteuid() != 0 or sys.platform != 'linux':
        raise RuntimeError('Gateway must run as root on Linux')
    RUNTIME.mkdir(mode=0o755, parents=True, exist_ok=True)
    try: SOCKET.unlink(missing_ok=True)
    except OSError: pass
    server = UnixHTTPServer(str(SOCKET), Handler)
    os.chmod(SOCKET, 0o666)  # Explicit SO_PEERCRED ACL, per-account isolation on each request
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        with SESSIONS_LOCK:
            for sess in SESSIONS.values(): sess.terminate()
        server.server_close()
        SOCKET.unlink(missing_ok=True)


def main():
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ('install', 'serve'):
            raise ValueError('Usage: desktop_gateway.py install|serve')
        res = install() if sys.argv[1] == 'install' else serve()
        if res is not None: send_json(res)
    except Exception as e:
        send_json({'ok': False, 'error': str(e)[:1200]})
        raise SystemExit(1)


if __name__ == '__main__':
    main()
