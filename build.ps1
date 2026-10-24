$ErrorActionPreference = "Stop"

python -m pip install -r requirements-build.txt
python -c "from elrs_sim.branding import save_icon; save_icon('assets/elrs.ico')"
python -m PyInstaller --noconfirm --clean ElrsSim.spec

Write-Host ""
Write-Host "Build finished: dist\ElrsSim.exe"
