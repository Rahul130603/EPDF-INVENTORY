import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import pymupdf
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from inventory.discovery import discover
from inventory.engine import run, Options, analyse_pdf
from inventory.metadata import find_isbns, valid_isbn, language
from inventory.vision import DEFAULT_MODELS, picture_regions, analyse_image

ENGLISH = ("The history of this book explains how people learn about the world. "
           "Each chapter gives the reader useful information and examples from everyday life. ") * 4
GERMAN = ("Dieses Buch beschreibt die Geschichte der Menschen und ihre Beziehung zur Natur. "
          "Die verschiedenen Kapitel enthalten zahlreiche Beispiele und wichtige Informationen über das Leben. ") * 4


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.tmp')
        self.base = Path(self.tmp.name)
        self.input = self.base / 'books'
        self.input.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def pdf(self, path, text=ENGLISH, images=False):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 60), 'A Journey Through Books', fontsize=24)
            page.insert_textbox((50, 100, 540, 600), 'ISBN: 9780306406157\n' + text, fontsize=11)
            if images:
                rgb = np.full((100, 120, 3), [30, 100, 180], dtype=np.uint8)
                pix = pymupdf.Pixmap(pymupdf.csRGB, 120, 100, rgb.tobytes(), False)
                page.insert_image(pymupdf.Rect(100, 620, 220, 720), pixmap=pix)
            doc.save(path)

    def scanned_image(self, path):
        image = Image.new('RGB', (1200, 1600), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 28)
        draw.text((80, 60), 'Title: The Illustrated World', font=font, fill='black')
        draw.text((80, 110), 'ISBN: 9780306406157', font=font, fill='black')
        lines = ['The history of this book explains how people learn about the world.',
                 'Each chapter gives the reader useful information and examples.',
                 'The natural world is full of interesting stories and discoveries.']
        for index, line in enumerate(lines * 2):
            draw.text((80, 180 + index * 45), line, font=font, fill='black')
        draw.rectangle((100, 650, 500, 1050), fill=(25, 85, 165))
        draw.ellipse((700, 650, 1100, 1050), fill=(180, 45, 45))
        image.save(path)

    def test_isbn_checksum_and_multiple_candidates(self):
        self.assertTrue(valid_isbn('0-306-40615-2'))
        self.assertTrue(valid_isbn('080442957X'))
        self.assertFalse(valid_isbn('9780306406158'))
        self.assertEqual(find_isbns('ISBN 978-0-306-40615-7; 0-306-40615-2; 9780306406158'),
                         ['9780306406157', '0306406152'])

    def test_language_english_german_unknown(self):
        self.assertEqual(language(ENGLISH)[0], 'ENGLISH')
        self.assertEqual(language(GERMAN)[0], 'GERMAN')
        self.assertEqual(language('Some title')[0], 'NEEDS REVIEW')

    def test_grouping_and_ambiguous_images(self):
        self.pdf(self.input / 'Book A.pdf')
        (self.input / 'Book A.txt').write_text(ENGLISH)
        (self.input / 'Book B.txt').write_text(GERMAN)
        Image.new('RGB', (100, 100)).save(self.input / 'unmatched.png')
        groups = discover(self.input)
        self.assertEqual(len(groups), 3)
        self.assertEqual(sorted(len(g.files) for g in groups), [1, 1, 2])
        self.assertEqual(len(discover(self.input, 'folder')), 1)

    def test_nested_images_attach_to_unique_parent(self):
        self.pdf(self.input / 'Book.pdf')
        sub = self.input / 'images'
        sub.mkdir()
        Image.new('RGB', (100, 100)).save(sub / '001.png')
        groups = discover(self.input)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].files), 2)

    def test_native_pdf_export_and_safe_excel_strings(self):
        self.pdf(self.input / 'Book.pdf', images=True)
        output = self.base / 'report.xlsx'
        rows, cancelled = run(self.input, output, Options(ocr=False,online_titles=False,workers=1))
        self.assertFalse(cancelled)
        self.assertEqual(rows[0]['isbn'], '9780306406157')
        self.assertEqual(rows[0]['language'], 'ENGLISH')
        self.assertEqual(rows[0]['book_type'], 'EDITABLE')
        self.assertEqual(rows[0]['page_count'], 1)
        self.assertEqual(rows[0]['image_count'], 1)
        self.assertEqual(rows[0]['title'], 'A Journey Through Books')
        with ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            headers = [cell.find('s:is/s:t', ns).text for cell in sheet.find('s:sheetData/s:row', ns)]
            self.assertEqual(headers, ['ISBN', 'TITLE', 'LANGUAGE', 'EDITABLE OR SCANNED', 'PAGE COUNT', 'IMAGE COUNT'])
        from inventory.export import export_workbook
        rows[0]['title'] = '=HYPERLINK("bad")\x01'
        export_workbook(output, rows)
        with ZipFile(output) as archive:
            self.assertNotIn('<f>', archive.read('xl/worksheets/sheet1.xml').decode())

    def test_checkpoint_resume_and_change_invalidation(self):
        book = self.input / 'Book.txt'
        book.write_text('Title: Sample book\n' + ENGLISH)
        output = self.base / 'report.xlsx'
        run(self.input, output, Options(ocr=False,online_titles=False,workers=1))
        with patch('inventory.engine.analyse_book', side_effect=AssertionError('should resume')):
            run(self.input, output, Options(ocr=False,online_titles=False,workers=1))
        book.write_text('Title: Ein deutsches Buch\n' + GERMAN)
        rows, _ = run(self.input, output, Options(ocr=False,online_titles=False,workers=1))
        self.assertEqual(rows[0]['language'], 'GERMAN')

    def test_corrupt_pdf_does_not_stop_other_books(self):
        (self.input / 'Broken.pdf').write_bytes(b'not a pdf')
        (self.input / 'Working.txt').write_text(ENGLISH)
        rows, _ = run(self.input, self.base / 'report.xlsx', Options(ocr=False,online_titles=False,workers=1))
        self.assertEqual(len(rows), 2)
        broken = next(r for r in rows if r['sources'][0].endswith('Broken.pdf'))
        self.assertEqual(broken['book_type'], 'NEEDS REVIEW')
        self.assertEqual(broken['image_count'], 'NEEDS REVIEW')
        self.assertTrue(any(n.startswith('ERROR:') for n in broken['notes']))

    def test_cancel_saves_partial_and_resume(self):
        for index in range(3):
            (self.input / f'Book{index}.txt').write_text(ENGLISH)
        stop = threading.Event()
        def progress(done, total, text):
            if done == 1:
                stop.set()
        output = self.base / 'report.xlsx'
        rows, cancelled = run(self.input, output, Options(ocr=False,online_titles=False,workers=1), progress, stop)
        self.assertTrue(cancelled)
        self.assertEqual(len(rows), 1)
        self.assertTrue(json.loads(output.with_suffix('.audit.json').read_text())['cancelled'])
        rows, cancelled = run(self.input, output, Options(ocr=False,online_titles=False,workers=1))
        self.assertEqual(len(rows), 3)
        self.assertFalse(cancelled)

    def test_large_batch_205_books(self):
        for index in range(205):
            (self.input / f'Book{index:03}.txt').write_text('Title: Book\n' + (ENGLISH if index % 2 else GERMAN))
        rows, _ = run(self.input, self.base / 'report.xlsx', Options(ocr=False,online_titles=False,workers=1))
        self.assertEqual(len(rows), 205)
        self.assertEqual(sum(r['language'] == 'GERMAN' for r in rows), 103)

    def test_picture_regions_two_distinct_illustrations(self):
        rgb = np.full((1000, 800, 3), 255, np.uint8)
        rgb[200:450, 80:300] = [30, 70, 150]
        rgb[200:450, 500:720] = [150, 70, 30]
        self.assertEqual(len(picture_regions(rgb, [], 800, 1000)), 2)
        self.assertEqual(len(picture_regions(np.full_like(rgb, 255), [], 800, 1000)), 0)

    @unittest.skipUnless((DEFAULT_MODELS / 'eng.traineddata').exists(), 'Run setup_ocr.py for real OCR tests')
    def test_real_ocr_scanned_mixed_and_picture_count(self):
        image = self.base / 'page.png'
        self.scanned_image(image)
        text, count, frames, _ = analyse_image(image, DEFAULT_MODELS)
        self.assertIn('9780306406157', text.replace(' ', ''))
        self.assertEqual(count, 2)
        self.assertEqual(frames, 1)
        scanned = self.input / 'Scanned.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_image(page.rect, filename=str(image))
            doc.save(scanned)
        result = analyse_pdf(scanned, Options(online_titles=False,workers=1), lambda: False, lambda text: None)
        self.assertEqual(result['type'], 'SCANNED')
        self.assertEqual(result['images'], 2)
        mixed = self.base / 'Mixed.pdf'
        with pymupdf.open(scanned) as doc:
            page = doc.new_page()
            page.insert_textbox((50, 50, 540, 700), ENGLISH)
            doc.save(mixed)
        result = analyse_pdf(mixed, Options(online_titles=False,workers=1), lambda: False, lambda text: None)
        self.assertEqual(result['type'], 'MIXED')

    @unittest.skipUnless((DEFAULT_MODELS / 'deu.traineddata').exists(), 'Run setup_ocr.py for real OCR tests')
    def test_german_ocr_and_multiframe_tiff(self):
        image = Image.new('RGB', (1200, 900), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 27)
        lines = ['Titel: Die Geschichte der Natur',
                 'Dieses Buch beschreibt die Geschichte der Menschen und ihre',
                 'Beziehung zur Natur. Die verschiedenen Kapitel enthalten',
                 'zahlreiche Beispiele und wichtige Informationen über das Leben.',
                 'Die Entwicklung unserer Gesellschaft ist ein wichtiges Thema.']
        for index, line in enumerate(lines):
            draw.text((80, 80 + index*50), line, font=font, fill='black')
        target = self.input / 'German.tiff'
        image.save(target, save_all=True, append_images=[image.copy()])
        rows, _ = run(self.input, self.base / 'german.xlsx', Options(online_titles=False,workers=1))
        self.assertEqual(rows[0]['page_count'], 2)
        self.assertEqual(rows[0]['language'], 'GERMAN')
        self.assertEqual(rows[0]['image_count'], 0)
        rows, _ = run(self.input, self.base / 'files.xlsx', Options(image_mode='files',online_titles=False,workers=1))
        self.assertEqual(rows[0]['image_count'], 1)

    @unittest.skipUnless((DEFAULT_MODELS / 'eng.traineddata').exists(), 'Run setup_ocr.py for real OCR tests')
    def test_scan_with_ocr_layer_is_still_scanned(self):
        image = self.base / 'page.png'
        self.scanned_image(image)
        output = self.input / 'OCR_layer.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_image(page.rect, filename=str(image))
            page.insert_textbox((50, 50, 550, 600), ENGLISH, render_mode=3)
            doc.save(output)
        rows, _ = run(self.input, self.base / 'result.xlsx', Options(online_titles=False,workers=1))
        self.assertEqual(rows[0]['book_type'], 'SCANNED')
        self.assertEqual(rows[0]['image_count'], 2)


if __name__ == '__main__':
    unittest.main()


