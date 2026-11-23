#!/usr/bin/env python3
"""Проверка: новая версия сама останавливает старую, а не подключается к ней.
Запуск: python tests/upgrade_test.py <команда запуска программы>"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

for _st in (sys.stdout, sys.stderr):
    try:
        _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
CMD = sys.argv[1:]
root = Path(tempfile.mkdtemp(prefix="sd_upg_"))
home = root / "home"
home.mkdir()
env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData"))


def ping():
    for port in range(8765, 8785):
        try:
            m = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/o/ping" % port, timeout=1).read())
            if m.get("app") == "studiodrop":
                return m
        except Exception:
            continue
    return None


def wait(fn, t=40):
    t0 = time.time()
    while time.time() - t0 < t:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    return None


ok = True
olog = open(root / "old.log", "wb")
old = subprocess.Popen(CMD + ["--no-browser", "--force", "--port", "8772"], env=dict(env, STUDIODROP_FAKE_VERSION="old-1", PYTHONUNBUFFERED="1", PYTHONFAULTHANDLER="1"),
                       stdout=olog, stderr=olog, stdin=subprocess.DEVNULL)
p = wait(ping)
print(("PASS  " if p and p.get("ver") == "old-1" else "FAIL  ") + "старая версия запущена: %s" % p)
ok &= bool(p and p.get("ver") == "old-1")
if not p:
    if os.name != "nt":
        import signal
        try:
            old.send_signal(signal.SIGABRT); time.sleep(2)
        except Exception:
            pass
    olog.flush(); print("--- лог старой версии (код %s) ---" % old.poll()); print((root / "old.log").read_text(errors="replace")[-2500:])
    for port in range(8765, 8770):
        try:
            print(port, urllib.request.urlopen("http://127.0.0.1:%d/o/ping" % port, timeout=2).read())
        except Exception as e:
            print(port, "ошибка:", e)
new = subprocess.Popen(CMD + ["--no-browser", "--port", "8772"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
p = wait(lambda: (lambda x: x if x and x.get("ver") != "old-1" else None)(ping()))
print(("PASS  " if p else "FAIL  ") + "новая версия заняла место старой: %s" % p)
ok &= bool(p)
gone = wait(lambda: old.poll() is not None, 10)
print(("PASS  " if gone else "FAIL  ") + "старый процесс остановлен")
ok &= bool(gone)
for pr in (new, old):
    try:
        pr.kill()
    except Exception:
        pass
sys.exit(0 if ok else 1)
