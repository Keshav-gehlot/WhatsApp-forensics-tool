import os, sqlite3, stat, sys
from datetime import timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from test_core import make_sqlite
from modules import adb_extractor as adb, custody, export, image_analysis as imga, paths, report_pdf, timeline
from modules.case import Case
from modules.db_parser import WaDatabase


def _extras_db(path):
    make_sqlite(path, 6)
    c = sqlite3.connect(path)
    c.executescript("""
    INSERT INTO jid VALUES (2,'555@s.whatsapp.net'), (3,'grp@g.us'), (4,'status@broadcast');
    CREATE TABLE message_add_on(_id INTEGER PRIMARY KEY, parent_message_row_id INT, sender_jid_row_id INT, from_me INT, timestamp INT);
    CREATE TABLE message_add_on_reaction(message_add_on_row_id INT, reaction TEXT);
    INSERT INTO message_add_on VALUES (1, 2, 2, 0, 1700000100000), (2, 3, 0, 1, 1700000200000);
    INSERT INTO message_add_on_reaction VALUES (1, '👍'), (2, '❤');
    CREATE TABLE message_edit_info(message_row_id INT, edited_timestamp INT, sender_timestamp INT);
    INSERT INTO message_edit_info VALUES (2, 1700000300000, 1700000001000);
    CREATE TABLE group_participant_user(group_jid_row_id INT, user_jid_row_id INT, rank INT);
    INSERT INTO group_participant_user VALUES (3, 1, 2), (3, 2, 0);
    CREATE TABLE starred_message(message_row_id INT);
    INSERT INTO starred_message VALUES (4);
    INSERT INTO chat VALUES (2, 4);
    INSERT INTO message VALUES (900, 2, 1, 'my status', 1700000400000, 0);
    """)
    c.commit(); c.close()


def test_extras_parsing_and_export(tmp_path):
    p = str(tmp_path / "m.db"); _extras_db(p)
    db = WaDatabase(p)
    rx = db.get_reactions()
    assert {r.emoji for r in rx} == {"👍", "❤"} and any(r.reactor_jid == "me" for r in rx)
    assert db.get_edits()[0].edited_at.tzinfo == timezone.utc
    parts = db.get_group_participants()
    assert {x.role for x in parts} == {"superadmin", "member"}
    assert [m.text for m in db.get_starred()] == ["msg 3"]
    assert [m.text for m in db.get_status_updates()] == ["my status"]
    n = export.export_extras_xlsx(db, str(tmp_path / "extras.xlsx"))
    assert n == 2 + 1 + 2 + 1 + 1


def test_extras_missing_tables_return_empty(tmp_path):
    p = str(tmp_path / "m.db"); make_sqlite(p, 3)
    db = WaDatabase(p)
    assert db.get_reactions() == [] and db.get_edits() == [] and db.get_group_participants() == []
    assert db.get_starred() == [] and db.get_status_updates() == []


def test_timeline_merge_filter_and_csv_safety(tmp_path):
    p = str(tmp_path / "m.db"); _extras_db(p)
    db = WaDatabase(p)
    ev = timeline.build_timeline(db.get_chats(), db.get_calls(), db.get_reactions(), db.get_edits())
    kinds = {e.kind for e in ev}
    assert kinds == {"message", "call", "reaction", "edit"}
    stamps = [e.timestamp for e in ev if e.timestamp]
    assert stamps == sorted(stamps)
    assert all(e.kind == "call" for e in timeline.filter_timeline(ev, kinds={"call"}))
    out = str(tmp_path / "t.csv"); timeline.export_timeline_csv(ev, out)
    body = open(out, encoding="utf-8-sig").read()
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body     # formula neutralised


def test_case_flow(tmp_path):
    src = tmp_path / "msgstore.db.crypt14"; src.write_bytes(b"evidence-bytes")
    case = Case.create(str(tmp_path / "case1"), case_id="C-1", examiner="tester")
    ev = case.ingest(str(src))
    assert not (os.stat(ev).st_mode & stat.S_IWUSR)
    wc = case.working_copy(ev)
    assert open(wc, "rb").read() == b"evidence-bytes" and os.path.dirname(wc) == case.working_dir
    ok, msg = case.verify(); assert ok, msg
    # tamper with the evidence -> verify must fail
    os.chmod(ev, stat.S_IWUSR | stat.S_IRUSR); open(ev, "ab").write(b"x")
    ok, msg = case.verify(); assert not ok and "CHANGED" in msg


def test_pdf_report(tmp_path):
    case = Case.create(str(tmp_path / "c"), case_id="C-2", examiner="tester")
    p = str(tmp_path / "db.db"); make_sqlite(p, 5)
    out = report_pdf.write_pdf_report(str(tmp_path / "r.pdf"), case.custody_dir, WaDatabase(p).get_chats(), [])
    assert open(out, "rb").read(5) == b"%PDF-" and os.path.getsize(out) > 1000


def test_install_detection_with_mocked_adb(monkeypatch):
    def fake_run(args, timeout=30, check=True):
        if args[-2:] == ["list", "users"]:
            return "Users:\n\tUserInfo{0:Owner:c13} running\n\tUserInfo{95:Dual:410} running"
        if "packages" in args:
            user, pkg = args[args.index("--user") + 1], args[-1]
            present = {("0", "com.whatsapp"), ("0", "com.whatsapp.w4b"), ("95", "com.whatsapp")}
            return f"package:{pkg}" if (user, pkg) in present else ""
        return ""
    monkeypatch.setattr(adb, "_run", fake_run)
    labels = [i.label for i in adb.list_installs("SER")]
    assert labels == ["com.whatsapp_u0", "com.whatsapp.w4b_u0", "com.whatsapp_u95"]
    biz = adb._make_install("com.whatsapp.w4b", 0)
    assert biz.media_dir == "/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business/Media"
    clone = adb._make_install("com.whatsapp", 95)
    assert clone.data_root == "/data/user/95/com.whatsapp" and "/emulated/95/" in clone.media_dir


def test_person_matcher_falls_back_to_lbph(tmp_path):
    matcher, name = imga.make_person_matcher(str(tmp_path))   # empty models dir
    assert name == "lbph" and isinstance(matcher, imga.PersonMatcher)
    assert "LEADS" in imga.MATCH_DISCLAIMER
    assert imga.PersonMatch("a.jpg", 0.5, (0, 0, 1, 1), method="sface").describe_score().startswith("similarity")


def test_app_root_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "App.exe"))
    assert paths.app_root() == str(tmp_path)
