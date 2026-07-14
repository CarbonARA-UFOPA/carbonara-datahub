# Clientes das estações (`station/`)

Assistentes **guiados** que rodam na máquina da estação (o *cliente*) e enviam os
arquivos de dados para o servidor central (o *host*, preparado com o
[`rsync-hub`](../rsync-hub)) por **rsync-over-SSH**. Um script por sistema:

| Script | Sistema | Requisitos |
|--------|---------|------------|
| `carbonara-station.sh` | Linux | `rsync` e `openssh-client` (padrão na maioria das distros) |
| `carbonara-station.ps1` + `carbonara-station.cmd` | Windows 10/11 | `rsync` via **WSL** ou **cwRsync** (o `setup` orienta a instalação) e OpenSSH (nativo) |

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

## Windows sem rsync?

O `setup` detecta e orienta. Resumo das opções:

- **WSL (recomendada)** — em um PowerShell como administrador: `wsl --install`; depois,
  no Ubuntu: `sudo apt install -y rsync openssh-client`. A chave SSH fica dentro do WSL
  e os dados do Windows são alcançados via `/mnt/c/...` automaticamente.
- **cwRsync (sem WSL)** — baixe o *free client* em <https://itefix.net/cwrsync>, extraia
  (ex.: `C:\cwrsync`) e adicione a subpasta `bin` ao PATH do usuário.
