"""Builds WiFile-Setup.bat: the installer script with the whole app packed inside (one file, one click).
Run:  python installer/build_setup.py   ->  dist/WiFile-Setup.bat"""
import base64, io, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
FILES = ["sync_app.py", "filesync.py", "page.html", "requirements.txt", "wifile.ico"]

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for f in FILES:
        z.write(APP / f, f)
b64 = base64.b64encode(buf.getvalue()).decode()
head = (ROOT / "installer" / "setup_head.bat").read_text(encoding="utf-8").replace("\r\n", "\n")
out = ROOT / "dist" / "WiFile-Setup.bat"
out.parent.mkdir(exist_ok=True)
with open(out, "w", newline="\r\n", encoding="utf-8") as f:
    f.write(head + "::PAYLOAD::\n" + "\n".join(b64[i:i + 120] for i in range(0, len(b64), 120)) + "\n")
print(f"built {out} ({out.stat().st_size // 1024} KB)")
