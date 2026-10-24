import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import pymupdf
from inventory.discovery import discover,Book
from inventory.engine import analyse_book,analyse_pdf,Options,run
import json
import os

ISBN = '9780820421834'
TEXT = 'Title: The Illustrated World\n' + ('The history of this book explains how people learn about the world. Each chapter gives the reader useful information and examples. '*3)


class StructuredWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'.tmp')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def create_pdf(self,path,pages=1):
        path.parent.mkdir(parents=True,exist_ok=True)
        with pymupdf.open() as doc:
            for _ in range(pages):
                doc.new_page()
            doc.save(path)
        return path

    def test_isbn_layout_merges_text_image_and_never_enters_cover(self):
        root = self.root/ISBN
        self.create_pdf(root/'INPUT'/'TEXT'/f'{ISBN}_txt.pdf')
        self.create_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_img.pdf')
        cover = root/'INPUT'/'COVER'/f'{ISBN}_Front.jpg'
        cover.parent.mkdir(parents=True)
        cover.write_bytes(b'cover should never be read or OCRed')
        (cover.parent/'front.jpg').write_bytes(b'unnamed COVER asset must also be excluded')
        for grouping in ('auto','folder'):
            groups = discover(self.root,grouping)
            self.assertEqual(len(groups),1)
            self.assertEqual(len(groups[0].files),2)

    def test_text_ocr_stops_after_ten_pages_and_img_ocr_runs_all_pages(self):
        txt = self.create_pdf(self.root/f'{ISBN}_txt.pdf',12)
        img = self.create_pdf(self.root/f'{ISBN}_img.pdf',8)
        image_info = [{'bbox':(0,0,595,842),'width':595,'height':842}]
        calls=[]
        def fake_ocr(page,models):
            calls.append(page.number)
            return TEXT,[]
        with patch('inventory.engine.ocr_page',side_effect=fake_ocr) as ocr, \
             patch.object(pymupdf.Page,'get_image_info',return_value=image_info), \
             patch('inventory.engine.picture_regions',return_value=[[1,2,3,4]]) as pictures:
            metadata = analyse_pdf(txt,Options(online_titles=False,workers=1),lambda:False,lambda message:None,purpose='metadata')
            self.assertEqual(ocr.call_count,10)
            self.assertEqual(calls,list(range(10)))
            self.assertEqual(metadata['pages'],12)
            self.assertEqual(metadata['title'],'The Illustrated World')
            self.assertEqual(pictures.call_count,0)
            ocr.reset_mock()
            result = analyse_pdf(img,Options(online_titles=False,workers=1),lambda:False,lambda message:None,purpose='images')
            self.assertEqual(ocr.call_count,8)
            self.assertEqual(result['images'],8)

    def test_pod_covers_and_generated_copies_do_not_duplicate_inventory(self):
        for isbn, pages in ((ISBN,350),('9780306406157',250)):
            root = self.root / isbn
            self.create_pdf(root/'INPUT'/'TEXT'/f'{isbn}_txt.pdf',pages)
            self.create_pdf(root/'OUTPUT'/'POD'/f'{isbn}_txt.pdf',2)
            self.create_pdf(root/'OUTPUT'/'POD'/f'{isbn}_cvr_eu.pdf',1)
            self.create_pdf(root/'OUTPUT'/'POD'/f'{isbn}_cvr_int.pdf',2)
            self.create_pdf(root/'OUTPUT'/'POD'/'proof.pdf',1)
        for grouping in ('auto','folder'):
            for fast in (False,True):
                with self.subTest(grouping=grouping,fast=fast):
                    options=Options(grouping=grouping,ocr=False,online_titles=False,workers=1,
                                    isbn_filter=(ISBN,'9780306406157') if fast else None,
                                    fast_isbn_folders=fast)
                    output=self.root/'report.xlsx'
                    rows,cancelled=run(self.root,output,options)
                    self.assertFalse(cancelled)
                    self.assertEqual(len(rows),2)
                    self.assertEqual({r['isbn']:r['page_count'] for r in rows},
                                     {ISBN:350,'9780306406157':250})
                    self.assertTrue(all(len(r['sources']) == 1 for r in rows))
                    audit=json.loads(output.with_suffix('.audit.json').read_text(encoding='utf-8'))
                    self.assertEqual(audit['completed'],2)

    def test_cover_only_exports_are_not_books(self):
        self.create_pdf(self.root/'OUTPUT'/'POD'/f'{ISBN}_cvr_eu.pdf')
        self.create_pdf(self.root/'INPUT'/'COVER'/'front.pdf')
        self.assertEqual(discover(self.root),[])

    def test_teil_books_keep_separate_text_page_counts_and_no_ancillary_rows(self):
        root=self.root/ISBN
        self.create_pdf(root/'INPUT'/'TEXT'/f'{ISBN}_txt_Teil_1.pdf',350)
        self.create_pdf(root/'INPUT'/'TEXT'/f'{ISBN}_txt_Teil_2.pdf',250)
        self.create_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_img_TEIL1.pdf',2)
        self.create_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_SPREADSHEET_02.pdf',1)
        self.create_pdf(root/'OUTPUT'/'POD'/f'{ISBN}_txt.pdf',1)
        cover=root/'INPUT'/'COVER'/f'{ISBN}_Front_TEIL2.jpg'
        cover.parent.mkdir(parents=True)
        cover.write_bytes(b'cover must not be OCRed')
        for grouping in ('auto','folder'):
            for workers in (1,2):
                with self.subTest(grouping=grouping,workers=workers):
                    rows,_=run(self.root,self.root/'parts.xlsx',Options(
                        grouping=grouping,workers=workers,ocr=False,online_titles=False,
                        isbn_filter=(ISBN,),fast_isbn_folders=True))
                    self.assertEqual(len(rows),2)
                    self.assertEqual({r['part']:r['page_count'] for r in rows},{1:350,2:250})
                    self.assertTrue(all(f"[Teil {r['part']}]" in r['title'] for r in rows))
                    self.assertTrue(all(not any('SPREADSHEET' in p or 'OUTPUT' in p for p in r['sources']) for r in rows))

    def test_spreadsheet_does_not_become_a_second_book(self):
        root=self.root/ISBN
        self.create_pdf(root/'INPUT'/'TEXT'/f'{ISBN}_txt.pdf',152)
        self.create_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_SPREADSHEET.pdf',1)
        self.create_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_SPREADSHEET_02.pdf',2)
        rows,_=run(self.root,self.root/'report.xlsx',Options(ocr=False,online_titles=False,workers=1))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['page_count'],152)
        self.assertEqual(len(rows[0]['image_sources']),2)

    def picture_pdf(self,path,pictures_per_page):
        path.parent.mkdir(parents=True,exist_ok=True)
        pix=pymupdf.Pixmap(pymupdf.csRGB,50,50,bytes([30,80,140])*2500,False)
        with pymupdf.open() as doc:
            for count in pictures_per_page:
                page=doc.new_page()
                for index in range(count):
                    x=40+index*100
                    page.insert_image(pymupdf.Rect(x,100,x+70,170),pixmap=pix)
            doc.save(path)
        return path

    def test_image_and_all_spreadsheets_add_pictures_without_changing_text_pages(self):
        root=self.root/ISBN
        self.create_pdf(root/'INPUT'/'TEXT'/f'{ISBN}_txt.pdf',152)
        self.picture_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_img.pdf',[3])
        self.picture_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_SPREADSHEET.pdf',[2])
        self.picture_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_SPREADSHEET_02.pdf',[1,1])
        for workers in (1,2):
            rows,_=run(self.root,self.root/'sum.xlsx',Options(ocr=False,online_titles=False,workers=workers))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['page_count'],152)
            self.assertEqual(rows[0]['image_count'],7)
            self.assertEqual(sorted(rows[0]['image_counts_by_source'].values()),[2,2,3])

    def test_spreadsheet_only_images_are_counted_without_img_pdf(self):
        root=self.root/ISBN
        self.create_pdf(root/'INPUT'/'TEXT'/f'{ISBN}_txt.pdf',25)
        self.picture_pdf(root/'INPUT'/'IMAGE'/'SPREADSHEET.pdf',[2,1])
        rows,_=run(self.root,self.root/'sheets.xlsx',Options(ocr=False,online_titles=False,workers=1))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['page_count'],25)
        self.assertEqual(rows[0]['image_count'],3)

    def test_spreadsheets_match_teil_and_ambiguous_sheet_is_never_counted_twice(self):
        root=self.root/ISBN
        for part,pages,count in ((1,35,2),(2,25,3)):
            self.create_pdf(root/'INPUT'/'TEXT'/f'{ISBN}_txt_Teil_{part}.pdf',pages)
            self.picture_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_SPREADSHEET_Teil_{part}.pdf',[count])
        options=Options(ocr=False,online_titles=False,workers=1)
        rows,_=run(self.root,self.root/'parts.xlsx',options)
        self.assertEqual({r['part']:r['image_count'] for r in rows},{1:2,2:3})
        self.picture_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_SPREADSHEET.pdf',[4])
        rows,_=run(self.root,self.root/'ambiguous.xlsx',options)
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['image_count']=='NEEDS REVIEW' for r in rows))

    def test_cover_directory_is_not_even_enumerated(self):
        self.create_pdf(self.root/ISBN/'INPUT'/'TEXT'/f'{ISBN}_txt.pdf')
        cover=self.root/ISBN/'INPUT'/'cOvEr'
        (cover/'nested').mkdir(parents=True)
        (cover/'nested'/'unreadable.pdf').write_bytes(b'not a PDF')
        original=os.scandir
        def guarded(path):
            self.assertNotIn('COVER',[p.upper() for p in Path(path).parts])
            return original(path)
        with patch('inventory.discovery.os.scandir',side_effect=guarded):
            groups=discover(self.root)
        self.assertEqual(len(groups),1)
        self.assertEqual(len(groups[0].files),1)
        self.assertEqual(discover(cover),[])

    def test_first_text_page_is_online_fallback_not_later_pages(self):
        text=self.root/f'{ISBN}_txt_Teil_2.pdf'
        with pymupdf.open() as doc:
            doc.new_page()  # A blank first page must not borrow a later title.
            doc.new_page().insert_text((50,80),'Wrong Later Title',fontsize=24)
            doc.save(text)
        options=Options(ocr=False,online_titles=True,workers=1)
        with patch('inventory.titles.lookup_title',return_value={'title':None,'reason':'Online title unavailable'}):
            rows,_=run(self.root,self.root/'blank-title.xlsx',options)
        self.assertEqual(rows[0]['title'],'NEEDS REVIEW [Teil 2]')
        with pymupdf.open(text) as doc:
            doc[0].insert_text((50,80),'Correct First Page Title',fontsize=24)
            doc.save(self.root/'replacement.pdf')
        os.replace(self.root/'replacement.pdf',text)
        with patch('inventory.titles.lookup_title',return_value={'title':None,'reason':'Online title unavailable'}):
            rows,_=run(self.root,self.root/'first-title.xlsx',options)
        self.assertEqual(rows[0]['title'],'Correct First Page Title [Teil 2]')

    def test_failed_spreadsheet_does_not_export_a_partial_picture_total(self):
        root=self.root/ISBN
        self.create_pdf(root/'INPUT'/'TEXT'/f'{ISBN}_txt.pdf',25)
        image=self.picture_pdf(root/'INPUT'/'IMAGE'/f'{ISBN}_img.pdf',[3])
        sheet=image.parent/f'{ISBN}_SPREADSHEET.pdf'
        sheet.write_bytes(b'corrupt PDF')
        rows,_=run(self.root,self.root/'failed.xlsx',Options(ocr=False,online_titles=False,workers=1))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['image_count'],'NEEDS REVIEW')
        self.assertEqual(rows[0]['page_count'],25)

    def test_online_lookup_and_resume_keep_part_labels(self):
        self.create_pdf(self.root/f'{ISBN}_txt_Teil_2.pdf',25)
        self.create_pdf(self.root/f'{ISBN}_txt_Teil_3.pdf',30)
        output=self.root/'online-parts.xlsx'
        def same_online_title(row):
            return {**row,'title':'Shared book title'}
        with patch('inventory.titles.enrich_title',side_effect=same_online_title):
            for _ in range(2):
                rows,_=run(self.root,output,Options(ocr=False,online_titles=True,workers=1))
                self.assertEqual({r['title']:r['page_count'] for r in rows},
                                 {'Shared book title [Teil 2]':25,'Shared book title [Teil 3]':30})

    def test_missing_part_text_never_uses_image_page_count(self):
        self.create_pdf(self.root/f'{ISBN}_img_Teil_1.pdf',2)
        rows,_=run(self.root,self.root/'missing.xlsx',Options(ocr=False,online_titles=False,workers=1))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['part'],1)
        self.assertEqual(rows[0]['page_count'],'N/A')

    def test_output_text_remains_usable_without_input_sources(self):
        text=self.create_pdf(self.root/ISBN/'OUTPUT'/'POD'/f'{ISBN}_txt.pdf',25)
        self.create_pdf(text.parent/f'{ISBN}_cvr_eu.pdf')
        groups=discover(self.root)
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0].files,[text])

    def test_general_pdf_never_ocr_after_page_ten(self):
        path=self.create_pdf(self.root/'Book.pdf',14)
        with patch('inventory.engine.ocr_page',return_value=(TEXT,[])) as ocr:
            analyse_pdf(path,Options(online_titles=False,workers=1),lambda:False,lambda message:None)
            self.assertEqual(ocr.call_count,10)

    def test_metadata_uses_txt_and_image_count_uses_img_exclusively(self):
        txt=self.root/f'{ISBN}_txt.pdf'
        img=self.root/f'{ISBN}_img.pdf'
        cover=self.root/f'{ISBN}_Front.jpg'
        def pdf_result(path,options,cancel,progress,purpose='general'):
            if path == txt:
                self.assertEqual(purpose,'metadata')
                return dict(text=TEXT,title='The Illustrated World',pages=232,type='SCANNED',images=None,notes=[],regions=[],metadata='')
            self.assertEqual(path,img)
            self.assertEqual(purpose,'images')
            return dict(text='German image captions must not override book language',title='Wrong image title',pages=8,type='SCANNED',images=10,notes=[],regions=[{'page':1}],metadata='')
        with patch('inventory.engine.analyse_pdf',side_effect=pdf_result) as analyse, \
             patch('inventory.engine.analyse_image',side_effect=AssertionError('COVER must not be OCRed')):
            result=analyse_book(Book(ISBN,[cover,img,txt]),Options(online_titles=False,workers=1),lambda:False,lambda message:None)
            self.assertEqual(analyse.call_count,2)
            self.assertEqual(result['title'],'The Illustrated World')
            self.assertEqual(result['language'],'ENGLISH')
            self.assertEqual(result['page_count'],232)
            self.assertEqual(result['image_count'],10)

    def test_missing_img_is_review_not_a_text_or_cover_count(self):
        txt=self.create_pdf(self.root/f'{ISBN}_txt.pdf')
        with patch('inventory.engine.ocr_page',return_value=(TEXT,[])):
            row=analyse_book(Book(ISBN,[txt]),Options(online_titles=False,workers=1),lambda:False,lambda message:None)
        self.assertEqual(row['image_count'],'NEEDS REVIEW')


if __name__ == '__main__':
    unittest.main()



