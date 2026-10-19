import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if sys.platform == 'win32':
    raise unittest.SkipTest('Optional startup manager uses POSIX file locks.')
BASE=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('layout_manager',BASE/'automation/pdf2zh_patch_manager.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
BUNDLE=BASE/'automation/babeldoc-patches'

class LayoutManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=Path(self.temp.name)
        self.root=self.env/'babeldoc/format/pdf/document_il'
        (self.root/'midend').mkdir(parents=True);(self.root/'utils').mkdir()
        payload=(BUNDLE/'paragraph_finder.py').read_text()
        # Recover the reviewed upstream source from the exact added hooks.
        payload=payload.replace('from babeldoc.format.pdf.document_il.utils.structure_helper import merge_word_fragments\n','')
        payload=payload.replace('from babeldoc.format.pdf.document_il.utils.structure_helper import preserve_code_blocks\n','').replace('from babeldoc.format.pdf.document_il.utils.structure_helper import split_numbered_lists\n','')
        payload=payload.replace('        self._preserved_code_blocks = 0\n','').replace('                if self._preserved_code_blocks:\n                    return\n','')
        payload=payload.replace('        if not self.translation_config.ocr_workaround:\n            merge_word_fragments(\n                paragraphs, self.update_paragraph_data, self.update_line_data\n            )\n            self._preserved_code_blocks += preserve_code_blocks(page, paragraphs)\n\n','')
        payload=payload.replace('        if not self.translation_config.ocr_workaround:\n            split_numbered_lists(page, paragraphs, self.update_paragraph_data)\n            for paragraph in paragraphs:\n                if not paragraph.debug_id:\n                    paragraph.debug_id = generate_base58_id()\n\n','')
        self.target=self.root/'midend/paragraph_finder.py';self.target.write_text(payload)
        self.original=self.target.read_bytes()
        class Dist:
            def locate_file(_,name):return self.root
        self.dist=patch.object(m,'distribution',return_value=Dist());self.dist.start()
        self.port=patch.object(m,'listening',return_value=False);self.port.start()
        self.check=patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0));self.check.start()
    def tearDown(self):
        self.dist.stop();self.port.stop();self.check.stop();self.temp.cleanup()
    def test_replay_is_idempotent_and_readonly_mode_never_writes(self):
        with self.assertRaises(RuntimeError):m.ensure_layout_patch(self.env,allow_repair=False)
        self.assertEqual(self.original,self.target.read_bytes())
        m.ensure_layout_patch(self.env,allow_repair=True)
        before=self.target.stat().st_mtime_ns
        m.ensure_layout_patch(self.env,allow_repair=False)
        self.assertEqual(before,self.target.stat().st_mtime_ns)
        self.assertEqual(1,len(list(self.target.parent.glob('*.before-layout-*'))))
    def test_unknown_upstream_source_is_left_untouched(self):
        self.target.write_bytes(self.original+b'\n# upstream change\n')
        before=self.target.read_bytes()
        with self.assertRaises(RuntimeError):m.ensure_layout_patch(self.env,allow_repair=True)
        self.assertEqual(before,self.target.read_bytes())
        self.assertFalse((self.root/'utils/structure_helper.py').exists())
    def test_failed_behavior_checks_restore_all_files(self):
        self.check.stop();self.check=patch.object(m.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['check']));self.check.start()
        with self.assertRaises(subprocess.CalledProcessError):m.ensure_layout_patch(self.env,allow_repair=True)
        self.assertEqual(self.original,self.target.read_bytes())
        self.assertFalse((self.root/'utils/structure_helper.py').exists())
    def test_live_server_blocks_changes(self):
        self.port.stop();self.port=patch.object(m,'listening',return_value=True);self.port.start()
        with self.assertRaises(RuntimeError):m.ensure_layout_patch(self.env,allow_repair=True)
        self.assertEqual(self.original,self.target.read_bytes())
    def test_corrupt_payload_is_rejected_before_any_write(self):
        local = self.env / 'bundle-copy'
        shutil.copytree(BUNDLE, local / 'babeldoc-patches')
        (local / 'babeldoc-patches/structure_helper.py').write_text('# damaged')
        with patch.object(m, '__file__', str(local / 'manager.py')):
            with self.assertRaises(RuntimeError):
                m.ensure_layout_patch(self.env, allow_repair=True)
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertFalse((self.root / 'utils/structure_helper.py').exists())

    def test_partial_write_failure_restores_original_files(self):
        atomic = m.atomic_write
        calls = 0
        def fail_second(path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated interrupted write')
            atomic(path, content)
        with patch.object(m, 'atomic_write', side_effect=fail_second):
            with self.assertRaises(OSError):
                m.ensure_layout_patch(self.env, allow_repair=True)
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertFalse((self.root / 'utils/structure_helper.py').exists())

    def test_foreign_environment_is_rejected(self):
        with self.assertRaises(RuntimeError):m.ensure_layout_patch(self.env/'other',allow_repair=True)
        self.assertEqual(self.original,self.target.read_bytes())

if __name__=='__main__':unittest.main()
