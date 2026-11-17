# TEK KOMUT — venv kur (yoksa), borsapy kur, doğrulamayı çalıştır.
#
# Neden bu script var: `Activate.ps1` yeni PowerShell penceresinde kendiliğinden
# aktif olmaz ve `pip` bazen başka bir Python'a kurar. Burada aktivasyon YOK:
# her şey `.venv\Scripts\python.exe` üzerinden doğrudan çağrılıyor.
#
# Kullanım:
#     powershell -ExecutionPolicy Bypass -File .\scripts\dogrulama\kur_ve_calistir.ps1
#
# Sonda docs\dogrulama-sonuc2.json üretir; onu bana gönder.

$ErrorActionPreference = "Stop"

# 1) Repo köküne geç (script nereden çağrılırsa çağrılsın)
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root
Write-Host ("Klasor: " + (Get-Location).Path) -ForegroundColor Cyan

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "HATA: bu klasor repo degil (pyproject.toml yok)." -ForegroundColor Red
    exit 1
}

# 2) Uygun Python'u bul (3.11-3.13 oncelikli; 3.14'te tekerlek sorunu cikabilir)
$pyExe = $null
$pyPre = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.12", "3.13", "3.11", "3.14")) {
        & py "-$v" --version *> $null
        if ($LASTEXITCODE -eq 0) { $pyExe = "py"; $pyPre = @("-$v"); break }
    }
    if (-not $pyExe) { & py -3 --version *> $null; if ($LASTEXITCODE -eq 0) { $pyExe = "py"; $pyPre = @("-3") } }
}
if (-not $pyExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $pyExe = "python"; $pyPre = @()
}
if (-not $pyExe) {
    Write-Host "HATA: Python bulunamadi. Kur:  winget install --id Python.Python.3.12 -e" -ForegroundColor Red
    exit 1
}

$verOut = (& $pyExe @($pyPre + @("--version")) 2>&1) -join " "
Write-Host ("Python: " + $verOut + "  (" + $pyExe + " " + ($pyPre -join " ") + ")")

# 3) venv
$vpy = Join-Path (Get-Location).Path ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    Write-Host "venv olusturuluyor..." -ForegroundColor Yellow
    & $pyExe @($pyPre + @("-m", "venv", ".venv"))
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $vpy)) {
        Write-Host "HATA: venv olusturulamadi." -ForegroundColor Red
        exit 1
    }
}
Write-Host ("venv: " + $vpy)

# 4) bagimliliklar — venv python'u ile, aktivasyon YOK
Write-Host "pip guncelleniyor..." -ForegroundColor Yellow
& $vpy -m pip install --upgrade pip --quiet
Write-Host "borsapy kuruluyor (bir iki dakika surer)..." -ForegroundColor Yellow
& $vpy -m pip install "borsapy==0.11.0"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "HATA: borsapy kurulamadi. Muhtemel sebepler:" -ForegroundColor Red
    Write-Host "  - internet yok / proxy"
    Write-Host "  - Python 3.14 icin pandas tekerlegi yok -> 3.12 ile dene:"
    Write-Host "      py -3.12 -m venv .venv --clear"
    exit 1
}

# 5) dogrula
Write-Host ""
& $vpy -c "import borsapy; print('borsapy hazir:', borsapy.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "HATA: import edilemiyor (kurulum bozuk)." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== DOGRULAMA BASLIYOR ===" -ForegroundColor Cyan
& $vpy "scripts\dogrulama\ttm_dogrula2.py"

Write-Host ""
Write-Host "Bitti. Rapor dosyasi:" -ForegroundColor Cyan
Write-Host ("  " + (Join-Path (Get-Location).Path "docs\dogrulama-sonuc2.json"))
Write-Host "Gondermek icin:"
Write-Host "  git add docs\dogrulama-sonuc2.json"
Write-Host "  git commit -m `"dogrulama 2`""
Write-Host "  git push origin arena/2f801e3b-bist-man"
