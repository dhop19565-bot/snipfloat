"""
SnipFloat - System tray snipping tool for Windows
"""

import tkinter as tk
from tkinter import filedialog
import threading
import os
import io
from PIL import Image, ImageTk, ImageDraw, ImageGrab
import pystray
from pystray import MenuItem as item

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── Globals ───────────────────────────────────────────────────────────────
snip_windows = []
tray_icon    = None
_tk_root     = None
_tk_ready    = threading.Event()

ORANGE    = "#FF8C00"
ORANGE_DK = "#CC6600"
GOLD      = "#FFD700"
DARK_BG   = "#1e1e2e"
BLUE      = "#7aa2f7"


# ══════════════════════════════════════════════════════════════════════════
#  ICON
# ══════════════════════════════════════════════════════════════════════════
def _draw_scissors(d, size):
    s = size / 64
    def sp(x, y): return (x*s, y*s)
    def sr(*v):   return [i*s for i in v]
    d.polygon([sp(10,10),sp(16,8),sp(54,44),sp(52,50),sp(46,48),sp(8,14)], fill=ORANGE)
    d.polygon([sp(8,50),sp(14,52),sp(52,16),sp(54,10),sp(48,8),sp(10,44)], fill=ORANGE)
    cx,cy,r = 32*s,32*s,5*s
    d.ellipse([cx-r,cy-r,cx+r,cy+r], fill=GOLD, outline=ORANGE_DK, width=max(1,int(s)))
    d.ellipse(sr(1,40,21,62),  fill=ORANGE, outline=ORANGE_DK, width=max(1,int(1.5*s)))
    d.ellipse(sr(5,44,17,58),  fill=DARK_BG)
    d.ellipse(sr(43,1,63,22),  fill=ORANGE, outline=ORANGE_DK, width=max(1,int(1.5*s)))
    d.ellipse(sr(47,5,59,17),  fill=DARK_BG)

def make_tray_image():
    img = Image.new("RGBA",(64,64),(0,0,0,0))
    d   = ImageDraw.Draw(img)
    d.rounded_rectangle([0,0,63,63], radius=12, fill=DARK_BG)
    _draw_scissors(d,64)
    return img

def make_ico_file():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),"snipfloat.ico")
    sizes,frames = [256,64,48,32,16],[]
    for sz in sizes:
        f = Image.new("RGBA",(sz,sz),(0,0,0,0))
        d = ImageDraw.Draw(f)
        d.rounded_rectangle([0,0,sz-1,sz-1], radius=max(2,sz//5), fill=DARK_BG)
        _draw_scissors(d,sz)
        frames.append(f)
    try:
        frames[0].save(path, format="ICO", append_images=frames[1:],
                       sizes=[(s,s) for s in sizes])
    except Exception: pass
    return path


# ══════════════════════════════════════════════════════════════════════════
#  DPI SCALE
# ══════════════════════════════════════════════════════════════════════════
def get_dpi_scale():
    """Compare tkinter logical screen size to actual pixel size to get scale."""
    try:
        # Use a temporary root to get logical screen dimensions
        test = tk.Toplevel(_tk_root)
        logical_w = test.winfo_screenwidth()
        logical_h = test.winfo_screenheight()
        test.destroy()
        # Grab a tiny 1x1 screenshot to see real pixel space
        # Instead, use ctypes to get true resolution
        import ctypes
        user32 = ctypes.windll.user32
        # SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN = full virtual desktop in real pixels
        real_w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        real_h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        if real_w > 0 and logical_w > 0:
            return real_w / logical_w, real_h / logical_h
    except Exception:
        pass
    return 1.0, 1.0


# ══════════════════════════════════════════════════════════════════════════
#  SELECTION OVERLAY
# ══════════════════════════════════════════════════════════════════════════
class SelectionOverlay:
    def __init__(self):
        self.start_x  = self.start_y = 0
        self.cur_x    = self.cur_y   = 0
        self.dragging = False
        self.dash_off = 0
        self.anim_id  = None
        self.scale_x, self.scale_y = get_dpi_scale()

        tmp = tk.Toplevel(_tk_root)
        self.tk_sw = tmp.winfo_screenwidth()
        self.tk_sh = tmp.winfo_screenheight()
        tmp.destroy()

        self.win = tk.Toplevel(_tk_root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.geometry(f"{self.tk_sw}x{self.tk_sh}+0+0")
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.15)
        self.win.configure(bg="black")
        self.win.config(cursor="crosshair")

        self.cv = tk.Canvas(self.win, width=self.tk_sw, height=self.tk_sh,
                            bd=0, highlightthickness=0, bg="black")
        self.cv.pack(fill=tk.BOTH, expand=True)

        self.hline = self.cv.create_line(0,0,self.tk_sw,0, fill=BLUE, width=1, dash=(5,4))
        self.vline = self.cv.create_line(0,0,0,self.tk_sh, fill=BLUE, width=1, dash=(5,4))

        self.sel_fill  = None
        self.sel_outer = None
        self.sel_inner = None
        self.sel_label = None

        self.cv.bind("<Motion>",          self._move)
        self.cv.bind("<ButtonPress-1>",   self._press)
        self.cv.bind("<B1-Motion>",       self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>",         lambda e: self._cancel())

        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def _move(self, e):
        if not self.dragging:
            self.cv.coords(self.hline, 0, e.y, self.tk_sw, e.y)
            self.cv.coords(self.vline, e.x, 0, e.x, self.tk_sh)

    def _press(self, e):
        self.start_x = self.cur_x = e.x
        self.start_y = self.cur_y = e.y
        self.dragging = True
        self.cv.itemconfigure(self.hline, state="hidden")
        self.cv.itemconfigure(self.vline, state="hidden")
        self.sel_fill  = self.cv.create_rectangle(e.x,e.y,e.x,e.y,
                             fill=BLUE, stipple="gray25", outline="")
        self.sel_outer = self.cv.create_rectangle(e.x,e.y,e.x,e.y,
                             outline="white", width=1)
        self.sel_inner = self.cv.create_rectangle(e.x,e.y,e.x,e.y,
                             outline=BLUE, width=2, dash=(6,4))
        self.sel_label = self.cv.create_text(e.x+4,e.y+4, text="",
                             fill="white", font=("Segoe UI",9,"bold"), anchor="nw")
        self._animate()

    def _drag(self, e):
        self.cur_x, self.cur_y = e.x, e.y
        self._update()

    def _update(self):
        x1,y1 = self.start_x,self.start_y
        x2,y2 = self.cur_x,self.cur_y
        self.cv.coords(self.sel_fill,  x1,y1,x2,y2)
        self.cv.coords(self.sel_outer, x1,y1,x2,y2)
        self.cv.coords(self.sel_inner, x1,y1,x2,y2)
        w,h = abs(x2-x1), abs(y2-y1)
        lx,ly = max(x1,x2)+4, max(y1,y2)+4
        anch = "nw"
        if lx+90 > self.tk_sw: lx = min(x1,x2)-4; anch="ne"
        if ly+22 > self.tk_sh: ly = min(y1,y2)-4
        self.cv.coords(self.sel_label, lx, ly)
        self.cv.itemconfigure(self.sel_label, text=f" {w} × {h} ", anchor=anch)

    def _animate(self):
        if not self.dragging: return
        self.dash_off = (self.dash_off+1) % 10
        self.cv.itemconfigure(self.sel_inner, dashoffset=self.dash_off)
        self.anim_id = self.win.after(60, self._animate)

    def _release(self, e):
        self.dragging = False
        if self.anim_id: self.win.after_cancel(self.anim_id)

        # logical pixel coords of selection
        lx1 = int(min(self.start_x, e.x))
        ly1 = int(min(self.start_y, e.y))
        lx2 = int(max(self.start_x, e.x))
        ly2 = int(max(self.start_y, e.y))

        # real pixel coords for ImageGrab
        rx1 = int(lx1 * self.scale_x);  ry1 = int(ly1 * self.scale_y)
        rx2 = int(lx2 * self.scale_x);  ry2 = int(ly2 * self.scale_y)

        # position for the floating snip window (logical coords)
        sx = max(0, lx1)
        sy = max(0, ly1 - 30)

        self.win.destroy()

        if (lx2-lx1) > 5 and (ly2-ly1) > 5:
            _tk_root.after(250, lambda: _do_grab(rx1, ry1, rx2, ry2, sx, sy))

    def _cancel(self):
        self.dragging = False
        if self.anim_id: self.win.after_cancel(self.anim_id)
        self.win.destroy()


# ══════════════════════════════════════════════════════════════════════════
#  GRAB
# ══════════════════════════════════════════════════════════════════════════
def _do_grab(rx1, ry1, rx2, ry2, sx, sy):
    try:
        # Try without all_screens first (correct origin for single monitor)
        img = ImageGrab.grab(bbox=(rx1, ry1, rx2, ry2))
        if img and img.size[0] > 0 and img.size[1] > 0:
            # Sanity check: if image is mostly black, retry with all_screens
            import numpy as np
            arr = np.array(img.convert("L"))
            if arr.mean() < 5:
                img = ImageGrab.grab(bbox=(rx1, ry1, rx2, ry2), all_screens=True)
            FloatingSnip(img, x=sx, y=sy)
    except ImportError:
        # numpy not available, just grab normally
        try:
            img = ImageGrab.grab(bbox=(rx1, ry1, rx2, ry2))
            FloatingSnip(img, x=sx, y=sy)
        except Exception as ex:
            print(f"Grab error: {ex}")
    except Exception as ex:
        print(f"Grab error: {ex}")


# ══════════════════════════════════════════════════════════════════════════
#  FLOATING SNIP
# ══════════════════════════════════════════════════════════════════════════
class FloatingSnip:
    def __init__(self, image: Image.Image, x, y):
        self.image = image
        self.win   = tk.Toplevel(_tk_root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=DARK_BG)

        w,h = image.size
        if w > 900 or h > 700:
            image.thumbnail((900,700), Image.LANCZOS)
            w,h = image.size

        self.win.geometry(f"{w}x{h+30}+{x}+{y}")

        # ── title bar ─────────────────────────────────────────────────
        bar = tk.Frame(self.win, bg=DARK_BG, height=30)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.pack_propagate(False)
        tk.Label(bar, text="  ✂  snip", bg=DARK_BG, fg="#a9b1d6",
                 font=("Segoe UI",9)).pack(side=tk.LEFT)
        for txt,fg,cmd in [("✕","#f7768e",self.close),
                            ("💾","#9ece6a",self.save),
                            ("📋",BLUE,    self.copy)]:
            tk.Button(bar, text=txt, bg=DARK_BG, fg=fg,
                      font=("Segoe UI",9,"bold"), bd=0, padx=8,
                      activebackground=fg, activeforeground="white",
                      command=cmd).pack(side=tk.RIGHT)

        # ── image ─────────────────────────────────────────────────────
        self.tk_img = ImageTk.PhotoImage(image)
        self.cv = tk.Canvas(self.win, width=w, height=h,
                            bd=0, highlightthickness=0, bg=DARK_BG, cursor="fleur")
        self.cv.pack(fill=tk.BOTH, expand=True)
        self.cv.create_image(0,0, anchor=tk.NW, image=self.tk_img)

        # ── right-click context menu ───────────────────────────────────
        self.menu = tk.Menu(self.win, tearoff=0,
                            bg="#2a2a3e", fg="white",
                            activebackground=BLUE, activeforeground="white",
                            font=("Segoe UI", 10),
                            relief=tk.FLAT, bd=0)
        self.menu.add_command(label="📋  Copy",        command=self.copy)
        self.menu.add_command(label="💾  Save as...",  command=self.save)
        self.menu.add_separator()
        self.menu.add_command(label="✕  Close",        command=self.close)

        # ── drag + right-click bindings ────────────────────────────────
        self._dx = self._dy = 0
        for widget in [bar, self.cv] + list(bar.winfo_children()):
            widget.bind("<ButtonPress-1>",   self._ds)
            widget.bind("<B1-Motion>",       self._dm)
            widget.bind("<ButtonPress-3>",   self._show_menu)

        self.win.lift()
        snip_windows.append(self)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    def _ds(self, e):
        self._dx = e.x_root - self.win.winfo_x()
        self._dy = e.y_root - self.win.winfo_y()

    def _dm(self, e):
        self.win.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}")

    def _show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def copy(self):
        try:
            import win32clipboard
            buf = io.BytesIO()
            self.image.convert("RGB").save(buf,"BMP")
            data = buf.getvalue()[14:]
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            win32clipboard.CloseClipboard()
        except Exception: pass

    def save(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG","*.png"),("JPEG","*.jpg"),("All","*.*")],
            title="Save snip")
        if p: self.image.save(p)

    def close(self):
        if self in snip_windows: snip_windows.remove(self)
        self.win.destroy()


# ══════════════════════════════════════════════════════════════════════════
#  TK + TRAY
# ══════════════════════════════════════════════════════════════════════════
def take_snip():
    _tk_root.after(0, SelectionOverlay)

def close_all_snips():
    for w in list(snip_windows): w.close()

def _run_tk():
    global _tk_root
    _tk_root = tk.Tk()
    _tk_root.withdraw()
    _tk_ready.set()
    _tk_root.mainloop()

def quit_app(icon, _=None):
    close_all_snips()
    icon.stop()
    os._exit(0)

def build_tray():
    global tray_icon
    menu = pystray.Menu(
        item("✂  Take Snip",       lambda i,_: take_snip(), default=True),
        item("🗑  Close All Snips", lambda i,_: _tk_root.after(0, close_all_snips)),
        pystray.Menu.SEPARATOR,
        item("✕  Quit",            quit_app),
    )
    tray_icon = pystray.Icon("SnipFloat", make_tray_image(),
                              "SnipFloat — click to snip", menu)
    tray_icon.run()

if __name__ == "__main__":
    make_ico_file()
    threading.Thread(target=_run_tk, daemon=True).start()
    _tk_ready.wait()
    build_tray()
