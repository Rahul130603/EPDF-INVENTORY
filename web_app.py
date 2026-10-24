"""Loopback-only browser dashboard for the existing inventory engine."""
import argparse
import json
import mimetypes
import multiprocessing
import os
from pathlib import Path
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from inventory.paths import BUNDLE_ROOT, STATE_DIR, REPORT_DIR, FROZEN

ROOT = BUNDLE_ROOT


class Dashboard:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.token = secrets.token_urlsafe(32)
        self.isbn_lists = {}
        self.history_file = STATE_DIR / '.web-history.json'
        try:
            self.history = json.loads(self.history_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.history = []
        self.data = dict(running=False, done=0, total=0, message='Choose your book folder to begin.',
                         status='Ready to scan', rows=[], started=None, elapsed=0, report=None, error=None)

    def snapshot(self):
        with self.lock:
            data = dict(self.data)
            data['rows'] = list(self.data['rows'])
            if data['running'] and data['started']:
                data['elapsed'] = int(time.time()-data['started'])
            return {**data, 'token': self.token, 'history': self.history, 'default_output': str(REPORT_DIR/'inventory.xlsx'),
                    'workflow':'isbn-list-filter-v10','txt_ocr_pages':10,'online_titles':True,'live_results':True,'workers':4}

    def start(self, body):
        from inventory.engine import Options, DEFAULT_WORKERS, MAX_WORKERS
        from inventory.vision import DEFAULT_MODELS, check_models
        folder_text, output_text = str(body.get('folder','')).strip(), str(body.get('output','')).strip()
        if not folder_text or not Path(folder_text).is_dir():
            raise ValueError('Select an existing book folder.')
        folder, output = Path(folder_text).resolve(), Path(output_text).resolve()
        if not output_text or output.suffix.lower() != '.xlsx':
            raise ValueError('Choose an Excel output filename ending in .xlsx.')
        grouping, image_mode = body.get('grouping','auto'), body.get('image_mode','pictures')
        if grouping not in ('auto','folder') or image_mode not in ('pictures','files'):
            raise ValueError('Invalid scan settings.')
        list_id=body.get('isbn_list_id')
        with self.lock:
            requested=self.isbn_lists.get(list_id) if list_id else None
        if list_id and requested is None:
            raise ValueError('Upload the ISBN Excel sheet again before scanning.')
        options = Options(grouping=grouping,image_mode=image_mode,ocr=bool(body.get('ocr',True)),online_titles=bool(body.get('online_titles',True)),workers=body.get('workers',DEFAULT_WORKERS),fast_isbn_folders=bool(body.get('fast_isbn_folders',False)))
        if type(options.workers) is not int or not 1 <= options.workers <= MAX_WORKERS:
            raise ValueError(f'Workers must be between 1 and {MAX_WORKERS}')
        if requested is not None:
            options.isbn_filter=tuple(requested)
        if options.ocr:
            check_models(DEFAULT_MODELS)
        with self.lock:
            if self.data['running']:
                return 409, {'error':'A scan is already running.'}
            if output.exists() and not body.get('overwrite',False):
                return 409, {'error':'This Excel report already exists. Replace it?', 'code':'exists'}
            self.stop.clear()
            self.data.update(running=True,done=0,total=0,status='Scanning books',message='Discovering files…',
                             rows=[],started=time.time(),elapsed=0,error=None,report=None)
        threading.Thread(target=self.worker,args=(folder,output,options,bool(body.get('resume',True))),daemon=True).start()
        return 202, {'ok':True}

    def worker(self, folder, output, options, resume):
        from inventory.engine import run, atomic_json
        def progress(done,total,message):
            with self.lock:
                self.data.update(done=done,total=total)
                if not self.stop.is_set():
                    self.data['message'] = message
        def book_complete(row):
            with self.lock:
                self.data['rows'].append({k:v for k,v in row.items() if k != 'regions'})
        try:
            rows,cancelled = run(folder,output,options,progress,self.stop,resume,on_book_complete=book_complete)
            with self.lock:
                self.data.update(rows=[{k:v for k,v in row.items() if k != 'regions'} for row in rows],report=str(output),
                                 status='Partial report saved' if cancelled else 'Scan complete', message=str(output))
                self.history.insert(0,dict(id=secrets.token_hex(8),time=time.strftime('%Y-%m-%d %H:%M'),
                                          folder=str(folder),output=str(output),books=len(rows),status=self.data['status']))
                self.history = self.history[:100]
                history = list(self.history)
            try:
                atomic_json(self.history_file,history)
            except OSError:
                pass  # Report remains available even if optional history cannot be saved.
        except Exception as exc:
            with self.lock:
                self.data.update(status='Scan failed',message=str(exc),error=str(exc))
        finally:
            with self.lock:
                self.data.update(running=False,elapsed=int(time.time()-self.data['started']))


class Handler(BaseHTTPRequestHandler):
    server_version = 'EPDFLocal/1.0'

    def allowed(self):
        hosts = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
        if self.headers.get('Host','') not in hosts:
            self.send_json(403,{'error':'Local dashboard requests only.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in hosts}:
            self.send_json(403,{'error':'Cross-origin requests are not allowed.'})
            return False
        return True

    def send_data(self,status,data,content_type,extra=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for key,value in (extra or {}).items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self,status,value):
        self.send_data(status,json.dumps(value,ensure_ascii=False).encode('utf-8'),'application/json; charset=utf-8')

    def do_GET(self):
        if not self.allowed():
            return
        url = urlsplit(self.path)
        app = self.server.dashboard
        try:
            if url.path == '/api/state':
                return self.send_json(200,app.snapshot())
            if url.path == '/api/folders':
                params = parse_qs(url.query)
                path = params.get('path',[''])[0]
                if not path:
                    drives = [f'{letter}:\\' for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if Path(f'{letter}:\\').exists()]
                    return self.send_json(200,{'path':'','parent':None,'folders':drives})
                folder = Path(path).resolve()
                if not folder.is_dir():
                    raise ValueError('Folder does not exist.')
                names = []
                for child in folder.iterdir():
                    try:
                        if child.is_dir() and not child.name.startswith(('.', '$')):
                            names.append(child.name)
                    except OSError:
                        continue
                return self.send_json(200,{'path':str(folder),'parent':str(folder.parent) if folder.parent != folder else '',
                                           'folders':sorted(names,key=str.casefold)})
            if url.path == '/api/report':
                requested = parse_qs(url.query).get('id',[''])[0]
                with app.lock:
                    path = next((item['output'] for item in app.history if item['id'] == requested),None) if requested else app.data['report']
                if not path or not Path(path).is_file():
                    return self.send_json(404,{'error':'Report not found.'})
                return self.send_data(200,Path(path).read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                      {'Content-Disposition':'attachment; filename="inventory.xlsx"'})
            files = {'/':ROOT/'web'/'index.html','/app.css':ROOT/'web'/'app.css','/app.js':ROOT/'web'/'app.js',
                     '/assets/books-and-plants.png':ROOT/'assets'/'books-and-plants.png'}
            path = files.get(url.path)
            if path and path.is_file():
                mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
                return self.send_data(200,path.read_bytes(),mime+('; charset=utf-8' if mime.startswith(('text/','application/javascript')) else ''))
            return self.send_json(404,{'error':'Not found.'})
        except (ValueError,OSError) as exc:
            return self.send_json(400,{'error':str(exc)})

    def do_POST(self):
        if not self.allowed():
            return
        app = self.server.dashboard
        if not secrets.compare_digest(self.headers.get('X-EPDF-Token',''),app.token):
            return self.send_json(403,{'error':'Refresh this dashboard before continuing.'})
        try:
            length = int(self.headers.get('Content-Length','0'))
            if self.path == '/api/isbn-list':
                if not 0 < length <= 5_000_000:
                    raise ValueError('Upload an .xlsx file smaller than 5 MB.')
                from inventory.isbn_filter import read_isbn_sheet
                isbns=read_isbn_sheet(self.rfile.read(length))
                list_id=secrets.token_hex(16)
                with app.lock:
                    if len(app.isbn_lists)>=20:
                        app.isbn_lists.pop(next(iter(app.isbn_lists)))
                    app.isbn_lists[list_id]=isbns
                return self.send_json(200,{'id':list_id,'count':len(isbns)})
            if not 0 <= length <= 32768:
                raise ValueError('Request too large.')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body,dict):
                raise ValueError('Invalid request.')
            if self.path == '/api/open-report':
                with app.lock:
                    report_id=body.get('id')
                    saved=next((item['output'] for item in app.history if item['id']==report_id),None) if report_id else app.data['report']
                if not saved or not Path(saved).is_file():
                    return self.send_json(404,{'error':'Saved report not found. Run the scan first.'})
                path=Path(saved).resolve()
                if path.suffix.lower() != '.xlsx':
                    raise ValueError('Only saved Excel reports can be opened.')
                os.startfile(str(path))
                return self.send_json(200,{'ok':True,'path':str(path)})
            if self.path == '/api/scan':
                status,data = app.start(body)
                return self.send_json(status,data)
            if self.path == '/api/cancel':
                with app.lock:
                    if app.data['running']:
                        app.stop.set()
                        app.data.update(status='Saving progress',message='Waiting for current OCR operation to finish…')
                return self.send_json(200,{'ok':True})
            return self.send_json(404,{'error':'Not found.'})
        except (ValueError,RuntimeError,OSError) as exc:
            return self.send_json(400,{'error':str(exc)})

    def log_message(self,format,*args):
        if '/api/state' not in str(args):
            super().log_message(format,*args)


def create_server(port=4510):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.dashboard = Dashboard()
    return server


def main():
    parser = argparse.ArgumentParser(description='EPDF Inventory local browser dashboard')
    parser.add_argument('--port',type=int,default=4510)
    parser.add_argument('--open-browser',action='store_true',default=FROZEN)
    parser.add_argument('--no-open-browser',dest='open_browser',action='store_false')
    args = parser.parse_args()
    server = create_server(args.port)
    print(f'EPDF Inventory: http://localhost:{server.server_port}',flush=True)
    if args.open_browser:
        import webbrowser
        webbrowser.open(f'http://localhost:{server.server_port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.dashboard.stop.set()
        server.server_close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()

