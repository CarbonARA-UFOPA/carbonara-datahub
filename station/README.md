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

## Dois modos de autenticação

| Modo | Como funciona | Quando usar |
|------|---------------|-------------|
| **Chave SSH** (recomendado) | rsync-over-SSH na porta 22; a estação usa um arquivo de chave | Sempre que possível — tráfego **criptografado** |
| **Usuário + senha** | rsync direto na porta 873 (rsync daemon); usuário = id da estação | Quando a chave complicar para o operador. **Exige a porta 873/tcp aberta** em todo o caminho até o servidor (não passa pela porta do SSH). A senha não trafega em claro, mas os **dados sim** — use em redes fechadas/VPN |

Nos dois modos a estação só escreve na própria pasta do servidor (write-only, sem
apagar nada lá).

## Fluxo (igual nos dois sistemas)

1. **No servidor** — o administrador autoriza a estação:
   ```bash
   sudo rsync-hub add <id>            # modo CHAVE: gera o par no host
   sudo rsync-hub add <id> --senha    # modo USUÁRIO+SENHA: gera a senha
   ```
   Os dois criam `<raiz>/stations/<id>/` com um **`INSTRUCOES.txt`** de ativação —
   no modo chave, com a **chave privada** (`<id>_key`) para levar à estação; no modo
   senha, com as credenciais (usuário = `<id>`).

2. **Na estação** — rode o assistente de configuração:
   ```bash
   ./carbonara-station.sh setup        # Linux
   ```
   ```powershell
   .\carbonara-station.cmd setup       # Windows (Prompt ou PowerShell)
   ```
   Ele pergunta, nesta ordem: **id da estação** → **IP do host** → **modo de
   autenticação** (chave, porta 22 e caminho do arquivo recebido; ou usuário+senha,
   porta 873 e a senha do `INSTRUCOES.txt`) → faz um **teste simples de conexão** →
   **pasta local a enviar** (rsync) → **tempo de reciclagem** (minutos), já
   oferecendo ativar o envio automático.

   > No modo chave, a estação também pode gerar a própria chave: se o caminho
   > informado não existir, o assistente oferece criar um novo par e mostra a
   > **chave pública** para o administrador autorizar no servidor
   > (`sudo rsync-hub add <id> <chave.pub>`).

3. **Operação** — a qualquer momento, na estação:
   ```bash
   ./carbonara-station.sh test       # testa a conexão (dry-run, nada é enviado)
   ./carbonara-station.sh send       # envia agora os arquivos pendentes
   ./carbonara-station.sh schedule   # (re)agenda o envio periódico (cron / Agendador de Tarefas)
   ./carbonara-station.sh status     # configuração, pendências e últimos envios
   ```
   (No Windows, os mesmos subcomandos com `.\carbonara-station.cmd`.)

Sem argumentos, os dois scripts abrem um **menu interativo** com todas as opções.

> **Windows + Campbell Scientific:** se `C:\Campbellsci\LoggerNet` existir (saída padrão
> do LoggerNet), o `setup` a sugere como pasta de dados default.

## Como o envio funciona

- **A pasta local NUNCA é alterada** — nada é movido nem apagado; o propósito é
  *sincronizar* com o servidor. O controle do que já foi enviado é um **estado local**
  (assinatura tamanho+mtime por arquivo, em `sent.state` na pasta de configuração):
  cada ciclo envia só o que é **novo ou mudou**.
- Arquivos que **crescem** (ex.: `.dat` do LoggerNet recebendo novas leituras) são
  reenviados por inteiro quando mudam — sem problema: a ingestão do servidor é
  **idempotente** e não duplica medições. Para forçar um reenvio completo: `reset`.
- Só entram arquivos **assentados** (mais velhos que a *idade mínima* configurada) — para
  não enviar arquivo que o sensor ainda está escrevendo. Pastas e arquivos ocultos
  (`.algo`) são ignorados, o mesmo critério da ingestão no servidor.
- Em caso de falha, o estado não muda: o próximo ciclo tenta de novo, e o
  `rsync --partial` retoma transferências interrompidas (link instável).

### E se o datalogger estiver gravando na hora do envio?

**A coleta nunca é prejudicada** — o cliente só **lê** os arquivos, e leitura não
bloqueia a escrita do datalogger. Se o software de coleta tiver o arquivo travado, quem
falha é o *envio* (que apenas tenta de novo no próximo ciclo), nunca a gravação. As
proteções, em camadas:

1. A **idade mínima** faz o envio pegar só arquivos "quietos" (sem escrita há N
   minutos) — ajuste-a para caber entre as coletas do datalogger (ex.: coleta a cada
   15 min → idade mínima de 5–10 min funciona bem).
2. Se mesmo assim o arquivo for **appendado durante a transferência**, o instantâneo
   enviado pode ficar com a última linha incompleta — e o sistema se corrige sozinho:
   a assinatura registrada é a de *antes*, então o arquivo (agora diferente) é
   **reenviado completo no ciclo seguinte**, e a ingestão idempotente não duplica nada.
3. No **servidor** não existe arquivo pela metade: o rsync grava num temporário oculto
   e renomeia no final, e a ingestão ignora ocultos e respeita um tempo de assentamento.

Opcional, para quem usa LoggerNet: configurar a rotação de arquivos ("baling"/novo
arquivo por período) reduz ainda mais a janela de colisão — mas não é necessário.
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
