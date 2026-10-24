from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call
from zipfile import ZipFile
from inventory.isbn_filter import read_isbn_sheet,select_books
from inventory.discovery import Book, discover
from inventory.engine import run,Options

A='9780820421834'
B='9780306406157'


def workbook(rows):
    stream=BytesIO()
    with ZipFile(stream,'w') as z:
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(
            '<row>'+''.join(f'<c r="{chr(65+j)}{i+1}" t="inlineStr"><is><t>{v}</t></is></c>' for j,v in enumerate(row))+'</row>' for i,row in enumerate(rows))+'</sheetData></worksheet>')
    return stream.getvalue()


class IsbnFilterTests(unittest.TestCase):
    def test_read_header_column_dedup_and_equivalent_isbn(self):
        self.assertEqual(read_isbn_sheet(workbook([['ISBN','Other'],[A,B],['0-8204-2183-9',''],[A,'']])),[A])

    def test_no_header_and_invalid_sheet(self):
        self.assertEqual(read_isbn_sheet(workbook([[A],[B]])),[A,B])
        with self.assertRaises(ValueError):read_isbn_sheet(b'not excel')
        with self.assertRaises(ValueError):read_isbn_sheet(workbook([['ISBN'],['wrong']]))

    def test_filter_matches_filename_or_folder_and_reports_missing(self):
        books=[Book('A',[Path(f'D:/Books/{A}/INPUT/TEXT/book.pdf')]),Book('B',[Path(f'D:/Books/{B}_txt.pdf')])]
        selected,missing=select_books(books,[A,'9780820424675'])
        self.assertEqual(selected,[books[0]])
        self.assertEqual(missing,['9780820424675'])

    def test_unlisted_book_never_analysed_and_missing_row_exported(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'.tmp') as folder:
            root=Path(folder)
            for isbn in (A,B):
                (root/f'{isbn}.txt').write_text('Title: Sample\n'+'The book contains information about the history of the world and the people who live in it. '*3)
            from inventory.engine import analyse_book
            seen=[]
            def record(book,*args):
                seen.extend(p.name for p in book.files)
                return analyse_book(book,*args)
            with patch('inventory.engine.analyse_book',side_effect=record):
                rows,_=run(root,root/'out.xlsx',Options(ocr=False,online_titles=False,workers=1,isbn_filter=(A,'9780820424675')))
            self.assertEqual(seen,[A+'.txt'])
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[1]['title'],'NOT FOUND')

    def test_exact_isbn_folders_skip_unrelated_root_tree(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'.tmp') as folder:
            root=Path(folder)
            for isbn in (A,B):
                book=root/isbn
                book.mkdir()
                (book/f'{isbn}.txt').write_text('Title: Sample\n'+'This book explains the history of the world. '*5)
            messages=[]
            with patch('inventory.engine.discover',wraps=discover) as scanner:
                rows,_=run(root,root/'out.xlsx',Options(ocr=False,online_titles=False,workers=1,isbn_filter=(A,)),
                           progress=lambda done,total,message:messages.append(message))
            self.assertEqual(scanner.call_args_list,[call(root/A,'auto')])
            self.assertEqual([row['isbn'] for row in rows],[A])
            self.assertTrue(any('ISBN folder 1/1' in message for message in messages))

    def test_fast_server_mode_reports_missing_direct_folder_without_full_walk(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'.tmp') as folder:
            root=Path(folder)
            (root/A).mkdir()
            (root/A/f'{A}.txt').write_text('Title: Sample\n'+'This book explains the history of the world. '*5)
            (root/'other').mkdir()
            (root/'other'/f'{B}.txt').write_text('Title: Elsewhere')
            with patch('inventory.engine.discover',wraps=discover) as scanner:
                rows,_=run(root,root/'out.xlsx',Options(ocr=False,online_titles=False,workers=1,
                    isbn_filter=(A,B),fast_isbn_folders=True))
            self.assertEqual(scanner.call_args_list,[call(root/A,'auto')])
            self.assertEqual([row['isbn'] for row in rows],[A,B])
            self.assertEqual(rows[1]['title'],'NOT FOUND')
            self.assertIn('direct ISBN folder',rows[1]['notes'][0])


if __name__=='__main__':unittest.main()
