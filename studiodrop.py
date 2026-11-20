#!/usr/bin/env python3
"""
StudioDrop — обмен файлами и текстом между устройствами в одной Wi-Fi сети.

Запуск:   python studiodrop.py
Дальше:   на ПК откроется страница с QR-кодом. Наведите камеру телефона на QR —
          и можно отправлять/забирать файлы. Телефону ничего устанавливать не нужно.

Только стандартная библиотека Python 3.8+, без установки пакетов.
Полученные файлы лежат в папке  ~/StudioDrop  (Загрузки не трогаем).
"""
import argparse
import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import shutil
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

for _name in ("stdout", "stderr"):  # без консоли (.exe/.app) потока нет; на Windows кодировка не знает кириллицу
    _stream = getattr(sys, _name)
    if _stream is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
    else:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

FROZEN = getattr(sys, "frozen", False)

# ───────────────────────────── QR-код (без библиотек) ─────────────────────────────


def make_qr_svg(text):
    """Минимальный генератор QR (байтовый режим, коррекция L, версии 1–5)."""
    data = text.encode("utf-8")
    table = [(1, 19, 7), (2, 34, 10), (3, 55, 15), (4, 80, 20), (5, 108, 26)]
    for ver, dcw, ecw in table:
        if len(data) <= dcw - 2:
            break
    else:
        raise ValueError("Слишком длинный текст для QR")

    # --- данные ---
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(4, 4)
    put(len(data), 8)
    for b in data:
        put(b, 8)
    put(0, min(4, dcw * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    i = 0
    while len(cw) < dcw:
        cw.append((0xEC, 0x11)[i % 2])
        i += 1

    # --- Рид-Соломон ---
    exp, log = [0] * 512, [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]

    def mul(a, b):
        return 0 if a == 0 or b == 0 else exp[log[a] + log[b]]

    gen = [1]
    for i in range(ecw):
        ng = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            ng[j] ^= c
            ng[j + 1] ^= mul(c, exp[i])
        gen = ng
    res = [0] * ecw
    for b in cw:
        f = b ^ res[0]
        res = res[1:] + [0]
        for k in range(ecw):
            res[k] ^= mul(gen[k + 1], f)
    allcw = cw + res

    # --- матрица ---
    n = ver * 4 + 17
    mod = [[False] * n for _ in range(n)]
    isf = [[False] * n for _ in range(n)]

    def setf(x, y, v):
        mod[y][x] = v
        isf[y][x] = True

    for i in range(n):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)

    def finder(cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < n and 0 <= y < n:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))

    finder(3, 3)
    finder(n - 4, 3)
    finder(3, n - 4)
    if ver >= 2:
        c = 4 * ver + 10
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                setf(c + dx, c + dy, max(abs(dx), abs(dy)) != 1)

    def format_cells(mask):
        d = (1 << 3) | mask  # уровень L = 01
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        fb = ((d << 10) | rem) ^ 0x5412
        g = lambda i: (fb >> i) & 1 == 1
        cells = []
        for i in range(0, 6):
            cells.append((8, i, g(i)))
        cells += [(8, 7, g(6)), (8, 8, g(7)), (7, 8, g(8))]
        for i in range(9, 15):
            cells.append((14 - i, 8, g(i)))
        for i in range(0, 8):
            cells.append((n - 1 - i, 8, g(i)))
        for i in range(8, 15):
            cells.append((8, n - 15 + i, g(i)))
        cells.append((8, n - 8, True))
        return cells

    for x, y, v in format_cells(0):
        setf(x, y, v)

    # размещение данных
    idx = 0
    total_bits = len(allcw) * 8
    right = n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = n - 1 - vert if upward else vert
                if not isf[y][x] and idx < total_bits:
                    mod[y][x] = ((allcw[idx >> 3] >> (7 - (idx & 7))) & 1) == 1
                    idx += 1
        right -= 2

    masks = [
        lambda x, y: (x + y) % 2 == 0,
        lambda x, y: y % 2 == 0,
        lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0,
        lambda x, y: (x // 3 + y // 2) % 2 == 0,
        lambda x, y: x * y % 2 + x * y % 3 == 0,
        lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
    ]

    def build(m):
        g = [row[:] for row in mod]
        for y in range(n):
            for x in range(n):
                if not isf[y][x] and masks[m](x, y):
                    g[y][x] = not g[y][x]
        for x, y, v in format_cells(m):
            g[y][x] = v
        return g

    def penalty(g):
        p = 0
        pats = ("10111010000", "00001011101")
        for lines in (g, [list(c) for c in zip(*g)]):
            for line in lines:
                run = 1
                for i in range(1, n):
                    if line[i] == line[i - 1]:
                        run += 1
                    else:
                        if run >= 5:
                            p += run - 2
                        run = 1
                if run >= 5:
                    p += run - 2
                s = "".join("1" if v else "0" for v in line)
                for i in range(n - 10):
                    if s[i:i + 11] in pats:
                        p += 40
        for y in range(n - 1):
            for x in range(n - 1):
                if g[y][x] == g[y][x + 1] == g[y + 1][x] == g[y + 1][x + 1]:
                    p += 3
        dark = sum(v for row in g for v in row)
        tot = n * n
        p += ((abs(dark * 20 - tot * 10) + tot - 1) // tot - 1) * 10
        return p

    best = min((build(m) for m in range(8)), key=penalty)

    q = 4
    size = n + 2 * q
    path = "".join(
        "M%d %dh1v1h-1z" % (x + q, y + q)
        for y in range(n) for x in range(n) if best[y][x]
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges">'
        '<rect width="100%%" height="100%%" fill="#fff"/><path d="%s" fill="#000"/></svg>'
        % (size, size, path)
    )


# ───────────────────────────── вспомогательное ─────────────────────────────

FOLDER = Path.home() / "StudioDrop"
TOKEN = ""
PORT = 8765
LAN_IP = "127.0.0.1"
TEXTS = []  # [{id, text, t}]
TEXTS_LOCK = threading.Lock()
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

UDP_PORT = 8766
DEVICE_ID = secrets.token_hex(4)
DEV_NAME = socket.gethostname()
PEERS = {}    # другие ПК с StudioDrop (найдены по UDP)
PHONES = {}   # устройства, у которых открыта страница (heartbeat)
OFFERS = {}   # исходящие предложения: oid -> {...}
INBOX = []    # входящие предложения
STAGES = {}   # sid -> [Path] файлы, подготовленные к отправке
STATE_LOCK = threading.Lock()
LAST_POLL = [0.0]     # когда последний раз открытая страница на этом ПК опрашивала сервер
NO_BROWSER = [False]
OUT = FOLDER / ".out"


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # пакеты не отправляются
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def lan_url():
    return "http://%s:%d/%s/" % (LAN_IP, PORT, TOKEN)


def safe_name(name):
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name.startswith("."):
        name = "file" + name
    if len(name) > 200:
        stem, ext = os.path.splitext(name)
        name = stem[: 200 - len(ext)] + ext
    return name


def reserve_part(name, folder=None):
    """Выбирает свободное имя и создаёт для него .part-файл. Возвращает (итог, part, файл)."""
    folder = folder or FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    stem, ext = os.path.splitext(name)
    k = 0
    while True:
        cand = name if k == 0 else "%s (%d)%s" % (stem, k, ext)
        dest = folder / cand
        part = folder / (cand + ".part")
        if not dest.exists():
            try:
                f = open(part, "xb")
                return dest, part, f
            except FileExistsError:
                pass
        k += 1


def list_files():
    out = []
    for p in FOLDER.iterdir():
        if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part"):
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def open_folder():
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(FOLDER))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(FOLDER)])
        else:
            subprocess.Popen(["xdg-open", str(FOLDER)])
    except Exception:
        pass


# ───────────────────────────── Windows: меню «Отправить» и автозапуск ─────────────────────────────

IS_WIN = sys.platform.startswith("win")


def launch_cmd():
    """Как запускать эту программу из .cmd-файла Windows."""
    if FROZEN:
        return '"%s"' % sys.executable
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return '"%s" "%s"' % (pyw if pyw.exists() else sys.executable, os.path.abspath(__file__))


def win_paths():
    base = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
    return base / "SendTo" / "StudioDrop.cmd", base / "Start Menu" / "Programs" / "Startup" / "StudioDrop.cmd"


def integration_on():
    return IS_WIN and all(p.exists() for p in win_paths())


def integration_set(on):
    if not IS_WIN:
        return
    sendto, startup = win_paths()
    if on:
        for path, tail in ((sendto, "send %*"), (startup, "--no-browser")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("@echo off\r\nchcp 65001>nul\r\nstart \"\" /min %s %s\r\n" % (launch_cmd(), tail)).encode("utf-8"))
    else:
        for path in (sendto, startup):
            try:
                path.unlink()
            except OSError:
                pass


def msgbox(text):
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, "StudioDrop", 0x40)
            return
        except Exception:
            pass
    print(text)


# ───────────────────────────── устройства и предложения ─────────────────────────────


def devices():
    now = time.time()
    out = []
    with STATE_LOCK:
        for d in PEERS.values():
            if now - d["t"] < 10:
                out.append({"id": d["id"], "name": d["name"], "kind": "pc"})
        for d in PHONES.values():
            if now - d["t"] < 8:
                out.append({"id": d["id"], "name": d["name"], "kind": "phone"})
    return sorted(out, key=lambda d: (d["kind"], d["name"].lower()))


def udp_start():
    """Рассылает «я здесь» и слушает такие же сообщения от других ПК."""
    try:
        rs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rs.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            rs.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        rs.bind(("", UDP_PORT))
    except OSError:
        rs = None

    def listen():
        while rs:
            try:
                data, addr = rs.recvfrom(2048)
                m = json.loads(data.decode("utf-8"))
                if m.get("app") != "studiodrop" or m.get("id") == DEVICE_ID:
                    continue
                with STATE_LOCK:
                    PEERS[m["id"]] = {"id": m["id"], "name": str(m.get("name", "ПК"))[:40],
                                      "ip": addr[0], "port": int(m["port"]), "t": time.time()}
            except Exception:
                continue

    def announce():
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            msg = json.dumps({"app": "studiodrop", "id": DEVICE_ID, "name": DEV_NAME, "port": PORT}).encode()
            targets = ["255.255.255.255"]
            if LAN_IP.count(".") == 3 and not LAN_IP.startswith("127."):
                targets.append(LAN_IP.rsplit(".", 1)[0] + ".255")
            for t in targets:
                try:
                    tx.sendto(msg, (t, UDP_PORT))
                except OSError:
                    pass
            time.sleep(3)

    threading.Thread(target=listen, daemon=True).start()
    threading.Thread(target=announce, daemon=True).start()


def file_meta(paths):
    return [{"name": p.name, "size": p.stat().st_size} for p in paths]


def make_offer(sid, dev_id):
    paths = [p for p in STAGES.get(sid, []) if p.is_file()]
    if not paths:
        raise ValueError("Нет файлов для отправки")
    dev = next((d for d in devices() if d["id"] == dev_id), None)
    if not dev:
        raise ValueError("Устройство больше не в сети")
    oid = secrets.token_hex(8)
    meta = file_meta(paths)
    OFFERS[oid] = {"files": paths, "state": "waiting", "t": time.time(), "done": set(), "to": dev["name"]}
    if dev["kind"] == "pc":
        peer = PEERS[dev_id]
        body = json.dumps({"oid": oid, "from": DEV_NAME, "files": meta,
                           "sender": "http://%s:%d" % (LAN_IP, PORT)}).encode()
        req = urllib.request.Request("http://%s:%d/o/offer" % (peer["ip"], peer["port"]), data=body,
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            OFFERS.pop(oid, None)
            raise ValueError("Не удалось связаться с «%s». Проверьте, что StudioDrop там запущен." % dev["name"])
    else:
        with STATE_LOCK:
            INBOX.append({"oid": oid, "target": dev_id, "from": DEV_NAME, "sender": "", "files": meta,
                          "state": "pending", "got": 0, "cur": "", "t": time.time()})
    return oid


def inbox_view(me):
    now = time.time()
    with STATE_LOCK:
        INBOX[:] = [i for i in INBOX if now - i["t"] < 1800 or i["state"] == "receiving"][-20:]
        return [{"oid": i["oid"], "from": i["from"], "files": i["files"], "state": i["state"],
                 "got": i["got"], "cur": i["cur"], "err": i.get("err", ""), "link": i["sender"] == ""}
                for i in INBOX if i["target"] == me]


def receive_thread(item):
    total_ok = False
    try:
        item["state"] = "receiving"
        for i, f in enumerate(item["files"]):
            item["cur"] = f["name"]
            dest, part, fh = reserve_part(safe_name(f["name"]))
            ok = False
            try:
                with fh, urllib.request.urlopen("%s/o/pull/%s/%d" % (item["sender"], item["oid"], i), timeout=20) as r:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        item["got"] += len(chunk)
                os.replace(part, dest)
                ok = True
            finally:
                if not ok:
                    try:
                        part.unlink()
                    except OSError:
                        pass
        item["state"] = "done"
        total_ok = True
    except Exception as e:
        item["state"] = "error"
        item["err"] = str(e)[:200]
    return total_ok


def notify_decline(item):
    if item.get("sender"):
        try:
            urllib.request.urlopen(urllib.request.Request(
                "%s/o/decline/%s" % (item["sender"], item["oid"]), data=b"{}", method="POST"), timeout=4).read()
        except Exception:
            pass
    elif item["oid"] in OFFERS:
        OFFERS[item["oid"]]["state"] = "declined"


# ───────────────────────────── HTTP ─────────────────────────────


class Handler(BaseHTTPRequestHandler):
    server_version = "StudioDrop"

    def log_message(self, *a):
        pass

    # --- утилиты ---
    def is_local(self):
        ip = self.client_address[0]
        return ip in LOOPBACK or ip == LAN_IP

    def send_bytes(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_bytes(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_body(self, limit=1 << 20):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("too big")
        return self.rfile.read(n) if n else b""

    def route(self):
        """Возвращает (rest, query) или None, если ответ уже отправлен."""
        u = urlparse(self.path)
        parts = u.path.split("/", 2)
        if u.path == "/" and self.is_local():
            self.redirect("/%s/" % TOKEN)
            return None
        if len(parts) < 2 or not hmac.compare_digest(parts[1], TOKEN):
            self.send_bytes(404, "Нужна ссылка из QR-кода StudioDrop.".encode())
            return None
        if len(parts) == 2:
            self.redirect("/%s/" % TOKEN)
            return None
        return parts[2], parse_qs(u.query)


    # --- служебные адреса для связи между ПК (/o/...) ---
    def open_route(self):
        try:
            path = urlparse(self.path).path
            parts = path.split("/")  # ['', 'o', 'pull', oid, i]
            act = parts[2] if len(parts) > 2 else ""
            if self.command == "GET" and act == "ping":
                return self.send_json({"app": "studiodrop"})
            if self.command == "GET" and act == "pull" and len(parts) == 5:
                return self.pull(parts[3], parts[4])
            if self.command == "POST" and act == "offer":
                m = json.loads(self.read_body() or b"{}")
                files = [{"name": safe_name(str(f["name"])), "size": int(f["size"])} for f in m["files"]][:200]
                sender = str(m["sender"])
                if not re.fullmatch(r"http://[0-9.]+:\d+", sender) or not files:
                    raise ValueError("bad offer")
                with STATE_LOCK:
                    INBOX[:] = [i for i in INBOX if i["oid"] != m["oid"]]
                    INBOX.append({"oid": str(m["oid"])[:32], "target": "pc", "from": str(m.get("from", "ПК"))[:40],
                                  "sender": sender, "files": files, "state": "pending", "got": 0, "cur": "",
                                  "t": time.time()})
                if time.time() - LAST_POLL[0] > 6 and not NO_BROWSER[0]:
                    webbrowser.open("http://127.0.0.1:%d/%s/" % (PORT, TOKEN))
                return self.send_json({"ok": True})
            if self.command == "POST" and act == "decline" and len(parts) == 4:
                o = OFFERS.get(parts[3])
                if o:
                    o["state"] = "declined"
                return self.send_json({"ok": True})
            self.send_bytes(404, b"not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.send_json({"ok": False, "error": str(e)}, 400)
            except Exception:
                pass

    def pull(self, oid, idx):
        o = OFFERS.get(oid)
        try:
            i = int(idx)
            p = o["files"][i] if o else None
        except (ValueError, IndexError):
            p = None
        if not o or p is None or o["state"] == "declined" or time.time() - o["t"] > 1800 or not p.is_file():
            return self.send_bytes(404, b"not found")
        o["state"] = "sending"
        name = p.name
        ascii_name = re.sub(r'[^\x20-\x7e]', "_", name).replace('"', "_")
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(p.stat().st_size))
        self.send_header("Content-Disposition",
                         "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name)))
        self.end_headers()
        with open(p, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                self.wfile.write(chunk)
        o["done"].add(i)
        if len(o["done"]) >= len(o["files"]):
            o["state"] = "done"

    # --- методы ---
    def do_GET(self):
        if self.path.startswith("/o/"):
            return self.open_route()
        try:
            r = self.route()
            if r is None:
                return
            rest, q = r
            if rest == "":
                return self.send_bytes(200, PAGE.encode(), "text/html; charset=utf-8")
            if rest == "api/info":
                return self.send_json({
                    "url": lan_url(), "folder": str(FOLDER),
                    "local": self.is_local(), "host": socket.gethostname(),
                    "win": IS_WIN, "integ": integration_on(),
                })
            if rest == "api/list":
                with TEXTS_LOCK:
                    texts = list(TEXTS)
                return self.send_json({"files": list_files(), "texts": texts})
            if rest == "send":
                return self.send_bytes(200, SEND_PAGE.encode(), "text/html; charset=utf-8")
            if rest == "api/devices":
                return self.send_json({"devices": devices(), "me": DEV_NAME})
            if rest == "api/inbox":
                me = (q.get("me") or ["pc"])[0]
                if self.is_local() and me == "pc":
                    LAST_POLL[0] = time.time()
                return self.send_json({"items": inbox_view(me)})
            if rest == "api/stage":
                sid = (q.get("sid") or [""])[0]
                return self.send_json({"files": file_meta([p for p in STAGES.get(sid, []) if p.is_file()])})
            if rest == "api/offer":
                o = OFFERS.get((q.get("oid") or [""])[0])
                if not o:
                    return self.send_json({"state": "unknown"})
                st = "expired" if o["state"] == "waiting" and time.time() - o["t"] > 600 else o["state"]
                return self.send_json({"state": st, "to": o["to"]})
            if rest == "qr.svg":
                return self.send_bytes(200, make_qr_svg(lan_url()).encode(), "image/svg+xml")
            if rest.startswith("f/"):
                return self.download(unquote(rest[2:]), "view" in q)
            self.send_bytes(404, b"not found")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def download(self, name, inline):
        if name != safe_name(name) or name.endswith(".part"):
            return self.send_bytes(404, b"not found")
        p = FOLDER / name
        if not p.is_file():
            return self.send_bytes(404, b"not found")
        size = p.stat().st_size
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        ascii_name = re.sub(r'[^\x20-\x7e]', "_", name).replace('"', "_")
        disp = "inline" if inline else "attachment"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition",
                         "%s; filename=\"%s\"; filename*=UTF-8''%s" % (disp, ascii_name, quote(name)))
        self.end_headers()
        with open(p, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_PUT(self):
        try:
            r = self.route()
            if r is None:
                return
            rest, q = r
            if rest != "up":
                return self.send_bytes(404, b"not found")
            name = safe_name(unquote((q.get("name") or ["file"])[0]))
            if self.headers.get("Content-Length") is None:
                return self.send_bytes(411, b"length required")
            remaining = int(self.headers["Content-Length"])
            sid = (q.get("sid") or [""])[0]
            if sid and not re.fullmatch(r"[0-9a-f]{8,16}", sid):
                return self.send_bytes(400, b"bad sid")
            dest, part, f = reserve_part(name, OUT / sid if sid else None)
            ok = False
            try:
                with f:
                    while remaining > 0:
                        chunk = self.rfile.read(min(1 << 20, remaining))
                        if not chunk:
                            raise ConnectionError("клиент отключился")
                        f.write(chunk)
                        remaining -= len(chunk)
                os.replace(part, dest)
                ok = True
            finally:
                if not ok:
                    try:
                        part.unlink()
                    except OSError:
                        pass
            if sid:
                STAGES.setdefault(sid, []).append(dest)
            self.send_json({"ok": True, "name": dest.name})
        except (BrokenPipeError, ConnectionError):
            pass
        except OSError as e:
            try:
                self.send_json({"ok": False, "error": str(e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        if self.path.startswith("/o/"):
            return self.open_route()
        try:
            r = self.route()
            if r is None:
                return
            rest, q = r
            if rest == "text":
                text = json.loads(self.read_body() or b"{}").get("text", "").strip()
                if text:
                    with TEXTS_LOCK:
                        TEXTS.insert(0, {"id": secrets.token_hex(4), "text": text[:20000], "t": int(time.time())})
                        del TEXTS[30:]
                return self.send_json({"ok": True})
            if rest == "deltext":
                tid = (q.get("id") or [""])[0]
                with TEXTS_LOCK:
                    TEXTS[:] = [t for t in TEXTS if t["id"] != tid]
                return self.send_json({"ok": True})
            if rest == "del":
                name = unquote((q.get("name") or [""])[0])
                if name == safe_name(name) and (FOLDER / name).is_file():
                    (FOLDER / name).unlink()
                return self.send_json({"ok": True})
            if rest == "hello":
                m = json.loads(self.read_body() or b"{}")
                did = re.sub(r"[^0-9a-zA-Z]", "", str(m.get("id", "")))[:16]
                if did:
                    with STATE_LOCK:
                        PHONES[did] = {"id": did, "name": str(m.get("name", "Телефон"))[:40], "t": time.time()}
                return self.send_json({"ok": True})
            if rest == "stage":
                if not self.is_local():
                    return self.send_bytes(403, b"forbidden")
                m = json.loads(self.read_body(8 << 20) or b"{}")
                paths = [Path(x) for x in m.get("paths", [])]
                paths = [x for x in paths if x.is_file()]
                sid = secrets.token_hex(4)
                STAGES[sid] = paths
                return self.send_json({"ok": True, "sid": sid})
            if rest == "send":
                m = json.loads(self.read_body() or b"{}")
                try:
                    oid = make_offer(str(m.get("sid", "")), str(m.get("to", "")))
                except ValueError as e:
                    return self.send_json({"ok": False, "error": str(e)})
                return self.send_json({"ok": True, "oid": oid})
            if rest == "accept":
                oid = (q.get("oid") or [""])[0]
                with STATE_LOCK:
                    item = next((i for i in INBOX if i["oid"] == oid and i["target"] == "pc"), None)
                if item and self.is_local() and item["state"] == "pending":
                    threading.Thread(target=receive_thread, args=(item,), daemon=True).start()
                return self.send_json({"ok": True})
            if rest == "dismiss":
                oid = (q.get("oid") or [""])[0]
                with STATE_LOCK:
                    item = next((i for i in INBOX if i["oid"] == oid), None)
                    if item and item["state"] != "receiving":
                        INBOX.remove(item)
                    else:
                        item = None
                if item and item["state"] == "pending":
                    notify_decline(item)
                return self.send_json({"ok": True})
            if rest == "integration":
                if not self.is_local():
                    return self.send_bytes(403, b"forbidden")
                integration_set((q.get("on") or ["0"])[0] == "1")
                return self.send_json({"ok": True, "integ": integration_on()})
            if rest == "quit":
                if not self.is_local():
                    return self.send_bytes(403, b"forbidden")
                self.send_json({"ok": True})
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return
            if rest == "open":
                if not self.is_local():
                    return self.send_bytes(403, b"forbidden")
                open_folder()
                return self.send_json({"ok": True})
            self.send_bytes(404, b"not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.send_json({"ok": False, "error": str(e)}, 400)
            except Exception:
                pass


# ───────────────────────────── страница ─────────────────────────────

PAGE = r"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>StudioDrop</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--tx:#14161a;--mut:#6b7280;--line:#e5e7eb;--ac:#2563eb;--acx:#fff;--ok:#16a34a;--err:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#0e1013;--card:#171a1f;--tx:#eceef2;--mut:#8b93a1;--line:#272c34;--ac:#3b82f6}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;padding:max(16px,env(safe-area-inset-top)) 16px 40px}
.wrap{max-width:720px;margin:0 auto;display:grid;gap:14px}
h1{font-size:22px;margin:6px 0 0;display:flex;align-items:center;gap:10px}
h1 small{font-size:13px;font-weight:400;color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}
.card h2{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);margin:0 0 12px;font-weight:600}
.qr{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.qr img{width:170px;height:170px;border-radius:10px;border:1px solid var(--line);background:#fff}
.qr .t{flex:1;min-width:200px}
.url{font-family:ui-monospace,Consolas,monospace;font-size:14px;word-break:break-all;background:var(--bg);padding:8px 10px;border-radius:8px;margin:8px 0}
.mut{color:var(--mut);font-size:14px}
.drop{display:block;border:2px dashed var(--line);border-radius:14px;padding:28px 16px;text-align:center;cursor:pointer;transition:.15s}
.drop.over{border-color:var(--ac);background:color-mix(in srgb,var(--ac) 8%,transparent)}
.drop b{display:block;font-size:17px;margin-bottom:4px}
button,.btn{font:inherit;border:0;border-radius:10px;padding:9px 14px;background:var(--ac);color:var(--acx);cursor:pointer;text-decoration:none;display:inline-block}
button.ghost,.btn.ghost{background:transparent;color:var(--ac);border:1px solid var(--line)}
button.del{background:transparent;color:var(--err);border:1px solid var(--line)}
.row{display:flex;align-items:center;gap:10px;padding:10px 0;border-top:1px solid var(--line)}
.row:first-of-type{border-top:0}
.row .n{flex:1;min-width:0}
.row .n span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .n small{display:block;color:var(--mut);font-size:13px}
.acts{display:flex;gap:6px;flex-shrink:0}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;width:0;background:var(--ac);transition:width .1s}
.bar.ok i{background:var(--ok)}.bar.err i{background:var(--err)}
textarea{width:100%;font:inherit;color:var(--tx);background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px;resize:vertical;min-height:64px}
.txt{white-space:pre-wrap;word-break:break-word;flex:1}
.empty{color:var(--mut);padding:6px 0}
.top{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}
.top h2{margin:0}
</style></head><body>
<div class="wrap">
  <h1>📡 StudioDrop <small id="host"></small></h1>

  <div class="card" id="qrcard" hidden>
    <h2>Подключить телефон</h2>
    <div class="qr">
      <img id="qr" alt="QR">
      <div class="t">
        <div><b>Наведите камеру телефона на QR-код</b></div>
        <div class="mut">Телефон и компьютер должны быть в одной Wi-Fi сети. Устанавливать ничего не нужно.</div>
        <div class="url" id="url"></div>
        <button class="ghost" id="openf">Открыть папку с файлами</button>
      </div>
    </div>
  </div>

  <div class="card" id="inbox" hidden></div>

  <div class="card">
    <div class="top"><h2>Отправить файлы</h2><a class="btn ghost" id="tosend">На другое устройство →</a></div>
    <label class="drop" id="drop">
      <b>Выбрать файлы</b>
      <span class="mut" id="hint">или перетащите их сюда</span>
      <input type="file" id="file" multiple hidden>
    </label>
    <div id="ups"></div>
  </div>

  <div class="card">
    <h2>Файлы</h2>
    <div id="files"><div class="empty">Пока пусто</div></div>
  </div>

  <div class="card">
    <h2>Текст и ссылки</h2>
    <textarea id="txt" placeholder="Вставьте текст или ссылку, чтобы передать на другое устройство"></textarea>
    <div style="margin:8px 0 4px"><button id="sendt">Отправить текст</button></div>
    <div id="texts"></div>
  </div>
</div>

<div class="wrap" id="settings" hidden style="margin-top:14px">
  <div class="card">
    <h2>Настройки</h2>
    <label id="integw" hidden style="display:block;margin-bottom:12px"><input type="checkbox" id="integ"> Пункт «Отправить → StudioDrop» и автозапуск с Windows</label>
    <button class="del" id="quit">Выйти из StudioDrop</button>
  </div>
</div>
<script>
const base = location.pathname.replace(/\/$/, '');
const $ = id => document.getElementById(id);
const fmtSize = n => n < 1024 ? n + ' Б' : n < 1048576 ? (n/1024).toFixed(0) + ' КБ' : n < 1073741824 ? (n/1048576).toFixed(1) + ' МБ' : (n/1073741824).toFixed(2) + ' ГБ';
const fmtTime = t => new Date(t*1000).toLocaleString('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
const el = (tag, props, ...kids) => { const e = document.createElement(tag); Object.assign(e, props||{}); kids.forEach(k => e.append(k)); return e; };

let me = 'pc';
const devId = (() => { try { let v = localStorage.getItem('as_id'); if (!v) { v = Math.random().toString(16).slice(2, 10); localStorage.setItem('as_id', v); } return v; } catch (e) { return Math.random().toString(16).slice(2, 10); } })();
const devName = () => { const u = navigator.userAgent; return /iPhone/.test(u) ? 'iPhone' : /iPad/.test(u) ? 'iPad' : /Android/.test(u) ? 'Android' : /Mac/.test(u) ? 'Mac (браузер)' : /Windows/.test(u) ? 'ПК (браузер)' : 'Устройство'; };
async function init() {
  $('tosend').href = base + '/send';
  const info = await (await fetch(base + '/api/info')).json();
  $('host').textContent = info.host;
  if (!info.local) {
    me = devId;
    const hello = () => fetch(base + '/hello', {method:'POST', body:JSON.stringify({id:devId, name:devName()})}).catch(() => {});
    hello(); setInterval(hello, 3000);
  }
  if (info.local) {
    $('qrcard').hidden = false;
    $('qr').src = base + '/qr.svg';
    $('url').textContent = info.url;
    $('openf').onclick = () => fetch(base + '/open', {method:'POST'});
    $('settings').hidden = false;
    if (info.win) { $('integw').hidden = false; $('integ').checked = info.integ; $('integ').onchange = e => fetch(base + '/integration?on=' + (e.target.checked ? 1 : 0), {method:'POST'}); }
    $('quit').onclick = async () => { if (confirm('Выйти? Другие устройства перестанут видеть этот компьютер.')) { await fetch(base + '/quit', {method:'POST'}).catch(() => {}); document.body.innerHTML = '<p style="padding:40px;text-align:center;font:18px sans-serif">StudioDrop остановлен. Можно закрыть вкладку.</p>'; } };
  }
  if (!info.local) $('hint').textContent = 'можно выбрать фото, видео и любые файлы';
}

// ---------- загрузка ----------
const queue = []; let busy = false;
function addFiles(list) { for (const f of list) queue.push(f); pump(); }
function pump() {
  if (busy || !queue.length) return;
  busy = true;
  const file = queue.shift();
  const bar = el('i'); const barw = el('div', {className:'bar'}, bar);
  const st = el('small', {textContent:'0%'});
  const row = el('div', {className:'row'}, el('div', {className:'n'}, el('span', {textContent:file.name}), st, barw));
  $('ups').append(row);
  const xhr = new XMLHttpRequest(); const t0 = Date.now();
  xhr.open('PUT', base + '/up?name=' + encodeURIComponent(file.name));
  xhr.upload.onprogress = e => {
    if (!e.lengthComputable) return;
    const p = e.loaded / e.total * 100; bar.style.width = p + '%';
    const sp = e.loaded / Math.max(1, (Date.now()-t0)/1000);
    st.textContent = p.toFixed(0) + '% · ' + fmtSize(sp) + '/с';
  };
  const done = ok => {
    barw.classList.add(ok ? 'ok' : 'err');
    st.textContent = ok ? 'Готово · ' + fmtSize(file.size) : 'Ошибка отправки';
    if (ok) bar.style.width = '100%';
    setTimeout(() => row.remove(), ok ? 2500 : 8000);
    busy = false; refresh(); pump();
  };
  xhr.onload = () => done(xhr.status === 200);
  xhr.onerror = () => done(false);
  xhr.send(file);
}
$('file').onchange = e => { addFiles(e.target.files); e.target.value = ''; };
const drop = $('drop');
['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => addFiles(e.dataTransfer.files));
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => { e.preventDefault(); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
window.addEventListener('paste', e => { if (e.target.tagName === 'TEXTAREA') return; if (e.clipboardData.files.length) addFiles(e.clipboardData.files); });

// ---------- списки ----------
function copyText(s) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(s);
  const ta = el('textarea', {value:s}); ta.style.position = 'fixed'; ta.style.opacity = 0;
  document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove();
}
let last = '';
async function refresh() {
  let d; try { d = await (await fetch(base + '/api/list')).json(); } catch (e) { return; }
  const sig = JSON.stringify(d); if (sig === last) return; last = sig;
  const files = $('files'); files.replaceChildren();
  if (!d.files.length) files.append(el('div', {className:'empty', textContent:'Пока пусто'}));
  for (const f of d.files) {
    const url = base + '/f/' + encodeURIComponent(f.name);
    const acts = el('div', {className:'acts'});
    if (/\.(jpe?g|png|gif|webp|heic|bmp|mp4|mov|pdf)$/i.test(f.name))
      acts.append(el('a', {className:'btn ghost', href:url + '?view=1', target:'_blank', textContent:'Открыть'}));
    acts.append(el('a', {className:'btn', href:url, textContent:'Скачать'}));
    const del = el('button', {className:'del', textContent:'✕', title:'Удалить'});
    del.onclick = async () => { if (confirm('Удалить «' + f.name + '»?')) { await fetch(base + '/del?name=' + encodeURIComponent(f.name), {method:'POST'}); last = ''; refresh(); } };
    acts.append(del);
    files.append(el('div', {className:'row'}, el('div', {className:'n'}, el('span', {textContent:f.name}), el('small', {textContent:fmtSize(f.size) + ' · ' + fmtTime(f.mtime)})), acts));
  }
  const texts = $('texts'); texts.replaceChildren();
  for (const t of d.texts) {
    const cp = el('button', {className:'ghost', textContent:'Копировать'});
    cp.onclick = async () => { await copyText(t.text); cp.textContent = 'Скопировано ✓'; setTimeout(() => cp.textContent = 'Копировать', 1500); };
    const dl = el('button', {className:'del', textContent:'✕'});
    dl.onclick = async () => { await fetch(base + '/deltext?id=' + t.id, {method:'POST'}); last = ''; refresh(); };
    const txt = el('div', {className:'txt'}); txt.textContent = t.text;
    const linkish = /^https?:\/\/\S+$/.test(t.text);
    if (linkish) { txt.textContent = ''; txt.append(el('a', {href:t.text, target:'_blank', textContent:t.text, style:'color:var(--ac)'})); }
    texts.append(el('div', {className:'row'}, txt, el('div', {className:'acts'}, cp, dl)));
  }
}
$('sendt').onclick = async () => {
  const v = $('txt').value.trim(); if (!v) return;
  await fetch(base + '/text', {method:'POST', body:JSON.stringify({text:v})});
  $('txt').value = ''; last = ''; refresh();
};

// ---------- входящие ----------
let lastInbox = '';
const post = u => fetch(base + u, {method:'POST'});
async function refreshInbox() {
  let d; try { d = await (await fetch(base + '/api/inbox?me=' + me)).json(); } catch (e) { return; }
  const sig = JSON.stringify(d); if (sig === lastInbox) return; lastInbox = sig;
  const box = $('inbox'); box.replaceChildren(); box.hidden = !d.items.length;
  if (!d.items.length) return;
  box.append(el('h2', {textContent:'Входящие'}));
  for (const it of d.items) {
    const total = it.files.reduce((a, f) => a + f.size, 0);
    const names = it.files.map(f => f.name).join(', ');
    const acts = el('div', {className:'acts'});
    const info = el('div', {className:'n'}, el('span', {textContent: it.from + ' · ' + (it.files.length > 1 ? it.files.length + ' файлов' : it.files[0].name)}),
                    el('small', {textContent: it.files.length > 1 ? names : ''}), el('small', {textContent: fmtSize(total)}));
    const row = el('div', {className:'row'}, info, acts);
    const dismiss = txt => { const b = el('button', {className:'ghost', textContent:txt}); b.onclick = async () => { await post('/dismiss?oid=' + it.oid); lastInbox = ''; refreshInbox(); }; return b; };
    if (it.link) {
      // телефон: сразу даём ссылки на скачивание
      const col = el('div', {className:'n'}, el('span', {textContent: it.from + ' отправляет вам:'}));
      it.files.forEach((f, i) => col.append(el('a', {className:'btn', style:'margin:6px 6px 0 0', href:'/o/pull/' + it.oid + '/' + i, textContent:'Скачать ' + f.name + ' (' + fmtSize(f.size) + ')'})));
      row.replaceChildren(col, dismiss('Скрыть'));
    } else if (it.state === 'pending') {
      const ok = el('button', {textContent:'Принять'}); ok.onclick = async () => { await post('/accept?oid=' + it.oid); lastInbox = ''; refreshInbox(); };
      acts.append(ok, dismiss('Отклонить'));
    } else if (it.state === 'receiving') {
      const p = total ? Math.min(100, it.got / total * 100) : 0;
      info.append(el('small', {textContent:'Загрузка… ' + p.toFixed(0) + '%'}), el('div', {className:'bar'}, el('i', {style:'width:' + p + '%'})));
    } else if (it.state === 'done') {
      info.append(el('small', {textContent:'✓ Сохранено в папку StudioDrop'}));
      acts.append(dismiss('Ок'));
    } else {
      info.append(el('small', {textContent:'Ошибка: ' + it.err}));
      acts.append(dismiss('Закрыть'));
    }
    box.append(row);
  }
}
init(); refresh(); refreshInbox(); setInterval(refresh, 2000); setInterval(refreshInbox, 1000);
</script></body></html>
"""

SEND_PAGE = r"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>StudioDrop — отправить</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--tx:#14161a;--mut:#6b7280;--line:#e5e7eb;--ac:#2563eb;--ok:#16a34a;--err:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#0e1013;--card:#171a1f;--tx:#eceef2;--mut:#8b93a1;--line:#272c34;--ac:#3b82f6}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;padding:20px 16px}
.wrap{max-width:560px;margin:0 auto;display:grid;gap:14px}
h1{font-size:22px;margin:4px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}
.card h2{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);margin:0 0 12px;font-weight:600}
.f{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-top:1px solid var(--line)}
.f:first-of-type{border-top:0}.f span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.f span:last-child{color:var(--mut);white-space:nowrap}
.devs{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}
.dev{border:1px solid var(--line);background:var(--bg);color:var(--tx);border-radius:14px;padding:16px 8px;text-align:center;cursor:pointer;font:inherit}
.dev:hover{border-color:var(--ac)}.dev .i{font-size:34px;display:block}.dev small{color:var(--mut)}
.mut{color:var(--mut)}.status{font-size:17px;padding:6px 0}.ok{color:var(--ok)}.err{color:var(--err)}
label.add{color:var(--ac);cursor:pointer;display:inline-block;margin-top:8px}
</style></head><body><div class="wrap">
<h1>📡 Отправить</h1>
<div class="card"><h2>Файлы</h2><div id="files"><span class="mut">Выберите файлы…</span></div>
<label class="add" id="addl">+ Добавить файлы<input type="file" id="file" multiple hidden></label><div id="up" class="mut"></div></div>
<div class="card"><h2>Устройства рядом</h2>
<div id="status" class="status" hidden></div>
<div class="devs" id="devs"></div>
<p class="mut" id="none">Ищем устройства… На другом ПК должен быть запущен StudioDrop. На телефоне — открыта страница StudioDrop (отсканируйте QR на главной).</p></div>
<a href="." style="color:var(--ac)">← На главную</a>
</div>
<script>
const base = location.pathname.replace(/\/send\/?$/, '');
const $ = id => document.getElementById(id);
const sid = new URLSearchParams(location.search).get('sid') || Array.from(crypto.getRandomValues(new Uint8Array(4)), b => b.toString(16).padStart(2, '0')).join('');
const fmt = n => n < 1024 ? n + ' Б' : n < 1048576 ? (n/1024).toFixed(0) + ' КБ' : n < 1073741824 ? (n/1048576).toFixed(1) + ' МБ' : (n/1073741824).toFixed(2) + ' ГБ';
let files = [], busy = false;
async function loadFiles() {
  const d = await (await fetch(base + '/api/stage?sid=' + sid)).json(); files = d.files;
  const box = $('files'); box.replaceChildren();
  if (!files.length) { box.innerHTML = '<span class="mut">Выберите файлы…</span>'; return; }
  for (const f of files) { const r = document.createElement('div'); r.className = 'f'; const a = document.createElement('span'); a.textContent = f.name; const b = document.createElement('span'); b.textContent = fmt(f.size); r.append(a, b); box.append(r); }
}
$('file').onchange = async e => {
  for (const f of e.target.files) {
    await new Promise(res => { const x = new XMLHttpRequest(); x.open('PUT', base + '/up?sid=' + sid + '&name=' + encodeURIComponent(f.name));
      x.upload.onprogress = ev => { $('up').textContent = f.name + ': ' + (ev.loaded / ev.total * 100).toFixed(0) + '%'; };
      x.onloadend = () => { $('up').textContent = ''; res(); }; x.send(f); });
  }
  e.target.value = ''; loadFiles();
};
async function pollDevices() {
  if (busy) return;
  let d; try { d = await (await fetch(base + '/api/devices')).json(); } catch (e) { return; }
  const box = $('devs'); box.replaceChildren(); $('none').hidden = d.devices.length > 0;
  for (const dev of d.devices) {
    const b = document.createElement('button'); b.className = 'dev';
    b.innerHTML = '<span class="i">' + (dev.kind === 'pc' ? '💻' : '📱') + '</span>'; const n = document.createElement('div'); n.textContent = dev.name; b.append(n);
    b.onclick = () => send(dev); box.append(b);
  }
}
async function send(dev) {
  if (!files.length) { alert('Сначала выберите файлы'); return; }
  busy = true; const st = $('status'); st.hidden = false; st.className = 'status'; st.textContent = 'Ждём подтверждения на «' + dev.name + '»…';
  const r = await (await fetch(base + '/send', {method:'POST', body:JSON.stringify({sid, to:dev.id})})).json();
  if (!r.ok) { st.className = 'status err'; st.textContent = r.error; busy = false; return; }
  const t = setInterval(async () => {
    const o = await (await fetch(base + '/api/offer?oid=' + r.oid)).json();
    if (o.state === 'sending') st.textContent = 'Передача на «' + dev.name + '»…';
    if (o.state === 'done') { clearInterval(t); st.className = 'status ok'; st.textContent = '✓ Отправлено на «' + dev.name + '»'; busy = false; }
    if (o.state === 'declined') { clearInterval(t); st.className = 'status err'; st.textContent = '«' + dev.name + '» отклонил'; busy = false; }
    if (o.state === 'expired' || o.state === 'unknown') { clearInterval(t); st.className = 'status err'; st.textContent = 'Не дождались ответа'; busy = false; }
  }, 1000);
}
loadFiles(); pollDevices(); setInterval(pollDevices, 1500);
</script></body></html>
"""

# ───────────────────────────── запуск ─────────────────────────────


def find_running():
    """Ищет уже запущенный StudioDrop на этом ПК. Возвращает порт или None."""
    for port in range(8765, 8785):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/o/ping" % port, timeout=0.5) as r:
                if json.loads(r.read()).get("app") == "studiodrop":
                    return port
        except Exception:
            continue
    return None


def cmd_send(paths):
    """Вызывается из меню «Отправить»: готовит файлы и открывает выбор устройства."""
    paths = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
    if not paths:
        return
    port = find_running()
    if port is None:
        kw = {}
        if sys.platform.startswith("win"):
            kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        argv = [sys.executable] + ([] if FROZEN else [os.path.abspath(__file__)]) + ["--no-browser"]
        subprocess.Popen(argv,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)
        for _ in range(40):
            time.sleep(0.25)
            port = find_running()
            if port:
                break
    if port is None:
        sys.exit("StudioDrop не запустился")
    token = (Path.home() / "StudioDrop" / ".studiodrop_token").read_text().strip()
    req = urllib.request.Request("http://127.0.0.1:%d/%s/stage" % (port, token),
                                 data=json.dumps({"paths": paths}).encode(), method="POST")
    sid = json.loads(urllib.request.urlopen(req, timeout=5).read())["sid"]
    url = "http://127.0.0.1:%d/%s/send?sid=%s" % (port, token, sid)
    try:
        (Path.home() / "StudioDrop" / ".last_send_url").write_text(url)
    except OSError:
        pass
    print(url)
    if not os.environ.get("STUDIODROP_NO_OPEN"):
        webbrowser.open(url)


def main():
    global FOLDER, TOKEN, PORT, LAN_IP, OUT
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        return cmd_send(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in ("install", "uninstall"):
        integration_set(sys.argv[1] == "install")
        msgbox("Готово. Пункт «Отправить → StudioDrop» и автозапуск %s." % ("включены" if sys.argv[1] == "install" else "выключены"))
        return
    if find_running() and "--force" not in sys.argv:
        # уже запущен: просто открываем страницу
        port = find_running()
        try:
            tok = (Path.home() / "StudioDrop" / ".studiodrop_token").read_text().strip()
            if "--no-browser" not in sys.argv:
                webbrowser.open("http://127.0.0.1:%d/%s/" % (port, tok))
        except OSError:
            pass
        print("StudioDrop уже запущен.")
        return
    ap = argparse.ArgumentParser(description="StudioDrop — обмен файлами по Wi-Fi")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dir", help="папка для файлов (по умолчанию ~/StudioDrop)")
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    ap.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    args, _ = ap.parse_known_args()  # macOS может добавить свои аргументы (-psn_…)

    if args.dir:
        FOLDER = Path(args.dir).expanduser()
    FOLDER.mkdir(parents=True, exist_ok=True)
    OUT = FOLDER / ".out"
    shutil.rmtree(OUT, ignore_errors=True)
    marker = FOLDER / ".integrated"
    if FROZEN and IS_WIN and not marker.exists():  # первый запуск .exe: включаем меню и автозапуск
        integration_set(True)
        marker.write_text("1")
    NO_BROWSER[0] = args.no_browser
    for p in FOLDER.glob("*.part"):  # хвосты от прерванных передач
        try:
            p.unlink()
        except OSError:
            pass

    tok_file = FOLDER / ".studiodrop_token"
    try:
        TOKEN = tok_file.read_text().strip()
    except OSError:
        TOKEN = ""
    if not re.fullmatch(r"[0-9a-f]{12}", TOKEN):
        TOKEN = secrets.token_hex(6)
        tok_file.write_text(TOKEN)

    LAN_IP = get_lan_ip()
    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
            PORT = port
            break
        except OSError:
            continue
    if server is None:
        sys.exit("Не удалось занять порт. Закройте другие копии StudioDrop.")
    server.daemon_threads = True
    udp_start()

    local = "http://127.0.0.1:%d/%s/" % (PORT, TOKEN)
    print("=" * 56)
    print(" StudioDrop запущен")
    print(" На компьютере: ", local)
    print(" С телефона:    ", lan_url())
    print(" Папка файлов:  ", FOLDER)
    print(" Остановить: Ctrl+C или закройте это окно")
    print("=" * 56)
    if LAN_IP.startswith("127."):
        print(" ! Не найдена сеть Wi-Fi/Ethernet — подключитесь к сети.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(local)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
