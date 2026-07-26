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

# Highlighter pen colours (RGB, used as a multiply tint)
HL_YELLOW = (255, 245, 120)
HL_GREEN  = (170, 255, 170)
HL_PINK   = (255, 180, 210)
HL_BLUE   = (170, 215, 255)


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
                        # Snip-and-sum: show the total right beside the snip,
                        # anchored to the top-right corner of the selection.
                        near = (real_x2, real_y1)
                        _sum_image_directly(img, near_pos=near)
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

# ── RapidOCR (neural OCR, runs fully offline) — PRIMARY ENGINE ────────────
_rapid_engine = None
_rapid_checked = False

def _get_rapidocr():
    """Return a RapidOCR engine if available, else None (cached)."""
    global _rapid_engine, _rapid_checked
    if _rapid_checked:
        return _rapid_engine
    _rapid_checked = True
    try:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            from rapidocr import RapidOCR
        _rapid_engine = RapidOCR()
    except Exception:
        _rapid_engine = None
    return _rapid_engine


def _rapidocr_items(pil_image):
    """
    Run RapidOCR and return [(text, x_left, x_right, x_centre, y_centre, h)]
    so we can reconstruct table rows and columns.
    """
    try:
        engine = _get_rapidocr()
        if engine is None:
            return None
        import numpy as np
        arr = np.array(pil_image.convert("RGB"))
        result, _elapse = engine(arr)
        if not result:
            return None
        items = []
        for entry in result:
            try:
                box, txt = entry[0], entry[1]
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                items.append((str(txt), min(xs), max(xs),
                              sum(xs)/len(xs), sum(ys)/len(ys),
                              max(ys) - min(ys)))
            except Exception:
                continue
        return items or None
    except Exception:
        return None


def _column_bounds_from_whitespace(items, width, min_gutter=10):
    """
    Find column boundaries by locating vertical gutters — x ranges where no
    text appears anywhere in the snip.
    """
    if not items or width <= 0:
        return []
    occupied = bytearray(width + 1)
    for (_t, l, r, _cx, _cy, _h) in items:
        a = max(0, int(l)); b = min(width, int(r))
        for x in range(a, b + 1):
            occupied[x] = 1

    gutters, run_start = [], None
    for x in range(width + 1):
        if not occupied[x]:
            if run_start is None:
                run_start = x
        else:
            if run_start is not None:
                if x - run_start >= min_gutter:
                    gutters.append((run_start, x))
                run_start = None
    if run_start is not None and (width - run_start) >= min_gutter:
        gutters.append((run_start, width))

    edge = max(5, width // 100)
    inner = [(a, b) for (a, b) in gutters if a > edge and b < width - edge]
    return [(a + b) / 2.0 for (a, b) in inner]


def _column_bounds_from_header(items, med_h):
    """
    Use the top row (usually column headers) to infer column boundaries.
    Headers are short and well separated, so they reveal splits that long
    data values would otherwise hide.
    """
    if not items:
        return []
    top_y = min(i[4] for i in items)
    header = [i for i in items if abs(i[4] - top_y) <= max(4.0, med_h * 0.6)]
    if len(header) < 2:
        return []
    header.sort(key=lambda i: i[1])
    return [(a[2] + b[1]) / 2.0 for a, b in zip(header, header[1:])]


def _merge_bounds(*lists, tol=15.0):
    """Combine boundary lists, collapsing ones that sit close together."""
    allb = sorted(b for lst in lists for b in lst)
    out = []
    for b in allb:
        if not out or b - out[-1] > tol:
            out.append(b)
        else:
            out[-1] = (out[-1] + b) / 2.0
    return out


def _extract_table(pil_image):
    """
    Reconstruct a table from the snip as rows of cells.
    Returns a list of rows, each a list of cell strings, or None.
    """
    items = _rapidocr_items(pil_image)
    if not items:
        return None

    width = pil_image.size[0]

    # ── Rows: cluster by vertical centre, tolerance from text height ──────
    heights = sorted(h for (_t, _l, _r, _cx, _cy, h) in items)
    med_h = heights[len(heights) // 2] if heights else 12
    row_tol = max(6.0, med_h * 0.6)

    rows = []
    for it in sorted(items, key=lambda i: i[4]):
        placed = False
        for r in rows:
            if abs(it[4] - r["y"]) <= row_tol:
                r["cells"].append(it)
                r["y"] = (r["y"] * r["n"] + it[4]) / (r["n"] + 1)
                r["n"] += 1
                placed = True
                break
        if not placed:
            rows.append({"y": it[4], "n": 1, "cells": [it]})
    rows.sort(key=lambda r: r["y"])

    # ── Columns: whitespace gutters + header-row gaps ────────────────────
    # Whitespace finds obvious splits; the header row reveals splits that
    # long data values (e.g. product names) would otherwise mask.
    gutter = max(8, int(med_h * 0.7))
    ws_bounds  = _column_bounds_from_whitespace(items, width, min_gutter=gutter)
    hdr_bounds = _column_bounds_from_header(items, med_h)
    bounds = _merge_bounds(ws_bounds, hdr_bounds, tol=max(10.0, med_h))

    def col_index(cx):
        i = 0
        for b in bounds:
            if cx > b:
                i += 1
        return i

    ncols = len(bounds) + 1

    grid = []
    for r in rows:
        cells = [""] * ncols
        for it in sorted(r["cells"], key=lambda i: i[3]):
            ci = min(col_index(it[3]), ncols - 1)
            cells[ci] = (cells[ci] + " " + it[0]).strip() if cells[ci] else it[0]
        grid.append(cells)

    # Drop columns that are empty in every row
    keep = [c for c in range(ncols) if any(row[c].strip() for row in grid)]
    if keep and len(keep) < ncols:
        grid = [[row[c] for c in keep] for row in grid]

    return grid or None


def _rapidocr_read(pil_image):
    """
    Run RapidOCR on a PIL image.
    Returns (text, min_confidence) with lines ordered top-to-bottom,
    or None if unavailable / nothing found.
    """
    try:
        engine = _get_rapidocr()
        if engine is None:
            return None
        import numpy as np
        arr = np.array(pil_image.convert("RGB"))
        result, _elapse = engine(arr)
        if not result:
            return None

        items = []
        for entry in result:
            try:
                box, txt, score = entry[0], entry[1], entry[2]
                cy = sum(p[1] for p in box) / len(box)
                items.append((cy, str(txt), float(score)))
            except Exception:
                continue
        if not items:
            return None

        items.sort(key=lambda it: it[0])          # preserve row order
        text = "\n".join(t for _cy, t, _s in items)
        confs = [s for _cy, t, s in items if any(c.isdigit() for c in t)]
        min_conf = min(confs) if confs else None
        return text, min_conf
    except Exception:
        return None


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
        data = img.tobytes("raw", "BGRA")
        buf = CryptographicBuffer.create_from_byte_array(list(data))
        bmp = SoftwareBitmap.create_copy_from_buffer(
            buf, BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED)

        import asyncio
        async def _run():
            return await engine.recognize_async(bmp)

        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_run())
            loop.close()
        except Exception:
            return None

        if result is None:
            return None

        return "\n".join(line.text for line in result.lines)
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
    # Normalise Unicode look-alikes that OCR engines often emit
    # (full-width parens/minus, en/em dashes, fancy quotes for thousands).
    text = (text or "")
    for a, b in (("（", "("), ("）", ")"),
                 ("−", "-"), ("–", "-"), ("—", "-"), ("‒", "-"),
                 ("，", ","), ("．", "."), ("﹒", "."),
                 ("｜", "|")):
        text = text.replace(a, b)

    numbers = []
    # [ \t]* (not \s*) keeps tokens from spanning newlines.
    token_re = re.compile(r'\(?[-+]?[£$€]?[ \t]*\d[\d.,]*-?%?\)?')

    for raw in token_re.findall(text):
        token = raw.strip()
        if not any(ch.isdigit() for ch in token):
            continue

        negative = False
        # Accounting negatives: accept either paren, since OCR can drop one
        if "(" in token or ")" in token:
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


def _find_subsets(values, target, max_items=4, max_results=6):
    """
    Find combinations of `values` that sum to `target`.
    Works in whole pennies so floating-point drift can't cause a near-miss.
    Searches smallest-first and stops at the first size that produces hits,
    so the simplest explanation wins.
    Returns (exact_matches, closest_or_None).
    """
    from itertools import combinations

    vals = [(int(round(v * 100)), v) for v in values if abs(v) > 0.004]
    if not vals:
        return [], None
    tgt = int(round(target * 100))

    # Keep the search bounded on long lists
    n = len(vals)
    if n > 80:
        max_items = min(max_items, 2)
    elif n > 45:
        max_items = min(max_items, 3)
    max_items = max(1, min(max_items, n))

    exact = []
    best = None                      # (distance_in_pennies, combo)

    for size in range(1, max_items + 1):
        for combo in combinations(vals, size):
            s = 0
            for c in combo:
                s += c[0]
            d = s - tgt
            if d == 0:
                exact.append([c[1] for c in combo])
                if len(exact) >= max_results:
                    return exact, None
            else:
                ad = -d if d < 0 else d
                if best is None or ad < best[0]:
                    best = (ad, [c[1] for c in combo])
        if exact:
            return exact, None

    return exact, best


def _detect_currency(text):
    """Return the first currency symbol found in the OCR text, or GBP default."""
    for sym in ("£", "$", "€"):
        if sym in text:
            return sym
    return "£"   # default for this user's F&O entity (GBP)


def _fmt_num(n, currency=""):
    """Format a number cleanly: drop trailing .0, add thousands separators."""
    if abs(n - round(n)) < 1e-9:
        body = f"{int(round(n)):,}"
    else:
        body = f"{n:,.2f}"
    return f"{currency}{body}" if currency else body


# ── Row counting: how many lines of text are actually in the snip? ─────────
def _count_text_rows(pil_img):
    """
    Count horizontal bands of text via a projection profile.
    Lets us tell when OCR has silently missed a row.
    """
    try:
        from PIL import ImageOps
        g = pil_img.convert("L")
        w, h = g.size
        if h < 20 or w < 10:
            return 0
        g = ImageOps.autocontrast(g, cutoff=1)
        px = g.load()
        hist = g.histogram()
        dark_bg = sum(hist[:128]) > sum(hist[128:])
        step = max(1, w // 200)
        samples = len(range(0, w, step))
        thresh = max(1, int(0.02 * samples))
        bands, in_band, start = 0, False, 0
        for y in range(h):
            ink = 0
            for x in range(0, w, step):
                v = px[x, y]
                if (v > 170) if dark_bg else (v < 110):
                    ink += 1
            if ink >= thresh and not in_band:
                in_band, start = True, y
            elif ink < thresh and in_band:
                in_band = False
                if y - start >= max(5, h // 60):
                    bands += 1
        if in_band and h - start >= max(5, h // 60):
            bands += 1
        return bands
    except Exception:
        return 0


# ── Preprocessing variants for multi-pass OCR ─────────────────────────────
def _ocr_variants(pil_image):
    """Yield several differently-processed versions of the snip."""
    from PIL import ImageOps, ImageFilter, ImageEnhance
    out = []
    try:
        base = pil_image.convert("L")
        w, h = base.size
        hist = base.histogram()
        dark_bg = sum(hist[:128]) > sum(hist[128:])
        if dark_bg:
            base = ImageOps.invert(base)

        target = 1600
        scales = []
        for s in (3, 4, 5):
            if max(w, h) * s <= 6000:
                scales.append(s)
        if not scales:
            scales = [2]

        for s in scales:
            im = base.resize((w*s, h*s), Image.LANCZOS).filter(ImageFilter.SHARPEN)
            ac = ImageOps.autocontrast(im, cutoff=1)
            # A: normalised contrast
            out.append(ImageOps.expand(ac, border=60, fill=255))
            # B: hard binarised
            out.append(ImageOps.expand(
                ac.point(lambda p: 255 if p > 160 else 0), border=60, fill=255))
            # C: strong contrast boost
            out.append(ImageOps.expand(
                ImageEnhance.Contrast(ac).enhance(2.0), border=60, fill=255))
    except Exception:
        pass
    return out


def _ocr_raw_text(pil_image):
    """
    Return all text found in the image (line structure preserved), or None.
    Tries RapidOCR, then Windows OCR, then Tesseract.
    """
    # RapidOCR
    try:
        r = _rapidocr_read(pil_image)
        if r and r[0] and r[0].strip():
            return r[0]
    except Exception:
        pass
    # Windows OCR
    try:
        t = _windows_ocr_text(pil_image.convert("RGB"))
        if t and t.strip():
            return t
    except Exception:
        pass
    # Tesseract
    try:
        import pytesseract
        _configure_tesseract()
        from PIL import ImageOps
        img = pil_image.convert("L")
        w, h = img.size
        if max(w, h) < 1400:
            f = max(2, 1400 // max(w, h))
            img = img.resize((w*f, h*f), Image.LANCZOS)
        img = ImageOps.autocontrast(img, cutoff=1)
        img = ImageOps.expand(img, border=40, fill=255)
        t = pytesseract.image_to_string(img, config="--psm 6")
        if t and t.strip():
            return t
    except Exception:
        pass
    return None


# ── Shared OCR core: multi-pass with consensus voting ─────────────────────
def _ocr_image(pil_image):
    """
    Run OCR across several preprocessing variants and both available engines,
    then pick the result that MOST passes agree on (consensus voting).
    This rejects one-off misreads that a single pass would accept.
    Returns (numbers, total, agreement_ratio, currency, expected_rows) or None.
    """
    from collections import Counter

    expected_rows = _count_text_rows(pil_image)

    # ── PRIMARY: RapidOCR (neural, offline) ───────────────────────────────
    # Accurate enough that it needs no preprocessing; try it plain first,
    # then lightly upscaled if the row count looks short.
    rapid = _rapidocr_read(pil_image)
    if rapid is None:
        try:
            from PIL import ImageOps
            big = pil_image.convert("RGB")
            w, h = big.size
            if max(w, h) < 800:
                big = big.resize((w*2, h*2), Image.LANCZOS)
            rapid = _rapidocr_read(big)
        except Exception:
            rapid = None

    if rapid is not None:
        rtext, rconf = rapid
        rnums, rtotal = _extract_and_sum(rtext)
        if rnums:
            # If it found at least as many values as there are rows, trust it.
            if not expected_rows or len(rnums) >= expected_rows:
                return (rnums, rtotal,
                        (rconf if rconf is not None else 1.0),
                        _detect_currency(rtext), expected_rows)
            # Short read — retry once upscaled before falling through
            try:
                w, h = pil_image.size
                big = pil_image.convert("RGB").resize(
                    (w*3, h*3), Image.LANCZOS)
                r2 = _rapidocr_read(big)
                if r2:
                    t2, c2 = r2
                    n2, tot2 = _extract_and_sum(t2)
                    if n2 and len(n2) >= len(rnums):
                        return (n2, tot2, (c2 if c2 is not None else 1.0),
                                _detect_currency(t2), expected_rows)
            except Exception:
                pass
            return (rnums, rtotal,
                    (rconf if rconf is not None else 1.0),
                    _detect_currency(rtext), expected_rows)

    # ── FALLBACK: multi-pass consensus across Windows OCR + Tesseract ─────
    variants = _ocr_variants(pil_image)
    if not variants:
        variants = [pil_image]

    candidates = []      # list of (tuple_of_numbers, source_text)

    # --- Engine: Windows native OCR ---
    for v in variants:
        try:
            t = _windows_ocr_text(v.convert("RGB"))
            if t and any(ch.isdigit() for ch in t):
                nums, _ = _extract_and_sum(t)
                if nums:
                    candidates.append((tuple(nums), t))
        except Exception:
            pass

    # --- Engine 2: Tesseract ---
    try:
        import pytesseract
        _configure_tesseract()
        whitelist = "0123456789.,-()£$€%"
        cfgs = [
            f"--psm 6 -c tessedit_char_whitelist={whitelist}",
            f"--psm 4 -c tessedit_char_whitelist={whitelist}",
        ]
        for v in variants:
            for cfg in cfgs:
                try:
                    t = pytesseract.image_to_string(v, config=cfg)
                    if t and any(ch.isdigit() for ch in t):
                        nums, _ = _extract_and_sum(t)
                        if nums:
                            candidates.append((tuple(nums), t))
                except Exception:
                    pass
    except Exception:
        pass

    if not candidates:
        return None

    # Consensus vote on the exact sequence of numbers
    votes = Counter(c[0] for c in candidates)

    # Prefer the most-voted result; if a result matches the detected row
    # count and another doesn't, favour the matching one.
    def score(item):
        seq, n = item
        matches_rows = (expected_rows > 0 and len(seq) == expected_rows)
        return (matches_rows, n, len(seq))

    best_seq, best_votes = max(votes.items(), key=score)

    numbers = list(best_seq)
    total = sum(numbers)
    agreement = best_votes / max(1, len(candidates))

    # Currency from the text of a winning pass
    text_for_cur = ""
    for seq, t in candidates:
        if seq == best_seq:
            text_for_cur = t
            break
    currency = _detect_currency(text_for_cur)

    return numbers, total, agreement, currency, expected_rows


# ── Running tally across snips ─────────────────────────────────────────────
_running_tally = []          # list of individual numbers accumulated
_tally_enabled = False       # toggle from tray


def _sum_image_directly(pil_image, near_pos=None):
    """Snip-and-sum in one action: OCR then show a toast, no floating window."""
    def worker():
        result = _ocr_image(pil_image)
        if result is None:
            _tk_root.after(0, lambda: show_toast(
                "No numbers found", subtitle="Try a tighter snip",
                accent=AMBER, near_pos=near_pos))
            return
        numbers, total, agreement, currency, rows = result
        _tk_root.after(0, lambda: _present_sum(
            numbers, total, agreement, currency, near_pos, rows))
    threading.Thread(target=worker, daemon=True).start()


def _present_sum(numbers, total, agreement, currency, near_pos=None, rows=0):
    """Show a sum result toast; feed the running tally if enabled."""
    global _running_tally
    if not numbers:
        show_toast("No numbers found", subtitle="Try a tighter snip",
                   accent=AMBER, near_pos=near_pos)
        return

    count = len(numbers)

    warns = []
    # Allow a 1-row discrepancy (grid underlines can read as a row)
    if rows and count < rows - 1:
        warns.append(f"⚠ read {count} of ~{rows} rows")
    if agreement is not None and agreement < 0.75:
        warns.append("⚠ low confidence — check figures")

    # Running tally mode
    if _tally_enabled:
        _running_tally.extend(numbers)
        grand = sum(_running_tally)
        try:
            _tk_root.clipboard_clear()
            _tk_root.clipboard_append(_fmt_num(grand).replace(",", ""))
        except Exception:
            pass
        sub = (f"+{_fmt_num(total, currency)} this snip  ·  "
               f"{len(_running_tally)} values total")
        if warns:
            sub += "  ·  " + "  ".join(warns)
        show_toast(f"Running total:  {_fmt_num(grand, currency)}",
                   subtitle=sub, accent=(AMBER if warns else BLUE),
                   numbers=numbers, currency=currency, near_pos=near_pos)
        return

    # Normal sum
    try:
        _tk_root.clipboard_clear()
        _tk_root.clipboard_append(_fmt_num(total).replace(",", ""))
    except Exception:
        pass
    avg = total / count if count else 0
    stats = (f"{count} value{'s' if count != 1 else ''}  ·  "
             f"avg {_fmt_num(avg, currency)}")
    if warns:
        stats += "  ·  " + "  ".join(warns)
    show_toast(f"Total:  {_fmt_num(total, currency)}", subtitle=stats,
               accent=(AMBER if warns else GREEN),
               numbers=numbers, currency=currency, near_pos=near_pos)


# ══════════════════════════════════════════════════════════════════════════
#  SMALL THEMED INPUT DIALOG
# ══════════════════════════════════════════════════════════════════════════
def ask_amount(prompt="Difference to find", near_pos=None, prefill=""):
    """Modal mini-dialog matching the tool's theme. Returns float or None."""
    result = {"value": None}

    dlg = tk.Toplevel(_tk_root)
    dlg.overrideredirect(True)
    dlg.attributes("-topmost", True)
    dlg.configure(bg=DARK_BG)

    frame = tk.Frame(dlg, bg=DARK_BG, highlightthickness=1,
                     highlightbackground=BLUE)
    frame.pack(fill=tk.BOTH, expand=True)

    tk.Label(frame, text=prompt, bg=DARK_BG, fg="white",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
    tk.Label(frame, text="Enter the amount you're trying to explain",
             bg=DARK_BG, fg="#a9b1d6",
             font=("Segoe UI", 9)).pack(anchor="w", padx=16)

    var = tk.StringVar(value=prefill)
    entry = tk.Entry(frame, textvariable=var, bg="#2a2a3e", fg="white",
                     insertbackground="white", relief=tk.FLAT,
                     font=("Consolas", 13), width=18)
    entry.pack(padx=16, pady=(10, 4), ipady=5)

    btns = tk.Frame(frame, bg=DARK_BG)
    btns.pack(fill=tk.X, padx=16, pady=(6, 14))

    def ok(_=None):
        raw = var.get().strip()
        raw = re.sub(r"[£$€,\s]", "", raw)
        neg = raw.startswith("(") and raw.endswith(")")
        raw = raw.strip("()")
        try:
            v = float(raw)
            result["value"] = -abs(v) if neg else v
        except ValueError:
            result["value"] = None
        dlg.destroy()

    def cancel(_=None):
        result["value"] = None
        dlg.destroy()

    tk.Button(btns, text="Find", command=ok, bg=BLUE, fg="white",
              relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
              padx=14, pady=3, activebackground="#5c86e6",
              activeforeground="white").pack(side=tk.RIGHT)
    tk.Button(btns, text="Cancel", command=cancel, bg="#2a2a3e", fg="#a9b1d6",
              relief=tk.FLAT, font=("Segoe UI", 9),
              padx=12, pady=3, activebackground="#3a3a4e",
              activeforeground="white").pack(side=tk.RIGHT, padx=(0, 8))

    dlg.bind("<Return>", ok)
    dlg.bind("<Escape>", cancel)

    dlg.update_idletasks()
    w, h = dlg.winfo_width(), dlg.winfo_height()
    sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
    if near_pos:
        x, y = near_pos[0] + 12, near_pos[1]
        if x + w + 20 > sw: x = near_pos[0] - w - 12
        if y + h + 20 > sh: y = sh - h - 20
        x, y = max(10, x), max(10, y)
    else:
        x, y = (sw - w) // 2, (sh - h) // 3
    dlg.geometry(f"+{int(x)}+{int(y)}")

    entry.focus_force()
    entry.select_range(0, tk.END)
    dlg.grab_set()
    dlg.wait_window()
    return result["value"]


# ══════════════════════════════════════════════════════════════════════════
#  TOAST NOTIFICATION  (lightweight, bottom-right, auto-dismiss)
# ══════════════════════════════════════════════════════════════════════════
_active_toasts = []

def show_toast(title, subtitle="", accent=BLUE, numbers=None, duration=6000,
               currency="", near_pos=None):
    """Show a small notification that fades away.
    If near_pos=(x, y) is given, appear next to that screen point (the snip);
    otherwise appear in the bottom-right corner."""
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

        # Breakdown of the figures found, wrapped over lines so you can
        # check them against the source rather than truncated.
        if numbers:
            parts = [_fmt_num(n, currency) for n in numbers]
            lines, cur_line = [], ""
            for p in parts:
                candidate = (cur_line + "   " + p).strip() if cur_line else p
                if len(candidate) > 46:
                    lines.append(cur_line)
                    cur_line = p
                else:
                    cur_line = candidate
            if cur_line:
                lines.append(cur_line)
            if len(lines) > 6:                      # keep the card sensible
                shown = lines[:6]
                remaining = len(parts) - sum(len(l.split()) for l in shown)
                shown.append(f"… +{max(1, remaining)} more")
                lines = shown
            tk.Label(body, text="\n".join(lines), bg=DARK_BG, fg="#8b93b0",
                     font=("Consolas", 9), anchor="w",
                     justify="left").pack(anchor="w", pady=(6, 0))

        toast.update_idletasks()
        tw = toast.winfo_width()
        th = toast.winfo_height()
        sw = toast.winfo_screenwidth()
        sh = toast.winfo_screenheight()
        margin = 18

        if near_pos is not None:
            # Appear just to the right of (and level with) the selection.
            nx, ny = near_pos
            x = nx + 12
            y = ny
            # Keep it fully on-screen
            if x + tw + margin > sw:      # would overflow right → put on left
                x = nx - tw - 12
            if x < margin:
                x = margin
            if y + th + margin > sh:
                y = sh - th - margin
            if y < margin:
                y = margin
        else:
            # Bottom-right, stacked above existing toasts
            taskbar = 48
            offset  = sum(t.winfo_height() + 10 for t in _active_toasts
                          if t.winfo_exists())
            x = sw - tw - margin
            y = sh - th - taskbar - margin - offset

        toast.geometry(f"+{int(x)}+{int(y)}")

        _active_toasts.append(toast)

        # Auto-dismiss timer, pausable on hover
        state = {"job": None, "hovering": False}

        def schedule(delay=duration):
            cancel()
            state["job"] = toast.after(delay, lambda: _fade_out(toast))

        def cancel():
            if state["job"] is not None:
                try:
                    toast.after_cancel(state["job"])
                except Exception:
                    pass
                state["job"] = None

        def on_enter(_=None):
            # Keep it up (and fully opaque) while the user is reading it
            state["hovering"] = True
            cancel()
            try:
                toast.attributes("-alpha", 0.99)
            except Exception:
                pass

        def on_leave(_=None):
            state["hovering"] = False
            schedule(2500)          # short grace period after moving away

        # Click to dismiss immediately
        def dismiss(_=None):
            cancel()
            _fade_out(toast)

        for wdg in [frame, body, toast] + list(body.winfo_children()):
            wdg.bind("<Button-1>", dismiss)
            wdg.bind("<Enter>",    on_enter)
            wdg.bind("<Leave>",    on_leave)

        _fade_in(toast)
        schedule()
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

        # Pristine copy so highlights can be undone / cleared, and so OCR
        # always reads the unmarked original.
        self.original_image = image.copy()
        self.highlights = []          # list of (x1,y1,x2,y2,(r,g,b))

        # Highlighter state
        self.hl_mode   = False
        self.hl_colour = HL_YELLOW
        self._hl_start = None
        self._hl_rect  = None

        self.win.geometry(f"{w}x{h}+{x}+{y}")

        self.tk_img = ImageTk.PhotoImage(image)
        self.cv = tk.Canvas(self.win, width=w, height=h,
                            bd=0, highlightthickness=0, bg=DARK_BG,
                            cursor="fleur")
        self.cv.pack(fill=tk.BOTH, expand=True)
        self.cv_img_id = self.cv.create_image(0,0, anchor=tk.NW,
                                              image=self.tk_img)

        self._build_menu()

        self._dx = self._dy = 0
        self._moved = False
        self.cv.bind("<ButtonPress-1>",   self._ds)
        self.cv.bind("<B1-Motion>",       self._dm)
        self.cv.bind("<ButtonRelease-1>", self._click_dismiss)
        self.cv.bind("<ButtonPress-3>",   self._show_menu)
        self.win.bind("<Escape>",         lambda e: self._set_hl_mode(False))

        self.win.lift()
        snip_windows.append(self)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    # ── Menu ──────────────────────────────────────────────────────────
    def _build_menu(self):
        self.menu = tk.Menu(self.win, tearoff=0,
                            bg="#2a2a3e", fg="white",
                            activebackground=BLUE, activeforeground="white",
                            font=("Segoe UI",10), relief=tk.FLAT, bd=0)
        self.menu.add_command(label="📋  Copy",       command=self.copy)
        self.menu.add_command(label="💾  Save as...", command=self.save)
        self.menu.add_command(label="🔢  Sum numbers (OCR)",
                              command=self.sum_numbers)
        self.menu.add_command(label="📊  Copy figures as column",
                              command=self.copy_figures_column)
        self.menu.add_command(label="📄  Copy all text",
                              command=self.copy_all_text)
        self.menu.add_command(label="🗂  Copy as table (for Excel)",
                              command=self.copy_as_table)
        self.menu.add_command(label="🔍  Find difference…",
                              command=self.find_difference)
        self.menu.add_separator()

        label = "🖍  Highlighter: ON" if self.hl_mode else "🖍  Highlighter"
        self.menu.add_command(label=label, command=self._toggle_hl)

        colours = tk.Menu(self.menu, tearoff=0, bg="#2a2a3e", fg="white",
                          activebackground=BLUE, activeforeground="white",
                          font=("Segoe UI",10), relief=tk.FLAT, bd=0)
        for name, rgb in (("Yellow", HL_YELLOW), ("Green", HL_GREEN),
                          ("Pink", HL_PINK), ("Blue", HL_BLUE)):
            mark = " ✓" if rgb == self.hl_colour else ""
            colours.add_command(label=name + mark,
                                command=lambda c=rgb: self._set_colour(c))
        self.menu.add_cascade(label="🎨  Highlight colour", menu=colours)

        if self.highlights:
            self.menu.add_command(label="↩  Undo highlight",
                                  command=self._undo_highlight)
            self.menu.add_command(label="🧹  Clear highlights",
                                  command=self._clear_highlights)
        self.menu.add_separator()
        self.menu.add_command(label="✕  Close", command=self.close)

    # ── Highlighter ───────────────────────────────────────────────────
    def _toggle_hl(self):
        self._set_hl_mode(not self.hl_mode)

    def _set_hl_mode(self, on):
        self.hl_mode = bool(on)
        self.cv.config(cursor="pencil" if self.hl_mode else "fleur")
        self._build_menu()

    def _set_colour(self, rgb):
        self.hl_colour = rgb
        if not self.hl_mode:
            self._set_hl_mode(True)
        else:
            self._build_menu()

    def _apply_highlights(self):
        """Rebuild the working image from the original plus all highlights."""
        from PIL import ImageDraw, ImageChops
        base = self.original_image.convert("RGB")
        if self.highlights:
            tint = Image.new("RGB", base.size, (255, 255, 255))
            td = ImageDraw.Draw(tint)
            for (x1, y1, x2, y2, rgb) in self.highlights:
                td.rectangle([x1, y1, x2, y2], fill=rgb)
            # Multiply blend = authentic highlighter: text stays dark,
            # background takes the colour.
            base = ImageChops.multiply(base, tint)
        self.image = base
        self.tk_img = ImageTk.PhotoImage(self.image)
        self.cv.itemconfigure(self.cv_img_id, image=self.tk_img)

    def _undo_highlight(self):
        if self.highlights:
            self.highlights.pop()
            self._apply_highlights()
            self._build_menu()

    def _clear_highlights(self):
        self.highlights = []
        self._apply_highlights()
        self._build_menu()

    # ── Mouse ─────────────────────────────────────────────────────────
    def _ds(self, e):
        if self.hl_mode:
            self._hl_start = (e.x, e.y)
            r, g, b = self.hl_colour
            self._hl_rect = self.cv.create_rectangle(
                e.x, e.y, e.x, e.y,
                outline=f"#{r:02x}{g:02x}{b:02x}", width=1,
                fill=f"#{r:02x}{g:02x}{b:02x}", stipple="gray50")
            return
        self._dx = e.x_root - self.win.winfo_x()
        self._dy = e.y_root - self.win.winfo_y()
        self._moved = False

    def _dm(self, e):
        if self.hl_mode:
            if self._hl_rect is not None and self._hl_start:
                x0, y0 = self._hl_start
                self.cv.coords(self._hl_rect, x0, y0, e.x, e.y)
            return
        self._moved = True
        self.win.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}")

    def _click_dismiss(self, e):
        if self.hl_mode:
            # Commit the highlight stroke into the image
            if self._hl_rect is not None and self._hl_start:
                x0, y0 = self._hl_start
                x1, y1 = min(x0, e.x), min(y0, e.y)
                x2, y2 = max(x0, e.x), max(y0, e.y)
                self.cv.delete(self._hl_rect)
                self._hl_rect = None
                self._hl_start = None
                if (x2 - x1) > 3 and (y2 - y1) > 3:
                    self.highlights.append((x1, y1, x2, y2, self.hl_colour))
                    self._apply_highlights()
                    self._build_menu()
            return
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

    def copy_figures_column(self):
        """Copy the figures one-per-line so they paste into Excel as a column."""
        near = self._toast_anchor()
        def worker():
            result = _ocr_image(self.original_image)
            if result is None:
                _tk_root.after(0, lambda: show_toast(
                    "No numbers found", subtitle="Try a tighter snip",
                    accent=AMBER, near_pos=near))
                return
            numbers, total, _conf, currency, _rows = result
            if not numbers:
                _tk_root.after(0, lambda: show_toast(
                    "No numbers found", subtitle="Try a tighter snip",
                    accent=AMBER, near_pos=near))
                return
            # Bare values, no separators/symbols, so Excel parses them as numbers
            payload = "\n".join(
                (f"{n:.2f}".rstrip("0").rstrip(".") if n % 1 else f"{int(n)}")
                for n in numbers)
            def finish():
                try:
                    _tk_root.clipboard_clear()
                    _tk_root.clipboard_append(payload)
                except Exception:
                    pass
                show_toast(f"{len(numbers)} figures copied",
                           subtitle=f"Paste as a column  ·  total "
                                    f"{_fmt_num(total, currency)}",
                           accent=GREEN, numbers=numbers,
                           currency=currency, near_pos=near)
            _tk_root.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def find_difference(self):
        """Find which of the snipped figures add up to a target amount."""
        near = self._toast_anchor()

        # Pre-fill from the clipboard if it already holds a number
        prefill = ""
        try:
            clip = _tk_root.clipboard_get()
            if clip and re.fullmatch(r"[-+(]?[£$€]?[\d,]+\.?\d*\)?", clip.strip()):
                prefill = clip.strip()
        except Exception:
            pass

        target = ask_amount("Find difference", near_pos=near, prefill=prefill)
        if target is None:
            return

        def worker():
            result = _ocr_image(self.original_image)
            if result is None:
                _tk_root.after(0, lambda: show_toast(
                    "No figures found", subtitle="Try a tighter snip",
                    accent=AMBER, near_pos=near))
                return
            numbers, _total, _conf, currency, _rows = result
            if not numbers:
                _tk_root.after(0, lambda: show_toast(
                    "No figures found", subtitle="Try a tighter snip",
                    accent=AMBER, near_pos=near))
                return

            exact, closest = _find_subsets(numbers, target)
            # If nothing matches, the sign may be the other way round
            if not exact:
                exact2, closest2 = _find_subsets(numbers, -target)
                if exact2:
                    exact = exact2
                elif closest2 and (closest is None or closest2[0] < closest[0]):
                    closest = closest2

            _tk_root.after(0, lambda: self._show_difference(
                exact, closest, target, currency, near, len(numbers)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_difference(self, exact, closest, target, currency, near, pool):
        if exact:
            first = sorted(exact[0], key=lambda v: -abs(v))
            parts = "  +  ".join(_fmt_num(v, currency) for v in first)
            sub = f"{len(first)} of {pool} figures"
            if len(exact) > 1:
                sub += f"  ·  ⚠ {len(exact)} possible combinations"
            # Copy the matching figures so they can be pasted / ticked off
            try:
                _tk_root.clipboard_clear()
                _tk_root.clipboard_append(
                    "\n".join(f"{v:.2f}".rstrip("0").rstrip(".")
                               if v % 1 else f"{int(v)}" for v in first))
            except Exception:
                pass
            show_toast(parts,
                       subtitle=f"= {_fmt_num(target, currency)}  ·  {sub}",
                       accent=(AMBER if len(exact) > 1 else GREEN),
                       near_pos=near)
            return

        if closest:
            gap_pennies, combo = closest
            gap = gap_pennies / 100.0
            parts = "  +  ".join(_fmt_num(v, currency)
                                 for v in sorted(combo, key=lambda v: -abs(v)))
            show_toast("No exact match",
                       subtitle=(f"closest is {parts}  ·  "
                                 f"out by {_fmt_num(gap, currency)}"),
                       accent=AMBER, near_pos=near)
            return

        show_toast("No combination found",
                   subtitle=f"nothing in these {pool} figures makes "
                            f"{_fmt_num(target, currency)}",
                   accent=AMBER, near_pos=near)

    def copy_as_table(self):
        """Copy the snip as tab-separated rows/columns — pastes into Excel."""
        near = self._toast_anchor()
        def worker():
            grid = _extract_table(self.original_image)
            if not grid:
                _tk_root.after(0, lambda: show_toast(
                    "No table found", subtitle="Try a tighter snip",
                    accent=AMBER, near_pos=near))
                return
            # Sanitise: a stray tab/newline inside a cell would shift the
            # whole row when Excel parses it, so collapse them to spaces.
            def clean(c):
                return " ".join(str(c).replace("\t", " ")
                                       .replace("\r", " ")
                                       .replace("\n", " ").split())
            ncols = max(len(r) for r in grid)
            # Pad every row to the same width so columns stay aligned
            rows = [[clean(c) for c in r] + [""] * (ncols - len(r))
                    for r in grid]
            tsv = "\n".join("\t".join(r) for r in rows)
            def finish():
                try:
                    _tk_root.clipboard_clear()
                    _tk_root.clipboard_append(tsv)
                except Exception:
                    pass
                show_toast(
                    f"Table copied  ·  {len(grid)} × {ncols}",
                    subtitle="Paste into Excel to fill rows and columns",
                    accent=GREEN, near_pos=near)
            _tk_root.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def copy_all_text(self):
        """Copy every line of text in the snip (codes, descriptions, refs)."""
        near = self._toast_anchor()
        def worker():
            text = _ocr_raw_text(self.original_image)
            if not text or not text.strip():
                _tk_root.after(0, lambda: show_toast(
                    "No text found", subtitle="Try a tighter snip",
                    accent=AMBER, near_pos=near))
                return
            lines = [l for l in (ln.strip() for ln in text.splitlines()) if l]
            payload = "\n".join(lines)
            def finish():
                try:
                    _tk_root.clipboard_clear()
                    _tk_root.clipboard_append(payload)
                except Exception:
                    pass
                preview = lines[0][:34] + ("…" if len(lines[0]) > 34 else "")
                show_toast(f"{len(lines)} line{'s' if len(lines)!=1 else ''} copied",
                           subtitle=preview, accent=GREEN, near_pos=near)
            _tk_root.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def _toast_anchor(self):
        try:
            return (self.win.winfo_x() + self.win.winfo_width(),
                    self.win.winfo_y())
        except Exception:
            return None

    def sum_numbers(self):
        # Anchor the result toast to the top-right of this snip window
        try:
            near = (self.win.winfo_x() + self.win.winfo_width(),
                    self.win.winfo_y())
        except Exception:
            near = None
        def worker():
            result = _ocr_image(self.original_image)
            if result is None:
                _tk_root.after(0, lambda: show_toast(
                    "No numbers found", subtitle="Try a tighter snip",
                    accent=AMBER, near_pos=near))
                return
            numbers, total, agreement, currency, rows = result
            _tk_root.after(0, lambda: _present_sum(
                numbers, total, agreement, currency, near, rows))
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
    # Warm up the OCR engine in the background so the first snip is fast
    threading.Thread(target=_get_rapidocr, daemon=True).start()
    build_tray()
