# Instala o edge de áudio do Jarvis no Windows (o core roda no WSL) e o coloca na inicialização.
# Uso (PowerShell):  powershell -ExecutionPolicy Bypass -File install-windows-edge.ps1 [-Source <caminho ou git url>]
param(
    [string]$Source = "",
    [string]$Core = "ws://127.0.0.1:8766"
)
$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "-> instalando uv"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (-not $Source) {
    # Repositório dentro do WSL (distro padrão), visto pelo Windows.
    $wslHome = (wsl.exe -e sh -c 'wslpath -w ~/dev/jarvis').Trim()
    $Source = $wslHome
}
Write-Host "-> instalando jarvis[edge] de $Source"
uv tool install --force --python 3.11 "jarvis[edge] @ $Source"

Write-Host "-> baixando modelos do edge"
jarvis download edge

# Atalho na pasta Inicializar (sem janela de console).
$startup = [Environment]::GetFolderPath("Startup")
$exe = (Get-Command jarvis-edge).Source
$lnk = Join-Path $startup "Jarvis Edge.lnk"
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $exe
$s.Arguments = "--core $Core"
$s.WindowStyle = 7  # minimizado
$s.Save()
Write-Host "OK: edge instalado. Ele inicia com o Windows ($lnk). Para iniciar agora: jarvis-edge --core $Core"
