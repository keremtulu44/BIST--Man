# KAP fixture kaydı (PowerShell) — backfill parser'ı için gerçek yanıtları indirir.
#
# Neden: `bildirim-sorgu-sonuc` bir HTML tablo döndürüyor. O tabloyu güvenilir
# ayrıştırmak için GERÇEK HTML'i bir kez kaydetmemiz gerekiyor; tahminle parser
# yazmak, sessizce yanlış ayrıştıran kod demek.
#
# Kullanım (repo kökünde):
#     .\scripts\dogrulama\kap_fixture_kaydet.ps1
#
# Windows PowerShell 5.1 ve PowerShell 7'de çalışır. TLS 1.2 zorlanır.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$outDir = Join-Path $PSScriptRoot "..\..\tests\fixtures\kap" | Resolve-Path -ErrorAction SilentlyContinue
if (-not $outDir) { $outDir = New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot "..\..\tests\fixtures\kap") -Force }
$outDir = (Resolve-Path $outDir).Path

$headers = @{
    "User-Agent"      = "bistman/0.1 (kisisel arastirma)"
    "Accept"          = "application/json, text/html;q=0.9, */*;q=0.8"
    "Accept-Language" = "tr-TR,tr;q=0.9"
}

# 1) MTRKS'in KAP üye OID'i (canlı doğrulandı) — 1 yıllık geçmiş tablosu
$memberOid = "8acae2c575a1b5b60177de29c7177130"

$targets = @(
    @{ Name = "member_history_mtrks.html"; Url = "https://www.kap.org.tr/tr/bildirim-sorgu-sonuc?member=$memberOid" },
    @{ Name = "list_light_live.json";      Url = "https://www.kap.org.tr/tr/api/disclosure/list/light" },
    @{ Name = "detail_1699030_live.json";  Url = "https://www.kap.org.tr/tr/api/notification/attachment-detail/1699030" }
)

Write-Host "Hedef klasor: $outDir" -ForegroundColor Cyan
Write-Host ""

foreach ($t in $targets) {
    $path = Join-Path $outDir $t.Name
    try {
        # Once sayfayi isit (KAP WAF'i icin): cerez alip sonra dosyayi indir
        $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
        Invoke-WebRequest -Uri "https://www.kap.org.tr/tr/bildirim-sorgu" -Headers $headers `
            -WebSession $session -UseBasicParsing -TimeoutSec 30 | Out-Null

        Invoke-WebRequest -Uri $t.Url -Headers $headers -WebSession $session -UseBasicParsing `
            -TimeoutSec 30 -OutFile $path

        $size = (Get-Item $path).Length
        $status = if ($size -lt 500) { "SUPHELI (cok kucuk)" } else { "OK" }
        $color = if ($status -eq "OK") { "Green" } else { "Yellow" }
        Write-Host ("{0,-30} {1,10:N0} bayt  {2}" -f $t.Name, $size, $status) -ForegroundColor $color
    }
    catch {
        Write-Host ("{0,-30} INDIRILEMEDI: {1}" -f $t.Name, $_.Exception.Message) -ForegroundColor Red
    }
    Start-Sleep -Milliseconds 1500   # nazik ol: KAP'i dovmeyelim
}

Write-Host ""
Write-Host "Simdi bu dosyalari bana gonder veya repoya ekleyip commit et:" -ForegroundColor Cyan
Get-ChildItem $outDir -Filter "*.html" | ForEach-Object { Write-Host ("  " + $_.Name) }
Get-ChildItem $outDir -Filter "*_live.json" | ForEach-Object { Write-Host ("  " + $_.Name) }
