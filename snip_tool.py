"""
SnipFloat - System tray screenshot tool for Windows
Click tray icon → drag to select → floating snip appears on screen
"""

import tkinter as tk
from tkinter import filedialog
import threading
import sys
import os
import io
from PIL import Image, ImageTk, ImageDraw, ImageGrab
import pystray
from pystray import MenuItem as item


# ── Globals ──────────────────────────────────────────────────────────────────
snip_windows = []   # track open floating snips
tray_icon = None


# ── Tray icon image (scissors emoji style, drawn with Pillow) ─────────────
def make_tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Dark rounded square background
    d.rounded_rectangle([0, 0, 63, 63], radius=12, fill="#1e1e2e")
    # Simple scissors / snip icon
    d.line([14, 14, 50, 50], fill="white", width=4)
    d.line([14, 50, 50, 14], fill="white", width=4)
    d.ellipse([6, 6, 22, 22], outline="white", width=3)
    d.ellipse([42, 42, 58, 58], outline="white", width=3)
    return img


# ── Selection overlay ─────────────────────────────────────────────────────
class SelectionOverlay:
    def __init__(self, on_done):
        self.on_done = on_done
        self.root = tk.Toplevel()
        self.root.withdraw()

        # Fullscreen transparent overlay
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-alpha", 0.25)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.config(cursor="crosshair")

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.start_x = self.start_y = 0
        self.rect = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Escape>", lambda e: self.cancel())

        self.root.deiconify()
        self.root.focus_force()

    def on_press(self, e):
        self.start_x, self.start_y = e.x, e.y
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y,
            outline="#7aa2f7", width=2, fill="#7aa2f720"
        )

    def on_drag(self, e):
        self.canvas.coords(self.rect, self.start_x, self.start_y, e.x, e.y)

    def on_release(self, e):
        x1 = min(self.start_x, e.x)
        y1 = min(self.start_y, e.y)
        x2 = max(self.start_x, e.x)
        y2 = max(self.start_y, e.y)
        self.root.destroy()
        if (x2 - x1) > 5 and (y2 - y1) > 5:
            self.on_done(x1, y1, x2, y2)

    def cancel(self):
        self.root.destroy()


# ── Floating snip window ──────────────────────────────────────────────────
class FloatingSnip:
    def __init__(self, image: Image.Image, x, y):
        self.image = image
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)          # borderless
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 1.0)

        w, h = image.size
        # Clamp size so huge snips stay usable
        max_w, max_h = 900, 700
        if w > max_w or h > max_h:
            image.thumbnail((max_w, max_h), Image.LANCZOS)
            w, h = image.size

        self.win.geometry(f"{w}x{h+28}+{x}+{y}")
        self.win.configure(bg="#1e1e2e")

        # ── Title bar ───────────────────────────────────────────────────
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

        # ── Image canvas ────────────────────────────────────────────────
        self.tk_img = ImageTk.PhotoImage(image)
        canvas = tk.Canvas(self.win, width=w, height=h,
                           bd=0, highlightthickness=0, bg="#1e1e2e")
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)

        # ── Drag to move ────────────────────────────────────────────────
        self._drag_x = self._drag_y = 0
        bar.bind("<ButtonPress-1>",   self._drag_start)
        bar.bind("<B1-Motion>",       self._drag_move)
        for child in bar.winfo_children():
            child.bind("<ButtonPress-1>",   self._drag_start)
            child.bind("<B1-Motion>",        self._drag_move)

        # Track this window
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
            # Fallback: xclip / no-op on non-Windows
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


# ── Take a snip ──────────────────────────────────────────────────────────
def take_snip():
    """Called from tray — opens the selection overlay in the Tk thread."""
    root = _get_root()
    root.after(0, _open_overlay)


def _open_overlay():
    SelectionOverlay(on_done=_capture)


def _capture(x1, y1, x2, y2):
    # Small delay so the overlay is fully gone before grabbing
    _get_root().after(120, lambda: _do_grab(x1, y1, x2, y2))


def _do_grab(x1, y1, x2, y2):
    screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
    # Place floating snip near the selection
    FloatingSnip(screenshot, x=max(0, x1), y=max(0, y1 - 28))


def close_all_snips():
    for w in list(snip_windows):
        w.close()


# ── Shared hidden Tk root ─────────────────────────────────────────────────
_tk_root = None

def _get_root():
    global _tk_root
    return _tk_root


def _run_tk():
    global _tk_root
    _tk_root = tk.Tk()
    _tk_root.withdraw()           # hidden master window
    _tk_root.mainloop()


# ── Tray setup ────────────────────────────────────────────────────────────
def quit_app(icon, item=None):
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


# ── Entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Tk must run on the main thread on Windows
    tk_thread = threading.Thread(target=_run_tk, daemon=True)
    tk_thread.start()

    import time
    time.sleep(0.3)   # let Tk initialise

    build_tray()      # blocks until quit
