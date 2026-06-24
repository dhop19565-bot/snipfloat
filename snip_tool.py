"""
SnipFloat - System tray screenshot tool for Windows
Click tray icon → drag to select → floating snip appears on screen
Drag the snip anywhere by clicking on the image itself
"""

import tkinter as tk
from tkinter import filedialog
import threading
import os
import io
from PIL import Image, ImageTk, ImageDraw, ImageGrab
import pystray
from pystray import MenuItem as item


# ── Globals ───────────────────────────────────────────────────────────────
snip_windows = []
tray_icon = None


# ── Tray icon ─────────────────────────────────────────────────────────────
def make_tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 63, 63], radius=12, fill="#1e1e2e")
    d.line([14, 14, 50, 50], fill="white", width=4)
    d.line([14, 50, 50, 14], fill="white", width=4)
    d.ellipse([6, 6, 22, 22], outline="white", width=3)
    d.ellipse([42, 42, 58, 58], outline="white", width=3)
    return img


# ── Selection overlay ─────────────────────────────────────────────────────
CHROMA = "#fe01fe"   # magenta chroma-key — made fully transparent

class SelectionOverlay:
    def __init__(self, on_done):
        self.on_done = on_done
        self.start_x = self.start_y = 0
        self.cur_x = self.cur_y = 0
        self.dragging = False
        self._dash_offset = 0
        self._anim_id = None

        self.root = tk.Toplevel()
        self.root.withdraw()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 1.0)
        # Make the chroma colour fully transparent (Windows colorkey)
        self.root.wm_attributes("-transparentcolor", CHROMA)
        self.root.configure(bg=CHROMA)
        self.root.config(cursor="crosshair")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        self.canvas = tk.Canvas(
            self.root, bg=CHROMA, highlightthickness=0,
            width=sw, height=sh
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Crosshair lines
        self.h_line = self.canvas.create_line(
            0, 0, sw, 0, fill="#7aa2f7", width=1, dash=(4, 4))
        self.v_line = self.canvas.create_line(
            0, 0, 0, sh, fill="#7aa2f7", width=1, dash=(4, 4))

        self.fill_rect  = None
        self.rect_outer = None
        self.rect_inner = None
        self.size_label = None

        self.canvas.bind("<ButtonPress-1>",   self.on_press)
        self.canvas.bind("<B1-Motion>",       self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>",          self.on_mouse_move)
        self.root.bind("<Escape>", lambda e: self.cancel())

        self.root.deiconify()
        self.root.focus_force()

    def on_mouse_move(self, e):
        if not self.dragging:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.canvas.coords(self.h_line, 0, e.y, sw, e.y)
            self.canvas.coords(self.v_line, e.x, 0, e.x, sh)

    def on_press(self, e):
        self.start_x, self.start_y = e.x, e.y
        self.cur_x, self.cur_y = e.x, e.y
        self.dragging = True

        # Hide crosshair while selecting
        self.canvas.itemconfigure(self.h_line, state="hidden")
        self.canvas.itemconfigure(self.v_line, state="hidden")

        # Selection rectangle — fill uses stipple so chroma shows through
        self.fill_rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y,
            fill="#7aa2f7", stipple="gray25", outline="")
        self.rect_outer = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="white", width=1)
        self.rect_inner = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="#7aa2f7", width=2,
            dash=(6, 4), dashoffset=0)
        self.size_label = self.canvas.create_text(
            e.x + 4, e.y + 4, text="", fill="white",
            font=("Segoe UI", 9, "bold"), anchor="nw")

        self._animate_ants()

    def on_drag(self, e):
        self.cur_x, self.cur_y = e.x, e.y
        self._update_rect()

    def _update_rect(self):
        x1, y1 = self.start_x, self.start_y
        x2, y2 = self.cur_x, self.cur_y
        self.canvas.coords(self.fill_rect,  x1, y1, x2, y2)
        self.canvas.coords(self.rect_outer, x1, y1, x2, y2)
        self.canvas.coords(self.rect_inner, x1, y1, x2, y2)

        w = abs(x2 - x1)
        h = abs(y2 - y1)
        label = f" {w} × {h} "

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        lx = max(x1, x2) + 4
        ly = max(y1, y2) + 4
        anchor = "nw"
        if lx + 90 > sw:
            lx = min(x1, x2) - 4
            anchor = "ne"
        if ly + 22 > sh:
            ly = min(y1, y2) - 4
        self.canvas.coords(self.size_label, lx, ly)
        self.canvas.itemconfigure(self.size_label, text=label, anchor=anchor)

    def _animate_ants(self):
        if not self.dragging:
            return
        self._dash_offset = (self._dash_offset + 1) % 10
        self.canvas.itemconfigure(self.rect_inner, dashoffset=self._dash_offset)
        self._anim_id = self.root.after(60, self._animate_ants)

    def on_release(self, e):
        self.dragging = False
        if self._anim_id:
            self.root.after_cancel(self._anim_id)
        x1 = min(self.start_x, e.x)
        y1 = min(self.start_y, e.y)
        x2 = max(self.start_x, e.x)
        y2 = max(self.start_y, e.y)
        self.root.destroy()
        if (x2 - x1) > 5 and (y2 - y1) > 5:
            self.on_done(x1, y1, x2, y2)

    def cancel(self):
        self.dragging = False
        if self._anim_id:
            self.root.after_cancel(self._anim_id)
        self.root.destroy()


# ── Floating snip window ──────────────────────────────────────────────────
class FloatingSnip:
    def __init__(self, image: Image.Image, x, y):
        self.image = image
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 1.0)
        self.win.configure(bg="#1e1e2e")

        w, h = image.size
        max_w, max_h = 900, 700
        if w > max_w or h > max_h:
            image.thumbnail((max_w, max_h), Image.LANCZOS)
            w, h = image.size

        self.win.geometry(f"{w}x{h+28}+{x}+{y}")

        # ── Title bar ────────────────────────────────────────────────────
        bar = tk.Frame(self.win, bg="#1e1e2e", height=28)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.pack_propagate(False)

        tk.Label(bar, text="  ✂  snip", bg="#1e1e2e", fg="#a9b1d6",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)

        tk.Button(bar, text="✕", bg="#1e1e2e", fg="#f7768e",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=6,
                  activebackground="#f7768e", activeforeground="white",
                  command=self.close).pack(side=tk.RIGHT)

        tk.Button(bar, text="💾", bg="#1e1e2e", fg="#9ece6a",
                  font=("Segoe UI", 9), bd=0, padx=6,
                  activebackground="#9ece6a", activeforeground="white",
                  command=self.save).pack(side=tk.RIGHT)

        tk.Button(bar, text="📋", bg="#1e1e2e", fg="#7aa2f7",
                  font=("Segoe UI", 9), bd=0, padx=6,
                  activebackground="#7aa2f7", activeforeground="white",
                  command=self.copy).pack(side=tk.RIGHT)

        # ── Image canvas ─────────────────────────────────────────────────
        self.tk_img = ImageTk.PhotoImage(image)
        self.canvas = tk.Canvas(self.win, width=w, height=h,
                                bd=0, highlightthickness=0, bg="#1e1e2e",
                                cursor="fleur")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)

        # ── Drag from anywhere ────────────────────────────────────────────
        self._drag_x = self._drag_y = 0
        for widget in (bar, self.canvas):
            widget.bind("<ButtonPress-1>",  self._drag_start)
            widget.bind("<B1-Motion>",      self._drag_move)
        for child in bar.winfo_children():
            child.bind("<ButtonPress-1>",  self._drag_start)
            child.bind("<B1-Motion>",       self._drag_move)

        snip_windows.append(self)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    def _drag_start(self, e):
        self._drag_x = e.x_root - self.win.winfo_x()
        self._drag_y = e.y_root - self.win.winfo_y()

    def _drag_move(self, e):
        x = e.x_root - self._drag_x
        y = e.y_root - self._drag_y
        self.win.geometry(f"+{x}+{y}")

    def copy(self):
        try:
            import win32clipboard
            output = io.BytesIO()
            self.image.convert("RGB").save(output, "BMP")
            data = output.getvalue()[14:]
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            win32clipboard.CloseClipboard()
        except ImportError:
            pass

    def save(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("All", "*.*")],
            title="Save snip"
        )
        if path:
            self.image.save(path)

    def close(self):
        if self in snip_windows:
            snip_windows.remove(self)
        self.win.destroy()


# ── Capture flow ──────────────────────────────────────────────────────────
def take_snip():
    _get_root().after(0, _open_overlay)

def _open_overlay():
    SelectionOverlay(on_done=_capture)

def _capture(x1, y1, x2, y2):
    # Longer delay so the overlay is fully gone before grabbing
    _get_root().after(200, lambda: _do_grab(x1, y1, x2, y2))

def _do_grab(x1, y1, x2, y2):
    screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
    FloatingSnip(screenshot, x=max(0, x1), y=max(0, y1 - 28))

def close_all_snips():
    for w in list(snip_windows):
        w.close()


# ── Shared Tk root ────────────────────────────────────────────────────────
_tk_root = None

def _get_root():
    return _tk_root

def _run_tk():
    global _tk_root
    _tk_root = tk.Tk()
    _tk_root.withdraw()
    _tk_root.mainloop()


# ── Tray ──────────────────────────────────────────────────────────────────
def quit_app(icon, _=None):
    close_all_snips()
    icon.stop()
    os._exit(0)

def build_tray():
    global tray_icon
    image = make_tray_image()
    menu = pystray.Menu(
        item("✂  Take Snip", lambda icon, i: take_snip(), default=True),
        item("🗑  Close All Snips", lambda icon, i: _get_root().after(0, close_all_snips)),
        pystray.Menu.SEPARATOR,
        item("✕  Quit", quit_app),
    )
    tray_icon = pystray.Icon("SnipFloat", image, "SnipFloat — click to snip", menu)
    tray_icon.run()


# ── Entry ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import time
    threading.Thread(target=_run_tk, daemon=True).start()
    time.sleep(0.3)
    build_tray()
