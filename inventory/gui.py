"""Pastel dashboard for the local scanner, with native file dialogs and controls."""
import json
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
from .engine import run, Options, DEFAULT_WORKERS, MAX_WORKERS
from .vision import DEFAULT_MODELS, check_models
from .ui_widgets import COLORS as C, rounded, icon, DashboardButton, add_placeholder
from .paths import BUNDLE_ROOT, STATE_DIR, REPORT_DIR

PROJECT = BUNDLE_ROOT


class InventoryApp:
    def __init__(self, root):
        self.root = root
        self.events, self.stop = queue.Queue(), threading.Event()
        self.running, self.output_path = False, None
        self.rows, self.history = [], []
        self.started_at, self.elapsed_seconds = None, 0
        self.total_books, self.done_books = 0, 0
        self.active_nav = 'Scan & Export'
        self.windows, self.side_windows = {}, {}
        self.images = {}
        self.history_path = STATE_DIR / '.dashboard-history.json'
        try:
            self.history = json.loads(self.history_path.read_text(encoding='utf-8'))
            if not isinstance(self.history, list):
                self.history = []
        except (OSError, ValueError):
            pass
        self.art = None
        art_path = PROJECT / 'assets' / 'books-and-plants.png'
        if art_path.exists():
            with Image.open(art_path) as source:
                self.art = source.convert('RGB')

        root.title('EPDF Inventory · Local Book Scanner')
        # Pixel-based layout follows the supplied 1672px dashboard, and scales down
        # together with native controls. Smaller displays can scroll the content.
        root.tk.call('tk', 'scaling', 1.0)
        width = min(1600, root.winfo_screenwidth()-60)
        height = min(930, root.winfo_screenheight()-90)
        root.geometry(f'{width}x{height}')
        root.minsize(1050, 650)
        root.configure(bg=C['background'])
        self._styles()
        self.folder = tk.StringVar()
        self.output = tk.StringVar(value=str(REPORT_DIR / 'inventory.xlsx'))
        self.grouping = tk.StringVar(value='Auto: same filename / ISBN')
        self.image_mode = tk.StringVar(value='Pictures inside images (estimate)')
        self.workers = tk.IntVar(value=DEFAULT_WORKERS)
        self.ocr, self.resume = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        self.search = tk.StringVar()
        self.status = tk.StringVar(value="Choose your book folder and click 'Scan & Export Excel' to begin.")
        self.status_title = tk.StringVar(value='Ready to scan')
        self.summary = tk.StringVar(value='Scanned book metadata will be shown here after export.')
        self.percent = tk.StringVar(value='0%')
        self.elapsed = tk.StringVar(value='00:00:00')
        self.books_found = tk.StringVar(value='0')
        self.book_badge = tk.StringVar(value='0 books')

        self.sidebar = tk.Canvas(root, bg=C['sidebar'], bd=0, highlightthickness=0)
        self.sidebar.pack(side='left', fill='y')
        self.shell = tk.Frame(root, bg=C['background'])
        self.shell.pack(side='left', fill='both', expand=True)
        self.canvas = tk.Canvas(self.shell, bg=C['background'], bd=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.shell, orient='vertical', command=self.canvas.yview)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self._controls()
        self.canvas.bind('<Configure>', self._schedule_layout)
        self.sidebar.bind('<Configure>', self._schedule_layout)
        root.bind('<MouseWheel>', self._mousewheel, add='+')
        root.bind('<Control-f>', self.focus_search)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self._layout_job = None
        root.after(30, self._layout)
        root.after(100, self.poll)

    def _styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Dashboard.TEntry', fieldbackground='#ffffff', foreground=C['ink'],
                        bordercolor=C['border'], lightcolor=C['border'], darkcolor=C['border'], padding=(12, 10))
        style.map('Dashboard.TEntry', bordercolor=[('focus', C['purple'])])
        style.configure('Dashboard.TCombobox', fieldbackground='white', background='white', foreground=C['ink'],
                        bordercolor=C['border'], lightcolor=C['border'], darkcolor=C['border'], padding=(10, 8), arrowsize=14)
        style.map('Dashboard.TCombobox', fieldbackground=[('readonly', 'white')], selectbackground=[('readonly', 'white')],
                  selectforeground=[('readonly', C['ink'])])
        style.configure('Dashboard.Treeview', background='white', fieldbackground='white', foreground=C['ink'],
                        rowheight=35, borderwidth=0, relief='flat')
        style.configure('Dashboard.Treeview.Heading', background='#f0ebf7', foreground='#3b315e',
                        bordercolor='#e8e0f1', relief='flat', padding=(8, 10))
        style.map('Dashboard.Treeview', background=[('selected', '#e9e0fc')], foreground=[('selected', '#33205d')])
        style.map('Dashboard.Treeview.Heading', background=[('active', '#e9e0f4')])
        style.configure('Dashboard.Horizontal.TProgressbar', background=C['purple'], troughcolor='#e5e2ea',
                        bordercolor='#e5e2ea', lightcolor=C['purple'], darkcolor=C['purple'], thickness=16)

    def label(self, variable, bg='white', color=None, bold=False):
        return tk.Label(self.canvas, textvariable=variable, bg=bg, fg=color or C['ink'],
                        anchor='w', bd=0, font=('Segoe UI', 11, 'bold' if bold else 'normal'))

    def _controls(self):
        self.folder_entry = ttk.Entry(self.canvas, textvariable=self.folder, style='Dashboard.TEntry')
        self.folder_hint = add_placeholder(self.folder_entry,self.folder,'Select the folder containing your book files…')
        self.output_entry = ttk.Entry(self.canvas, textvariable=self.output, style='Dashboard.TEntry')
        self.group_combo = ttk.Combobox(self.canvas, textvariable=self.grouping, state='readonly', style='Dashboard.TCombobox',
                                       values=['Auto: same filename / ISBN', 'One book per folder'])
        self.image_combo = ttk.Combobox(self.canvas, textvariable=self.image_mode, state='readonly', style='Dashboard.TCombobox',
                                       values=['Pictures inside images (estimate)', 'External image files only'])
        self.workers_combo = ttk.Combobox(self.canvas, textvariable=self.workers, state='readonly', style='Dashboard.TCombobox',
                                         values=list(range(1, MAX_WORKERS + 1)))
        self.browse_button = DashboardButton(self.canvas, 'Browse Folder', self.pick_folder, 'folder', bg='white')
        self.save_button = DashboardButton(self.canvas, 'Save As…', self.pick_output, 'save', bg='white')
        self.start_button = DashboardButton(self.canvas, 'Scan & Export Excel', self.start, 'play', 'primary', bg='white')
        self.cancel_button = DashboardButton(self.canvas, 'Cancel', self.cancel, 'stop', bg=C['soft'])
        self.open_button = DashboardButton(self.canvas, 'Open Report', self.open_report, 'report', bg=C['soft'])
        self.cancel_button.configure(state='disabled')
        self.open_button.configure(state='disabled')
        self.how_button = DashboardButton(self.canvas, 'How it works?', self.how_it_works, 'help', bg=C['background'])
        self.guide_button = DashboardButton(self.canvas, 'User Guide', self.user_guide, 'book', bg=C['background'])
        self.settings_button = DashboardButton(self.canvas, 'Settings', lambda: self.navigate('Settings'), 'settings', bg=C['background'])
        self.ocr_check = tk.Checkbutton(self.canvas, text='English + German OCR', variable=self.ocr,
                                        bg='#f3effc', activebackground='#f3effc', fg=C['ink'],
                                        selectcolor='white', activeforeground=C['purple'], bd=0, highlightthickness=0,
                                        anchor='w', cursor='hand2')
        self.resume_check = tk.Checkbutton(self.canvas, text='Resume unchanged books', variable=self.resume,
                                           bg='#f3effc', activebackground='#f3effc', fg=C['ink'],
                                           selectcolor='white', activeforeground=C['purple'], bd=0, highlightthickness=0,
                                           anchor='w', cursor='hand2')
        self.status_label = self.label(self.status, '#f3effa', C['muted'])
        self.state_label = self.label(self.status_title, '#f3effa', bold=True)
        self.elapsed_label = self.label(self.elapsed, '#f5f3f8', bold=True)
        self.found_label = self.label(self.books_found, '#f5f3f8', bold=True)
        self.percent_label = self.label(self.percent, 'white', bold=True)
        self.summary_label = self.label(self.summary, 'white', C['muted'])
        self.badge_label = self.label(self.book_badge, '#f7f1f3', bold=True)
        self.badge_label.configure(anchor='center')
        self.progress = ttk.Progressbar(self.canvas, mode='determinate', style='Dashboard.Horizontal.TProgressbar')
        self.search_entry = ttk.Entry(self.canvas, textvariable=self.search, style='Dashboard.TEntry')
        self.search_hint = add_placeholder(self.search_entry,self.search,'Search books…')
        self.search.trace_add('write', lambda *args: self.filter_results())
        self.table_frame = tk.Frame(self.canvas, bg='white')
        self.table_frame.rowconfigure(0, weight=1)
        self.table_frame.columnconfigure(0, weight=1)
        columns = ('number', 'isbn', 'title', 'language', 'type', 'pages', 'images', 'status')
        self.tree = ttk.Treeview(self.table_frame, columns=columns, show='headings', style='Dashboard.Treeview')
        for name, title, width in zip(columns, ('#', 'ISBN', 'Title', 'Language', 'Editable / scanned', 'Pages', 'Images', 'Status'),
                                      (45, 160, 265, 110, 170, 80, 80, 110)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=40 if name == 'number' else 65,
                             anchor='w' if name == 'title' else 'center', stretch=True)
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.table_scroll = ttk.Scrollbar(self.table_frame, orient='vertical', command=self.tree.yview)
        self.table_scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(self.table_frame, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=self.table_scroll.set, xscrollcommand=horizontal.set)
        self.tree.tag_configure('review', foreground='#775391')
        self.tree.bind('<Double-1>', self.show_book_details)
        self.empty = tk.Frame(self.table_frame, bg='white')
        self.empty_icon = tk.Canvas(self.empty, width=45, height=45, bg='white', bd=0, highlightthickness=0)
        self.empty_icon.pack()
        icon(self.empty_icon, 'empty', 3, 1, 40, '#9391ac')
        self.empty_title = tk.Label(self.empty, text='No books scanned yet', bg='white', fg='#48435f', font=('Segoe UI', 11))
        self.empty_title.pack(pady=(5, 0))
        self.empty_message = tk.Label(self.empty, text="Choose a folder and click 'Scan & Export Excel' to get started.",
                                      bg='white', fg=C['muted'], font=('Segoe UI', 10))
        self.empty_message.pack()
        self.empty.place(relx=.5, rely=.65, anchor='center')
        self.nav = {}
        for name, symbol in [('Scan & Export','home'), ('Results','table'), ('History','clock'), ('Settings','settings'), ('Help','help')]:
            self.nav[name] = DashboardButton(self.sidebar, name, lambda n=name: self.navigate(n), symbol,
                                             'nav_active' if name == self.active_nav else 'nav', bg=C['sidebar'])

    def _schedule_layout(self, event=None):
        if self._layout_job:
            self.root.after_cancel(self._layout_job)
        self._layout_job = self.root.after(35, self._layout)

    def _put(self, key, widget, x, y, width, height, side=False):
        host, windows = (self.sidebar, self.side_windows) if side else (self.canvas, self.windows)
        if key not in windows:
            windows[key] = host.create_window(x, y, window=widget, anchor='nw', width=width, height=height)
        else:
            host.coords(windows[key], x, y)
            host.itemconfigure(windows[key], width=width, height=height)
        if isinstance(widget, DashboardButton):
            widget.scale_factor = self.scale
            widget.draw()

    def _layout(self):
        self._layout_job = None
        s = self.scale = max(.65, min(1.15, self.root.winfo_width()/1600, self.canvas.winfo_height()/905))
        side_width = round(175*s)
        if int(float(self.sidebar.cget('width'))) != side_width:
            self.sidebar.configure(width=side_width)
        c = self.canvas
        width = c.winfo_width()
        if width < 100:
            return
        c.delete('art')
        # All artwork is displayed from the bundled original image; no Downloads dependency.
        right = width - 20*s
        left, gap = 20*s, 15*s
        available = right-left
        fs = lambda size: max(8, round(size*s))
        def txt(x,y,text,size=12,bold=False,color=None,anchor='w', **kwargs):
            return c.create_text(x,y,text=text,anchor=anchor,font=('Segoe UI', fs(size), 'bold' if bold else 'normal'),
                                 fill=color or C['ink'],tags='art', **kwargs)
        def box(x,y,w,h,fill='white',radius=14,outline=''):
            return rounded(c,x,y,w,h,radius*s,fill=fill,outline=outline,tags='art')
        def card(x,y,w,h):
            box(x,y+3*s,w,h,fill='#f4edf0')
            box(x,y,w,h,outline='#f0e9ef')
        def badge(x,y,name,color='#6741de',fill='#eee5ff'):
            box(x,y,45*s,45*s,fill,10)
            icon(c,name,x+10*s,y+10*s,25*s,color,'art')
        def put(key,widget,x,y,w,h):
            self._put(key,widget,x,y,w,h)

        if self.art:
            # Runtime display crops are analogous to CSS object-position.
            header = self.art.crop((635,0,self.art.width,self.art.height))
            header.thumbnail((round(345*s), round(205*s)), Image.Resampling.LANCZOS)
            self.images['header'] = ImageTk.PhotoImage(header)
            c.create_image(width+10*s,-5*s,image=self.images['header'],anchor='ne',tags='art')
        icon(c,'logo',left+15*s,25*s,45*s,C['ink'],'art')
        txt(left+85*s,40*s,'EPDF Inventory',29,True)
        box(left+325*s,24*s,160*s,30*s,'#ede3ff',9)
        txt(left+405*s,39*s,'Local Book Scanner',13,True,anchor='center')
        txt(left+85*s,75*s,'Scan local book files and export metadata to Excel.',15)
        txt(left+85*s,99*s,'Supports PDF, TXT and images  ·  English + German  ·  Files stay on this computer.',12,color=C['muted'])
        action_x = right-405*s
        for key,button,x,w in [('how',self.how_button,action_x,145),('guide',self.guide_button,action_x+155*s,125),('settings',self.settings_button,action_x+290*s,115)]:
            put(key,button,x,15*s,w*s,40*s)

        # 01 / 02 source and destination cards.
        top, top_h = 135*s, 180*s
        half = (available-gap)/2
        for x, number, title, subtitle in [(left,'01','Select Book Folder','Choose the folder containing your book files (PDF, TXT, images).'),
                                            (left+half+gap,'02','Excel Output','Choose where to save the inventory report.')]:
            card(x,top,half,top_h)
            box(x+18*s,top+15*s,45*s,45*s,'#eae0ff',12)
            txt(x+40.5*s,top+37*s,number,23,True,'#351598',anchor='center')
            txt(x+80*s,top+29*s,title,15,True)
            txt(x+80*s,top+51*s,subtitle,12,color=C['muted'])
            box(x+18*s,top+125*s,half-36*s,37*s,'#f5f2fa',8)
            icon(c,'info',x+30*s,top+134*s,18*s,'#4c4272','art')
        x = left
        badge(x+18*s,top+73*s,'folder','#8b5d28','#fff3e2')
        put('folder',self.folder_entry,x+73*s,top+73*s,half-248*s,44*s)
        put('browse',self.browse_button,x+half-165*s,top+73*s,147*s,44*s)
        txt(x+57*s,top+143*s,'Supports PDF, TXT and images. Select a folder with multiple books.',11,color='#51496d')
        x = left+half+gap
        badge(x+18*s,top+73*s,'file','#5e586e','#f7f5f8')
        put('output',self.output_entry,x+73*s,top+73*s,half-230*s,44*s)
        put('save',self.save_button,x+half-147*s,top+73*s,129*s,44*s)
        txt(x+57*s,top+143*s,'The scanned book details will be saved in an Excel file.',11,color='#51496d')

        # Scan settings card and action/status strip.
        top, settings_h = 330*s, 295*s
        card(left,top,available,settings_h)
        badge(left+18*s,top+15*s,'settings')
        txt(left+83*s,top+30*s,'Scan Settings',16,True)
        txt(left+83*s,top+53*s,'Configure how books are processed and analyzed.',12,color=C['muted'])
        x1 = left+18*s
        group_w = available*.28
        image_w = available*.30
        check_w = (available-group_w-image_w-50*s)/2
        x2 = x1+group_w+10*s
        x3 = x2+image_w+10*s
        x4 = x3+check_w+10*s
        badge(x1,top+88*s,'layers',C['ink'],'#f8f6fc')
        txt(x1+60*s,top+96*s,'Book grouping',12)
        put('group',self.group_combo,x1+60*s,top+106*s,group_w-60*s,36*s)
        txt(x1+65*s,top+157*s,'Group files by filename or ISBN.',11,color=C['muted'])
        badge(x2,top+88*s,'image','#5e586e','#f8f6fc')
        txt(x2+60*s,top+96*s,'Image count',12)
        put('image',self.image_combo,x2+60*s,top+106*s,image_w-60*s,36*s)
        txt(x2+65*s,top+157*s,'Picture counts are estimates; verify Review.',10,color=C['muted'])
        for x, checkbox, help_text in [(x3,self.ocr_check,'Extract text in English and German.'),
                                         (x4,self.resume_check,'Skip books already scanned.')]:
            box(x,top+83*s,check_w,73*s,'#f3effc',10)
            put('ocr' if checkbox is self.ocr_check else 'resume',checkbox,x+9*s,top+94*s,check_w-18*s,30*s)
            txt(x+29*s,top+134*s,help_text,10,color=C['muted'])
        txt(x1+60*s,top+188*s,'Books processed at once',12)
        put('workers',self.workers_combo,x1+250*s,top+171*s,100*s,36*s)
        txt(x1+365*s,top+188*s,'4 recommended on this PC; choose 1–6.',11,color=C['muted'])
        box(left+15*s,top+225*s,available-30*s,58*s,'#f3effa',10)
        put('start',self.start_button,left+15*s,top+225*s,292*s,58*s)
        put('cancel',self.cancel_button,left+317*s,top+225*s,170*s,58*s)
        put('open',self.open_button,left+497*s,top+225*s,215*s,58*s)
        status_x = left+max(730*s,available*.64)
        badge(status_x,top+232*s,'results','#6943ba','#ebd8ff')
        status_width = max(60*s,right-status_x-80*s)
        put('state',self.state_label,status_x+60*s,top+235*s,status_width,22*s)
        put('status',self.status_label,status_x+60*s,top+258*s,status_width,18*s)

        # Progress, elapsed time and discovered book count.
        top = 640*s
        card(left,top,available,90*s)
        c.create_oval(left+18*s,top+15*s,left+58*s,top+55*s,outline='#e3dded',width=6*s,tags='art')
        c.create_arc(left+18*s,top+15*s,left+58*s,top+55*s,start=15,extent=80,style='arc',outline=C['purple'],width=6*s,tags='art')
        txt(left+78*s,top+23*s,'Scanning Progress',14,True)
        txt(left+78*s,top+44*s,'This may take a few minutes depending on the number of files.',11,color=C['muted'])
        progress_w = available-500*s
        put('progress',self.progress,left+78*s,top+61*s,progress_w,16*s)
        put('percent',self.percent_label,left+78*s+progress_w+15*s,top+57*s,55*s,24*s)
        metric_x = right-325*s
        box(metric_x,top+15*s,307*s,60*s,'#f5f3f8',10)
        badge(metric_x+12*s,top+23*s,'clock',C['ink'],'white')
        txt(metric_x+73*s,top+36*s,'Elapsed time',11,color=C['muted'])
        put('elapsed',self.elapsed_label,metric_x+73*s,top+47*s,95*s,22*s)
        c.create_line(metric_x+174*s,top+28*s,metric_x+174*s,top+63*s,fill='#cfc6db',tags='art')
        txt(metric_x+204*s,top+36*s,'Books found',11,color=C['muted'])
        put('found',self.found_label,metric_x+204*s,top+47*s,80*s,22*s)

        # Searchable results with a real empty state.
        top = 745*s
        result_h = max(185*s,self.canvas.winfo_height()-top-20*s)
        self.results_top, self.content_height = top, top+result_h+20*s
        card(left,top,available,result_h)
        badge(left+15*s,top+10*s,'results','#6741ab','#efddff')
        txt(left+70*s,top+25*s,'Books (Results)',15,True)
        put('summary',self.summary_label,left+70*s,top+39*s,available-640*s,24*s)
        icon(c,'search',right-445*s,top+25*s,20*s,'#55506f','art')
        put('search',self.search_entry,right-415*s,top+15*s,310*s,38*s)
        put('badge',self.badge_label,right-95*s,top+15*s,80*s,38*s)
        put('table',self.table_frame,left+15*s,top+70*s,available-30*s,result_h-82*s)
        for widget in (self.folder_entry,self.output_entry,self.group_combo,self.image_combo,self.workers_combo,self.search_entry):
            widget.configure(font=('Segoe UI',fs(12)))
        for widget in (self.state_label,self.elapsed_label,self.found_label,self.percent_label,self.badge_label):
            widget.configure(font=('Segoe UI',fs(12),'bold'))
        for widget in (self.status_label,self.summary_label):
            widget.configure(font=('Segoe UI',fs(11)))
        for widget in (self.ocr_check,self.resume_check):
            widget.configure(font=('Segoe UI',fs(12),'bold'))
        style = ttk.Style(self.root)
        style.configure('Dashboard.TEntry',padding=(round(12*s),round(6*s)))
        style.configure('Dashboard.TCombobox',padding=(round(10*s),round(3*s)),arrowsize=round(14*s))
        style.configure('Dashboard.Treeview',font=('Segoe UI',fs(11)),rowheight=round(34*s))
        style.configure('Dashboard.Treeview.Heading',font=('Segoe UI',fs(11),'bold'),padding=(round(8*s),round(8*s)))
        c.configure(scrollregion=(0,0,width,self.content_height))
        c.tag_lower('art')
        self._draw_sidebar(side_width,s)

    def _draw_sidebar(self, width, s):
        c = self.sidebar
        c.delete('art')
        height = c.winfo_height()
        if self.art:
            image = self.art.crop((132,360,410,self.art.height))
            image = image.resize((width,round(image.height*width/image.width)),Image.Resampling.LANCZOS)
            self.images['sidebar'] = ImageTk.PhotoImage(image)
            c.create_image(0,height-80*s,image=self.images['sidebar'],anchor='sw',tags='art')
        for i,(name,button) in enumerate(self.nav.items()):
            button.variant = 'nav_active' if name == self.active_nav else 'nav'
            self._put(name,button,8*s,(75+i*55)*s,width-16*s,49*s,side=True)
        c.create_text(30*s,height-43*s,text='EPDF Inventory',anchor='w',fill='#565075',font=('Segoe UI',max(8,round(12*s)),'bold'),tags='art')
        c.create_text(30*s,height-22*s,text='v 1.0.0',anchor='w',fill='#a59ab8',font=('Segoe UI',max(8,round(11*s))),tags='art')
        c.tag_lower('art')

    def _mousewheel(self, event):
        # The results table owns its own scrolling.
        if event.widget is self.tree or str(event.widget).startswith(str(self.tree)):
            return
        self.canvas.yview_scroll(int(-event.delta/120), 'units')

    def navigate(self, name):
        self.active_nav = name
        if name == 'History':
            self.show_history()
        elif name == 'Help':
            self.how_it_works()
        elif name == 'Results':
            self.canvas.yview_moveto(min(1,self.results_top/self.content_height))
            self.search_entry.focus_set()
        elif name == 'Settings':
            self.canvas.yview_moveto(0)
            self.group_combo.focus_set()
        else:
            self.canvas.yview_moveto(0)
            self.folder_entry.focus_set()
        self._draw_sidebar(self.sidebar.winfo_width(),self.scale)

    def focus_search(self, event=None):
        self.navigate('Results')
        self.search_entry.selection_range(0,'end')
        return 'break'

    def pick_folder(self):
        path = filedialog.askdirectory(title='Select folder containing books',parent=self.root)
        if path:
            self.folder.set(path)

    def pick_output(self):
        path = filedialog.asksaveasfilename(title='Save Excel report',defaultextension='.xlsx',
                                           initialfile='inventory.xlsx',filetypes=[('Excel workbook','*.xlsx')],parent=self.root)
        if path:
            self.output.set(path)

    def start(self):
        if self.running:
            return
        folder, output = Path(self.folder.get().strip()), Path(self.output.get().strip())
        if not self.folder.get().strip() or not folder.is_dir():
            messagebox.showerror('Select folder','Select an existing book folder.')
            return
        if output.suffix.lower() != '.xlsx':
            messagebox.showerror('Excel output','Choose a filename ending in .xlsx.')
            return
        if self.ocr.get():
            try:
                check_models(DEFAULT_MODELS)
            except Exception as exc:
                messagebox.showerror('OCR setup required',str(exc))
                return
        if output.exists() and not messagebox.askyesno('Replace report?','This report already exists. Replace it with the new scan?'):
            return
        options = Options(grouping='folder' if self.grouping.get().startswith('One') else 'auto',
                          image_mode='files' if self.image_mode.get().startswith('External') else 'pictures',
                          ocr=self.ocr.get(),workers=self.workers.get())
        resume = self.resume.get()
        self.running = True
        self.stop.clear()
        self.started_at = time.monotonic()
        self.elapsed_seconds, self.total_books, self.done_books = 0, 0, 0
        self.elapsed.set('00:00:00')
        self.books_found.set('0')
        self.percent.set('0%')
        self.progress.configure(value=0,maximum=1)
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.open_button.configure(state='disabled')
        self.rows = []
        self.search.set('')
        self.filter_results()
        self.empty_title.configure(text='Scanning your books…')
        self.empty_message.configure(text='Each completed book will appear here while scanning continues.')
        self.status_title.set('Scanning books')
        self.status.set('Discovering PDF, TXT and image files…')
        self.summary.set('TEXT: first 10 pages only. Pictures: ISBN_img.pdf only; COVER excluded.')
        self._scan_folder = str(folder.resolve())
        def worker():
            try:
                rows,cancelled = run(folder,output,options,
                                     lambda done,total,text: self.events.put(('progress',done,total,text)), self.stop,resume,
                                     on_book_complete=lambda row:self.events.put(('book',row)))
                self.events.put(('done',rows,cancelled,output.resolve()))
            except Exception as exc:
                self.events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def cancel(self):
        if not self.running:
            return
        self.stop.set()
        self.status_title.set('Saving progress')
        self.status.set('Waiting for current OCR operation; completed books will be saved…')
        self.cancel_button.configure(state='disabled')

    def _update_progress(self,done,total,text):
        self.done_books,self.total_books = done,total
        self.books_found.set(str(total))
        self.progress.configure(maximum=max(1,total),value=done)
        self.percent.set(f'{round(done/max(1,total)*100)}%')
        if not self.stop.is_set():
            self.status.set(f'{done}/{total} books · {text}')

    def poll(self):
        if self.running and self.started_at:
            self.elapsed_seconds = int(time.monotonic()-self.started_at)
            h,rest = divmod(self.elapsed_seconds,3600)
            m,sec = divmod(rest,60)
            self.elapsed.set(f'{h:02}:{m:02}:{sec:02}')
        latest = None
        for _ in range(300):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'progress':
                latest = event
                continue
            if event[0] == 'book':
                self.rows.append(event[1])
                self.filter_results()
                self.summary.set(f'{len(self.rows)} books completed · New entries appear as each book finishes.')
                continue
            if latest:
                self._update_progress(*latest[1:])
                latest = None
            self.running = False
            self.start_button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
            if event[0] == 'error':
                self.status_title.set('Scan failed')
                self.status.set('Completed checkpoints can be resumed.')
                self.empty_title.configure(text='Scan could not finish')
                self.empty_message.configure(text='Check the selected folder and output path, then try again.')
                messagebox.showerror('Scan failed',event[1])
            else:
                _,rows,cancelled,output = event
                self.rows,self.output_path = rows,output
                self.open_button.configure(state='normal')
                self.status_title.set('Partial report saved' if cancelled else 'Scan complete')
                self.status.set(str(output))
                self.summary.set(f'{len(rows)} books exported · Double-click a row for review notes.')
                self.filter_results()
                if not cancelled:
                    self.progress.configure(value=max(1,self.total_books))
                    self.percent.set('100%')
                self._record_history(output,len(rows),cancelled)
        if latest:
            self._update_progress(*latest[1:])
        self.root.after(100,self.poll)

    def filter_results(self):
        self.tree.delete(*self.tree.get_children())
        query = self.search.get().strip().casefold()
        visible = 0
        for index,row in enumerate(self.rows,1):
            if query and query not in ' '.join(str(row.get(k,'')) for k in ('isbn','title','language','book_type')).casefold():
                continue
            review = bool(row.get('notes'))
            values = [index]+[row[k] for k in ('isbn','title','language','book_type','page_count','image_count')]+['Review' if review else 'Ready']
            self.tree.insert('', 'end',iid=str(index-1),values=values,tags=('review',) if review else ())
            visible += 1
        self.book_badge.set(f'{visible} / {len(self.rows)}' if query else f'{len(self.rows)} books')
        if visible:
            self.empty.place_forget()
        else:
            self.empty_title.configure(text='No matching books' if query else 'No books scanned yet')
            self.empty_message.configure(text='Try a different title, ISBN or language.' if query else "Choose a folder and click 'Scan & Export Excel' to get started.")
            self.empty.place(relx=.5,rely=.65,anchor='center')

    def show_book_details(self,event=None):
        selection = self.tree.selection()
        if not selection:
            return
        row = self.rows[int(selection[0])]
        messagebox.showinfo(row['title'], 'Source files\n'+'\n'.join(row['sources'])+'\n\nReview notes\n'+
                            ('\n'.join(row.get('notes',[])) or 'No review notes.')+'\n\nImage method\n'+row.get('image_method',''))

    def _record_history(self,output,count,cancelled):
        self.history.insert(0,{'time':time.strftime('%Y-%m-%d %H:%M'), 'folder':self._scan_folder,
                               'output':str(output),'books':count,'status':'Partial' if cancelled else 'Complete'})
        self.history = self.history[:100]
        try:
            self.history_path.parent.mkdir(parents=True,exist_ok=True)
            temporary = self.history_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.history,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.history_path)
        except OSError:
            self.summary.set(self.summary.get()+' History could not be saved.')

    def show_history(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('Scan history')
        dialog.geometry('900x440')
        dialog.configure(bg=C['background'])
        dialog.transient(self.root)
        tk.Label(dialog,text='Recent scans',font=('Segoe UI',20,'bold'),bg=C['background'],fg=C['ink']).pack(anchor='w',padx=25,pady=20)
        tk.Label(dialog,text='Double-click a scan to open its Excel report.',bg=C['background'],fg=C['muted']).pack(anchor='w',padx=25)
        tree = ttk.Treeview(dialog,columns=('time','books','status','output'),show='headings',style='Dashboard.Treeview')
        for name,width in [('time',140),('books',70),('status',80),('output',520)]:
            tree.heading(name,text=name.title())
            tree.column(name,width=width)
        tree.pack(fill='both',expand=True,padx=25,pady=20)
        for i,item in enumerate(self.history):
            tree.insert('','end',iid=str(i),values=[item.get(k,'') for k in ('time','books','status','output')])
        if not self.history:
            tree.insert('','end',values=('No scans yet','','','Run your first scan to see it here.'))
        def open_history(event):
            chosen = tree.selection()
            if chosen and chosen[0].isdigit():
                self._open_path(Path(self.history[int(chosen[0])]['output']))
        tree.bind('<Double-1>',open_history)

    def how_it_works(self):
        messagebox.showinfo('How it works',
            '1. Select your local book folder. Subfolders are included.\n\n'
            '2. Choose the Excel output and grouping. Use one book per folder only when that matches your collection.\n\n'
            '3. Keep English + German OCR enabled for scanned PDFs and images.\n\n'
            '4. Click Scan & Export Excel. Cancel saves completed books, and Resume skips unchanged books next time.\n\n'
            '5. Search the results or double-click a row for review notes. Open the Excel Review sheet to verify uncertain fields.\n\n'
            'ISBN_txt.pdf: OCR first 10 pages for title/language. ISBN_img.pdf: analyse all image pages for picture estimates. COVER files are excluded. Same ISBN sources produce one row.')

    def user_guide(self):
        self._open_path(PROJECT/'README.md')

    def _open_path(self,path):
        if not path.exists():
            messagebox.showerror('File not found',f'This file is no longer available:\n{path}')
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror('Open file',str(exc))

    def open_report(self):
        if self.output_path:
            self._open_path(self.output_path)

    def close(self):
        if self.running:
            self.cancel()
            messagebox.showinfo('Saving progress','Cancellation requested. Wait until the partial report is saved, then close.')
        else:
            self.root.destroy()


def launch():
    root = tk.Tk()
    InventoryApp(root)
    root.mainloop()

