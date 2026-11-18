# TEŞHİS — "borsapy kurulu değil / komut çalışmıyor" durumunda ilk çalıştırılacak.
#
# Ne yapıyor: hangi klasörde olduğunu, hangi Python'ların kurulu olduğunu ve
# borsapy'nin HANGİ Python'da göründüğünü tek tek yazdırıyor.
#
# Kullanım (herhangi bir klasörden, tam yol ile):
#     powershell -ExecutionPolicy Bypass -File C:\BIST--Man\scripts\dogrulama\kontrol.ps1
#
# Çıktının tamamını bana yapıştır.

Write-Host "=== 1) Neredeyim ===" -ForegroundColor Cyan
Write-Host ("Klasor : " + (Get-Location).Path)
$repo = Test-Path (Join-Path (Get-Location).Path "pyproject.toml")
Write-Host ("Repo mu: " + $(if ($repo) { "EVET" } else { "HAYIR - once: cd C:\BIST--Man" }))

Write-Host ""
Write-Host "=== 2) Kurulu Python'lar ===" -ForegroundColor Cyan
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -0p
} else {
    Write-Host "'py' launcher YOK"
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    Write-Host ("python --version : " + ((& python --version 2>&1) -join " "))
    Write-Host ("python nerede    : " + ((& python -c "import sys; print(sys.executable)" 2>&1) -join " "))
} else {
    Write-Host "python komutu PATH'te YOK (sadece 'py' launcher var)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== 3) .venv var mi ===" -ForegroundColor Cyan
$vpy = Join-Path (Get-Location).Path ".venv\Scripts\python.exe"
if (Test-Path $vpy) {
    Write-Host ("var    : " + $vpy)
    Write-Host ("surum  : " + (& $vpy --version 2>&1))
} else {
    Write-Host "YOK - olusturulacak: py -m venv .venv" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== 4) borsapy hangi Python'da var ===" -ForegroundColor Cyan

function Test-Borsapy([string]$exe, [string[]]$pre = @()) {
    $cmd = @()
    $cmd += $pre
    $cmd += @("-c", "import borsapy; print('borsapy', borsapy.__version__)")
    $out = & $exe @cmd 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  VAR  : " + ($out -join " ")) -ForegroundColor Green
        return $true
    } else {
        Write-Host ("  YOK  : " + (($out -join " ").Trim() -split "`n" | Select-Object -First 1)) -ForegroundColor Red
        return $false
    }
}

Write-Host "sistem python:"
if (Get-Command python -ErrorAction SilentlyContinue) { Test-Borsapy "python" | Out-Null }
Write-Host ".venv python  :"
if (Test-Path $vpy) { Test-Borsapy $vpy | Out-Null }

Write-Host ""
Write-Host "=== 5) Ne yapmali ===" -ForegroundColor Cyan
Write-Host "Aktivasyonla ugrasma. .venv'in python'unu DOGRUDAN cagir:" -ForegroundColor Yellow
Write-Host ""
Write-Host '  cd C:\BIST--Man'
Write-Host '  .\.venv\Scripts\python.exe -m pip install "borsapy==0.11.0"'
Write-Host '  .\.venv\Scripts\python.exe scripts\dogrulama\ttm_dogrula2.py'
Write-Host ""
Write-Host "Veya tek komut (venv yoksa kurar, bagimliligi kurar, calistirir):" -ForegroundColor Yellow
Write-Host '  powershell -ExecutionPolicy Bypass -File .\scripts\dogrulama\kur_ve_calistir.ps1'
