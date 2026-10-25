import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from web_app import create_server


class WebDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.tmp')
        self.folder = Path(self.tmp.name)
        self.server = create_server(0)
        self.server.dashboard.history = []
        self.server.dashboard.history_file = self.folder / 'history.json'
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self,path,body=None,token=True,extra=None):
        connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=15)
        headers = dict(extra or {})
        if token:
            headers['X-EPDF-Token'] = self.server.dashboard.token
        data = body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None
        if data:
            headers['Content-Type'] = 'application/json'
        connection.request('POST' if body is not None else 'GET',path,data,headers)
        response = connection.getresponse()
        result = response.status,response.read(),dict(response.getheaders())
        connection.close()
        return result

    def test_dashboard_assets_and_folder_picker(self):
        for path in ('/','/app.css','/app.js','/assets/books-and-plants.png'):
            self.assertEqual(self.request(path)[0],200)
        self.assertEqual(self.request('/api/folders')[0],200)
        from urllib.parse import quote
        status,data,_ = self.request('/api/folders?path='+quote(str(self.folder)))
        self.assertEqual(status,200)
        self.assertEqual(json.loads(data)['path'],str(self.folder.resolve()))

    def test_reject_cross_origin_and_missing_token(self):
        self.assertEqual(self.request('/api/scan',{},token=False)[0],403)
        self.assertEqual(self.request('/api/cancel',{},extra={'Origin':'https://unrelated.example'})[0],403)
        self.assertEqual(self.request('/api/state',extra={'Host':'unrelated.example'})[0],403)

    def test_scan_progress_download_and_overwrite_confirmation(self):
        (self.folder/'book.txt').write_text('Title: A World of Books\nISBN: 9780306406157\n'+
            'The history of this book explains how people learn about the world. Each chapter contains useful examples for the reader. '*4)
        body={'folder':str(self.folder),'output':str(self.folder/'inventory.xlsx'),'ocr':False,'online_titles':False,'workers':1}
        self.assertEqual(self.request('/api/scan',body)[0],202)
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            data=json.loads(self.request('/api/state')[1])
            if not data['running']:
                break
            time.sleep(.05)
        self.assertFalse(data['running'])
        self.assertEqual(data['status'],'Scan complete',data)
        self.assertEqual(data['rows'][0]['isbn'],'9780306406157')
        self.assertEqual(data['rows'][0]['language'],'ENGLISH')
        status,report,headers=self.request('/api/report')
        self.assertEqual(status,200)
        self.assertTrue(report.startswith(b'PK'))
        self.assertIn('attachment',headers['Content-Disposition'])
        status,result,_=self.request('/api/scan',body)
        self.assertEqual(status,409)
        self.assertEqual(json.loads(result)['code'],'exists')

    def test_invalid_folder_and_report(self):
        self.assertEqual(self.request('/api/scan',{'folder':str(self.folder/'missing'),'output':str(self.folder/'a.xlsx'),'ocr':False,'online_titles':False,'workers':1})[0],400)
        self.assertEqual(self.request('/api/report')[0],404)
        self.assertEqual(self.request('/../web_app.py')[0],404)

    def test_open_report_opens_only_the_saved_output_path(self):
        report=self.folder/'chosen destination'/'custom.xlsx'
        report.parent.mkdir()
        report.write_bytes(b'PK')
        self.server.dashboard.data['report']=str(report)
        with patch('web_app.os.startfile') as open_file:
            status,data,_=self.request('/api/open-report',{})
            self.assertEqual(status,200)
            self.assertEqual(json.loads(data)['path'],str(report.resolve()))
            open_file.assert_called_once_with(str(report.resolve()))
        self.server.dashboard.data['report']=None
        self.assertEqual(self.request('/api/open-report',{})[0],404)

    def test_upload_isbn_list_and_scan_only_selected_book(self):
        from test_isbn_filter import workbook
        target='9780820421834'
        for isbn in (target,'9780306406157'):
            (self.folder/f'{isbn}.txt').write_text('Title: Selected book\n'+'This book contains useful examples about the world and its history for people who enjoy reading books. '*3)
        status,payload,_=self.request('/api/isbn-list',workbook([['ISBN'],[target]]))
        self.assertEqual(status,200)
        uploaded=json.loads(payload)
        self.assertEqual(uploaded['count'],1)
        body={'folder':str(self.folder),'output':str(self.folder/'selected.xlsx'),'ocr':False,'online_titles':False,'workers':1,'isbn_list_id':uploaded['id']}
        self.assertEqual(self.request('/api/scan',body)[0],202)
        deadline=time.monotonic()+10
        while self.server.dashboard.snapshot()['running'] and time.monotonic()<deadline:
            time.sleep(.02)
        state=self.server.dashboard.snapshot()
        self.assertEqual([r['isbn'] for r in state['rows']],[target])
        self.assertEqual(state['total'],1)
        self.assertEqual(self.request('/api/scan',{**body,'isbn_list_id':'expired'})[0],400)

    def test_completed_book_visible_while_next_book_is_processing(self):
        from inventory.engine import analyse_book
        second_started,release_second=threading.Event(),threading.Event()
        for name in ('A','B'):
            (self.folder/f'{name}.txt').write_text('Title: Book '+name+'\n'+
                'The history of this book explains how people learn about the world. Each chapter contains useful examples for the reader. '*4)
        calls=[]
        def slow_second(book,*args):
            calls.append(book.key)
            if len(calls)==2:
                second_started.set()
                if not release_second.wait(10):
                    raise RuntimeError('Test timed out waiting for release')
            return analyse_book(book,*args)
        with patch('inventory.engine.analyse_book',side_effect=slow_second):
            try:
                body={'folder':str(self.folder),'output':str(self.folder/'live.xlsx'),'ocr':False,'online_titles':False,'workers':1}
                self.assertEqual(self.request('/api/scan',body)[0],202)
                self.assertTrue(second_started.wait(10))
                state=json.loads(self.request('/api/state')[1])
                self.assertTrue(state['running'])
                self.assertEqual(len(state['rows']),1)
                self.assertEqual(state['rows'][0]['title'],'Book A')
                self.assertTrue(state['report'].endswith('live.partial.xlsx'))
                self.assertTrue(Path(state['report']).is_file())
                self.assertEqual(state['done'],1)
                self.assertEqual(state['total'],2)
            finally:
                release_second.set()
                deadline=time.monotonic()+10
                while self.server.dashboard.snapshot()['running'] and time.monotonic()<deadline:
                    time.sleep(.02)
            final=json.loads(self.request('/api/state')[1])
            self.assertFalse(final['running'])
            self.assertEqual(len(final['rows']),2)
            # Cached books also publish one completion event each on a resumed run.
            self.assertEqual(self.request('/api/scan',{**body,'overwrite':True})[0],202)
            deadline=time.monotonic()+10
            while self.server.dashboard.snapshot()['running'] and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertEqual(len(self.server.dashboard.snapshot()['rows']),2)

    def test_completed_rows_survive_later_worker_failure(self):
        self.server.dashboard.data.update(running=True,started=time.time())
        row={'isbn':'9780306406157','title':'Finished book','regions':[{'large':'omitted'}]}
        def failed_run(*args,**kwargs):
            kwargs['on_book_complete'](row)
            raise OSError('Output is locked')
        with patch('inventory.engine.run',side_effect=failed_run):
            self.server.dashboard.worker(self.folder,self.folder/'failed.xlsx',None,True)
        state=self.server.dashboard.snapshot()
        self.assertFalse(state['running'])
        self.assertEqual(state['rows'][0]['title'],'Finished book')
        self.assertNotIn('regions',state['rows'][0])
        self.assertEqual(state['status'],'Scan failed')

    def test_web_worker_uses_four_processes_by_default(self):
        for name in ('A','B'):
            (self.folder/f'{name}.txt').write_text('Title: Parallel book '+name+'\n'+
                'This book explains the history of the world and the people who live in it. Each chapter contains useful information and examples for the reader. '*3)
        body={'folder':str(self.folder),'output':str(self.folder/'parallel.xlsx'),'ocr':False,'online_titles':False}
        self.assertEqual(self.request('/api/scan',body)[0],202)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            state=json.loads(self.request('/api/state')[1])
            if not state['running']:
                break
            time.sleep(.05)
        self.assertEqual(state['status'],'Scan complete',state)
        self.assertEqual(len(state['rows']),2)
        self.assertEqual({r['title'] for r in state['rows']},{'Parallel book A','Parallel book B'})
        audit=json.loads((self.folder/'parallel.audit.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['options']['workers'],4)

    def test_dashboard_survives_a_native_pdf_worker_crash(self):
        import pymupdf
        from test_crash_recovery import crash_one_book
        for name in ('A_good','B_crash','C_good'):
            with pymupdf.open() as doc:
                doc.new_page().insert_text((50,80),'Title: A book about history')
                doc.save(self.folder/f'{name}.pdf')
        with patch('inventory.engine.isolated_book_worker',crash_one_book):
            self.assertEqual(self.request('/api/scan',{'folder':str(self.folder),
                'output':str(self.folder/'recovered.xlsx'),'ocr':False,
                'online_titles':False,'workers':1})[0],202)
            deadline=time.monotonic()+25
            while self.server.dashboard.snapshot()['running'] and time.monotonic()<deadline:
                self.assertEqual(self.request('/api/state')[0],200)
                time.sleep(.1)
        state=self.server.dashboard.snapshot()
        self.assertEqual(state['status'],'Scan complete',state)
        self.assertIsNone(state['error'])
        self.assertEqual(len(state['rows']),3)
        self.assertEqual(sum(bool(r.get('worker_error')) for r in state['rows']),1)
        self.assertEqual(self.request('/api/report')[0],200)

    def test_timer_and_http_remain_responsive_during_stalled_native_call(self):
        import pymupdf
        from test_crash_recovery import stalled_worker
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(self.folder/'Stalled.pdf')
        with patch('inventory.engine.isolated_book_worker',stalled_worker), \
             patch('inventory.engine.WORKER_STALL_SECONDS',3):
            self.assertEqual(self.request('/api/scan',{'folder':str(self.folder),
                'output':str(self.folder/'timeout.xlsx'),'ocr':False,
                'online_titles':False,'workers':1})[0],202)
            start=json.loads(self.request('/api/state')[1])
            time.sleep(1.1)
            current=json.loads(self.request('/api/state')[1])
            self.assertTrue(current['running'])
            self.assertGreater(current['elapsed'],start['elapsed'])
            deadline=time.monotonic()+15
            while self.server.dashboard.snapshot()['running'] and time.monotonic()<deadline:
                time.sleep(.1)
        final=self.server.dashboard.snapshot()
        self.assertEqual(final['status'],'Scan complete')
        self.assertIn('No page progress',final['rows'][0]['worker_error'])

    def test_worker_count_accepts_six_and_rejects_invalid_values(self):
        for workers in (0, 7, '6', True):
            body={'folder':str(self.folder),'output':str(self.folder/'invalid.xlsx'),
                  'ocr':False,'online_titles':False,'workers':workers}
            self.assertEqual(self.request('/api/scan',body)[0],400)
        (self.folder/'Book.txt').write_text('Title: Parallel book\n'+
            'This book explains the history of the world for its readers. '*4)
        body={'folder':str(self.folder),'output':str(self.folder/'six.xlsx'),
              'ocr':False,'online_titles':False,'workers':6}
        self.assertEqual(self.request('/api/scan',body)[0],202)
        deadline=time.monotonic()+20
        while self.server.dashboard.snapshot()['running'] and time.monotonic()<deadline:
            time.sleep(.05)
        self.assertEqual(self.server.dashboard.snapshot()['status'],'Scan complete')
        audit=json.loads((self.folder/'six.audit.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['options']['workers'],6)


if __name__ == '__main__':
    unittest.main()


