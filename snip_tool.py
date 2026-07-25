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
import ctypes.wintypes
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
snip_windows  = []
tray_icon     = None
_tk_root      = None
_tk_ready     = threading.Event()
_snip_active  = False

ORANGE    = "#FF8C00"
ORANGE_DK = "#CC6600"
GOLD      = "#FFD700"
DARK_BG   = "#1e1e2e"
BLUE      = "#7aa2f7"
GREEN     = "#9ece6a"
AMBER     = "#e0af68"


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
#  VIRTUAL DESKTOP
# ══════════════════════════════════════════════════════════════════════════
def get_virtual_desktop():
    u = ctypes.windll.user32
    l = u.GetSystemMetrics(76)
    t = u.GetSystemMetrics(77)
    w = u.GetSystemMetrics(78)
    h = u.GetSystemMetrics(79)
    return l, t, w, h


# ══════════════════════════════════════════════════════════════════════════
#  SELECTION OVERLAY  — runs on the MAIN Tk thread via _tk_root.after()
# ══════════════════════════════════════════════════════════════════════════
def _open_overlay(sum_mode=False):
    global _snip_active
    if _snip_active:
        return
    _snip_active = True

    vl, vt, vw, vh = get_virtual_desktop()

    state = {
        "dragging": False,
        "start_x": 0, "start_y": 0,
        "cur_x": 0,   "cur_y": 0,
        "dash_off": 0,
        "anim_id": None,
        "hline": None, "vline": None,
        "sel_outer": None, "sel_inner": None, "sel_label": None,
    }

    win = tk.Toplevel(_tk_root)
    win.withdraw()
    win.overrideredirect(True)
    win.geometry(f"{vw}x{vh}+{vl}+{vt}")
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.30)   # light enough to see through, solid enough for clicks
    win.configure(bg="#202020")
    win.config(cursor="crosshair")

    cv = tk.Canvas(win, width=vw, height=vh,
                   bd=0, highlightthickness=0, bg="#202020")
    cv.pack(fill=tk.BOTH, expand=True)

    # Crosshair
    state["hline"] = cv.create_line(0,0,vw,0, fill=BLUE, width=1, dash=(6,4))
    state["vline"] = cv.create_line(0,0,0,vh, fill=BLUE, width=1, dash=(6,4))

    def on_move(e):
        if not state["dragging"]:
            cv.coords(state["hline"], 0, e.y, vw, e.y)
            cv.coords(state["vline"], e.x, 0, e.x, vh)

    def on_press(e):
        state["dragging"]  = True
        state["start_x"]   = state["cur_x"] = e.x
        state["start_y"]   = state["cur_y"] = e.y
        cv.itemconfigure(state["hline"], state="hidden")
        cv.itemconfigure(state["vline"], state="hidden")
        state["sel_outer"] = cv.create_rectangle(e.x,e.y,e.x,e.y,
                                 outline="white", width=1)
        state["sel_inner"] = cv.create_rectangle(e.x,e.y,e.x,e.y,
                                 outline=BLUE, width=2, dash=(6,4))
        state["sel_label"] = cv.create_text(e.x+4,e.y+4, text="",
                                 fill="white", font=("Segoe UI",9,"bold"),
                                 anchor="nw")
        animate()

    def on_drag(e):
        state["cur_x"], state["cur_y"] = e.x, e.y
        x1,y1 = state["start_x"], state["start_y"]
        x2,y2 = e.x, e.y
        rx1,ry1 = min(x1,x2), min(y1,y2)
        rx2,ry2 = max(x1,x2), max(y1,y2)
        cv.coords(state["sel_outer"], rx1,ry1,rx2,ry2)
        cv.coords(state["sel_inner"], rx1,ry1,rx2,ry2)
        w,h = rx2-rx1, ry2-ry1
        lx,ly = rx2+6, ry2+6
        anch = "nw"
        if lx+110 > vw: lx=rx1-6; anch="ne"
        if ly+22  > vh: ly=ry1-6
        cv.coords(state["sel_label"], lx, ly)
        cv.itemconfigure(state["sel_label"],
                         text=f" {w} × {h} px ", anchor=anch)

    def animate():
        if not state["dragging"]: return
        state["dash_off"] = (state["dash_off"]+1) % 10
        cv.itemconfigure(state["sel_inner"], dashoffset=state["dash_off"])
        state["anim_id"] = win.after(60, animate)

    def on_release(e):
        state["dragging"] = False
        if state["anim_id"]: win.after_cancel(state["anim_id"])

        # Query the TRUE cursor position from Windows at release time.
        # On fast drags, the release event's e.x/e.y can lag behind the
        # actual cursor, so we use the OS cursor position as source of truth.
        try:
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            # Convert screen coords -> canvas coords
            end_x = pt.x - vl
            end_y = pt.y - vt
        except Exception:
            end_x, end_y = e.x, e.y

        # Clamp to canvas bounds
        ex = max(0, min(end_x, vw))
        ey = max(0, min(end_y, vh))
        sx = max(0, min(state["start_x"], vw))
        sy = max(0, min(state["start_y"], vh))
        rx1 = min(sx, ex);  ry1 = min(sy, ey)
        rx2 = max(sx, ex);  ry2 = max(sy, ey)
        _finish(rx1, ry1, rx2, ry2)

    def on_escape(e):
        if state["anim_id"]: win.after_cancel(state["anim_id"])
        _finish(0, 0, 0, 0)

    def _finish(rx1, ry1, rx2, ry2):
        global _snip_active
        valid = (rx2-rx1) > 5 and (ry2-ry1) > 5
        # Real screen coords of the selection
        real_x1 = vl + rx1
        real_y1 = vt + ry1
        real_x2 = vl + rx2
        real_y2 = vt + ry2
        # Hide overlay instantly so it's not in the grab
        win.destroy()
        _snip_active = False
        if valid:
            # Grab ONLY the selected region — fast even on multi-monitor
            def grab_and_show():
                try:
                    img = ImageGrab.grab(
                        bbox=(real_x1, real_y1, real_x2, real_y2),
                        all_screens=True)
                    if sum_mode:
                        # Snip-and-sum: skip the floating window, OCR directly
                        _sum_image_directly(img)
                    else:
                        FloatingSnip(img, x=real_x1, y=real_y1)
                except Exception:
                    pass
            # Tiny delay lets the overlay fully clear from screen first
            _tk_root.after(30, grab_and_show)

    cv.bind("<Motion>",          on_move)
    cv.bind("<ButtonPress-1>",   on_press)
    cv.bind("<B1-Motion>",       on_drag)
    cv.bind("<ButtonRelease-1>", on_release)
    win.bind("<Escape>",         on_escape)

    win.deiconify()
    win.lift()
    win.focus_force()


# ══════════════════════════════════════════════════════════════════════════
#  OCR HELPERS  (used by "Sum numbers")
# ══════════════════════════════════════════════════════════════════════════
import re

# ── Windows native OCR (Windows.Media.Ocr) ─────────────────────────────────
_win_ocr_engine = None
_win_ocr_checked = False

def _get_windows_ocr():
    """Return a Windows OCR engine if available, else None (cached)."""
    global _win_ocr_engine, _win_ocr_checked
    if _win_ocr_checked:
        return _win_ocr_engine
    _win_ocr_checked = True
    try:
        # winsdk is the maintained WinRT projection; winrt is the older name
        try:
            from winsdk.windows.media.ocr import OcrEngine
        except ImportError:
            from winrt.windows.media.ocr import OcrEngine
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            eng = OcrEngine.try_create_from_language(None)
        _win_ocr_engine = eng
    except Exception:
        _win_ocr_engine = None
    return _win_ocr_engine


def _windows_ocr_text(pil_image):
    """Run Windows native OCR on a PIL image. Returns text or None on failure."""
    try:
        try:
            from winsdk.windows.graphics.imaging import (
                SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode)
            from winsdk.windows.security.cryptography import CryptographicBuffer
        except ImportError:
            from winrt.windows.graphics.imaging import (
                SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode)
            from winrt.windows.security.cryptography import CryptographicBuffer

        engine = _get_windows_ocr()
        if engine is None:
            return None

        img = pil_image.convert("RGBA")
        w, h = img.size
        # BGRA byte order for Windows
        data = img.tobytes("raw", "BGRA")
        buf = CryptographicBuffer.create_from_byte_array(list(data))
        bmp = SoftwareBitmap.create_copy_from_buffer(
            buf, BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED)

        import asyncio
        async def _run():
            result = await engine.recognize_async(bmp)
            return result

        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_run())
            loop.close()
        except Exception:
            return None

        if result is None:
            return None

        # Preserve line structure
        lines = []
        for line in result.lines:
            lines.append(line.text)
        return "\n".join(lines)
    except Exception:
        return None


_tesseract_configured = False

def _configure_tesseract():
    """Point pytesseract at a bundled tesseract.exe if one is present."""
    global _tesseract_configured
    if _tesseract_configured:
        return
    try:
        import pytesseract
        import sys
        base = getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(base, "tesseract", "tesseract.exe"),
            os.path.join(base, "tesseract.exe"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "tesseract", "tesseract.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                pytesseract.pytesseract.tesseract_cmd = c
                tessdata = os.path.join(os.path.dirname(c), "tessdata")
                if os.path.isdir(tessdata):
                    os.environ["TESSDATA_PREFIX"] = tessdata
                break
    except Exception:
        pass
    _tesseract_configured = True


def _extract_and_sum(text):
    """
    Pull numbers out of OCR text and sum them.
    Handles: thousands separators (1,234.56), European (1.234,56),
    currency symbols (£$€), percentages, leading/trailing minus, and
    parentheses-as-negative accounting style. Newline-safe so a minus at
    the start of the next line is never stolen by the number above it.
    Returns (list_of_numbers, total).
    """
    numbers = []
    # [ \t]* (not \s*) keeps tokens from spanning newlines.
    token_re = re.compile(r'\(?[-+]?[£$€]?[ \t]*\d[\d.,]*-?%?\)?')

    for raw in token_re.findall(text):
        token = raw.strip()
        if not any(ch.isdigit() for ch in token):
            continue

        negative = False
        if "(" in token and ")" in token:
            negative = True

        first_digit = next(i for i, ch in enumerate(token) if ch.isdigit())
        last_digit  = (len(token) - 1 -
                       next(i for i, ch in enumerate(reversed(token))
                            if ch.isdigit()))
        before = token[:first_digit]
        after  = token[last_digit+1:]
        if "-" in before:          # leading minus
            negative = True
        if "-" in after:           # trailing minus (finance exports)
            negative = True

        cleaned = re.sub(r'[£$€%()\s+\-]', "", token)

        # Resolve decimal vs thousands separators
        if "." in cleaned and "," in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")  # EU
            else:
                cleaned = cleaned.replace(",", "")                    # UK/US
        elif "," in cleaned:
            parts = cleaned.split(",")
            if len(parts) == 2 and len(parts[1]) in (1, 2):
                cleaned = cleaned.replace(",", ".")   # decimal comma 12,5
            else:
                cleaned = cleaned.replace(",", "")    # thousands

        try:
            val = float(cleaned)
        except ValueError:
            continue
        if negative:
            val = -abs(val)
        numbers.append(val)

    total = sum(numbers)
    return numbers, total


def _detect_currency(text):
    """Return the first currency symbol found in the OCR text, or ''."""
    for sym in ("£", "$", "€"):
        if sym in text:
            return sym
    return ""


def _fmt_num(n, currency=""):
    """Format a number cleanly: drop trailing .0, add thousands separators."""
    if abs(n - round(n)) < 1e-9:
        body = f"{int(round(n)):,}"
    else:
        body = f"{n:,.2f}"
    return f"{currency}{body}" if currency else body


# ── Shared OCR core: image -> (numbers, total, min_conf, currency) ─────────
def _ocr_image(pil_image):
    """Run OCR (Windows native first, Tesseract fallback) and extract numbers."""
    from PIL import ImageOps, ImageFilter, ImageEnhance
    text = None
    min_conf = None

    # 1. Windows native OCR — best on clean on-screen text
    try:
        wimg = pil_image.convert("L")
        w, h = wimg.size
        if max(w, h) < 1400:
            factor = max(2, 1400 // max(w, h))
            wimg = wimg.resize((w*factor, h*factor), Image.LANCZOS)
        wimg = wimg.filter(ImageFilter.SHARPEN)
        hist = wimg.histogram()
        if sum(hist[:128]) > sum(hist[128:]):      # dark mode
            wimg = ImageOps.invert(wimg)
        wtext = _windows_ocr_text(wimg.convert("RGB"))
        if wtext and any(ch.isdigit() for ch in wtext):
            text = wtext
    except Exception:
        text = None

    # 2. Tesseract fallback
    if text is None:
        try:
            import pytesseract
            _configure_tesseract()
            img = pil_image.convert("L")
            w, h = img.size
            if max(w, h) < 1400:
                factor = max(3, 1400 // max(w, h))
                img = img.resize((w*factor, h*factor), Image.LANCZOS)
            img = img.filter(ImageFilter.SHARPEN)
            img = ImageOps.autocontrast(img, cutoff=2)
            img = ImageEnhance.Contrast(img).enhance(1.5)
            hist_mean = int(sum(i*c for i, c in enumerate(img.histogram()))
                            / max(1, sum(img.histogram())))
            threshold = max(120, min(200, hist_mean))
            bw = img.point(lambda p: 255 if p > threshold else 0)
            if bw.histogram()[0] > bw.histogram()[255]:
                bw = ImageOps.invert(bw)
            bw = ImageOps.expand(bw, border=40, fill=255)
            whitelist = "0123456789.,-()£$€%"
            config = f"--psm 6 -c tessedit_char_whitelist={whitelist}"
            data = pytesseract.image_to_data(
                bw, config=config, output_type=pytesseract.Output.DICT)
            words, confs = [], []
            for i, word in enumerate(data.get("text", [])):
                word = word.strip()
                if not word:
                    continue
                try:
                    conf = float(data["conf"][i])
                except (ValueError, KeyError, IndexError):
                    conf = -1
                if any(ch.isdigit() for ch in word):
                    words.append(word)
                    if conf >= 0:
                        confs.append(conf)
            text = "\n".join(words)
            min_conf = min(confs) if confs else None
        except Exception:
            return None   # signals "no OCR engine / error"

    currency = _detect_currency(text or "")
    numbers, total = _extract_and_sum(text or "")
    return numbers, total, min_conf, currency


# ── Running tally across snips ─────────────────────────────────────────────
_running_tally = []          # list of individual numbers accumulated
_tally_enabled = False       # toggle from tray


def _sum_image_directly(pil_image):
    """Snip-and-sum in one action: OCR then show a toast, no floating window."""
    def worker():
        result = _ocr_image(pil_image)
        if result is None:
            _tk_root.after(0, lambda: show_toast(
                "OCR unavailable", subtitle="No OCR engine found", accent=AMBER))
            return
        numbers, total, min_conf, currency = result
        _tk_root.after(0, lambda: _present_sum(
            numbers, total, min_conf, currency))
    threading.Thread(target=worker, daemon=True).start()


def _present_sum(numbers, total, min_conf, currency):
    """Show a sum result toast; feed the running tally if enabled."""
    global _running_tally
    if not numbers:
        show_toast("No numbers found", subtitle="Try a tighter snip",
                   accent=AMBER)
        return

    # Running tally mode: accumulate and show the grand total
    if _tally_enabled:
        _running_tally.extend(numbers)
        grand = sum(_running_tally)
        try:
            _tk_root.clipboard_clear()
            _tk_root.clipboard_append(_fmt_num(grand).replace(",", ""))
        except Exception:
            pass
        show_toast(
            f"Running total:  {_fmt_num(grand, currency)}",
            subtitle=(f"+{_fmt_num(total, currency)} this snip  ·  "
                      f"{len(_running_tally)} values total"),
            accent=BLUE, numbers=numbers, currency=currency)
        return

    # Normal one-shot sum
    total_str = _fmt_num(total, currency)
    try:
        _tk_root.clipboard_clear()
        _tk_root.clipboard_append(_fmt_num(total).replace(",", ""))
    except Exception:
        pass
    count = len(numbers)
    avg   = total / count if count else 0
    low   = (min_conf is not None and min_conf < 75)
    stats = (f"{count} value{'s' if count != 1 else ''}  ·  "
             f"avg {_fmt_num(avg, currency)}")
    if low:
        stats += "  ·  ⚠ check figures"
    show_toast(f"Total:  {total_str}", subtitle=stats,
               accent=(AMBER if low else GREEN),
               numbers=numbers, currency=currency)


# ══════════════════════════════════════════════════════════════════════════
#  TOAST NOTIFICATION  (lightweight, bottom-right, auto-dismiss)
# ══════════════════════════════════════════════════════════════════════════
_active_toasts = []

def show_toast(title, subtitle="", accent=BLUE, numbers=None, duration=4200, currency=""):
    """Show a small notification in the bottom-right corner that fades away."""
    try:
        toast = tk.Toplevel(_tk_root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.attributes("-alpha", 0.0)
        toast.configure(bg=DARK_BG)

        pad = 14
        frame = tk.Frame(toast, bg=DARK_BG)
        frame.pack(fill=tk.BOTH, expand=True)

        # Accent stripe down the left
        stripe = tk.Frame(frame, bg=accent, width=4)
        stripe.pack(side=tk.LEFT, fill=tk.Y)

        body = tk.Frame(frame, bg=DARK_BG)
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, pad), pady=pad)

        tk.Label(body, text=title, bg=DARK_BG, fg="white",
                 font=("Segoe UI", 14, "bold"), anchor="w",
                 justify="left").pack(anchor="w")

        if subtitle:
            tk.Label(body, text=subtitle, bg=DARK_BG, fg="#a9b1d6",
                     font=("Segoe UI", 9), anchor="w",
                     justify="left").pack(anchor="w", pady=(2, 0))

        # Optional: small breakdown of the numbers found
        if numbers:
            preview = "  ".join(_fmt_num(n, currency) for n in numbers)
            if len(preview) > 60:
                preview = preview[:57] + "…"
            tk.Label(body, text=preview, bg=DARK_BG, fg="#6b7089",
                     font=("Consolas", 8), anchor="w",
                     justify="left").pack(anchor="w", pady=(4, 0))

        # Size & position: bottom-right, stacked above existing toasts
        toast.update_idletasks()
        tw = toast.winfo_width()
        th = toast.winfo_height()
        sw = toast.winfo_screenwidth()
        sh = toast.winfo_screenheight()
        margin = 18
        taskbar = 48
        offset  = sum(t.winfo_height() + 10 for t in _active_toasts
                      if t.winfo_exists())
        x = sw - tw - margin
        y = sh - th - taskbar - margin - offset
        toast.geometry(f"+{x}+{y}")

        _active_toasts.append(toast)

        # Click to dismiss immediately
        def dismiss(_=None):
            _fade_out(toast)
        for wdg in [frame, body, toast] + list(body.winfo_children()):
            wdg.bind("<Button-1>", dismiss)

        _fade_in(toast)
        toast.after(duration, lambda: _fade_out(toast))
    except Exception:
        pass


def _fade_in(win, alpha=0.0):
    if not win.winfo_exists():
        return
    alpha = min(0.96, alpha + 0.12)
    try:
        win.attributes("-alpha", alpha)
    except Exception:
        return
    if alpha < 0.96:
        win.after(16, lambda: _fade_in(win, alpha))


def _fade_out(win, alpha=None):
    if not win.winfo_exists():
        return
    if alpha is None:
        try:
            alpha = float(win.attributes("-alpha"))
        except Exception:
            alpha = 0.96
    alpha -= 0.10
    if alpha <= 0:
        if win in _active_toasts:
            _active_toasts.remove(win)
        try:
            win.destroy()
        except Exception:
            pass
        return
    try:
        win.attributes("-alpha", alpha)
    except Exception:
        return
    win.after(16, lambda: _fade_out(win, alpha))


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
        self.menu.add_command(label="🔢  Sum numbers (OCR)", command=self.sum_numbers)
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

    def sum_numbers(self):
        def worker():
            result = _ocr_image(self.image)
            if result is None:
                _tk_root.after(0, lambda: show_toast(
                    "OCR unavailable", subtitle="No OCR engine found",
                    accent=AMBER))
                return
            numbers, total, min_conf, currency = result
            _tk_root.after(0, lambda: _present_sum(
                numbers, total, min_conf, currency))
        threading.Thread(target=worker, daemon=True).start()

    def close(self):
        if self in snip_windows: snip_windows.remove(self)
        self.win.destroy()


# ══════════════════════════════════════════════════════════════════════════
#  TAKE SNIP  — schedules onto main Tk thread
# ══════════════════════════════════════════════════════════════════════════
def take_snip():
    _tk_root.after(0, lambda: _open_overlay(sum_mode=False))

def sum_snip():
    """Snip a region and immediately sum the numbers in it (one action)."""
    _tk_root.after(0, lambda: _open_overlay(sum_mode=True))

def toggle_tally(icon=None, item=None):
    global _tally_enabled, _running_tally
    _tally_enabled = not _tally_enabled
    _running_tally = []
    if tray_icon:
        tray_icon.update_menu()
    if _tally_enabled:
        _tk_root.after(0, lambda: show_toast(
            "Running tally ON",
            subtitle="Each summed snip adds to the total", accent=BLUE))
    else:
        _tk_root.after(0, lambda: show_toast(
            "Running tally OFF", subtitle="Tally cleared", accent=BLUE))

def reset_tally(icon=None, item=None):
    global _running_tally
    _running_tally = []
    _tk_root.after(0, lambda: show_toast(
        "Tally reset", subtitle="Running total cleared", accent=BLUE))

def close_all_snips():
    for w in list(snip_windows): w.close()

def _start_hotkey_listener():
    try:
        import keyboard
        keyboard.add_hotkey("ctrl+shift+s", take_snip)   # snip -> floating window
        keyboard.add_hotkey("ctrl+shift+x", sum_snip)    # snip -> sum instantly
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
        item("✂  Take Snip  (Ctrl+Shift+S)",
             lambda i, _: take_snip(), default=True),
        item("🔢  Snip & Sum  (Ctrl+Shift+X)",
             lambda i, _: sum_snip()),
        pystray.Menu.SEPARATOR,
        item("➕  Running tally",
             toggle_tally, checked=lambda i: _tally_enabled),
        item("♻  Reset tally", reset_tally),
        pystray.Menu.SEPARATOR,
        item("🗑  Close All Snips",
             lambda i, _: _tk_root.after(0, close_all_snips)),
        item("✕  Quit", quit_app),
    )
    tray_icon = pystray.Icon("DarcySnipTool", make_tray_image(),
                              "DarcySnipTool — snip & sum figures", menu)
    tray_icon.run()

if __name__ == "__main__":
    make_ico_file()
    threading.Thread(target=_run_tk, daemon=True).start()
    _tk_ready.wait()
    threading.Thread(target=_start_hotkey_listener, daemon=True).start()
    build_tray()
