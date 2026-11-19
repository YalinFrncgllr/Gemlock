"""
Gemlock 2.2.1 — Riot hesap yöneticisi
Kurulum: "Gemlock Setup.pyw" dosyasına çift tıkla (kendini kurar, masaüstüne kısayol koyar).
- Çift tıkla: Riot Client kapalıysa onu da açar, hesap listesi gelir
- Oyunu seç (Valorant / LoL / sadece Client), hesabı seç (Enter / çift tık / 1-9)
  -> giriş yapar -> oyunu başlatır -> işi bitince TAMAMEN kapanır
- Rank'lar: Valorant (Gemlock rank sunucusu üzerinden, anahtar gerekmez) ve LoL (açık olan LoL client'tan)
- LoL seçiliyse Blitz'i de açıp öne getirebilir
- Tüm veriler (hesap adları, kullanıcı adları, şifreler, Riot ID'ler, rank'lar, ayarlar)
  sadece bu bilgisayarda, %LOCALAPPDATA%/Gemlock/data içinde, Windows DPAPI ile şifreli durur.
  Program bu verileri hiçbir yere göndermez; kodu indiren biri boş bir liste görür.
"""
import base64
import math
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
import tkinter.font as tkfont

APPDATA = os.environ.get("APPDATA", ".")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", APPDATA)
# Veriler "Local" klasöründe: ağ/roaming profiliyle başka bilgisayara senkronlanmaz
DATA_DIR = os.path.join(LOCALAPPDATA, "Gemlock", "data")
DATA_FILE = os.path.join(DATA_DIR, "accounts.dat")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.dat")
RANK_FILE = os.path.join(DATA_DIR, "ranks.dat")
OLD_FILES = {  # eski sürümlerin açık (şifresiz) JSON dosyaları -> taşınıp silinir
    os.path.join(APPDATA, "riot_hesaplar.json"): DATA_FILE,
    os.path.join(APPDATA, "riot_hesap_ayarlar.json"): SETTINGS_FILE,
    os.path.join(APPDATA, "riot_hesap_rank.json"): RANK_FILE,
}
ICON_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APPDATA), "Gemlock")
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APPDATA), "Gemlock")
INSTALLED_SCRIPT = os.path.join(INSTALL_DIR, "gemlock.pyw")
CREATE_NO_WINDOW = 0x08000000
VAL_IDLE_SEC = 6 * 3600    # oynanmayan hesaplar 6 saatte bir yenilenir
RANK_API = "https://gemlock-api.vercel.app/api/rank"  # anahtar sunucuda gizli; kullanıcı hiçbir şey kurmaz
VERSION = "2.2.1"
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
        raise OSError(t("dpapi"))
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


VAL_PROCS = {"valorant.exe", "valorant-win64-shipping.exe"}   # Valorant oyunu
LOL_CLIENT_PROCS = {"leagueclient.exe", "leagueclientux.exe"}    # LoL client'ı (lobi)
LOL_MATCH_PROCS = {"league of legends.exe"}                      # LoL maçı
GAME_PROCESSES = VAL_PROCS | LOL_CLIENT_PROCS | LOL_MATCH_PROCS


def game_running(names=GAME_PROCESSES):
    """Verilen oyun süreçlerinden biri açık mı?"""
    try:
        psapi = ctypes.WinDLL("psapi")
        arr = (wintypes.DWORD * 4096)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(needed)):
            return False
        for i in range(needed.value // ctypes.sizeof(wintypes.DWORD)):
            if arr[i] and _process_name(arr[i]) in names:
                return True
    except Exception:
        pass
    return False


def bring_to_front(hwnd):
    user32.ShowWindow(hwnd, 9)
    user32.ShowWindow(hwnd, 5)
    _send(vk=VK_MENU)
    _send(vk=VK_MENU, flags=KEYEVENTF_KEYUP)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)


# ================================================== Dosyalar / yollar ====
def load_json(path, default):
    """.dat dosyaları DPAPI ile şifreli okunur; diğerleri (Riot'un kendi ayar dosyaları) düz okunur."""
    try:
        if path.endswith(".dat"):
            with open(path, "rb") as f:
                raw = f.read()
            try:
                return json.loads(_dpapi(crypt32.CryptUnprotectData, raw).decode("utf-8"))
            except (OSError, ValueError):
                # çözülemedi (başka Windows kullanıcısı / bozuk): üzerine yazmamak için kenara al
                os.replace(path, path + f".bozuk-{int(time.time())}")
                return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    if path.endswith(".dat"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        blob = _dpapi(crypt32.CryptProtectData, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        with open(tmp, "wb") as f:
            f.write(blob)
    else:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def wipe_file(path):
    """Dosyanın içini sıfırlarla ezip siler (eski açık verilerin diskte kalmaması için)."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.write(b"\0" * size)
            f.flush()
            os.fsync(f.fileno())
        os.remove(path)
    except OSError:
        pass


def migrate_old_data():
    """Eski sürümlerin açık JSON dosyalarını şifreli .dat dosyalarına taşır ve eskileri siler."""
    for old, new in OLD_FILES.items():
        if not os.path.exists(old):
            continue
        if not os.path.exists(new):
            data = load_json(old, None)
            if data is None:
                continue
            save_json(new, data)
            if load_json(new, None) != data:  # doğrulanmadan eskiyi silme
                continue
        wipe_file(old)
        wipe_file(old + ".tmp")


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
    """Açık GitHub deposundan bir dosyanın ham içeriğini indirir.
    Önce GitHub API (gecikmesiz), olmazsa raw adresi (birkaç dakika önbellekli olabilir)."""
    q = urllib.parse.quote(path)
    tries = [
        (f"https://api.github.com/repos/{UPDATE_REPO}/contents/{q}?ref=main",
         {"Accept": "application/vnd.github.raw+json", "User-Agent": "Gemlock"}),
        (f"https://raw.githubusercontent.com/{UPDATE_REPO}/main/{q}?t={int(time.time())}",
         {"User-Agent": "Gemlock", "Cache-Control": "no-cache"}),
    ]
    last = None
    for url, headers in tries:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                return r.read()
        except Exception as e:
            last = e
    raise last


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
LANG = "en"  # App açılınca ayarlardan okunur ("en" varsayılan)

STR = {
    "en": {
        "client_only": "Client only", "blitz": "Also open Blitz", "h_account": "ACCOUNT",
        "login": "Log in", "keys": "1-9 quick login · Enter log in · Right-click an account to edit · Esc close",
        "first_login": "Fills in on first login", "rid_pending": "Riot ID is added automatically on the first login with this account",
        "unranked": "Unranked", "last": "last", "peak": "peak", "last_season": "last season", "prev_season": "last season",
        "flex": "Flex", "tft": "TFT", "just_now": "just now", "hours_ago": "{n}h ago", "days_ago": "{n}d ago",
        "rid_nf": "No Valorant data",
        "srv_busy": "Rank server is busy; the rest will update next time", "val_fail": "Some Valorant ranks could not be loaded (check your internet)",
        "val_ok": "Valorant ranks are up to date",
        "upd_title": "Gemlock update", "upd_q": "A new version is available: {new} (you have {cur})\n\n{notes}\n\nUpdate now?",
        "upd_ok": "Gemlock is up to date · version {v}", "upd_fail_check": "Could not check for updates (check your internet)",
        "upd_dl": "Downloading version {v}…", "upd_fail_t": "Update failed",
        "upd_fail": "The update could not be installed: {e}\nThe current version keeps running.",
        "rc_open": "Riot Client is open · pick an account", "rc_opening": "Opening Riot Client… you can pick an account already",
        "rc_missing": "Riot Client not found, please open it yourself", "blitz_pick": "Select Blitz.exe",
        "pending": "'{name}' will log in as soon as Riot is ready…", "err": "Error",
        "stop_t": "Login stopped",
        "stop": "Riot Client did not come to the front, so the password was not typed for safety.\nClick the Riot window once and press 'Log in' again.",
        "f_label": "Label (e.g. Main)", "f_user": "Username", "f_pw": "Password", "f_rid": "Riot ID (name#tag)",
        "rid_hint": "Optional. Leave it empty and it fills in automatically on the first login through Gemlock (e.g. Player#TR1).",
        "save": "Save", "manage_t": "Gemlock · Settings & accounts", "add_t": "Add account", "edit_t": "Edit account",
        "del_t": "Delete", "del_q": "Delete '{name}'?", "b_add": "Add", "b_edit": "Edit", "b_del": "Delete",
        "val_box": "Valorant rank", "val_nokey": "No key needed; ranks come from the Gemlock server.", "region": "Region",
        "region_hint": "Pick 'eu' for Turkey servers.", "val_refreshing": "Refreshing Valorant ranks…",
        "val_save": "Save and refresh ranks now", "upd_box": "Updates",
        "upd_info": "Version {v} · new versions are checked on every start.", "upd_checking": "Checking for updates…",
        "upd_now": "Check now", "sc_ok_t": "Done", "sc_ok": "A 'Gemlock' shortcut was created on your desktop.",
        "sc_fail": "Could not create the shortcut.", "sc_btn": "Create desktop shortcut",
        "dpapi": "Could not decrypt (it may belong to another Windows user)",
        "add_account": "+  Add account", "logging_in": "Logging in…", "f_region": "Region",
        "copy": "Copy", "copied": "Riot ID copied", "last_played": "Last played through Gemlock: {ago}",
        "never_played": "Not played through Gemlock yet", "since_last": "since last time", "rank_up": "ranked up",
        "rank_down": "ranked down",
        "auto": "Auto", "pin": "Pin to top", "unpin": "Unpin", "sort_game": "Sort by the selected game",
        "sort_hint": "Accounts with ranks in the selected game come first; pinned accounts always stay on top.",
        "sec_list": "Account list", "region_auto_hint": "Gemlock detects each account's region on its first login.", "minimize": "Minimize", "close": "Close", "show": "Show", "hide": "Hide", "settings": "Settings", "back": "←  Back", "sec_general": "General",
        "sec_val": "Valorant ranks", "refresh_ranks": "Refresh ranks now", "sec_app": "App", "sec_data": "Your data",
        "data_info": "Accounts and passwords are stored only on this PC, encrypted by Windows. Nothing is uploaded.",
        "open_folder": "Open data folder", "move_up": "Move up", "move_down": "Move down", "cancel": "Cancel",
        "version": "Version {v}", "blitz_setting": "Open Blitz together with LoL / TFT",
        "switch_t": "Switch account", "switch_q": "Riot Client is signed in as {cur}.\nSign out and log in as {new}?",
        "game_open": "A game is running. Close it first, then switch accounts.",
        "switching": "Signing out and restarting Riot Client…", "same_acc": "Already signed in · starting the game",
        "decay": "Decay risk: not played through Gemlock for {n} days (Diamond+ can stay inactive for at most 28 days)",
    },
    "tr": {
        "client_only": "Sadece Client", "blitz": "Blitz'i de aç", "h_account": "HESAP",
        "login": "Giriş yap", "keys": "1-9 hızlı giriş · Enter giriş · Düzenlemek için hesaba sağ tıkla · Esc kapat",
        "first_login": "İlk girişte gelir", "rid_pending": "Riot ID bu hesapla ilk girişte kendiliğinden eklenecek",
        "unranked": "Rank yok", "last": "son", "peak": "en yüksek", "last_season": "son sezon", "prev_season": "geçen sezon",
        "flex": "Esnek", "tft": "TFT", "just_now": "az önce", "hours_ago": "{n} saat önce", "days_ago": "{n} gün önce",
        "rid_nf": "Valorant verisi yok",
        "srv_busy": "Rank sunucusu yoğun, kalan rank'lar sonraki açılışta güncellenecek",
        "val_fail": "Bazı Valorant rank'ları alınamadı (internet bağlantısını kontrol et)", "val_ok": "Valorant rank'ları güncel",
        "upd_title": "Gemlock güncellemesi", "upd_q": "Yeni sürüm var: {new} (şu an {cur})\n\n{notes}\n\nŞimdi güncellensin mi?",
        "upd_ok": "Gemlock güncel · sürüm {v}", "upd_fail_check": "Güncelleme kontrol edilemedi (internet bağlantısını kontrol et)",
        "upd_dl": "Sürüm {v} indiriliyor…", "upd_fail_t": "Güncelleme başarısız",
        "upd_fail": "Güncelleme kurulamadı: {e}\nEski sürüm çalışmaya devam ediyor.",
        "rc_open": "Riot Client açık · hesabını seç", "rc_opening": "Riot Client açılıyor… hesabı şimdiden seçebilirsin",
        "rc_missing": "Riot Client bulunamadı, kendin aç", "blitz_pick": "Blitz.exe dosyasını seç",
        "pending": "Riot hazır olunca '{name}' girilecek…", "err": "Hata",
        "stop_t": "Giriş durduruldu",
        "stop": "Riot Client öne gelmediği için güvenlik amacıyla şifre yazılmadı.\nRiot penceresine bir kez tıklayıp tekrar 'Giriş yap'a bas.",
        "f_label": "Etiket (örn. Main)", "f_user": "Kullanıcı adı", "f_pw": "Şifre", "f_rid": "Riot ID (isim#etiket)",
        "rid_hint": "İsteğe bağlı. Boş bırakırsan bu hesapla Gemlock üzerinden ilk girişte kendiliğinden dolar (örn. Oyuncu#TR1).",
        "save": "Kaydet", "manage_t": "Gemlock · Ayarlar ve Hesaplar", "add_t": "Hesap ekle", "edit_t": "Hesabı düzenle",
        "del_t": "Sil", "del_q": "'{name}' silinsin mi?", "b_add": "Ekle", "b_edit": "Düzenle", "b_del": "Sil",
        "val_box": "Valorant rank", "val_nokey": "Anahtar gerekmez; rank'lar Gemlock sunucusundan gelir.", "region": "Bölge",
        "region_hint": "Türkiye sunucusu için 'eu' seç.", "val_refreshing": "Valorant rank'ları yenileniyor…",
        "val_save": "Kaydet ve rank'ları şimdi yenile", "upd_box": "Güncelleme",
        "upd_info": "Sürüm {v} · her açılışta yeni sürüm otomatik kontrol edilir.", "upd_checking": "Güncelleme kontrol ediliyor…",
        "upd_now": "Şimdi kontrol et", "sc_ok_t": "Tamam", "sc_ok": "Masaüstüne 'Gemlock' kısayolu oluşturuldu.",
        "sc_fail": "Kısayol oluşturulamadı.", "sc_btn": "Masaüstüne simgeli kısayol oluştur",
        "dpapi": "Şifre çözülemedi (başka bir Windows kullanıcısına ait olabilir)",
        "add_account": "+  Hesap ekle", "logging_in": "Giriş yapılıyor…", "f_region": "Bölge",
        "copy": "Kopyala", "copied": "Riot ID kopyalandı", "last_played": "Gemlock ile son oynama: {ago}",
        "never_played": "Henüz Gemlock ile oynanmadı", "since_last": "son seferden beri", "rank_up": "rank atladı",
        "rank_down": "rank düştü",
        "auto": "Otomatik", "pin": "En üste sabitle", "unpin": "Sabitlemeyi kaldır", "sort_game": "Seçili oyuna göre sırala",
        "sort_hint": "Seçili oyunda rank'ı olan hesaplar üstte; sabitlenen hesaplar her zaman en üstte.",
        "sec_list": "Hesap listesi", "region_auto_hint": "Gemlock her hesabın bölgesini ilk girişte kendisi bulur.", "minimize": "Küçült", "close": "Kapat", "show": "Göster", "hide": "Gizle", "settings": "Ayarlar", "back": "←  Geri", "sec_general": "Genel",
        "sec_val": "Valorant rank'ları", "refresh_ranks": "Rank'ları şimdi yenile", "sec_app": "Uygulama",
        "sec_data": "Verilerin",
        "data_info": "Hesapların ve şifrelerin sadece bu bilgisayarda, Windows şifrelemesiyle saklanıyor. Hiçbir yere yüklenmez.",
        "open_folder": "Veri klasörünü aç", "move_up": "Yukarı taşı", "move_down": "Aşağı taşı", "cancel": "Vazgeç",
        "version": "Sürüm {v}", "blitz_setting": "LoL / TFT ile birlikte Blitz'i aç",
        "switch_t": "Hesap değiştir", "switch_q": "Riot Client'ta {cur} oturumu açık.\nÇıkış yapılıp {new} ile girilsin mi?",
        "game_open": "Bir oyun açık. Önce oyunu kapat, sonra hesap değiştir.",
        "switching": "Oturum kapatılıyor, Riot Client yeniden açılıyor…", "same_acc": "Zaten bu hesapla girilmiş · oyun başlatılıyor",
        "decay": "Düşüş riski: {n} gündür Gemlock ile oynanmadı (Diamond ve üstü en fazla 28 gün oynamadan durabilir)",
    },
}


def t(key, **kw):
    txt = STR.get(LANG, STR["en"]).get(key) or STR["en"].get(key, key)
    return txt.format(**kw) if kw else txt


# Rank'lar dilden bağımsız saklanır (İngilizce kademe adları), ekranda seçili dile çevrilir.
VAL_TIERS_TR = {"Iron": "Demir", "Bronze": "Bronz", "Silver": "Gümüş", "Gold": "Altın", "Platinum": "Platin",
                "Diamond": "Elmas", "Ascendant": "Yücelik", "Immortal": "Ölümsüz", "Radiant": "Radyant"}
LOL_TIERS = {"IRON": ("Iron", "Demir"), "BRONZE": ("Bronze", "Bronz"), "SILVER": ("Silver", "Gümüş"),
             "GOLD": ("Gold", "Altın"), "PLATINUM": ("Platinum", "Platin"), "EMERALD": ("Emerald", "Zümrüt"),
             "DIAMOND": ("Diamond", "Elmas"), "MASTER": ("Master", "Ustalık"),
             "GRANDMASTER": ("Grandmaster", "Büyük Ustalık"), "CHALLENGER": ("Challenger", "Şampiyonluk")}
ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4"}
VAL_COLORS = {"Iron": "#8a8a8a", "Bronze": "#b07a3c", "Silver": "#b9c2c9", "Gold": "#e5b94e", "Platinum": "#49b3c0",
              "Diamond": "#c48ef5", "Ascendant": "#3fbf7f", "Immortal": "#e0466a", "Radiant": "#f5e07a"}
LOL_COLORS = {"IRON": "#7a7470", "BRONZE": "#9a6240", "SILVER": "#9aa4ad", "GOLD": "#d4a94c", "PLATINUM": "#4fa3a5",
              "EMERALD": "#2fbf71", "DIAMOND": "#6e8ff0", "MASTER": "#b45be0", "GRANDMASTER": "#e04b4b",
              "CHALLENGER": "#f2c94c"}
# eski sürümlerin Türkçe kaydettiği rank'ların rengi için
_LEGACY_COLOR = {"Demir": "#8a8a8a", "Bronz": "#b07a3c", "Gümüş": "#b9c2c9", "Altın": "#e5b94e", "Platin": "#49b3c0",
                 "Elmas": "#c48ef5", "Yücelik": "#3fbf7f", "Ölümsüz": "#e0466a", "Radyant": "#f5e07a",
                 "Zümrüt": "#2fbf71", "Ustalık": "#b45be0", "Büyük": "#e04b4b", "Şampiyonluk": "#f2c94c"}


def val_tier_name(name):
    if not name:
        return ""
    if LANG != "tr":
        return name
    parts = name.split()
    return " ".join([VAL_TIERS_TR.get(parts[0], parts[0])] + parts[1:])


def lol_tier_name(tier, div=""):
    tier = (tier or "").upper()
    if not tier or tier in ("NONE", "UNRANKED"):
        return ""
    en, tr = LOL_TIERS.get(tier, (tier.title(), tier.title()))
    name = tr if LANG == "tr" else en
    if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return name
    return f"{name} {ROMAN.get(div or '', '')}".strip()


# Rank sembolleri: Riot'un orijinal görselleri, hayran araçlarının kullandığı kaynaklardan bir kez indirilip
# %LOCALAPPDATA%/Gemlock/rank-icons içinde saklanır.
RANK_ICON_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Gemlock", "rank-icons")
VAL_ICON_URL = "https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/{n}/smallicon.png"
LOL_ICON_URLS = ["https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-shared-components/global/default/{t}.png",
                 "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-mini-crests/{t}.png"]
_VAL_BASE = {"Iron": 3, "Bronze": 6, "Silver": 9, "Gold": 12, "Platinum": 15, "Diamond": 18,
             "Ascendant": 21, "Immortal": 24}


def val_tier_num(name):
    """'Diamond 2' -> 19 (Valorant kademe numarası), 'Radiant' -> 27."""
    parts = (name or "").split()
    if not parts:
        return None
    if parts[0] == "Radiant":
        return 27
    base = _VAL_BASE.get(parts[0])
    if base is None:
        return None
    try:
        return base + int(parts[1]) - 1 if len(parts) > 1 else base
    except ValueError:
        return base


def val_icon_key(v):
    if not v or "k" not in v:
        return None
    for f in ("cur", "last", "peak"):
        if v.get(f):
            n = val_tier_num(v[f])
            return f"val_{n}" if n else None
    return None


def lol_icon_key(l):
    if not l or "k" not in l:
        return None
    q = l.get("solo") or l.get("prev")
    return f"lol_{q['t'].lower()}" if q else None


def icon_path(key):
    return os.path.join(RANK_ICON_DIR, key + ".png")


def download_icon(key):
    """Sembolü bir kez indirir; zaten varsa dokunmaz."""
    path = icon_path(key)
    if os.path.exists(path):
        return True
    if key.startswith("val_"):
        urls = [VAL_ICON_URL.format(n=key[4:])]
    else:
        urls = [u.format(t=key[4:]) for u in LOL_ICON_URLS]
    os.makedirs(RANK_ICON_DIR, exist_ok=True)
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Gemlock"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                continue
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
            return True
        except Exception:
            continue
    return False


def ago_text(ts):
    h = int((time.time() - ts) / 3600)
    return t("just_now") if h < 1 else t("hours_ago", n=h) if h < 48 else t("days_ago", n=h // 24)


def split_riot_id(rid):
    if not rid or "#" not in rid:
        return None, None
    name, tag = rid.rsplit("#", 1)
    return name.strip(), tag.strip()


def summarize_val(data):
    """Henrik v3 mmr cevabını dilden bağımsız, küçük bir kayda çevirir."""
    cur = data.get("current") or {}
    tier = cur.get("tier") or {}
    rec = {"k": 2, "ts": time.time(), "cur": "", "rr": cur.get("rr", 0)}
    if (tier.get("id") or 0) > 2 and (tier.get("name") or "").lower() != "unrated":
        rec["cur"] = tier.get("name")
    for sea in reversed(data.get("seasonal") or []):
        et = sea.get("end_tier") or {}
        if (et.get("id") or 0) > 2:
            rec["last"], rec["last_s"] = et.get("name"), (sea.get("season") or {}).get("short", "")
            break
    peak = data.get("peak") or {}
    if (peak.get("tier") or {}).get("name"):
        rec["peak"], rec["peak_s"] = peak["tier"]["name"], (peak.get("season") or {}).get("short", "")
    return rec


def val_short(v, has_rid=True):
    if not v:
        return t("first_login") if not has_rid else "—"
    if "k" not in v:
        return v.get("short", "—")  # eski sürüm kaydı
    if v.get("nf"):
        return t("rid_nf")
    if v.get("cur"):
        return f"{val_tier_name(v['cur'])} · {v.get('rr', 0)} RR"
    if v.get("last"):
        return f"{t('unranked')} · {t('last')} {val_tier_name(v['last'])}"
    if v.get("peak"):
        return f"{t('unranked')} · {t('peak')} {val_tier_name(v['peak'])}"
    return t("unranked")


def val_color(v):
    if not v:
        return None
    if "k" not in v:
        return _legacy_color(v.get("short", ""))
    for f in ("cur", "last", "peak"):
        if v.get(f):
            return VAL_COLORS.get(v[f].split()[0])
    return None


def val_detail(v):
    if not v:
        return ""
    if "k" not in v:
        return f"Valorant: {v.get('current', '—')}"
    if v.get("nf"):
        return f"Valorant: {t('rid_nf')}"
    s = "Valorant: " + (f"{val_tier_name(v['cur'])} · {v.get('rr', 0)} RR" if v.get("cur") else t("unranked"))
    if v.get("last"):
        s += f"  ·  {t('last_season')} {val_tier_name(v['last'])}" + (f" ({v['last_s']})" if v.get("last_s") else "")
    if v.get("peak"):
        s += f"  ·  {t('peak')} {val_tier_name(v['peak'])}" + (f" ({v['peak_s']})" if v.get("peak_s") else "")
    return s


def summarize_lol(stats):
    """LCU ranked-stats cevabını dilden bağımsız, küçük bir kayda çevirir (Solo/Duo, Esnek, TFT)."""
    qm = stats.get("queueMap") or {}

    def q(name):
        e = qm.get(name) or {}
        tier = (e.get("tier") or "").upper()
        if not tier or tier in ("NONE", "UNRANKED"):
            return None, e
        return {"t": tier, "d": e.get("division", ""), "lp": e.get("leaguePoints", 0),
                "w": e.get("wins", 0), "l": e.get("losses", 0)}, e

    solo, se = q("RANKED_SOLO_5x5")
    flex, _ = q("RANKED_FLEX_SR")
    tft, _ = q("RANKED_TFT")
    prev = None
    for tier, div in ((se.get("previousSeasonEndTier"), se.get("previousSeasonEndDivision")),
                      (stats.get("highestPreviousSeasonEndTier"), stats.get("highestPreviousSeasonEndDivision"))):
        if tier and tier.upper() not in ("NONE", "UNRANKED"):
            prev = {"t": tier.upper(), "d": div or ""}
            break
    return {"k": 2, "ts": time.time(), "solo": solo, "flex": flex, "tft": tft, "prev": prev}


def _q_text(q, wr=False):
    if not q:
        return t("unranked")
    s = f"{lol_tier_name(q['t'], q['d'])} · {q.get('lp', 0)} LP"
    games = q.get("w", 0) + q.get("l", 0)
    if wr and games:
        s += f" · %{round(100 * q['w'] / games)}"
    return s


def lol_short(l):
    if not l:
        return "—"
    if "k" not in l:
        return l.get("short", "—")
    if l.get("solo"):
        return _q_text(l["solo"])
    if l.get("prev"):
        return f"{t('unranked')} · {t('last')} {lol_tier_name(l['prev']['t'], l['prev']['d'])}"
    return t("unranked")


def tft_short(l):
    if not l or "k" not in l or not l.get("tft"):
        return "—"
    return _q_text(l["tft"])


def tft_icon_key(l):
    if not l or "k" not in l or not l.get("tft"):
        return None
    return f"lol_{l['tft']['t'].lower()}"


def tft_color(l):
    if not l or "k" not in l or not l.get("tft"):
        return None
    return LOL_COLORS.get(l["tft"]["t"])


def lol_color(l):
    if not l:
        return None
    if "k" not in l:
        return _legacy_color(l.get("short", ""))
    q = l.get("solo") or l.get("prev")
    return LOL_COLORS.get(q["t"]) if q else None


def lol_detail(l):
    if not l:
        return ""
    if "k" not in l:
        return f"LoL Solo/Duo: {l.get('solo', '—')}  ·  {t('flex')}: {l.get('flex', '—')}  ({ago_text(l.get('ts', time.time()))})"
    s = f"LoL Solo/Duo: {_q_text(l.get('solo'), True)}  ·  {t('flex')}: {_q_text(l.get('flex'), True)}"
    if l.get("tft"):
        s += f"  ·  {t('tft')}: {_q_text(l['tft'])}"
    if l.get("prev"):
        s += f"  ·  {t('prev_season')} {lol_tier_name(l['prev']['t'], l['prev']['d'])}"
    return s + f"  ({ago_text(l.get('ts', time.time()))})"


LOL_ORDER = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER",
             "CHALLENGER"]
LOL_DIV = {"IV": 0, "III": 1, "II": 2, "I": 3}
CHANGE_KEEP_SEC = 24 * 3600  # değişim etiketi 24 saat görünür


def val_score(v):
    """Valorant: kademe*100 + RR (aynı kademe içinde fark = RR farkı)."""
    n = val_tier_num(v.get("cur")) if v and v.get("cur") else None
    return None if n is None else (n * 100 + int(v.get("rr") or 0), n)


def lol_score(q):
    """LoL: kademe ve küme sırası + LP (aynı küme içinde fark = LP farkı)."""
    if not q or q.get("t") not in LOL_ORDER:
        return None
    step = LOL_ORDER.index(q["t"]) * 4 + LOL_DIV.get(q.get("d") or "", 0)
    return step * 100 + int(q.get("lp") or 0), step


def with_change(old, new, kind):
    """Yeni rank kaydına, eskisine göre değişimi ekler (24 saat boyunca gösterilir)."""
    now = time.time()
    try:
        if kind == "val":
            o, n = val_score(old), val_score(new)
        else:
            o = lol_score((old or {}).get("solo")) if "k" in (old or {}) else None
            n = lol_score(new.get("solo"))
        if o and n and o[0] != n[0]:
            new["chg"] = {"d": n[0] - o[0], "same": o[1] == n[1], "ts": now}
        elif old and old.get("chg") and now - old["chg"].get("ts", 0) < CHANGE_KEEP_SEC:
            new["chg"] = old["chg"]
    except Exception:
        pass
    return new


def change_badge(rec, unit):
    """('+18', '#4ade80') gibi kısa etiket ve renk; değişim yoksa None."""
    c = (rec or {}).get("chg")
    if not c or time.time() - c.get("ts", 0) > CHANGE_KEEP_SEC:
        return None
    up = c["d"] > 0
    color = "#4ade80" if up else "#f87171"
    if c.get("same"):
        return (f"{'+' if up else '−'}{abs(c['d'])}", color,
                f"{'+' if up else '−'}{abs(c['d'])} {unit}")
    return ("▲" if up else "▼", color, t("rank_up") if up else t("rank_down"))


def _legacy_color(text):
    if not text or (text.startswith("Rank yok") and "son" not in text and "en yüksek" not in text):
        return None
    for word in re.findall(r"\w+", text):
        if word in _LEGACY_COLOR:
            return _LEGACY_COLOR[word]
    return None


# Riot Client'ın verdiği bölge kodu -> Valorant rank sunucusunun bölgesi
REGION_TO_AFFINITY = {
    "TR": "eu", "EUW": "eu", "EUNE": "eu", "EU": "eu", "RU": "eu", "ME": "eu", "MENA": "eu",
    "NA": "na", "PBE": "na", "LATAM": "latam", "LAN": "latam", "LAS": "latam", "LA1": "latam", "LA2": "latam",
    "BR": "br", "KR": "kr", "AP": "ap", "JP": "ap", "OCE": "ap", "OC1": "ap", "SEA": "ap", "SG": "ap",
    "PH": "ap", "TH": "ap", "TW": "ap", "VN": "ap",
}


def affinity_of(account, default="eu"):
    code = (account.get("region") or "").upper()
    return REGION_TO_AFFINITY.get(code, default)


def fetch_val_rank(region, name, tag, version):
    """Gemlock rank sunucusundan Valorant rank'ı. `version` aynıysa sunucu önbellekten cevap verir."""
    q = urllib.parse.urlencode({"region": region, "name": name, "tag": tag, "v": version})
    req = urllib.request.Request(f"{RANK_API}?{q}", headers={"X-Gemlock": VERSION, "User-Agent": "Gemlock/" + VERSION})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8")).get("data") or {}


RIOT_CLIENT_LOCKFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Riot Games", "Riot Client", "Config", "lockfile")


class LCU:
    """Açık olan LoL client'ın yerel API'si (Blitz/Porofessor'un kullandığı)."""

    def __init__(self, league_dir=None, lockfile=None):
        self.lockfile = lockfile or os.path.join(league_dir, "lockfile")
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
# Obsidian + buz mavisi teması (nötr griler, tek vurgu rengi)
BG, PANEL, FG, MUTED = "#1e1e1e", "#262626", "#dadada", "#999999"
LINE, ROW_LINE, SEL, SEG_BG = "#363636", "#2c2c2c", "#2f2f2f", "#161616"
ACC, ACC_HOVER, ON_ACC, TITLE = "#8ccbee", "#a3d6f2", "#0c1624", "#181818"
BTN_BORDER, HOVER, KBD, TAB_SEL = "#3f3f3f", "#2c2c2c", "#363636", "#363636"
DANGER, DANGER_BORDER, DANGER_HOVER, WARN = "#f87171", "#4a2a2a", "#2a1d1d", "#e0a458"
ACC_BUSY, KBD_ON_ACC = "#6d8fa3", "#74b3d6"
ACCENT = ACC
def fit_text(text, font, px):
    """Metni verilen piksel genişliğe sığdırır, sığmazsa sonuna … koyar."""
    if font.measure(text) <= px:
        return text
    while text and font.measure(text + "…") > px:
        text = text[:-1]
    return text + "…"


def no_maximize(win):
    """Boyutu değişmeyen pencerede büyüt düğmesini kaldırır (Riot Client gibi sadece küçült ve kapat)."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id())
        style = user32.GetWindowLongW(hwnd, -16)               # GWL_STYLE
        user32.SetWindowLongW(hwnd, -16, style & ~0x00010000)  # WS_MAXIMIZEBOX kaldır
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)       # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED
    except Exception:
        pass


RIOT_PROCS = ["RiotClientServices.exe", "RiotClientUx.exe", "RiotClientUxRender.exe", "Riot Client.exe",
              "LeagueClient.exe", "LeagueClientUx.exe", "LeagueClientUxRender.exe"]
RIOT_PRIVATE_SETTINGS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Riot Games", "Riot Client", "Data",
                                     "RiotGamesPrivateSettings.yaml")


def riot_session():
    """Riot Client'ta açık oturumun 'isim#etiket'i (yoksa None). Sadece bilgisayarın içinde okunur."""
    try:
        rc = LCU(lockfile=RIOT_CLIENT_LOCKFILE)
        if not rc.connect():
            return None
        sess = rc.get("/chat/v1/session")
        if sess.get("loaded") and sess.get("game_name") and sess.get("game_tag"):
            return f"{sess['game_name']}#{sess['game_tag']}"
    except Exception:
        pass
    return None


def sign_out_riot():
    """Riot Client'ı kapatır ve hatırlanan oturumu siler; yeniden açılınca giriş ekranı gelir."""
    for name in RIOT_PROCS:
        subprocess.run(["taskkill", "/F", "/T", "/IM", name], creationflags=CREATE_NO_WINDOW,
                       capture_output=True)
    deadline = time.time() + 10
    while time.time() < deadline and user32.FindWindowW(None, "Riot Client"):
        time.sleep(0.5)
    time.sleep(1.0)
    try:
        if os.path.exists(RIOT_PRIVATE_SETTINGS):
            os.remove(RIOT_PRIVATE_SETTINGS)
    except OSError:
        pass


class Toggle(tk.Label):
    """Modern açma/kapama anahtarı. Kenarları yumuşak, önceden çizilmiş karelerle (Windows'ta pikselli görünmez)."""
    _imgs = None

    def __init__(self, parent, on, command):
        if Toggle._imgs is None:
            Toggle._imgs = [tk.PhotoImage(data=d) for d in TOGGLE_FRAMES]
        super().__init__(parent, bg=parent["bg"], bd=0, cursor="hand2", padx=0, pady=2)
        self.on = bool(on)
        self.command = command
        self._show(len(self._imgs) - 1 if self.on else 0)
        self.bind("<Button-1>", lambda e: self.toggle())

    def _show(self, k):
        self.config(image=self._imgs[k])

    def set(self, on, animate=True):
        on = bool(on)
        if on == self.on:
            return
        self.on = on
        n = len(self._imgs) - 1
        if not animate:
            self._show(n if on else 0)
            return
        seq = range(1, n + 1) if on else range(n - 1, -1, -1)
        for i, k in enumerate(seq, start=1):
            self.after(i * 18, lambda k=k: self._show(k))

    def toggle(self):
        self.set(not self.on)
        self.command(self.on)


_CORNER_CACHE = {}


def _rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def corner_image(r, fill, border, outer, corner):
    """Kenarı yumuşatılmış köşe görseli (çalışırken çizilir, aynı renkler için önbelleklenir)."""
    key = (r, fill, border, outer, corner)
    if key in _CORNER_CACHE:
        return _CORNER_CACHE[key]
    f, b, o = _rgb(fill), _rgb(border), _rgb(outer)
    n = 4
    rows = []
    for y in range(r):
        row = []
        for x in range(r):
            nf = nb = no = 0
            for sy in range(n):
                for sx in range(n):
                    d = math.hypot(r - (x + (sx + .5) / n), r - (y + (sy + .5) / n))
                    if d > r:
                        no += 1
                    elif d > r - 1:
                        nb += 1
                    else:
                        nf += 1
            tot = n * n
            row.append("#%02x%02x%02x" % tuple(round((f[i] * nf + b[i] * nb + o[i] * no) / tot) for i in range(3)))
        rows.append(row)
    if corner in ("tr", "br"):
        rows = [list(reversed(rw)) for rw in rows]
    if corner in ("bl", "br"):
        rows = list(reversed(rows))
    img = tk.PhotoImage(width=r, height=r)
    img.put(" ".join("{" + " ".join(rw) + "}" for rw in rows))
    _CORNER_CACHE[key] = img
    return img


class RoundBox(tk.Frame):
    """Yuvarlak köşeli, isteğe bağlı 1 px kenarlıklı kutu. İçerik .body'ye konur; renkleri paint() ile değişir."""

    def __init__(self, parent, r=8, fill=PANEL, border=None, outer=None, padx=0, pady=0, cursor=""):
        self.r, self.fill = r, fill
        self.border = border or fill
        self.outer = outer or parent.cget("bg")
        super().__init__(parent, bg=fill, cursor=cursor)
        self.body = tk.Frame(self, bg=fill, cursor=cursor)
        self.body.pack(fill="both", expand=True, padx=max(1, padx), pady=max(1, pady))
        self.members = []
        self.lines = [tk.Frame(self, bg=self.border) for _ in range(4)]
        top, bot, left, right = self.lines
        top.place(x=r, y=0, relwidth=1, width=-2 * r, height=1)
        bot.place(x=r, rely=1, y=-1, relwidth=1, width=-2 * r, height=1)
        left.place(x=0, y=r, relheight=1, height=-2 * r, width=1)
        right.place(relx=1, x=-1, y=r, relheight=1, height=-2 * r, width=1)
        self.corners = {}
        for c, (rx, ry, x, y) in {"tl": (0, 0, 0, 0), "tr": (1, 0, -r, 0),
                                  "bl": (0, 1, 0, -r), "br": (1, 1, -r, -r)}.items():
            lb = tk.Label(self, bd=0, highlightthickness=0, padx=0, pady=0, bg=self.outer, cursor=cursor)
            lb.place(relx=rx, rely=ry, x=x, y=y, width=r, height=r)
            self.corners[c] = lb
        # İç boşluk yeterliyse içerik köşelerin üstünde durur; böylece iç içe kutularda dış köşe
        # içteki kutunun köşesini örtmez (iç dikdörtgen, dış yuvarlağın tamamen içinde kalır).
        p = padx if isinstance(padx, int) else min(padx)
        q = pady if isinstance(pady, int) else min(pady)
        if min(p, q) >= math.ceil(0.3 * r):
            self.body.lift()
        self.paint()

    def add(self, w):
        self.members.append(w)
        return w

    def paint(self, fill=None, border=None, outer=None):
        if fill:
            self.fill = fill
        if border:
            self.border = border
        if outer:
            self.outer = outer
        self.config(bg=self.fill)
        self.body.config(bg=self.fill)
        for ln in self.lines:
            ln.config(bg=self.border)
        for c, lb in self.corners.items():
            lb.config(image=corner_image(self.r, self.fill, self.border, self.outer, c))
        for m in self.members:
            try:
                m.config(bg=self.fill)
            except tk.TclError:
                pass

    def parts(self):
        return [self, self.body, *self.lines, *self.corners.values(), *self.members]

    def bind_parts(self, seq, fn):
        for w in self.parts():
            w.bind(seq, fn)


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
# Hesap listesinin sütunları (piksel): başlık ve satırlar aynı genişlikleri kullanır, böylece hizalı kalır
COL_W = {"bar": 3, "num": 26, "name": 146, "vi": 24, "vt": 140, "li": 24, "lt": 136, "ti": 24, "tt": 102}
ROW_H = 34
GAMES = [("valorant", "Valorant"), ("league_of_legends", "LoL"), ("tft", "TFT"), ("", None)]  # None -> t("client_only")
# Riot Client'a verilecek ürün adı (TFT, LoL client'ının içinden açılır)
PRODUCT = {"valorant": "valorant", "league_of_legends": "league_of_legends", "tft": "league_of_legends", "": ""}
LEAGUE_GAMES = {"league_of_legends", "tft"}
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
        self.rid_done = True
        self.exit_procs = GAME_PROCESSES
        self.ranks_dirty = False
        self.ui_msg = ""
        self.logged_in = False
        self.riot_product = None
        self.update_info = None
        self.switching = False
        self.restart_riot = False
        self.icon_imgs = {}

        self.apply_styles()
        self.build_titlebar()

        global LANG
        LANG = self.settings.get("lang", "en")
        self.game = tk.StringVar(value=self.settings.get("game", "valorant"))
        # Blitz her oyun için ayrı: {"valorant": bool, "league_of_legends": bool, "tft": bool}
        if "blitz_games" not in self.settings:
            old = bool(self.settings.get("blitz", False))  # eski tek ayar LoL ve TFT'ye taşınır
            self.settings["blitz_games"] = {"valorant": False, "league_of_legends": old, "tft": old}
        self.game.trace_add("write", lambda *_: (self.update_blitz_box(), self.paint_games(),
                                                  self.refresh() if self.row_widgets is not None else None))
        self.row_widgets = None
        self._last_sel = None
        self.sel_index = None
        self.status_text = ""
        self.ui = None
        self.build_ui()
        self.retitle()

        self.bind("<Up>", lambda e: self.move_sel(-1))
        self.bind("<Down>", lambda e: self.move_sel(1))

        self.bind("<Return>", lambda e: self.choose())
        self.bind("<Escape>", lambda e: self.close())
        for n in range(1, 10):
            self.bind(str(n), lambda e, i=n - 1: self.quick(i))

        self.sync_game()
        self.show_detail()
        self.restore_position()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.focus_force()

        self.refresh_shortcut_icon()
        self.start_riot()
        self.refresh_val_async()
        self.check_update_async()
        self.fetch_icons_async()
        self.after(300, self.tick)
        self.after(500, self.poll_ui)
        if JUST_INSTALLED:
            self.after(300, lambda: messagebox.showinfo(
                "Gemlock Setup",
                "Gemlock has been installed successfully.\n\n"
                "From now on, open it with the Gemlock shortcut on your desktop.\n"
                "The setup file has been removed.", parent=self))
        if not self.accounts:
            self.after(400, self.add_account)

    def refresh_shortcut_icon(self):
        """Güncellemeyle yeni simge geldiyse masaüstü kısayolunu yeni simgeyle yeniler."""
        try:
            name = os.path.basename(ensure_icon())
            if self.settings.get("icon_file") == name:
                return
            if os.path.exists(INSTALLED_SCRIPT) and create_desktop_shortcut(INSTALLED_SCRIPT):
                ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
            self.settings["icon_file"] = name
            save_json(SETTINGS_FILE, self.settings)
        except Exception:
            pass

    # ------------------------------------------------------------ arayüz
    def build_ui(self):
        """Ana ekranı (seçili dilde) kurar. Dil değişince baştan kurulur."""
        if self.ui is not None:
            self.ui.destroy()
        self.ui = tk.Frame(self, bg=BG)
        self.ui.pack(fill="both", expand=True)
        self.main_view = root = tk.Frame(self.ui, bg=BG)
        root.pack(fill="both", expand=True)
        self.settings_view = None

        # Oyun seçimi: Valorant · LoL · TFT tek parça; "Sadece Client" ayrı (oyun değil, farklı seçenek)
        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=12, pady=(12, 8))
        track = RoundBox(top, r=10, fill=SEG_BG, border=LINE, padx=3, pady=3)
        track.pack(side="left", fill="x", expand=True)
        self.game_btns = {}
        for code, label in GAMES:
            if not code:
                continue
            tab = RoundBox(track.body, r=7, fill=SEG_BG, outer=SEG_BG, cursor="hand2")
            tab.pack(side="left", fill="x", expand=True, padx=1)
            lb = tab.add(tk.Label(tab.body, text=label, bg=SEG_BG, fg=MUTED, font=("Segoe UI", 9, "bold"),
                                  pady=6, cursor="hand2"))
            lb.pack(fill="x")
            line = tk.Frame(tab.body, bg=SEG_BG, height=2)
            line.pack(fill="x", side="bottom", padx=10)
            tab.bind_parts("<Button-1>", lambda e, c=code: self.set_game(c))
            self.game_btns[code] = (tab, lb, line)
        solo = self.pill(top, t("client_only"), lambda: self.set_game(""), kind="ghost", padx=14, pady=6)
        solo.pack(side="left", padx=(10, 0))
        self.game_btns[""] = (solo, solo.label, None)

        row1 = tk.Frame(root, bg=BG)
        row1.pack(fill="x", padx=14)
        self.blitz_box = tk.Frame(row1, bg=BG)
        self.blitz_switch = Toggle(self.blitz_box, self.blitz_on(), self.blitz_toggled)
        self.blitz_switch.pack(side="left")
        lb = tk.Label(self.blitz_box, text=t("blitz"), bg=BG, fg=FG, font=("Segoe UI", 9), cursor="hand2")
        lb.pack(side="left", padx=(8, 0))
        lb.bind("<Button-1>", lambda e: self.blitz_switch.toggle())
        langs = tk.Frame(row1, bg=BG)
        langs.pack(side="right")
        for i, code in enumerate(("en", "tr")):
            if i:
                tk.Label(langs, text="·", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left")
            lb = tk.Label(langs, text=code.upper(), bg=BG, fg=ACC if LANG == code else MUTED,
                          font=("Segoe UI", 8, "bold" if LANG == code else "normal"), cursor="hand2", padx=2)
            lb.pack(side="left")
            lb.bind("<Button-1>", lambda e, c=code: self.set_lang(c))
        self.blitz_row = row1

        st_row = tk.Frame(root, bg=BG)
        st_row.pack(fill="x", padx=14, pady=(4, 6))
        self.status_dot = tk.Label(st_row, text="●", bg=BG, fg=ACC, font=("Segoe UI", 7))
        self.status_dot.pack(side="left")
        self.status = tk.Label(st_row, text=self.status_text, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.status.pack(side="left", padx=(4, 0))
        self.status_row = st_row

        box = RoundBox(root, r=10, fill=PANEL, border=LINE, padx=1, pady=(1))
        box.pack(padx=12, fill="x")
        head = tk.Frame(box.body, bg=PANEL)
        head.pack(fill="x")
        hdr = [(COL_W["bar"] + COL_W["num"], ""), (COL_W["name"], t("h_account")),
               (COL_W["vi"] + COL_W["vt"], "VALORANT"), (COL_W["li"] + COL_W["lt"], "LOL"),
               (COL_W["ti"] + COL_W["tt"], "TFT")]
        self.head_labels = {}
        for (w, txt), key in zip(hdr, (None, None, "vt", "lt", "tt")):
            c = tk.Frame(head, bg=PANEL, width=w, height=28)
            c.pack(side="left")
            c.pack_propagate(False)
            lb = tk.Label(c, text=txt, bg=PANEL, fg=MUTED, font=("Segoe UI", 7, "bold"), anchor="w")
            lb.pack(fill="both", expand=True, padx=(2, 0))
            if key:
                self.head_labels[key] = lb
        tk.Frame(box.body, bg=LINE, height=1).pack(fill="x")
        self.rows_frame = tk.Frame(box.body, bg=PANEL)
        self.rows_frame.pack(fill="x")
        tk.Frame(box.body, bg=PANEL, height=8).pack(fill="x")  # alt köşe payı
        self.row_widgets = []

        tools = tk.Frame(root, bg=BG)
        tools.pack(fill="x", padx=12, pady=(10, 0))
        self.pill(tools, t("add_account"), self.add_account, kind="accent").pack(side="left")
        # Sil: kırmızı ve diğerlerinden ayrı (skill: destructive-emphasis)
        self.pill(tools, t("b_del"), lambda: self.sel_index is not None and self.delete_account(self.sel_index),
                  kind="danger").pack(side="right", padx=(12, 0))
        for txt, cmd in (("↓", lambda: self.sel_index is not None and self.move_account(self.sel_index, 1)),
                         ("↑", lambda: self.sel_index is not None and self.move_account(self.sel_index, -1)),
                         (t("b_edit"), lambda: self.sel_index is not None and self.edit_account(self.sel_index))):
            self.pill(tools, txt, cmd).pack(side="right", padx=(6, 0))

        det = tk.Frame(root, bg=BG)
        det.pack(fill="x", padx=14, pady=(10, 0))
        id_row = tk.Frame(det, bg=BG)
        id_row.pack(fill="x")
        self.detail_id = tk.Label(id_row, text="", bg=BG, fg=FG, font=("Segoe UI", 9, "bold"), anchor="w")
        self.detail_id.pack(side="left")
        self.copy_btn = tk.Label(id_row, text=t("copy"), bg=BG, fg=ACC, font=("Segoe UI", 8, "bold"), cursor="hand2")
        self.copy_btn.bind("<Button-1>", lambda e: self.copy_riot_id())
        self.detail_id.bind("<Button-1>", lambda e: self.copy_riot_id())
        self.detail = tk.Label(det, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left",
                               anchor="w", wraplength=sum(COL_W.values()) - 8)
        self.detail.pack(fill="x")

        # Tek ana buton: Giriş yap (skill: primary-action)
        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=12, pady=(12, 6))
        self.go_box = go = RoundBox(bar, r=10, fill=ACC, cursor="hand2")
        go.pack(side="left", expand=True, fill="x")
        inner = go.add(tk.Frame(go.body, bg=ACC, cursor="hand2"))
        inner.pack(pady=7)
        self.go_label = go.add(tk.Label(inner, text=t("login"), bg=ACC, fg=ON_ACC, font=("Segoe UI", 10, "bold"),
                                        cursor="hand2"))
        self.go_label.pack(side="left")
        self.go_kbd = RoundBox(inner, r=4, fill=KBD_ON_ACC, outer=ACC, cursor="hand2")
        self.go_kbd.pack(side="left", padx=(8, 0))
        self.go_kbd.add(tk.Label(self.go_kbd.body, text="Enter", bg=KBD_ON_ACC, fg=ON_ACC,
                                 font=("Segoe UI", 7, "bold"), padx=5, cursor="hand2")).pack()
        self._go_busy = None
        for w in go.parts() + self.go_kbd.parts():
            w.bind("<Button-1>", lambda e: self.choose())
            w.bind("<Enter>", lambda e: self.paint_go(hover=True))
            w.bind("<Leave>", lambda e: self.paint_go(hover=False))
        cog = self.pill(bar, "⚙", self.show_settings, padx=10, pady=5, font=("Segoe UI", 11), r=10)
        cog.pack(side="left", padx=(8, 0), fill="y")
        tk.Label(root, text=t("keys"), bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(pady=(2, 10))

        self.paint_games()
        self.paint_go()
        self.update_blitz_box()
        cur = self.sel_index
        self.sel_index = None
        self.refresh(select_last=cur is None)
        if cur is not None:
            self.select(cur, user=False)

    def pill(self, parent, text, cmd, kind="ghost", padx=12, pady=4, font=None, r=None):
        """Hap şeklinde buton. kind: ghost (kenarlıklı), accent (vurgu yazılı), danger (kırmızı), primary (dolu)."""
        outer = parent.cget("bg")
        styles = {
            "ghost": (outer, BTN_BORDER, FG, HOVER),
            "accent": (outer, BTN_BORDER, ACC, HOVER),
            "danger": (outer, DANGER_BORDER, DANGER, DANGER_HOVER),
            "primary": (ACC, ACC, ON_ACC, ACC_HOVER),
        }
        fill, border, fg, hover = styles[kind]
        font = font or ("Segoe UI", 9, "bold" if kind in ("primary", "accent") else "normal")
        h = tkfont.Font(font=font).metrics("linespace") + 2 * pady + 2
        r = r or max(4, h // 2)
        b = RoundBox(parent, r=r, fill=fill, border=border, outer=outer, cursor="hand2")
        b.label = b.add(tk.Label(b.body, text=text, bg=fill, fg=fg, font=font, padx=max(padx, r // 2),
                                 pady=pady, cursor="hand2"))
        b.label.pack(fill="both", expand=True)
        b.style = (fill, border, fg, hover)
        b.bind_parts("<Button-1>", lambda e: cmd())
        b.bind_parts("<Enter>", lambda e: b.paint(fill=b.style[3]))
        b.bind_parts("<Leave>", lambda e: b.paint(fill=b.style[0]))
        return b

    def paint_go(self, hover=None):
        if not hasattr(self, "go_box"):
            return
        if hover is not None:
            self._go_hover = hover
        busy = self.pending is not None
        fill = ACC_BUSY if busy else (ACC_HOVER if getattr(self, "_go_hover", False) else ACC)
        self.go_box.paint(fill=fill, border=fill)
        self.go_label.config(text=t("logging_in") if busy else t("login"),
                             cursor="watch" if busy else "hand2")
        if busy:
            self.go_kbd.pack_forget()
        elif not self.go_kbd.winfo_ismapped():
            self.go_kbd.pack(side="left", padx=(8, 0))
        self.go_kbd.paint(outer=fill)
        self._go_busy = busy

    # ------------------------------------------------------------ başlık çubuğu (Riot Client gibi: sadece – ve ✕)
    def build_titlebar(self):
        self.title_bar = bar = tk.Frame(self, bg=TITLE, height=34)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        try:
            self.title_icon = tk.PhotoImage(data=TITLE_ICON_B64)
            tk.Label(bar, image=self.title_icon, bg=TITLE).pack(side="left", padx=(10, 6))
        except tk.TclError:
            pass
        self.title_label = tk.Label(bar, text="Gemlock", bg=TITLE, fg=MUTED, font=("Segoe UI", 9))
        self.title_label.pack(side="left")
        self.btn_close = tk.Label(bar, text="✕", bg=TITLE, fg=MUTED, font=("Segoe UI", 10), width=5, cursor="hand2")
        self.btn_close.pack(side="right", fill="y")
        self.btn_min = tk.Label(bar, text="—", bg=TITLE, fg=MUTED, font=("Segoe UI", 9), width=5, cursor="hand2")
        self.btn_min.pack(side="right", fill="y")
        self.btn_close.bind("<Button-1>", lambda e: self.close())
        self.btn_close.bind("<Enter>", lambda e: self.btn_close.config(bg="#c42b1c", fg="white"))
        self.btn_close.bind("<Leave>", lambda e: self.btn_close.config(bg=TITLE, fg=MUTED))
        self.btn_min.bind("<Button-1>", lambda e: self.minimize())
        self.btn_min.bind("<Enter>", lambda e: self.btn_min.config(bg=SEL, fg=FG))
        self.btn_min.bind("<Leave>", lambda e: self.btn_min.config(bg=TITLE, fg=MUTED))
        for w in (bar, self.title_label):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        # Windows'un kendi başlık çubuğunu kaldır, görev çubuğunda görünmeye devam et
        self.custom_frame = False
        try:
            self.overrideredirect(True)
            self.after(10, self._show_in_taskbar)
            self.custom_frame = True
        except tk.TclError:
            bar.destroy()
            self.after(50, lambda: (dark_titlebar(self), no_maximize(self)))

    def _show_in_taskbar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            ex = (ex & ~0x00000080) | 0x00040000                  # TOOLWINDOW kaldır, APPWINDOW ekle
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex)
            self.withdraw()
            self.after(10, self.deiconify)
        except Exception:
            pass

    def minimize(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception:
            pass

    def _drag_start(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e):
        dx, dy = getattr(self, "_drag", (0, 0))
        self.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def retitle(self):
        if hasattr(self, "btn_min"):
            self.btn_min_tip = t("minimize")

    def set_lang(self, code):
        global LANG
        if code == LANG:
            return
        LANG = code
        self.settings["lang"] = code
        save_json(SETTINGS_FILE, self.settings)
        self.status_text = ""
        self.build_ui()
        self.retitle()

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

    def display_order(self):
        """Ekranda görünen sıra: eklenme sırası; sabitlenenler en üstte."""
        idx = list(range(len(self.accounts)))
        return sorted(idx, key=lambda i: not self.accounts[i].get("pinned"))

    def select(self, i, user=True):
        if not self.accounts:
            self.sel_index = None
            return
        i = max(0, min(i, len(self.accounts) - 1))
        self.sel_index = i
        for acc_i, row, bar, cells, chip in self.row_widgets:
            bg = SEL if acc_i == i else PANEL
            for w in [row, bar] + cells:
                w.config(bg=bg)
            if chip:
                chip.paint(outer=bg)
        if user:
            self.sync_game()
        self.show_detail()

    def move_sel(self, d):
        if self.sel_index is None:
            return
        order = [r[0] for r in self.row_widgets]
        if self.sel_index in order:
            pos = max(0, min(order.index(self.sel_index) + d, len(order) - 1))
            self.select(order[pos])

    def refresh(self, select_last=False):
        cur = self.selected()
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self.row_widgets = []
        name_font = tkfont.Font(family="Segoe UI", size=10)
        small_font = tkfont.Font(family="Segoe UI", size=9)

        def cell(row, w):
            c = tk.Frame(row, bg=PANEL, width=w, height=ROW_H)
            c.pack(side="left")
            c.pack_propagate(False)
            return c

        game = self.game.get()
        focus_col = {"valorant": "vt", "league_of_legends": "lt", "tft": "tt"}.get(game)
        for name_key, lb in getattr(self, "head_labels", {}).items():
            lb.config(fg=ACC if name_key == focus_col else MUTED)
        for pos, i in enumerate(self.display_order()):
            a = self.accounts[i]
            row = tk.Frame(self.rows_frame, bg=PANEL)
            row.pack(fill="x")
            if pos:
                tk.Frame(self.rows_frame, bg=ROW_LINE, height=1).pack(fill="x", before=row)
            bar = cell(row, COL_W["bar"])
            cells = []
            c = cell(row, COL_W["num"])
            chip = None
            if pos < 9:
                chip = RoundBox(c, r=4, fill=KBD, outer=PANEL)
                chip.add(tk.Label(chip.body, text=str(pos + 1), bg=KBD, fg=FG, font=("Segoe UI", 7, "bold"),
                                  padx=4)).pack()
                chip.place(relx=1, rely=.5, x=-6, anchor="e")
            cells += [c]
            c = cell(row, COL_W["name"])
            region = (a.get("region") or "").upper()
            pin = "★ " if a.get("pinned") else ""
            room = COL_W["name"] - 8 - (len(region) * 7 + 14 if region else 0)
            name = tk.Label(c, text=pin + fit_text(a["name"], name_font, room - (14 if pin else 0)), bg=PANEL,
                            fg=FG, font=name_font, anchor="w")
            name.pack(side="left", fill="y")
            cells += [c, name]
            if region:
                rtag = tk.Label(c, text=region, bg=PANEL, fg=MUTED, font=("Segoe UI", 7, "bold"))
                rtag.pack(side="left", padx=(6, 0))
                cells.append(rtag)
            vr, lr = self.rank_of(a, "val"), self.rank_of(a, "lol")
            warn = bool(self.decay_days(a))
            badges = {"vt": change_badge(vr, "RR"), "lt": change_badge(lr, "LP"), "tt": None}
            for (text, color, ikey), (wi, wt) in (
                    ((val_short(vr, bool(a.get("riot_id"))), val_color(vr), val_icon_key(vr)), ("vi", "vt")),
                    ((("⚠ " if warn else "") + lol_short(lr), lol_color(lr), lol_icon_key(lr)), ("li", "lt")),
                    ((tft_short(lr), tft_color(lr), tft_icon_key(lr)), ("ti", "tt"))):
                ci = cell(row, COL_W[wi])
                img = self.rank_icon(ikey)
                if img is not None:
                    dot = tk.Label(ci, image=img, bg=PANEL)
                else:
                    dot = tk.Label(ci, text="●" if color else "○", bg=PANEL, fg=color or MUTED, font=("Segoe UI", 8))
                dot.pack(fill="both", expand=True)
                ct = cell(row, COL_W[wt])
                # seçili oyunun sütunu öne çıkar, diğerleri bir ton soluk
                base = FG if (focus_col is None or wt == focus_col) else "#b5b5b5"
                badge = badges.get(wt)
                room = COL_W[wt] - 6 - (26 if badge else 0)
                lab = tk.Label(ct, text=fit_text(text, small_font, room), bg=PANEL,
                               fg=WARN if (warn and wt == "lt") else (base if color else MUTED), font=small_font,
                               anchor="w")
                lab.pack(side="left", fill="y", padx=(2, 0))
                cells += [ci, dot, ct, lab]
                if badge:
                    bl = tk.Label(ct, text=badge[0], bg=PANEL, fg=badge[1], font=("Segoe UI", 7, "bold"))
                    bl.pack(side="left", padx=(4, 0))
                    cells.append(bl)
            targets = [row, bar] + cells + (chip.parts() if chip else [])
            for w in targets:
                w.bind("<Button-3>", lambda e, k=i: self.row_menu(e, k))
                w.bind("<Button-1>", lambda e, k=i: self.select(k))
                w.bind("<Double-Button-1>", lambda e, k=i: (self.select(k), self.choose()))
            self.row_widgets.append((i, row, bar, cells, chip))
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
        rid = a.get("riot_id")
        self.detail_id.config(text=rid or t("rid_pending"), cursor="hand2" if rid else "")
        if rid:
            if not self.copy_btn.winfo_ismapped():
                self.copy_btn.pack(side="left", padx=(8, 0))
        else:
            self.copy_btn.pack_forget()
        vline, lline = val_detail(v), lol_detail(l)
        vb, lb = change_badge(v, "RR"), change_badge(l, "LP")
        if vline and vb:
            vline += f"  ({vb[2]}, {t('since_last')})"
        if lline and lb:
            lline += f"  ({lb[2]}, {t('since_last')})"
        lines = [x for x in (vline, lline) if x]
        played = self.settings.get("played", {}).get(self.key_of(a))
        lines.append(t("last_played", ago=ago_text(played)) if played else t("never_played"))
        if self.decay_days(a):
            lines.append("⚠ " + t("decay", n=self.decay_days(a)))
        self.detail.config(text="\n".join(lines))

    def copy_riot_id(self):
        i = self.selected()
        rid = self.accounts[i].get("riot_id") if i is not None else None
        if not rid:
            return
        self.clipboard_clear()
        self.clipboard_append(rid)
        self.set_status(t("copied"))
        self.copy_btn.config(text="✓")
        self.after(1500, lambda: self.copy_btn.config(text=t("copy")))

    def set_status(self, text):
        self.status_text = text
        try:
            self.status.config(text=text)
        except tk.TclError:
            pass

    # ------------------------------------------------------- Valorant rank
    def refresh_val_async(self, force=False):
        """Sadece gerekli hesapları sorar:
        - Gemlock ile giriş yapılıp oynanmış olabilecek hesaplar (rank değişmiş olabilir)
        - 6 saattir yenilenmemiş hesaplar
        Böylece sunucunun ortak sorgu hakkı korunur."""
        region = self.settings.get("region", "eu")
        played = self.settings.get("played", {})
        now = time.time()
        todo = []
        for a in self.accounts:
            name, tag = split_riot_id(a.get("riot_id"))
            if not name:
                continue
            k = self.key_of(a)
            old = self.rank_of(a, "val")
            last = old.get("ts", 0) if "k" in old else 0  # eski biçimli kayıtlar yenilenir
            pts = played.get(k, 0)
            if force:
                ver = str(int(now))
            elif pts > last:
                ver = f"p{int(pts)}"                 # oynandı: yeni sorgu
            elif now - last > VAL_IDLE_SEC:
                ver = f"b{int(now // VAL_IDLE_SEC)}"  # 6 saatlik dilim: herkes aynı önbelleği paylaşır
            else:
                continue
            todo.append((k, name, tag, ver, affinity_of(a, region)))
        if not todo:
            return

        def work():
            busy = failed = 0
            for k, name, tag, ver, reg in todo:
                try:
                    summ = summarize_val(fetch_val_rank(reg, name, tag, ver))
                    with self.rank_lock:
                        self.ranks.setdefault(k, {})["val"] = with_change(self.ranks.get(k, {}).get("val"), summ, "val")
                        save_json(RANK_FILE, self.ranks)
                    self.ranks_dirty = True
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        busy += 1
                        break                       # sunucu yoğun: kalanları sonraki açılışa bırak
                    if e.code == 404:
                        with self.rank_lock:
                            self.ranks.setdefault(k, {})["val"] = {"k": 2, "nf": True, "ts": time.time()}
                            save_json(RANK_FILE, self.ranks)
                        self.ranks_dirty = True
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                time.sleep(1.0)                     # sunucuya nazik ol
            if busy:
                self.ui_msg = t("srv_busy")
            elif failed:
                self.ui_msg = t("val_fail")
            else:
                self.ui_msg = t("val_ok")
            self.ranks_dirty = True

        threading.Thread(target=work, daemon=True).start()

    def fetch_icons_async(self):
        """Listede görünen rank'ların sembollerini (yoksa) arka planda indirir."""
        keys = set()
        for a in self.accounts:
            for k in (val_icon_key(self.rank_of(a, "val")), lol_icon_key(self.rank_of(a, "lol")),
                      tft_icon_key(self.rank_of(a, "lol"))):
                if k and not os.path.exists(icon_path(k)):
                    keys.add(k)
        if not keys:
            return

        def work():
            if any([download_icon(k) for k in sorted(keys)]):
                self.ranks_dirty = True

        threading.Thread(target=work, daemon=True).start()

    def rank_icon(self, key):
        """İndirilmiş sembolü ~18 px boyutunda döndürür (yoksa None)."""
        if not key:
            return None
        if key in self.icon_imgs:
            return self.icon_imgs[key]
        path = icon_path(key)
        if not os.path.exists(path):
            return None
        try:
            img = tk.PhotoImage(file=path)
            n = max(1, -(-max(img.width(), img.height()) // 18))  # yukarı yuvarlanmış küçültme oranı
            if n > 1:
                img = img.subsample(n, n)
        except tk.TclError:
            img = None
        self.icon_imgs[key] = img
        return img

    def poll_ui(self):
        """Arka plan işlerinden gelen güncellemeleri ana pencereye uygular."""
        if getattr(self, "_go_busy", None) is not None and self._go_busy != (self.pending is not None):
            self.paint_go()
        if self.ranks_dirty:
            self.ranks_dirty = False
            self.fetch_icons_async()
            if self.state() != "withdrawn":
                self.refresh()
                self.show_detail()
        if self.ui_msg:
            self.set_status(self.ui_msg)
            self.ui_msg = ""
        if self.update_info and self.pending is None and self.state() != "withdrawn":
            info, self.update_info = self.update_info, None
            notes = info.get("notes_en" if LANG == "en" else "notes", "") or info.get("notes", "")
            if messagebox.askyesno(t("upd_title"), t("upd_q", new=info["version"], cur=VERSION, notes=notes),
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
                    self.ui_msg = t("upd_ok", v=VERSION)
            except Exception:
                if manual:
                    self.ui_msg = t("upd_fail_check")

        threading.Thread(target=work, daemon=True).start()

    def apply_update(self, info):
        import hashlib
        self.set_status(t("upd_dl", v=info["version"]))
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
            messagebox.showerror(t("upd_fail_t"), t("upd_fail", e=e),
                                 parent=self)
            self.after(500, self.poll_ui)
            return
        self.save_position()
        kernel32.CloseHandle(_mutex)  # yeni sürüm açılabilsin
        subprocess.Popen([pythonw_exe(), INSTALLED_SCRIPT], close_fds=True, cwd=INSTALL_DIR)
        self.destroy()

    # ------------------------------------------------------------ Riot ID
    def read_riot_id(self, user_key):
        """Girişten sonra Riot Client'ın yerel bilgisinden oyun adı#etiketi okur, hesaba yazar
        ve o hesabın Valorant rank'ını hemen çeker. Sadece bilgisayarın içinde okuma yapılır."""
        try:
            rc = LCU(lockfile=RIOT_CLIENT_LOCKFILE)
            deadline = time.time() + 30
            rid = None
            while time.time() < deadline and not rid and not game_running():
                if rc.connect():
                    try:
                        sess = rc.get("/chat/v1/session")
                        if sess.get("loaded") and sess.get("game_name") and sess.get("game_tag"):
                            rid = f"{sess['game_name']}#{sess['game_tag']}"
                    except Exception:
                        pass
                if not rid:
                    time.sleep(2)
            if not rid:
                return
            region = None
            try:
                region = (rc.get("/riotclient/region-locale").get("region") or "").upper() or None
            except Exception:
                pass
            acc = None
            with self.rank_lock:
                for a in self.accounts:
                    if self.key_of(a) == user_key:
                        acc = a
                        changed = False
                        if a.get("riot_id") != rid:
                            a["riot_id"] = rid  # yeni ya da değişmiş isim
                            changed = True
                        if region and not a.get("region_manual") and a.get("region") != region:
                            a["region"] = region  # bölge otomatik bulundu
                            changed = True
                        if changed:
                            save_json(DATA_FILE, self.accounts)
            name, tag = split_riot_id(rid)
            played = self.settings.get("played", {}).get(user_key, time.time())
            try:
                reg = affinity_of(acc or {}, self.settings.get("region", "eu"))
                summ = summarize_val(fetch_val_rank(reg, name, tag, f"p{int(played)}"))
                with self.rank_lock:
                    self.ranks.setdefault(user_key, {})["val"] = with_change(
                        self.ranks.get(user_key, {}).get("val"), summ, "val")
                    save_json(RANK_FILE, self.ranks)
            except Exception:
                pass
        finally:
            self.rid_done = True

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
        stop = VAL_PROCS | LOL_MATCH_PROCS
        while time.time() < deadline and not game_running(stop):
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
            if game_running(stop):
                break  # maç başladı: okumayı bırak
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
                    self.ranks.setdefault(self.key_of(a), {})["lol"] = with_change(
                        self.ranks.get(self.key_of(a), {}).get("lol"), summ, "lol")
                    save_json(RANK_FILE, self.ranks)
            except Exception:
                continue
        self.lcu_done = True

    # ------------------------------------------------------------- Riot
    def start_riot(self):
        if user32.FindWindowW(None, "Riot Client"):
            self.set_status(t("rc_open"))
            return
        exe = find_riot_exe()
        if exe:
            product = PRODUCT.get(self.game.get(), "")
            args = [exe] + ([f"--launch-product={product}", "--launch-patchline=live"] if product else [])
            subprocess.Popen(args, close_fds=True)
            self.riot_product = product
            self.set_status(t("rc_opening"))
        else:
            self.set_status(t("rc_missing"))

    def tick(self):
        if self.switching:  # eski oturum kapatılırken eski pencereye yazma
            self.after(300, self.tick)
            return
        if self.restart_riot:
            self.restart_riot = False
            self.start_riot()
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
    def blitz_on(self, game=None):
        game = self.game.get() if game is None else game
        return bool(self.settings.get("blitz_games", {}).get(game, False))

    def update_blitz_box(self):
        """Blitz anahtarı her oyun sekmesinde o oyunun kendi ayarını gösterir; Client only'de gizlenir."""
        if self.game.get():
            self.blitz_switch.set(self.blitz_on(), animate=False)
            if not self.blitz_box.winfo_ismapped():
                self.blitz_box.pack(side="left")
        else:
            self.blitz_box.pack_forget()

    def blitz_toggled(self, on):
        game = self.game.get()
        if on and not find_blitz_exe(self.settings.get("blitz_path")):
            path = filedialog.askopenfilename(parent=self, title=t("blitz_pick"),
                                              filetypes=[("Blitz", "Blitz.exe"), ("Programlar", "*.exe")])
            if path:
                self.settings["blitz_path"] = path
            else:
                on = False
                self.blitz_switch.set(False)
        self.settings.setdefault("blitz_games", {})[game] = on
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
        order = [r[0] for r in self.row_widgets]
        if i < len(order):
            self.select(order[i])
            self.choose()

    # ------------------------------------------------------------ oyun
    def paint_games(self):
        cur = self.game.get()
        for code, (box, lb, line) in self.game_btns.items():
            sel = code == cur
            if code:
                fill = TAB_SEL if sel else SEG_BG
                box.paint(fill=fill, border=fill, outer=SEG_BG)
                lb.config(fg=FG if sel else MUTED)
                line.config(bg=ACC if sel else fill)
            else:
                box.style = (BG, ACC if sel else BTN_BORDER, ACC if sel else MUTED, HOVER)
                box.paint(fill=BG, border=box.style[1])
                lb.config(fg=box.style[2])

    def set_game(self, code):
        if self.game.get() == code:
            return
        self.game.set(code)
        self.tell_riot_product()

    def tell_riot_product(self):
        """Giriş yapılmadan önce Riot Client'ı seçilen oyuna çevirir (giriş ekranı o oyunun teması olur)."""
        product = PRODUCT.get(self.game.get(), "")
        if not product or self.logged_in or product == self.riot_product:
            return
        exe = find_riot_exe()
        if exe and user32.FindWindowW(None, "Riot Client"):
            subprocess.Popen([exe, f"--launch-product={product}", "--launch-patchline=live"], close_fds=True)
            self.riot_product = product

    def choose(self):
        i = self.selected()
        if i is None or self.pending is not None:
            return
        a = self.accounts[i]
        cur = riot_session()
        if cur:
            if a.get("riot_id") and cur.lower() == a["riot_id"].lower():
                # zaten bu hesapla girilmiş: sadece oyunu başlat
                self.set_status(t("same_acc"))
                self.settings["last"], self.settings["game"] = i, self.game.get()
                save_json(SETTINGS_FILE, self.settings)
                self.logged_in = True
                self.exit_procs = GAME_PROCESSES
                self.rid_done = True
                self.launch_started = time.time()
                self.after(300, lambda: self.launch_when_ready(PRODUCT.get(self.game.get(), "")))
                self.finish_started = time.time()
                self.after(1500, self.finish_when_done)
                return
            if game_running(VAL_PROCS | LOL_MATCH_PROCS):
                messagebox.showwarning(t("switch_t"), t("game_open"), parent=self)
                return
            if not messagebox.askyesno(t("switch_t"), t("switch_q", cur=cur, new=a.get("riot_id") or a["name"]),
                                       parent=self):
                return
            self.set_status(t("switching"))
            self.update_idletasks()

            self.switching = True

            def restart():
                try:
                    sign_out_riot()
                    self.riot_seen_at = None
                    self.riot_product = None
                    self.restart_riot = True
                finally:
                    self.switching = False

            threading.Thread(target=restart, daemon=True).start()
        self.pending = i
        self.settings["last"] = i
        self.settings["game"] = self.game.get()
        save_json(SETTINGS_FILE, self.settings)
        self.accounts[i]["game"] = self.game.get()
        save_json(DATA_FILE, self.accounts)
        if not riot_hwnd() or (self.riot_seen_at and time.time() - self.riot_seen_at <= 4):
            self.set_status(t("pending", name=self.accounts[i]["name"]))

    # ------------------------------------------------------------ giriş
    def do_login(self, hwnd, i):
        a = self.accounts[i]
        try:
            pw = decrypt(a["pw"])
        except OSError as e:
            messagebox.showerror(t("err"), str(e), parent=self)
            self.pending = None
            self.after(300, self.tick)
            return

        self.save_position()
        self.logged_in = True
        self.settings.setdefault("played", {})[self.key_of(a)] = time.time()
        save_json(SETTINGS_FILE, self.settings)
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
            messagebox.showwarning(t("stop_t"), t("stop"), parent=self)
            self.after(300, self.tick)
            return

        product = PRODUCT.get(self.game.get(), "")

        # Önce Riot ID ve Valorant rank'ı (Play'den önce, sadece Riot Client'tan), sonra oyunu başlat.
        # Oyun açıldığı an Gemlock kapanır; oyun açıkken hiçbir şey okunmaz.
        self.rid_done = False
        threading.Thread(target=self.read_riot_id, args=(self.key_of(a),), daemon=True).start()
        self.launch_started = time.time()
        self.after(500, lambda: self.launch_when_ready(product))

        # Valorant / sadece Client: herhangi bir oyun açıldığı an kapan.
        # LoL: client'ta rank'ları okuyup kapan; maç başlarsa her durumda anında kapan.
        if product == "league_of_legends":
            self.exit_procs = VAL_PROCS | LOL_MATCH_PROCS
            self.lcu_done = False
            threading.Thread(target=self.refresh_lol_from_client, args=(self.key_of(a),), daemon=True).start()
        else:
            self.exit_procs = GAME_PROCESSES

        if product and self.blitz_on():
            if True:
                blitz = find_blitz_exe(self.settings.get("blitz_path"))
                if blitz:
                    subprocess.Popen([blitz], close_fds=True, cwd=os.path.dirname(blitz))
                    self.blitz_done = False
                    self.blitz_tries = 0
                    self.after(1500, lambda: self.focus_blitz(os.path.basename(blitz).lower()))
        self.finish_started = time.time()
        self.after(500, self.finish_when_done)

    def launch_when_ready(self, product):
        """Riot ID okununca (en fazla 15 sn) seçilen oyunu başlatır."""
        if not self.rid_done and time.time() - self.launch_started < 15 and not game_running():
            self.after(500, lambda: self.launch_when_ready(product))
            return
        exe = find_riot_exe()
        if product and exe and not game_running():
            subprocess.Popen([exe, f"--launch-product={product}", "--launch-patchline=live"], close_fds=True)

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
        # işler bitince ya da oyun/maç başladığı an (Gemlock oyun açıkken hiç çalışmasın) tamamen kapan
        if (self.lcu_done and self.blitz_done and self.rid_done) or game_running(self.exit_procs) \
                or time.time() - self.finish_started > 210:
            self.destroy()
            return
        self.after(500, self.finish_when_done)

    # ----------------------------------------------------------- hesaplar
    def dialog(self, parent, title, name="", user="", pw="", rid="", region=""):
        """Hesap ekleme/düzenleme penceresi: ana pencerenin ortasında, koyu temada."""
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title(title)
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.transient(parent)
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=14)
        tk.Label(body, text=title, bg=BG, fg=FG, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        rows = [(t("f_label"), name, False), (t("f_user"), user, False),
                (t("f_pw"), pw, True), (t("f_rid"), rid, False)]
        vars_ = []
        for r, (label, val, secret) in enumerate(rows, start=1):
            tk.Label(body, text=label, bg=BG, fg=MUTED, font=("Segoe UI", 9)).grid(row=r, column=0, sticky="w",
                                                                                   pady=5, padx=(0, 12))
            v = tk.StringVar(value=val)
            e = tk.Entry(body, textvariable=v, width=30, show="•" if secret else "", bg=PANEL, fg=FG,
                         insertbackground=FG, relief="flat", font=("Segoe UI", 10), highlightthickness=1,
                         highlightbackground=BTN_BORDER, highlightcolor=ACC)
            e.grid(row=r, column=1, sticky="we", pady=5, ipady=4)
            if r == 1:
                e.focus_set()
            if secret:
                eye = tk.Label(body, text=t("show"), bg=BG, fg=ACC, font=("Segoe UI", 8, "bold"), cursor="hand2")
                eye.grid(row=r, column=2, padx=(8, 0))

                def flip(_e=None, e=e, eye=eye):
                    hidden = e.cget("show") != ""
                    e.config(show="" if hidden else "•")
                    eye.config(text=t("hide") if hidden else t("show"))

                eye.bind("<Button-1>", flip)
            vars_.append(v)
        # Bölge: Otomatik (ilk girişte bulunur) ya da elle
        tk.Label(body, text=t("f_region"), bg=BG, fg=MUTED, font=("Segoe UI", 9)).grid(row=5, column=0, sticky="w",
                                                                                     pady=5, padx=(0, 12))
        regv = tk.StringVar(value=region or "")
        track = RoundBox(body, r=9, fill=SEG_BG, border=LINE, padx=3, pady=3)
        track.grid(row=5, column=1, columnspan=2, sticky="w", pady=5)
        pills = {}

        def paint_regions():
            for code, (pb, lb) in pills.items():
                sel = code == regv.get()
                fill = TAB_SEL if sel else SEG_BG
                pb.paint(fill=fill, border=ACC if sel else fill, outer=SEG_BG)
                lb.config(fg=FG if sel else MUTED)

        for code, label in [("", t("auto"))] + [(c.upper(), c.upper()) for c in REGIONS]:
            pb = RoundBox(track.body, r=7, fill=SEG_BG, outer=SEG_BG, cursor="hand2")
            pb.pack(side="left", padx=1)
            lb = pb.add(tk.Label(pb.body, text=label, bg=SEG_BG, font=("Segoe UI", 8, "bold"), padx=6, pady=2,
                                 cursor="hand2"))
            lb.pack()
            pb.bind_parts("<Button-1>", lambda e, c=code: (regv.set(c), paint_regions()))
            pills[code] = (pb, lb)
        # otomatik bulunmuş bir bölge varsa (TR, EUW...) "Otomatik" seçili görünür
        if regv.get() and regv.get() not in pills:
            regv.set("")
        paint_regions()
        tk.Label(body, text=t("rid_hint") + " " + t("region_auto_hint"), bg=BG, fg=MUTED, font=("Segoe UI", 8),
                 wraplength=360, justify="left").grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 12))
        result = {}

        def ok(_=None):
            result["v"] = [vars_[0].get().strip(), vars_[1].get().strip(), vars_[2].get(), vars_[3].get().strip(),
                           regv.get()]
            win.destroy()

        btns = tk.Frame(body, bg=BG)
        btns.grid(row=7, column=0, columnspan=3, sticky="e")
        self.pill(btns, t("cancel"), win.destroy, kind="ghost", padx=14, pady=5).pack(side="left", padx=(0, 8))
        self.pill(btns, t("save"), ok, kind="primary", padx=16, pady=5).pack(side="left")
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())
        # ana pencerenin ortasına yerleştir
        win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.deiconify()
        win.after(30, lambda: (dark_titlebar(win), no_maximize(win)))
        win.grab_set()
        parent.wait_window(win)
        return result.get("v")

    # ------------------------------------------------------------ hesap işlemleri
    def add_account(self):
        r = self.dialog(self, t("add_t"))
        if r and r[1] and r[2]:
            acc = {"name": r[0] or r[1], "user": r[1], "pw": encrypt(r[2]), "riot_id": r[3]}
            if r[4]:
                acc["region"], acc["region_manual"] = r[4], True
            self.accounts.append(acc)
            save_json(DATA_FILE, self.accounts)
            self.refresh()
            self.select(len(self.accounts) - 1)
            self.refresh_val_async(force=True)

    def edit_account(self, i):
        a = self.accounts[i]
        try:
            pw = decrypt(a["pw"])
        except OSError:
            pw = ""
        r = self.dialog(self, t("edit_t"), a["name"], a["user"], pw, a.get("riot_id", ""),
                        a.get("region", "") if a.get("region_manual") else "")
        if r and r[1] and r[2]:
            new = {**a, "name": r[0] or r[1], "user": r[1], "pw": encrypt(r[2]), "riot_id": r[3]}
            if r[4]:
                new["region"], new["region_manual"] = r[4], True
            elif a.get("region_manual"):
                new.pop("region", None)  # Otomatik'e döndü: bir sonraki girişte yeniden bulunur
                new.pop("region_manual", None)
            self.accounts[i] = new
            save_json(DATA_FILE, self.accounts)
            self.refresh()
            self.select(i)
            self.refresh_val_async(force=True)

    def delete_account(self, i):
        if messagebox.askyesno(t("del_t"), t("del_q", name=self.accounts[i]["name"]), parent=self):
            del self.accounts[i]
            save_json(DATA_FILE, self.accounts)
            self.sel_index = None
            self.refresh(select_last=True)

    def move_account(self, i, d):
        j = i + d
        if 0 <= j < len(self.accounts):
            self.accounts[i], self.accounts[j] = self.accounts[j], self.accounts[i]
            save_json(DATA_FILE, self.accounts)
            self.refresh()
            self.select(j)

    def row_menu(self, event, i):
        self.select(i)
        m = tk.Menu(self, tearoff=0, bg=PANEL, fg=FG, activebackground=SEL, activeforeground=FG,
                    bd=0, relief="flat", font=("Segoe UI", 9))
        m.add_command(label=t("b_edit"), command=lambda: self.edit_account(i))
        pinned = bool(self.accounts[i].get("pinned"))
        m.add_command(label=t("unpin") if pinned else t("pin"), command=lambda: self.toggle_pin(i))
        m.add_command(label=t("move_up"), command=lambda: self.move_account(i, -1),
                      state="normal" if i > 0 else "disabled")
        m.add_command(label=t("move_down"), command=lambda: self.move_account(i, 1),
                      state="normal" if i < len(self.accounts) - 1 else "disabled")
        m.add_separator()
        m.add_command(label=t("b_del"), command=lambda: self.delete_account(i))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def toggle_pin(self, i):
        a = self.accounts[i]
        a["pinned"] = not a.get("pinned")
        save_json(DATA_FILE, self.accounts)
        self.refresh()
        self.select(i, user=False)

    def decay_days(self, a):
        """Diamond ve üstü LoL hesabı 21+ gündür Gemlock ile oynanmadıysa gün sayısı, değilse 0."""
        l = self.rank_of(a, "lol")
        solo = l.get("solo") if "k" in l else None
        if not solo or solo.get("t") not in ("DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"):
            return 0
        played = self.settings.get("played", {}).get(self.key_of(a))
        if not played:
            return 0
        days = int((time.time() - played) / 86400)
        return days if days >= 21 else 0

    # ------------------------------------------------------------ ayarlar ekranı (aynı pencerede)
    def show_settings(self):
        width = self.main_view.winfo_width()
        self.main_view.pack_forget()
        if self.settings_view is not None:
            self.settings_view.destroy()
        self.settings_view = v = tk.Frame(self.ui, bg=BG)
        v.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Frame(v, bg=BG, width=max(0, width - 24), height=0).pack()  # ana ekranla aynı genişlik

        top = tk.Frame(v, bg=BG)
        top.pack(fill="x", pady=(0, 10))
        back = self.pill(top, t("back"), self.hide_settings, kind="ghost")
        back.pack(side="left")
        tk.Label(top, text=t("settings"), bg=BG, fg=FG, font=("Segoe UI", 12, "bold")).pack(side="left", padx=14)

        def section(title):
            tk.Label(v, text=title.upper(), bg=BG, fg=MUTED, font=("Segoe UI", 7, "bold")).pack(
                anchor="w", pady=(10, 4))
            box = RoundBox(v, r=10, fill=PANEL, border=LINE, padx=12, pady=10)
            box.pack(fill="x")
            return box.body

        def button(parent, text, cmd, primary=False):
            return self.pill(parent, text, cmd, kind="accent" if primary else "ghost")

        # Valorant
        vb = section(t("sec_val"))
        row = tk.Frame(vb, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text=t("region"), bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(side="left")
        regv = tk.StringVar(value=self.settings.get("region", "eu"))
        track = RoundBox(row, r=9, fill=SEG_BG, border=LINE, padx=3, pady=3)
        track.pack(side="left", padx=10)
        pills = track.body
        pill_lbls = {}

        def paint_pills():
            for code, (pb, lb) in pill_lbls.items():
                sel = code == regv.get()
                fill = TAB_SEL if sel else SEG_BG
                pb.paint(fill=fill, border=ACC if sel else fill, outer=SEG_BG)
                lb.config(fg=FG if sel else MUTED)

        def region_changed(_=None):
            self.settings["region"] = regv.get()
            self.settings.pop("henrik_key", None)
            save_json(SETTINGS_FILE, self.settings)
            paint_pills()

        for code in REGIONS:
            pb = RoundBox(pills, r=7, fill=SEG_BG, outer=SEG_BG, cursor="hand2")
            pb.pack(side="left", padx=1)
            lb = pb.add(tk.Label(pb.body, text=code.upper(), bg=SEG_BG, font=("Segoe UI", 8, "bold"), padx=7, pady=2,
                                 cursor="hand2"))
            lb.pack()
            pb.bind_parts("<Button-1>", lambda e, c=code: (regv.set(c), region_changed()))
            pill_lbls[code] = (pb, lb)
        paint_pills()

        def refresh_now():
            region_changed()
            self.set_status(t("val_refreshing"))
            self.refresh_val_async(force=True)
            self.hide_settings()

        button(row, t("refresh_ranks"), refresh_now).pack(side="right")
        tk.Label(vb, text=t("region_auto_hint") + "  " + t("val_nokey"), bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
                 wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))

        # Uygulama
        ab = section(t("sec_app"))
        row = tk.Frame(ab, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text=t("version", v=VERSION), bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(side="left")

        def check_now():
            self.set_status(t("upd_checking"))
            self.check_update_async(manual=True)
            self.hide_settings()

        def shortcut():
            if create_desktop_shortcut(INSTALLED_SCRIPT if os.path.exists(INSTALLED_SCRIPT) else None):
                messagebox.showinfo(t("sc_ok_t"), t("sc_ok"), parent=self)
            else:
                messagebox.showerror(t("err"), t("sc_fail"), parent=self)

        button(row, t("upd_now"), check_now).pack(side="right")
        button(row, t("sc_btn"), shortcut).pack(side="right", padx=8)

        # Veriler
        db = section(t("sec_data"))
        row = tk.Frame(db, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text=t("data_info"), bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=400,
                 justify="left").pack(side="left")

        def open_folder():
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                os.startfile(DATA_DIR)
            except Exception:
                pass

        button(row, t("open_folder"), open_folder).pack(side="right")
        self.bind("<Escape>", lambda e: self.hide_settings())

    def hide_settings(self):
        if self.settings_view is not None:
            self.settings_view.destroy()
            self.settings_view = None
        self.main_view.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.close())
        self.refresh()


TOGGLE_FRAMES = ["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"]

TITLE_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACZklEQVR42rWTy0tUcRTHv+d3ZxxnNCcfkZuKFiE5BgVBEkGzbxEShpseqxK0Fu1yM4KLVmbqxn+gTQ8tWlWLLLWcnCRfhSCaMPlgfMzjzsz93Xt/v9PCSQYJskWf5XlxvofvAQowM2GfFNfSnkQjgGoAvDdXFNskovHdaIRZFJprTcU2/4Xh7z/tsUWrFgAikYgQnYAAABNosQneyeVNmZGuAqAsx1WZvK0sV6ul9aR69mFaOi57oTItABAOdwoq6BHLKXcm66i6+Moam1nLOH38EI4droKtGatbJuaWVkBg5fP5yZbb832JmVOvL13THuxsoCu9evRo0HfSMUuVwRrDXxdw5sQRlAVK8fnbIhxLIpVMwmUh7Oz66JvWG/rqk6fCAEDhcBiu13ivNa77/WUV/lIv11RX0UY6i5HoFDYTCWxvbem8JUXeyq/kcqnLY2/P2c319TuXZmaDiFSKuUkAg/M/NpSrtCGljdjkNGzHgi1dFThQYaRT5pWutubBublZIxRqUAIAiEgxsxEkGoonrKGDVTVGLpdTeSnhahe2dJUvEDDWV1dfdLU1D0bHx41QqEGhoL/IBkyqvPSumTXTJAyyLMmObTMIZFky7Q1W3mFmmojF+HfT7gAi0gBEQ4DiycT2fa+/XEjpKOW6KkAeoZOpjge3muKPerqN9vZ2/UcnAsAws3ER0C8nFka11udnp6bBpvkxcu/mhfkvUVF3tlEV14u9A4KxKIiIS3y+265iDRLaZWoFEXf3+Pb3LH29ax4A6H3+rq+j/3E/AAwM9HvwDxAz06uhjPfhp5GSglsJ/4NfWPpuydfGlREAAAAASUVORK5CYII="

ICON_B64 = "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"

if __name__ == "__main__":
    migrate_old_data()
    JUST_INSTALLED = self_install()
    App().mainloop()
