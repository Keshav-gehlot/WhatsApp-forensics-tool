"""Headless GUI smoke test (needs a display, e.g. xvfb-run). Builds the real
App, loads a synthetic DB through the real code path, shows a chat, the
calls tab and runs an export, then exits non-zero on any exception."""
import os, sys, tempfile, time, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_core import make_sqlite
import main
import tkinter.messagebox as _mb
# Modal dialogs would block a headless run; record them instead.
dialogs = []
for _n in ('showinfo', 'showerror', 'showwarning'):
    setattr(_mb, _n, lambda *a, _n=_n, **k: dialogs.append((_n, a)))

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "msgstore.db"); make_sqlite(db, 40)
errors = []
app = main.App()
app.report_callback_exception = lambda *a: (errors.append(a), traceback.print_exception(*a))

def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.02)

pump(0.5)
app.session["msgstore_path"] = db
app._load_database(); pump(2)
assert app.session.get("chats"), "chats not loaded"
print("chats loaded:", len(app.session["chats"][0].messages), "messages")
app._show_chat(app.session["chats"][0]); pump(0.3)
app._refresh_calls(); pump(0.3)
print(app.calls_view.get("1.0", "end")[:400] if hasattr(app, "calls_view") else "")
app.export_out_entry.insert(0, os.path.join(tmp, "exp"))
for k, v in app.export_vars.items(): v.set(True)
app._run_export(); pump(3)
print(sorted(os.listdir(os.path.join(tmp, "exp"))))
# error path: loading a non-database must raise a dialog, not a NameError
bad = os.path.join(tmp, "bad.db"); open(bad, "wb").write(b"not sqlite")
app.session["msgstore_path"] = bad; app._load_database(); pump(2)
assert any(d[0] == 'showerror' and d[1][0] == 'Load Error' for d in dialogs), dialogs
dialogs[:] = [d for d in dialogs if d[0] != 'showerror']
print('dialogs:', dialogs)
app.destroy()
sys.exit(1 if errors or any(d[0]=='showerror' for d in dialogs) else 0)
