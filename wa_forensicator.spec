# PyInstaller spec — build with:  pyinstaller wa_forensicator.spec --noconfirm
# (or run build_exe.bat). Produces dist/WhatsAppForensicator/WhatsAppForensicator.exe
#
# The Tesseract install (bin/) and optional face models (models/) are NOT
# bundled into the exe: copy them next to WhatsAppForensicator.exe after the
# build (build_exe.bat does this if they exist). That keeps the exe small and
# keeps Tesseract's own license files with its binaries.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("customtkinter")      # themes/fonts customtkinter loads at runtime
datas += collect_data_files("cv2")               # haar cascades used for face detection
hiddenimports = collect_submodules("modules") + collect_submodules("ui")

a = Analysis(["main.py"], pathex=["."], datas=datas, hiddenimports=hiddenimports,
             excludes=["pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WhatsAppForensicator",
          console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="WhatsAppForensicator")
