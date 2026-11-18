"""
Gemlock 1.2.1 — Riot hesap yöneticisi
Kurulum: "Gemlock Setup.pyw" dosyasına çift tıkla (kendini kurar, masaüstüne kısayol koyar).
- Çift tıkla: Riot Client kapalıysa onu da açar, hesap listesi gelir
- Oyunu seç (Valorant / LoL / sadece Client), hesabı seç (Enter / çift tık / 1-9)
  -> giriş yapar -> oyunu başlatır -> işi bitince TAMAMEN kapanır
- Rank'lar: Valorant (HenrikDev API, Riot ID ile) ve LoL (açık olan LoL client'tan)
- LoL seçiliyse Blitz'i de açıp öne getirebilir
- Şifreler Windows DPAPI ile şifreli; internete hiçbir şifre gönderilmez
"""
import base64
import ctypes
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import winreg
from ctypes import wintypes
from tkinter import filedialog, messagebox, ttk

APPDATA = os.environ.get("APPDATA", ".")
DATA_FILE = os.path.join(APPDATA, "riot_hesaplar.json")
SETTINGS_FILE = os.path.join(APPDATA, "riot_hesap_ayarlar.json")
RANK_FILE = os.path.join(APPDATA, "riot_hesap_rank.json")
ICON_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APPDATA), "Gemlock")
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APPDATA), "Gemlock")
INSTALLED_SCRIPT = os.path.join(INSTALL_DIR, "gemlock.pyw")
CREATE_NO_WINDOW = 0x08000000
VAL_REFRESH_SEC = 10 * 60  # Valorant rank'ı en fazla 10 dakikada bir yenilenir
VERSION = "1.2.1"
UPDATE_REPO = "YalinFrncgllr/Gemlock"  # herkese açık depo; güncellemeler buradan gelir

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

# Önceki bir sürüm Windows açılışına eklendiyse kaldır
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, "RiotHesapYoneticisi")
except OSError:
    pass

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Gemlock")
except Exception:
    pass

_mutex = kernel32.CreateMutexW(None, False, "Gemlock_Mutex")
if ctypes.get_last_error() == 183:
    sys.exit(0)


# ================================================================ DPAPI ====
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(func, data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    b_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    b_out = DATA_BLOB()
    if not func(ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out)):
        raise OSError("Şifre işlemi başarısız (başka bir Windows kullanıcısı olabilir)")
    raw = ctypes.string_at(b_out.pbData, b_out.cbData)
    kernel32.LocalFree(b_out.pbData)
    return raw


def encrypt(text: str) -> str:
    return base64.b64encode(_dpapi(crypt32.CryptProtectData, text.encode("utf-8"))).decode()


def decrypt(token: str) -> str:
    return _dpapi(crypt32.CryptUnprotectData, base64.b64decode(token)).decode("utf-8")


# ============================================================ Klavye ====
PUL = ctypes.POINTER(ctypes.c_ulong)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", PUL)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", PUL)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
VK_TAB, VK_RETURN, VK_CONTROL, VK_A, VK_MENU = 0x09, 0x0D, 0x11, 0x41, 0x12


def _send(vk=0, scan=0, flags=0):
    inp = INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, None)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def press(vk):
    _send(vk=vk)
    _send(vk=vk, flags=KEYEVENTF_KEYUP)


def type_text(text: str, delay=0.01):
    for ch in text:
        code = ord(ch)
        _send(scan=code, flags=KEYEVENTF_UNICODE)
        _send(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
        time.sleep(delay)


def is_front(hwnd):
    """Klavyenin şu an gerçekten bu pencereye gidip gitmediğini kontrol eder."""
    fg = user32.GetForegroundWindow()
    return bool(fg) and (fg == hwnd or user32.GetAncestor(fg, 3) == hwnd)  # GA_ROOTOWNER


def type_text_safe(text, hwnd, delay=0.01):
    """Her harften önce Riot'un hâlâ önde olduğunu kontrol eder; değilse durur.
    Böylece şifre yanlışlıkla Discord gibi başka bir pencereye yazılmaz."""
    for ch in text:
        if not is_front(hwnd):
            return False
        code = ord(ch)
        _send(scan=code, flags=KEYEVENTF_UNICODE)
        _send(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
        time.sleep(delay)
    return True


def select_all():
    _send(vk=VK_CONTROL)
    press(VK_A)
    _send(vk=VK_CONTROL, flags=KEYEVENTF_KEYUP)


# ====================================================== Pencereler ====
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def riot_hwnd():
    hwnd = user32.FindWindowW(None, "Riot Client")
    return hwnd if hwnd and user32.IsWindowVisible(hwnd) else None


def riot_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _process_name(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def find_window_of(exe_name):
    found = []

    def cb(hwnd, _):
        if user32.GetWindowTextLengthW(hwnd) == 0 or user32.GetWindow(hwnd, 4):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _process_name(pid.value) == exe_name:
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    visible = [h for h in found if user32.IsWindowVisible(h)]
    return (visible or found or [None])[0]


def bring_to_front(hwnd):
    user32.ShowWindow(hwnd, 9)
    user32.ShowWindow(hwnd, 5)
    _send(vk=VK_MENU)
    _send(vk=VK_MENU, flags=KEYEVENTF_KEYUP)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)


# ================================================== Dosyalar / yollar ====
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def find_riot_exe():
    cfg = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Riot Games", "RiotClientInstalls.json")
    try:
        data = load_json(cfg, {})
        for key in ("rc_default", "rc_live", "rc_beta"):
            p = data.get(key)
            if p and os.path.exists(p):
                return p
    except Exception:
        pass
    for p in (r"C:\Riot Games\Riot Client\RiotClientServices.exe",
              r"D:\Riot Games\Riot Client\RiotClientServices.exe"):
        if os.path.exists(p):
            return p
    return None


def find_league_dir():
    meta = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Riot Games", "Metadata",
                        "league_of_legends.live", "league_of_legends.live.product_settings.yaml")
    try:
        with open(meta, "r", encoding="utf-8") as f:
            m = re.search(r'product_install_full_path:\s*"?([^"\r\n]+)"?', f.read())
            if m and os.path.isdir(m.group(1)):
                return m.group(1)
    except OSError:
        pass
    for p in (r"C:\Riot Games\League of Legends", r"D:\Riot Games\League of Legends"):
        if os.path.isdir(p):
            return p
    return None


def find_blitz_exe(saved=None):
    if saved and os.path.exists(saved):
        return saved
    local = os.environ.get("LOCALAPPDATA", "")
    for p in (os.path.join(local, "Programs", "Blitz", "Blitz.exe"),
              os.path.join(local, "Blitz", "Blitz.exe"),
              r"C:\Program Files\Blitz\Blitz.exe"):
        if os.path.exists(p):
            return p
    return None


def ensure_icon():
    """Simge değişince yeni bir dosya adı kullanır; böylece Windows eski simgeyi önbellekten göstermez."""
    import hashlib
    data = base64.b64decode(ICON_B64)
    os.makedirs(ICON_DIR, exist_ok=True)
    ICON_FILE = os.path.join(ICON_DIR, f"gemlock_{hashlib.md5(data).hexdigest()[:8]}.ico")
    try:
        with open(ICON_FILE, "rb") as f:
            same = f.read() == data
    except OSError:
        same = False
    if not same:
        with open(ICON_FILE, "wb") as f:
            f.write(data)
    return ICON_FILE


def create_desktop_shortcut(script=None):
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-10] + "pythonw.exe"
        if os.path.exists(cand):
            exe = cand
    script = script or os.path.abspath(__file__)
    q = lambda x: x.replace("'", "''")
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "Remove-Item -LiteralPath (Join-Path $d 'Riot Hesap.lnk') -ErrorAction SilentlyContinue;"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Gemlock.lnk'));"
        f"$s.TargetPath='{q(exe)}';"
        f"$s.Arguments='\"{q(script)}\"';"
        f"$s.WorkingDirectory='{q(os.path.dirname(script))}';"
        f"$s.IconLocation='{q(ensure_icon())},0';"
        "$s.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       creationflags=CREATE_NO_WINDOW, capture_output=True, text=True)
    return r.returncode == 0


def pythonw_exe():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-10] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def gh_raw(path):
    """Açık GitHub deposundan bir dosyanın ham içeriğini indirir."""
    url = f"https://raw.githubusercontent.com/{UPDATE_REPO}/main/{urllib.parse.quote(path)}?t={int(time.time())}"
    req = urllib.request.Request(url, headers={"User-Agent": "Gemlock", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def self_install():
    """Masaüstünden/İndirilenler'den açılınca kendini gizli klasöre kurar, masaüstüne
    sadece simgeli kısayol bırakır ve indirilen kopyayı siler. Güncelleme de aynı yolla olur."""
    here = os.path.abspath(__file__)
    if os.path.normcase(os.path.dirname(here)) == os.path.normcase(INSTALL_DIR):
        return False
    try:
        os.makedirs(INSTALL_DIR, exist_ok=True)
        with open(here, "rb") as src, open(INSTALLED_SCRIPT + ".tmp", "wb") as dst:
            dst.write(src.read())
        os.replace(INSTALLED_SCRIPT + ".tmp", INSTALLED_SCRIPT)
        create_desktop_shortcut(INSTALLED_SCRIPT)
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED: simgeleri yenile
    except OSError:
        return False
    try:
        os.remove(here)  # masaüstündeki Python simgeli kopya kaybolur
    except OSError:
        pass
    return True


# ================================================================ Rank'lar ====
VAL_TR = {"Iron": "Demir", "Bronze": "Bronz", "Silver": "Gümüş", "Gold": "Altın", "Platinum": "Platin",
          "Diamond": "Elmas", "Ascendant": "Yücelik", "Immortal": "Ölümsüz", "Radiant": "Radyant"}
LOL_TR = {"IRON": "Demir", "BRONZE": "Bronz", "SILVER": "Gümüş", "GOLD": "Altın", "PLATINUM": "Platin",
          "EMERALD": "Zümrüt", "DIAMOND": "Elmas", "MASTER": "Ustalık", "GRANDMASTER": "Büyük Ustalık",
          "CHALLENGER": "Şampiyonluk"}
ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4"}


def val_tier_tr(name):
    if not name:
        return ""
    parts = name.split()
    return " ".join([VAL_TR.get(parts[0], parts[0])] + parts[1:])


def lol_tier_tr(tier, div=""):
    if not tier or tier in ("NONE", "UNRANKED"):
        return ""
    t = LOL_TR.get(tier.upper(), tier.title())
    d = ROMAN.get(div or "", "")
    return f"{t} {d}".strip() if tier.upper() not in ("MASTER", "GRANDMASTER", "CHALLENGER") else t


def split_riot_id(rid):
    if not rid or "#" not in rid:
        return None, None
    name, tag = rid.rsplit("#", 1)
    return name.strip(), tag.strip()


def summarize_val(data):
    """Henrik v3 mmr cevabını kısa özetlere çevirir."""
    cur = (data.get("current") or {})
    tier = (cur.get("tier") or {})
    cur_name = tier.get("name") or ""
    ranked = (tier.get("id") or 0) > 2 and cur_name.lower() != "unrated"
    last = ""
    for s in reversed(data.get("seasonal") or []):
        et = s.get("end_tier") or {}
        if (et.get("id") or 0) > 2:
            short = (s.get("season") or {}).get("short", "")
            last = f"{val_tier_tr(et.get('name'))} ({short})" if short else val_tier_tr(et.get("name"))
            break
    peak = data.get("peak") or {}
    peak_name = val_tier_tr((peak.get("tier") or {}).get("name"))
    peak_season = (peak.get("season") or {}).get("short", "")
    if ranked:
        short = f"{val_tier_tr(cur_name)} · {cur.get('rr', 0)} RR"
    elif last:
        short = f"Rank yok · son {last.split(' (')[0]}"
    elif peak_name:
        short = f"Rank yok · en yüksek {peak_name}"
    else:
        short = "Rank yok"
    return {
        "short": short,
        "current": f"{val_tier_tr(cur_name)} · {cur.get('rr', 0)} RR" if ranked else "Rank yok",
        "last": last,
        "peak": f"{peak_name} ({peak_season})" if peak_name and peak_season else peak_name,
        "ts": time.time(),
    }


def summarize_lol(stats):
    """LCU ranked-stats cevabını kısa özetlere çevirir."""
    qm = stats.get("queueMap") or {}

    def q(name):
        e = qm.get(name) or {}
        t = lol_tier_tr(e.get("tier"), e.get("division"))
        if not t:
            return "", e
        lp = e.get("leaguePoints", 0)
        w, l = e.get("wins", 0), e.get("losses", 0)
        wr = f" · %{round(100 * w / (w + l))}" if w + l else ""
        return f"{t} · {lp} LP{wr}", e

    solo, se = q("RANKED_SOLO_5x5")
    flex, fe = q("RANKED_FLEX_SR")
    prev = lol_tier_tr(se.get("previousSeasonEndTier"), se.get("previousSeasonEndDivision")) \
        or lol_tier_tr(stats.get("highestPreviousSeasonEndTier"), stats.get("highestPreviousSeasonEndDivision"))
    if solo:
        short = solo.split(" · %")[0]
    elif prev:
        short = f"Rank yok · son {prev}"
    else:
        short = "Rank yok"
    return {"short": short, "solo": solo or "Rank yok", "flex": flex or "Rank yok", "prev": prev, "ts": time.time()}


def henrik_mmr(api_key, region, name, tag):
    url = "https://api.henrikdev.xyz/valorant/v3/mmr/{}/pc/{}/{}".format(
        urllib.parse.quote(region), urllib.parse.quote(name), urllib.parse.quote(tag))
    req = urllib.request.Request(url, headers={"Authorization": api_key, "User-Agent": "Gemlock/" + VERSION})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8")).get("data") or {}


class LCU:
    """Açık olan LoL client'ın yerel API'si (Blitz/Porofessor'un kullandığı)."""

    def __init__(self, league_dir):
        self.lockfile = os.path.join(league_dir, "lockfile")
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE  # sadece 127.0.0.1, client'ın kendi sertifikası
        self.port = self.auth = None

    def connect(self):
        try:
            with open(self.lockfile, "r", encoding="utf-8") as f:
                _name, _pid, port, password, _proto = f.read().strip().split(":")
        except (OSError, ValueError):
            return False
        self.port = port
        self.auth = "Basic " + base64.b64encode(f"riot:{password}".encode()).decode()
        return True

    def get(self, path):
        req = urllib.request.Request(f"https://127.0.0.1:{self.port}{path}",
                                     headers={"Authorization": self.auth, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5, context=self.ctx) as r:
            return json.loads(r.read().decode("utf-8"))

    def puuid_of(self, name, tag):
        try:
            res = self.get("/player-account/aliases/v1/lookup?" +
                           urllib.parse.urlencode({"gameName": name, "tagLine": tag}))
            if isinstance(res, list) and res and res[0].get("puuid"):
                return res[0]["puuid"]
        except Exception:
            pass
        try:
            res = self.get("/lol-summoner/v1/summoners?" + urllib.parse.urlencode({"name": f"{name}#{tag}"}))
            return res.get("puuid")
        except Exception:
            return None


# ================================================================ Arayüz ====
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# Elmas Gece teması
BG, PANEL, FG, MUTED = "#0f1420", "#151c2b", "#e6edf5", "#7f8ca1"
LINE, ROW_LINE, SEL, SEG_BG = "#232d40", "#1b2333", "#1c2740", "#0b101a"
ACC, ACC_HOVER, ON_ACC, TITLE = "#bfe3f7", "#d6eefb", "#0c1624", "#0b0f18"
ACCENT = ACC
VAL_COLORS = {"Demir": "#8a8a8a", "Bronz": "#b07a3c", "Gümüş": "#b9c2c9", "Altın": "#e5b94e", "Platin": "#49b3c0",
              "Elmas": "#c48ef5", "Yücelik": "#3fbf7f", "Ölümsüz": "#e0466a", "Radyant": "#f5e07a"}
LOL_COLORS = {"Demir": "#7a7470", "Bronz": "#9a6240", "Gümüş": "#9aa4ad", "Altın": "#d4a94c", "Platin": "#4fa3a5",
              "Zümrüt": "#2fbf71", "Elmas": "#6e8ff0", "Ustalık": "#b45be0", "Büyük": "#e04b4b", "Şampiyonluk": "#f2c94c"}


def tier_color(text, cmap):
    if not text:
        return None
    if text.startswith("Rank yok") and "son" not in text and "en yüksek" not in text:
        return None
    for word in re.findall(r"\w+", text):
        if word in cmap:
            return cmap[word]
    return None


def dark_titlebar(win):
    """Windows 10/11'de başlık çubuğunu koyu yapar."""
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        r, g, b = int(TITLE[1:3], 16), int(TITLE[3:5], 16), int(TITLE[5:7], 16)
        color = ctypes.c_int(r | (g << 8) | (b << 16))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
    except Exception:
        pass
GAMES = [("valorant", "Valorant"), ("league_of_legends", "LoL"), ("", "Sadece Client")]
REGIONS = ["eu", "na", "ap", "kr", "latam", "br"]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gemlock")
        try:
            self.iconbitmap(default=ensure_icon())
        except Exception:
            pass
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.resizable(False, False)

        self.accounts = load_json(DATA_FILE, [])
        self.settings = load_json(SETTINGS_FILE, {})
        self.ranks = load_json(RANK_FILE, {})
        self.rank_lock = threading.Lock()
        self.riot_seen_at = None
        self.pending = None
        self.last_rect = None
        self.lcu_done = True
        self.blitz_done = True
        self.ranks_dirty = False
        self.ui_msg = ""
        self.logged_in = False
        self.riot_product = None
        self.update_info = None

        self.apply_styles()
        self.after(50, lambda: dark_titlebar(self))

        # oyun seçimi (segment)
        self.game = tk.StringVar(value=self.settings.get("game", "valorant"))
        seg = tk.Frame(self, bg=SEG_BG, padx=3, pady=3)
        seg.pack(fill="x", padx=12, pady=(12, 6))
        self.game_btns = {}
        for code, label in GAMES:
            b = tk.Label(seg, text=label, bg=SEG_BG, fg=MUTED, font=("Segoe UI", 9, "bold"),
                         pady=6, cursor="hand2")
            b.pack(side="left", expand=True, fill="x", padx=1)
            b.bind("<Button-1>", lambda e, c=code: self.set_game(c))
            self.game_btns[code] = b

        self.blitz = tk.BooleanVar(value=self.settings.get("blitz", True))
        self.blitz_box = tk.Checkbutton(self, text="LoL ile Blitz'i de aç", variable=self.blitz,
                                        command=self.blitz_toggled, bg=BG, fg=FG, selectcolor=PANEL,
                                        activebackground=BG, activeforeground=FG, font=("Segoe UI", 9),
                                        bd=0, highlightthickness=0)
        st_row = tk.Frame(self, bg=BG)
        st_row.pack(fill="x", padx=12, pady=(2, 6))
        self.status_dot = tk.Label(st_row, text="●", bg=BG, fg=ACC, font=("Segoe UI", 7))
        self.status_dot.pack(side="left")
        self.status = tk.Label(st_row, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.status.pack(side="left", padx=(4, 0))
        self.status_row = st_row
        self.game.trace_add("write", lambda *_: (self.update_blitz_box(), self.paint_games()))
        self._last_sel = None

        # hesap listesi
        self.sel_index = None
        box = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        box.pack(padx=12, fill="x")
        head = tk.Frame(box, bg=PANEL)
        head.pack(fill="x")
        for col, (txt, w) in enumerate((("HESAP", 18), ("VALORANT", 20), ("LOL", 20))):
            tk.Label(head, text=txt, bg=PANEL, fg=MUTED, font=("Segoe UI", 7, "bold"), anchor="w",
                     width=w).grid(row=0, column=col, sticky="w", padx=(14 if col == 0 else 4, 4), pady=(6, 5))
        tk.Frame(box, bg=LINE, height=1).pack(fill="x")
        self.rows_frame = tk.Frame(box, bg=PANEL)
        self.rows_frame.pack(fill="x")
        self.row_widgets = []

        # detay
        det = tk.Frame(self, bg=BG)
        det.pack(fill="x", padx=12, pady=(8, 0))
        self.detail_id = tk.Label(det, text="", bg=BG, fg=FG, font=("Segoe UI", 9, "bold"), anchor="w")
        self.detail_id.pack(fill="x")
        self.detail = tk.Label(det, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left",
                               anchor="w", wraplength=470)
        self.detail.pack(fill="x")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(10, 6))
        go = tk.Label(bar, text="Giriş yap", bg=ACC, fg=ON_ACC, font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2")
        go.pack(side="left", expand=True, fill="x")
        go.bind("<Button-1>", lambda e: self.choose())
        go.bind("<Enter>", lambda e: go.config(bg=ACC_HOVER))
        go.bind("<Leave>", lambda e: go.config(bg=ACC))
        cog = tk.Label(bar, text="⚙", bg=PANEL, fg=MUTED, font=("Segoe UI", 11), width=3, cursor="hand2",
                       highlightthickness=1, highlightbackground=LINE)
        cog.pack(side="left", padx=(6, 0), fill="y")
        cog.bind("<Button-1>", lambda e: self.manage())
        tk.Label(self, text="1-9 hızlı giriş · ↑↓ seç · Enter giriş · Esc kapat", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(pady=(0, 10))
        self.bind("<Up>", lambda e: self.move_sel(-1))
        self.bind("<Down>", lambda e: self.move_sel(1))

        self.bind("<Return>", lambda e: self.choose())
        self.bind("<Escape>", lambda e: self.close())
        for n in range(1, 10):
            self.bind(str(n), lambda e, i=n - 1: self.quick(i))

        self.paint_games()
        self.refresh(select_last=True)
        self.sync_game()
        self.update_blitz_box()
        self.show_detail()
        self.restore_position()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.focus_force()

        self.start_riot()
        self.refresh_val_async()
        self.check_update_async()
        self.after(300, self.tick)
        self.after(500, self.poll_ui)
        if JUST_INSTALLED:
            self.after(300, lambda: messagebox.showinfo(
                "Gemlock Setup",
                "Gemlock has been installed successfully.\n\n"
                "From now on, open it with the Gemlock shortcut on your desktop.\n"
                "The setup file has been removed.", parent=self))
        if not self.accounts:
            self.after(400, self.manage)

    # ------------------------------------------------------------ stil
    def apply_styles(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=LINE,
                     lightcolor=LINE, darkcolor=LINE, font=("Segoe UI", 9))
        st.configure("TButton", background=PANEL, foreground=FG, borderwidth=1, padding=6)
        st.map("TButton", background=[("active", SEL)])
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("TFrame", background=BG)
        st.configure("TLabelframe", background=BG, bordercolor=LINE)
        st.configure("TLabelframe.Label", background=BG, foreground=MUTED)
        st.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG)
        st.configure("TCombobox", fieldbackground=PANEL, foreground=FG, background=PANEL, arrowcolor=FG)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)

    # ------------------------------------------------------------ konum
    def restore_position(self):
        """Pencere en son nereye sürüklendiyse orada açılır; Riot'u takip etmez, zıplamaz."""
        pos = self.settings.get("pos")
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if pos and 0 <= pos[0] < sw - 80 and 0 <= pos[1] < sh - 80:
            self.geometry(f"+{pos[0]}+{pos[1]}")
        else:
            self.geometry(f"+40+{max(40, (sh - self.winfo_reqheight()) // 2)}")

    def save_position(self):
        try:
            self.settings["pos"] = [self.winfo_x(), self.winfo_y()]
            save_json(SETTINGS_FILE, self.settings)
        except tk.TclError:
            pass

    def close(self):
        self.save_position()
        self.destroy()

    # ------------------------------------------------------------ yardımcı
    def key_of(self, a):
        return a["user"].lower()

    def rank_of(self, a, game):
        return (self.ranks.get(self.key_of(a)) or {}).get(game) or {}

    def selected(self):
        return self.sel_index

    def select(self, i, user=True):
        if not self.accounts:
            self.sel_index = None
            return
        i = max(0, min(i, len(self.accounts) - 1))
        self.sel_index = i
        for k, (row, bar, cells) in enumerate(self.row_widgets):
            bg = SEL if k == i else PANEL
            row.config(bg=bg)
            bar.config(bg=ACC if k == i else PANEL)
            for c in cells:
                c.config(bg=bg)
        if user:
            self.sync_game()
        self.show_detail()

    def move_sel(self, d):
        if self.sel_index is not None:
            self.select(self.sel_index + d)

    def refresh(self, select_last=False):
        cur = self.selected()
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self.row_widgets = []
        for i, a in enumerate(self.accounts):
            row = tk.Frame(self.rows_frame, bg=PANEL)
            row.pack(fill="x")
            if i:
                tk.Frame(self.rows_frame, bg=ROW_LINE, height=1).pack(fill="x", before=row)
            bar = tk.Frame(row, bg=PANEL, width=3)
            bar.pack(side="left", fill="y")
            cells = []
            num = tk.Label(row, text=str(i + 1) if i < 9 else "", bg=PANEL, fg=MUTED,
                           font=("Segoe UI", 8, "bold"), width=2, anchor="e")
            name = tk.Label(row, text=a["name"], bg=PANEL, fg=FG, font=("Segoe UI", 10), anchor="w", width=16)
            num.pack(side="left", padx=(6, 6), pady=7)
            name.pack(side="left")
            cells += [num, name]
            v = self.rank_of(a, "val").get("short") or ("Riot ID ekle" if not a.get("riot_id") else "—")
            l = self.rank_of(a, "lol").get("short") or "—"
            for text, cmap in ((v, VAL_COLORS), (l, LOL_COLORS)):
                color = tier_color(text, cmap)
                dot = tk.Label(row, text="●" if color else "○", bg=PANEL, fg=color or MUTED, font=("Segoe UI", 8))
                lab = tk.Label(row, text=text, bg=PANEL, fg=FG if color else MUTED, font=("Segoe UI", 9),
                               anchor="w", width=17)
                dot.pack(side="left", padx=(4, 3))
                lab.pack(side="left")
                cells += [dot, lab]
            for w in [row, bar] + cells:
                w.bind("<Button-1>", lambda e, k=i: self.select(k))
                w.bind("<Double-Button-1>", lambda e, k=i: (self.select(k), self.choose()))
            self.row_widgets.append((row, bar, cells))
        if self.accounts:
            i = self.settings.get("last", 0) if (select_last or cur is None) else cur
            self.select(i, user=False)

    def show_detail(self):
        i = self.selected()
        if i is None:
            self.detail_id.config(text="")
            self.detail.config(text="")
            return
        a = self.accounts[i]
        v, l = self.rank_of(a, "val"), self.rank_of(a, "lol")
        self.detail_id.config(text=a.get("riot_id") or "Riot ID girilmemiş · ⚙ → Düzenle")
        lines = []
        if v:
            s = f"Valorant: {v.get('current', '—')}"
            if v.get("last"):
                s += f"  ·  son sezon {v['last']}"
            if v.get("peak"):
                s += f"  ·  en yüksek {v['peak']}"
            lines.append(s)
        if l:
            s = f"LoL Solo/Duo: {l.get('solo', '—')}  ·  Esnek: {l.get('flex', '—')}"
            if l.get("prev"):
                s += f"  ·  geçen sezon {l['prev']}"
            ago = int((time.time() - l.get("ts", time.time())) / 3600)
            s += f"  ({'az önce' if ago < 1 else f'{ago} saat önce' if ago < 48 else f'{ago // 24} gün önce'})"
            lines.append(s)
        self.detail.config(text="\n".join(lines))

    def set_status(self, text):
        try:
            self.status.config(text=text)
        except tk.TclError:
            pass

    # ------------------------------------------------------- Valorant rank
    def refresh_val_async(self, force=False):
        key = self.settings.get("henrik_key", "").strip()
        if not key:
            return
        region = self.settings.get("region", "eu")
        todo = []
        for a in self.accounts:
            name, tag = split_riot_id(a.get("riot_id"))
            if not name:
                continue
            old = self.rank_of(a, "val")
            if force or time.time() - old.get("ts", 0) > VAL_REFRESH_SEC:
                todo.append((self.key_of(a), name, tag))
        if not todo:
            return

        def work():
            errors = 0
            for k, name, tag in todo:
                try:
                    summ = summarize_val(henrik_mmr(key, region, name, tag))
                    with self.rank_lock:
                        self.ranks.setdefault(k, {})["val"] = summ
                        save_json(RANK_FILE, self.ranks)
                except urllib.error.HTTPError as e:
                    errors += 1
                    if e.code in (401, 403):
                        self.ui_msg = "Henrik API anahtarı geçersiz (⚙)"
                        break
                    if e.code == 429:
                        time.sleep(3)
                except Exception:
                    errors += 1
                time.sleep(0.4)
            if not errors and not self.ui_msg:
                self.ui_msg = "Valorant rank'ları güncel"
            self.ranks_dirty = True

        threading.Thread(target=work, daemon=True).start()

    def poll_ui(self):
        """Arka plan işlerinden gelen güncellemeleri ana pencereye uygular."""
        if self.ranks_dirty:
            self.ranks_dirty = False
            if self.state() != "withdrawn":
                self.refresh()
                self.show_detail()
        if self.ui_msg:
            self.set_status(self.ui_msg)
            self.ui_msg = ""
        if self.update_info and self.pending is None and self.state() != "withdrawn":
            info, self.update_info = self.update_info, None
            notes = info.get("notes", "")
            if messagebox.askyesno("Gemlock güncellemesi",
                                   f"Yeni sürüm var: {info['version']} (şu an {VERSION})\n\n{notes}\n\nŞimdi güncellensin mi?",
                                   parent=self):
                self.apply_update(info)
                return
        self.after(500, self.poll_ui)

    # ------------------------------------------------------------ güncelleme
    def check_update_async(self, manual=False):
        def work():
            try:
                info = json.loads(gh_raw("version.json").decode("utf-8"))
                if ver_tuple(info.get("version")) > ver_tuple(VERSION):
                    self.update_info = info
                elif manual:
                    self.ui_msg = f"Gemlock güncel · sürüm {VERSION}"
            except Exception:
                if manual:
                    self.ui_msg = "Güncelleme kontrol edilemedi (internet bağlantısını kontrol et)"

        threading.Thread(target=work, daemon=True).start()

    def apply_update(self, info):
        import hashlib
        self.set_status(f"Sürüm {info['version']} indiriliyor…")
        self.update_idletasks()
        try:
            data = gh_raw(info.get("file", "gemlock.pyw"))
            if info.get("sha256") and hashlib.sha256(data).hexdigest() != info["sha256"]:
                raise ValueError("dosya bozuk indi")
            compile(data.decode("utf-8"), "gemlock.pyw", "exec")  # bozuk kod kurulmasın
            os.makedirs(INSTALL_DIR, exist_ok=True)
            with open(INSTALLED_SCRIPT + ".tmp", "wb") as f:
                f.write(data)
            os.replace(INSTALLED_SCRIPT + ".tmp", INSTALLED_SCRIPT)
        except Exception as e:
            messagebox.showerror("Güncelleme başarısız", f"Güncelleme kurulamadı: {e}\nEski sürüm çalışmaya devam ediyor.",
                                 parent=self)
            self.after(500, self.poll_ui)
            return
        self.save_position()
        kernel32.CloseHandle(_mutex)  # yeni sürüm açılabilsin
        subprocess.Popen([pythonw_exe(), INSTALLED_SCRIPT], close_fds=True, cwd=INSTALL_DIR)
        self.destroy()

    # ------------------------------------------------------------ LoL rank
    def refresh_lol_from_client(self, logged_user_key):
        """LoL client açılınca tüm hesapların rank'ını okur, sonra biter."""
        league = find_league_dir()
        if not league:
            self.lcu_done = True
            return
        lcu = LCU(league)
        deadline = time.time() + 180
        me = None
        while time.time() < deadline:
            if lcu.connect():
                try:
                    me = lcu.get("/lol-summoner/v1/current-summoner")
                    if me.get("puuid"):
                        break
                except Exception:
                    pass
            time.sleep(2)
        if not me or not me.get("puuid"):
            self.lcu_done = True
            return
        time.sleep(2)  # rank servisinin hazır olması için
        with self.rank_lock:
            # giriş yapılan hesabın Riot ID'sini kendiliğinden kaydet
            for a in self.accounts:
                if self.key_of(a) == logged_user_key and not a.get("riot_id") and me.get("gameName"):
                    a["riot_id"] = f"{me['gameName']}#{me.get('tagLine', '')}"
                    save_json(DATA_FILE, self.accounts)
        for a in list(self.accounts):
            try:
                if self.key_of(a) == logged_user_key:
                    puuid = me["puuid"]
                else:
                    name, tag = split_riot_id(a.get("riot_id"))
                    if not name:
                        continue
                    puuid = lcu.puuid_of(name, tag)
                if not puuid:
                    continue
                summ = summarize_lol(lcu.get(f"/lol-ranked/v1/ranked-stats/{puuid}"))
                with self.rank_lock:
                    self.ranks.setdefault(self.key_of(a), {})["lol"] = summ
                    save_json(RANK_FILE, self.ranks)
            except Exception:
                continue
        self.lcu_done = True

    # ------------------------------------------------------------- Riot
    def start_riot(self):
        if user32.FindWindowW(None, "Riot Client"):
            self.set_status("Riot Client açık · hesabını seç")
            return
        exe = find_riot_exe()
        if exe:
            product = self.game.get()
            args = [exe] + ([f"--launch-product={product}", "--launch-patchline=live"] if product else [])
            subprocess.Popen(args, close_fds=True)
            self.riot_product = product
            self.set_status("Riot Client açılıyor… hesabı şimdiden seçebilirsin")
        else:
            self.set_status("Riot Client bulunamadı, kendin aç")

    def tick(self):
        hwnd = riot_hwnd()
        if hwnd:
            if self.riot_seen_at is None:
                self.riot_seen_at = time.time()
            if self.pending is not None and time.time() - self.riot_seen_at > 4:
                self.do_login(hwnd, self.pending)
                return
        else:
            self.riot_seen_at = None
        self.after(300, self.tick)

    # ------------------------------------------------------------ seçim
    def update_blitz_box(self):
        if self.game.get() == "league_of_legends":
            self.blitz_box.pack(anchor="w", padx=10, before=self.status_row)
        else:
            self.blitz_box.pack_forget()

    def blitz_toggled(self):
        self.settings["blitz"] = self.blitz.get()
        if self.blitz.get() and not find_blitz_exe(self.settings.get("blitz_path")):
            path = filedialog.askopenfilename(parent=self, title="Blitz.exe dosyasını seç",
                                              filetypes=[("Blitz", "Blitz.exe"), ("Programlar", "*.exe")])
            if path:
                self.settings["blitz_path"] = path
            else:
                self.blitz.set(False)
                self.settings["blitz"] = False
        save_json(SETTINGS_FILE, self.settings)

    def sync_game(self):
        """Sadece başka bir hesaba geçilince o hesabın son oyununu seç; butonlara dokunmaz."""
        i = self.selected()
        if i == self._last_sel:
            return
        self._last_sel = i
        if i is not None and "game" in self.accounts[i] and self.game.get() != self.accounts[i]["game"]:
            self.game.set(self.accounts[i]["game"])

    def quick(self, i):
        if i < len(self.accounts):
            self.select(i)
            self.choose()

    # ------------------------------------------------------------ oyun
    def paint_games(self):
        cur = self.game.get()
        for code, b in self.game_btns.items():
            b.config(bg=ACC if code == cur else SEG_BG, fg=ON_ACC if code == cur else MUTED)

    def set_game(self, code):
        if self.game.get() == code:
            return
        self.game.set(code)
        self.tell_riot_product()

    def tell_riot_product(self):
        """Giriş yapılmadan önce Riot Client'ı seçilen oyuna çevirir (giriş ekranı o oyunun teması olur)."""
        product = self.game.get()
        if not product or self.logged_in or product == self.riot_product:
            return
        exe = find_riot_exe()
        if exe and user32.FindWindowW(None, "Riot Client"):
            subprocess.Popen([exe, f"--launch-product={product}", "--launch-patchline=live"], close_fds=True)
            self.riot_product = product

    def choose(self):
        i = self.selected()
        if i is None:
            return
        self.pending = i
        self.settings["last"] = i
        self.settings["game"] = self.game.get()
        save_json(SETTINGS_FILE, self.settings)
        self.accounts[i]["game"] = self.game.get()
        save_json(DATA_FILE, self.accounts)
        if not riot_hwnd() or (self.riot_seen_at and time.time() - self.riot_seen_at <= 4):
            self.set_status(f"Riot hazır olunca '{self.accounts[i]['name']}' girilecek…")

    # ------------------------------------------------------------ giriş
    def do_login(self, hwnd, i):
        a = self.accounts[i]
        try:
            pw = decrypt(a["pw"])
        except OSError as e:
            messagebox.showerror("Hata", str(e), parent=self)
            self.pending = None
            self.after(300, self.tick)
            return

        self.save_position()
        self.logged_in = True
        self.withdraw()
        time.sleep(0.25)
        for _ in range(4):  # Riot'u öne getir, gerçekten öne geldiğinden emin ol
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)
            bring_to_front(hwnd)
            time.sleep(0.35)
            if is_front(hwnd):
                break

        ok = is_front(hwnd)
        if ok:
            select_all()
            ok = type_text_safe(a["user"], hwnd)
        if ok and is_front(hwnd):
            press(VK_TAB)
            time.sleep(0.1)
            ok = is_front(hwnd)
            if ok:
                select_all()
                ok = type_text_safe(pw, hwnd)
        if ok and is_front(hwnd):
            time.sleep(0.15)
            press(VK_RETURN)
        pw = None
        if not ok:
            # güvenlik: Riot önde değilse hiçbir şey göndermeden geri dön
            self.logged_in = False
            self.pending = None
            self.deiconify()
            messagebox.showwarning(
                "Giriş durduruldu",
                "Riot Client öne gelmediği için güvenlik amacıyla şifre yazılmadı.\n"
                "Riot penceresine bir kez tıklayıp tekrar 'Giriş yap'a bas.", parent=self)
            self.after(300, self.tick)
            return

        product = self.game.get()
        exe = find_riot_exe()
        if product and exe:
            time.sleep(1.5)
            subprocess.Popen([exe, f"--launch-product={product}", "--launch-patchline=live"], close_fds=True)

        if product == "league_of_legends":
            self.lcu_done = False
            threading.Thread(target=self.refresh_lol_from_client, args=(self.key_of(a),), daemon=True).start()
            if self.blitz.get():
                blitz = find_blitz_exe(self.settings.get("blitz_path"))
                if blitz:
                    subprocess.Popen([blitz], close_fds=True, cwd=os.path.dirname(blitz))
                    self.blitz_done = False
                    self.blitz_tries = 0
                    self.after(1500, lambda: self.focus_blitz(os.path.basename(blitz).lower()))
        self.finish_started = time.time()
        self.after(500, self.finish_when_done)

    def focus_blitz(self, exe_name):
        hwnd = find_window_of(exe_name)
        if hwnd:
            bring_to_front(hwnd)
            if user32.IsWindowVisible(hwnd) and self.blitz_tries >= 3:
                self.blitz_done = True
                return
        self.blitz_tries += 1
        if self.blitz_tries > 25:
            self.blitz_done = True
            return
        self.after(1000, lambda: self.focus_blitz(exe_name))

    def finish_when_done(self):
        # LoL rank'ları okunup Blitz öne gelince (en fazla ~3,5 dk) program tamamen kapanır
        if (self.lcu_done and self.blitz_done) or time.time() - self.finish_started > 210:
            self.destroy()
            return
        self.after(500, self.finish_when_done)

    # ----------------------------------------------------------- hesaplar
    def dialog(self, parent, title, name="", user="", pw="", rid=""):
        win = tk.Toplevel(parent)
        win.title(title)
        win.configure(bg=BG)
        win.after(50, lambda: dark_titlebar(win))
        win.attributes("-topmost", True)
        win.grab_set()
        win.resizable(False, False)
        rows = [("Etiket (örn. Main)", name, False), ("Kullanıcı adı", user, False),
                ("Şifre", pw, True), ("Riot ID (isim#etiket)", rid, False)]
        vars_ = []
        for r, (label, val, secret) in enumerate(rows):
            ttk.Label(win, text=label).grid(row=r, column=0, padx=8, pady=6, sticky="w")
            v = tk.StringVar(value=val)
            e = ttk.Entry(win, textvariable=v, width=28, show="*" if secret else "")
            e.grid(row=r, column=1, padx=8)
            if r == 0:
                e.focus_set()
            vars_.append(v)
        ttk.Label(win, text="Riot ID oyunda görünen isim, örn. Oyuncu#TR1. Rank için gerekli; "
                            "boş bırakırsan LoL'e ilk girişte kendiliğinden dolar.",
                  wraplength=330, foreground=MUTED).grid(row=4, column=0, columnspan=2, padx=8, sticky="w")
        result = {}

        def ok(_=None):
            result["v"] = [vars_[0].get().strip(), vars_[1].get().strip(), vars_[2].get(), vars_[3].get().strip()]
            win.destroy()

        win.bind("<Return>", ok)
        ttk.Button(win, text="Kaydet", command=ok).grid(row=5, column=0, columnspan=2, pady=10)
        parent.wait_window(win)
        return result.get("v")

    def manage(self):
        win = tk.Toplevel(self)
        win.title("Gemlock · Ayarlar ve Hesaplar")
        win.configure(bg=BG)
        win.after(50, lambda: dark_titlebar(win))
        win.attributes("-topmost", True)
        win.geometry("420x620")
        lb = tk.Listbox(win, font=("Segoe UI", 11), activestyle="none", height=8, bg=PANEL, fg=FG,
                        selectbackground=SEL, selectforeground=FG, highlightthickness=1,
                        highlightbackground=LINE, borderwidth=0)
        lb.pack(fill="both", expand=True, padx=10, pady=10)

        def fill():
            lb.delete(0, "end")
            for a in self.accounts:
                lb.insert("end", f"{a['name']}   ({a.get('riot_id') or a['user']})")
            self.refresh()
            self.show_detail()

        def sel():
            s = lb.curselection()
            return s[0] if s else None

        def add():
            r = self.dialog(win, "Hesap ekle")
            if r and r[1] and r[2]:
                self.accounts.append({"name": r[0] or r[1], "user": r[1], "pw": encrypt(r[2]), "riot_id": r[3]})
                save_json(DATA_FILE, self.accounts)
                fill()
                self.refresh_val_async(force=True)

        def edit():
            i = sel()
            if i is None:
                return
            a = self.accounts[i]
            r = self.dialog(win, "Hesabı düzenle", a["name"], a["user"], decrypt(a["pw"]), a.get("riot_id", ""))
            if r and r[1] and r[2]:
                self.accounts[i] = {**a, "name": r[0] or r[1], "user": r[1], "pw": encrypt(r[2]), "riot_id": r[3]}
                save_json(DATA_FILE, self.accounts)
                fill()
                self.refresh_val_async(force=True)

        def remove():
            i = sel()
            if i is not None and messagebox.askyesno("Sil", f"'{self.accounts[i]['name']}' silinsin mi?", parent=win):
                del self.accounts[i]
                save_json(DATA_FILE, self.accounts)
                fill()

        def move(d):
            i = sel()
            if i is None or not (0 <= i + d < len(self.accounts)):
                return
            self.accounts[i], self.accounts[i + d] = self.accounts[i + d], self.accounts[i]
            save_json(DATA_FILE, self.accounts)
            fill()
            lb.selection_set(i + d)

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        for text, cmd in (("Ekle", add), ("Düzenle", edit), ("Sil", remove),
                          ("▲", lambda: move(-1)), ("▼", lambda: move(1))):
            ttk.Button(bar, text=text, command=cmd, width=7 if len(text) > 1 else 3).pack(side="left", padx=2)

        box = ttk.LabelFrame(win, text="Valorant rank (HenrikDev API)")
        box.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(box, text="API anahtarı").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        keyv = tk.StringVar(value=self.settings.get("henrik_key", ""))
        ttk.Entry(box, textvariable=keyv, width=30, show="•").grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(box, text="Bölge").grid(row=1, column=0, padx=6, pady=4, sticky="w")
        regv = tk.StringVar(value=self.settings.get("region", "eu"))
        ttk.Combobox(box, textvariable=regv, values=REGIONS, state="readonly", width=8).grid(
            row=1, column=1, padx=6, pady=4, sticky="w")
        ttk.Label(box, text="Türkiye sunucusu için 'eu' seç.", foreground=MUTED).grid(
            row=2, column=0, columnspan=2, padx=6, sticky="w")

        def save_key():
            self.settings["henrik_key"] = keyv.get().strip()
            self.settings["region"] = regv.get()
            save_json(SETTINGS_FILE, self.settings)
            self.set_status("Valorant rank'ları yenileniyor…")
            self.refresh_val_async(force=True)

        ttk.Button(box, text="Kaydet ve rank'ları yenile", command=save_key).grid(
            row=3, column=0, columnspan=2, padx=6, pady=6, sticky="we")

        up = ttk.LabelFrame(win, text="Güncelleme")
        up.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(up, text=f"Sürüm {VERSION} · her açılışta yeni sürüm otomatik kontrol edilir.",
                  foreground=MUTED).grid(row=0, column=0, padx=6, pady=4, sticky="w")

        def check_now():
            self.set_status("Güncelleme kontrol ediliyor…")
            self.check_update_async(manual=True)

        ttk.Button(up, text="Şimdi kontrol et", command=check_now).grid(row=1, column=0, padx=6, pady=6, sticky="we")
        up.columnconfigure(0, weight=1)

        def shortcut():
            if create_desktop_shortcut(INSTALLED_SCRIPT if os.path.exists(INSTALLED_SCRIPT) else None):
                messagebox.showinfo("Tamam", "Masaüstüne 'Gemlock' kısayolu oluşturuldu.", parent=win)
            else:
                messagebox.showerror("Hata", "Kısayol oluşturulamadı.", parent=win)

        ttk.Button(win, text="Masaüstüne simgeli kısayol oluştur", command=shortcut).pack(fill="x", padx=10, pady=(0, 10))
        fill()


ICON_B64 = "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"

if __name__ == "__main__":
    JUST_INSTALLED = self_install()
    App().mainloop()
