import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import pymupdf
from inventory.engine import run, Options, isolated_book_worker


def crash_one_book(book, options, stop, connection):
    connection.send(('progress', f'child-pid:{os.getpid()}'))
    if any(path.stem == 'B_crash' for path in book.files):
        connection.send(('progress', 'B_crash.pdf page 1'))
        os._exit(17)  # A native crash bypasses every Python exception handler.
    isolated_book_worker(book, options, stop, connection)


def stalled_worker(book, options, stop, connection):
    connection.send(('progress', 'Waiting on an unresponsive native call'))
    time.sleep(60)


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'.tmp')
        self.root=Path(self.tmp.name)
        self.books=self.root/'books'
        self.books.mkdir()
        for name,pages in [('A_good',2),('B_crash',1),('C_good',3)]:
            with pymupdf.open() as doc:
                for _ in range(pages):
                    doc.new_page().insert_text((50,80),'Title: A book about the natural world')
                doc.save(self.books/f'{name}.pdf')
        self.output=self.root/'inventory.xlsx'

    def tearDown(self):
        self.tmp.cleanup()

    def test_abrupt_child_exit_does_not_stop_single_or_parallel_scan(self):
        for workers in (1,3):
            with self.subTest(workers=workers):
                messages=[]
                with patch('inventory.engine.isolated_book_worker',crash_one_book):
                    rows,cancelled=run(self.books,self.output,
                        Options(ocr=False,online_titles=False,workers=workers),
                        lambda done,total,message:messages.append(message),resume=False)
                self.assertFalse(cancelled)
                self.assertEqual(len(rows),3)
                bad=[row for row in rows if row.get('worker_error')]
                self.assertEqual(len(bad),1)
                self.assertIn('B_crash.pdf page 1',bad[0]['worker_error'])
                self.assertEqual(bad[0]['page_count'],'N/A')
                self.assertEqual(bad[0]['image_count'],'NEEDS REVIEW')
                healthy={Path(row['sources'][0]).stem:row['page_count'] for row in rows if not row.get('worker_error')}
                self.assertEqual(healthy,{'A_good':2,'C_good':3})
                child_pids={int(m.split(':')[1]) for m in messages if m.startswith('child-pid:')}
                self.assertEqual(len(child_pids),3)
                self.assertNotIn(os.getpid(),child_pids)
                with ZipFile(self.output) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertIn(b'Worker exited without a result',archive.read('xl/worksheets/sheet2.xml'))
                self.assertEqual(json.loads(self.output.with_suffix('.audit.json').read_text(encoding='utf-8'))['completed'],3)

    def test_stalled_worker_is_bounded_and_flagged(self):
        for path in self.books.glob('*.pdf'):
            if path.stem!='B_crash':path.unlink()
        started=time.monotonic()
        with patch('inventory.engine.isolated_book_worker',stalled_worker), \
             patch('inventory.engine.WORKER_STALL_SECONDS',1):
            rows,_=run(self.books,self.output,Options(ocr=False,online_titles=False,workers=1))
        self.assertLess(time.monotonic()-started,15)
        self.assertEqual(len(rows),1)
        self.assertIn('No page progress',rows[0]['worker_error'])

    def test_cancel_kills_a_stalled_native_call_and_saves_partial(self):
        stop=threading.Event()
        def progress(done,total,message):
            if message=='Waiting on an unresponsive native call':stop.set()
        started=time.monotonic()
        with patch('inventory.engine.isolated_book_worker',stalled_worker), \
             patch('inventory.engine.CANCEL_GRACE_SECONDS',.1):
            rows,cancelled=run(self.books,self.output,Options(ocr=False,online_titles=False,workers=1),progress,stop)
        self.assertLess(time.monotonic()-started,15)
        self.assertTrue(cancelled)
        self.assertEqual(rows,[])
        self.assertTrue(self.output.is_file())

    def test_partial_excel_survives_a_later_coordinator_error(self):
        seen=[]
        def complete(row):
            seen.append(row)
            self.assertTrue(self.output.with_name('inventory.partial.xlsx').is_file())
            if len(seen)==2:raise RuntimeError('Coordinator failed after saving rows')
        with self.assertRaisesRegex(RuntimeError,'Coordinator failed'):
            run(self.books,self.output,Options(ocr=False,online_titles=False,workers=1),on_book_complete=complete)
        partial=self.output.with_name('inventory.partial.xlsx')
        with ZipFile(partial) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn(b'PARTIAL - IN PROGRESS',archive.read('xl/worksheets/sheet2.xml'))
        self.assertEqual(len(seen),2)

    def test_resume_ignores_extracted_model_path_and_timestamp(self):
        (self.books/'B_crash.pdf').unlink()
        model1=self.root/'model1';model2=self.root/'model2'
        model1.mkdir();model2.mkdir()
        for folder in (model1,model2):
            (folder/'eng.traineddata').write_bytes(b'identical model bytes')
        run(self.books,self.output,Options(ocr=False,online_titles=False,workers=1,models=str(model1)))
        with patch('inventory.engine.isolated_book_worker',side_effect=AssertionError('Cached PDFs must not spawn')):
            rows,_=run(self.books,self.output,Options(ocr=False,online_titles=False,workers=2,models=str(model2)))
        self.assertEqual(len(rows),2)
        self.assertFalse(any(row.get('worker_error') for row in rows))

    def test_existing_source_runtime_checkpoint_is_migrated(self):
        from inventory.discovery import discover
        for path in self.books.glob('*.pdf'):path.unlink()
        (self.books/'Book.txt').write_text('A source unchanged since its old scan',encoding='utf-8')
        book=discover(self.books)[0]
        options=Options(ocr=False,online_titles=False,workers=1)
        files=[(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in book.files]
        models=[(p.name,p.stat().st_size,p.stat().st_mtime_ns) for p in Path(options.models).glob('*.traineddata')]
        identity=dict(version=10,options=dict(grouping='auto',image_mode='pictures',ocr=False,models=options.models),
                      models=models,notes=book.notes,files=files)
        key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        row=dict(isbn='NOT FOUND',title='Previously completed book',language='ENGLISH',book_type='EDITABLE',
                 page_count='N/A',image_count=0,sources=[str(p) for p in book.files],notes=[],
                 language_confidence=1,image_method='TXT only',external_image_files=0,regions=[])
        self.output.with_suffix('.cache.json').write_text(json.dumps({key:row}),encoding='utf-8')
        with patch('inventory.engine.analyse_book',side_effect=AssertionError('Must reuse old result')):
            rows,_=run(self.books,self.output,options)
        self.assertEqual(rows[0]['title'],'Previously completed book')


if __name__=='__main__':unittest.main()
