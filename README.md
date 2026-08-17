# GeoJardins

Base geográfica para identificar demandas nas seis áreas operacionais da UGR Jardins.

O projeto extrai as áreas vetoriais do PDF, cadastra Ground Control Points (GCPs), compara transformações métricas e só publica os GeoJSONs finais quando existe validação independente aceitável. Nenhuma coordenada é inventada.

## 1. Preparar o ambiente

No PowerShell, a partir da raiz do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## 2. Extrair as áreas do PDF

```powershell
python -m geojardins.polygon_extractor
```

O comando detecta o PDF em `data/raw/` e gera `data/interim/areas_pdf_coordinates.geojson`, ainda em pontos do PDF, além das imagens e do relatório da extração em `output/debug/`.

## 3. Cadastrar GCPs visualmente

```powershell
python -m geojardins.gcp_picker
```

O picker abre o mapa em alta resolução. A roda do mouse sobre o mapa amplia/reduz em torno do cursor; os botões `+`, `-`, `Ajustar à tela` e `100%` são alternativas visíveis. O nível atual aparece como `Zoom: 245%`. Use o botão do meio do mouse para pan. Atalhos com o mapa em foco: `+`/`=` aproxima, `-` afasta e `0` ajusta à tela. Clique em um cruzamento reconhecível, preencha nome, latitude, longitude e escolha o uso:

- `FIT`: participa do ajuste da transformação;
- `VALIDATION`: não participa do ajuste e mede o erro independentemente.

Use **Salvar novo GCP** para acrescentar um ponto. Para corrigir um ponto, selecione-o na lista e use **Atualizar selecionado**; a substituição exige confirmação. A remoção também exige confirmação. Nomes duplicados nunca sobrescrevem silenciosamente o CSV.

Os pontos são gravados em `data/control_points/control_points.csv`, com o cabeçalho:

```csv
name,pdf_x,pdf_y,latitude,longitude,role,notes
```

### Quantidade e distribuição

O mínimo operacional implementado é:

- 6 GCPs no total;
- pelo menos 4 `FIT`;
- pelo menos 2 `VALIDATION`.

O recomendado é cadastrar 12 a 20 pontos. Uma boa composição é 11 `FIT` e 4 `VALIDATION`, distribuídos por norte, sul, leste, oeste, centro, extremos e regiões intermediárias. O picker avisa quando a extensão dos GCPs cobre menos de 60% do mapa em algum eixo ou menos de três quadrantes. Esse aviso é uma heurística transparente de distribuição, não uma medida de precisão.

As coordenadas geográficas devem vir de uma fonte real e confiável. Evite escolher todos os pontos na Av. Paulista ou usar pontos visualmente ambíguos.

## 4. Executar o georreferenciamento

Depois de preencher os GCPs:

```powershell
python -m geojardins.georef
```

O pipeline:

1. separa `FIT` e `VALIDATION`;
2. converte os GCPs de EPSG:4326 para cada CRS métrico candidato;
3. testa EPSG:31983 e EPSG:3857 com transformação afim;
4. compara os candidatos pelo RMSE geodésico dos pontos `VALIDATION`;
5. calcula erro individual, média, mediana, RMSE e máximo em metros;
6. marca possíveis outliers por mediana e MAD, sem excluí-los;
7. valida geometrias, sobreposições, adjacências, holes e MultiPolygons;
8. publica os GeoJSONs somente se a validação independente for aceita.

O eixo Y invertido do PDF é absorvido pelo modelo afim completo e a orientação resultante fica registrada pelo determinante da transformação.

### Critério operacional de qualidade

Por padrão:

- `EXCELENTE`: RMSE de validação ≤ 5 m e erro máximo ≤ 10 m;
- `ACEITAVEL`: RMSE de validação ≤ 10 m e erro máximo ≤ 25 m;
- `REVISAR`/`INADEQUADO`: relatório gerado, mas GeoJSONs finais não são sobrescritos.

Os limites de 10 m/25 m são guardrails do projeto para classificação urbana próxima a divisas. Podem ser alterados explicitamente por `--max-validation-rmse-m` e `--max-validation-error-m`; os valores usados sempre ficam registrados no relatório.

## 5. Conferir os resultados

O georreferenciamento gera:

- `output/debug/georef_report.json`;
- `output/debug/georef_report.txt`;
- `output/debug/gcp_residuals.png`;
- `data/processed/areas_jardins.geojson`, se aceito;
- `data/processed/ugr_jardins_boundary.geojson`, se aceito.

O relatório compara todos os CRSs candidatos, registra coeficientes, métricas FIT/VALIDATION, resíduos individuais, possíveis outliers, warnings de distribuição e problemas topológicos. Nenhum outlier é removido automaticamente.

### Diagnosticar GCPs sem alterar os dados

Quando o georreferenciamento for rejeitado, execute:

```powershell
python -m geojardins.gcp_diagnose
```

O diagnóstico preserva o CSV e os polígonos. Ele produz `gcp_diagnosis.txt`,
`gcp_diagnosis.json`, `gcp_influence.png` e `gcp_pdf_overlay.png` em
`output/debug/`, com leave-one-out, validação cruzada dos 15 pontos, RANSAC,
comparação afim/homografia/polinomial de 2ª ordem, três projeções métricas,
análise de resíduos e escala entre pares, além da auditoria matemática do picker.

Os relatórios `.txt` são gravados como UTF-8 com BOM para detecção automática no
Windows. No PowerShell, a leitura explícita e portátil é:

```powershell
Get-Content -Encoding UTF8 output\debug\gcp_diagnosis.txt
Get-Content -Encoding UTF8 output\debug\georef_report.txt
```

## 6. Testar uma coordenada

### Resolver e validar as sobreposições

Depois do georreferenciamento aceito, execute:

```powershell
python -m geojardins.overlap_diagnose
```

O diagnóstico lê a ordem real dos preenchimentos vetoriais no PDF, compara a
geometria par-ímpar original com a extração e só publica a partição quando cada
overlap possui evidência inequívoca. A prioridade não é definida por nome. Não
são usados buffer nem simplificação. Os principais resultados são:

- `output/debug/overlap_diagnosis.json` e `.txt`;
- `output/debug/overlaps_map.png`;
- `output/debug/overlaps_source_comparison.png`;
- `data/processed/areas_jardins_partition.geojson`;
- `data/processed/ugr_jardins_boundary_partition.geojson`;
- `data/processed/unresolved_overlaps.geojson`.

Uma coleção `unresolved_overlaps.geojson` vazia registra explicitamente que não
há decisões pendentes. Se houver qualquer `UNRESOLVED`, a partição não é
publicada.

### Consultar a partição

Depois que os GeoJSONs finais existirem:

```powershell
python -m geojardins.area_lookup -23.56 -46.67
```

Exemplo de resposta:

```text
Dentro da UGR Jardins: SIM
Área: Tomé_3
Distância da divisa: 184.0 m
Ambiguidade geométrica: NÃO
```

O lookup prefere automaticamente a partição canônica. A distância é calculada
em SIRGAS 2000 / UTM 23S (EPSG:31983). `covers` é usado para detectar pontos
exatamente na linha; nesses casos a ambiguidade é reportada e nenhum lado é
escolhido silenciosamente. O teste de exclusividade usa `contains`, portanto a
linha matemática não pertence ao interior de nenhuma área. Distâncias de até
1 mm são normalizadas para `0.0 m` apenas como tolerância numérica de
reprojeção/serialização; isso não define a futura zona de segurança do produto.

## 7. Executar os testes

```powershell
python -m unittest discover -s tests -v
```

Para validar tecnicamente o render e o CSV do picker sem abrir uma janela:

```powershell
python -m geojardins.gcp_picker --check --render-scale 0.2
```

## 8. Motor de endereços (Fases 3 e 3A)

O motor recebe `logradouro + altura`, consulta primeiro a Geoapify e usa
LocationIQ como fallback quando a resposta primária não traz um endereço útil.
Depois valida a coordenada contra a partição canônica da UGR Jardins e devolve
área Tomé, fiscal, distância até a divisa, status operacional e um link de
visualização no Google Maps. Nenhum provider decide território: a decisão
continua sendo feita exclusivamente pelos GeoJSONs canônicos.

Na Fase 3A, o endereço de entrada também passa por uma normalização
determinística e auditável. Abreviações e apelidos explícitos ficam em
`data/config/address_aliases.json`; possíveis erros de digitação são apenas
registrados em `Sugestão de Correção`, nunca corrigidos silenciosamente. São
aceitos cruzamentos como `Rua A x Rua B`, `Rua A esquina Rua B` e `Rua A com
Rua B`.

### Configurar Geoapify e LocationIQ

Crie uma conta na [Geoapify](https://www.geoapify.com/) e obtenha uma API key.
A LocationIQ é opcional, porém recomendada para o fallback. Nunca registre as
chaves em arquivos do projeto. No PowerShell, antes de executar uma consulta
real:

```powershell
$env:GEOJARDINS_GEOAPIFY_API_KEY="SUA_CHAVE"
$env:GEOJARDINS_LOCATIONIQ_TOKEN="SEU_TOKEN"
```

O arquivo `.env.example` mostra apenas o nome da variável; `.env` é ignorado
pelo Git. Os testes normais não precisam de token e não acessam a internet.

### Consulta individual

```powershell
python -m geojardins.address_lookup "Rua Oscar Freire" 1250
```

A altura aceita `1250`, `1250.0`, `"1250"` e `"1250 "`; internamente todos são
enviados como `1250`. A saída exibe o candidato selecionado, coordenadas, área,
fiscal, distância de divisa, qualidade do match, status, motivo e URL de Google
Maps baseada exatamente nas coordenadas usadas pelo GIS.

O provider principal usa a busca estruturada da Geoapify com `housenumber`,
`street`, `city`, `state`, `country` e um filtro `rect` obtido do boundary da
UGR com margem métrica configurável. A LocationIQ é consultada apenas quando a
Geoapify não retorna candidato utilizável — número, via, país/contexto de São
Paulo, coordenadas finitas e posição plausível segundo o boundary canônico — ou
falha transitoriamente. Isso é diferente de confirmação: um endereço correto
com building confidence zero ainda é utilizável e pode terminar em `CONFERIR`
sem gastar o fallback. Quando ambos são consultados, seus candidatos
normalizados seguem juntos para o ranking do domínio; fallback não sobrescreve
um candidato melhor automaticamente. Se ambos trouxerem posições plausíveis
mas divergirem além de `provider_divergence_m`, o resultado é marcado
`CONFERIR`, sem escolher uma coordenada arbitrariamente.

A compatibilidade básica é compartilhada entre a decisão de fallback e a
seleção final. Ela aceita `city=São Paulo`; se a cidade vier ausente, aceita o
contexto administrativo `SP` em `state`, `state_code` ou `county_code`. Assim,
respostas OpenAddresses com `state=Southeast` e `county_code=SP` não são
descartadas indevidamente. Um ponto fora da UGR não é utilizável para dispensar
o fallback, mas ainda pode receber `FORA_DA_UGR` se não houver provider
secundário disponível.

A caixa favorece a região, porém `bounded` fica desativado por padrão para não
descartar endereços limítrofes por pequeno desvio do provider. Os campos de
qualidade `matchcode`, `matchtype` e `matchlevel` são mantidos para auditoria.
Um centroid de rua sem número continua sendo `CONFERIR`, mesmo que esteja dentro
da UGR: esta estratégia procura evidência melhor, sem inventar coordenadas.

Para resultados `building`, a regra histórica de `ALTA` continua exigindo número
compatível, `result_type=building`, `match_type=full_match` e confiança geral/de
rua/de building de pelo menos `0.5`. Building confidence entre 0 e 0.5 produz
`MEDIA`; `0` ou ausência de evidência produz `BAIXA` e, portanto, `CONFERIR`.

Para visualizar o diagnóstico resumido da estratégia usada:

```powershell
python -m geojardins.address_lookup "Rua Oscar Freire" 1250 --verbose
```

A saída mostra provider selecionado, `result_type`, house number, níveis de
confiança, candidatos Geoapify retornados/utilizáveis, motivo do fallback e
quantidade de candidatos LocationIQ, além do motivo resumido da seleção. Se o
fallback for necessário mas a chave LocationIQ não estiver configurada, isso
também aparece explicitamente no modo verbose.

Para Geoapify, o caminho existente de precisão `ALTA` para `building` com
`full_match` permanece inalterado. Há também um caminho específico e estrito
para `result_type=amenity`: exige número exato, via com similaridade de pelo
menos `0.95`, país/contexto São Paulo válidos, ponto dentro da UGR e
`rank.confidence`, `confidence_street_level` e `confidence_building_level`
todos de pelo menos `0.95`. Esse limiar está isolado em
`geoapify_precision.AMENITY_HIGH_CONFIDENCE_THRESHOLD`; é uma escolha
operacional conservadora do GeoJardins e pode ser configurada no futuro.
`amenity` e `inner_part` isoladamente não elevam a precisão. No modo
`--verbose`, a evidência usada na decisão é exibida para o candidato selecionado.

Exemplos para auditoria:

```powershell
python -m geojardins.address_lookup "Rua Haddock Lobo" 1307 --verbose
python -m geojardins.address_lookup "Rua Sena Madureira" 1500 --verbose
```

### Processamento em massa

```powershell
python -m geojardins.batch_lookup demandas.xlsx
python -m geojardins.batch_lookup demandas.csv
python -m geojardins.batch_lookup demandas.txt --output resultado.xlsx
```

O XLSX mantém todas as colunas originais e acrescenta ao final:

- `Logradouro Original`, `Logradouro Normalizado`, `Tipo Consulta`, `Latitude`,
  `Longitude`, `Endereço Geocodificado`, `Área`, `Áreas Candidatas` e `Fiscal`;
- evidências do provider (`Match Code`, `Match Type`, `Match Level`, precisão,
  provider principal/utilizado, fallback, distância entre providers e números
  solicitado/retornado/compatível);
- `Status`, `Flags`, `Motivo Status`, `Sugestão de Correção`, `Link Google Maps`
  e `Place ID`.

A coluna Google Maps é um hyperlink sem macros. Para CSV, são aceitos UTF-8,
UTF-8-SIG e fallbacks comuns de Windows/Brasil, com delimitadores `;`, `,`, TAB
ou `|`. TXT aceita `Rua Augusta;2770`, TAB, ou uma única vírgula inequívoca.
Uma linha inválida recebe `DADOS_INVALIDOS` e não interrompe o restante do
lote. CSV e TXT também geram XLSX como saída padrão: `*_geojardins.xlsx`.

As colunas são encontradas pelos aliases `logradouro/endereço/endereco/rua/via`
e `altura/alt num/alt_num/numero/número/num/nº`. Se houver mais de uma opção,
o comando falha de forma segura; informe, por exemplo:

```powershell
python -m geojardins.batch_lookup demandas.xlsx --logradouro-col "Via" --altura-col "Altura numérica"
```

Resultados repetidos na mesma execução são deduplicados por logradouro e
altura normalizados antes de chamar APIs. Há também um cache SQLite persistente
em `data/cache/geocoding_cache.sqlite`, que não armazena tokens. O resumo e o
relatório JSON contabilizam linhas, chaves únicas, consultas evitadas por
deduplicação, hits/misses do cache, fallbacks, divergências e coordenadas
genéricas. Linhas da mesma via com números diferentes que retornem a mesma
coordenada (ou coordenadas muito próximas) forçam a checagem por fallback e
ficam em `CONFERIR` se não houver confirmação independente.

```powershell
python -m geojardins.batch_lookup demandas.xlsx --report output/reports/lote.json
python -m geojardins.batch_lookup demandas.xlsx --compare-baseline output/reports/antes.json
python -m geojardins.batch_lookup demandas.xlsx --divider-warning-m 50
python -m geojardins.batch_lookup demandas.xlsx --no-cache
python -m geojardins.batch_lookup --clear-cache
```

### Status e configurações

Os status são `CONFIRMADO`, `PROXIMO_DA_DIVISA`, `CONFERIR`, `FORA_DA_UGR`,
`ENDERECO_NAO_LOCALIZADO`, `ERRO_GEOCODIFICACAO` e `DADOS_INVALIDOS`. A ordem
de decisão é: dados inválidos, erro técnico, ausência de candidato plausível,
endereço plausível fora da UGR, incerteza de precisão/número/geometria,
proximidade de divisa e confirmação. A falta de fiscal não invalida a área e
aparece como `NÃO CONFIGURADO`.

Edite `config/settings.json` para ajustar, entre outros, a margem operacional
`boundary_warning_m`, `geoapify_bbox_margin_m`, timeouts, tentativas e limites
por segundo de ambos os providers. O rate limiter respeita esses valores entre
os inícios de chamadas. Respostas 429, 5xx e falhas transitórias recebem backoff
e retry limitado; 400, 401 e 403 não são repetidos. Mapeie os fiscais por Tomé
em `config/fiscais.json`, sem inventar nomes.

Os limites novos são explícitos: `provider_divergence_m` (75 m por padrão) não
confirma um provider contra o outro quando a distância excede o valor; apenas
eleva para conferência. `generic_coordinate_tolerance_m` (5 m por padrão) é a
tolerância de detecção de coordenada genérica no lote. A margem territorial
continua em 30 m por padrão e pode ser alterada no arquivo ou por
`--divider-warning-m` em uma execução.

### Fase 3B: confiança composta e auditoria por evidência

A precisão declarada por um provider continua sendo preservada, porém não é a
única evidência operacional. A Fase 3B registra `Confiança Final`, `Motivo
Decisão`, evidências positivas e negativas no objeto de resultado. Uma resposta
de baixa precisão só pode ser promovida a `CONFIRMADO` quando todos estes gates
estão satisfeitos: logradouro e número compatíveis, evidência de imóvel/match,
área exclusiva da UGR, ausência de coordenada genérica e divergência, provider
secundário compatível dentro da tolerância e distância segura da divisa. A regra
não relaxa incompatibilidade de rua, número, sobreposição ou proximidade de
divisa.

Os valores deliberadamente conservadores ficam em `config/settings.json`:

- `provider_agreement_strong_m=15`: concordância forte entre providers;
- `provider_agreement_acceptable_m=50`: concordância suficiente para a regra
  composta, sempre abaixo de `provider_divergence_m=75`;
- `safe_distance_from_divider_m=100`: folga adicional à margem operacional de
  30 m antes de promover um resultado de baixa precisão;
- `street_fuzzy_suggest_threshold=0.88`, `street_fuzzy_auto_threshold=0.97` e
  `street_fuzzy_min_margin=0.04`: limites de sugestão local. Mesmo
  `AUTO_SAFE` não altera silenciosamente a consulta; o texto original é
  preservado para auditoria.

O dicionário local `data/processed/ugr_streets.json` é reproduzível a partir do
texto do PDF oficial, filtrando rótulos curtos e anotações conhecidas. Gere-o
novamente, sem rede, quando a fonte for atualizada:

```powershell
python -m geojardins.build_street_dictionary
```

O fuzzy matching compara apenas com esse dicionário, preserva `Logradouro
Original` e preenche `Sugestão de Correção`, `Tipo da Sugestão` e `Confiança da
Sugestão`. Aliases contextuais também são externos em
`data/config/address_aliases.json`; por exemplo, `CAP` só vira `CAPITÃO` logo
após o tipo de via, nunca por substituição global.

Para cruzamentos, a altura é descartada na chave, na consulta e no link textual
do Google Maps. São exportados os matches de cada via; resultado que reconhece
apenas uma delas fica em `CONFERIR` com `CRUZAMENTO_PARCIAL`.

`Área` e `Fiscal` são preenchidos somente para `CONFIRMADO` e
`PROXIMO_DA_DIVISA`. Em resultados `CONFERIR`, as evidências aparecem como
`Área Candidata` e `Fiscal Candidato`, evitando transmitir certeza indevida.
Os fiscais são carregados exclusivamente de `config/fiscais.json`.

O relatório do lote agora inclui ROI do fallback, cache por provider/modo,
promoções por confiança composta, sugestões e um registro por linha. A
comparação aceita um relatório enriquecido ou a saída XLSX da execução anterior
e mostra transições de status, flags adicionadas/removidas e mudanças de Tomé:

```powershell
python -m geojardins.batch_lookup Pasta_geojardins.xlsx --output output/Pasta_geojardins_fase3b.xlsx --report output/reports/fase3b_689.json
python -m geojardins.batch_lookup Pasta_geojardins.xlsx --output output/Pasta_geojardins_fase3b.xlsx --report output/reports/fase3b_689.json --compare-baseline output/Pasta_geojardins_fase3a.xlsx
```

Um relatório Fase 3A somente agregado, como `output/reports/fase3a_689.json`,
continua fornecendo o delta de status; para transições por linha use o XLSX
Fase 3A correspondente. Nenhum desses comandos é executado automaticamente
pelo projeto, pois podem consumir APIs configuradas pelo operador.
