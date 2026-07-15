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
     id da estação → IP do host → como autenticar: CHAVE SSH (porta 22; arquivo
     recebido do servidor, gerado lá pelo `rsync-hub add <id>`) OU USUÁRIO+SENHA
     (porta 873, rsync direto; criado lá pelo `rsync-hub add <id> --senha` — mais
     simples, porém dados sem criptografia) → TESTE simples de conexão → pasta local
     a enviar (rsync) → tempo de reciclagem (minutos) + ativação do envio automático.
  2) Envio — `send` SINCRONIZA a pasta local com o servidor: NUNCA move nem apaga
     nada da pasta local. O controle do que já foi enviado é um estado local
     (tamanho+mtime por arquivo, em %LOCALAPPDATA%\carbonara-station\sent.state):
     só vai o que é novo ou mudou desde o último envio. Idade mínima configurável
     evita arquivo ainda sendo escrito; o rsync --partial retoma em link instável.
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
    [ValidateSet('menu', 'setup', 'test', 'send', 'schedule', 'unschedule', 'status', 'reset', 'help')]
    [string]$Command = 'menu',

    # Caminho da pasta de configuração. Usado pela tarefa agendada como SYSTEM, que tem
    # outro perfil de usuário e precisa apontar para a configuração de quem fez o setup.
    [string]$ConfigDir = ''
)

$ErrorActionPreference = 'Stop'
$ConfDir    = if ($ConfigDir) { $ConfigDir } else { Join-Path $env:LOCALAPPDATA 'carbonara-station' }
$ConfFile   = Join-Path $ConfDir 'config.json'
$LogFile    = Join-Path $ConfDir 'envios.log'
$StateFile  = Join-Path $ConfDir 'sent.state'   # o que já foi enviado: caminho<TAB>tamanho:mtime
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

# Monta a linha de comando do rsync (executável + argumentos), conforme o modo de
# autenticação: chave SSH (rsync-over-ssh) ou usuário+senha (rsync daemon, rsync://).
# $SourceDir permite trocar a origem (ex.: pasta vazia no teste de conexão).
function Get-RsyncPlan($Cfg, [string[]]$ExtraArgs, [string]$FilesFromWin, [string]$SourceDir = '') {
    if (-not $SourceDir) { $SourceDir = $Cfg.DataDir }
    $rsyncDir = Split-Path $Cfg.Rsync
    # Receita do próprio cwRsync: bin\ no PATH (DLLs cygwin) e HOME definido
    # (o ssh cygwin usa HOME para gravar o known_hosts).
    if ($env:PATH -notlike "$rsyncDir*") { $env:PATH = "$rsyncDir;$env:PATH" }
    if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
    $argv = @() + $ExtraArgs
    if ($FilesFromWin) { $argv += "--files-from=$(ConvertTo-UnixPath $FilesFromWin)" }
    if ($Cfg.AuthMode -eq 'senha') {
        # usuário = id da estação; a senha vai pela variável RSYNC_PASSWORD (os
        # chamadores definem/limpam) — nunca pela linha de comando.
        $dest = 'rsync://{0}@{1}:{2}/{0}/' -f $Cfg.StationId, $Cfg.HubHost, $Cfg.HubPort
        $argv += @("$(ConvertTo-UnixPath $SourceDir)/", $dest)
    } else {
        $dest = '{0}@{1}:/' -f $Cfg.HubUser, $Cfg.HubHost
        $keyArg = ConvertTo-UnixPath $Cfg.KeyPath
        $ssh  = "ssh -i $keyArg -p $($Cfg.HubPort) -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
        $argv += @('-e', $ssh, "$(ConvertTo-UnixPath $SourceDir)/", $dest)
    }
    return @{ Exe = $Cfg.Rsync; Args = $argv }
}

# Teste de conexão que NÃO depende da pasta de dados: dry-run a partir de uma pasta
# vazia — valida rede, porta, chave e o confinamento no servidor, sem enviar nada.
function Invoke-Probe($Cfg) {
    $tmp = Join-Path $env:TEMP ("carbonara-probe-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    if ($Cfg.AuthMode -eq 'senha') { $env:RSYNC_PASSWORD = $Cfg.Password }
    try {
        $plan = Get-RsyncPlan $Cfg @('-an', '--timeout=20') '' $tmp
        $r = Invoke-NativeQuiet $plan.Exe $plan.Args 90
        if ($r.TimedOut) { Write-Host 'falhou: o teste não respondeu a tempo (rede?).'; return $false }
        if ($r.Code -eq 0) {
            Write-Host 'ok: o servidor respondeu e aceitou a credencial.'
            return $true
        }
        Write-Host 'falhou:'
        $r.Out | ForEach-Object { Write-Host "  $_" }
        return $false
    } finally {
        Remove-Item Env:RSYNC_PASSWORD -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmp -Force -Recurse -ErrorAction SilentlyContinue
    }
}

# Inventário da pasta local (fora de pastas/arquivos ocultos — mesmo critério de
# dotfiles da ingestão no servidor): caminho relativo, assinatura (tamanho:mtime,
# detecta arquivo novo/alterado) e se já "assentou" (mais velho que MinAgeMin).
function Get-LocalFiles($Cfg) {
    $dataDir = (Get-Item -LiteralPath $Cfg.DataDir).FullName.TrimEnd('\')
    $cut = (Get-Date).AddMinutes(-[int]$Cfg.MinAgeMin)
    Get-ChildItem -LiteralPath $dataDir -Recurse -File | Where-Object {
        $_.Name -notlike '.*' -and
        $_.FullName.Substring($dataDir.Length) -notmatch '[\\/]\.'
    } | ForEach-Object {
        [pscustomobject]@{
            Rel     = $_.FullName.Substring($dataDir.Length + 1) -replace '\\', '/'
            Sig     = '{0}:{1}' -f $_.Length, $_.LastWriteTimeUtc.Ticks
            Settled = ($_.LastWriteTime -le $cut)
        }
    }
}

# Estado local do que já foi enviado (a pasta de dados NUNCA é alterada).
function Get-SentState {
    $h = @{}
    if (Test-Path $StateFile) {
        foreach ($ln in Get-Content $StateFile) {
            $i = $ln.LastIndexOf("`t")
            if ($i -gt 0) { $h[$ln.Substring(0, $i)] = $ln.Substring($i + 1) }
        }
    }
    return $h
}

function Save-SentState($State) {
    New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
    $lines = $State.GetEnumerator() | Sort-Object Key | ForEach-Object { "$($_.Key)`t$($_.Value)" }
    [IO.File]::WriteAllLines($StateFile, [string[]]@($lines), (New-Object System.Text.UTF8Encoding($false)))
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
    # 3. Autenticação: chave SSH ou usuário+senha
    Write-Host 'Como esta estação se autentica no servidor?'
    Write-Host '  1) chave SSH (recomendado — tráfego criptografado; porta 22)'
    Write-Host '  2) usuário e senha (mais simples — rsync direto na porta 873; dados sem criptografia)'
    $dModo = if ($old -and $old.AuthMode -eq 'senha') { '2' } else { '1' }
    $modo = Ask 'modo' $dModo
    $authMode = if ($modo -eq '2') { 'senha' } else { 'chave' }

    $newKey = $false
    $keyPath = $null
    $hubUser = 'carbonara'
    $password = $null
    if ($authMode -eq 'senha') {
        $hubPort = Ask 'Porta do rsync no servidor' $(if ($old -and $old.AuthMode -eq 'senha' -and $old.HubPort) { $old.HubPort } else { '873' })
        Write-Host ("O usuário é o próprio id da estação ({0}); a senha está no INSTRUCOES.txt" -f $id)
        Write-Host "entregue pelo administrador (gerada por 'rsync-hub add $id --senha')."
        do {
            $password = Ask 'Senha desta estação' $(if ($old -and $old.Password) { $old.Password })
            if (-not $password) { Write-Host '  informe a senha.' }
        } until ($password)
    } else {
    $hubPort = Ask 'Porta SSH do servidor' $(if ($old -and $old.AuthMode -ne 'senha' -and $old.HubPort) { $old.HubPort } else { '22' })
    # A conta de serviço é ÚNICA no servidor (recebe todas as estações) — quase sempre
    # 'carbonara'. Usuários tendem a digitar o id da estação aqui; confirme se mudar.
    $hubUser = Ask "Conta de serviço no servidor — NÃO é o id da estação; aperte Enter" $(if ($old -and $old.HubUser) { $old.HubUser } else { 'carbonara' })
    if ($hubUser -ne 'carbonara') {
        if (-not (Ask-YesNo ("  atenção: normalmente a conta é 'carbonara' — usar '{0}' mesmo?" -f $hubUser) $false)) {
            $hubUser = 'carbonara'
            Write-Host "  ok, usando 'carbonara'."
        }
    }

    # Chave de conexão (recebida do servidor; se não existir, oferece gerar aqui)
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

    }

    # 4. Teste simples de conexão (o host responde? a credencial é aceita?)
    Write-Host ''
    $probeCfg = [pscustomobject]@{
        Rsync = $rsyncPath; KeyPath = $keyPath; AuthMode = $authMode
        StationId = $id; Password = $password
        HubUser = $hubUser; HubHost = $hubHost; HubPort = $hubPort; DataDir = $env:TEMP
    }
    if ($newKey) {
        Write-Host '(chave nova, ainda não autorizada no servidor — o teste fica para depois.)'
    } elseif (Ask-YesNo 'Testar a conexão com o servidor agora?' $true) {
        if (-not (Invoke-Probe $probeCfg)) {
            if ($authMode -eq 'senha') {
                Write-Host '  dicas: senha correta? a estação foi criada no servidor com'
                Write-Host ("         'rsync-hub add {0} --senha'? porta {1} liberada no firewall?" -f $id, $hubPort)
            } else {
                Write-Host "  dicas: 'Permission denied' → a conta de serviço está certa? (padrão: carbonara,"
                Write-Host '         não é o id da estação) e a chave é a gerada no servidor para este id?'
                Write-Host '         Outras causas: estação não autorizada, IP/porta errados, sem rede.'
            }
            if (-not (Ask-YesNo '  Continuar a configuração mesmo assim?' $true)) {
                Write-Host 'configuração interrompida.'
                return
            }
        }
    }
    Write-Host ''

    # 5. Pasta local que será enviada (rsync)
    # Default: a pasta de saída do LoggerNet (dataloggers Campbell Scientific, o padrão
    # das estações do projeto) quando existir; senão, uma pasta genérica do usuário.
    do {
        $default = if ($old) { $old.DataDir }
                   elseif (Test-Path -LiteralPath 'C:\Campbellsci\LoggerNet') { 'C:\Campbellsci\LoggerNet' }
                   else { Join-Path $env:USERPROFILE 'carbonara-dados' }
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
    Write-Host '  (a pasta local NUNCA é alterada: nada é movido nem apagado — o envio só'
    Write-Host '   sincroniza com o servidor o que for novo ou mudar.)'

    # 6. Tempo de reciclagem (intervalo do envio automático)
    do {
        $every = Ask 'Tempo de reciclagem do envio — a cada quantos minutos? (1-1439)' $(if ($old -and $old.SendEveryMin) { $old.SendEveryMin } else { '10' })
    } until ($every -match '^\d+$' -and [int]$every -ge 1 -and [int]$every -le 1439)

    Save-Config ([pscustomobject]@{
        StationId = $id;      HubHost = $hubHost; HubPort = $hubPort; HubUser = $hubUser
        AuthMode  = $authMode; Password = $password
        DataDir   = $dataDir; MinAgeMin = $minAge; SendEveryMin = $every
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
        if ($cfg.AuthMode -eq 'senha') {
            Write-Host ("  - a senha confere com a do servidor? (lá: sudo rsync-hub add {0} --senha)" -f $cfg.StationId)
            Write-Host ("  - porta {0} liberada no firewall? o serviço carbonara-rsyncd está no ar?" -f $cfg.HubPort)
        } else {
            Write-Host ("  - 'Permission denied' → a conta de serviço está certa? (a sua: '{0}'; o padrão é 'carbonara' — não é o id da estação)" -f $cfg.HubUser)
            Write-Host ("  - a estação foi autorizada no servidor? (lá: sudo rsync-hub add {0} …)" -f $cfg.StationId)
            Write-Host ("  - a chave é a recebida do servidor? ({0})" -f $cfg.KeyPath)
        }
        Write-Host ("  - IP ({0}) e porta ({1}) corretos? há rede até o servidor?" -f $cfg.HubHost, $cfg.HubPort)
    }
}

# --- Envio -----------------------------------------------------------------------------
# Sincronização pura: NADA é movido ou apagado da pasta local. O que já foi enviado é
# controlado pelo estado local ($StateFile, assinatura tamanho:mtime por arquivo) — só
# vai o que é novo ou mudou; arquivos que crescem (ex.: .dat do LoggerNet) são
# reenviados por inteiro e o servidor não duplica medições (ingestão idempotente).
function Invoke-Send {
    $cfg = Get-Config
    # Global\: vale entre sessões (tarefa como SYSTEM × janela do usuário); se o
    # sistema negar o namespace global, cai no local — sobreposição rara e inofensiva
    # (o servidor é idempotente).
    try { $mtx = New-Object System.Threading.Mutex($false, 'Global\CarbonaraStationSend') }
    catch { $mtx = New-Object System.Threading.Mutex($false, 'CarbonaraStationSend') }
    if (-not $mtx.WaitOne(0)) { Write-Host 'outro envio já está em andamento — saindo.'; return }
    try {
        $state  = Get-SentState
        $files  = @(Get-LocalFiles $cfg)
        $tosend = @($files | Where-Object { $_.Settled -and $state[$_.Rel] -ne $_.Sig })
        if ($tosend.Count -eq 0) {
            Write-Host ("nada novo a sincronizar (arquivos com menos de {0} min não contam)." -f $cfg.MinAgeMin)
            return
        }
        New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
        $list = Join-Path $ConfDir 'files-from.txt'
        # UTF-8 SEM BOM — um BOM corromperia o primeiro caminho da lista no rsync.
        [IO.File]::WriteAllLines($list, [string[]]@($tosend | ForEach-Object { $_.Rel }), (New-Object System.Text.UTF8Encoding($false)))

        Write-Host ("sincronizando {0} arquivo(s) novo(s)/alterado(s) com {1}…" -f $tosend.Count, $cfg.HubHost)
        # -rltz (sem -p/-o/-g) + --chmod: os arquivos chegam 644/755 no servidor —
        # legíveis pelos usuários locais do host, independentemente das permissões aqui.
        # --partial-dir: transferência cancelada deixa o pedaço num diretório OCULTO no
        # servidor (a ingestão nunca o vê) e o próximo envio retoma dali.
        $plan = Get-RsyncPlan $cfg @('-rltz', '--chmod=D755,F644', '--partial-dir=.rsync-parcial', '--timeout=120') $list
        if ($cfg.AuthMode -eq 'senha') { $env:RSYNC_PASSWORD = $cfg.Password }
        try { $r = Invoke-Native $plan.Exe $plan.Args }
        finally { Remove-Item Env:RSYNC_PASSWORD -ErrorAction SilentlyContinue }
        $r.Out | ForEach-Object { Write-Host "  $_" }
        if ($r.Code -eq 0) {
            # Registra no estado — a pasta local permanece intocada.
            foreach ($f in $tosend) { $state[$f.Rel] = $f.Sig }
            # Poda do estado: só entradas de arquivos que ainda existem localmente.
            $existing = @{}
            foreach ($f in $files) { $existing[$f.Rel] = $true }
            foreach ($k in @($state.Keys)) { if (-not $existing[$k]) { $state.Remove($k) } }
            Save-SentState $state
            Write-EnvioLog ("OK {0} arquivo(s) sincronizados" -f $tosend.Count)
            Write-Host ("ok: {0} arquivo(s) sincronizados (nada foi movido ou apagado localmente)." -f $tosend.Count)
        } else {
            Write-EnvioLog ("FALHA rc={0} ({1} arquivo(s) pendentes)" -f $r.Code, $tosend.Count)
            switch ($r.Code) {
                23 { Write-Host 'aviso: parte dos arquivos não pôde ser lida (em uso pelo datalogger?).'
                     Write-Host '       nada foi registrado; tudo será tentado de novo no próximo ciclo.' }
                24 { Write-Host 'aviso: um arquivo sumiu durante a transferência — o próximo ciclo se ajusta sozinho.' }
                default { Write-Host ("falha no envio (rsync rc={0}) — o estado não mudou; será tentado de novo no próximo ciclo." -f $r.Code) }
            }
        }
    } finally {
        $mtx.ReleaseMutex() | Out-Null
        $mtx.Dispose()
    }
}

function Invoke-Reset {  # força reenvio completo no próximo send (o servidor não duplica)
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    Write-Host "ok: estado de envio limpo — o próximo 'send' reenvia tudo."
    Write-Host '(o servidor não duplica medições: a ingestão é idempotente.)'
}

# --- Agendamento (Agendador de Tarefas) --------------------------------------------------
function Install-Schedule([string]$Every) {  # instala/substitui a tarefa de envio periódico
    # -ConfigDir fixa a configuração desta instalação (necessário quando a tarefa roda
    # como SYSTEM, cujo perfil/%LOCALAPPDATA% é outro).
    $tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" send -ConfigDir `"$ConfDir`""
    # Invoke-NativeQuiet monta a linha de comando escapando as aspas internas do /TR
    # como \" — obrigatório quando o caminho do script tem espaços (ex.: C:\Users\Km67
    # Eddy System\...); com o operador & do PowerShell o schtasks quebraria no espaço.
    $argv = @('/Create', '/F', '/SC', 'MINUTE', '/MO', $Every, '/TN', $TaskName)
    $asSystem = Test-IsAdmin
    if ($asSystem) { $argv += @('/RU', 'SYSTEM') }   # roda mesmo sem ninguém logado
    $argv += @('/TR', $tr)
    $r = Invoke-NativeQuiet 'schtasks.exe' $argv 30
    if ($r.Code -eq 0) {
        Write-Host ("ok: envio automático ativo a cada {0} min (Agendador de Tarefas, tarefa '{1}')." -f $Every, $TaskName)
        if ($asSystem) {
            Write-Host 'a tarefa roda como SYSTEM: continua sincronizando com a janela fechada,'
            Write-Host 'sem ninguém logado e após reiniciar — e sem piscar janelas.'
        } else {
            Write-Host 'pode fechar esta janela: a tarefa continua rodando enquanto o usuário'
            Write-Host 'estiver LOGADO no Windows. Para sincronizar também sem login/após'
            Write-Host 'reiniciar, rode o schedule uma vez em um PowerShell como ADMINISTRADOR'
            Write-Host '(a tarefa então é recriada como SYSTEM).'
        }
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
    $r = Invoke-NativeQuiet 'schtasks.exe' @('/Delete', '/F', '/TN', $TaskName) 30
    if ($r.Code -eq 0) {
        Write-Host 'ok: agendamento removido.'
    } else {
        Write-Host 'não removi: ou não havia agendamento, ou a tarefa foi criada como SYSTEM —'
        Write-Host 'nesse caso rode o unschedule em um PowerShell como ADMINISTRADOR.'
    }
}

# --- Status ------------------------------------------------------------------------------
function Invoke-Status {
    $cfg = Get-Config
    Write-Host ("estação:   {0}" -f $cfg.StationId)
    if ($cfg.AuthMode -eq 'senha') {
        Write-Host ("servidor:  {0}:{1} (usuário+senha, rsync direto)" -f $cfg.HubHost, $cfg.HubPort)
    } else {
        Write-Host ("servidor:  {0}@{1}:{2} (chave SSH)" -f $cfg.HubUser, $cfg.HubHost, $cfg.HubPort)
        Write-Host ("chave:     {0}" -f $cfg.KeyPath)
    }
    Write-Host ("rsync:     {0}" -f $cfg.Rsync)
    Write-Host ("dados:     {0} (somente leitura: nada é movido/apagado)" -f $cfg.DataDir)
    $state = Get-SentState
    $files = @(Get-LocalFiles $cfg)
    $pend  = @($files | Where-Object { $state[$_.Rel] -ne $_.Sig }).Count
    Write-Host ("arquivos:  {0} no total · {1} novo(s)/alterado(s) a sincronizar" -f $files.Count, $pend)
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
        Write-Host ' 7) Limpar estado de envio (força reenvio de tudo)'
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
                '7' { if (Ask-YesNo 'limpar o estado e reenviar tudo no próximo envio?' $false) { Invoke-Reset } }
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
  .\carbonara-station.ps1 send          sincroniza agora (só o que é novo/alterado)
  .\carbonara-station.ps1 schedule      agenda o envio periódico
  .\carbonara-station.ps1 unschedule    remove o agendamento
  .\carbonara-station.ps1 status        configuração, pendências e últimos envios
  .\carbonara-station.ps1 reset         limpa o estado de envio (força reenvio de tudo)
  .\carbonara-station.ps1               menu interativo

a pasta local NUNCA é alterada (nada é movido nem apagado); o controle do que já
foi enviado fica em $StateFile
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
        'reset'      { Invoke-Reset }
        'menu'       { Show-Menu }
        'help'       { Show-Help }
    }
} catch {
    Write-Host ("erro: {0}" -f $_.Exception.Message)
    exit 1
}
