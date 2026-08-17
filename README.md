# GeoJardins

O GeoJardins é uma aplicação desktop para classificação geográfica de demandas da UGR Jardins. A partir de um logradouro e número, o sistema geocodifica o endereço, valida a posição contra a divisão operacional da UGR e identifica a área Tomé e o fiscal responsável. Resultados que exigem revisão permanecem acompanhados de status, confiança e evidências auditáveis.

A decisão territorial não é tomada pelo provider de geocodificação: ela é calculada exclusivamente a partir dos GeoJSONs canônicos da UGR Jardins.

## Principais recursos

- consulta individual por logradouro e número;
- identificação da área Tomé, fiscal e distância até a divisa;
- processamento em lote de XLSX, CSV e TXT;
- exportação XLSX com as colunas originais, status e evidências;
- suporte a cruzamentos, sem exigir altura numérica;
- Google Maps baseado nas coordenadas usadas pelo GIS;
- normalização, aliases contextuais e sugestões locais de logradouro;
- confiança composta, detecção de coordenada genérica e divergência entre providers;
- cache SQLite e configuração de fiscais;
- interface desktop PySide6 e CLI;
- testes automatizados e diagnósticos da base geográfica.

## Início rápido

No PowerShell, a partir da raiz do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Configure pelo menos a chave principal de geocodificação e abra a interface:

```powershell
$env:GEOJARDINS_GEOAPIFY_API_KEY = "SUA_CHAVE"
python -m geojardins.gui
```

A GUI é o caminho recomendado para consultas interativas. A configuração da segunda credencial é descrita abaixo.

## Configuração das APIs

O Geoapify é o provider principal. O LocationIQ é opcional e pode ser usado como fallback ou evidência adicional quando o candidato primário não é utilizável.

```powershell
$env:GEOJARDINS_GEOAPIFY_API_KEY = "SUA_CHAVE"
$env:GEOJARDINS_LOCATIONIQ_TOKEN = "SEU_TOKEN"
```

Os nomes das variáveis são exatamente `GEOJARDINS_GEOAPIFY_API_KEY` e `GEOJARDINS_LOCATIONIQ_TOKEN`. Nunca versione credenciais. O arquivo `.env.example` contém somente placeholders e `.env` é ignorado pelo Git.

A interface também permite informar e persistir as credenciais no perfil local do usuário. O backend não coloca tokens no cache de geocodificação.

## Aplicação desktop

A aplicação é escrita em PySide6 e possui as seguintes telas:

- **Dashboard**: visão operacional e métricas;
- **Consulta individual**: pesquisa de um endereço ou cruzamento;
- **Processamento em lote**: importação, acompanhamento e exportação;
- **Fiscais**: mapeamento configurável entre áreas Tomé e fiscais;
- **Configurações**: margem de divisa, providers e credenciais locais.

As operações de geocodificação e processamento em lote executam em workers fora da thread principal, mantendo a interface responsiva.

## CLI

A GUI é o fluxo principal para uso desktop. Os mesmos recursos podem ser usados pela linha de comando.

### Consulta individual

```powershell
python -m geojardins.address_lookup "Rua Oscar Freire" 1250
python -m geojardins.address_lookup "Rua Oscar Freire" 1250 --verbose
```

`--verbose` exibe provider, evidências de precisão, candidatos considerados e o motivo da decisão.

### Consulta territorial por coordenada

```powershell
python -m geojardins.area_lookup -23.56 -46.67
```

### Processamento em lote

```powershell
python -m geojardins.batch_lookup demandas.xlsx
python -m geojardins.batch_lookup demandas.csv
python -m geojardins.batch_lookup demandas.txt --output resultado.xlsx
```

O lote preserva as colunas de entrada e acrescenta coordenadas, área, fiscal, status, evidências, flags, sugestões e link do Google Maps. Linhas repetidas são deduplicadas por logradouro e altura normalizados antes das consultas. CSV e TXT também podem gerar XLSX como saída.

Opções úteis:

```powershell
python -m geojardins.batch_lookup demandas.xlsx --report output/reports/lote.json
python -m geojardins.batch_lookup demandas.xlsx --compare-baseline output/reports/anterior.json
python -m geojardins.batch_lookup demandas.xlsx --no-cache
python -m geojardins.batch_lookup --clear-cache
```

## Como funciona

```text
logradouro + número
        -> normalização e validação
        -> Geoapify (principal)
        -> LocationIQ quando necessário
        -> validação dos candidatos e das evidências
        -> point-in-polygon nos GeoJSONs canônicos
        -> área Tomé e fiscal
        -> status, confiança e auditoria
```

Os providers informam candidatos e evidências de endereço. Eles não escolhem o território. A inclusão na UGR, a exclusividade da área e a distância da divisa são verificadas localmente contra a partição canônica.

### Normalização, aliases e fuzzy matching

- aliases contextuais ficam em `data/config/address_aliases.json`;
- o dicionário local da UGR é usado como universo do fuzzy matching;
- sugestões de correção são registradas, mas não alteram silenciosamente o texto original;
- `AUTO_SAFE` permanece conservador e depende de margem suficiente sobre a segunda opção;
- cruzamentos aceitam formatos como `Rua A x Rua B`, `Rua A esquina Rua B` e `Rua A com Rua B`.

Para cruzamentos, a altura é descartada da chave e da consulta. Ambas as vias precisam ser validadas; reconhecer apenas uma delas leva a `CONFERIR`.

## Confiança e status

Uma resposta de baixa precisão pode ser promovida de forma conservadora pelo provider principal. LocationIQ não é requisito obrigatório para `HIGH` ou para a promoção. A promoção exige, simultaneamente, evidência estruturada real de endereço/imóvel, logradouro compatível, número exato, coordenada não genérica, área exclusiva, distância segura da divisa e ausência de ambiguidade, homônimo, sobreposição ou candidato forte contraditório. Um provider secundário concordante é evidência adicional, não requisito absoluto.

Os status atuais são:

- `CONFIRMADO`: evidência suficiente e área territorial exclusiva;
- `PROXIMO_DA_DIVISA`: resultado dentro da UGR, mas próximo da margem operacional;
- `CONFERIR`: há candidato, porém alguma evidência, precisão, número, geometria ou divergência exige revisão manual; não é necessariamente erro técnico;
- `FORA_DA_UGR`: coordenada plausível, mas fora da base territorial canônica;
- `ENDERECO_NAO_LOCALIZADO`: nenhum candidato utilizável foi encontrado;
- `ERRO_GEOCODIFICACAO`: falha de configuração, provider, rede ou processamento;
- `DADOS_INVALIDOS`: entrada ausente ou incompatível com o formato esperado.

A ausência de fiscal configurado é indicada separadamente como `NAO CONFIGURADO`; ela não transforma automaticamente uma área válida em erro.

## Cache e dados locais

Em desenvolvimento, o cache de geocodificação fica em:

```text
data/cache/geocoding_cache.sqlite
```

Em uma aplicação empacotada no Windows, o caminho é resolvido fora do executável:

```text
%LOCALAPPDATA%\GeoJardins\cache\geocoding_cache.sqlite
```

A configuração do usuário fica em `%LOCALAPPDATA%\GeoJardins\config.json` e os logs em `%LOCALAPPDATA%\GeoJardins\logs`. Cache, logs, outputs e builds não devem ser versionados.

O mapeamento Tomé -> fiscal é carregado de `config/fiscais.json` e pode ser alterado pela tela **Fiscais**. Os nomes concretos não fazem parte da regra de decisão territorial.

## Reprodução da base geográfica

A base territorial foi extraída do PDF original e validada antes de ser usada pelo produto. Esta seção concentra os comandos de reconstrução e auditoria; eles não são necessários para uma consulta normal quando os GeoJSONs canônicos já estão presentes.

### Extração e GCPs

```powershell
python -m geojardins.polygon_extractor
python -m geojardins.gcp_picker
```

O extrator detecta o PDF em `data/raw/` e gera `data/interim/areas_pdf_coordinates.geojson`. O GCP Picker permite zoom em torno do cursor, pan com o botão do meio, edição e remoção de pontos. Os cliques são convertidos para as coordenadas reais do PDF original.

Os pontos são gravados em `data/control_points/control_points.csv` e distinguem:

- `FIT`: participa do ajuste da transformação;
- `VALIDATION`: fica fora do ajuste e mede o erro de forma independente.

O mínimo operacional é de 6 pontos, com pelo menos 4 `FIT` e 2 `VALIDATION`. Recomenda-se distribuir 12 a 20 pontos por toda a UGR.

Para verificar o picker sem abrir uma janela:

```powershell
python -m geojardins.gcp_picker --check --render-scale 0.2
```

### Georreferenciamento

```powershell
python -m geojardins.georef
```

O pipeline separa `FIT` e `VALIDATION`, testa CRS métricos candidatos (incluindo EPSG:31983 e EPSG:3857), ajusta uma transformação afim, calcula erros independentes, verifica topologia e só publica os GeoJSONs finais quando a validação é aceita. O eixo Y do PDF é tratado pelo modelo afim completo.

Por padrão, `EXCELENTE` exige RMSE de validação de até 5 m e erro máximo de até 10 m; `ACEITAVEL` aceita RMSE de até 10 m e erro máximo de até 25 m. Em outros casos, o relatório é produzido, mas os GeoJSONs finais não são sobrescritos.

Diagnóstico detalhado, sem alterar os pontos:

```powershell
python -m geojardins.gcp_diagnose
```

Os relatórios e imagens ficam em `output/debug/`.

### Partição canônica e consulta territorial

```powershell
python -m geojardins.overlap_diagnose
python -m geojardins.area_lookup -23.56 -46.67
```

A análise de overlaps considera a ordem real dos preenchimentos vetoriais e não usa buffer ou simplificação para decidir fronteiras. A partição final é publicada somente sem decisões `UNRESOLVED`. Pontos na linha de uma área são tratados como ambíguos, sem escolher um lado silenciosamente.

## Testes

A suíte atual usa pytest, inclusive para os widgets PySide6:

```powershell
python -m pip install pytest
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

O estado validado no workspace atual é de 144 testes aprovados e 48 subtests aprovados. Os testes não precisam de tokens e não acessam a internet.

## Build para Windows

O build utiliza PyInstaller e suporta os modos `onedir` e `onefile` definidos em `GeoJardins.spec`.

```powershell
.\scripts\build_windows.ps1
```

O script gera os assets offline do mapa, executa os testes (a menos que `-SkipTests` seja informado), cria os dois formatos, executa o smoke test dos executáveis e grava os artefatos locais em `dist/` e `release/`, incluindo checksum SHA-256.

Para pular os testes durante um build local:

```powershell
.\scripts\build_windows.ps1 -SkipTests
```

Os diretórios e executáveis de build são artefatos locais; o repositório não presume uma URL de release oficial.

## Estrutura do projeto

```text
src/geojardins/
  geocoding/       providers, fallback, cache e modelos
  gui/             aplicação PySide6 e workers
  *.py             normalização, classificação e georreferenciamento
data/
  raw/             fonte original para reconstrução
  interim/         resultados intermediários da extração
  config/          aliases e regras de normalização
  control_points/  GCPs FIT/VALIDATION
  processed/       GeoJSONs canônicos e dicionário local
config/            settings e mapeamento de fiscais
tests/             testes automatizados
scripts/           entrypoint, geração de mapa e build Windows
output/            relatórios e imagens gerados localmente (ignorado)
```

Os recursos canônicos necessários em runtime ficam em `data/processed/`, `config/` e nos assets da GUI. Planilhas de benchmark, `output/`, cache, builds e dados temporários ficam fora do código versionado.
