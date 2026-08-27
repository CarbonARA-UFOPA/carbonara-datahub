# Dashboard de Monitoramento de Aquisição de Dados — `dashboard/`

*by Helvecio Neto (2026)*

Dashboard de **monitoramento de aquisição de dados** do CARBONARA: cadastra estações,
monitora arquivos de dados em tempo quase-real (tail dos `.dat`) e mostra o status de
cada estação a partir do watchdog do `rsync-hub`. **Não lê o banco** — a dashboard
completa virá depois, sobre os dados já ingeridos/qualificados; esta parte de
administração fará parte dela.

Tecnologia deliberadamente simples: **Flask + Chart.js** (vendorado em `static/` —
funciona sem internet), rodando **nativo no host**. Config em YAML, editada pela
própria UI (escrita atômica) ou à mão.

## Rodar

No servidor, use o utilitário `dashboardctl` — ele cria o ambiente Python sozinho
na primeira vez e deixa a dashboard rodando em segundo plano (**pode fechar a
janela do SSH que ela continua no ar**):

```bash
cd dashboard
cp config.example.yaml config.yaml     # ajuste data_root (/srv/carbonara em produção)
./dashboardctl iniciar                 # sobe em segundo plano (venv automático)
./dashboardctl status                  # rodando? endereço + últimas linhas do log
./dashboardctl reiniciar               # após atualizar o código
./dashboardctl parar                   # desliga
./dashboardctl log                     # acompanha o log ao vivo
sudo ./dashboardctl servico            # opcional: vira serviço systemd — além do
                                       # SSH, sobrevive a um REBOOT do servidor
```

Depois do `servico`, o controle passa a ser `sudo systemctl start|stop|restart
carbonara-dashboard` (o próprio utilitário avisa) e logs via
`journalctl -u carbonara-dashboard -f`.

Execução manual (dev), sem o utilitário:

```bash
pip install -r requirements.txt
python run.py                          # http://localhost:8080
# opções: --config outro.yaml --host 0.0.0.0 --port 8080
```

## O que ele mostra

- **Lista de estações** com status ao vivo (mesma regra do `rsync-hub monitor`):
  - 🟣 *Recebendo dados* — CONNECT sem DISCONNECT no `connections.log`, ou atividade ≤ 60 s;
  - 🟢 *Online* — último sinal dentro da janela `2×interval_min + 2 min` (o
    `interval_min` vem do heartbeat `.carbonara-status`; o "último sinal" é o mais
    recente entre mtime do heartbeat, mtime dos arquivos da estação e último CONNECT);
  - 🔴 *Offline* — fora da janela (sem heartbeat válido, janela = `stale_fallback_seconds`, default 1 h);
  - 🟡 *Pausada* — a recepção foi suspensa no hub (`rsync-hub pause`); tem prioridade
    sobre os demais estados, porque uma estação pausada não consegue nem mandar o
    sinal de vida — exibi-la como *Offline* seria enganoso;
  - ⚪ *Sem sinal* — nenhuma fonte encontrada.
- **Faixa de "recepção pausada"** no topo de todas as páginas (visível também para quem
  não é admin, com motivo e horário) — é a explicação de por que os dados pararam.
- **Consumo** por estação (coluna *Consumo*, mesmo formato da *Velocidade*): em
  destaque, o volume da **última transmissão**; abaixo, o total dos **últimos 30 dias**.
  Ambos saem do log do `rsync-hub` — a última transmissão é a sessão mais recente que
  entregou algo (o heartbeat sai com `bytes=0` e não conta), e o total de 30 dias é o
  mesmo número que `rsync-hub volume` mostra no terminal (uma varredura só para todas
  as estações, em cache de 1 min).
- **Ping por estação** (coluna *Ping*, mesmo formato): latência da última transmissão e
  a média das sessões medidas. É medido **ao contrário** — o servidor não manda pacote
  nenhum para a estação (ela está atrás de NAT, sem endereço fixo): quem conecta é a
  estação, e o kernel do servidor já mede o RTT dessa conexão TCP; o `rsync-hub` só lê
  esse valor e o grava no log (`rtt=`). Não tem piso de tamanho como a velocidade — a
  latência não depende do quanto foi enviado, então os ciclos que só mandam o heartbeat
  também contam. Com NAT no caminho, é o RTT até o endereço público da estação (em
  4G/satélite, justamente o trecho que domina). Servidor sem `ss` → coluna vazia.
- **Velocidade por estação** (coluna *Velocidade*): taxa útil da última sessão e a
  média das sessões medidas — payload entregue ÷ duração, do log do `rsync-hub`
  (sessões abaixo de 64 kB são ignoradas, pois a latência domina). Serve para comparar
  os enlaces: qual estação entrega melhor e qual é a pior.
- **Frescor do dado por estação** (coluna *Últ. dado*, ao lado de *Arqv. mon.*):
  último carimbo de tempo lido de **cada arquivo monitorado**. É uma coluna de saúde
  do DADO, não do transporte — uma estação pode estar *Online* (o rsync entrega em
  dia) e um sensor estar parado há dias: o arquivo continua chegando, só que com
  carimbo velho. Alerta por arquivo: **amarelo acima de 1 dia**, **vermelho acima de
  7 dias** (`series.FRESH_WARN_SECONDS` / `FRESH_ALERT_SECONDS`); o pontinho ao lado
  do carimbo resume o pior caso da estação, para varrer a coluna de cima a baixo.
  Em dia, a célula mostra só o carimbo mais recente e "N arquivo(s) em dia" — quem
  atrasou aparece **nomeado** ("qual arquivo?" é sempre a pergunta seguinte), no
  máximo 3 etiquetas mais um "+N"; arquivos sem leitura (o do período ainda não
  chegou, carimbo não reconhecido) ficam em cinza, tom neutro, porque não são falha.
  Passar o mouse na célula lista **todos** os arquivos com seus carimbos. A leitura é
  barata de propósito: só as últimas linhas de cada arquivo (`FRESH_TAIL_LINES`), com
  cache de 30 s por estação invalidado quando o cadastro muda — e roda na view da
  página, nunca em `/api/status`, que outras telas consultam a cada ciclo.
- **Velocidade de rede do servidor** no topo da aba Estações: taxa de download/upload
  da interface que tem a rota padrão (a "com internet"), com as demais interfaces numa
  lista recolhida. Lê os contadores do kernel (`/proc/net/dev`) — não gera tráfego nem
  faz teste de banda. Em sistemas sem `/proc`, o indicador simplesmente não aparece.
- **Gráficos por variável** das últimas N linhas de cada arquivo monitorado (tail
  eficiente — o arquivo nunca é lido inteiro), com auto-atualização.
- **Tipo por coluna** (no cadastro de cada variável): *Número* (padrão), *Inteiro*,
  *Booleano* (0/1, sim/não, on/off), *Data/hora (epoch)* e *Data/hora (texto)* — para as
  outras colunas de carimbo do arquivo, exibidas como hora em vez de número — e *Texto*,
  que não vira gráfico e aparece como "último valor lido". Os tipos valem também na
  pré-visualização: cada coluna mapeada mostra o valor convertido embaixo do valor cru.
- **Resolução do carimbo acompanha os dados**: data completa sempre; o relógio mostra
  minutos para um datalogger de 30 min, segundos para arquivos por segundo e
  milissegundos para cadência sub-segundo — assim registros diferentes nunca viram o
  mesmo rótulo.
- **Carimbo de tempo em Unix epoch**: no cadastro do arquivo, *Tipo do carimbo* →
  *Unix epoch*. Serve para qualquer coluna escolhida como eixo de tempo (não só a
  primeira), aceita fração de segundo (`1787542188.433029`, microssegundos) e também
  valores em ms/µs/ns — a escala é deduzida pela ordem de grandeza.
- **Arquivos que rotacionam pelo relógio**: marque *"O nome do arquivo muda com a
  data/hora"* no cadastro e escreva o caminho como padrão —
  `incoming/profsfo/torre/%Y-%m-%d__*.sfo` (o `*` cobre a parte imprevisível: muitos
  equipamentos nomeiam o arquivo com a hora em que o criaram; sozinho, `*.sfo` é "o
  mais recente da pasta"). A cada leitura ele é resolvido com a
  data do servidor, então um cadastro só acompanha o arquivo do dia. Se o do momento
  ainda não chegou, lê o mais recente que casa com o padrão (avisando qual); se nenhum
  existe ainda, a tela diz que está aguardando, em tom neutro, e não plota nada.
- **Tela Comparar** (`/compare`): selecione variáveis de quaisquer estações/arquivos já
  configurados (checkboxes) e veja **todas as séries num único gráfico**, com
  auto-atualização; a legenda identifica a origem de cada uma (estação · arquivo ·
  variável, com unidade) e clicar nela oculta/mostra a série. O eixo de tempo é a
  **união dos instantes** de todos os arquivos (campo `epochs` da API de séries) —
  séries com amostragens diferentes se alinham pelo tempo real, com `spanGaps` ligando
  os pontos de cada uma. O passo do eixo é **medido nos próprios dados**
  (`cmpAxisStep`): a mediana dos intervalos de cada série, e a **menor** entre as
  séries selecionadas — quem tem a maior resolução manda, então um arquivo de 1 s
  aparece com todos os seus pontos. O teto é 60 s, e é ele que faz dataloggers que
  gravam o mesmo minuto em segundos diferentes (09:00:15 vs 09:00:47) caírem no mesmo
  instante em vez de virarem duas linhas com "—" de um lado. Havendo mais de um ponto
  no mesmo intervalo, prevalece o último. O relógio exibido acompanha o passo (HH:MM,
  com segundos abaixo de 1 min e milésimos abaixo de 1 s) e o passo em uso aparece na
  linha de informação do gráfico. Limites de quanto se vê: **Linhas do final a
  plotar** (`tail_lines`) de cada arquivo e a decimação da API em 500 pontos
  (`series.MAX_POINTS`). Passar o mouse mostra uma **linha vertical** travada no
  instante mais próximo com a leitura de **todas** as séries (tooltip próprio — o
  nativo omitiria séries sem ponto exato no instante): valor exato quando a série tem
  ponto ali, senão o último valor anterior com o horário dele ao lado. Abaixo do
  gráfico, a tabela **Valores comparados** lista um instante do eixo comum por linha
  (mais recente primeiro, teto de 500 linhas), uma coluna por série; "—" = sem ponto
  naquele instante. Todas dividem o mesmo eixo y (grandezas de escalas muito
  diferentes ficam achatadas — compare o comparável); os limites físicos/trechos
  vermelhos são das telas por estação, não desta. A seleção fica na querystring
  (`?sel=<estação>:<arquivo>:<índice>`), então uma combinação pode ser
  favoritada/compartilhada como URL.
- **Tela Mapa** (`/map`, somente visualização): estações com coordenadas cadastradas
  aparecem como marcadores coloridos pelo status (mesmas cores da lista); clicar em uma
  abre uma **subjanela (modal)** sobre o mapa com os gráficos dos arquivos cadastrados
  (fecha no ✕, clicando fora ou com Esc). Camadas base à escolha — **relevo
  (OpenTopoMap) é a padrão** —, além de satélite (Esri World Imagery) e OpenStreetMap. A
  biblioteca **Leaflet é vendorada** em `static/leaflet/` (como o Chart.js), mas os
  *tiles* vêm de servidores públicos — **o mapa precisa de internet**; o resto do
  dashboard segue funcionando offline. Estações sem coordenadas simplesmente não
  aparecem no mapa.

## Cadastro (tudo configurável)

Estações **não são criadas na aba Estações**: elas nascem na aba **Coleta** (autorizar
no rsync-hub) ou pelo botão *Importar do rsync-hub* — assim toda estação da dashboard
tem par no hub. A aba Estações lista e edita o que já existe.

1. **Estação**: id (igual ao do rsync-hub), nome de exibição e coordenadas geográficas
   (latitude/longitude em graus decimais WGS84, opcionais — necessárias apenas para a
   estação aparecer na tela Mapa). Só isso: o **watchdog** e a **janela de offline**
   não se configuram aqui, porque não são do dashboard — o heartbeat é enviado pela
   própria estação (`incoming/<id>/.carbonara-status`, com o `interval_min` que ela
   declara) e a janela deriva dele, sempre `2×intervalo + 2 min`. Para mudar a
   cadência, muda-se o tempo de reciclagem **no cliente da estação**.
2. **Arquivo de monitoramento** (por estação, quantos quiser): caminho do `.dat`,
   quantas linhas do final ler, **período a plotar em horas** (opcional: recorta os
   gráficos às últimas N horas dentro das linhas lidas — se elas não cobrirem o
   período, o gráfico avisa), separador de colunas (`\t` para tabulação), aspas,
   separador decimal, linhas de cabeçalho a pular, linha com nomes de colunas
   (opcional — permite referenciar colunas por nome), coluna+formato do timestamp e a
   lista de variáveis (coluna → rótulo → unidade → limites físicos mín/máx opcionais).
   Um gráfico por variável. Os **limites físicos** viram linhas tracejadas de marcação no
   gráfico e os trechos da série fora deles ficam **vermelhos** — dado bom vs. ruim à
   primeira vista (pré-qualificação visual, alinhada ao `range_check` da camada
   `processing/`; os valores continuam intactos, é só sinalização).
3. **Pré-visualização**: o formulário tem um botão *Pré-visualizar parse* que mostra as
   últimas linhas já separadas em colunas e diagnostica erros comuns (separador errado,
   formato de timestamp que não casa) **antes** de salvar.

Dicas para `.dat` TOA5 (Campbell/LoggerNet): 4 linhas de cabeçalho (`skip_header_lines: 4`),
nomes das colunas na linha 2 (`header_line: 2`), separador `,`, aspas `"`.
Valores `NAN`/`±7999` viram lacunas no gráfico.

## Gerenciamento da coleta (página **Coleta**, só admin)

A página `/hub` administra o **rsync-hub** pela interface — o mesmo que o admin faria
no terminal, sem sair da dashboard:

- **Autorizar estação** (modo chave: gera o par no hub ou cola uma `.pub`; modo
  usuário+senha: porta 873, senha opcional — vazia = gerada), **revogar acesso** e
  **migrar a raiz de dados**. As escritas executam o **próprio `rsync-hub`** (nada é
  reimplementado): direto se a dashboard rodar como root, senão via `sudo -n`.
  Para habilitar, rode **uma vez no servidor**:

  ```bash
  sudo ./dashboard/setup-hub-access.sh <usuario-que-roda-a-dashboard>
  ```

  Ele instala/atualiza o `rsync-hub` em `/usr/local/bin` e grava em
  `/etc/sudoers.d/carbonara-dashboard` uma regra NOPASSWD **restrita aos
  subcomandos** `add`/`remove`/`move-root` (validada com `visudo -cf` antes de
  instalar — nunca dá shell root nem `install`). Sem a regra (ou sem o rsync-hub),
  a página degrada para **somente leitura** com a instrução na tela. O executável é
  procurado em `/usr/local/bin/rsync-hub` e no PATH; `rsync_hub_bin` no config.yaml
  força outro.
- **Credenciais**: ver a chave pública, a linha do `authorized_keys` e o
  `INSTRUCOES.txt`; **baixar** a chave privada/instruções de `stations/<id>/`
  (leitura direta — a política do hub os mantém legíveis sem sudo).
- **Pausar/retomar a recepção** (card *Recepção de dados*, na página Coleta e na
  página de cada estação; também há um botão no detalhe da estação): executa
  `rsync-hub pause|resume <id|--todas>`. Suspende só a **chegada** de arquivos — as
  credenciais continuam válidas, nada é apagado e as estações reenviam o que ficou
  pendente quando a recepção volta. Aceita um *motivo*, que aparece na interface.
- **Excluir estação definitivamente** (zona de perigo na página da estação):
  além de revogar, **apaga do servidor** `incoming/`, `archive/`, `quarantine/` e
  `stations/` da estação (via `rsync-hub purge <id>`) e a remove do cadastro do
  dashboard. Gate triplo: digitar o id exato da estação para destravar o botão,
  diálogo de confirmação, e **revalidação da digitação no servidor** (o JS é só
  conveniência). Irreversível — a UI sugere baixar os arquivos antes; linhas já
  inseridas no TimescaleDB não são afetadas. *Instalações que rodaram o
  `setup-hub-access.sh` antes desta versão precisam rodá-lo de novo* (a regra
  sudoers ganhou o subcomando `purge`).
- **Kit de ativação (.zip)** — botão "Baixar kit da estação" (na página da estação e
  na listagem): um pacote único com tudo o que o operador leva para a máquina da
  estação — credenciais de `stations/<id>/` (chave privada, `.pub`, `INSTRUCOES.txt`),
  os clientes de `collection/station/` (o `.ps1` vai byte a byte, preservando o BOM
  UTF-8 obrigatório) e um `LEIA-ME.txt` gerado. No zip, a chave privada sai `600` e o
  `.sh` executável. Estação sem credenciais no hub (chave gerada na própria estação)
  ainda ganha o kit com cliente + LEIA-ME.
- **Navegador de arquivos** (`incoming/`, `archive/`, `quarantine/`, `stations/`):
  listar com tamanho/data e **baixar** qualquer arquivo. Sempre **somente leitura** —
  a dashboard nunca move/apaga; quem move `incoming/ → archive/|quarantine/` é a
  ingestão. Dotfiles (heartbeat, `.rsync-parcial/`) aparecem marcados como ocultos.
  A aba **Estações → Editar** traz o **mesmo navegador embutido** no card *Arquivos
  desta estação no servidor*: abre já listando `incoming/<id>/`, navega pelas subpastas
  com trilho e "pasta acima", mostra tamanho/data/tipo, permite baixar — e acrescenta a
  **caixa de marcação para monitorar**, com apelido sugerido por arquivo. A seleção
  **sobrevive à troca de pastas** (dá para marcar arquivos de subpastas diferentes e
  adicionar todos de uma vez); arquivos já monitorados e ocultos aparecem, mas
  bloqueados. Um filtro client-side restringe a pasta atual por texto ou extensão.
  Atalhos para *Arquivados*, *Quarentena* e *Gerenciar na Coleta* ficam no cabeçalho do
  card; vindo daí, o botão "voltar" do navegador da Coleta retorna à estação de origem.
  Tudo isso só existe para administrador — o card é renderizado sob `is_admin` e as
  rotas (`api.station_browse`, `pages.station_file_download`, `/hub/...`) são
  `admin_required`, então visitante sem login não vê nem alcança nada. O download é
  escopado a `incoming/<id>/` pela mesma trava anti-traversal do navegador da Coleta.

- **Mover a raiz de dados** (card em *Configurações*): migra a raiz **inteira** do
  hub para outra pasta via `rsync-hub move-root <novo>` — copia tudo preservando
  dono/permissões (`rsync -aH`, duas passadas), muda o home da conta de serviço (o
  sshd acha o `authorized_keys` por ele), regrava `/etc/rsync-hub.conf` e
  regenera/reinicia o rsyncd. Só a raiz completa é movível — mover `incoming/`
  sozinho quebraria os invariantes do hub. A raiz **antiga fica intacta** com um
  `RAIZ-MIGRADA.txt` (remoção manual, depois de conferir). **As estações não
  precisam de nenhuma mudança**: o destino é decidido pelo servidor a cada conexão
  (o `serve` lê o `/etc/rsync-hub.conf`), então os envios caem na nova raiz
  automaticamente. Pendência manual só para quem roda a pilha Docker do projeto
  (banco/ingestão/FTP): ajustar `CARBONARA_ROOT` no `.env` e `docker compose up -d`
  — containers congelam o caminho montado quando sobem (o comando e a UI explicam;
  o `data_root` do dashboard é atualizado automaticamente quando a migração parte
  da UI). Para volumes muito grandes,
  prefira o terminal: `sudo rsync-hub move-root /nova/raiz` (idealmente com
  `docker compose stop ingestion` antes).

Diferente do resto da dashboard, aqui **até a visualização exige login** — as páginas
expõem credenciais e download de dados. Se `/etc/rsync-hub.conf` apontar para uma BASE
diferente do `data_root` do config, a página avisa (as escritas valem para a BASE do
rsync-hub).

## Limites (por desenho)

- Somente leitura sobre os dados: remover estação/arquivo do dashboard **não** apaga
  nada em disco.
- **Login de administração** (bloco `auth:` do config.yaml): a visualização é aberta,
  mas cadastrar/editar/remover exige login. A senha fica como **hash** (nunca texto
  puro); para trocar: gere um hash novo com
  `python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('NOVA-SENHA'))"`
  e cole em `auth.password_hash`. Sem o bloco `auth:`, a edição fica liberada (dev).
  Ainda assim, é pensado para rede interna — não exponha na internet (sem HTTPS aqui).
- O status replica a lógica do `rsync-hub monitor`; se ela mudar lá
  (collection/rsync-hub, `print_status`), atualize `app/status.py` junto.
