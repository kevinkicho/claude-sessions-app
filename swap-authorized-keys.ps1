# Elevated helper: replaces administrators_authorized_keys with the pubkey
# at the given path. Called by rotate-ssh.ps1 via Start-Process -Verb RunAs.

param(
    [Parameter(Mandatory=$true)] [string] $PubKeyPath
)

$path = 'C:\ProgramData\ssh\administrators_authorized_keys'
$newPub = (Get-Content $PubKeyPath -Raw).TrimEnd("`r", "`n")
Set-Content -Path $path -Value $newPub -Encoding ASCII
icacls $path /inheritance:r | Out-Null
icacls $path /grant 'Administrators:F' | Out-Null
icacls $path /grant 'SYSTEM:F' | Out-Null
Write-Host "swapped authorized_keys with $PubKeyPath"
