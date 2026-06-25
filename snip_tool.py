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
snip_windows = []
tray_icon    = None
_tk_root     = None
_tk_ready    = threading.Event()
_overlay_running = False  # prevent double-launch

ORANGE    = "#FF8C00"
ORANGE_DK = "#CC6600"
GOLD      = "#FFD700"
DARK_BG   = "#1e1e2e"
BLUE      = "#7aa2f7"

user32   = ctypes.windll.user32
gdi32    = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


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
    l = user32.GetSystemMetrics(76)
    t = user32.GetSystemMetrics(77)
    w = user32.GetSystemMetrics(78)
    h = user32.GetSystemMetrics(79)
    return l, t, w, h


# ══════════════════════════════════════════════════════════════════════════
#  WIN32 CONSTANTS
# ══════════════════════════════════════════════════════════════════════════
WS_POPUP         = 0x80000000
WS_VISIBLE       = 0x10000000
WS_EX_TOPMOST    = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
CS_HREDRAW       = 0x0002
CS_VREDRAW       = 0x0001
IDC_CROSS        = 32515
WM_LBUTTONDOWN   = 0x0201
WM_LBUTTONUP     = 0x0202
WM_MOUSEMOVE     = 0x0200
WM_KEYDOWN       = 0x0100
WM_PAINT         = 0x000F
WM_DESTROY       = 0x0002
WM_ERASEBKGND    = 0x0014
WM_SETCURSOR     = 0x0020
VK_ESCAPE        = 0x1B
PS_SOLID         = 0
PS_DOT           = 2
NULL_BRUSH       = 5
SRCCOPY          = 0x00CC0020
BI_RGB           = 0
DIB_RGB_COLORS   = 0
TRANSPARENT_MODE = 1

WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_longlong,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM)

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.c_uint),
        ("style",         ctypes.c_uint),
        ("lpfnWndProc",   WNDPROCTYPE),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     ctypes.wintypes.HINSTANCE),
        ("hIcon",         ctypes.wintypes.HICON),
        ("hCursor",       ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm",       ctypes.wintypes.HICON),
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
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_uint32 * 1),
    ]

def rgb(r,g,b): return r | (g<<8) | (b<<16)

BLUE_C  = rgb(0x7a, 0xa2, 0xf7)
WHITE_C = rgb(0xFF, 0xFF, 0xFF)
BLACK_C = rgb(0x00, 0x00, 0x00)


# ══════════════════════════════════════════════════════════════════════════
#  WIN32 OVERLAY
# ══════════════════════════════════════════════════════════════════════════
_wnd_proc_ref = None  # keep reference so GC doesn't collect it
_class_registered = False

def _run_win32_overlay(on_done):
    global _overlay_running, _wnd_proc_ref, _class_registered

    if _overlay_running:
        return
    _overlay_running = True

    vl, vt, vw, vh = get_virtual_desktop()

    # Screenshot before anything appears
    try:
        screenshot = ImageGrab.grab(bbox=(vl, vt, vl+vw, vt+vh), all_screens=True)
    except Exception:
        screenshot = ImageGrab.grab()

    img_bgr = screenshot.convert("RGB").tobytes("raw", "BGRX")

    state = {
        "dragging": False,
        "x0":0, "y0":0, "x1":0, "y1":0,
        "hdc_mem": None, "hbm": None,
        "hcursor": None,
    }

    def paint(hwnd):
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

        if state["hdc_mem"]:
            gdi32.BitBlt(hdc, 0, 0, vw, vh, state["hdc_mem"], 0, 0, SRCCOPY)

        # Semi-transparent dim over entire screen
        hbr_black = gdi32.CreateSolidBrush(BLACK_C)
        blend_dc  = gdi32.CreateCompatibleDC(hdc)
        blend_bm  = gdi32.CreateCompatibleBitmap(hdc, vw, vh)
        gdi32.SelectObject(blend_dc, blend_bm)
        rc = ctypes.wintypes.RECT(0, 0, vw, vh)
        user32.FillRect(blend_dc, ctypes.byref(rc), hbr_black)

        # Use AlphaBlend for the dim
        try:
            BLENDFUNCTION = ctypes.c_uint32(0x00FF0001)  # alpha=0x26 ~15%
            blend_alpha   = 0x28
            BLENDFUNCTION = (0) | (0<<8) | (blend_alpha<<16) | (1<<24)
            ctypes.windll.msimg32.AlphaBlend(
                hdc, 0, 0, vw, vh,
                blend_dc, 0, 0, vw, vh,
                BLENDFUNCTION)
        except Exception:
            pass

        gdi32.DeleteDC(blend_dc)
        gdi32.DeleteObject(blend_bm)
        gdi32.DeleteObject(hbr_black)

        gdi32.SetBkMode(hdc, TRANSPARENT_MODE)
        null_brush = gdi32.GetStockObject(NULL_BRUSH)
        gdi32.SelectObject(hdc, null_brush)

        if state["dragging"]:
            x0 = state["x0"] - vl;  y0 = state["y0"] - vt
            x1 = state["x1"] - vl;  y1 = state["y1"] - vt
            rx1,ry1 = min(x0,x1), min(y0,y1)
            rx2,ry2 = max(x0,x1), max(y0,y1)

            # Re-blit the unshaded screenshot inside the selection
            if state["hdc_mem"]:
                gdi32.BitBlt(hdc, rx1, ry1, rx2-rx1, ry2-ry1,
                             state["hdc_mem"], rx1, ry1, SRCCOPY)

            # White outer border
            hpen = gdi32.CreatePen(PS_SOLID, 1, WHITE_C)
            gdi32.SelectObject(hdc, hpen)
            gdi32.SelectObject(hdc, null_brush)
            gdi32.Rectangle(hdc, rx1-1, ry1-1, rx2+2, ry2+2)
            gdi32.DeleteObject(hpen)

            # Blue inner border
            hpen = gdi32.CreatePen(PS_SOLID, 2, BLUE_C)
            gdi32.SelectObject(hdc, hpen)
            gdi32.Rectangle(hdc, rx1, ry1, rx2, ry2)
            gdi32.DeleteObject(hpen)

            # Size label
            w_px = abs(state["x1"]-state["x0"])
            h_px = abs(state["y1"]-state["y0"])
            label = f" {w_px} x {h_px} "
            gdi32.SetTextColor(hdc, WHITE_C)
            lx = rx2+6; ly = ry2+6
            if lx+110 > vw: lx = rx1-116
            if ly+22  > vh: ly = ry1-28
            user32.TextOutW(hdc, lx, ly, label, len(label))
        else:
            # Crosshair
            mx = state["x1"]-vl;  my = state["y1"]-vt
            hpen = gdi32.CreatePen(PS_DOT, 1, BLUE_C)
            gdi32.SelectObject(hdc, hpen)
            gdi32.MoveToEx(hdc, 0,  my, None); gdi32.LineTo(hdc, vw, my)
            gdi32.MoveToEx(hdc, mx, 0,  None); gdi32.LineTo(hdc, mx, vh)
            gdi32.DeleteObject(hpen)

        user32.EndPaint(hwnd, ctypes.byref(ps))

    def GET_X(lp): return ctypes.c_int16(lp & 0xFFFF).value + vl
    def GET_Y(lp): return ctypes.c_int16((lp>>16) & 0xFFFF).value + vt

    def wnd_proc(hwnd, msg, wp, lp):
        if msg == WM_SETCURSOR:
            user32.SetCursor(state["hcursor"])
            return 1
        elif msg == WM_MOUSEMOVE:
            state["x1"] = GET_X(lp); state["y1"] = GET_Y(lp)
            user32.InvalidateRect(hwnd, None, False)
            return 0
        elif msg == WM_LBUTTONDOWN:
            state["dragging"] = True
            state["x0"] = state["x1"] = GET_X(lp)
            state["y0"] = state["y1"] = GET_Y(lp)
            user32.SetCapture(hwnd)
            return 0
        elif msg == WM_LBUTTONUP:
            user32.ReleaseCapture()
            x0,y0 = state["x0"], state["y0"]
            x1,y1 = GET_X(lp), GET_Y(lp)
            rx1,ry1 = min(x0,x1), min(y0,y1)
            rx2,ry2 = max(x0,x1), max(y0,y1)
            user32.DestroyWindow(hwnd)
            if (rx2-rx1) > 5 and (ry2-ry1) > 5:
                cx1,cy1 = rx1-vl, ry1-vt
                cx2,cy2 = rx2-vl, ry2-vt
                cropped = screenshot.crop((cx1,cy1,cx2,cy2))
                _tk_root.after(0, lambda: FloatingSnip(cropped, x=rx1, y=ry1))
            return 0
        elif msg == WM_KEYDOWN and wp == VK_ESCAPE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == WM_PAINT:
            paint(hwnd)
            return 0
        elif msg == WM_ERASEBKGND:
            return 1
        elif msg == WM_DESTROY:
            if state["hdc_mem"]: gdi32.DeleteDC(state["hdc_mem"])
            if state["hbm"]:     gdi32.DeleteObject(state["hbm"])
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    _wnd_proc_ref = WNDPROCTYPE(wnd_proc)
    hinstance  = kernel32.GetModuleHandleW(None)
    class_name = "DarcySnip_v2"

    # Always unregister first to avoid stale class from previous run
    user32.UnregisterClassW(class_name, hinstance)

    wc = WNDCLASSEXW()
    wc.cbSize       = ctypes.sizeof(WNDCLASSEXW)
    wc.style        = CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc  = _wnd_proc_ref
    wc.hInstance    = hinstance
    wc.hbrBackground= 0
    wc.lpszClassName= class_name
    user32.RegisterClassExW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(
        WS_EX_TOPMOST,
        class_name, "DarcySnipTool",
        WS_POPUP | WS_VISIBLE,
        vl, vt, vw, vh,
        None, None, hinstance, None)

    if not hwnd:
        _overlay_running = False
        return

    # Load crosshair cursor
    state["hcursor"] = user32.LoadCursorW(
        None, ctypes.cast(IDC_CROSS, ctypes.wintypes.LPCWSTR))

    # Build off-screen bitmap with screenshot
    hdc_win = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbm     = gdi32.CreateCompatibleBitmap(hdc_win, vw, vh)
    gdi32.SelectObject(hdc_mem, hbm)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize        = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth       = vw
    bmi.bmiHeader.biHeight      = -vh
    bmi.bmiHeader.biPlanes      = 1
    bmi.bmiHeader.biBitCount    = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf = (ctypes.c_byte * len(img_bgr)).from_buffer_copy(img_bgr)
    gdi32.SetDIBits(hdc_mem, hbm, 0, vh, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
    user32.ReleaseDC(hwnd, hdc_win)

    state["hdc_mem"] = hdc_mem
    state["hbm"]     = hbm

    user32.SetForegroundWindow(hwnd)
    user32.SetCursor(state["hcursor"])

    # Message loop
    msg_s = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg_s), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg_s))
        user32.DispatchMessageW(ctypes.byref(msg_s))

    user32.UnregisterClassW(class_name, hinstance)
    _overlay_running = False


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
#  TK + TRAY + HOTKEY
# ══════════════════════════════════════════════════════════════════════════
def take_snip():
    threading.Thread(
        target=_run_win32_overlay,
        args=(None,),
        daemon=True
    ).start()

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
