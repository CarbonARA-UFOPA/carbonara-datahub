# Clientes das estações (`station/`)

Assistentes **guiados** que rodam na máquina da estação (o *cliente*) e enviam os
arquivos de dados para o servidor central (o *host*, preparado com o
[`rsync-hub`](../rsync-hub)) por **rsync-over-SSH**. Um script por sistema:

| Script | Sistema | Requisitos |
|--------|---------|------------|
| `carbonara-station.sh` | Linux | `rsync` e `openssh-client` (padrão na maioria das distros) |
| `carbonara-station.ps1` + `carbonara-station.cmd` | Windows 10/11 | **cwRsync** — o `setup` **baixa e instala sozinho** em `C:\cwrsync` na primeira vez (requer PowerShell **como administrador** só nessa primeira vez) |

> **Windows:** copie os **dois** arquivos (`.ps1` e `.cmd`) para a mesma pasta e execute o
> **`carbonara-station.cmd`** (duplo clique abre o menu). Ele existe porque o Windows, por
> padrão, bloqueia scripts `.ps1` ("running scripts is disabled on this system") — o `.cmd`
> executa o `.ps1` contornando essa política, sem precisar de `Set-ExecutionPolicy`. Se
> preferir chamar o `.ps1` direto, libere uma vez com
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e, se o arquivo veio por
> download/pendrive, `Unblock-File .\carbonara-station.ps1`.

> Estes são os clientes simples e guiados do projeto. Agentes mais completos (estado por
> arquivo, retry com backoff, transporte plugável) vivem no repo
> [`carbonara-stations`](https://github.com/CarbonARA-UFOPA/carbonara-stations).

## Fluxo (igual nos dois sistemas)

1. **No servidor** — o administrador autoriza a estação **gerando a chave lá**:
   ```bash
   sudo rsync-hub add <id>     # sem arquivo de chave: gera o par no host
   ```
   Isso cria `<raiz>/stations/<id>/` com a **chave privada** (`<id>_key`), a pública
   (que permanece lá, já autorizada) e um **`INSTRUCOES.txt`** de ativação. Leve a
   chave privada para a máquina da estação, junto com o cliente desta pasta.

2. **Na estação** — rode o assistente de configuração:
   ```bash
   ./carbonara-station.sh setup        # Linux
   ```
   ```powershell
   .\carbonara-station.cmd setup       # Windows (Prompt ou PowerShell)
   ```
   Ele pergunta, nesta ordem: **id da estação** → **IP do host** (porta opcional,
   padrão 22) → **caminho local da chave** recebida → faz um **teste simples de
   conexão** → **pasta local a enviar** (rsync) → **tempo de reciclagem** (minutos),
   já oferecendo ativar o envio automático.

   > A estação também pode gerar a própria chave: se o caminho informado não existir,
   > o assistente oferece criar um novo par e mostra a **chave pública** para o
   > administrador autorizar no servidor (`sudo rsync-hub add <id> <chave.pub>`).

3. **Operação** — a qualquer momento, na estação:
   ```bash
   ./carbonara-station.sh test       # testa a conexão (dry-run, nada é enviado)
   ./carbonara-station.sh send       # envia agora os arquivos pendentes
   ./carbonara-station.sh schedule   # (re)agenda o envio periódico (cron / Agendador de Tarefas)
   ./carbonara-station.sh status     # configuração, pendências e últimos envios
   ```
   (No Windows, os mesmos subcomandos com `.\carbonara-station.cmd`.)

Sem argumentos, os dois scripts abrem um **menu interativo** com todas as opções.

## Como o envio funciona

- Só entram arquivos **assentados** (mais velhos que a *idade mínima* configurada) — para
  não enviar arquivo que o sensor ainda está escrevendo. Pastas e arquivos ocultos
  (`.algo`) são ignorados, o mesmo critério da ingestão no servidor.
- Após um envio **bem-sucedido**, cada arquivo é movido para `.enviados/` dentro da pasta
  de dados (ou apagado, conforme escolhido no `setup`) — nada é reenviado.
- Em caso de falha, nada é movido: o próximo ciclo tenta de novo, e o `rsync --partial`
  retoma transferências interrompidas (link instável).
- No servidor, a estação só consegue **escrever na própria pasta** (`rrsync -wo` +
  `restrict` na chave): não lê, não baixa e não alcança as outras estações.

## Configuração gravada

- Linux: `~/.config/carbonara-station/config` (+ `envios.log` ao lado).
- Windows: `%LOCALAPPDATA%\carbonara-station\config.json` (+ `envios.log` ao lado).

Rodar `setup` de novo reconfigura (os valores atuais viram os defaults do assistente).

## Windows: como o rsync é instalado

O transporte no Windows é o **cwRsync** (rsync nativo, base cygwin). Na **primeira vez**
que o `setup` roda numa máquina sem rsync, ele mesmo **baixa o zip oficial e extrai para
`C:\cwrsync`** — como criar `C:\cwrsync` exige elevação, o assistente pede para você
abrir o **PowerShell como administrador** (menu Iniciar → digite "PowerShell" → botão
direito → *Executar como administrador*) e rodar o `setup` de novo. Só essa primeira vez
precisa de administrador; o uso normal (test/send/schedule) não.

Máquina da estação **sem internet**? Baixe o *cwRsync free client* em
<https://itefix.net/cwrsync> em outra máquina e extraia o zip para `C:\cwrsync` (deve
existir `C:\cwrsync\bin\rsync.exe`) — o `setup` reconhece e segue normalmente.
