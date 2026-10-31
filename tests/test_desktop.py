import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('desktop_bridge', Path(__file__).resolve().parents[1] / 'plugin' / 'desktop_bridge.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

class FakeProcess:
    def __init__(self, out='', code=0, err=''):
        self.stdout=out
        self.stderr=err
        self.returncode=code

class DesktopTests(unittest.TestCase):
    def test_windows_explicitly_unsupported(self):
        with patch.object(d.sys,'platform','win32'):
            self.assertFalse(d.status()['supported'])
            with self.assertRaisesRegex(ValueError,'only on Linux'):
                d.make_plan('install','gnome','minimal','','managed')

    def test_custom_options_are_whitelisted(self):
        self.assertEqual(d.groups('custom','files,login'),['core','files','login'])
        with self.assertRaisesRegex(ValueError,'custom component'):
            d.groups('custom','files,files')
        with self.assertRaises(ValueError): d.groups('custom','../../bash')
        with self.assertRaises(ValueError): d.groups('custom','files,arbitrary')

    def test_request_rejects_bogus_desktops_and_scopes(self):
        with self.assertRaises(ValueError): d.validate_request('install','rm -rf /','standard','','managed')
        with self.assertRaises(ValueError): d.validate_request('install','gnome','standard','','system')

    def test_package_plan_and_hash_stable(self):
        with patch.object(d,'linux'), patch.object(d,'distro',return_value={'id':'debian','name':'Debian','manager':'apt'}),\
             patch.object(d,'installed',return_value=False), patch.object(d,'available',return_value=True),\
             patch.object(d,'run',return_value=FakeProcess('Inst gnome-shell\nInst gnome-session\n')):
            plan = d.make_plan('install','gnome','standard','','managed')
            self.assertEqual(plan['packages'],['gnome-shell','gnome-session','nautilus','gnome-terminal','gdm3'])
            self.assertEqual(len(plan['planHash']),64)
            self.assertEqual(plan['planHash'],d.make_plan('install','gnome','standard','','managed')['planHash'])
            self.assertTrue(all(isinstance(x,str) for x in plan['commands'][0]))

    def test_apt_install_that_removes_existing_package_is_blocked(self):
        with patch.object(d,'linux'),patch.object(d,'distro',return_value={'manager':'apt'}),\
             patch.object(d,'installed',return_value=False),patch.object(d,'available',return_value=True),\
             patch.object(d,'run',return_value=FakeProcess('Remv firefox [1]\nInst gnome-shell\n')):
            with self.assertRaisesRegex(ValueError,'would remove existing packages'):
                d.make_plan('install','gnome','minimal','','managed')

    def test_managed_remove_only_owned_packages(self):
        with patch.object(d,'linux'),patch.object(d,'distro',return_value={'manager':'apt'}),\
             patch.object(d,'installed',return_value=True),patch.object(d,'available',return_value=True),\
             patch.object(d,'owner_data',return_value={'gnome':['gnome-shell']}),\
             patch.object(d,'run',return_value=FakeProcess('Remv gnome-shell\n')):
            plan=d.make_plan('remove','gnome','full','','managed')
            self.assertEqual(plan['packages'],['gnome-shell'])

    def test_system_remove_does_not_purge_login_manager(self):
        with patch.object(d,'linux'),patch.object(d,'distro',return_value={'manager':'apt'}),\
             patch.object(d,'installed',return_value=True),patch.object(d,'available',return_value=True),\
             patch.object(d,'run',return_value=FakeProcess('Remv gnome-shell\n')):
            plan=d.make_plan('remove','gnome','minimal','','system')
            self.assertNotIn('gdm3',plan['packages'])
            self.assertIn('gnome-shell',plan['packages'])

    def test_mismatched_plan_hash_refused_without_starting_jobs(self):
        with patch.object(d,'require_root'),patch.object(d,'make_plan',return_value={'planHash':'expected','packages':['gnome-shell']}):
            with self.assertRaisesRegex(ValueError,'system changed'):
                d.start_job(['install','gnome','minimal','','managed','bad-hash'])

    def test_no_arbitrary_system_commands(self):
        with patch.object(d,'linux'),patch.object(d,'require_root'):
            with self.assertRaisesRegex(ValueError,'Invalid control action'):
                d.control('shell','/bin/sh')
            with self.assertRaisesRegex(ValueError,'Unknown display manager'):
                d.control('dm-start','sshd')

    def test_json_transaction_state_atomic(self):
        with tempfile.TemporaryDirectory() as dirname:
            p=Path(dirname)/'jobs'/'test.json'
            d.atomic_json(p,{'status':'done'})
            self.assertEqual(p.read_text(),'{'+'"status": "done"'+'}')
            self.assertEqual(p.stat().st_mode & 0o777,0o644)

if __name__=='__main__':unittest.main()
