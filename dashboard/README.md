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

```bash
cd dashboard
cp config.example.yaml config.yaml     # ajuste data_root (/srv/carbonara em produção)
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
  - ⚪ *Sem sinal* — nenhuma fonte encontrada.
- **Gráficos por variável** das últimas N linhas de cada arquivo monitorado (tail
  eficiente — o arquivo nunca é lido inteiro), com auto-atualização.
- **Tela Comparar** (`/compare`): selecione variáveis de quaisquer estações/arquivos já
  configurados (checkboxes) e veja os gráficos lado a lado, com auto-atualização. A
  seleção fica na querystring (`?sel=<estação>:<arquivo>:<índice>`), então uma
  combinação pode ser favoritada/compartilhada como URL.
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

1. **Estação**: id (igual ao do rsync-hub), nome de exibição, coordenadas geográficas
   (latitude/longitude em graus decimais WGS84, opcionais — necessárias apenas para a
   estação aparecer na tela Mapa), opcionalmente o caminho do watchdog
   (default: `incoming/<id>/.carbonara-status`) e, também opcional, uma **janela de
   offline personalizada** em minutos — vazio usa a automática (2×intervalo do
   heartbeat + 2 min); um valor fixo vale **só no dashboard** e faz o status daqui
   divergir do `rsync-hub monitor`.
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
