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
     simples, porém dados sem criptografia) → TESTE simples de conexão → pasta(s)
     local(is) a enviar (rsync) → tempo de reciclagem (minutos) + envio automático.
  2) Envio — `send` SINCRONIZA as pastas locais com o servidor: NUNCA move nem apaga
     nada das pastas locais. A cada ciclo TODAS as pastas configuradas são varridas de
     novo (arquivos novos entram automaticamente). O controle do que já foi enviado é
     um estado local (tamanho+mtime por arquivo, em
     %LOCALAPPDATA%\carbonara-station\sent.state): só vai o que é novo ou mudou desde o
     último envio. Idade mínima configurável evita arquivo ainda sendo escrito; o
     rsync --partial retoma em link instável.
  3) Agendamento — `schedule` cria a tarefa no Agendador de Tarefas do Windows.

Múltiplas pastas: cada pasta configurada é enviada para um sub-diretório próprio no
servidor (o "rótulo", derivado do nome da pasta), evitando colisão de nomes entre
pastas. Cada pasta pode ter FILTROS: tipos de arquivo a NÃO enviar (globs, ex.:
*.tmp,*.bak) e subpastas a NÃO enviar (ex.: temp,cache).

Uso (PowerShell):
  .\carbonara-station.ps1               # menu interativo (mostra as pastas no cabeçalho)
  .\carbonara-station.ps1 setup         # configuração guiada (1ª vez ou reconfigurar)
  .\carbonara-station.ps1 test          # testa a conexão com o servidor (dry-run)
  .\carbonara-station.ps1 send          # envia agora os arquivos pendentes
  .\carbonara-station.ps1 folders       # gerencia as pastas de sincronização
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
    [ValidateSet('menu', 'setup', 'test', 'send', 'folders', 'schedule', 'unschedule', 'status', 'reset', 'purge', 'help')]
    [string]$Command = 'menu',

    # Caminho da pasta de configuração. Usado pela tarefa agendada como SYSTEM, que tem
    # outro perfil de usuário e precisa apontar para a configuração de quem fez o setup.
    [string]$ConfigDir = '',

    # Intervalo (min) para o 'schedule' — usado quando o setup/agendamento se re-executa
    # elevado (UAC) para criar a tarefa SYSTEM sem perguntar o intervalo de novo.
    [int]$Every = 0
)

$ErrorActionPreference = 'Stop'
$ConfDir    = if ($ConfigDir) { $ConfigDir } else { Join-Path $env:LOCALAPPDATA 'carbonara-station' }
$ConfFile   = Join-Path $ConfDir 'config.json'
$LogFile    = Join-Path $ConfDir 'envios.log'
$StateFile  = Join-Path $ConfDir 'sent.state'   # o que já foi enviado: rótulo/caminho<TAB>tamanho:mtime
$TaskName   = 'CarbonaraStationSend'
$DefaultHubHost = '200.139.13.53'   # servidor central do projeto (padrão sugerido no setup)
$CwRsyncDir = 'C:\cwrsync'
$CwRsyncUrl = 'https://itefix.net/sites/default/files/2026-06/cwrsync_6.4.8_x64_free.zip'

# --- Utilitários -----------------------------------------------------------------
# Grava texto UTF-8 SEM BOM e com quebras de linha LF (\n) — NUNCA CRLF. Obrigatório
# para arquivos lidos por ferramentas Unix no servidor/rsync: o heartbeat (o monitor
# valida 'interval_min=<n>' e um '\r' o reprova, jogando a estação na janela global de
# 1h) e o --files-from do rsync (um '\r' no fim de cada caminho faz o rsync procurar
# "arquivo\r", inexistente). [IO.File]::WriteAllLines usa CRLF no Windows — por isso este.
function Write-TextLf([string]$Path, [string[]]$Lines) {
    $text = ''
    if ($Lines -and $Lines.Count -gt 0) { $text = ($Lines -join "`n") + "`n" }
    [IO.File]::WriteAllText($Path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

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

# Divide uma lista separada por vírgula em array (itens aparados, sem vazios).
function Split-Csv([string]$s) {
    if (-not $s) { return @() }
    return @($s -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
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
    # -Depth: o config tem Folders[] de objetos com arrays de filtros dentro (>2 níveis).
    $Cfg | ConvertTo-Json -Depth 8 | Set-Content -Path $ConfFile -Encoding UTF8
}

# --- Pastas configuradas ------------------------------------------------------------
# Rótulo saneado e ÚNICO (não vazio) para o sub-diretório da pasta no servidor.
function New-Label([string[]]$Existing, [string]$Path) {
    $base = Split-Path $Path -Leaf
    $lbl = ($base.ToLower() -replace '[^a-z0-9_-]', '_').Trim('_')
    if (-not $lbl) { $lbl = 'pasta' }
    $b = $lbl; $i = 2
    while ($Existing -contains $lbl) { $lbl = "$b-$i"; $i++ }
    return $lbl
}

# Lista normalizada das pastas configuradas. Migra config antiga (uma única DataDir).
function Get-Folders($Cfg) {
    $folders = @()
    if ($Cfg.PSObject.Properties.Name -contains 'Folders' -and $Cfg.Folders) {
        foreach ($f in @($Cfg.Folders)) {
            $folders += [pscustomobject]@{
                Path         = [string]$f.Path
                Label        = [string]$f.Label
                FileExcludes = @($f.FileExcludes | Where-Object { $_ })
                DirExcludes  = @($f.DirExcludes  | Where-Object { $_ })
            }
        }
    }
    if ($folders.Count -eq 0 -and ($Cfg.PSObject.Properties.Name -contains 'DataDir') -and $Cfg.DataDir) {
        $folders += [pscustomobject]@{
            Path         = [string]$Cfg.DataDir
            Label        = (New-Label @() ([string]$Cfg.DataDir))
            FileExcludes = @(); DirExcludes = @()
        }
    }
    return , $folders
}

# Grava a lista de pastas no config e persiste (remove o DataDir legado).
function Set-Folders($Cfg, $Folders) {
    if ($Cfg.PSObject.Properties.Name -contains 'Folders') { $Cfg.Folders = @($Folders) }
    else { $Cfg | Add-Member -NotePropertyName Folders -NotePropertyValue @($Folders) -Force }
    if ($Cfg.PSObject.Properties.Name -contains 'DataDir') { $Cfg.PSObject.Properties.Remove('DataDir') }
    Save-Config $Cfg
}

function Show-FoldersSummary($Cfg) {
    $folders = @(Get-Folders $Cfg)
    if ($folders.Count -eq 0) {
        Write-Host '  (nenhuma pasta configurada — rode setup ou adicione em Gerenciar pastas)'
        return
    }
    # Quando a config está disponível, mostramos o DESTINO completo (IP + pasta da estação
    # + subpasta/rótulo desta pasta); senão, só o rótulo da subpasta no servidor.
    $hh = if ($Cfg -and ($Cfg.PSObject.Properties.Name -contains 'HubHost')) { $Cfg.HubHost } else { $null }
    $st = if ($Cfg -and ($Cfg.PSObject.Properties.Name -contains 'StationId')) { $Cfg.StationId } else { $null }
    for ($i = 0; $i -lt $folders.Count; $i++) {
        $f = $folders[$i]
        $extra = ''
        if ($f.FileExcludes.Count) { $extra += "  · exceto arquivos: $($f.FileExcludes -join ',')" }
        if ($f.DirExcludes.Count)  { $extra += "  · sem subpastas: $($f.DirExcludes -join ',')" }
        if ($hh) {
            $sub = if ($st) { "$st/" } else { '' }
            $dst = "{0}:/{1}{2}/" -f $hh, $sub, $f.Label
        } else {
            $dst = "subpasta no servidor: [$($f.Label)]"
        }
        Write-Host ("  {0}) {1}  ->  {2}{3}" -f ($i + 1), $f.Path, $dst, $extra)
    }
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

# Caminho cygwin da CHAVE, garantido SEM espaços. O rsync divide o valor de -e por
# espaços ao montar o comando ssh, então um "-i" cujo caminho tenha espaço (ex.:
# C:\Users\Km67 Eddy System\...) quebra o ssh (ele lê metade do caminho e trata o resto
# como hostname). Solução: usar o nome CURTO 8.3 do Windows (nunca tem espaço) antes de
# converter para /cygdrive/. Se o 8.3 estiver desativado no disco, devolve o caminho
# longo (e o chamador avisa o usuário).
function Get-CygSshKey([string]$WinPath) {
    $p = $WinPath
    try {
        $fso = New-Object -ComObject Scripting.FileSystemObject
        if ($fso.FileExists($WinPath)) { $p = $fso.GetFile($WinPath).ShortPath }
    } catch { }
    return (ConvertTo-UnixPath $p)
}

# Monta a linha de comando do rsync (executável + argumentos), conforme o modo de
# autenticação: chave SSH (rsync-over-ssh) ou usuário+senha (rsync daemon, rsync://).
# $SourceDir troca a origem (ex.: pasta vazia no teste, ou cada pasta no envio);
# $DestSub acrescenta um sub-diretório no destino (o rótulo da pasta).
function Get-RsyncPlan($Cfg, [string[]]$ExtraArgs, [string]$FilesFromWin, [string]$SourceDir = '', [string]$DestSub = '') {
    if (-not $SourceDir) {
        if (($Cfg.PSObject.Properties.Name -contains 'DataDir') -and $Cfg.DataDir) { $SourceDir = $Cfg.DataDir }
        else { $SourceDir = $env:TEMP }
    }
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
        if ($DestSub) { $dest += "$DestSub/" }
        $argv += @("$(ConvertTo-UnixPath $SourceDir)/", $dest)
    } else {
        $dest = '{0}@{1}:/' -f $Cfg.HubUser, $Cfg.HubHost
        if ($DestSub) { $dest += "$DestSub/" }
        $keyArg = Get-CygSshKey $Cfg.KeyPath
        $ssh  = "ssh -i $keyArg -p $($Cfg.HubPort) -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
        $argv += @('-e', $ssh, "$(ConvertTo-UnixPath $SourceDir)/", $dest)
    }
    return @{ Exe = $Cfg.Rsync; Args = $argv }
}

# Sinal de vida: a cada ciclo de envio (mesmo sem dados novos) sobe um arquivinho
# OCULTO (.carbonara-status) para a pasta da estação no servidor, declarando o tempo
# de reciclagem — é com ele que o monitor marca Online/Offline por estação. Sem -t:
# o mtime no servidor é o horário de CHEGADA (imune a fuso/relógio da estação).
function Send-Heartbeat($Cfg) {
    New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
    $hb = Join-Path $ConfDir '.carbonara-status'
    $lines = @(
        "station=$($Cfg.StationId)",
        "interval_min=$($Cfg.SendEveryMin)",
        ('ts={0:yyyy-MM-ddTHH:mm:ssZ}' -f (Get-Date).ToUniversalTime())
    )
    Write-TextLf $hb ([string[]]$lines)   # LF: o monitor do servidor valida interval_min
    $rsyncDir = Split-Path $Cfg.Rsync
    if ($env:PATH -notlike "$rsyncDir*") { $env:PATH = "$rsyncDir;$env:PATH" }
    if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
    $src = ConvertTo-UnixPath $hb
    if ($Cfg.AuthMode -eq 'senha') {
        $dest = 'rsync://{0}@{1}:{2}/{0}/.carbonara-status' -f $Cfg.StationId, $Cfg.HubHost, $Cfg.HubPort
        $env:RSYNC_PASSWORD = $Cfg.Password
        try { $r = Invoke-NativeQuiet $Cfg.Rsync @('-z', '--chmod=F644', '--timeout=30', $src, $dest) 60 }
        finally { Remove-Item Env:RSYNC_PASSWORD -ErrorAction SilentlyContinue }
    } else {
        $keyArg = Get-CygSshKey $Cfg.KeyPath
        $ssh = "ssh -i $keyArg -p $($Cfg.HubPort) -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
        $dest = '{0}@{1}:/.carbonara-status' -f $Cfg.HubUser, $Cfg.HubHost
        $r = Invoke-NativeQuiet $Cfg.Rsync @('-z', '--chmod=F644', '--timeout=30', '-e', $ssh, $src, $dest) 60
    }
    return ($r.Code -eq 0)
}

# Teste de conexão que NÃO depende das pastas de dados: dry-run a partir de uma pasta
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

# Inventário das pastas locais. Varre TODAS as pastas configuradas a cada chamada
# (é isto que faz arquivos novos serem detectados em todo ciclo), aplicando os filtros
# (tipos de arquivo e subpastas excluídos) e o critério de dotfiles da ingestão. Cada
# item traz a chave "rótulo/caminho", a assinatura (tamanho:mtime) e se já assentou.
function Get-LocalFiles($Cfg) {
    $cut = (Get-Date).AddMinutes(-[int]$Cfg.MinAgeMin)
    foreach ($f in @(Get-Folders $Cfg)) {
        if (-not (Test-Path -LiteralPath $f.Path)) { continue }
        $dataDir = (Get-Item -LiteralPath $f.Path).FullName.TrimEnd('\')
        # Rótulo e caminho FIXADOS como string (escalar) por pasta — evita que uma
        # eventual coleção vinda do JSON contamine cada item com um array.
        $lbl  = [string]$f.Label
        $fp   = [string]$f.Path
        $fexc = @($f.FileExcludes)
        $dexc = @($f.DirExcludes)
        Get-ChildItem -LiteralPath $dataDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -notlike '.*' -and
            $_.FullName.Substring($dataDir.Length) -notmatch '[\\/]\.'
        } | ForEach-Object {
            $rel = $_.FullName.Substring($dataDir.Length + 1) -replace '\\', '/'
            $nm = $_.Name
            $skip = $false
            foreach ($d in $dexc) { if (("/$rel/") -like "*/$d/*") { $skip = $true; break } }
            if (-not $skip) { foreach ($g in $fexc) { if ($nm -like $g) { $skip = $true; break } } }
            if (-not $skip) {
                # Emite o objeto direto (stream) — NÃO acumula em $out nem usa ',$out';
                # assim @(Get-LocalFiles) é sempre uma lista PLANA, sem aninhamento.
                [pscustomobject]@{
                    Key     = "$lbl/$rel"
                    Rel     = $rel
                    Label   = $lbl
                    Path    = $fp
                    Sig     = '{0}:{1}' -f $_.Length, $_.LastWriteTimeUtc.Ticks
                    Settled = ($_.LastWriteTime -le $cut)
                }
            }
        }
    }
}

# Estado local do que já foi enviado (as pastas de dados NUNCA são alteradas).
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
        $hubHost = Ask 'IP (ou hostname) do servidor central' $(if ($old -and $old.HubHost) { $old.HubHost } else { $DefaultHubHost })
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
            # Restringe a chave, mas concede LEITURA ao SYSTEM — senão a tarefa agendada
            # como SYSTEM não conseguiria ler a chave e o envio por SSH falharia.
            Invoke-Native 'icacls.exe' @($keyPath, '/inheritance:r', '/grant:r', "$($env:USERNAME):F", '/grant:r', 'SYSTEM:R') | Out-Null
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
                # Restringe a chave, mas concede LEITURA ao SYSTEM — senão a tarefa agendada
            # como SYSTEM não conseguiria ler a chave e o envio por SSH falharia.
            Invoke-Native 'icacls.exe' @($keyPath, '/inheritance:r', '/grant:r', "$($env:USERNAME):F", '/grant:r', 'SYSTEM:R') | Out-Null
                $script:pubKey = (Get-Content -LiteralPath "$keyPath.pub" -Raw).Trim()
                $newKey = $true
            }
        }
    }

    }

    # 4. Teste simples de conexão (o host responde? a credencial é aceita?)
    Write-Host ''
    if ($authMode -ne 'senha' -and $keyPath -and ((Get-CygSshKey $keyPath) -match ' ')) {
        Write-Host 'AVISO: o caminho da chave tem ESPAÇOS e o nome curto (8.3) do Windows parece'
        Write-Host 'desativado neste disco — o rsync/ssh pode falhar ("Could not resolve hostname").'
        Write-Host 'Se o teste falhar, mova a chave para uma pasta SEM espaços (ex.: C:\carbonara\)'
        Write-Host 'e rode o setup de novo apontando para o novo caminho.'
        Write-Host ''
    }
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

    # 5. Pasta(s) local(is) que serão enviadas (rsync)
    # Default: a pasta de saída do LoggerNet (dataloggers Campbell Scientific, o padrão
    # das estações do projeto) quando existir; senão, uma pasta genérica do usuário.
    $folders = @()
    if ($old) { $folders = @(Get-Folders $old) }
    $keep = $false
    if ($folders.Count -gt 0) {
        Write-Host 'Pastas já configuradas:'
        Show-FoldersSummary $old
        $keep = Ask-YesNo "Manter essas pastas? (responda 'n' para reconfigurar a pasta principal)" $true
    }
    if (-not $keep) {
        do {
            $default = if ($folders.Count) { $folders[0].Path }
                       elseif (Test-Path -LiteralPath 'C:\Campbellsci\LoggerNet') { 'C:\Campbellsci\LoggerNet' }
                       else { Join-Path $env:USERPROFILE 'carbonara-dados' }
            $dataDir = Ask 'Pasta LOCAL principal a enviar por rsync' $default
            if (-not (Test-Path -LiteralPath $dataDir)) {
                if (Ask-YesNo "  a pasta não existe — criar $dataDir?") {
                    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
                }
            }
        } until (Test-Path -LiteralPath $dataDir)
        $full = (Get-Item -LiteralPath $dataDir).FullName
        Write-Host 'Filtros desta pasta (opcionais — Enter deixa em branco):'
        $fe = Ask '  Tipos de arquivo a NÃO enviar (globs por vírgula, ex.: *.tmp,*.bak)'
        $de = Ask '  Subpastas a NÃO enviar (por vírgula, ex.: temp,cache)'
        $folders = @([pscustomobject]@{
            Path         = $full; Label = (New-Label @() $full)
            FileExcludes = @(Split-Csv $fe); DirExcludes = @(Split-Csv $de)
        })
        Write-Host '  (dá para adicionar mais pastas depois: menu → Gerenciar pastas.)'
    }
    do {
        $minAge = Ask 'Idade mínima (minutos) para um arquivo ser considerado pronto' $(if ($old -and $old.MinAgeMin) { $old.MinAgeMin } else { '1' })
    } until ($minAge -match '^\d+$')
    Write-Host '  (as pastas locais NUNCA são alteradas: nada é movido nem apagado — o envio só'
    Write-Host '   sincroniza com o servidor o que for novo ou mudar.)'

    # 6. Tempo de reciclagem (intervalo do envio automático)
    do {
        $every = Ask 'Tempo de reciclagem do envio — a cada quantos minutos? (1-1439)' $(if ($old -and $old.SendEveryMin) { $old.SendEveryMin } else { '10' })
    } until ($every -match '^\d+$' -and [int]$every -ge 1 -and [int]$every -le 1439)

    Save-Config ([pscustomobject]@{
        StationId = $id;      HubHost = $hubHost; HubPort = $hubPort; HubUser = $hubUser
        AuthMode  = $authMode; Password = $password
        Folders   = @($folders); MinAgeMin = $minAge; SendEveryMin = $every
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
            Write-Host "  - saiu 'rsync-hub: Permission denied'? o problema é NO SERVIDOR (o script"
            Write-Host '    saiu do lugar/não é executável pela conta de serviço) — lá, rode de novo:'
            Write-Host ("    sudo rsync-hub add {0}   (regrava a chave apontando p/ /usr/local/bin)" -f $cfg.StationId)
            Write-Host ("  - a estação foi autorizada no servidor? (lá: sudo rsync-hub add {0} …)" -f $cfg.StationId)
            Write-Host ("  - a chave é a recebida do servidor? ({0})" -f $cfg.KeyPath)
        }
        Write-Host ("  - IP ({0}) e porta ({1}) corretos? há rede até o servidor?" -f $cfg.HubHost, $cfg.HubPort)
    }
}

# --- Envio -----------------------------------------------------------------------------
# Sincronização pura: NADA é movido ou apagado das pastas locais. A cada ciclo TODAS as
# pastas são varridas de novo (arquivos novos entram automaticamente). O que já foi
# enviado é controlado pelo estado local ($StateFile, chave rótulo/caminho → tamanho:mtime)
# — só vai o que é novo ou mudou; arquivos que crescem (ex.: .dat do LoggerNet) são
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
        # Sinal de vida ANTES de tudo: mesmo sem dados novos o servidor sabe que a
        # estação está viva (e qual é o seu ciclo). Falha aqui não interrompe o envio.
        if (-not (Send-Heartbeat $cfg)) {
            Write-Host 'aviso: não consegui enviar o sinal de vida ao servidor (sem rede?).'
        }
        $state  = Get-SentState
        $all    = @(Get-LocalFiles $cfg)
        $tosend = @($all | Where-Object { $_.Settled -and $state[$_.Key] -ne $_.Sig })
        if ($tosend.Count -eq 0) {
            Write-Host ("nada novo a sincronizar (arquivos com menos de {0} min não contam)." -f $cfg.MinAgeMin)
            return
        }
        Write-Host ("sincronizando {0} arquivo(s) novo(s)/alterado(s) com {1}…" -f $tosend.Count, $cfg.HubHost)
        New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
        $list = Join-Path $ConfDir 'files-from.txt'
        $worst = 0
        $sent = @{}
        # Um envio (e um files-from) por pasta — cada pasta vai para o seu sub-diretório
        # (rótulo) no servidor, o que evita colisão de nomes entre pastas diferentes.
        foreach ($fold in @(Get-Folders $cfg)) {
            $label = [string]$fold.Label
            $path  = [string]$fold.Path
            # Arquivos deste rótulo dentro do que há para enviar (lista plana).
            $items = @($tosend | Where-Object { $_.Label -eq $label })
            if ($items.Count -eq 0) { continue }
            if (-not (Test-Path -LiteralPath $path)) {
                Write-Host ("  [{0}] pasta não encontrada: {1} — pulando." -f $label, $path)
                continue
            }
            # UTF-8 SEM BOM e LF — um BOM ou um '\r' (CRLF) corromperia os caminhos no rsync.
            Write-TextLf $list ([string[]]@($items | ForEach-Object { [string]$_.Rel }))
            Write-Host ("  [{0}] {1} arquivo(s) de {2}" -f $label, $items.Count, $path)
            # -rltz (sem -p/-o/-g) + --chmod: os arquivos chegam 644/755 no servidor —
            # legíveis pelos usuários locais do host, independentemente das permissões aqui.
            # --partial-dir: transferência cancelada deixa o pedaço num diretório OCULTO no
            # servidor (a ingestão nunca o vê) e o próximo envio retoma dali.
            $plan = Get-RsyncPlan $cfg @('-rltz', '--chmod=D755,F644', '--partial-dir=.rsync-parcial', '--timeout=120') $list $path $label
            if ($cfg.AuthMode -eq 'senha') { $env:RSYNC_PASSWORD = $cfg.Password }
            try { $r = Invoke-Native $plan.Exe $plan.Args }
            finally { Remove-Item Env:RSYNC_PASSWORD -ErrorAction SilentlyContinue }
            $r.Out | ForEach-Object { Write-Host "  $_" }
            if ($r.Code -eq 0) {
                foreach ($it in $items) { $sent[$it.Key] = $it.Sig }
            } else {
                if ($r.Code -gt $worst) { $worst = $r.Code }
                Write-Host ("  [{0}] falha (rsync rc={1}) — será tentado de novo no próximo ciclo." -f $label, $r.Code)
            }
        }
        Remove-Item -LiteralPath $list -Force -ErrorAction SilentlyContinue
        # Atualiza o estado com o que foi enviado e poda para os arquivos que ainda
        # existem localmente. As pastas locais permanecem intocadas.
        foreach ($k in $sent.Keys) { $state[$k] = $sent[$k] }
        $existing = @{}
        foreach ($f in $all) { $existing[$f.Key] = $true }
        foreach ($k in @($state.Keys)) { if (-not $existing[$k]) { $state.Remove($k) } }
        Save-SentState $state
        if ($sent.Count -gt 0) {
            Write-EnvioLog ("OK {0} arquivo(s) sincronizados" -f $sent.Count)
            Write-Host ("ok: {0} arquivo(s) sincronizados (nada foi movido ou apagado localmente)." -f $sent.Count)
        }
        if ($worst -ne 0) {
            Write-EnvioLog ("FALHA rc={0} (parte dos arquivos pendente)" -f $worst)
            switch ($worst) {
                23 { Write-Host 'aviso: parte dos arquivos não pôde ser lida (em uso pelo datalogger?).'
                     Write-Host '       o que falhou não foi registrado; será tentado de novo no próximo ciclo.' }
                24 { Write-Host 'aviso: um arquivo sumiu durante a transferência — o próximo ciclo se ajusta sozinho.' }
                default { Write-Host ("falha em parte do envio (rsync rc={0}) — nova tentativa no próximo ciclo." -f $worst) }
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

# Remove a configuração/conexão desta estação E o agendamento — para começar do zero.
# NÃO toca nas pastas de DADOS nem no arquivo de CHAVE (que ficam fora do ConfDir).
function Invoke-Purge {
    Write-Host 'Isto apaga APENAS a CONFIGURAÇÃO desta estação: o endereço do servidor, a'
    Write-Host 'LISTA de quais pastas enviar (não as pastas!), os filtros, o agendamento e o'
    Write-Host 'histórico local (estado + log).'
    Write-Host ''
    Write-Host '>>> As suas PASTAS DE DADOS NÃO são apagadas — em NENHUMA hipótese. <<<'
    Write-Host '    O cliente só LÊ seus arquivos; remover a configuração apenas faz a estação'
    Write-Host "    'esquecer' para onde e o que enviar. Seus dados e a chave SSH ficam intactos."
    Write-Host ''
    if (-not (Ask-YesNo 'Remover a configuração?' $false)) { Write-Host 'cancelado.'; return }
    $r = Invoke-NativeQuiet 'schtasks.exe' @('/Delete', '/F', '/TN', $TaskName) 30
    if ($r.Code -eq 0) {
        Write-Host '  agendamento (Agendador de Tarefas) removido.'
    } else {
        Write-Host '  aviso: não removi o agendamento (talvez criado como SYSTEM) — rode o unschedule'
        Write-Host "  em um PowerShell como ADMINISTRADOR, ou apague a tarefa '$TaskName' manualmente."
    }
    foreach ($f in @($ConfFile, $StateFile, $LogFile,
                     (Join-Path $ConfDir '.carbonara-status'),
                     (Join-Path $ConfDir 'files-from.txt'))) {
        Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
    }
    Write-Host "ok: configuração removida — rode 'setup' para configurar de novo."
}

# --- Gerência de pastas ----------------------------------------------------------------
function Add-Folder {
    $cfg = Get-Config
    $folders = @(Get-Folders $cfg)
    do {
        $p = Ask 'Caminho da pasta a incluir (Enter cancela)'
        if (-not $p) { Write-Host 'cancelado.'; return }
        if (-not (Test-Path -LiteralPath $p)) {
            if (Ask-YesNo "  a pasta não existe — criar $p?" $false) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
            else { continue }
        }
        $full = (Get-Item -LiteralPath $p).FullName
        if (@($folders | Where-Object { $_.Path -eq $full }).Count) { Write-Host '  essa pasta já está na lista.'; return }
        break
    } while ($true)
    Write-Host 'Filtros desta pasta (opcionais — Enter deixa em branco):'
    $fe = Ask '  Tipos de arquivo a NÃO enviar (globs por vírgula, ex.: *.tmp,*.bak)'
    $de = Ask '  Subpastas a NÃO enviar (por vírgula, ex.: temp,cache)'
    $lbl = New-Label @($folders | ForEach-Object { $_.Label }) $full
    $folders += [pscustomobject]@{
        Path = $full; Label = $lbl
        FileExcludes = @(Split-Csv $fe); DirExcludes = @(Split-Csv $de)
    }
    Set-Folders $cfg $folders
    Write-Host ("ok: pasta adicionada (sub-diretório no servidor: [{0}])." -f $lbl)
}

function Remove-Folder {
    $cfg = Get-Config
    $folders = @(Get-Folders $cfg)
    if ($folders.Count -eq 0) { Write-Host 'não há pastas configuradas.'; return }
    Show-FoldersSummary $cfg
    $sel = Ask 'Número da pasta a remover (Enter cancela)'
    if (-not $sel) { return }
    if ($sel -notmatch '^\d+$') { Write-Host '  número inválido.'; return }
    $idx = [int]$sel - 1
    if ($idx -lt 0 -or $idx -ge $folders.Count) { Write-Host '  fora da faixa.'; return }
    if ($folders.Count -le 1 -and -not (Ask-YesNo 'essa é a única pasta — tirar da lista? (a estação ficará sem enviar dados)' $false)) { return }
    $new = @()
    for ($i = 0; $i -lt $folders.Count; $i++) { if ($i -ne $idx) { $new += $folders[$i] } }
    Set-Folders $cfg $new
    Write-Host 'ok: pasta retirada da LISTA de sincronização — os arquivos no disco continuam'
    Write-Host "lá, nada foi apagado. (o estado de envio dela é limpo no próximo 'send'.)"
}

function Edit-FolderFilters {
    $cfg = Get-Config
    $folders = @(Get-Folders $cfg)
    if ($folders.Count -eq 0) { Write-Host 'não há pastas configuradas.'; return }
    Show-FoldersSummary $cfg
    $sel = Ask 'Número da pasta cujos filtros deseja editar (Enter cancela)'
    if (-not $sel) { return }
    if ($sel -notmatch '^\d+$') { Write-Host '  número inválido.'; return }
    $idx = [int]$sel - 1
    if ($idx -lt 0 -or $idx -ge $folders.Count) { Write-Host '  fora da faixa.'; return }
    $f = $folders[$idx]
    Write-Host ("Editando filtros de: {0}" -f $f.Path)
    Write-Host '(Enter mantém o valor atual; digite um espaço e Enter para LIMPAR o filtro)'
    $fe = Ask '  Tipos de arquivo a NÃO enviar (globs por vírgula)' ($f.FileExcludes -join ',')
    $de = Ask '  Subpastas a NÃO enviar (por vírgula)' ($f.DirExcludes -join ',')
    $f.FileExcludes = @(Split-Csv $fe)
    $f.DirExcludes  = @(Split-Csv $de)
    $folders[$idx] = $f
    Set-Folders $cfg $folders
    Write-Host 'ok: filtros atualizados.'
}

function Show-FoldersMenu {
    while ($true) {
        Write-Host ''
        Write-Host '----- Pastas de sincronização -----'
        $cfg = Get-Config -Optional
        if ($cfg) { Show-FoldersSummary $cfg } else { Write-Host '  (estação ainda não configurada — use a opção 1 do menu)' }
        Write-Host ' 1) Adicionar pasta'
        Write-Host ' 2) Remover pasta'
        Write-Host ' 3) Editar filtros (tipos de arquivo / subpastas) de uma pasta'
        Write-Host ' 0) Voltar'
        $op = Read-Host 'opção'
        try {
            switch ($op) {
                '1' { Add-Folder }
                '2' { Remove-Folder }
                '3' { Edit-FolderFilters }
                '0' { return }
                ''  { return }
                default { Write-Host 'opção inválida' }
            }
        } catch {
            Write-Host ("erro: {0}" -f $_.Exception.Message)
        }
    }
}

# --- Agendamento (Agendador de Tarefas) --------------------------------------------------
# A rotina de envio é uma TAREFA do Windows — vive independente desta janela. O ideal é
# rodar como SYSTEM: continua com a janela FECHADA, sem ninguém logado e APÓS REINICIAR.
# Isso exige administrador; se não for o caso, o Install-Schedule oferece elevar (UAC).
function Install-Schedule([string]$Every) {  # instala/substitui a tarefa de envio periódico
    $minutes = [int]$Every
    if ($minutes -lt 1) { $minutes = 10 }

    # Sem admin: oferece re-executar ELEVADO para criar a tarefa como SYSTEM (o modo que
    # sobrevive a fechar a janela, deslogar e reiniciar). Passa -Every para não reperguntar.
    if (-not (Test-IsAdmin)) {
        Write-Host ''
        Write-Host 'Para o envio continuar com a janela FECHADA, sem login e APÓS REINICIAR, a'
        Write-Host 'tarefa precisa rodar como SYSTEM — isso pede administrador (aparece o aviso UAC).'
        if (Ask-YesNo 'Elevar agora e instalar como SYSTEM (recomendado)?' $true) {
            $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" schedule -ConfigDir `"$ConfDir`" -Every $minutes"
            try {
                Start-Process -FilePath 'powershell.exe' -ArgumentList $a -Verb RunAs -Wait
            } catch {
                Write-Host "não consegui elevar ($($_.Exception.Message)) — vou criar uma tarefa"
                Write-Host 'que roda só enquanto você estiver LOGADO.'
            }
            # Confirma pelo estado real da tarefa (a janela elevada já fechou).
            $q = Invoke-NativeQuiet 'schtasks.exe' @('/Query', '/TN', $TaskName) 15
            if ($q.Code -eq 0) {
                Write-Host ("ok: envio automático ativo a cada {0} min como SYSTEM (tarefa '{1}')." -f $minutes, $TaskName)
                Write-Host 'roda com a janela fechada, sem ninguém logado e após reiniciar.'
                return
            }
            Write-Host '(a tarefa como SYSTEM não foi criada — seguindo sem elevação.)'
        } else {
            Write-Host 'ok — a tarefa vai rodar só enquanto você estiver LOGADO no Windows.'
        }
    }

    $asSystem = Test-IsAdmin
    # Ação: 'send' OCULTO (sem piscar janela), apontando para ESTA configuração.
    # -ConfigDir fixa a config (a tarefa SYSTEM tem outro %LOCALAPPDATA%).
    $tr = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" send -ConfigDir `"$ConfDir`""
    # Invoke-NativeQuiet escapa as aspas internas do /TR como \" — obrigatório quando o
    # caminho do script tem espaços (ex.: C:\Users\Km67 Eddy System\...).
    $argv = @('/Create', '/F', '/SC', 'MINUTE', '/MO', "$minutes", '/TN', $TaskName)
    if ($asSystem) { $argv += @('/RU', 'SYSTEM', '/RL', 'HIGHEST') }   # roda mesmo sem ninguém logado
    $argv += @('/TR', $tr)
    $r = Invoke-NativeQuiet 'schtasks.exe' $argv 30
    if ($r.Code -ne 0) {
        $r.Out | ForEach-Object { Write-Host "  $_" }
        Write-Host 'falha ao criar a tarefa agendada — tente em um PowerShell como administrador.'
        return
    }
    # Reforço (best-effort, Windows 8+/10/11): não parar na bateria e RECUPERAR execuções
    # perdidas (ex.: logo após reiniciar). Assim a rotina fica realmente "sempre salva".
    try {
        if (Get-Command Set-ScheduledTask -ErrorAction SilentlyContinue) {
            $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -StartWhenAvailable -MultipleInstances IgnoreNew
            Set-ScheduledTask -TaskName $TaskName -Settings $set -ErrorAction Stop | Out-Null
        }
    } catch { }

    Write-Host ("ok: envio automático ativo a cada {0} min (Agendador de Tarefas, tarefa '{1}')." -f $minutes, $TaskName)
    if ($asSystem) {
        Write-Host 'roda como SYSTEM: continua com a janela FECHADA, sem ninguém logado e APÓS'
        Write-Host 'REINICIAR — sem piscar janelas. É o modo recomendado para estações de campo.'
    } else {
        Write-Host 'ATENÇÃO: esta tarefa roda só enquanto você estiver LOGADO no Windows (após'
        Write-Host 'reiniciar, só volta quando alguém logar). Para rodar sempre — janela fechada,'
        Write-Host 'sem login e após reboot — refaça o agendamento e aceite ELEVAR (SYSTEM).'
    }
    Write-Host 'para remover: .\carbonara-station.ps1 unschedule'
}

function Invoke-Schedule {
    Get-Config | Out-Null   # exige estar configurado
    # Se veio -Every (re-execução elevada), usa direto e não repergunta.
    if ($Every -ge 1) { Install-Schedule ([string]$Every); return }
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
    Write-Host 'pastas sincronizadas (somente leitura: nada é movido/apagado):'
    Show-FoldersSummary $cfg
    $state = Get-SentState
    $files = @(Get-LocalFiles $cfg)
    $pend  = @($files | Where-Object { $_.Settled -and $state[$_.Key] -ne $_.Sig }).Count
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
        $cfg = Get-Config -Optional
        if ($cfg) {
            Write-Host ("estação: {0}   servidor: {1}:{2}" -f $cfg.StationId, $cfg.HubHost, $cfg.HubPort)
        } else {
            Write-Host 'estação: (ainda não configurada — use a opção 1)'
        }
        Write-Host 'pastas sincronizadas:'
        if ($cfg) { Show-FoldersSummary $cfg } else { Write-Host '  (nenhuma)' }
        Write-Host '-------------------------------------------------------'
        Write-Host ' 1) Configurar esta estação (guiado)'
        Write-Host ' 2) Testar conexão com o servidor'
        Write-Host ' 3) Enviar agora'
        Write-Host ' 4) Gerenciar pastas de sincronização (incluir/remover/filtrar)'
        Write-Host ' 5) Agendar envio periódico'
        Write-Host ' 6) Remover agendamento'
        Write-Host ' 7) Status'
        Write-Host ' 8) Limpar estado de envio (força reenvio de tudo)'
        Write-Host ' 9) Remover configuração/conexão desta estação (e o agendamento)'
        Write-Host ' 0) Sair'
        $op = Read-Host 'opção'
        try {
            switch ($op) {
                '1' { Invoke-Setup }
                '2' { Invoke-Test }
                '3' { Invoke-Send }
                '4' { Show-FoldersMenu }
                '5' { Invoke-Schedule }
                '6' { Invoke-Unschedule }
                '7' { Invoke-Status }
                '8' { if (Ask-YesNo 'limpar o estado e reenviar tudo no próximo envio?' $false) { Invoke-Reset } }
                '9' { Invoke-Purge }
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
  .\carbonara-station.ps1 folders       gerencia as pastas (incluir/remover/filtrar)
  .\carbonara-station.ps1 schedule      agenda o envio periódico
  .\carbonara-station.ps1 unschedule    remove o agendamento
  .\carbonara-station.ps1 status        configuração, pendências e últimos envios
  .\carbonara-station.ps1 reset         limpa o estado de envio (força reenvio de tudo)
  .\carbonara-station.ps1 purge         remove a configuração/conexão e o agendamento (zera)
  .\carbonara-station.ps1               menu interativo

várias pastas são suportadas — cada uma vai para um sub-diretório próprio no servidor
e pode ter filtros (tipos de arquivo e subpastas a não enviar).
as pastas locais NUNCA são alteradas (nada é movido nem apagado); o controle do que já
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
        'folders'    { Show-FoldersMenu }
        'schedule'   { Invoke-Schedule }
        'unschedule' { Invoke-Unschedule }
        'status'     { Invoke-Status }
        'reset'      { Invoke-Reset }
        'purge'      { Invoke-Purge }
        'menu'       { Show-Menu }
        'help'       { Show-Help }
    }
} catch {
    Write-Host ("erro: {0}" -f $_.Exception.Message)
    exit 1
}
