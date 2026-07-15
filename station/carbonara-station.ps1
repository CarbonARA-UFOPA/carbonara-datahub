#Requires -Version 5.1
<#
carbonara-station.ps1 — CLIENTE de coleta CARBONARA para Windows, tudo em UM script.

Roda na máquina da ESTAÇÃO e envia os arquivos de dados para o servidor central
(o "host", preparado com o script rsync-hub deste projeto), por rsync-over-SSH.

O transporte usa o cwRsync (rsync nativo para Windows, base cygwin). Na PRIMEIRA vez,
o `setup` baixa e instala o cwRsync automaticamente em C:\cwrsync — para isso é
preciso rodar o PowerShell COMO ADMINISTRADOR (o assistente avisa e orienta). Depois
de instalado, todo o resto (setup/test/send/schedule) funciona sem administrador.

Papéis:
  1) Assistente guiado — `setup` instala/localiza o rsync e pergunta, nesta ordem:
     id da estação → IP do host (porta opcional, padrão 22) → caminho local da CHAVE
     recebida do servidor (gerada lá pelo `rsync-hub add <id>`; se não existir,
     oferece gerar uma nova aqui) → TESTE simples de conexão → pasta local a enviar
     (rsync) → tempo de reciclagem (minutos) + ativação do envio automático.
  2) Envio — `send` manda os arquivos prontos (idade mínima configurável) e, após o
     sucesso, move-os para .enviados\ (ou apaga, conforme escolhido) — nada é
     reenviado, e o rsync --partial retoma transferências em link instável.
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
política de execução.

Configuração gravada em %LOCALAPPDATA%\carbonara-station\config.json.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('menu', 'setup', 'test', 'send', 'schedule', 'unschedule', 'status', 'help')]
    [string]$Command = 'menu'
)

$ErrorActionPreference = 'Stop'
$ConfDir    = Join-Path $env:LOCALAPPDATA 'carbonara-station'
$ConfFile   = Join-Path $ConfDir 'config.json'
$LogFile    = Join-Path $ConfDir 'envios.log'
$TaskName   = 'CarbonaraStationSend'
$CwRsyncDir = 'C:\cwrsync'
$CwRsyncUrl = 'https://itefix.net/sites/default/files/2026-06/cwrsync_6.4.8_x64_free.zip'

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

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Executa um programa nativo capturando saída+erro sem disparar exceção do PowerShell.
function Invoke-Native([string]$Exe, [object[]]$Argv) {
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Argv 2>&1
        return @{ Out = @($out | ForEach-Object { "$_" }); Code = $LASTEXITCODE }
    } finally { $ErrorActionPreference = $eap }
}

# Como Invoke-Native, mas à prova de TRAVAMENTO: stdin fechado (nada pode "perguntar"
# e ficar esperando) e tempo-limite com kill.
function Invoke-NativeQuiet([string]$Exe, [object[]]$Argv, [int]$TimeoutSec = 30) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = ($Argv | ForEach-Object {
        $a = "$_"
        if ($a -match '[\s"]') { '"' + ($a -replace '"', '\"') + '"' } else { $a }
    }) -join ' '
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardInput  = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    try { $p = [System.Diagnostics.Process]::Start($psi) }
    catch { return @{ Out = @("$($_.Exception.Message)"); Code = -1; TimedOut = $false } }
    $p.StandardInput.Close()
    $outTask = $p.StandardOutput.ReadToEndAsync()
    $errTask = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        try { $p.Kill() } catch {}
        return @{ Out = @(); Code = -1; TimedOut = $true }
    }
    $text = $outTask.Result + "`n" + $errTask.Result
    $lines = @($text -split "`r?`n" | ForEach-Object { $_.Trim([char]0) } | Where-Object { $_ })
    return @{ Out = $lines; Code = $p.ExitCode; TimedOut = $false }
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

# --- cwRsync: localização e instalação automática ----------------------------------
# O cliente usa SEMPRE o cwRsync (rsync nativo para Windows). Ordem de busca:
# a instalação padrão deste projeto (C:\cwrsync\bin\rsync.exe) e depois o PATH.
function Find-Rsync {
    $std = Join-Path $CwRsyncDir 'bin\rsync.exe'
    if (Test-Path -LiteralPath $std) { return $std }
    if (Test-Path -LiteralPath $CwRsyncDir) {
        $hit = Get-ChildItem -Path $CwRsyncDir -Recurse -Filter 'rsync.exe' -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    $c = Get-Command rsync.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

# Baixa e extrai o cwRsync para C:\cwrsync. Precisa de PowerShell como ADMINISTRADOR
# (criar C:\cwrsync exige elevação) — se não for o caso, orienta e devolve $null.
function Install-CwRsync {
    if (-not (Test-IsAdmin)) {
        Write-Host ''
        Write-Host '*** PRIMEIRA CONFIGURAÇÃO: é preciso instalar o rsync (cwRsync). ***'
        Write-Host ''
        Write-Host 'Para isso, abra o PowerShell COMO ADMINISTRADOR:'
        Write-Host '    menu Iniciar → digite "PowerShell" → clique com o botão DIREITO'
        Write-Host '    → "Executar como administrador"'
        Write-Host ''
        Write-Host 'e, na janela que abrir, rode o setup de novo:'
        Write-Host "    cd `"$PSScriptRoot`""
        Write-Host '    .\carbonara-station.cmd setup'
        Write-Host ''
        Write-Host '(só esta primeira vez precisa de administrador; o uso normal, não.)'
        return $null
    }
    Write-Host ''
    Write-Host "O cwRsync será baixado e instalado em $CwRsyncDir (uma vez só)."
    if (-not (Ask-YesNo 'Baixar e instalar agora?' $true)) { Write-Host 'instalação cancelada.'; return $null }

    $zip = Join-Path $env:TEMP 'cwrsync_free.zip'
    Write-Host "baixando de $CwRsyncUrl (aprox. 5 MB)…"
    $pp = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
    try {
        [Net.ServicePointManager]::SecurityProtocol = `
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $CwRsyncUrl -OutFile $zip -UseBasicParsing
    } catch {
        Write-Host "falha no download: $($_.Exception.Message)"
        Write-Host ''
        Write-Host 'Sem internet nesta máquina? Instale manualmente:'
        Write-Host '  1. em outra máquina, baixe o "cwRsync free client": https://itefix.net/cwrsync'
        Write-Host "  2. extraia o zip para $CwRsyncDir (deve existir $CwRsyncDir\bin\rsync.exe)"
        Write-Host '  3. rode o setup de novo.'
        return $null
    } finally { $ProgressPreference = $pp }

    Write-Host "extraindo para $CwRsyncDir…"
    try {
        New-Item -ItemType Directory -Force -Path $CwRsyncDir | Out-Null
        Expand-Archive -Path $zip -DestinationPath $CwRsyncDir -Force
    } catch {
        Write-Host "falha ao extrair: $($_.Exception.Message)"
        return $null
    } finally {
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
    }
    $r = Find-Rsync
    if ($r) { Write-Host "ok: rsync instalado ($r)." } else { Write-Host "não encontrei rsync.exe após a extração — confira $CwRsyncDir." }
    return $r
}

# Converte um caminho Windows para a forma cygwin que o cwRsync entende:
# C:\pasta\arq → /cygdrive/c/pasta/arq
function ConvertTo-UnixPath([string]$WinPath) {
    if ($WinPath -match '^([A-Za-z]):[\\/](.*)$') {
        return '/cygdrive/' + $Matches[1].ToLower() + '/' + ($Matches[2] -replace '\\', '/')
    }
    return ($WinPath -replace '\\', '/')
}

# Monta a linha de comando do rsync (executável + argumentos).
# $SourceDir permite trocar a origem (ex.: pasta vazia no teste de conexão).
function Get-RsyncPlan($Cfg, [string[]]$ExtraArgs, [string]$FilesFromWin, [string]$SourceDir = '') {
    if (-not $SourceDir) { $SourceDir = $Cfg.DataDir }
    $dest = '{0}@{1}:/' -f $Cfg.HubUser, $Cfg.HubHost
    $rsyncDir = Split-Path $Cfg.Rsync
    # Receita do próprio cwRsync: bin\ no PATH (DLLs cygwin) e HOME definido
    # (o ssh cygwin usa HOME para gravar o known_hosts).
    if ($env:PATH -notlike "$rsyncDir*") { $env:PATH = "$rsyncDir;$env:PATH" }
    if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
    $keyArg = ConvertTo-UnixPath $Cfg.KeyPath
    $ssh  = "ssh -i $keyArg -p $($Cfg.HubPort) -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
    $argv = @() + $ExtraArgs
    if ($FilesFromWin) { $argv += "--files-from=$(ConvertTo-UnixPath $FilesFromWin)" }
    $argv += @('-e', $ssh, "$(ConvertTo-UnixPath $SourceDir)/", $dest)
    return @{ Exe = $Cfg.Rsync; Args = $argv }
}

# Teste de conexão que NÃO depende da pasta de dados: dry-run a partir de uma pasta
# vazia — valida rede, porta, chave e o confinamento no servidor, sem enviar nada.
function Invoke-Probe($Cfg) {
    $tmp = Join-Path $env:TEMP ("carbonara-probe-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        $plan = Get-RsyncPlan $Cfg @('-an', '--timeout=20') '' $tmp
        $r = Invoke-NativeQuiet $plan.Exe $plan.Args 90
        if ($r.TimedOut) { Write-Host 'falhou: o teste não respondeu a tempo (rede?).'; return $false }
        if ($r.Code -eq 0) {
            Write-Host 'ok: o servidor respondeu e aceitou a chave.'
            return $true
        }
        Write-Host 'falhou:'
        $r.Out | ForEach-Object { Write-Host "  $_" }
        return $false
    } finally {
        Remove-Item -LiteralPath $tmp -Force -Recurse -ErrorAction SilentlyContinue
    }
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
    Write-Host 'Você vai precisar de: o ENDEREÇO (IP) do servidor central, o ID desta estação'
    Write-Host "e o arquivo de CHAVE recebido do servidor (gerado lá por 'rsync-hub add <id>',"
    Write-Host 'que também cria um INSTRUCOES.txt com esses dados).'
    Write-Host ''

    # 0. rsync (cwRsync) — instala automaticamente na primeira vez
    $rsyncPath = Find-Rsync
    if (-not $rsyncPath) {
        $rsyncPath = Install-CwRsync
        if (-not $rsyncPath) { return }
        Write-Host ''
    }
    Write-Host ("rsync: {0}" -f $rsyncPath)
    Write-Host ''

    $old = Get-Config -Optional

    # 1. Identificação
    do {
        $id = Ask 'Id desta estação (minúsculas/dígitos/_ , ex.: flona01)' $(if ($old) { $old.StationId })
        $okId = $id -cmatch '^[a-z0-9][a-z0-9_]{1,30}$'
        if (-not $okId) { Write-Host "  id inválido — use minúsculas, dígitos e '_' (2 a 31 caracteres)." }
    } until ($okId)

    # 2. Servidor (IP + porta, padrão 22)
    do {
        $hubHost = Ask 'IP (ou hostname) do servidor central' $(if ($old) { $old.HubHost })
        if (-not $hubHost) { Write-Host '  informe um endereço.' }
    } until ($hubHost)
    $hubPort = Ask 'Porta SSH do servidor' $(if ($old -and $old.HubPort) { $old.HubPort } else { '22' })
    $hubUser = Ask 'Conta de serviço no servidor' $(if ($old -and $old.HubUser) { $old.HubUser } else { 'carbonara' })

    # 3. Chave de conexão (recebida do servidor; se não existir, oferece gerar aqui)
    $newKey = $false
    $keyPath = $null
    while (-not $keyPath) {
        $keyWin = Ask ("Caminho local da CHAVE desta estação (arquivo {0}_key recebido do servidor)" -f $id)
        if (-not $keyWin) { Write-Host '  informe um caminho.'; continue }
        if (Test-Path -LiteralPath $keyWin) {
            $keyPath = (Get-Item -LiteralPath $keyWin).FullName
            # Restringe a chave ao usuário atual (o ssh recusa chave com permissão aberta).
            Invoke-Native 'icacls.exe' @($keyPath, '/inheritance:r', '/grant:r', "$($env:USERNAME):F") | Out-Null
        } else {
            Write-Host "  não encontrei a chave em: $keyWin"
            if (Ask-YesNo '  Gerar um NOVO par de chaves aqui (a pública precisará ser autorizada no servidor)?' $false) {
                # ssh-keygen: o do Windows (OpenSSH nativo) ou o que acompanha o cwRsync.
                $sshDir = Join-Path $env:USERPROFILE '.ssh'
                New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
                $keyPath = Join-Path $sshDir "carbonara_$id"
                if (-not (Test-Path -LiteralPath $keyPath)) {
                    $winKeygen = Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue
                    if ($winKeygen) {
                        $r = Invoke-NativeQuiet $winKeygen.Source @('-q', '-t', 'ed25519', '-N', '', '-C', "carbonara-$id", '-f', $keyPath) 30
                    } else {
                        $cwKeygen = Join-Path (Split-Path $rsyncPath) 'ssh-keygen.exe'
                        if (-not (Test-Path -LiteralPath $cwKeygen)) { throw 'ssh-keygen não encontrado (nem no Windows, nem no cwRsync).' }
                        if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
                        $r = Invoke-NativeQuiet $cwKeygen @('-q', '-t', 'ed25519', '-N', '', '-C', "carbonara-$id", '-f', (ConvertTo-UnixPath $keyPath)) 30
                    }
                    if ($r.TimedOut -or $r.Code -ne 0) { throw "falha ao gerar a chave SSH: $($r.Out -join ' ')" }
                }
                Invoke-Native 'icacls.exe' @($keyPath, '/inheritance:r', '/grant:r', "$($env:USERNAME):F") | Out-Null
                $script:pubKey = (Get-Content -LiteralPath "$keyPath.pub" -Raw).Trim()
                $newKey = $true
            }
        }
    }

    # 4. Teste simples de conexão (o host responde? a chave é aceita?)
    Write-Host ''
    $probeCfg = [pscustomobject]@{
        Rsync = $rsyncPath; KeyPath = $keyPath
        HubUser = $hubUser; HubHost = $hubHost; HubPort = $hubPort; DataDir = $env:TEMP
    }
    if ($newKey) {
        Write-Host '(chave nova, ainda não autorizada no servidor — o teste fica para depois.)'
    } elseif (Ask-YesNo 'Testar a conexão com o servidor agora?' $true) {
        if (-not (Invoke-Probe $probeCfg)) {
            Write-Host '  dicas: a estação foi autorizada no servidor? IP/porta corretos? há rede?'
            if (-not (Ask-YesNo '  Continuar a configuração mesmo assim?' $true)) {
                Write-Host 'configuração interrompida.'
                return
            }
        }
    }
    Write-Host ''

    # 5. Pasta local que será enviada (rsync)
    do {
        $default = if ($old) { $old.DataDir } else { Join-Path $env:USERPROFILE 'carbonara-dados' }
        $dataDir = Ask 'Pasta LOCAL que deseja enviar por rsync' $default
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

    # 6. Tempo de reciclagem (intervalo do envio automático)
    do {
        $every = Ask 'Tempo de reciclagem do envio — a cada quantos minutos? (1-1439)' $(if ($old -and $old.SendEveryMin) { $old.SendEveryMin } else { '10' })
    } until ($every -match '^\d+$' -and [int]$every -ge 1 -and [int]$every -le 1439)

    Save-Config ([pscustomobject]@{
        StationId = $id;      HubHost = $hubHost; HubPort = $hubPort; HubUser = $hubUser
        DataDir   = $dataDir; MinAgeMin = $minAge; KeepCopy = $keep;  SendEveryMin = $every
        Rsync     = $rsyncPath; KeyPath = $keyPath
    })
    Write-Host ''
    Write-Host "ok: configuração gravada em $ConfFile."

    if ($newKey) {
        Write-Host ''
        Write-Host '===== FALTA UM PASSO, NO SERVIDOR ====='
        Write-Host 'A chave foi gerada AQUI, então o administrador precisa autorizar a pública:'
        Write-Host "    sudo rsync-hub add $id <arquivo-com-a-chave>.pub"
        Write-Host "(ou colando a linha no menu do rsync-hub → 'Adicionar estação')"
        Write-Host ''
        Write-Host '--- chave pública (copie a linha inteira) ---'
        Write-Host $script:pubKey
        Write-Host '---------------------------------------------'
        Write-Host ''
        Write-Host 'depois de autorizada, valide com: .\carbonara-station.ps1 test'
    }

    Write-Host ''
    if (Ask-YesNo ("Ativar o envio automático agora (a cada {0} min, via Agendador de Tarefas)?" -f $every) $true) {
        Install-Schedule $every
    } else {
        Write-Host "quando quiser ativar: .\carbonara-station.ps1 schedule   (ou envie manualmente com 'send')"
    }
}

# --- Teste de conexão ----------------------------------------------------------------
function Invoke-Test {
    $cfg = Get-Config
    Write-Host ("testando conexão com {0}@{1}:{2} (dry-run, nada é enviado)…" -f $cfg.HubUser, $cfg.HubHost, $cfg.HubPort)
    if (-not (Invoke-Probe $cfg)) {
        Write-Host 'verifique:'
        Write-Host ("  - a estação foi autorizada no servidor? (lá: sudo rsync-hub add {0} …)" -f $cfg.StationId)
        Write-Host ("  - a chave é a recebida do servidor? ({0})" -f $cfg.KeyPath)
        Write-Host ("  - IP ({0}) e porta ({1}) corretos? há rede até o servidor?" -f $cfg.HubHost, $cfg.HubPort)
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
function Install-Schedule([string]$Every) {  # instala/substitui a tarefa de envio periódico
    $tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" send"
    $r = Invoke-Native 'schtasks.exe' @('/Create', '/F', '/SC', 'MINUTE', '/MO', $Every, '/TN', $TaskName, '/TR', $tr)
    if ($r.Code -eq 0) {
        Write-Host ("ok: envio automático ativo a cada {0} min (Agendador de Tarefas, tarefa '{1}')." -f $Every, $TaskName)
        Write-Host 'para remover: .\carbonara-station.ps1 unschedule'
    } else {
        $r.Out | ForEach-Object { Write-Host "  $_" }
        Write-Host 'falha ao criar a tarefa agendada — tente em um PowerShell como administrador.'
    }
}

function Invoke-Schedule {
    $cfg = Get-Config
    do {
        $iv = Ask 'Enviar a cada quantos minutos? (1-1439)' $(if ($cfg.SendEveryMin) { $cfg.SendEveryMin } else { '10' })
    } until ($iv -match '^\d+$' -and [int]$iv -ge 1 -and [int]$iv -le 1439)
    Install-Schedule $iv
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
    Write-Host ("rsync:     {0}" -f $cfg.Rsync)
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

transporte: cwRsync (instalado automaticamente pelo setup em $CwRsyncDir;
a primeira instalação pede PowerShell como administrador).
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
