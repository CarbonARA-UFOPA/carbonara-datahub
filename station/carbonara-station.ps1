#Requires -Version 5.1
<#
carbonara-station.ps1 — CLIENTE de coleta CARBONARA para Windows, tudo em UM script.

Roda na máquina da ESTAÇÃO e envia os arquivos de dados para o servidor central
(o "host", preparado com o script rsync-hub deste projeto), por rsync-over-SSH.

Papéis:
  1) Assistente guiado — `setup` localiza o rsync (ou orienta a instalação: WSL ou
     cwRsync), pergunta id/servidor/pasta, gera a chave SSH e mostra a CHAVE PÚBLICA
     que o administrador autoriza no servidor (sudo rsync-hub add <id> <pub>).
  2) Envio — `send` manda os arquivos prontos (idade mínima configurável, para não
     pegar arquivo ainda sendo escrito) e, após o sucesso, move-os para .enviados\
     (ou apaga, conforme escolhido) — nada é reenviado, e o rsync --partial retoma
     a transferência em link instável.
  3) Agendamento — `schedule` cria a tarefa no Agendador de Tarefas do Windows.

Uso (PowerShell):
  .\carbonara-station.ps1               # menu interativo
  .\carbonara-station.ps1 setup         # configuração guiada (1ª vez ou reconfigurar)
  .\carbonara-station.ps1 test          # testa a conexão com o servidor (dry-run)
  .\carbonara-station.ps1 send          # envia agora os arquivos pendentes
  .\carbonara-station.ps1 schedule      # agenda o envio periódico
  .\carbonara-station.ps1 unschedule    # remove o agendamento
  .\carbonara-station.ps1 status        # configuração, pendências e últimos envios

Se o Windows bloquear o script ("running scripts is disabled on this system"), use o
atalho carbonara-station.cmd (mesma pasta) — ele executa este .ps1 contornando a
política de execução. Alternativas: liberar uma vez com
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   (+ Unblock-File .\carbonara-station.ps1
  se o arquivo veio por download/pendrive), ou rodar pontualmente com
  powershell -NoProfile -ExecutionPolicy Bypass -File .\carbonara-station.ps1

Configuração gravada em %LOCALAPPDATA%\carbonara-station\config.json.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('menu', 'setup', 'test', 'send', 'schedule', 'unschedule', 'status', 'help')]
    [string]$Command = 'menu'
)

$ErrorActionPreference = 'Stop'
$ConfDir  = Join-Path $env:LOCALAPPDATA 'carbonara-station'
$ConfFile = Join-Path $ConfDir 'config.json'
$LogFile  = Join-Path $ConfDir 'envios.log'
$TaskName = 'CarbonaraStationSend'

# --- Utilitários -----------------------------------------------------------------
function Ask([string]$Pergunta, [string]$Default = '') {
    $r = if ($Default) { Read-Host "$Pergunta [$Default]" } else { Read-Host $Pergunta }
    if ($r) { $r.Trim() } else { $Default }
}

function Ask-YesNo([string]$Pergunta, [bool]$DefaultYes = $true) {
    $h = if ($DefaultYes) { 'S/n' } else { 's/N' }
    $r = Read-Host "$Pergunta [$h]"
    if (-not $r) { return $DefaultYes }
    return $r -match '^[sSyY]'
}

function Write-EnvioLog([string]$Msg) {
    New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    Add-Content -Path $LogFile -Value "$ts $Msg"
}

# Executa um programa nativo capturando saída+erro sem disparar exceção do PowerShell.
function Invoke-Native([string]$Exe, [object[]]$Argv) {
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Argv 2>&1
        return @{ Out = @($out | ForEach-Object { "$_" }); Code = $LASTEXITCODE }
    } finally { $ErrorActionPreference = $eap }
}

function Get-Config([switch]$Optional) {
    if (-not (Test-Path $ConfFile)) {
        if ($Optional) { return $null }
        throw 'estação não configurada — rode: .\carbonara-station.ps1 setup'
    }
    Get-Content $ConfFile -Raw | ConvertFrom-Json
}

function Save-Config($Cfg) {
    New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
    $Cfg | ConvertTo-Json | Set-Content -Path $ConfFile -Encoding UTF8
}

# --- rsync: detecção e caminhos ---------------------------------------------------
# Procura o rsync em duas formas: nativo (cwRsync/MSYS2/scoop no PATH) ou via WSL.
function Find-Rsync {
    $c = Get-Command rsync.exe -ErrorAction SilentlyContinue
    if ($c) { return [pscustomobject]@{ Mode = 'nativo'; Rsync = $c.Source } }
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        $r = Invoke-Native 'wsl.exe' @('-e', 'sh', '-c', 'command -v rsync')
        if ($r.Code -eq 0) { return [pscustomobject]@{ Mode = 'wsl'; Rsync = 'rsync' } }
    }
    return $null
}

function Show-RsyncInstallHelp {
    Write-Host ''
    Write-Host 'O rsync não foi encontrado nesta máquina. Instale por UMA das opções:'
    Write-Host ''
    Write-Host '  Opção A — WSL (recomendada no Windows 10/11):'
    Write-Host '     1. em um PowerShell como ADMINISTRADOR:   wsl --install'
    Write-Host '     2. reinicie, abra o Ubuntu e rode:        sudo apt install -y rsync openssh-client'
    Write-Host '     3. rode este assistente de novo.'
    Write-Host ''
    Write-Host '  Opção B — cwRsync (rsync nativo para Windows, sem WSL):'
    Write-Host '     1. baixe o "cwRsync free client" em https://itefix.net/cwrsync'
    Write-Host '     2. extraia (ex.: C:\cwrsync) e adicione a subpasta bin ao PATH do usuário'
    Write-Host '     3. rode este assistente de novo.'
    Write-Host ''
}

# Converte um caminho Windows para a forma que o rsync em uso entende
# (wslpath no WSL; cygpath ou /cygdrive/... no rsync nativo, que é base cygwin).
function ConvertTo-UnixPath([string]$WinPath, $Cfg) {
    if ($Cfg.Mode -eq 'wsl') {
        $r = Invoke-Native 'wsl.exe' @('-e', 'wslpath', '-a', '--', $WinPath)
        if ($r.Code -ne 0) { throw "wslpath falhou para: $WinPath" }
        return ($r.Out | Select-Object -First 1).Trim()
    }
    $cygpath = Join-Path (Split-Path $Cfg.Rsync) 'cygpath.exe'
    if (Test-Path $cygpath) {
        $r = Invoke-Native $cygpath @('-u', $WinPath)
        if ($r.Code -eq 0) { return ($r.Out | Select-Object -First 1).Trim() }
    }
    if ($WinPath -match '^([A-Za-z]):[\\/](.*)$') {
        return '/cygdrive/' + $Matches[1].ToLower() + '/' + ($Matches[2] -replace '\\', '/')
    }
    return ($WinPath -replace '\\', '/')
}

# Monta a linha de comando do rsync (executável + argumentos) conforme o modo.
function Get-RsyncPlan($Cfg, [string[]]$ExtraArgs, [string]$FilesFromWin) {
    $dest = '{0}@{1}:/' -f $Cfg.HubUser, $Cfg.HubHost
    $sshOpts = "-p $($Cfg.HubPort) -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
    if ($Cfg.Mode -eq 'wsl') {
        # Chave e ssh vivem DENTRO do WSL; os dados são alcançados via /mnt/c/...
        $ssh  = "ssh -i $($Cfg.KeyPath) $sshOpts"
        $argv = @('-e', 'rsync') + $ExtraArgs
        if ($FilesFromWin) { $argv += "--files-from=$(ConvertTo-UnixPath $FilesFromWin $Cfg)" }
        $argv += @('-e', $ssh, "$(ConvertTo-UnixPath $Cfg.DataDir $Cfg)/", $dest)
        return @{ Exe = 'wsl.exe'; Args = $argv }
    }
    # Modo nativo: prefere o ssh que acompanha o rsync (mesma família cygwin);
    # sem ele, cai no OpenSSH do Windows (caminhos com '/').
    $rsyncDir = Split-Path $Cfg.Rsync
    if (Test-Path (Join-Path $rsyncDir 'ssh.exe')) {
        if ($env:PATH -notlike "$rsyncDir*") { $env:PATH = "$rsyncDir;$env:PATH" }
        $keyArg = ConvertTo-UnixPath $Cfg.KeyPath $Cfg
    } else {
        $keyArg = ($Cfg.KeyPath -replace '\\', '/')
    }
    $ssh  = "ssh -i $keyArg $sshOpts"
    $argv = @() + $ExtraArgs
    if ($FilesFromWin) { $argv += "--files-from=$(ConvertTo-UnixPath $FilesFromWin $Cfg)" }
    $argv += @('-e', $ssh, "$(ConvertTo-UnixPath $Cfg.DataDir $Cfg)/", $dest)
    return @{ Exe = $Cfg.Rsync; Args = $argv }
}

# Arquivos elegíveis para envio: fora de pastas ocultas (inclui .enviados) e mais
# velhos que MinAgeMin — mesmo critério de dotfiles da ingestão no servidor.
function Get-PendingFiles($Cfg, [switch]$IgnoreAge) {
    $dataDir = (Get-Item -LiteralPath $Cfg.DataDir).FullName.TrimEnd('\')
    $cut = (Get-Date).AddMinutes(-[int]$Cfg.MinAgeMin)
    Get-ChildItem -LiteralPath $dataDir -Recurse -File | Where-Object {
        ($IgnoreAge -or $_.LastWriteTime -le $cut) -and
        $_.Name -notlike '.*' -and
        $_.FullName.Substring($dataDir.Length) -notmatch '[\\/]\.'
    }
}

# --- Configuração guiada -----------------------------------------------------------
function Invoke-Setup {
    Write-Host ''
    Write-Host '===== Configuração guiada da estação (Windows) ====='
    Write-Host 'Você vai precisar de: o ENDEREÇO do servidor central e o ID desta estação'
    Write-Host '(combinados com o administrador do servidor).'
    Write-Host ''
    $rs = Find-Rsync
    if (-not $rs) { Show-RsyncInstallHelp; return }
    Write-Host ("rsync encontrado (modo {0})." -f $rs.Mode)
    Write-Host ''

    $old = Get-Config -Optional
    do {
        $id = Ask 'Id desta estação (minúsculas/dígitos/_ , ex.: flona01)' $(if ($old) { $old.StationId })
        $okId = $id -cmatch '^[a-z0-9][a-z0-9_]{1,30}$'
        if (-not $okId) { Write-Host "  id inválido — use minúsculas, dígitos e '_' (2 a 31 caracteres)." }
    } until ($okId)
    do {
        $hubHost = Ask 'Endereço do servidor central (IP ou hostname)' $(if ($old) { $old.HubHost })
        if (-not $hubHost) { Write-Host '  informe um endereço.' }
    } until ($hubHost)
    $hubPort = Ask 'Porta SSH do servidor' $(if ($old -and $old.HubPort) { $old.HubPort } else { '22' })
    $hubUser = Ask 'Conta de serviço no servidor' $(if ($old -and $old.HubUser) { $old.HubUser } else { 'carbonara' })
    do {
        $default = if ($old) { $old.DataDir } else { Join-Path $env:USERPROFILE 'carbonara-dados' }
        $dataDir = Ask 'Pasta LOCAL onde ficam os dados a enviar' $default
        if (-not (Test-Path -LiteralPath $dataDir)) {
            if (Ask-YesNo "  a pasta não existe — criar $dataDir?") {
                New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
            }
        }
    } until (Test-Path -LiteralPath $dataDir)
    do {
        $minAge = Ask 'Idade mínima (minutos) para um arquivo ser considerado pronto' $(if ($old -and $old.MinAgeMin) { $old.MinAgeMin } else { '1' })
    } until ($minAge -match '^\d+$')
    $keep = Ask-YesNo 'Manter cópia local após o envio (move para .enviados)?' $true

    # Par de chaves SSH da estação
    if ($rs.Mode -eq 'wsl') {
        $keyPath = "~/.ssh/carbonara_$id"    # dentro do WSL
        Write-Host ''
        Write-Host "Gerando o par de chaves SSH desta estação (no WSL: $keyPath)…"
        $r = Invoke-Native 'wsl.exe' @('-e', 'sh', '-c',
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && { [ -f $keyPath ] || ssh-keygen -q -t ed25519 -N '' -C carbonara-$id -f $keyPath; }")
        if ($r.Code -ne 0) { throw "falha ao gerar a chave no WSL: $($r.Out -join ' ')" }
        $r = Invoke-Native 'wsl.exe' @('-e', 'sh', '-c', "cat $keyPath.pub")
        if ($r.Code -ne 0) { throw 'falha ao ler a chave pública no WSL' }
        $pub = ($r.Out -join "`n").Trim()
    } else {
        if (-not (Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue)) {
            throw "ssh-keygen não encontrado — instale o 'Cliente OpenSSH' (Configurações > Aplicativos > Recursos Opcionais)."
        }
        $sshDir = Join-Path $env:USERPROFILE '.ssh'
        New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
        $keyPath = Join-Path $sshDir "carbonara_$id"
        if (-not (Test-Path -LiteralPath $keyPath)) {
            Write-Host ''
            Write-Host "Gerando o par de chaves SSH desta estação ($keyPath)…"
            $r = Invoke-Native 'ssh-keygen.exe' @('-q', '-t', 'ed25519', '-N', '', '-C', "carbonara-$id", '-f', $keyPath)
            if ($r.Code -ne 0) { throw "falha ao gerar a chave SSH: $($r.Out -join ' ')" }
        }
        # Restringe a chave ao usuário atual (o ssh recusa chave com permissão aberta).
        Invoke-Native 'icacls.exe' @($keyPath, '/inheritance:r', '/grant:r', "$($env:USERNAME):F") | Out-Null
        $pub = (Get-Content -LiteralPath "$keyPath.pub" -Raw).Trim()
    }

    Save-Config ([pscustomobject]@{
        StationId = $id;      HubHost = $hubHost; HubPort = $hubPort; HubUser = $hubUser
        DataDir   = $dataDir; MinAgeMin = $minAge; KeepCopy = $keep
        Mode      = $rs.Mode; Rsync = $rs.Rsync;   KeyPath = $keyPath
    })
    Write-Host ''
    Write-Host "ok: configuração gravada em $ConfFile."
    Write-Host ''
    Write-Host '===== FALTA UM PASSO, NO SERVIDOR ====='
    Write-Host 'Entregue a CHAVE PÚBLICA abaixo ao administrador do servidor, que autoriza com:'
    Write-Host "    sudo rsync-hub add $id <arquivo-com-a-chave>.pub"
    Write-Host "(ou colando a linha no menu do rsync-hub → 'Adicionar estação')"
    Write-Host ''
    Write-Host '--- chave pública (copie a linha inteira) ---'
    Write-Host $pub
    Write-Host '---------------------------------------------'
    Write-Host ''
    if (Ask-YesNo 'A estação já foi autorizada no servidor? Testar a conexão agora?' $false) {
        Invoke-Test
    } else {
        Write-Host 'quando ela for autorizada, valide com: .\carbonara-station.ps1 test'
    }
}

# --- Teste de conexão ----------------------------------------------------------------
function Invoke-Test {
    $cfg = Get-Config
    Write-Host ("testando conexão com {0}@{1}:{2} (dry-run, nada é enviado)…" -f $cfg.HubUser, $cfg.HubHost, $cfg.HubPort)
    $plan = Get-RsyncPlan $cfg @('-an', '--timeout=30') $null
    $r = Invoke-Native $plan.Exe $plan.Args
    if ($r.Code -eq 0) {
        Write-Host 'ok: conexão e permissões funcionando.'
    } else {
        Write-Host 'falhou:'
        $r.Out | ForEach-Object { Write-Host "  $_" }
        Write-Host 'verifique:'
        Write-Host ("  - a chave pública já foi autorizada no servidor? (sudo rsync-hub add {0} …)" -f $cfg.StationId)
        Write-Host ("  - endereço ({0}) e porta ({1}) corretos? há rede até o servidor?" -f $cfg.HubHost, $cfg.HubPort)
    }
}

# --- Envio -----------------------------------------------------------------------------
function Invoke-Send {
    $cfg = Get-Config
    $mtx = New-Object System.Threading.Mutex($false, 'CarbonaraStationSend')
    if (-not $mtx.WaitOne(0)) { Write-Host 'outro envio já está em andamento — saindo.'; return }
    try {
        $dataDir = (Get-Item -LiteralPath $cfg.DataDir).FullName.TrimEnd('\')
        $files = @(Get-PendingFiles $cfg)
        if ($files.Count -eq 0) {
            Write-Host ("nada a enviar (arquivos com menos de {0} min de idade não contam)." -f $cfg.MinAgeMin)
            return
        }
        $rel = $files | ForEach-Object { $_.FullName.Substring($dataDir.Length + 1) -replace '\\', '/' }
        New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
        $list = Join-Path $ConfDir 'files-from.txt'
        # UTF-8 SEM BOM — um BOM corromperia o primeiro caminho da lista no rsync.
        [IO.File]::WriteAllLines($list, [string[]]$rel, (New-Object System.Text.UTF8Encoding($false)))

        Write-Host ("enviando {0} arquivo(s) para {1}@{2}…" -f $files.Count, $cfg.HubUser, $cfg.HubHost)
        $plan = Get-RsyncPlan $cfg @('-az', '--partial', '--timeout=120') $list
        $r = Invoke-Native $plan.Exe $plan.Args
        $r.Out | ForEach-Object { Write-Host "  $_" }
        if ($r.Code -eq 0) {
            foreach ($p in $rel) {
                $srcWin = Join-Path $dataDir ($p -replace '/', '\')
                if ($cfg.KeepCopy) {
                    $dst = Join-Path (Join-Path $dataDir '.enviados') ($p -replace '/', '\')
                    New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
                    Move-Item -LiteralPath $srcWin -Destination $dst -Force
                } else {
                    Remove-Item -LiteralPath $srcWin -Force
                }
            }
            Write-EnvioLog ("OK {0} arquivo(s) enviados" -f $files.Count)
            Write-Host ("ok: {0} arquivo(s) enviados." -f $files.Count)
        } else {
            Write-EnvioLog ("FALHA rc={0} ({1} arquivo(s) pendentes)" -f $r.Code, $files.Count)
            Write-Host ("falha no envio (rsync rc={0}) — nada foi movido; será tentado de novo no próximo ciclo." -f $r.Code)
        }
    } finally {
        $mtx.ReleaseMutex() | Out-Null
        $mtx.Dispose()
    }
}

# --- Agendamento (Agendador de Tarefas) --------------------------------------------------
function Invoke-Schedule {
    $null = Get-Config
    do {
        $iv = Ask 'Enviar a cada quantos minutos? (1-1439)' '10'
    } until ($iv -match '^\d+$' -and [int]$iv -ge 1 -and [int]$iv -le 1439)
    $tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" send"
    $r = Invoke-Native 'schtasks.exe' @('/Create', '/F', '/SC', 'MINUTE', '/MO', $iv, '/TN', $TaskName, '/TR', $tr)
    if ($r.Code -eq 0) {
        Write-Host ("ok: envio agendado a cada {0} min (Agendador de Tarefas, tarefa '{1}')." -f $iv, $TaskName)
        Write-Host 'para remover: .\carbonara-station.ps1 unschedule'
    } else {
        $r.Out | ForEach-Object { Write-Host "  $_" }
        Write-Host 'falha ao criar a tarefa agendada — tente em um PowerShell como administrador.'
    }
}

function Invoke-Unschedule {
    Invoke-Native 'schtasks.exe' @('/Delete', '/F', '/TN', $TaskName) | Out-Null
    Write-Host 'ok: agendamento removido (se existia).'
}

# --- Status ------------------------------------------------------------------------------
function Invoke-Status {
    $cfg = Get-Config
    $pos = if ($cfg.KeepCopy) { 'move para .enviados\' } else { 'apaga' }
    Write-Host ("estação:   {0}" -f $cfg.StationId)
    Write-Host ("servidor:  {0}@{1}:{2}" -f $cfg.HubUser, $cfg.HubHost, $cfg.HubPort)
    Write-Host ("rsync:     modo {0} ({1})" -f $cfg.Mode, $cfg.Rsync)
    Write-Host ("chave:     {0}" -f $cfg.KeyPath)
    Write-Host ("dados:     {0} (pós-envio: {1})" -f $cfg.DataDir, $pos)
    $pend = @(Get-PendingFiles $cfg -IgnoreAge).Count
    Write-Host ("pendentes: {0} arquivo(s)" -f $pend)
    $r = Invoke-Native 'schtasks.exe' @('/Query', '/TN', $TaskName)
    if ($r.Code -eq 0) {
        Write-Host ("agenda:    tarefa '{0}' ativa (Agendador de Tarefas)" -f $TaskName)
    } else {
        Write-Host "agenda:    (não agendado — use 'schedule')"
    }
    Write-Host 'últimos envios:'
    if (Test-Path $LogFile) {
        Get-Content $LogFile -Tail 5 | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host '  (nenhum envio registrado)'
    }
}

# --- Menu interativo ----------------------------------------------------------------------
function Show-Menu {
    while ($true) {
        Write-Host ''
        Write-Host '===== carbonara-station — estação (cliente Windows) ====='
        Write-Host ' 1) Configurar esta estação (guiado)'
        Write-Host ' 2) Testar conexão com o servidor'
        Write-Host ' 3) Enviar agora'
        Write-Host ' 4) Agendar envio periódico'
        Write-Host ' 5) Remover agendamento'
        Write-Host ' 6) Status'
        Write-Host ' 0) Sair'
        $op = Read-Host 'opção'
        try {
            switch ($op) {
                '1' { Invoke-Setup }
                '2' { Invoke-Test }
                '3' { Invoke-Send }
                '4' { Invoke-Schedule }
                '5' { Invoke-Unschedule }
                '6' { Invoke-Status }
                '0' { return }
                ''  { return }
                default { Write-Host 'opção inválida' }
            }
        } catch {
            Write-Host ("erro: {0}" -f $_.Exception.Message)
        }
    }
}

function Show-Help {
    Write-Host @"
carbonara-station — cliente de coleta CARBONARA (Windows)

uso (PowerShell):
  .\carbonara-station.ps1 setup         configuração guiada (1ª vez ou reconfigurar)
  .\carbonara-station.ps1 test          testa a conexão com o servidor (dry-run)
  .\carbonara-station.ps1 send          envia agora os arquivos pendentes
  .\carbonara-station.ps1 schedule      agenda o envio periódico
  .\carbonara-station.ps1 unschedule    remove o agendamento
  .\carbonara-station.ps1 status        configuração, pendências e últimos envios
  .\carbonara-station.ps1               menu interativo

requisitos: rsync (WSL ou cwRsync — o setup orienta) e OpenSSH (nativo no Win10+).
configuração em $ConfFile
"@
}

# --- Despacho -------------------------------------------------------------------------------
try {
    switch ($Command) {
        'setup'      { Invoke-Setup }
        'test'       { Invoke-Test }
        'send'       { Invoke-Send }
        'schedule'   { Invoke-Schedule }
        'unschedule' { Invoke-Unschedule }
        'status'     { Invoke-Status }
        'menu'       { Show-Menu }
        'help'       { Show-Help }
    }
} catch {
    Write-Host ("erro: {0}" -f $_.Exception.Message)
    exit 1
}
