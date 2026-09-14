[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:\d{1,3}\.){3}\d{1,3}$')]
    [string]$LanIp
)

$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSScriptRoot
$certRoot = Join-Path $studioRoot 'certs'
$caKey = Join-Path $certRoot 'lumi-lan-ca-key.pem'
$caCert = Join-Path $certRoot 'lumi-lan-ca-cert.pem'
$serverKey = Join-Path $certRoot 'lumi-lan-key.pem'
$serverCsr = Join-Path $certRoot 'lumi-lan.csr'
$serverCert = Join-Path $certRoot 'lumi-lan-cert.pem'

$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
    throw 'OpenSSL was not found. Run from LumiMultiAgent or install OpenSSL.'
}

# Conda's OpenSSL binary may otherwise search a non-existent global config
# path on Windows. Prefer the openssl.cnf installed beside the chosen binary.
if (-not $env:OPENSSL_CONF -or -not (Test-Path -LiteralPath $env:OPENSSL_CONF)) {
    $opensslRoot = Split-Path -Parent (Split-Path -Parent $openssl.Source)
    $configCandidates = @((Join-Path $opensslRoot 'ssl\openssl.cnf'))
    if ($env:CONDA_PREFIX) {
        $configCandidates += Join-Path $env:CONDA_PREFIX 'Library\ssl\openssl.cnf'
    }
    $opensslConfig = $configCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $opensslConfig) { throw 'Could not find openssl.cnf for the selected OpenSSL binary.' }
    $env:OPENSSL_CONF = $opensslConfig
}

New-Item -ItemType Directory -Force -Path $certRoot | Out-Null

if (-not (Test-Path -LiteralPath $caKey) -or -not (Test-Path -LiteralPath $caCert)) {
    & $openssl.Source genrsa -out $caKey 4096
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Lumi LAN CA private key.' }
    & $openssl.Source req -x509 -new -nodes -key $caKey -sha256 -days 3650 `
        -subj '/CN=Lumi Studio LAN Root CA' -out $caCert
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Lumi LAN CA certificate.' }
}

Remove-Item -LiteralPath $serverKey, $serverCsr, $serverCert -Force -ErrorAction SilentlyContinue
& $openssl.Source genrsa -out $serverKey 2048
if ($LASTEXITCODE -ne 0) { throw 'Could not create the sandbox private key.' }

$san = "subjectAltName = IP:$LanIp,IP:127.0.0.1,DNS:localhost"
& $openssl.Source req -new -key $serverKey -subj '/CN=Lumi Studio Sandbox' -addext $san -out $serverCsr
if ($LASTEXITCODE -ne 0) { throw 'Could not create the sandbox certificate request.' }

& $openssl.Source x509 -req -in $serverCsr -CA $caCert -CAkey $caKey -CAcreateserial `
    -out $serverCert -days 825 -sha256 -copy_extensions copy
if ($LASTEXITCODE -ne 0) { throw 'Could not sign the sandbox certificate.' }

Remove-Item -LiteralPath $serverCsr -Force -ErrorAction SilentlyContinue
Write-Host "Certificate created for https://$LanIp`:8005"
Write-Host "Share only this CA certificate with testers: $caCert"
Write-Host 'Never share lumi-lan-ca-key.pem or lumi-lan-key.pem.'
