#!/usr/bin/env python3
"""
Сквозная проверка StudioDrop. Запуск:
    python tests/smoke.py python studiodrop.py          (исходники)
    python tests/smoke.py dist/StudioDrop.exe           (готовый exe)
    python tests/smoke.py /path/StudioDrop.app/Contents/MacOS/StudioDrop
Поднимает ДВЕ копии программы (как два ПК) и проверяет всё по-настоящему.
Код возврата 0 — всё прошло, иначе 1.
"""
import hashlib, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

CMD = sys.argv[1:]
if not CMD:
    sys.exit(__doc__)
IS_WIN = sys.platform.startswith("win")
results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (("  -> " + str(extra)) if (extra and not ok) else ""), flush=True)
    return ok


def http(method, url, data=None, timeout=60, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return 0, str(e).encode(), {}


def jget(url):
    st, body, _ = http("GET", url)
    try:
        return json.loads(body)
    except Exception:
        return {}


def jpost(url, obj=None):
    st, body, _ = http("POST", url, json.dumps(obj or {}).encode())
    try:
        return json.loads(body)
    except Exception:
        return {}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def free_ports(n):
    out = []
    for p in range(8765, 8785):
        s = socket.socket()
        try:
            s.bind(("0.0.0.0", p))
            out.append(p)
        except OSError:
            pass
        finally:
            s.close()
        if len(out) == n:
            return out
    sys.exit("нет свободных портов 8765-8784")


class Inst:
    def __init__(self, name, port, root):
        self.name, self.port = name, port
        self.home = root / name
        self.folder = self.home / "StudioDrop"
        self.home.mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                        APPDATA=str(self.home / "AppData"), STUDIODROP_NO_OPEN="1")
        self.log = open(root / (name + ".log"), "wb")
        self.proc = subprocess.Popen(CMD + ["--no-browser", "--force", "--port", str(port)], env=self.env,
                                     stdout=self.log, stderr=self.log, stdin=subprocess.DEVNULL)

    def wait_ready(self, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                return False
            if jget("http://127.0.0.1:%d/o/ping" % self.port).get("app") == "studiodrop":
                try:
                    self.token = (self.folder / ".studiodrop_token").read_text().strip()
                    self.base = "http://127.0.0.1:%d/%s" % (self.port, self.token)
                    return True
                except OSError:
                    pass
            time.sleep(0.4)
        return False

    def stop(self):
        try:
            http("POST", self.base + "/quit", b"{}", timeout=5)
        except Exception:
            pass
        for _ in range(40):
            if self.proc.poll() is not None:
                return True
            time.sleep(0.25)
        self.proc.kill()
        return False


def wait_for(fn, timeout=30, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def transfer(src, dst, files, label):
    """ПК -> ПК: предложить, принять, сверить хэши."""
    dev = wait_for(lambda: next((d for d in jget(src.base + "/api/devices").get("devices", []) if d["kind"] == "pc"), None), 40)
    if not check(label + ": устройство найдено по сети (UDP)", bool(dev)):
        return
    sid = jpost(src.base + "/stage", {"paths": [str(f) for f in files]}).get("sid")
    r = jpost(src.base + "/send", {"sid": sid, "to": dev["id"]})
    if not check(label + ": предложение отправлено", r.get("ok"), r):
        return
    oid = r["oid"]
    item = wait_for(lambda: next((i for i in jget(dst.base + "/api/inbox?me=pc").get("items", []) if i["oid"] == oid), None), 10)
    check(label + ": на приёмнике появилось «Принять?»", bool(item) and item["state"] == "pending")
    jpost(dst.base + "/accept?oid=" + oid)
    done = wait_for(lambda: next((i for i in jget(dst.base + "/api/inbox?me=pc").get("items", []) if i["oid"] == oid and i["state"] in ("done", "error")), None), 60)
    check(label + ": приём завершён", bool(done) and done["state"] == "done", done)
    ok = True
    for f in files:
        got = dst.folder / f.name
        ok = ok and got.exists() and sha(got) == sha(f)
    check(label + ": файлы совпали побайтно", ok)
    st = wait_for(lambda: jget(src.base + "/api/offer?oid=" + oid).get("state") == "done", 10)
    check(label + ": отправитель видит «отправлено»", bool(st))


def main():
    root = Path(tempfile.mkdtemp(prefix="studiodrop_smoke_"))
    pa, pb = free_ports(2)
    A, B = Inst("A", pa, root), Inst("B", pb, root)
    insts = [A, B]
    try:
        check("A запустился", A.wait_ready(), (root / "A.log").read_text(errors="replace")[-500:] if (root / "A.log").exists() else "")
        check("B запустился", B.wait_ready())
        if not all(results):
            return
        # ---- страница, QR, безопасность ----
        st, body, _ = http("GET", A.base + "/")
        check("главная страница открывается", st == 200 and b"StudioDrop" in body)
        st, body, h = http("GET", A.base + "/qr.svg")
        check("QR-код отдаётся", st == 200 and body.startswith(b"<svg") and b"<path" in body)
        st, _, _ = http("GET", "http://127.0.0.1:%d/wrongtoken/api/list" % pa)
        check("без правильной ссылки доступ закрыт (404)", st == 404)
        info = jget(A.base + "/api/info")
        check("info: локальный клиент опознан", info.get("local") is True)

        # ---- файлы через страницу (как с телефона) ----
        big = root / "Тестовый файл.bin"
        big.write_bytes(os.urandom(8 * 1024 * 1024))
        name = urllib.request.quote("Тестовый файл.bin")
        st, _, _ = http("PUT", A.base + "/up?name=" + name, big.read_bytes())
        st2, _, _ = http("PUT", A.base + "/up?name=" + name, big.read_bytes())
        names = [f["name"] for f in jget(A.base + "/api/list").get("files", [])]
        check("загрузка 8 МБ с русским именем", st == 200 and "Тестовый файл.bin" in names)
        check("одинаковое имя -> «(1)»", st2 == 200 and "Тестовый файл (1).bin" in names, names)
        st, data, h = http("GET", A.base + "/f/" + name)
        check("скачивание совпадает побайтно", st == 200 and hashlib.sha256(data).hexdigest() == sha(big))
        check("заголовок имени файла (UTF-8)", "filename*=UTF-8''" in h.get("Content-Disposition", ""), h)
        st, _, _ = http("PUT", A.base + "/up?name=..%2F..%2Fevil.txt", b"x")
        check("защита от ../ в имени", st == 200 and not (root / "evil.txt").exists() and not (A.home / "evil.txt").exists())
        st, _, _ = http("GET", A.base + "/f/..%2F.studiodrop_token")
        check("токен не скачивается", st == 404)
        check("текст: отправка/получение", jpost(A.base + "/text", {"text": "https://example.com"}).get("ok")
              and any(t["text"] == "https://example.com" for t in jget(A.base + "/api/list").get("texts", [])))
        http("POST", A.base + "/del?name=" + name)
        check("удаление файла", "Тестовый файл.bin" not in [f["name"] for f in jget(A.base + "/api/list").get("files", [])])

        # ---- ПК -> ПК, в обе стороны ----
        small = root / "Отчёт 2026.txt"
        small.write_text("привет\n" * 1000, encoding="utf-8")
        pic = root / "photo.jpg"
        pic.write_bytes(os.urandom(12 * 1024 * 1024))
        transfer(A, B, [pic, small], "A→B")
        transfer(B, A, [small], "B→A")

        # ---- отказ ----
        dev = wait_for(lambda: next((d for d in jget(A.base + "/api/devices").get("devices", []) if d["kind"] == "pc"), None), 20)
        if dev:
            sid = jpost(A.base + "/stage", {"paths": [str(small)]}).get("sid")
            oid = jpost(A.base + "/send", {"sid": sid, "to": dev["id"]}).get("oid")
            wait_for(lambda: jget(B.base + "/api/inbox?me=pc").get("items"), 10)
            http("POST", B.base + "/dismiss?oid=" + str(oid), b"")
            check("отказ: отправитель узнаёт", bool(wait_for(lambda: jget(A.base + "/api/offer?oid=" + str(oid)).get("state") == "declined", 10)))
            st, _, _ = http("GET", "http://127.0.0.1:%d/o/pull/%s/0" % (pa, oid))
            check("после отказа файл не отдаётся", st == 404)

        # ---- ПК -> телефон ----
        jpost(A.base + "/hello", {"id": "ph0ne1", "name": "iPhone"})
        sid = jpost(A.base + "/stage", {"paths": [str(small)]}).get("sid")
        r = jpost(A.base + "/send", {"sid": sid, "to": "ph0ne1"})
        check("телефон виден в списке устройств", r.get("ok"), r)
        items = jget(A.base + "/api/inbox?me=ph0ne1").get("items", [])
        check("у телефона появилось входящее со ссылкой", len(items) == 1 and items[0]["link"])
        if items:
            st, data, _ = http("GET", "http://127.0.0.1:%d/o/pull/%s/0" % (pa, items[0]["oid"]))
            check("телефон скачал файл без повреждений", st == 200 and hashlib.sha256(data).hexdigest() == sha(small))
        check("чужие входящие ПК не видит", not [i for i in jget(A.base + "/api/inbox?me=pc").get("items", []) if i["link"]])

        # ---- меню «Отправить» (команда send) ----
        marker = A.folder / ".last_send_url"
        if marker.exists():
            marker.unlink()
        if IS_WIN and (Path(A.env["APPDATA"]) / "Microsoft/Windows/SendTo/StudioDrop.cmd").exists():
            cmdfile = Path(A.env["APPDATA"]) / "Microsoft/Windows/SendTo/StudioDrop.cmd"
            check("exe сам добавил пункт «Отправить»", True)
            subprocess.Popen(["cmd", "/c", str(cmdfile), str(small)], env=A.env)
            label = "реальный .cmd из меню «Отправить»"
        else:
            subprocess.Popen(CMD + ["send", str(small)], env=A.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            label = "команда send"
        ok = wait_for(lambda: marker.exists() and marker.read_text().strip(), 45)
        check(label + " подготовила окно выбора", bool(ok))
        if ok:
            sid = ok.split("sid=")[-1]
            files = jget(A.base + "/api/stage?sid=" + sid).get("files", [])
            check(label + ": файл попал в список отправки", [f["name"] for f in files] == [small.name], files)
            st, body, _ = http("GET", ok)
            check("страница выбора устройства открывается", st == 200 and "Устройства рядом".encode() in body)
        if IS_WIN:
            check("без чёрного окна: нет падений print()", A.proc.poll() is None)
    finally:
        stopped = [i.stop() for i in insts]
        check("обе копии корректно завершаются (Выход)", all(stopped))
        for i in insts:
            i.log.close()
        if not all(results):
            for i in insts:
                print("\n===== лог %s =====" % i.name)
                print((root / (i.name + ".log")).read_text(errors="replace")[-1500:])
        shutil.rmtree(root, ignore_errors=True)
    print("\nИТОГО: %d проверок, провалено %d" % (len(results), results.count(False)))
    sys.exit(0 if all(results) else 1)


main()
