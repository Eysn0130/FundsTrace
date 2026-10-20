# Disposable hosted VM fixture. Never load or trust a production certificate/key.
function New-FundsTraceUpdateCertificate([string]$Directory, [scriptblock]$OnStage = {}) {
    $ErrorActionPreference = 'Stop'
    if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true') { throw 'Disposable Windows CI only.' }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'CI administrator prepares the shared test CA.' }
    if (-not [IO.Path]::IsPathRooted($Directory) -or (Test-Path -LiteralPath $Directory)) { throw 'A new absolute fixture directory is required.' }
    $certificate = @{ Directory=$Directory; RootCert=$null; LeafCert=$null; PublicRoot=$null; Trusted=$false }
    try {
        [void](New-Item -ItemType Directory -Path $Directory)
        & $OnStage 'createRoot'
        $certificate.RootCert = New-SelfSignedCertificate -Type Custom -Subject ('CN=SYN FundsTrace update ' + [guid]::NewGuid().ToString('N')) `
            -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 `
            -KeyUsage CertSign,CRLSign,DigitalSignature -KeyExportPolicy Exportable `
            -TextExtension @('2.5.29.19={critical}{text}ca=true') -NotAfter ([DateTime]::UtcNow.AddDays(2))
        & $OnStage 'createLeaf'
        $certificate.LeafCert = New-SelfSignedCertificate -Type Custom -DnsName 'analytix.top','download.analytix.top' -Signer $certificate.RootCert `
            -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 `
            -KeyUsage DigitalSignature,KeyEncipherment -KeyExportPolicy Exportable `
            -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.1') -NotAfter ([DateTime]::UtcNow.AddDays(1))
        $rootFile = Join-Path $Directory 'root.cer'
        [void](Export-Certificate -Cert $certificate.RootCert -FilePath $rootFile)
        $certificate.PublicRoot = [Security.Cryptography.X509Certificates.X509Certificate2]::new([IO.File]::ReadAllBytes($rootFile))
        & $OnStage 'addMachineRootTrust'
        $store = [Security.Cryptography.X509Certificates.X509Store]::new('Root',[Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine)
        try {
            $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            $store.Add($certificate.PublicRoot); $certificate.Trusted=$true
        } finally { $store.Dispose() }
        & $OnStage 'exportPfx'
        $certificate.Passphrase = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
        $certificate.Pfx = Join-Path $Directory 'fixture.pfx'
        [void](Export-PfxCertificate -Cert $certificate.LeafCert -FilePath $certificate.Pfx -CryptoAlgorithmOption AES256_SHA256 `
            -Password (ConvertTo-SecureString $certificate.Passphrase -AsPlainText -Force))
        return $certificate
    } catch { Remove-FundsTraceUpdateCertificate $certificate; throw }
}

function Remove-FundsTraceUpdateCertificate($Certificate) {
    if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true') { throw 'Disposable Windows CI only.' }
    if ($Certificate.Trusted) {
        $store = [Security.Cryptography.X509Certificates.X509Store]::new('Root',[Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine)
        try {
            $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            $store.Remove($Certificate.PublicRoot)
        } finally { $store.Dispose() }
    }
    if ($null -ne $Certificate.PublicRoot) { $Certificate.PublicRoot.Dispose() }
    foreach ($cert in @($Certificate.LeafCert,$Certificate.RootCert)) {
        if ($null -ne $cert) { Remove-Item -LiteralPath ('Cert:\CurrentUser\My\' + $cert.Thumbprint) -DeleteKey }
    }
    if (Test-Path -LiteralPath $Certificate.Directory) { Remove-Item -LiteralPath $Certificate.Directory -Recurse -Force }
}
