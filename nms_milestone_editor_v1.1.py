"""No Man's Sky Expedition Milestone Editor (standalone, Windows Steam/GOG saves).

Dev setup:   pip install customtkinter lz4
Build .exe:  pip install pyinstaller
             pyinstaller --onefile --noconsole --icon="atlas_diamond.ico" ^
               --name NMSMilestoneEditor --collect-all customtkinter ^
               --hidden-import lz4.block nms_milestone_editor.py

Pipeline: find save*.hg -> LZ4 block decompress -> locate SeasonData / SeasonState
(obfuscated keys) -> edit ONLY the MilestoneValues numbers in the raw JSON text ->
recompress -> update the encrypted mf_save*.hg metadata -> back up + atomic replace.
Everything is verified before and after writing; on any doubt the app refuses to write.
"""
import json
import math
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field

import customtkinter as ctk
import lz4.block
from tkinter import filedialog
from tkinter.colorchooser import askcolor
from tkinter import simpledialog

import ctypes
                                                                                
try:
    myappid = 'nms.milestone.editor.v1'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass

                                                                                               
def _app_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    for d in (os.path.join(base, "NMSMilestoneEditor"),
              os.path.join(tempfile.gettempdir(), "NMSMilestoneEditor")):
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except OSError:
            continue
    return tempfile.gettempdir()

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)
                                              

APP_DIR = _app_dir()
MAPPING_FILE = os.path.join(APP_DIR, "mapping.json")
CACHE_DIR = os.path.join(APP_DIR, "season_cache")
BACKUP_DIR = os.path.join(APP_DIR, "backups")

RAW = "https://raw.githubusercontent.com/cwmonkey/nms-expeditions/main/"
API_LIST = "https://api.github.com/repos/cwmonkey/nms-expeditions/contents/patched"
LANG_PATH = "_includes/PCBANKS/language/nms_all_usenglish.sorted.json"
SUFFIX = "_LATEST_SEASON_DATA_CACHE.JSON"

                                                                                    
KNOWN = {
    5: "01_PIONEERS", 40: "02_BEACHHEAD", 22: "03_CARTOGRAPHERS", 8: "04_EMERGENCE",
    13: "05_EXOBIOLOGY", 14: "06_THE_BLIGHTED", 15: "07_LEVIATHAN", 16: "08_POLESTAR",
    20: "09_UTOPIA", 21: "10_SINGULARITY", 23: "11_VOYAGERS", 31: "12_OMEGA",
    32: "13_ADRIFT", 33: "14_LIQUIDATORS", 34: "15_AQUARIUS", 35: "16_THE_CURSED",
    41: "17_TITAN", 42: "18_RELICS", 43: "19_CORVETTE", 44: "20_BREACH",
    45: "21_REMNANT", 47: "22_OUR_JOURNEY_CONTINUES", 46: "22_SWARM",
}


def pretty(stem):
    num, _, name = stem.partition("_")
    return f"{num} {name.replace('_', ' ').title()}"


AUTO = "Auto-detect from save"
MENU = {f"{pretty(s)} (ID {i})": i for i, s in sorted(KNOWN.items(), key=lambda kv: kv[1])}
MENU_BY_ID = {i: label for label, i in MENU.items()}

RED, GREEN, GRAY = "#ff5555", "#5fd07a", "#3a3a3a"

                                                               
                                                                                 
                                                                                  
THEMES = {
    "NMS-Core": {"bg": "#0a111e", "row": "#121c2c", "border": "#23395b",
                "text": "#dce8ff", "muted": "#5d7ba3", "amber": "#f2b134",
                "complete": "#00ffc4"},
    "Dark Mode": {"bg": "#2b2b2b", "row": "#333333", "border": "#444444",
                 "text": "#f2f2f2", "muted": "#a0a0a0", "amber": "#f0a030",
                 "complete": "#5fd07a"},
}
KOFI_RED = "#ff2400"
STEAM_URI = "steam://rungameid/275850"
KOFI_URL = "https://ko-fi.com/atlasprotocol"


                                                                                           
class SaveError(Exception):
    pass


MAGIC = 0xFEEDA1E5
HDR = 16
CHUNK = 0x80000
                                                                                   
K_STATE, K_MILE = ("qYy", "SeasonState"), ("psf", "MilestoneValues")
K_DATA, K_ID, K_NUM = ("Rol", "SeasonData"), ("gou", "SeasonId"), (">dq", "SeasonNumber")
NUM_RE = re.compile(rb"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def decompress_blocks(data):
    out, blocks, off = [], [], 0
    while off < len(data):
        if off + HDR > len(data):
            raise SaveError("Save file is truncated (incomplete block header).")
        magic, csz, usz, pad = struct.unpack_from("<IIII", data, off)
        if magic != MAGIC:
            raise SaveError("Unrecognized save format (block magic mismatch).")
        if usz > CHUNK:
            raise SaveError("Corrupted save (oversized block).")
        off += HDR
        if off + csz > len(data):
            raise SaveError("Save file is truncated (incomplete block).")
        try:
            out.append(lz4.block.decompress(data[off:off + csz], uncompressed_size=usz))
        except Exception as e:
            raise SaveError(f"Corrupted save (LZ4 error: {e}).")
        blocks.append((csz, usz, pad))
        off += csz
    return b"".join(out), blocks


def compress_blocks(raw, pad):
    out = bytearray()
    for i in range(0, len(raw), CHUNK):
        chunk = raw[i:i + CHUNK]
        comp = lz4.block.compress(chunk, mode="default", store_size=False)
        out += struct.pack("<IIII", MAGIC, len(comp), len(chunk), pad) + comp
    return bytes(out)


                                                                                     
_M = 0xFFFFFFFF
_DELTA, _RDELTA, META_MAGIC = 0x9E3779B9, 0x61C88647, 0xEEEEEEBE
META_FORMATS = (0x7D2, 0x7D3, 0x7D4)
META_LENS = (0x168, 0x180, 0x1B0)
_KEY = (0x5345414E, 0x44415645, 0x5259414E, 0x47524E54)
ROUNDS = 6


def derive_key(slot):
    x = (slot ^ 0x1422CB8C) & _M
    x = ((x << 13) | (x >> 19)) & _M
    return [(x * 5 + 0xE6546B64) & _M, _KEY[1], _KEY[2], _KEY[3]]


def _mix(cur, prev, key, ki, h):
    t1 = (cur >> 3) ^ ((prev << 4) & _M)
    t2 = ((cur * 4) & _M) ^ (prev >> 5)
    return (((t1 + t2) & _M) ^ (((prev ^ key[ki]) + (cur ^ h)) & _M))


def xxtea_decrypt(d, key, rounds=ROUNDS):
    last, h = len(d) - 1, (rounds * _DELTA) & _M
    for _ in range(rounds):
        ki, cur = (h >> 2) & 3, d[0]
        for j in range(last, 0, -1):
            d[j] = (d[j] - _mix(cur, d[j - 1], key, (j & 3) ^ ki, h)) & _M
            cur = d[j]
        d[0] = (d[0] - _mix(cur, d[last], key, ki, h)) & _M
        h = (h + _RDELTA) & _M


def xxtea_encrypt(d, key, rounds=ROUNDS):
    last, h = len(d) - 1, 0
    for _ in range(rounds):
        h = (h + _DELTA) & _M
        ki = (h >> 2) & 3
        d[0] = (d[0] + _mix(d[1], d[last], key, ki, h)) & _M
        for j in range(1, last + 1):
            nxt = d[0] if j == last else d[j + 1]
            d[j] = (d[j] + _mix(nxt, d[j - 1], key, (j & 3) ^ ki, h)) & _M


def meta_decrypt(mb):
    if len(mb) not in META_LENS:
        raise SaveError(f"Unsupported metadata file size ({len(mb)} bytes).")
    words = list(struct.unpack(f"<{len(mb) // 4}I", mb))
    for slot in range(2, 32):                                                     
        w = words[:]
        xxtea_decrypt(w, derive_key(slot))
        if w[0] == META_MAGIC:
            return w, slot
    raise SaveError("Couldn't decrypt the metadata file (unknown format).")


def meta_encrypt(words, slot):
    w = words[:]
    xxtea_encrypt(w, derive_key(slot))
    return struct.pack(f"<{len(w)}I", *w)


                                         
def find_all(tree, names):
    out, stack = [], [tree]
    while stack:
        x = stack.pop()
        pairs = x.items() if isinstance(x, dict) else enumerate(x) if isinstance(x, list) else ()
        for k, v in pairs:
            if isinstance(x, dict) and k in names:
                out.append(v)
            if isinstance(v, (dict, list)):
                stack.append(v)
    return out


def _pick(d, names):
    for n in names:
        if n in d:
            return d[n]
    return None


def _to_int(v):
    m = re.search(r"\d+", str(v))
    return int(m.group(0)) if m else None


def is_num_list(v):
    return (isinstance(v, list) and len(v) > 0 and
            all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v))


def extract(tree):
    """Returns (values, season_id, id_source) from a parsed save tree."""
    states = [s for s in find_all(tree, K_STATE)
              if isinstance(s, dict) and _pick(s, K_MILE) is not None]
    if not states:
        raise SaveError("No expedition progress in this save (is an expedition active?).")
    if len(states) > 1:
        raise SaveError("Several SeasonState blocks found; refusing to guess.")
    vals = _pick(states[0], K_MILE)
    if not is_num_list(vals):
        raise SaveError("MilestoneValues isn't a numeric array.")
    sid = src = None
    for sd in find_all(tree, K_DATA):
        if not isinstance(sd, dict):
            continue
        for names, label in ((K_ID, "SeasonId"), (K_NUM, "SeasonNumber")):
            v = _pick(sd, names)
            if v is not None and sid is None and _to_int(v) is not None:
                sid, src = _to_int(v), label
    return [float(x) for x in vals], sid, src


def locate_array(raw, vals):
    """Finds the one MilestoneValues array in raw JSON bytes; returns [(start, end, token)]."""
    found = []
    for name in K_MILE:
        pat = rb'"' + re.escape(name.encode()) + rb'"\s*:\s*\[([^\[\]]*)\]'
        for m in re.finditer(pat, raw):
            toks = [(m.start(1) + t.start(), m.start(1) + t.end(), t.group(0))
                    for t in NUM_RE.finditer(m.group(1))]
            if len(toks) == len(vals) and all(float(t[2]) == v for t, v in zip(toks, vals)):
                found.append(toks)
    if len(found) != 1:
        raise SaveError("Couldn't uniquely locate the MilestoneValues array in the save text.")
    return found[0]


                                                              
@dataclass
class SaveEntry:
    path: str
    account: str
    index: int
    mtime: float
    size: int
    newest: bool = False

    @property
    def slot(self):
        return (self.index + 1) // 2


@dataclass
class SaveCtx:
    path: str
    meta_path: str
    raw: bytes
    toks: list
    values: list
    season_id: object
    id_source: object
    sig: tuple
    writable: bool = False
    reason: str = ""
    comp_mode: str = ""
    pad: int = 0
    words: list = field(default_factory=list)
    meta_slot: int = 0
    meta_sig: tuple = ()
    account: str = "default"


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _sig(path):
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


def read_save(path, account="default"):
    data = _read(path)
    sig = _sig(path)
    if not data:
        raise SaveError("This save file is empty.")
    if data[:1] == b"{":
        raise SaveError("This save is plain JSON (from another tool). Only the game's own "
                        "compressed format is supported.")
    raw, blocks = decompress_blocks(data)
    try:
        tree = json.loads(raw.decode("utf-8").rstrip("\x00"))
    except ValueError as e:
        raise SaveError(f"Save data is corrupted or in an unknown layout ({e}).")
    values, sid, src = extract(tree)
    del tree
    toks = locate_array(raw, values)
    meta_path = os.path.join(os.path.dirname(path), "mf_" + os.path.basename(path))
    ctx = SaveCtx(path, meta_path, raw, toks, values, sid, src, sig, account=account)
    try:                                                                       
        if not os.path.isfile(meta_path):
            raise SaveError(f"Metadata file {os.path.basename(meta_path)} is missing.")
        mb = _read(meta_path)
        words, slot = meta_decrypt(mb)
        if words[1] not in META_FORMATS:
            raise SaveError(f"Unsupported metadata format 0x{words[1]:X}.")
        if words[14] != len(raw):
            raise SaveError(f"Metadata size field ({words[14]}) doesn't match the save "
                            f"({len(raw)}); unknown layout.")
        payload = sum(b[0] for b in blocks)
        if words[15] == len(data):
            ctx.comp_mode = "file"
        elif words[15] == payload:
            ctx.comp_mode = "payload"
        else:
            raise SaveError(f"Metadata compressed-size field ({words[15]}) matches neither "
                            f"{len(data)} nor {payload}; unknown layout.")
        pads = {b[2] for b in blocks}
        if len(pads) != 1:
            raise SaveError("Inconsistent block headers; unknown layout.")
        ctx.pad, ctx.words, ctx.meta_slot = pads.pop(), words, slot
        ctx.meta_sig = _sig(meta_path)
        ctx.writable = True
    except SaveError as e:
        ctx.reason = str(e)
    return ctx


def nms_running():
    """True/False, or None if it can't be determined."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq NMS.exe", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000).stdout
        return "NMS.exe" in out
    except Exception:
        return None


def fmt(v):
    return repr(float(v))


def _token(new, old):
    if not re.search(rb"[.eE]", old) and float(new).is_integer() and abs(new) < 1e15:
        return str(int(new)).encode()
    return repr(float(new)).encode()


def patch_raw(ctx, new_vals):
    out, last = [], 0
    for (a, b, _), old, new in zip(ctx.toks, ctx.values, new_vals):
        if new != old:
            out += [ctx.raw[last:a], _token(new, ctx.raw[a:b])]
            last = b
    out.append(ctx.raw[last:])
    return b"".join(out)


def write_save(ctx, new_vals):
    """Backs up, then writes save + metadata. Returns the backup folder."""
    if not ctx.writable:
        raise SaveError(f"Writing is disabled for this save: {ctx.reason}")
    if len(new_vals) != len(ctx.values) or not all(math.isfinite(v) for v in new_vals):
        raise SaveError("Values must be finite numbers.")
    if nms_running():
        raise SaveError("No Man's Sky is running. Close the game completely, then try again.")
    if _sig(ctx.path) != ctx.sig or _sig(ctx.meta_path) != ctx.meta_sig:
        raise SaveError("The save changed on disk since it was loaded. Re-select it and retry.")

    new_raw = patch_raw(ctx, new_vals)
    try:                                                                   
        got, _, _ = extract(json.loads(new_raw.decode("utf-8").rstrip("\x00")))
    except (ValueError, SaveError) as e:
        raise SaveError(f"Internal check failed on edited data: {e}")
    if got != [float(v) for v in new_vals]:
        raise SaveError("Internal check failed: edited values didn't round-trip.")

    blob = compress_blocks(new_raw, ctx.pad)
    if decompress_blocks(blob)[0] != new_raw:
        raise SaveError("Internal check failed: recompressed data didn't round-trip.")
    words = ctx.words[:]
    words[14] = len(new_raw)
    words[15] = len(blob) if ctx.comp_mode == "file" else sum(
        b[0] for b in decompress_blocks(blob)[1])
    meta_blob = meta_encrypt(words, ctx.meta_slot)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BACKUP_DIR, re.sub(r"[^\w.-]", "_", ctx.account), stamp)
    os.makedirs(bdir, exist_ok=True)
    bk_s = os.path.join(bdir, os.path.basename(ctx.path))
    bk_m = os.path.join(bdir, os.path.basename(ctx.meta_path))
    shutil.copy2(ctx.path, bk_s)
    shutil.copy2(ctx.meta_path, bk_m)
    if os.path.getsize(bk_s) != ctx.sig[1] or os.path.getsize(bk_m) != ctx.meta_sig[1]:
        raise SaveError("Backup verification failed; nothing was changed.")

    tmps = [ctx.path + ".nmsedit.tmp", ctx.meta_path + ".nmsedit.tmp"]
    try:
        for p, b in zip(tmps, (blob, meta_blob)):
            with open(p, "wb") as f:
                f.write(b)
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmps[0], ctx.path)
        os.replace(tmps[1], ctx.meta_path)
        chk = read_save(ctx.path, ctx.account)                                
        if chk.values != [float(v) for v in new_vals] or not chk.writable:
            raise SaveError("Post-write verification failed.")
    except Exception as e:
        shutil.copy2(bk_s, ctx.path)
        shutil.copy2(bk_m, ctx.meta_path)
        raise SaveError(f"Write failed, original files restored ({e}).")
    finally:
        for p in tmps:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    return bdir


                                                                
SAVE_RE = re.compile(r"^save(\d*)\.hg$", re.I)


def scan_dir(d, account):
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        m = SAVE_RE.match(n)
        p = os.path.join(d, n)
        if not m or not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_size > 0:                                      
            out.append(SaveEntry(p, account, int(m.group(1) or 1), st.st_mtime, st.st_size))
    return out


def discover(root):
    entries = scan_dir(root, os.path.basename(root.rstrip("\\/")) or root)
    try:
        subs = sorted(os.listdir(root))
    except OSError:
        subs = []
    for s in subs:
        p = os.path.join(root, s)
        if os.path.isdir(p):
            entries += scan_dir(p, s)
    best = {}
    for e in entries:
        k = (e.account, e.slot)
        if k not in best or e.mtime > best[k].mtime:
            best[k] = e
    for e in entries:
        e.newest = best[(e.account, e.slot)] is e
    return sorted(entries, key=lambda e: (e.account, e.index))


def default_roots():
    roots = []
    if os.environ.get("NMS_SAVE_DIR"):
        roots.append(os.environ["NMS_SAVE_DIR"])
    if os.environ.get("APPDATA"):
        roots.append(os.path.join(os.environ["APPDATA"], "HelloGames", "NMS"))
    return roots


def gamepass_present():
    import glob
    base = os.environ.get("LOCALAPPDATA")
    return bool(base and glob.glob(os.path.join(base, "Packages", "HelloGames.NoMansSky*")))


                                                                                       
class NoMapError(Exception):
    pass


def http_get(url, timeout=20, tries=2):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nms-milestone-editor"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
    raise last


def get_json(url, timeout=20):
    return json.loads(http_get(url, timeout).decode("utf-8-sig"))


def load_season(sid):
    if sid in KNOWN:
        name = KNOWN[sid] + SUFFIX
        return get_json(RAW + "patched/" + urllib.parse.quote(name)), name
    try:
        listing = get_json(API_LIST)
    except Exception as e:
        raise NoMapError(f"ID {sid} isn't in the built-in list and discovery failed ({e}). "
                         "Pick the expedition from the dropdown.")
    skip = {k + SUFFIX for k in KNOWN.values()}
    names = [x["name"] for x in listing if x["name"].endswith("_SEASON_DATA_CACHE.JSON")
             and "_REDUX_" not in x["name"] and x["name"] not in skip]
    names.sort(key=lambda n: (-int(n[:2]) if n[:2].isdigit() else 0, "_LATEST_" not in n))
    for n in names[:6]:
        d = get_json(RAW + "patched/" + urllib.parse.quote(n))
        if d.get("SeasonId") == sid:
            return d, n
    raise NoMapError(f"No online map for expedition ID {sid} yet.")


def fetch_missions(sid, progress):
    """{'stem', 'missions': [{stage, title, amount}], 'cached'} in engine (flattened) order."""
    cache = os.path.join(CACHE_DIR, f"season_{sid}_v2.json")
    try:
        with open(cache, "r", encoding="utf-8") as f:
            c = json.load(f)
        return {"stem": c["stem"], "missions": c["missions"], "cached": True}
    except (OSError, ValueError, KeyError):
        pass
    progress("Fetching expedition file...")
    data, fname = load_season(sid)
    progress("Downloading English dictionary (first lookup only, ~8 MB)...")
    lang = get_json(RAW + LANG_PATH, timeout=120)
    missions = []
    for si, stage in enumerate(data.get("Stages", []), 1):
        for m in stage.get("Milestones", []):
            key = (m.get("Title") or "").lstrip("^")
            missions.append({"stage": si, "title": lang.get(key, key),
                             "amount": m.get("Amount", 1)})
    stem = re.sub(r"_(LATEST|ORIGINAL)_SEASON_DATA_CACHE\.JSON$", "", fname)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = cache + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"stem": stem, "missions": missions}, f)
        os.replace(tmp, cache)
    except OSError:
        pass
    return {"stem": stem, "missions": missions, "cached": False}


def goal_of(m):
    a = m.get("amount", 1)
    return float(a) if isinstance(a, (int, float)) and a > 1 else 1.0


def goal_text(m):
    g = goal_of(m)
    if g == 1.0:
        return "1.0 (Complete)"
    return f"{int(g):,}" if g.is_integer() else f"{g:g}"


                                                     
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
                                                           
        self.title("ATLAS_PROTOCOL // PROGRESS_MATRIX_v1.1")

                                                                                
        icon_path = get_resource_path("atlas_diamond.ico")

        try:
            self.iconbitmap(icon_path)
        except Exception:
            pass

        self.geometry("1080x820")

                                                                           
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlas_diamond.ico")
        if os.path.exists(icon_path):
            try:
                                                                                             
                self.after(200, lambda: self.iconbitmap(icon_path))
            except Exception:
                pass
                                                                           

        self.geometry("1080x820")
        self.minsize(900, 580)

                                                               
        self.theme_name = "NMS-Core"
        self.custom_colors = dict(THEMES["NMS-Core"])                                     
        self.colors = dict(THEMES["NMS-Core"])

        self.rows, self.slots = [], {}
        self.ctx = None
        self.notes, self.user_themes = self.load_notes()
        self.season_id, self.tag = None, "[NO SAVE]"
        self.missions, self.online = None, {}
        self.load_tok = self.look_tok = 0
        self.q = queue.Queue()                                           
        self.busy = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.build_top()
        self.scroll = ctk.CTkScrollableFrame(self, label_text="")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        self.scroll.grid_columnconfigure(3, weight=1)
        self.header()
        self.build_bottom()

        self.apply_theme_colors()                                           

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_id = self.after(100, self.poll)
        self.after(150, self.rescan)

                                  
    def build_top(self):
        self.top = ctk.CTkFrame(self)
        self.top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        self.top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.top, text="Select Active Save Profile Slot",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        r1 = ctk.CTkFrame(self.top, fg_color="transparent")
        r1.grid(row=1, column=0, sticky="ew", padx=10, pady=2)
        self.slot_menu = ctk.CTkOptionMenu(r1, values=["(scanning...)"], width=620,
                                           command=self.on_slot)
        self.slot_menu.pack(side="left", padx=(2, 8))
        for txt, cmd in (("Rescan", self.rescan), ("Browse...", self.browse)):
            ctk.CTkButton(r1, text=txt, width=90, fg_color=GRAY, hover_color="#4a4a4a",
                          command=cmd).pack(side="left", padx=(0, 8))
        r2 = ctk.CTkFrame(self.top, fg_color="transparent")
        r2.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 4))
        self.menu = ctk.CTkOptionMenu(r2, values=[AUTO] + list(MENU), width=250,
                                      command=self.on_menu)
        self.menu.set(AUTO)
        self.menu.pack(side="left", padx=(2, 8))
        ctk.CTkButton(r2, text="Bulk Max All Rows", fg_color="#d6336c", hover_color="#b02a5b",
                      command=self.bulk_max).pack(side="left", padx=(0, 8))
        ctk.CTkButton(r2, text="Reset Original", fg_color=GRAY, hover_color="#4a4a4a",
                      command=self.reset_original).pack(side="left")

                                       
        r3 = ctk.CTkFrame(self.top, fg_color="transparent")
        r3.grid(row=3, column=0, sticky="ew", padx=10, pady=(4, 10))
        ctk.CTkLabel(r3, text="Theme Preset:").pack(side="left", padx=(2, 6))
        self.theme_menu = ctk.CTkOptionMenu(
            r3, values=list(THEMES) + sorted(self.user_themes) + ["Custom Designer"],
            width=160, command=self.on_theme_preset)
        self.theme_menu.set(self.theme_name)
        self.theme_menu.pack(side="left", padx=(0, 8))
        ctk.CTkButton(r3, text="\U0001F3A8 Main BG", width=110, fg_color=GRAY,
                      hover_color="#4a4a4a", command=lambda: self.pick_color("bg")).pack(
            side="left", padx=(0, 6))
        ctk.CTkButton(r3, text="\U0001F3A8 Slots", width=100, fg_color=GRAY,
                      hover_color="#4a4a4a", command=lambda: self.pick_color("row")).pack(
            side="left", padx=(0, 6))
        self.save_theme_btn = ctk.CTkButton(r3, text="\U0001F4BE Save Theme", width=130,
                                            command=self.save_custom_theme)
        self.save_theme_btn.pack(side="left", padx=(0, 12))
        ctk.CTkButton(r3, text="[ \u2615 Support me on Ko-fi ]", fg_color=KOFI_RED,
                      hover_color="#cc1d00",
                      command=lambda: webbrowser.open(KOFI_URL)).pack(side="left")

    def build_bottom(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 12))
        bar.grid_columnconfigure(2, weight=1)
        self.save_btn = ctk.CTkButton(bar, text="Save Changes directly to Game", height=40,
                                      font=ctk.CTkFont(size=14, weight="bold"),
                                      fg_color="#2b8a3e", hover_color="#237032",
                                      command=self.save_to_game)
        self.save_btn.grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(bar, text="\U0001F680 Launch No Man's Sky", height=40, fg_color=GRAY,
                      hover_color="#4a4a4a", command=self.launch_steam).grid(
            row=0, column=1, padx=(0, 12))
        self.status = ctk.CTkLabel(bar, text="Looking for your saves...",
                                   anchor="w", justify="left", wraplength=740)
        self.status.grid(row=0, column=2, sticky="ew")

    def header(self):
        self.head_labels = []
        cols = [("Index", 110), ("Current Value", 130), ("Max Goal Value", 150),
                ("Milestone Objective", 0), ("Original Record", 110)]
        for c, (txt, w) in enumerate(cols):
            lb = ctk.CTkLabel(self.scroll, text=txt, width=w, anchor="w")
            lb.grid(row=0, column=c, sticky="w", padx=6)
            self.head_labels.append(lb)

    def set_status(self, msg, color=None):
        self.status.configure(text=msg, text_color=color or self.colors["muted"])

    def alert(self, title, msg, ok=True):
        w = ctk.CTkToplevel(self)
        w.title(title)
        w.geometry("500x250")
        w.configure(fg_color=self.colors["bg"])
        w.transient(self)
        col = GREEN if ok else RED
        ctk.CTkLabel(w, text=("\u2714  " if ok else "\u2716  ") + title, text_color=col,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(22, 8))
        ctk.CTkLabel(w, text=msg, wraplength=450, justify="left",
                     text_color=self.colors["text"]).pack(padx=24, pady=4)
        ctk.CTkButton(w, text="OK", width=110, command=w.destroy).pack(pady=18)

        def grab():
            try:
                w.grab_set()
                w.focus()
            except Exception:
                pass
        w.after(200, grab)

                                   
    def on_theme_preset(self, choice):
        self.theme_name = choice
        if choice == "Custom Designer":
            self.colors = dict(self.custom_colors)
        elif choice in THEMES:
            self.colors = dict(THEMES[choice])
        elif choice in self.user_themes:
            self.colors = dict(self.user_themes[choice])
        else:
            return
        self.apply_theme_colors()

    def pick_color(self, key):
        rgb, hexval = askcolor(color=self.colors.get(key, "#000000"),
                               title=f"Choose {key} color")
        if not hexval:
            return
        self.custom_colors = dict(self.colors)                                           
        self.custom_colors[key] = hexval
        self.theme_name = "Custom Designer"
        self.theme_menu.set("Custom Designer")
        self.colors = dict(self.custom_colors)
        self.apply_theme_colors()

    def save_custom_theme(self):
        name = simpledialog.askstring("Save Theme", "Name your custom theme:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in THEMES:
            self.set_status(f'"{name}" is a built-in theme name; pick another.', RED)
            return
        self.user_themes[name] = dict(self.colors)                                             
        self.save_notes()
        self.theme_menu.configure(
            values=list(THEMES) + sorted(self.user_themes) + ["Custom Designer"])
        self.theme_menu.set(name)
        self.theme_name = name
        self.set_status(f'Saved custom theme "{name}".', self.colors["complete"])

    @staticmethod
    def _lighten(hexcolor, amt):
        h = hexcolor.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return "#{:02x}{:02x}{:02x}".format(*(min(255, x + amt) for x in (r, g, b)))

    def _row_hover(self, idx, entering):
        base_color = "transparent" if self.theme_menu.get() == "NMS-Core" else self.colors.get("row", "transparent")
        hover_color = "#283242" if self.theme_menu.get() == "NMS-Core" else "#3d3d3d"
        
        if idx < len(self.rows):
                                                                                                                        
            for col in (0, 2, 3):
                for w in self.scroll.grid_slaves(row=idx + 1, column=col):
                    if w:
                        w.configure(fg_color=hover_color if entering else base_color)

    def _paint_row_cells(self, row_idx, target_color):
        """Helper method to forcefully paint cells in columns 0, 2, and 3."""
        if row_idx < len(self.rows):
            for col in (0, 2, 3):
                slaves = self.scroll.grid_slaves(row=row_idx + 1, column=col)
                for w in slaves:
                    if w:
                        w.configure(fg_color=target_color)
        
                                                                                          
                                                                                
        if idx < len(self.rows):
                                                            
            item = self.rows[idx]
                                                                                                
                                                                                                                
            widgets_to_paint = [self.scroll.grid_slaves(row=idx+1, column=0)[0], 
                                self.scroll.grid_slaves(row=idx+1, column=2)[0], 
                                self.scroll.grid_slaves(row=idx+1, column=3)[0]]
            
                                                             
            for w in widgets_to_paint:
                w.configure(fg_color=hover_color if entering else base_color)

    def apply_theme_colors(self):
        """Repaints the window, containers, and every live row -- no restart needed."""
        c = self.colors
        try:
            self.configure(fg_color=c["bg"])
            self.top.configure(fg_color=c["row"], border_width=1, border_color=c["border"])
            self.scroll.configure(fg_color=c["bg"], border_width=1, border_color=c["border"],
                                  scrollbar_button_color=c["border"])
            for lb in getattr(self, "head_labels", []):
                lb.configure(text_color=c["muted"])
            self.status.configure(text_color=c["muted"])
            self.save_theme_btn.configure(fg_color=c["complete"], hover_color=c["amber"],
                                          text_color="#0a0a0a")
            for r in self.rows:
                r["ent"].configure(fg_color=c["row"], border_color=c["border"],
                                   text_color=(c["amber"] if r["edited"] else c["text"]))
                r["lbl"].configure(fg_color=c["row"], border_color=c["border"],
                                   text_color=c["text"])
                r["goal_lbl"].configure(text_color=c["muted"])
                r["idx_lbl"].configure(text_color=c["muted"])
                r["orig_lbl"].configure(text_color=c["muted"])
        except Exception:
            pass                                                    

    def launch_steam(self):
        try:
            if os.name == "nt":
                subprocess.Popen(["cmd", "/c", "start", "", STEAM_URI], shell=False)
            else:
                webbrowser.open(STEAM_URI)
            self.set_status(f"{self.tag} Launching Steam...")
        except Exception as e:
            self.set_status(f"{self.tag} Couldn't launch Steam: {e}", RED)

                                          
    def key(self):
        return str(self.season_id) if self.season_id is not None else "default"

    def load_notes(self):
        """Returns (season_notes, user_themes). 'user_themes' is a sibling top-level key;
        everything else in mapping.json is per-expedition milestone notes as before."""
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {}, {}
        if not isinstance(raw, dict):
            return {}, {}
        themes = raw.pop("user_themes", {})
        if not isinstance(themes, dict):
            themes = {}
        if raw and all(isinstance(v, str) for v in raw.values()):
            notes = {"default": {str(k): v for k, v in raw.items()}}
        else:
            notes = {str(k): {str(i): str(t) for i, t in v.items()}
                     for k, v in raw.items() if isinstance(v, dict)}
        return notes, themes

    def save_notes(self, *_):
        if self.rows:
            b = self.notes.setdefault(self.key(), {})
            for i, r in enumerate(self.rows):
                t = r["lbl"].get().strip()
                if t and t != self.online.get(i):
                    b[str(i)] = t
                else:
                    b.pop(str(i), None)
            if not b:
                self.notes.pop(self.key(), None)
        try:
            with open(MAPPING_FILE, "w", encoding="utf-8") as f:
                json.dump({**self.notes, "user_themes": self.user_themes}, f, indent=2)
        except OSError:
            pass

                                     
    def rescan(self, roots=None):
        entries = []
        for root in (roots or default_roots()):
            try:
                entries += discover(root)
            except Exception:
                continue
        seen, uniq = set(), []
        for e in entries:
            rp = os.path.realpath(e.path)
            if rp not in seen:
                seen.add(rp)
                uniq.append(e)
        self.slots = {}
        for e in uniq:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.mtime))
            star = "  \u2605 newest" if e.newest else ""
            self.slots[f"{e.account}  |  Slot {e.slot}  |  {os.path.basename(e.path)}  |  "
                       f"{when}{star}"] = e
        if not self.slots:
            self.slot_menu.configure(values=["(no saves found)"])
            self.slot_menu.set("(no saves found)")
            gp = ("Game Pass / Microsoft Store saves use a different container format that "
                  "this tool can't edit. " if gamepass_present() else "")
            self.set_status(f"{gp}No save files found in %APPDATA%\\HelloGames\\NMS. "
                            "Use Browse... to point at a folder containing save.hg.",
                            self.colors["amber"])
            return
        labels = list(self.slots)
        self.slot_menu.configure(values=labels)
        best = max(labels, key=lambda k: self.slots[k].mtime)
        self.slot_menu.set(best)
        self.on_slot(best)

    def browse(self):
        d = filedialog.askdirectory(title="Select your NMS save folder (or an st_ folder)")
        if not d:
            return
        self._clear_rows("Loading save data from the selected folder...")                    
        self.rescan([d])

    def _clear_rows(self, msg=""):
        for w in self.scroll.winfo_children():
            w.destroy()
        self.rows = []
        self.header()
        if msg:
            self.set_status(msg)

                                                   
    def on_slot(self, label):
        e = self.slots.get(label)
        if not e:
            return
        self.load_tok += 1
        self.look_tok += 1
        self.set_status(f"Reading {os.path.basename(e.path)}...")
        threading.Thread(target=self.load_worker, args=(e, self.load_tok), daemon=True).start()

    def load_worker(self, e, tok):
        try:
            self.q.put(("load", tok, read_save(e.path, e.account)))
        except SaveError as ex:
            self.q.put(("load_err", tok, str(ex)))
        except Exception as ex:                                     
            self.q.put(("load_err", tok, f"Unexpected error reading save: {ex!r}"))

    def on_loaded(self, ctx):
        self.ctx = ctx
        sid, src = ctx.season_id, ctx.id_source
        if sid is not None:
            self.menu.set(MENU_BY_ID.get(sid, AUTO))
        else:
            sid = MENU.get(self.menu.get())
            src = "dropdown" if sid is not None else None
        self.set_season(sid)
        self.build_rows(ctx.values)                                         
        ro = "" if ctx.writable else f"  READ-ONLY: {ctx.reason}"
        if sid is None:
            self.set_status(f"{self.tag} {len(ctx.values)} milestones loaded. No SeasonId in "
                            f"this save: pick the expedition from the dropdown.{ro}",
                            self.colors["amber"])
        else:
            self.start_lookup(src, ro)

    def set_season(self, sid):
        self.season_id, self.missions, self.online = sid, None, {}
        stem = KNOWN.get(sid)
        self.tag = (f"[{stem.replace('_', ' ')}]" if stem else
                    f"[ID {sid}]" if sid is not None else "[NO EXPEDITION]")

    def build_rows(self, values):
        self._clear_rows()
        bucket = self.notes.get(self.key(), {})
        c = self.colors
        for i, v in enumerate(values):
            n = i + 1
            idx_lbl = ctk.CTkLabel(self.scroll, text=f"Milestone #{i}", width=110, anchor="w",
                                   text_color=c["muted"])
            idx_lbl.grid(row=n, column=0, sticky="w", padx=6, pady=2)
            var = ctk.StringVar(value=fmt(v))
            ent = ctk.CTkEntry(self.scroll, textvariable=var, width=130,
                               fg_color=c["row"], border_color=c["border"])
            ent.grid(row=n, column=1, padx=6, pady=2)
            var.trace_add("write", lambda *_, i=i: self.refresh_color(i))
            goal = ctk.CTkLabel(self.scroll, text="\u2014", width=150, anchor="w",
                                text_color=c["muted"])
            goal.grid(row=n, column=2, sticky="w", padx=6)
            lbl = ctk.CTkEntry(self.scroll, placeholder_text=f"Background Tracker Node #{i}",
                               fg_color=c["row"], border_color=c["border"])
            lbl.grid(row=n, column=3, sticky="ew", padx=6, pady=2)
            if str(i) in bucket:
                lbl.insert(0, bucket[str(i)])
            lbl.bind("<FocusOut>", self.save_notes)
            orig_lbl = ctk.CTkLabel(self.scroll, text=fmt(v), width=110, anchor="w",
                                    text_color=c["muted"])
            orig_lbl.grid(row=n, column=4, sticky="w", padx=6)
            self.rows.append({"orig": v, "var": var, "ent": ent, "lbl": lbl, "goal_lbl": goal,
                              "idx_lbl": idx_lbl, "orig_lbl": orig_lbl, "goal": None,
                              "edited": False})

                                                                                           
                                                                           
            for w in (idx_lbl,):
                w.bind("<Enter>", lambda e, i=i: self._row_hover(i, True))
                w.bind("<Leave>", lambda e, i=i: self._row_hover(i, False))
                                                                                               
            ent.bind("<Tab>", lambda e, i=i: self._tab_step(i, 1))
            ent.bind("<Shift-Tab>", lambda e, i=i: self._tab_step(i, -1))

    def _tab_step(self, i, direction):
        j = i + direction
        if 0 <= j < len(self.rows):
            self.rows[j]["ent"].focus_set()
        return "break"

    def refresh_color(self, i):
        r = self.rows[i]
        try:
            r["edited"] = float(r["var"].get()) != r["orig"]
            color = self.colors["amber"] if r["edited"] else self.colors["text"]
        except ValueError:
            r["edited"] = True
            color = RED
        r["ent"].configure(text_color=color)

                                         
    def on_menu(self, choice):
        sid = MENU.get(choice)
        if sid is None or not self.rows:
            return
        self.save_notes()
        self.set_season(sid)
        bucket = self.notes.get(self.key(), {})
        for i, r in enumerate(self.rows):
            r["lbl"].delete(0, "end")
            r["goal_lbl"].configure(text="\u2014")
            r["goal"] = None
            if str(i) in bucket:
                r["lbl"].insert(0, bucket[str(i)])
        self.start_lookup("dropdown", "")

    def start_lookup(self, src, ro):
        self.look_tok += 1
        self.set_status(f"{self.tag} Syncing online... (ID from {src}){ro}")
        threading.Thread(target=self.look_worker, args=(self.season_id, self.look_tok),
                         daemon=True).start()

    def look_worker(self, sid, tok):
        try:
            res = fetch_missions(sid, lambda m: self.q.put(("look_prog", tok, m)))
            self.q.put(("look", tok, res))
        except NoMapError as e:
            self.q.put(("look_missing", tok, str(e)))
        except Exception as e:
            self.q.put(("look_off", tok, str(e)))

    def apply_missions(self, res):
        ms = res["missions"]
        self.tag = f"[{res['stem'].replace('_', ' ')}]"
        if len(ms) != len(self.rows):
            self.set_status(f"{self.tag} Map has {len(ms)} milestones but your save has "
                            f"{len(self.rows)}. Names NOT applied (wrong expedition?).",
                            self.colors["amber"])
            return
        self.missions = ms
        bucket = self.notes.get(self.key(), {})
        for i, (m, r) in enumerate(zip(ms, self.rows)):
            text = f"P{m['stage']}: {m['title']}"
            self.online[i] = text
            r["goal"] = goal_of(m)
            r["goal_lbl"].configure(text=goal_text(m))
            if str(i) not in bucket and not r["lbl"].get().strip():
                r["lbl"].insert(0, text)
        how = "Loaded from local cache" if res["cached"] else "Online Sync Connected"
        ro = "" if self.ctx and self.ctx.writable else f"  READ-ONLY: {self.ctx.reason}"
        self.set_status(f"{self.tag} {how} | Done.{ro}",
                        self.colors["complete"] if not ro else self.colors["amber"])

                                                         
    def poll(self):
        try:
            while True:
                kind, tok, payload = self.q.get_nowait()
                if kind.startswith("look") and tok != self.look_tok:
                    continue
                if kind.startswith("load") and tok != self.load_tok:
                    continue
                self.handle(kind, payload)
        except queue.Empty:
            pass
        self.poll_id = self.after(100, self.poll)

    def handle(self, kind, p):
        if kind == "load":
            self.on_loaded(p)
        elif kind == "load_err":
            self.ctx = None
            self.set_status(f"Couldn't read this save: {p}", RED)
        elif kind == "look_prog":
            self.set_status(f"{self.tag} {p}")
        elif kind == "look":
            self.apply_missions(p)
        elif kind == "look_missing":
            self.set_status(f"{self.tag} {p}", self.colors["amber"])
        elif kind == "look_off":
            self.set_status(f"{self.tag} Offline ({p[:70]}). Manual notes still work.", RED)
        elif kind == "saved":
            self.busy = False
            self.save_btn.configure(state="normal")
            self.set_status(f"{self.tag} Save updated. Backup: {p}", self.colors["complete"])
            self.alert("Save File Updated Successfully!",
                       f"Your milestones were written to the game.\n\nBackup of the originals:\n{p}")
            e = self.slots.get(self.slot_menu.get())
            if e:
                self.on_slot(self.slot_menu.get())                                        
        elif kind == "save_err":
            self.busy = False
            self.save_btn.configure(state="normal")
            self.set_status(f"{self.tag} Save failed: {p}", RED)
            self.alert("Couldn't Save", p, ok=False)

                                   
    def bulk_max(self):
        if not self.rows:
            self.set_status("Load a save first.", RED)
            return
        if not self.missions:
            self.set_status(f"{self.tag} Bulk Max needs the Max Goal column (online sync). "
                            "Wait for sync or pick an expedition.", self.colors["amber"])
            return
        n = 0
        for r in self.rows:
            if r["goal"] is None:
                continue
            try:
                cur = float(r["var"].get())
            except ValueError:
                cur = 0.0
            if cur < r["goal"]:                                                           
                r["var"].set(fmt(r["goal"]))
                n += 1
        self.set_status(f"{self.tag} Bulk Max raised {n} rows to their goal.",
                        self.colors["complete"])

    def reset_original(self):
        for r in self.rows:
            r["var"].set(fmt(r["orig"]))
        self.set_status(f"{self.tag} All values reset to original.")

    def save_to_game(self):
        if self.busy:
            return
        if not self.ctx:
            self.alert("Nothing Loaded", "Select a save slot first.", ok=False)
            return
        vals = []
        for i, r in enumerate(self.rows):
            try:
                v = float(r["var"].get())
                if not math.isfinite(v):
                    raise ValueError
                vals.append(v)
            except ValueError:
                self.set_status(f"Milestone #{i} isn't a valid number.", RED)
                r["ent"].focus()
                return
        if vals == self.ctx.values:
            self.alert("No Changes", "Every value matches the save already.", ok=False)
            return
        self.save_notes()
        self.busy = True
        self.save_btn.configure(state="disabled")
        self.set_status(f"{self.tag} Writing save...")
        threading.Thread(target=self.save_worker, args=(self.ctx, vals), daemon=True).start()

    def save_worker(self, ctx, vals):
        try:
            self.q.put(("saved", 0, write_save(ctx, vals)))
        except SaveError as e:
            self.q.put(("save_err", 0, str(e)))
        except Exception as e:
            self.q.put(("save_err", 0, f"Unexpected error: {e!r}. Originals are untouched "
                                       "or restored from backup."))

    def on_close(self):
        if self.poll_id:
            self.after_cancel(self.poll_id)
        self.load_tok += 1
        self.look_tok += 1
        self.save_notes()
        self.destroy()


if __name__ == "__main__":
    try:
        App().mainloop()
    except Exception as exc:                                                                 
        try:
            with open(os.path.join(APP_DIR, "crash.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.ctime()}: {exc!r}\n")
        finally:
            sys.exit(1)