# PyInstaller build of the app as a windowed, one-folder bundle (the installer packs the folder).
# Run through build.ps1, which generates packaging/generated/app.ico first.
from pathlib import Path

ROOT = Path(SPECPATH).parent
PACKAGE = ROOT / "src" / "mk96_app_pc_rocket_tool"

analysis = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    # Bundled sounds and icons keep their path inside the package (paths.RESOURCES_DIR).
    datas=[(str(PACKAGE / "resources"), "mk96_app_pc_rocket_tool/resources")],
    hiddenimports=["keyring.backends.Windows"],  # chosen at runtime by entry point
    excludes=["tkinter", "unittest", "pydoc"],
)
# Pulled in by the QtMultimedia hook but never used by this widgets app (verified by running
# the bundle: tray, windows, toasts and .ogg playback work without them). ~45 MB less.
UNUSED = ("qt6quick", "qt6qml", "qt6virtualkeyboard", "qtvirtualkeyboard", "opengl32sw",
          "multimediawidgets", "qmltooling", "/qml/", "\\qml\\")


def _is_used(entry):
    name = entry[0].lower()
    return not any(token in name for token in UNUSED)


analysis.binaries = [entry for entry in analysis.binaries if _is_used(entry)]
analysis.datas = [entry for entry in analysis.datas if _is_used(entry)]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="mk96-app-pc-rocket-tool",
    icon=str(ROOT / "packaging" / "generated" / "app.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="mk96-app-pc-rocket-tool", upx=False)
