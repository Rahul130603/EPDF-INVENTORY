import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import pymupdf
import numpy as np
from inventory.engine import run,Options
from inventory.vision import DEFAULT_MODELS


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'.tmp')
        self.root=Path(self.tmp.name)
        self.books=self.root/'books'
        self.books.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self,number=4):
        for i in range(number):
            text=('This book explains the history of people and their relationship with the natural world. Each chapter contains useful information for the reader. '
                  if i%2==0 else 'Dieses Buch beschreibt die Geschichte der Menschen und ihre Beziehung zur Natur. Die Kapitel enthalten zahlreiche Beispiele und wichtige Informationen über das Leben. ')
            (self.books/f'Book{i}.txt').write_text(f'Title: Book {i}\n'+text*4,encoding='utf-8')

    def test_real_processes_match_sequential_and_publish_live(self):
        self.fixture()
        sequential,_=run(self.books,self.root/'sequential.xlsx',Options(ocr=False,online_titles=False,workers=1))
        seen=[]
        parallel,cancelled=run(self.books,self.root/'parallel.xlsx',Options(ocr=False,online_titles=False),on_book_complete=seen.append)
        self.assertFalse(cancelled)
        self.assertEqual(len(seen),4)
        self.assertEqual(sorted(parallel,key=lambda r:r['title']),sorted(sequential,key=lambda r:r['title']))
        self.assertEqual(len({r['sources'][0] for r in seen}),4)
        # Worker count doesn't invalidate cache or spawn processes for cached books.
        with patch('inventory.engine.ProcessPoolExecutor',side_effect=AssertionError('Cache should be reused')):
            resumed,_=run(self.books,self.root/'parallel.xlsx',Options(ocr=False,online_titles=False,workers=1))
            self.assertEqual(len(resumed),4)

    def test_six_workers_process_six_books(self):
        self.fixture(6)
        seen=[]
        rows,cancelled=run(self.books,self.root/'six.xlsx',
                           Options(ocr=False,online_titles=False,workers=6),on_book_complete=seen.append)
        self.assertFalse(cancelled)
        self.assertEqual(len(rows),6)
        self.assertEqual(len(seen),6)
        self.assertEqual({row['title'] for row in rows},{f'Book {i}' for i in range(6)})

    def test_parallel_cancel_saves_partial_and_resumes(self):
        self.fixture(8)
        stop=threading.Event()
        seen=[]
        def complete(row):
            seen.append(row)
            stop.set()
        output=self.root/'cancelled.xlsx'
        rows,cancelled=run(self.books,output,Options(ocr=False,online_titles=False),stop=stop,on_book_complete=complete)
        self.assertTrue(cancelled)
        self.assertGreaterEqual(len(rows),1)
        self.assertLess(len(rows),8)
        self.assertEqual(rows,seen)
        self.assertTrue(json.loads(output.with_suffix('.audit.json').read_text(encoding='utf-8'))['cancelled'])
        resumed,cancelled=run(self.books,output,Options(ocr=False,online_titles=False))
        self.assertFalse(cancelled)
        self.assertEqual(len(resumed),8)

    @unittest.skipUnless((DEFAULT_MODELS/'eng.traineddata').exists(),'OCR models required')
    def test_real_scanned_pdfs_match_sequential(self):
        for i in range(2):
            with pymupdf.open() as doc:
                page=doc.new_page(width=600,height=800)
                page.insert_textbox((40,40,560,350),f'Title: Example book {i}\n'+
                    'The history of this book explains how people learn about the world. Each chapter contains useful examples for the reader. '*3,fontsize=15)
                rgb=np.full((120,180,3),[30,70,140],np.uint8)
                page.insert_image(pymupdf.Rect(100,400,280,520),pixmap=pymupdf.Pixmap(pymupdf.csRGB,180,120,rgb.tobytes(),False))
                pix=page.get_pixmap()
                with pymupdf.open() as scanned:
                    p=scanned.new_page(width=600,height=800)
                    p.insert_image(p.rect,pixmap=pix)
                    scanned.save(self.books/f'Scan{i}.pdf')
        sequential,_=run(self.books,self.root/'s.xlsx',Options(online_titles=False,workers=1))
        parallel,_=run(self.books,self.root/'p.xlsx',Options(online_titles=False,workers=2))
        self.assertEqual(sorted(sequential,key=lambda r:r['title']),sorted(parallel,key=lambda r:r['title']))


if __name__=='__main__':
    unittest.main()
