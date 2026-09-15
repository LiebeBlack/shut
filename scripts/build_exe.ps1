# Build SmartShutdownHub (onedir folder, windowed) and Inno Setup installer on Windows.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
$ErrorActionPreference = "Stop"

# 1. Virtual environment setup
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment .venv..."
    python -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
$pip = ".\.venv\Scripts\pip.exe"

Write-Host "Upgrading pip and installing dependencies..."
& $py -m pip install --upgrade pip
& $pip install -r requirements.txt
& $pip install pyinstaller pillow

# 2. Generate the multi-resolution .ico
Write-Host "Generating multi-resolution icon..."
& $py -m sshub.gui.assets.make_ico

# 3. PyInstaller (onedir: complete self-contained folder)
Write-Host "Building standalone folder with PyInstaller..."
& ".\.venv\Scripts\pyinstaller.exe" sshub.spec --clean --noconfirm

if (Test-Path "dist\SmartShutdownHub\SmartShutdownHub.exe") {
    Write-Host "Folder build generated: dist\SmartShutdownHub\" -ForegroundColor Green

    # Create Portable ZIP of the complete folder
    Write-Host "Packaging Portable ZIP..."
    Compress-Archive -Path "dist\SmartShutdownHub", "README.md" -DestinationPath "dist\SmartShutdownHub-Portable.zip" -Force
}

# 4. Inno Setup installer (if ISCC.exe is available)
$iscc = Get-ChildItem "C:\Program Files (x86)\Inno Setup*\ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $iscc) {
    $iscc = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
}

if ($iscc) {
    Write-Host "Building modern Windows installer with Inno Setup..."
    & $iscc.FullName scripts\installer.iss
    Write-Host "Installer generated in installer\" -ForegroundColor Green
} else {
    Write-Host "Inno Setup (ISCC.exe) not found. To build the installer, install Inno Setup: choco install innosetup -y" -ForegroundColor Yellow
}

Write-Host "`nBuild process complete!" -ForegroundColor Green
