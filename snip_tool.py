"""
DarcySnipTool - System tray snipping tool for Windows
Uses raw Win32 for the selection overlay so it spans all monitors correctly.
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

# ── DPI aware ─────────────────────────────────────────────────────────────
try:
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
BLUE_RGB  = (0x7a, 0xa2, 0xf7)


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
#  VIRTUAL DESKTOP  (real physical pixels, all monitors)
# ══════════════════════════════════════════════════════════════════════════
def get_virtual_desktop():
    u = ctypes.windll.user32
    l = u.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    t = u.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    w = u.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    h = u.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    return l, t, w, h


# ══════════════════════════════════════════════════════════════════════════
#  WIN32 OVERLAY  — pure ctypes, no tkinter, spans all monitors
# ══════════════════════════════════════════════════════════════════════════
# Win32 constants
WS_POPUP          = 0x80000000
WS_EX_TOPMOST     = 0x00000008
WS_EX_TOOLWINDOW  = 0x00000080
WS_EX_LAYERED     = 0x00080000
WS_VISIBLE        = 0x10000000
CS_HREDRAW        = 0x0002
CS_VREDRAW        = 0x0001
IDC_CROSS         = 32515
WM_LBUTTONDOWN    = 0x0201
WM_LBUTTONUP      = 0x0202
WM_MOUSEMOVE      = 0x0200
WM_KEYDOWN        = 0x0100
WM_PAINT          = 0x000F
WM_DESTROY        = 0x0002
WM_ERASEBKGND     = 0x0014
VK_ESCAPE         = 0x1B
PS_SOLID          = 0
PS_DOT            = 2
LWA_ALPHA         = 0x00000002
LWA_COLORKEY      = 0x00000001
DIB_RGB_COLORS    = 0
BI_RGB            = 0
SRCCOPY           = 0x00CC0020
TRANSPARENT       = 1
OPAQUE            = 2

user32 = ctypes.windll.user32
gdi32  = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.wintypes.HWND,
    ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.c_uint),
        ("style",         ctypes.c_uint),
        ("lpfnWndProc",   WNDPROCTYPE),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     ctypes.wintypes.HANDLE),
        ("hIcon",         ctypes.wintypes.HANDLE),
        ("hCursor",       ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HANDLE),
        ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm",       ctypes.wintypes.HANDLE),
    ]

class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc",         ctypes.wintypes.HDC),
        ("fErase",      ctypes.wintypes.BOOL),
        ("rcPaint",     ctypes.wintypes.RECT),
        ("fRestore",    ctypes.wintypes.BOOL),
        ("fIncUpdate",  ctypes.wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          ctypes.c_uint32),
        ("biWidth",         ctypes.c_int32),
        ("biHeight",        ctypes.c_int32),
        ("biPlanes",        ctypes.c_uint16),
        ("biBitCount",      ctypes.c_uint16),
        ("biCompression",   ctypes.c_uint32),
        ("biSizeImage",     ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed",       ctypes.c_uint32),
        ("biClrImportant",  ctypes.c_uint32),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", ctypes.c_uint32 * 1)]


def _run_win32_overlay(result_box, done_event):
    """
    Runs a pure Win32 fullscreen overlay covering all monitors.
    Draws the pre-captured screenshot as background, then lets user drag
    a selection rectangle. Returns selected coords in result_box.
    """
    vl, vt, vw, vh = get_virtual_desktop()

    # Capture screen BEFORE showing anything
    screenshot = ImageGrab.grab(bbox=(vl, vt, vl+vw, vt+vh), all_screens=True)
    # Convert to BGR raw bytes for BitBlt
    screenshot_rgb = screenshot.convert("RGB")
    img_data = screenshot_rgb.tobytes("raw", "BGRX")

    state = {
        "dragging": False,
        "x0": 0, "y0": 0,
        "x1": 0, "y1": 0,
        "hwnd": None,
        "hdc_mem": None,
        "hbm": None,
    }

    def make_color(r, g, b):
        return r | (g << 8) | (b << 16)

    BLUE_C  = make_color(0x7a, 0xa2, 0xf7)
    WHITE_C = make_color(0xFF, 0xFF, 0xFF)
    BLACK_C = make_color(0x00, 0x00, 0x00)

    def draw(hwnd):
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

        # Blit the screenshot
        if state["hdc_mem"]:
            gdi32.BitBlt(hdc, 0, 0, vw, vh, state["hdc_mem"], 0, 0, SRCCOPY)

        # Dim overlay
        hbr = gdi32.CreateSolidBrush(BLACK_C)
        old_rop = gdi32.SetROP2(hdc, 6)  # R2_MASKPEN gives semi-transparent look
        gdi32.SetROP2(hdc, old_rop)

        if state["dragging"]:
            x0,y0 = state["x0"]-vl, state["y0"]-vt
            x1,y1 = state["x1"]-vl, state["y1"]-vt
            rx1,ry1 = min(x0,x1), min(y0,y1)
            rx2,ry2 = max(x0,x1), max(y0,y1)

            # Blue dashed selection rectangle
            hpen_white = gdi32.CreatePen(PS_SOLID, 1, WHITE_C)
            hpen_blue  = gdi32.CreatePen(PS_SOLID, 2, BLUE_C)
            null_brush = gdi32.GetStockObject(5)  # NULL_BRUSH

            gdi32.SelectObject(hdc, hpen_white)
            gdi32.SelectObject(hdc, null_brush)
            gdi32.Rectangle(hdc, rx1-1, ry1-1, rx2+1, ry2+1)

            gdi32.SelectObject(hdc, hpen_blue)
            gdi32.Rectangle(hdc, rx1, ry1, rx2, ry2)

            # Size label
            w_px = abs(state["x1"] - state["x0"])
            h_px = abs(state["y1"] - state["y0"])
            label = f" {w_px} x {h_px} px "
            gdi32.SetBkMode(hdc, TRANSPARENT)
            gdi32.SetTextColor(hdc, WHITE_C)
            lx = rx2 + 6
            ly = ry2 + 6
            if lx + 100 > vw: lx = rx1 - 106
            if ly + 20  > vh: ly = ry1 - 26
            user32.TextOutW(hdc, lx, ly, label, len(label))

            gdi32.DeleteObject(hpen_white)
            gdi32.DeleteObject(hpen_blue)
        else:
            # Crosshair
            hpen = gdi32.CreatePen(PS_DOT, 1, BLUE_C)
            gdi32.SelectObject(hdc, hpen)
            null_brush = gdi32.GetStockObject(5)
            gdi32.SelectObject(hdc, null_brush)
            mx,my = state["x1"]-vl, state["y1"]-vt
            gdi32.MoveToEx(hdc, 0, my, None)
            gdi32.LineTo(hdc, vw, my)
            gdi32.MoveToEx(hdc, mx, 0, None)
            gdi32.LineTo(hdc, mx, vh)
            gdi32.DeleteObject(hpen)

        gdi32.DeleteObject(hbr)
        user32.EndPaint(hwnd, ctypes.byref(ps))

    def GET_X(lparam): return ctypes.c_int16(lparam & 0xFFFF).value + vl
    def GET_Y(lparam): return ctypes.c_int16((lparam >> 16) & 0xFFFF).value + vt

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_MOUSEMOVE:
            state["x1"] = GET_X(lparam)
            state["y1"] = GET_Y(lparam)
            if state["dragging"]:
                pass
            user32.InvalidateRect(hwnd, None, False)
            return 0

        elif msg == WM_LBUTTONDOWN:
            state["dragging"] = True
            state["x0"] = state["x1"] = GET_X(lparam)
            state["y0"] = state["y1"] = GET_Y(lparam)
            user32.SetCapture(hwnd)
            return 0

        elif msg == WM_LBUTTONUP:
            user32.ReleaseCapture()
            x0,y0 = state["x0"], state["y0"]
            x1,y1 = GET_X(lparam), GET_Y(lparam)
            rx1,ry1 = min(x0,x1), min(y0,y1)
            rx2,ry2 = max(x0,x1), max(y0,y1)
            if (rx2-rx1) > 5 and (ry2-ry1) > 5:
                result_box.append((rx1, ry1, rx2, ry2))
            user32.DestroyWindow(hwnd)
            return 0

        elif msg == WM_KEYDOWN:
            if wparam == VK_ESCAPE:
                user32.DestroyWindow(hwnd)
            return 0

        elif msg == WM_PAINT:
            draw(hwnd)
            return 0

        elif msg == WM_ERASEBKGND:
            return 1

        elif msg == WM_DESTROY:
            # Clean up GDI objects
            if state["hdc_mem"]:
                gdi32.DeleteDC(state["hdc_mem"])
            if state["hbm"]:
                gdi32.DeleteObject(state["hbm"])
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wnd_proc_cb = WNDPROCTYPE(wnd_proc)
    hinstance   = kernel32.GetModuleHandleW(None)
    class_name  = "DarcySnipOverlay"

    wc = WNDCLASSEX()
    wc.cbSize       = ctypes.sizeof(WNDCLASSEX)
    wc.style        = CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc  = wnd_proc_cb
    wc.hInstance    = hinstance
    wc.hCursor      = user32.LoadCursorW(None, ctypes.cast(IDC_CROSS, ctypes.wintypes.LPCWSTR))
    wc.hbrBackground= 0
    wc.lpszClassName= class_name
    user32.RegisterClassExW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
        class_name, "DarcySnipTool",
        WS_POPUP | WS_VISIBLE,
        vl, vt, vw, vh,
        None, None, hinstance, None
    )
    state["hwnd"] = hwnd

    # Prepare off-screen DC with screenshot bitmap
    hdc_screen = user32.GetDC(hwnd)
    hdc_mem    = gdi32.CreateCompatibleDC(hdc_screen)
    hbm        = gdi32.CreateCompatibleBitmap(hdc_screen, vw, vh)
    gdi32.SelectObject(hdc_mem, hbm)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize        = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth       = vw
    bmi.bmiHeader.biHeight      = -vh   # negative = top-down
    bmi.bmiHeader.biPlanes      = 1
    bmi.bmiHeader.biBitCount    = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf = (ctypes.c_byte * len(img_data)).from_buffer_copy(img_data)
    gdi32.SetDIBits(hdc_mem, hbm, 0, vh, buf,
                    ctypes.byref(bmi), DIB_RGB_COLORS)
    user32.ReleaseDC(hwnd, hdc_screen)

    state["hdc_mem"] = hdc_mem
    state["hbm"]     = hbm

    user32.ShowWindow(hwnd, 1)
    user32.SetForegroundWindow(hwnd)

    # Message loop
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    # Store screenshot for cropping
    result_box.append(screenshot)
    done_event.set()
    user32.UnregisterClassW(class_name, hinstance)


# ══════════════════════════════════════════════════════════════════════════
#  TAKE SNIP  — launches the Win32 overlay in a thread
# ══════════════════════════════════════════════════════════════════════════
def take_snip():
    def _run():
        result_box  = []
        done_event  = threading.Event()
        _run_win32_overlay(result_box, done_event)
        done_event.wait()

        # result_box = [coords_tuple (optional), screenshot_image]
        # coords is first item if selection was made
        coords     = None
        screenshot = None
        for item_ in result_box:
            if isinstance(item_, tuple):
                coords = item_
            elif isinstance(item_, Image.Image):
                screenshot = item_

        if coords and screenshot:
            rx1,ry1,rx2,ry2 = coords
            vl,vt,vw,vh = get_virtual_desktop()
            # Crop coords are relative to virtual desktop origin
            cx1 = rx1 - vl
            cy1 = ry1 - vt
            cx2 = rx2 - vl
            cy2 = ry2 - vt
            cropped = screenshot.crop((cx1, cy1, cx2, cy2))
            _tk_root.after(0, lambda: FloatingSnip(cropped, x=rx1, y=ry1))

    threading.Thread(target=_run, daemon=True).start()


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
                            bd=0, highlightthickness=0, bg=DARK_BG, cursor="fleur")
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

        self._dx = self._dy = self._moved = 0
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
#  TK + TRAY + HOTKEY
# ══════════════════════════════════════════════════════════════════════════
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
