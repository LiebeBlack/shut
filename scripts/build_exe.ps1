# Build SmartShutdownHub.exe (onefile, windowed) on Windows.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
$ErrorActionPreference = "Stop"

# 1. Virtual env
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip

# 2. Dependencies (runtime + build-time)
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pip install pyinstaller cairosvg

# 3. Generate the multi-resolution .ico from the SVG
.\.venv\Scripts\python -m sshub.gui.assets.make_ico

# 4. PyInstaller
.\.venv\Scripts\pyinstaller sshub.spec --clean --noconfirm

Write-Host "`nDone -> dist\SmartShutdownHub.exe"
