import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('desktop_gateway', Path(__file__).resolve().parents[1] / 'plugin' / 'desktop_gateway.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class UDSConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=5)
        self.path = path
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(self.path)

class FakeProcess:
    pid = 9999999
    def poll(self): return None

class GatewayTests(unittest.TestCase):
    def test_only_known_desktops_and_valid_geometry(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported resolution'):
            g.start(os.getuid(), 'xfce', '9999x9999')
        with self.assertRaisesRegex(ValueError, 'not supported'):
            g.start(os.getuid(), '../../bin/bash', '1280x720')
        with patch.object(g, 'x11_sessions', return_value={}), patch.object(g.shutil, 'which', return_value='/bin/x'):
            self.assertEqual(g.can_start('xfce')[0], False)
            self.assertIn('X11', g.can_start('xfce')[1])

    def test_stream_and_input_relay_are_duplex_and_local(self):
        with tempfile.TemporaryDirectory() as folder:
            gateway_path = str(Path(folder) / 'gateway.sock')
            vnc_path = str(Path(folder) / 'vnc.sock')
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            upstream.bind(vnc_path)
            upstream.listen(1)
            received=[]
            ready=threading.Event()
            def fake_vnc():
                client,_ = upstream.accept()
                try:
                    client.sendall(b'RFB 003.008\n')
                    ready.set()
                    received.append(client.recv(4))
                finally:
                    client.close()
            worker=threading.Thread(target=fake_vnc,daemon=True)
            worker.start()
            server=g.UnixHTTPServer(gateway_path, g.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            uid = 1001 if os.getuid() < 1000 else os.getuid()
            sess = g.Session(uid,'xfce','1280x720',180,FakeProcess(),FakeProcess(),vnc_path)
            g.SESSIONS[uid] = sess
            original = g.Handler.user_id
            g.Handler.user_id = lambda self: uid
            read = UDSConnection(gateway_path)
            try:
                read.request('GET','/v1/stream',headers={'X-Desktop-Token':sess.token})
                response=read.getresponse()
                self.assertEqual(response.status,200)
                self.assertEqual(response.read(12),b'RFB 003.008\n')
                self.assertTrue(ready.wait(3))
                write=UDSConnection(gateway_path)
                write.request('POST','/v1/input',body=b'PING',headers={'X-Desktop-Token':sess.token})
                ack=write.getresponse()
                self.assertEqual(ack.status,200)
                ack.read()
                write.close()
                worker.join(timeout=3)
                self.assertEqual(received,[b'PING'])
            finally:
                g.Handler.user_id = original
                read.close()
                g.SESSIONS.pop(uid,None)
                server.shutdown()
                server.server_close()
                upstream.close()
                thread.join(timeout=3)

    def test_windows_install_blocked(self):
        with patch.object(g.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(RuntimeError, 'Linux'):
                g.install()

    def test_unix_http_is_bound_to_peer_uid_and_token(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'gateway.sock')
            server = g.UnixHTTPServer(path, g.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            original = g.Handler.user_id
            try:
                if os.getuid() < 1000:
                    g.Handler.user_id = lambda self: 1001
                # Simulate Ervisio's unprivileged user bridge when tests run as root.
                regular_user = 1001 if os.getuid() < 1000 else os.getuid()
                with patch.object(g.Handler, 'user_id', return_value=regular_user), patch.object(g, 'vnc_binary', return_value='/usr/bin/Xvnc'), patch.object(g,'can_start',return_value=(True,None)):
                    conn = UDSConnection(path)
                    conn.request('GET','/v1/status')
                    response = conn.getresponse()
                    self.assertEqual(response.status,200)
                    data=json.loads(response.read())
                    self.assertTrue(data['gateway'])
                    self.assertIsNone(data['session'])
                    conn.close()
                proc = FakeProcess()
                sess = g.Session(regular_user,'xfce','1280x720',180,proc,proc,'/bad/file')
                g.SESSIONS[regular_user] = sess
                conn = UDSConnection(path)
                conn.request('POST','/v1/input', body=b'test', headers={'X-Desktop-Token':'0'*32})
                response = conn.getresponse()
                self.assertEqual(response.status,403)
                response.read()
                conn.close()
            finally:
                g.Handler.user_id = original
                g.SESSIONS.pop(regular_user,None)
                server.shutdown()
                server.server_close()
                thread.join(timeout=4)

if __name__ == '__main__': unittest.main()
