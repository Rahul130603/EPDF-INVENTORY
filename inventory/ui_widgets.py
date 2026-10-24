"""Small native dashboard primitives; no web server or extra UI dependency."""
import tkinter as tk
import math

COLORS = {
    "background": "#fffaf7", "sidebar": "#f0eaf7", "surface": "#ffffff",
    "ink": "#11112e", "muted": "#71718b", "purple": "#6741de",
    "hover": "#5732cd", "lavender": "#f1edfc", "border": "#e4deed",
    "soft": "#f7f5fb", "success": "#208266",
}


def rounded(canvas, x, y, box_width, box_height, radius=12, **kwargs):
    width, height = box_width, box_height
    radius = min(radius, width / 2, height / 2)
    points = [x+radius,y, x+width-radius,y, x+width,y, x+width,y+radius,
              x+width,y+height-radius, x+width,y+height, x+width-radius,y+height,
              x+radius,y+height, x,y+height, x,y+height-radius, x,y+radius, x,y]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def icon(canvas, name, x, y, size=24, color=None, tag=None):
    color = color or COLORS['ink']
    k = size / 24
    options = {"tags": tag} if tag else {}
    def line(*points, **kw):
        return canvas.create_line(*[x+p*k if i % 2 == 0 else y+p*k for i,p in enumerate(points)],
                                  fill=color, width=max(1.3, 1.7*k), capstyle='round', joinstyle='round', **options, **kw)
    def rect(a,b,c,d, **kw):
        return canvas.create_rectangle(x+a*k,y+b*k,x+c*k,y+d*k,outline=color,width=max(1.2,1.6*k), **options, **kw)
    def oval(a,b,c,d, **kw):
        return canvas.create_oval(x+a*k,y+b*k,x+c*k,y+d*k,outline=color,width=max(1.2,1.6*k), **options, **kw)
    if name in ('book', 'logo'):
        if name == 'logo':
            canvas.create_polygon(x+2*k,y+5*k,x+10*k,y+2*k,x+12*k,y+4*k,x+20*k,y+1*k,x+22*k,y+4*k,x+22*k,y+22*k,x+13*k,y+21*k,x+11*k,y+23*k,x+2*k,y+20*k,fill='#c7b4df',outline=color,width=1.7*k,**options)
            canvas.create_polygon(x+5*k,y+4*k,x+11*k,y+2*k,x+12*k,y+5*k,x+19*k,y+3*k,x+19*k,y+19*k,x+12*k,y+21*k,x+5*k,y+18*k,fill='#ffe5b7',outline=color,width=1.4*k,**options)
            line(12,5,12,21)
        else:
            line(12,5,8,3,3,3,3,20,8,20,12,22,16,20,21,20,21,3,16,3,12,5,12,22)
    elif name == 'home':
        line(2,11,12,3,22,11)
        rect(5,10,19,21)
        rect(10,15,14,21)
    elif name in ('table', 'results'):
        rect(3,3,21,21)
        line(8,3,8,21)
        for yy in (8,12,16):
            line(11,yy,18,yy)
        line(5,7,6,7)
    elif name in ('clock', 'history'):
        oval(2,2,22,22)
        line(12,6,12,12,16,15)
    elif name == 'settings':
        for i in range(8):
            angle = i*math.pi/4
            a,b = math.cos(angle), math.sin(angle)
            line(12+8*a,12+8*b,12+10.5*a,12+10.5*b)
        oval(4,4,20,20)
        oval(9,9,15,15)
    elif name in ('help', 'info'):
        oval(3,3,21,21)
        if name == 'help':
            line(8,8,10,6,14,6,16,8,15,11,12,13,12,15)
        else:
            line(12,10,12,16)
            oval(11.5,6.5,12.5,7.5,fill=color)
        if name == 'help':
            oval(11.5,18,12.5,19,fill=color)
    elif name == 'folder':
        line(2,20,2,6,9,6,11,9,21,9,21,11)
        line(2,20,6,11,23,11,19,20,2,20)
    elif name in ('file', 'guide'):
        line(5,2,14,2,20,8,20,22,5,22,5,2)
        line(14,2,14,8,20,8)
        line(9,12,16,12)
        line(9,16,16,16)
    elif name == 'save':
        line(3,3,18,3,21,6,21,21,3,21,3,3)
        rect(7,3,16,10)
        rect(7,15,17,21)
    elif name == 'image':
        rect(3,3,21,21)
        oval(6,6,10,10)
        line(3,18,9,12,13,16,17,11,21,16)
    elif name == 'layers':
        for yy in (5,10,15):
            line(3,yy+3,12,yy,21,yy+3,12,yy+6,3,yy+3)
    elif name == 'ocr':
        for a,b,c,d,e,f in ((3,8,3,3,8,3),(16,3,21,3,21,8),(21,16,21,21,16,21),(8,21,3,21,3,16)):
            line(a,b,c,d,e,f)
        line(8,16,10,8,12,16)
        line(9,13,11,13)
        line(15,8,15,16,18,16)
    elif name == 'play':
        canvas.create_polygon(x+8*k,y+5*k,x+19*k,y+12*k,x+8*k,y+19*k,fill=color,outline=color,**options)
    elif name == 'stop':
        rounded(canvas,x+5*k,y+5*k,14*k,14*k,2*k,fill=color,outline='',**options)
    elif name == 'report':
        for a,b,c,d in ((3,12,7,22),(10,4,14,22),(17,8,21,22)):
            rect(a,b,c,d)
    elif name == 'search':
        oval(3,3,16,16)
        line(15,15,22,22)
    elif name == 'empty':
        rounded(canvas,x+5*k,y+2*k,15*k,20*k,2*k,fill='',outline=color,width=1.5*k,**options)
        line(8,7,17,7)
        line(8,11,17,11)
        line(8,15,14,15)


class DashboardButton(tk.Canvas):
    def __init__(self, parent, text, command, symbol=None, variant='light', **kwargs):
        super().__init__(parent, highlightthickness=0, bd=0, takefocus=1, cursor='hand2', **kwargs)
        self.label, self.command, self.symbol, self.variant = text, command, symbol, variant
        self.scale_factor = 1
        self.enabled = True
        self.hover = False
        self.bind('<Configure>', lambda e: self.draw())
        self.bind('<Enter>', lambda e: self.set_hover(True))
        self.bind('<Leave>', lambda e: self.set_hover(False))
        self.bind('<Button-1>', self.click)
        self.bind('<Return>', self.click)
        self.bind('<space>', self.click)
        self.bind('<FocusIn>', lambda e: self.draw())
        self.bind('<FocusOut>', lambda e: self.draw())

    def set_hover(self, value):
        self.hover = value
        self.draw()

    def click(self, event=None):
        if self.enabled:
            self.focus_set()
            self.command()

    def configure(self, cnf=None, **kwargs):
        if 'state' in kwargs:
            self.enabled = kwargs['state'] != 'disabled'
        result = super().configure(cnf, **kwargs)
        if hasattr(self, 'enabled'):
            self.draw()
        return result

    config = configure

    def draw(self):
        self.delete('all')
        w, h, s = self.winfo_width(), self.winfo_height(), self.scale_factor
        primary = self.variant in ('primary', 'nav_active')
        transparent = self.variant == 'nav'
        fill = (COLORS['hover'] if self.hover else COLORS['purple']) if primary else (COLORS['lavender'] if self.hover else COLORS['soft'])
        if self.variant == 'nav_active':
            fill = '#8975ac' if not self.hover else '#776199'
        if transparent:
            fill = '#e6ddf3' if self.hover else COLORS['sidebar']
        color = 'white' if primary else COLORS['ink']
        if not self.enabled:
            fill, color = '#f0eef4', '#828195'
        focused = self.focus_get() == self
        rounded(self, 1, 1, max(1,w-2), max(1,h-2), 9*s,
                fill=fill, outline=COLORS['purple'] if focused else (fill if primary or transparent else COLORS['border']), width=1)
        size = 22*s
        text_size = max(9, round((15 if self.variant == 'primary' else 12)*s))
        font = ('Segoe UI', text_size, 'bold' if primary else 'normal')
        if self.variant.startswith('nav'):
            left = 15*s
            icon(self,self.symbol,left,(h-size)/2,size,color)
            self.create_text(left+size+14*s,h/2,text=self.label,anchor='w',font=font,fill=color)
        else:
            import tkinter.font as tkfont
            tw = tkfont.Font(font=font).measure(self.label)
            total = tw + (size + 12*s if self.symbol else 0)
            left = max(8*s, (w-total)/2)
            if self.symbol:
                icon(self,self.symbol,left,(h-size)/2,size,color)
                left += size+12*s
            self.create_text(left,h/2,text=self.label,anchor='w',font=font,fill=color)


def add_placeholder(entry, variable, text):
    """A visual hint that never enters the input's actual StringVar value."""
    hint = tk.Label(entry,text=text,bg='white',fg=COLORS['muted'],bd=0,anchor='w',cursor='xterm')
    def refresh(event=None):
        hint.configure(font=entry.cget('font'))
        if not variable.get() and entry.focus_get() != entry:
            hint.place(x=12,rely=.5,anchor='w',relwidth=.88)
        else:
            hint.place_forget()
    hint.bind('<Button-1>',lambda e: entry.focus_set())
    for name in ('<FocusIn>','<FocusOut>','<Configure>'):
        entry.bind(name,refresh,add='+')
    variable.trace_add('write',lambda *args: refresh())
    refresh()
    return hint
