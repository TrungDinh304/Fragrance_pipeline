<#
.SYNOPSIS
    Chạy một lát ngân sách crawl. Đây là script mà Task Scheduler gọi.

.DESCRIPTION
    Bọc `python -m perfume_intel daily` với ba thứ mà bộ lên lịch cần:
      - ghi log ra file theo ngày (Task Scheduler không giữ stdout);
      - buộc UTF-8, vì console Windows mặc định cp1252 sẽ làm chết log tiếng Việt;
      - trả đúng exit code để Task Scheduler biết lần chạy có bị chặn hay không.

    Exit code:  0 = xong   1 = lỗi thường   2 = bị chặn (429/Cloudflare)

.EXAMPLE
    .\scripts\daily_crawl.ps1
    .\scripts\daily_crawl.ps1 -Budget 80 -Brands 1
    .\scripts\daily_crawl.ps1 -Install          # đăng ký lịch chạy 02:30 hằng ngày
    .\scripts\daily_crawl.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [int]    $Budget    = 0,
    [int]    $Brands    = 0,
    [string] $Delay     = "",
    [switch] $NoRender,
    [switch] $Install,
    [switch] $Uninstall,
    [string] $At        = "02:30",
    [string] $TaskName  = "PerfumeIntel-Daily"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# --- đăng ký / bỏ lịch -----------------------------------------------------
if ($Uninstall) {
    schtasks /Delete /TN $TaskName /F
    exit $LASTEXITCODE
}

if ($Install) {
    $self = Join-Path $PSScriptRoot "daily_crawl.ps1"
    $cmd  = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$self`""
    # /RL LIMITED: không cần quyền admin. Lịch chạy vào giờ ít ai dùng máy.
    schtasks /Create /TN $TaskName /TR $cmd /SC DAILY /ST $At /RL LIMITED /F
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "Đã đăng ký '$TaskName' — chạy $At hằng ngày."
        Write-Host "  xem tiến độ : python -m perfume_intel queue"
        Write-Host "  chạy thử ngay: schtasks /Run /TN $TaskName"
        Write-Host "  bỏ lịch      : .\scripts\daily_crawl.ps1 -Uninstall"
        Write-Host ""
        Write-Host "Lưu ý: máy phải đang bật vào giờ đó. Task Scheduler mặc định"
        Write-Host "KHÔNG chạy bù lần bị bỏ lỡ — nhưng không sao, hàng đợi vẫn"
        Write-Host "nằm nguyên trong sổ, hôm sau đi tiếp đúng chỗ dừng."
    }
    exit $LASTEXITCODE
}

# --- chạy thật -------------------------------------------------------------
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily_" + (Get-Date -Format "yyyyMMdd") + ".log")

# Console Windows là cp1252; không ép UTF-8 thì log tiếng Việt làm chết tiến trình.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$cmdArgs = @("-m", "perfume_intel", "daily")
if (-not $NoRender) { $cmdArgs += "--render" }
if ($Budget -gt 0)  { $cmdArgs += @("--budget", $Budget) }
if ($Brands -gt 0)  { $cmdArgs += @("--brands", $Brands) }
if ($Delay -ne "")  { $cmdArgs += @("--delay") + $Delay.Split(" ") }

Set-Location $root
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') === python $($cmdArgs -join ' ')" |
    Out-File -FilePath $log -Append -Encoding utf8

& python @cmdArgs 2>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE

"=== ket thuc, exit=$code ===" | Out-File -FilePath $log -Append -Encoding utf8

if ($code -eq 2) {
    Write-Warning "Bi chan (429/Cloudflare). So theo doi da cho moi hang nghi; hom sau tu chay lai."
}
exit $code
