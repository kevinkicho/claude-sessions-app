# Rotate the SSH key used by phone/tablet to connect to this PC.
#
# Flow:
#  1. Generate a fresh ed25519 keypair locally (once per run).
#  2. Swap the public key in administrators_authorized_keys (one UAC click).
#  3. Issue a 10-minute rotation token for devices that will fetch over Tailnet.
#  4. Interactive ADB-push loop:
#       - Push the new key to any currently-connected ADB device.
#       - Prompt: "Plug in another device and press ENTER to push again, or Q to finish."
#     Lets the user update multiple devices one-by-one with a single cable.

[CmdletBinding()]
param(
    [switch] $TokenOnly  # Skip keygen + authorized_keys swap; just issue a fresh token.
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$keyPath = Join-Path $here 'ssh_key'
$pubPath = Join-Path $here 'ssh_key.pub'
$tokensPath = Join-Path $here 'rotation-tokens.json'
$adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'

Write-Host ""
Write-Host "=== STT SSH key rotation ==="

if (-not $TokenOnly) {
    if (Test-Path $pubPath) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        Copy-Item $pubPath "$pubPath.rotated-$stamp" -Force
    }
    if (Test-Path $keyPath) { Remove-Item $keyPath -Force }
    if (Test-Path $pubPath) { Remove-Item $pubPath -Force }

    $comment = "stt-app-rotated-$(Get-Date -Format 'yyyy-MM-dd')"
    & ssh-keygen -t ed25519 -f $keyPath -N '""' -C $comment -q
    if (-not (Test-Path $keyPath)) {
        throw "ssh-keygen did not produce $keyPath"
    }
    Write-Host "  new pubkey: $(Get-Content $pubPath)"

    $swap = Join-Path $here 'swap-authorized-keys.ps1'
    Write-Host ""
    Write-Host "Click YES on the UAC prompt to install the new public key."
    Start-Process powershell -Verb RunAs -Wait -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $swap, '-PubKeyPath', $pubPath
    )
    Write-Host "  authorized_keys swap complete"
} else {
    Write-Host "  (token-only mode: skipping keygen and authorized_keys swap)"
}

# Issue a rotation token valid for 10 minutes (for remote / unplugged devices).
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$token = ([Convert]::ToBase64String($bytes)) -replace '[^A-Za-z0-9]', ''
if ($token.Length -gt 32) { $token = $token.Substring(0, 32) }
$expires = [int64](Get-Date -UFormat %s) + 600

$existing = @()
if (Test-Path $tokensPath) {
    try {
        $raw = Get-Content $tokensPath -Raw
        if ($raw -and $raw.Trim().Length -gt 0) {
            $existing = @(ConvertFrom-Json $raw)
        }
    } catch { $existing = @() }
    $now = [int64](Get-Date -UFormat %s)
    $existing = @($existing | Where-Object { $_.expires_at -gt $now })
}
$entry = [pscustomobject]@{
    token = $token
    expires_at = $expires
    issued = (Get-Date).ToString('o')
}
$existing = @($existing) + $entry
$existing | ConvertTo-Json -Depth 5 | Set-Content -Path $tokensPath -Encoding ASCII

$tsIp = ''
try { $tsIp = (& tailscale ip -4 2>$null | Select-Object -First 1).Trim() } catch {}
if (-not $tsIp) { $tsIp = '<PC_TAILSCALE_IP>' }

Write-Host ""
Write-Host "=== Rotation staged ==="
Write-Host "Token for remote devices (over Tailnet):"
Write-Host ""
Write-Host "  rotate-key $token" -ForegroundColor Cyan
Write-Host ""
$exp = (Get-Date).AddSeconds(600).ToString('HH:mm:ss')
Write-Host "Token expires at $exp."
Write-Host ""

# Interactive ADB-push loop.
function Push-KeyToConnectedDevices {
    if (-not (Test-Path $adb)) {
        Write-Host "  ADB not found at $adb — skipping USB push."
        return 0
    }
    $devLines = & $adb devices 2>&1
    $devices = @()
    foreach ($line in $devLines) {
        if ($line -match '^([A-Za-z0-9]+)\s+device$') {
            $devices += $matches[1]
        }
    }
    if ($devices.Count -eq 0) {
        Write-Host "  No ADB devices detected. Plug one in (USB debugging on) and try again."
        return 0
    }
    $ok = 0
    foreach ($dev in $devices) {
        Write-Host "  → $dev ..." -NoNewline
        & $adb -s $dev push $keyPath '/sdcard/Download/id_ed25519' 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host " pushed" -ForegroundColor Green
            $ok++
        } else {
            Write-Host " FAILED" -ForegroundColor Yellow
        }
    }
    if ($ok -gt 0) {
        Write-Host "  In Termux on the device(s) above, run:  rotate-key"
    }
    return $ok
}

Write-Host "=== USB push (plug devices in, one at a time) ==="
Write-Host "Scanning for connected ADB devices..."
$null = Push-KeyToConnectedDevices

while ($true) {
    Write-Host ""
    Write-Host "Plug in the NEXT device (or reconnect), then press ENTER to push the key again."
    Write-Host "Press Q then ENTER to finish." -NoNewline
    Write-Host ""
    $line = Read-Host
    if ($line -match '^[Qq]') { break }
    Write-Host "Scanning..."
    $null = Push-KeyToConnectedDevices
}

Write-Host ""
Write-Host "=== Rotation complete ==="
Write-Host "Remote devices can still use the token above until $exp."
Write-Host ""
