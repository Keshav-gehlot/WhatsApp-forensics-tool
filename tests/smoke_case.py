"""Headless GUI test of the Case workflow: create case -> ingest+decrypt a
synthetic backup -> load -> timeline -> verify -> PDF. Needs a display."""
import os, sys, tempfile, time, traceback, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from Crypto.Cipher import AES
from test_core import make_sqlite, _varint
import main
import tkinter.messagebox as _mb

dialogs = []
for _n in ("showinfo", "showerror", "showwarning"):
    setattr(_mb, _n, lambda *a, _n=_n, **k: dialogs.append((_n, a)))
errors = []
tmp = tempfile.mkdtemp()

# synthetic encrypted backup + key
plain_db = os.path.join(tmp, "plain.db"); make_sqlite(plain_db, 30)
key, iv = os.urandom(32), os.urandom(16)
c = AES.new(key, AES.MODE_GCM, nonce=iv)
ct = c.encrypt(zlib.compress(open(plain_db, "rb").read())); tag = c.digest()
proto = b"\x0a" + _varint(16) + iv
enc = os.path.join(tmp, "msgstore.db.crypt14")
open(enc, "wb").write(b"\x00" + _varint(len(proto)) + proto + ct + tag)
keyf = os.path.join(tmp, "key"); open(keyf, "wb").write(os.urandom(126) + key)

app = main.App()
app.report_callback_exception = lambda *a: (errors.append(a), traceback.print_exception(*a))
def pump(s):
    end = time.time() + s
    while time.time() < end: app.update(); time.sleep(0.02)
pump(0.4)

app.examiner_entry.insert(0, "Smoke Tester"); app.case_entry.insert(0, "CASE-1")
app.case_dir_entry.insert(0, os.path.join(tmp, "case")); app._open_or_create_case(); pump(0.3)
assert app.case and app.decrypt_out_entry.get() == app.case.working_dir

app.encrypted_db_entry.insert(0, enc); app.key_entry.insert(0, keyf)
app._run_decrypt(); pump(1)
assert os.path.exists(os.path.join(app.case.working_dir, "msgstore.decrypted.db")), dialogs
assert os.path.exists(os.path.join(app.case.evidence_dir, "msgstore.db.crypt14"))

app.session["msgstore_path"] = os.path.join(app.case.working_dir, "msgstore.decrypted.db")
app._load_database(); pump(2)
assert app.session["chats"]
app._refresh_timeline(); pump(0.3)
assert "message" in app.tl_view.get("1.0", "end")
app._verify_case(); pump(0.3)
assert "INTEGRITY OK" in app.case_log.get("1.0", "end"), app.case_log.get("1.0", "end")
app._make_pdf_report(); pump(2)
assert os.path.getsize(os.path.join(app.case.exports_dir, "case_report.pdf")) > 1000
print("case workflow OK; dialogs:", [d[0] for d in dialogs])
app.destroy()
sys.exit(1 if errors or any(d[0] == "showerror" for d in dialogs) else 0)
