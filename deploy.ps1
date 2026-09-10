<#
    Usage: .\deploy.ps1 -Server tanss-host [-DryRun]

    Autor: SO, (c) abasoft GmbH 2026-09-10
    Datei: deploy.ps1
    Beschreibung: Rollt TANSS-Triage nach /home/tanss/listings/TANSS-Triage
                  aus (gleiches Muster wie MCP-Server-TANSS-Remote), legt bei
                  Bedarf das venv an, installiert die Abhängigkeiten und
                  zeigt zum Schluss --version als Selbsttest. .env,
                  config.toml und state.db auf dem Server bleiben unberührt.
                  Mit -DryRun wird nur gezeigt, was passieren würde.
    Letzte Änderung: 2026-09-10
#>

param(
    [string]$Server = $env:TANSS_MCP_SSH_HOST,
    [string]$User = "tanss",
    [int]$Port = 22,
    [string]$Key = "",
    [string]$TargetDir = "/home/tanss/listings/TANSS-Triage",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $Server) {
    Write-Host "FEHLER  Kein Server angegeben. Aufruf:" -ForegroundColor Red
    Write-Host "  .\deploy.ps1 -Server tanss-host"
    Write-Host "  (oder TANSS_MCP_SSH_HOST setzen)"
    exit 2
}

$SourceDir = $PSScriptRoot

# Was ausgerollt wird. .env, config.toml und state.db fehlen mit Absicht:
# das ist Laufzeitzustand des Servers.
$Files = @("pyproject.toml", "VERSION", "CHANGELOG.md", "README.md",
           ".env.example", "config.example.toml")
$Directories = @("src", "deploy")

$SshOptions = @("-p", "$Port")
$ScpOptions = @("-P", "$Port")
if ($Key) {
    if (-not (Test-Path $Key)) {
        Write-Host "FEHLER  Schlüssel $Key gibt es nicht." -ForegroundColor Red
        exit 2
    }
    $SshOptions += @("-i", $Key)
    $ScpOptions += @("-i", $Key)
}
$Address = "$User@$Server"

$Version = (Get-Content (Join-Path $SourceDir "VERSION") -TotalCount 1).Trim()
Write-Host "Ausrollen von TANSS-Triage $Version nach ${Address}:$TargetDir"
Write-Host ""

if ($DryRun) {
    Write-Host "-DryRun gesetzt: es wird nichts kopiert." -ForegroundColor Cyan
    Write-Host "Es würde laufen:"
    Write-Host "  ssh $Address mkdir -p $TargetDir"
    Write-Host "  scp $($Files -join ', ') ${Address}:$TargetDir/"
    Write-Host "  scp -r $($Directories -join ', ') ${Address}:$TargetDir/"
    Write-Host "  ssh $Address 'python3 -m venv .venv && pip install .'"
    Write-Host "  ssh $Address '$TargetDir/.venv/bin/python -m tanss_triage --version'"
    exit 0
}

Write-Host "1/4  Verzeichnis anlegen"
& ssh @SshOptions $Address "mkdir -p $TargetDir"
if ($LASTEXITCODE -ne 0) {
    Write-Host "FEHLER  ssh scheiterte (Code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Write-Host "2/4  Dateien kopieren"
$FullPaths = $Files | ForEach-Object { Join-Path $SourceDir $_ }
& scp @ScpOptions @FullPaths "${Address}:$TargetDir/"
if ($LASTEXITCODE -ne 0) { Write-Host "FEHLER  scp scheiterte." -ForegroundColor Red; exit 1 }
foreach ($directory in $Directories) {
    & scp -r @ScpOptions (Join-Path $SourceDir $directory) "${Address}:$TargetDir/"
    if ($LASTEXITCODE -ne 0) { Write-Host "FEHLER  scp $directory scheiterte." -ForegroundColor Red; exit 1 }
}

Write-Host "3/4  venv und Abhängigkeiten"
$Setup = "cd $TargetDir; " +
         "if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi; " +
         ".venv/bin/pip install --quiet --upgrade pip; " +
         ".venv/bin/pip install --quiet ."
& ssh @SshOptions $Address $Setup
if ($LASTEXITCODE -ne 0) { Write-Host "FEHLER  Installation scheiterte." -ForegroundColor Red; exit 1 }

Write-Host "4/4  Selbsttest"
& ssh @SshOptions $Address "$TargetDir/.venv/bin/python -m tanss_triage --version"
$Code = $LASTEXITCODE
Write-Host ""
if ($Code -eq 0) {
    Write-Host "Ausgerollt. Nächste Schritte auf dem Server:" -ForegroundColor Green
    Write-Host "  - .env und config.toml anlegen (Vorlagen liegen daneben)"
    Write-Host "  - systemd-Unit installieren: deploy/tanss-triage.service"
    Write-Host "  - Dienst neu starten: systemctl restart tanss-triage"
} else {
    Write-Host "Ausgerollt, aber der Selbsttest meldet Fehler (Code $Code)." -ForegroundColor Yellow
}
exit $Code
