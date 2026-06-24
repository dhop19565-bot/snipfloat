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
_tk_root = None


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
# Run the overlay in its own private Tk instance so -transparentcolor
# never leaks to the snip Toplevel windows on the main root.

CHROMA = "#010101"   # near-black used as the transparent key colour

def _run_overlay_thread(on_done_event, result_box):
    """Runs a fresh Tk root just for the selection overlay, then exits."""
    root = tk.Tk()
    root.withdraw()

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()

    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 1.0)
    root.wm_attributes("-transparentcolor", CHROMA)
    root.configure(bg=CHROMA)
    root.config(cursor="crosshair")

    canvas = tk.Canvas(root, bg=CHROMA, highlightthickness=0,
                       width=sw, height=sh)
    canvas.pack(fill=tk.BOTH, expand=True)

    state = {
        "dragging": False,
        "start_x": 0, "start_y": 0,
        "cur_x": 0,   "cur_y": 0,
        "dash_off": 0, "anim_id": None,
        "fill": None, "outer": None, "inner": None, "label": None,
        "h_line": None, "v_line": None,
    }

    # Crosshair
    state["h_line"] = canvas.create_line(0, 0, sw, 0,
                                          fill="#7aa2f7", width=1, dash=(4,4))
    state["v_line"] = canvas.create_line(0, 0, 0, sh,
                                          fill="#7aa2f7", width=1, dash=(4,4))

    def move(e):
        if not state["dragging"]:
            canvas.coords(state["h_line"], 0, e.y, sw, e.y)
            canvas.coords(state["v_line"], e.x, 0, e.x, sh)

    def press(e):
        state["dragging"] = True
        state["start_x"] = state["cur_x"] = e.x
        state["start_y"] = state["cur_y"] = e.y
        canvas.itemconfigure(state["h_line"], state="hidden")
        canvas.itemconfigure(state["v_line"], state="hidden")
        state["fill"]  = canvas.create_rectangle(e.x, e.y, e.x, e.y,
                             fill="#7aa2f7", stipple="gray25", outline="")
        state["outer"] = canvas.create_rectangle(e.x, e.y, e.x, e.y,
                             outline="white", width=1)
        state["inner"] = canvas.create_rectangle(e.x, e.y, e.x, e.y,
                             outline="#7aa2f7", width=2, dash=(6,4), dashoffset=0)
        state["label"] = canvas.create_text(e.x+4, e.y+4, text="",
                             fill="white", font=("Segoe UI", 9, "bold"), anchor="nw")
        animate()

    def drag(e):
        state["cur_x"], state["cur_y"] = e.x, e.y
        update_rect()

    def update_rect():
        x1, y1 = state["start_x"], state["start_y"]
        x2, y2 = state["cur_x"],   state["cur_y"]
        canvas.coords(state["fill"],  x1, y1, x2, y2)
        canvas.coords(state["outer"], x1, y1, x2, y2)
        canvas.coords(state["inner"], x1, y1, x2, y2)
        w, h = abs(x2-x1), abs(y2-y1)
        lx = max(x1,x2)+4;  ly = max(y1,y2)+4
        anchor = "nw"
        if lx + 90 > sw: lx = min(x1,x2)-4; anchor = "ne"
        if ly + 22 > sh: ly = min(y1,y2)-4
        canvas.coords(state["label"], lx, ly)
        canvas.itemconfigure(state["label"], text=f" {w} × {h} ", anchor=anchor)

    def animate():
        if not state["dragging"]: return
        state["dash_off"] = (state["dash_off"] + 1) % 10
        canvas.itemconfigure(state["inner"], dashoffset=state["dash_off"])
        state["anim_id"] = root.after(60, animate)

    def release(e):
        state["dragging"] = False
        if state["anim_id"]:
            root.after_cancel(state["anim_id"])
        x1 = min(state["start_x"], e.x)
        y1 = min(state["start_y"], e.y)
        x2 = max(state["start_x"], e.x)
        y2 = max(state["start_y"], e.y)
        root.destroy()
        if (x2-x1) > 5 and (y2-y1) > 5:
            result_box.append((x1, y1, x2, y2))
        on_done_event.set()

    def cancel(e=None):
        state["dragging"] = False
        if state["anim_id"]:
            root.after_cancel(state["anim_id"])
        root.destroy()
        on_done_event.set()

    canvas.bind("<Motion>",          move)
    canvas.bind("<ButtonPress-1>",   press)
    canvas.bind("<B1-Motion>",       drag)
    canvas.bind("<ButtonRelease-1>", release)
    root.bind("<Escape>", cancel)

    root.deiconify()
    root.focus_force()
    root.mainloop()


# ── Floating snip window ──────────────────────────────────────────────────
class FloatingSnip:
    def __init__(self, image: Image.Image, x, y):
        self.image = image
        self.win = tk.Toplevel(_tk_root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
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
    _tk_root.after(0, _open_overlay)

def _open_overlay():
    done_event = threading.Event()
    result_box = []
    t = threading.Thread(target=_run_overlay_thread,
                         args=(done_event, result_box), daemon=True)
    t.start()

    def wait_for_result():
        if done_event.is_set():
            if result_box:
                x1, y1, x2, y2 = result_box[0]
                # Give OS time to fully remove overlay before grabbing
                _tk_root.after(250, lambda: _do_grab(x1, y1, x2, y2))
        else:
            _tk_root.after(50, wait_for_result)

    _tk_root.after(50, wait_for_result)

def _do_grab(x1, y1, x2, y2):
    screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
    FloatingSnip(screenshot, x=max(0, x1), y=max(0, y1 - 28))

def close_all_snips():
    for w in list(snip_windows):
        w.close()


# ── Shared Tk root ────────────────────────────────────────────────────────
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
        item("🗑  Close All Snips", lambda icon, i: _tk_root.after(0, close_all_snips)),
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
