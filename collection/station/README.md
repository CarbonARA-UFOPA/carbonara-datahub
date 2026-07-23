# Clientes das estações (`collection/station/`)

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
   **pasta local principal a enviar** (rsync) e seus **filtros opcionais** (tipos de
   arquivo e subpastas a *não* enviar) → **tempo de reciclagem** (minutos), já
   oferecendo ativar o envio automático. Mais pastas podem ser incluídas depois
   (veja *Várias pastas e filtros*, abaixo).

   > No modo chave, a estação também pode gerar a própria chave: se o caminho
   > informado não existir, o assistente oferece criar um novo par e mostra a
   > **chave pública** para o administrador autorizar no servidor
   > (`sudo rsync-hub add <id> <chave.pub>`).

3. **Operação** — a qualquer momento, na estação:
   ```bash
   ./carbonara-station.sh test       # testa a conexão (dry-run, nada é enviado)
   ./carbonara-station.sh send       # envia agora os arquivos pendentes
   ./carbonara-station.sh folders    # gerencia as pastas (incluir/remover/filtrar)
   ./carbonara-station.sh schedule   # (re)agenda o envio periódico (cron / Agendador de Tarefas)
   ./carbonara-station.sh status     # configuração, pendências e últimos envios
   ./carbonara-station.sh purge      # remove a configuração/conexão e o agendamento (zera)
   ```
   (No Windows, os mesmos subcomandos com `.\carbonara-station.cmd`.)

Sem argumentos, os dois scripts abrem um **menu interativo** com todas as opções. O
**cabeçalho do menu** mostra a estação, o servidor e a lista de **pastas sincronizadas**
(com seus filtros) já assim que o script abre — desde que já esteja configurado.

> **Windows + Campbell Scientific:** se `C:\Campbellsci\LoggerNet` existir (saída padrão
> do LoggerNet), o `setup` a sugere como pasta de dados default.

## Várias pastas e filtros

A estação pode enviar **mais de uma pasta**. Cada pasta configurada vai para um
**sub-diretório próprio** no servidor (o *rótulo*, derivado do nome da pasta), então
arquivos de nome igual em pastas diferentes **não colidem**.

Gerencie as pastas pelo menu (**Gerenciar pastas de sincronização**) ou pelo subcomando
`folders` — dá para **incluir**, **remover** e, por pasta, definir dois **filtros
opcionais**:

- **Tipos de arquivo a não enviar** — globs separados por vírgula (ex.: `*.tmp,*.bak`),
  casados contra o *nome* do arquivo.
- **Subpastas a não enviar** — nomes/relativos separados por vírgula (ex.: `temp,cache`);
  qualquer arquivo dentro de uma subpasta com esse nome é ignorado.

Os filtros valem só na *seleção* do que enviar — as pastas locais continuam intocadas.
Alterar filtros ou pastas **reconfigura automaticamente** o que os próximos ciclos
sincronizam; rode `schedule` de novo se quiser mudar o intervalo.

## Como o envio funciona

- **As pastas locais NUNCA são alteradas** — nada é movido nem apagado; o propósito é
  *sincronizar* com o servidor. **A cada ciclo, TODAS as pastas configuradas são
  varridas de novo** — arquivos criados depois da primeira sincronização entram
  automaticamente. O controle do que já foi enviado é um **estado local** (assinatura
  tamanho+mtime por arquivo, chaveado por `rótulo/caminho` em `sent.state`, na pasta de
  configuração): cada ciclo envia só o que é **novo ou mudou**.
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

### Como o servidor sabe que a estação está Online/Offline?

Pelo **sinal de vida**: a cada ciclo de envio (mesmo sem dados novos), o cliente sobe um
arquivinho oculto `.carbonara-status` para a pasta da estação no servidor, declarando o
**tempo de reciclagem** configurado. O monitor (`rsync-hub monitor`) usa o horário de
**chegada** desse arquivo (imune a fuso/relógio da estação) e marca **Offline** quem
ficar **2 ciclos + 2 minutos** sem dar sinal — ou seja, o limite é individual: uma
estação com ciclo de 15 min fica vermelha após ~32 min de silêncio. A ingestão ignora o
arquivo (é oculto). Estações sem sinal de vida (clientes antigos) caem na janela global
de 1 hora do servidor.

### Posso fechar a janela? (Windows)

Pode. Quem executa o envio periódico é o **Agendador de Tarefas** do Windows, não a
janela — depois do `schedule` (ou do "ativar envio automático" no `setup`), feche o
`cmd`/PowerShell à vontade. Detalhe importante:

- `schedule` rodado em PowerShell **comum**: a tarefa executa enquanto o usuário
  estiver **logado** no Windows (após reiniciar, é preciso logar de novo).
- `schedule` rodado em PowerShell **como administrador**: a tarefa é criada como
  **SYSTEM** — sincroniza com a janela fechada, **sem ninguém logado e após
  reiniciar**, sem piscar janelas. Recomendado para estações de campo.

Confira com `status` (mostra se a tarefa está ativa) e pelos carimbos em `envios.log`.

## Configuração gravada

- Linux: `~/.config/carbonara-station/config` + `folders` (lista de pastas) e
  `envios.log` ao lado.
- Windows: `%LOCALAPPDATA%\carbonara-station\config.json` (as pastas ficam no próprio
  JSON, em `Folders`) + `envios.log` ao lado.

Rodar `setup` de novo reconfigura (os valores atuais viram os defaults do assistente); ao
chegar nas pastas, ele oferece **manter** as já configuradas. Configurações antigas (uma
única pasta) são **migradas** automaticamente na primeira execução do novo cliente.

Para **começar do zero** (trocar de servidor, refazer a conexão), use `purge` (ou a opção
correspondente no menu): ele apaga a configuração, a lista de pastas, o estado de envio, o
log e **o agendamento** — mas **não toca nas pastas de dados nem no arquivo de chave**, que
ficam fora da pasta de configuração. No Windows, se o agendamento tiver sido criado como
**SYSTEM**, rode o `purge` (ou `unschedule`) em um PowerShell **como administrador** para
conseguir removê-lo.

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
