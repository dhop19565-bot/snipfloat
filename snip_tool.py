"""
DarcySnipTool - System tray snipping tool for Windows
Ctrl+Shift+S or click tray icon to snip.
"""

import tkinter as tk
from tkinter import filedialog
import threading
import os
import io
import ctypes
from PIL import Image, ImageTk, ImageDraw, ImageGrab
import pystray
from pystray import MenuItem as item

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── Globals ───────────────────────────────────────────────────────────────
snip_windows     = []
tray_icon        = None
_tk_root         = None
_tk_ready        = threading.Event()
_overlay_lock    = threading.Lock()

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
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),"darcysniptool.ico")
    sizes, frames = [256,64,48,32,16], []
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
#  VIRTUAL DESKTOP SIZE  (all monitors, real pixels)
# ══════════════════════════════════════════════════════════════════════════
def get_virtual_desktop():
    u = ctypes.windll.user32
    l = u.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    t = u.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    w = u.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    h = u.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    return l, t, w, h


# ══════════════════════════════════════════════════════════════════════════
#  SELECTION OVERLAY  — tkinter, but using the screenshot as background
#  so the window is fully opaque (clicks always work) yet looks transparent
# ══════════════════════════════════════════════════════════════════════════
class SelectionOverlay:
    def __init__(self):
        self.start_x  = self.start_y = 0
        self.cur_x    = self.cur_y   = 0
        self.dragging = False
        self.dash_off = 0
        self.anim_id  = None

        # --- 1. Grab the full virtual desktop BEFORE showing any window ---
        vl, vt, vw, vh = get_virtual_desktop()
        self.vl, self.vt = vl, vt
        self.vw, self.vh = vw, vh

        try:
            self.screenshot = ImageGrab.grab(
                bbox=(vl, vt, vl+vw, vt+vh), all_screens=True)
        except Exception:
            self.screenshot = ImageGrab.grab()
            vw, vh = self.screenshot.size
            self.vw, self.vh = vw, vh

        # --- 2. Build a SEPARATE Tk root for the overlay ---
        #        (avoids any shared state with _tk_root)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)

        # Place at virtual desktop origin, size = full virtual desktop
        self.root.geometry(f"{vw}x{vh}+{vl}+{vt}")
        self.root.attributes("-topmost", True)
        self.root.config(cursor="crosshair")
        self.root.configure(bg="black")

        # --- 3. Canvas with screenshot as background ---
        self.cv = tk.Canvas(self.root, width=vw, height=vh,
                            bd=0, highlightthickness=0, bg="black")
        self.cv.pack(fill=tk.BOTH, expand=True)

        # Background: screenshot
        self.bg_photo = ImageTk.PhotoImage(self.screenshot)
        self.cv.create_image(0, 0, anchor=tk.NW, image=self.bg_photo)

        # Dark overlay on top of screenshot
        self.cv.create_rectangle(0, 0, vw, vh,
                                 fill="black", stipple="gray25", outline="")

        # Crosshair lines
        self.hline = self.cv.create_line(0,0, vw,0,
                                          fill=BLUE, width=1, dash=(6,4))
        self.vline = self.cv.create_line(0,0, 0,vh,
                                          fill=BLUE, width=1, dash=(6,4))

        self.sel_clear = None   # rectangle to reveal unshaded screenshot
        self.sel_outer = None
        self.sel_inner = None
        self.sel_label = None

        self.cv.bind("<Motion>",          self._move)
        self.cv.bind("<ButtonPress-1>",   self._press)
        self.cv.bind("<B1-Motion>",       self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.root.bind("<Escape>",        lambda e: self._cancel())

        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.mainloop()   # blocks until destroyed

    # ── crosshair ──────────────────────────────────────────────────────
    def _move(self, e):
        if not self.dragging:
            self.cv.coords(self.hline, 0, e.y, self.vw, e.y)
            self.cv.coords(self.vline, e.x, 0, e.x, self.vh)

    # ── start drag ─────────────────────────────────────────────────────
    def _press(self, e):
        self.start_x = self.cur_x = e.x
        self.start_y = self.cur_y = e.y
        self.dragging = True

        self.cv.itemconfigure(self.hline, state="hidden")
        self.cv.itemconfigure(self.vline, state="hidden")

        # Reveal screenshot inside selection by re-drawing the bg image
        # clipped to the selection rect — simplest: just remove the dim there
        self.sel_clear = self.cv.create_image(
            e.x, e.y, anchor=tk.NW, image=self.bg_photo)   # placeholder
        self.sel_outer = self.cv.create_rectangle(
            e.x,e.y,e.x,e.y, outline="white", width=1)
        self.sel_inner = self.cv.create_rectangle(
            e.x,e.y,e.x,e.y, outline=BLUE, width=2, dash=(6,4))
        self.sel_label = self.cv.create_text(
            e.x+4, e.y+4, text="", fill="white",
            font=("Segoe UI",9,"bold"), anchor="nw")

        self._animate()

    # ── drag ───────────────────────────────────────────────────────────
    def _drag(self, e):
        self.cur_x, self.cur_y = e.x, e.y
        self._update()

    def _update(self):
        x1,y1 = self.start_x, self.start_y
        x2,y2 = self.cur_x,   self.cur_y
        rx1,ry1 = min(x1,x2), min(y1,y2)
        rx2,ry2 = max(x1,x2), max(y1,y2)

        # Move the clear-image to top-left of selection
        self.cv.coords(self.sel_clear, rx1, ry1)
        self.cv.coords(self.sel_outer, rx1,ry1,rx2,ry2)
        self.cv.coords(self.sel_inner, rx1,ry1,rx2,ry2)

        w,h = rx2-rx1, ry2-ry1
        lx,ly = rx2+6, ry2+6
        anch = "nw"
        if lx+110 > self.vw: lx = rx1-6; anch="ne"
        if ly+22  > self.vh: ly = ry1-6
        self.cv.coords(self.sel_label, lx, ly)
        self.cv.itemconfigure(self.sel_label,
                              text=f" {w} × {h} px ", anchor=anch)

    # ── marching ants ──────────────────────────────────────────────────
    def _animate(self):
        if not self.dragging: return
        self.dash_off = (self.dash_off+1) % 10
        self.cv.itemconfigure(self.sel_inner, dashoffset=self.dash_off)
        self.anim_id = self.root.after(60, self._animate)

    # ── release → crop & show ──────────────────────────────────────────
    def _release(self, e):
        self.dragging = False
        if self.anim_id: self.root.after_cancel(self.anim_id)

        rx1 = min(self.start_x, e.x);  ry1 = min(self.start_y, e.y)
        rx2 = max(self.start_x, e.x);  ry2 = max(self.start_y, e.y)

        self.root.destroy()

        if (rx2-rx1) > 5 and (ry2-ry1) > 5:
            cropped = self.screenshot.crop((rx1, ry1, rx2, ry2))
            # Screen position = virtual desktop origin + canvas offset
            sx = self.vl + rx1
            sy = self.vt + ry1
            _tk_root.after(0, lambda: FloatingSnip(cropped, x=sx, y=sy))

    def _cancel(self):
        self.dragging = False
        if self.anim_id: self.root.after_cancel(self.anim_id)
        self.root.destroy()


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

        self.win.geometry(f"{w}x{h}+{x}+{y}")

        self.tk_img = ImageTk.PhotoImage(image)
        self.cv = tk.Canvas(self.win, width=w, height=h,
                            bd=0, highlightthickness=0, bg=DARK_BG,
                            cursor="fleur")
        self.cv.pack(fill=tk.BOTH, expand=True)
        self.cv.create_image(0,0, anchor=tk.NW, image=self.tk_img)

        self.menu = tk.Menu(self.win, tearoff=0,
                            bg="#2a2a3e", fg="white",
                            activebackground=BLUE, activeforeground="white",
                            font=("Segoe UI",10), relief=tk.FLAT, bd=0)
        self.menu.add_command(label="📋  Copy",       command=self.copy)
        self.menu.add_command(label="💾  Save as...", command=self.save)
        self.menu.add_separator()
        self.menu.add_command(label="✕  Close",       command=self.close)

        self._dx = self._dy = 0
        self._moved = False
        self.cv.bind("<ButtonPress-1>",   self._ds)
        self.cv.bind("<B1-Motion>",       self._dm)
        self.cv.bind("<ButtonRelease-1>", self._click_dismiss)
        self.cv.bind("<ButtonPress-3>",   self._show_menu)

        self.win.lift()
        snip_windows.append(self)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    def _ds(self, e):
        self._dx = e.x_root - self.win.winfo_x()
        self._dy = e.y_root - self.win.winfo_y()
        self._moved = False

    def _dm(self, e):
        self._moved = True
        self.win.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}")

    def _click_dismiss(self, e):
        if not self._moved:
            self.close()
        self._moved = False

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
#  TAKE SNIP  — runs overlay in its own thread with its own Tk instance
# ══════════════════════════════════════════════════════════════════════════
def take_snip():
    if not _overlay_lock.acquire(blocking=False):
        return   # already open
    def _run():
        try:
            SelectionOverlay()   # blocks until closed
        finally:
            _overlay_lock.release()
    threading.Thread(target=_run, daemon=True).start()

def close_all_snips():
    for w in list(snip_windows): w.close()

def _start_hotkey_listener():
    try:
        import keyboard
        keyboard.add_hotkey("ctrl+shift+s", take_snip)
        keyboard.wait()
    except ImportError:
        pass

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
    tray_icon = pystray.Icon("DarcySnipTool", make_tray_image(),
                              "DarcySnipTool — click to snip", menu)
    tray_icon.run()

if __name__ == "__main__":
    make_ico_file()
    threading.Thread(target=_run_tk, daemon=True).start()
    _tk_ready.wait()
    threading.Thread(target=_start_hotkey_listener, daemon=True).start()
    build_tray()
