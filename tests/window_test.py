#!/usr/bin/env python3
"""
Проверка, что программа открывает СОБСТВЕННОЕ окно (не браузер) и страница в нём загружается.
Запуск:  python tests/window_test.py <команда запуска программы>
"""
import os, subprocess, sys, tempfile, time
from pathlib import Path

for _st in (sys.stdout, sys.stderr):
    try:
        _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
CMD = sys.argv[1:]
root = Path(tempfile.mkdtemp(prefix="sd_window_"))
home = root / "home"
home.mkdir()
env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData"))
log = open(root / "log.txt", "wb")
t0 = time.time()
proc = subprocess.Popen(CMD + ["--window-test", "--force", "--port", "8791"], env=env, stdout=log, stderr=log)
result = home / "StudioDrop" / ".window_ok"
while time.time() - t0 < 150 and proc.poll() is None:
    time.sleep(0.5)
if proc.poll() is None:
    proc.kill()
    print("FAIL  окно не закрылось за 150 с (зависло)")
text = result.read_text(encoding="utf-8") if result.exists() else ""
log.close()
ok = text.startswith("StudioDrop|qr")
print(("PASS  " if ok else "FAIL  ") + "собственное окно открылось и загрузило страницу: %r (%.0f с)" % (text, time.time() - t0))
if not ok:
    print("--- лог ---")
    print((root / "log.txt").read_text(errors="replace")[-2000:])
    sys.exit(1)
