import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if sys.platform == 'win32':
    raise unittest.SkipTest('The optional startup manager uses POSIX file locks.')

BASE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('manager', BASE / 'automation/pdf2zh_patch_manager.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
# Minimal classifier fixture reproducing the upstream NewCM false positive.
# The guard extracts this function without importing the rest of BabelDOC.
ORIGINAL = '''@functools.cache
def is_formulas_font(name, pattern):
    precise = (r"^(Math"
               r"|.*NewCM.*"
               r"|.*NewComputerModern.*"
               r")$")
    name = name.split('+')[-1]
    return bool(re.match(precise, name) or re.match(pattern or r".*(Math|Mono).*", name))
'''

class PatchTests(unittest.TestCase):
    def test_original_repaired_and_repeated_application_is_noop(self):
        fixed, changed = m.repaired_source(ORIGINAL)
        self.assertTrue(changed)
        m.font_checks(fixed)
        self.assertEqual((fixed, False), m.repaired_source(fixed))

    def test_unrecognized_change_is_rejected(self):
        unknown = ORIGINAL.replace('r"|.*NewCM.*"', 'r"|.*NewC.*"')
        with self.assertRaises(ValueError):
            m.repaired_source(unknown)

    def test_correct_upstream_rewrite_needs_no_patch(self):
        source = '''def is_formulas_font(name, pattern):
    name = name.split('+')[-1]
    return ('Math' in name) or (pattern is None and 'Mono' in name)
'''
        self.assertEqual((source, False), m.repaired_source(source))

    def test_file_backup_repair_and_idempotence(self):
        with tempfile.TemporaryDirectory() as root:
            env = Path(root)
            helper = env / 'formular_helper.py'
            helper.write_text(ORIGINAL)
            class Dist:
                def locate_file(self, name): return helper
            with patch.object(m, 'distribution', return_value=Dist()), patch.object(m, 'version', return_value='test'):
                with self.assertRaises(RuntimeError):
                    m.ensure_patch(env, allow_repair=False)
                self.assertEqual(ORIGINAL, helper.read_text())
                m.ensure_patch(env, allow_repair=True)
                m.font_checks(helper.read_text())
                backups = list(env.glob('*.before-newcm-*'))
                self.assertEqual(1, len(backups))
                self.assertEqual(ORIGINAL, backups[0].read_text())
                before = helper.stat().st_mtime_ns
                m.ensure_patch(env, allow_repair=True)
                self.assertEqual(before, helper.stat().st_mtime_ns)

    def test_failed_patch_leaves_original_file_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            env = Path(root)
            helper = env / 'formular_helper.py'
            unknown = ORIGINAL.replace('r"|.*NewCM.*"', 'r"|.*NewC.*"')
            helper.write_text(unknown)
            class Dist:
                def locate_file(self, name): return helper
            with patch.object(m, 'distribution', return_value=Dist()), patch.object(m, 'version', return_value='test'):
                with self.assertRaises(ValueError):
                    m.ensure_patch(env, allow_repair=True)
            self.assertEqual(unknown, helper.read_text())
            self.assertEqual([], list(env.glob('*.before-newcm-*')))

    def test_pypi_releases_filter_python_yanked_prerelease_and_bounds(self):
        data = {'releases': {
            '0.6.2': [{'requires_python': '>=3.10', 'yanked': False}],
            '0.6.4': [{'requires_python': '>=3.10', 'yanked': False}],
            '0.6.5': [{'requires_python': '>=99', 'yanked': False}],
            '0.6.6': [{'yanked': True}],
            '0.6.7rc1': [{'yanked': False}],
            '0.7.0': [{'yanked': False}],
        }}
        with patch.object(m, 'urlopen', return_value=io.BytesIO(json.dumps(data).encode())):
            self.assertEqual(m.Version('0.6.4'), m.latest_compatible('BabelDOC', m.SpecifierSet('>=0.6.2,<0.7')))

    def test_update_success_and_failures_preserve_or_restore_environment(self):
        real_run = subprocess.run
        for outcome in ['success', 'install_failure', 'verify_failure']:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as root:
                server = Path(root) / 'server'
                env = server / 'zotero-pdf2zh-next-venv'
                (env / 'bin').mkdir(parents=True)
                (env / 'marker').write_text('working-patched')
                (server / 'config').mkdir()
                (server / 'config/venv.json').write_text(json.dumps({
                    'pdf2zh_next': {'uv': {'packages': ['pdf2zh_next>=2.9.0,<3.0.0', 'flask']}}
                }))
                def run(cmd, **kwargs):
                    if cmd[0] == '/bin/cp':
                        return real_run(cmd, **kwargs)
                    if cmd[:3] == ['/fake/uv', 'pip', 'install']:
                        self.assertIn('pdf2zh_next>=2.9.0,<3.0.0', cmd)
                        self.assertIn('--upgrade-package', cmd)
                        (env / 'marker').write_text('updated-candidate')
                        if outcome == 'install_failure':
                            raise subprocess.CalledProcessError(1, cmd)
                        return subprocess.CompletedProcess(cmd, 0)
                    if '--verify' in cmd:
                        if outcome == 'verify_failure':
                            raise subprocess.CalledProcessError(1, cmd)
                        return subprocess.CompletedProcess(cmd, 0)
                    self.fail(f'Unexpected command: {cmd}')
                with patch.object(m, 'listening', return_value=False), patch.object(m, 'uv_binary', return_value='/fake/uv'), patch.object(m.subprocess, 'run', side_effect=run):
                    success = m.update_runtime(server, env)
                self.assertEqual(outcome == 'success', success)
                state = Path(root) / '.pdf2zh-local'
                if success:
                    self.assertEqual('updated-candidate', (env / 'marker').read_text())
                    backup, = state.glob('venv-before-update-*')
                    self.assertEqual('working-patched', (backup / 'marker').read_text())
                else:
                    self.assertEqual('working-patched', (env / 'marker').read_text())
                    failed, = state.glob('venv-failed-update-*')
                    self.assertEqual('updated-candidate', (failed / 'marker').read_text())

    def test_running_server_blocks_dependency_updates(self):
        with tempfile.TemporaryDirectory() as root:
            server = Path(root) / 'server'
            with patch.object(m, 'listening', return_value=True):
                self.assertFalse(m.update_runtime(server, server / 'env'))
            self.assertFalse((Path(root) / '.pdf2zh-local').exists())

    def test_source_installs_are_not_replaced_by_pypi(self):
        for source_package in ['pdf2zh-next', 'BabelDOC']:
            with self.subTest(package=source_package):
                class Dist:
                    requires = ['BabelDOC>=0.6.2,<0.7']

                    def __init__(self, name):
                        self.name = name

                    def read_text(self, name):
                        return '{"url": "https://example.org/fork.git"}' if self.name == source_package else None

                with (patch.object(m, 'distribution', side_effect=Dist),
                      patch.object(m, 'runtime_requirements', return_value=['pdf2zh-next>=2.9']),
                      patch.object(m, 'latest_compatible') as query):
                    self.assertFalse(m.check_runtime_updates(Path('server')))
                    query.assert_not_called()

    def test_failed_backup_never_starts_installation(self):
        with tempfile.TemporaryDirectory() as root:
            server = Path(root) / 'server'
            env = server / 'env'
            env.mkdir(parents=True)
            (env / 'marker').write_text('working')
            with (patch.object(m, 'listening', return_value=False),
                  patch.object(m, 'runtime_requirements', return_value=['pdf2zh-next']),
                  patch.object(m, 'uv_binary', return_value='/fake/uv'),
                  patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run,
                  patch.object(m.shutil, 'copytree', side_effect=OSError('disk full'))):
                self.assertFalse(m.update_runtime(server, env))
            self.assertEqual('working', (env / 'marker').read_text())
            self.assertEqual(1, run.call_count)
            self.assertEqual('/bin/cp', run.call_args.args[0][0])

if __name__ == '__main__':
    unittest.main(verbosity=2)
