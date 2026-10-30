# Análise antes da implementação

Análise do workspace em 08/10/2026. Nenhum arquivo funcional do desktop é alterado.
Baseline: 169 testes + 48 subtestes aprovados (pytest, Qt offscreen). Há alterações
prévias do usuário em 12 arquivos; elas devem permanecer byte a byte intactas.

## Arquitetura encontrada

Python >=3.11, setuptools, PySide6 (GUI e QThreads), SQLite (cache), JSON
(configurações, fiscais, aliases e dicionário), GeoJSON (partição canônica),
openpyxl (XLSX), csv (CSV/TXT), urllib HTTPS (Geoapify e LocationIQ), Shapely e
pyproj (geometria e SIRGAS 2000/UTM 23S). NumPy, Matplotlib, PyMuPDF e pikepdf
pertencem principalmente à preparação e auditoria da base. O empacotamento
Windows usa PyInstaller. Não há backend próprio, autenticação de usuário,
sincronização remota, câmera ou captura GPS no aplicativo atual.

A GUI tem Dashboard, Consulta individual, Lote, Fiscais e Configurações. A
decisão é implementada em address_lookup/classification/confidence, não na GUI.
O cache contém candidatos limpos e metadados, sem tokens. user_config usa DPAPI
no Windows; runtime resolve LOCALAPPDATA. Esses dois componentes são específicos
da plataforma. A GUI não é portável para Android sem reconstrução.

## Matriz de compatibilidade

| Recurso e código de origem | Estratégia Android |
|---|---|
| Endereço, cruzamento, validação: address_normalizer/address_lookup | Reutilizar Python sem editar o original |
| Aliases, sugestões conservadoras, street_dictionary | Reutilizar código e JSON |
| Confiança, precisão, candidatos e divergências | Reutilizar confidence/candidate_compatibility/geoapify_precision |
| Point-in-polygon, divisas, sobreposição, EPSG:31983 | Reutilizar area_lookup/classification e GeoJSONs; validar bibliotecas Android |
| Geoapify principal, LocationIQ opcional, retries e limites | Reutilizar providers HTTPS; credenciais informadas no aparelho |
| Cache SQLite | Mesmo esquema, arquivo privado Android, modo offline explícito |
| XLSX/CSV/TXT/paste, inferência e seleção de colunas | Reutilizar batch_lookup; seletor de documentos Android |
| Deduplicação, coordenada genérica, revisão e cancelamento | Reutilizar BatchProcessor; trabalho fora da UI |
| Exportação XLSX, Resumo, relatório JSON e comparação | Reutilizar exportadores; salvar via Storage Access Framework |
| Dashboard e indicadores | Reconstruir em Compose com resultados reais |
| Fiscais e margem de divisa | Reutilizar modelos; editor mobile e persistência privada |
| Mapa offline, zoom e destaque de área | Adaptar assets existentes e desenho com toque |
| Link Google Maps e cópia de resultado | Intents e clipboard Android |
| DPAPI, LOCALAPPDATA, titlebar Windows, PyInstaller | Substituir só no módulo Android: Keystore, filesDir, Activity e Gradle |
| Extração PDF, editor de GCPs, georreferenciamento, diagnóstico de overlaps e construção do dicionário | Ferramentas de preparação continuam no desktop; transferência local da base validada por pacote com hashes. Não são telas Android de edição cartográfica |

## Escolha técnica e limites

Kotlin + Jetpack Compose para a UI; Chaquopy para executar o núcleo Python
localmente. Uma reescrita integral em Kotlin duplicaria milhares de linhas de
regras conservadoras. Flutter ou WebView não tornam PySide6 ou GEOS portáveis.
Nenhum servidor ou publicação externa é necessário.

O índice oficial Chaquopy possui Shapely 1.8.5 até Python 3.10; o desktop exige
Shapely >=2. O caminho inicial Android usa Python 3.10, Shapely 1.8.5 e pyproj
3.4.1, restritos ao núcleo operacional que usa APIs presentes nessas versões.
Isso exige testes de paridade e NÃO implica suporte aos utilitários cartográficos
que usam APIs Shapely 2. A idade desses binários e suporte a páginas de memória
de 16 KB precisam ser tratados na homologação; não se pode declarar compatibilidade
plena com o Samsung sem instalar e executar no aparelho. O build e o relatório
final devem distinguir código implementado de validação em dispositivo.

Fontes técnicas: [Chaquopy](https://chaquo.com/chaquopy/doc/current/android.html),
[Shapely Android](https://chaquo.com/pypi-13.1/shapely/),
[pyproj Android](https://chaquo.com/pypi-13.1/pyproj/),
[compatibilidade AGP](https://developer.android.com/build/releases/agp-8-9-0-release-notes).

## Segurança e armazenamento

Somente recursos explicitamente permitidos entram no APK; nunca .env, cache,
configuração pessoal, planilhas, logs ou chaves de assinatura. Chaves dos providers
usam AES-GCM com chave no Android Keystore. Backup de dados do app é desativado.
Permissão INTERNET apenas; arquivos usam escolha explícita do usuário, sem acesso
amplo ao armazenamento. Sem GPS/câmera porque não existem no fluxo original.
Consultas novas de endereço usam os mesmos providers do desktop e dependem de
internet/chave. Consultas territoriais, mapa, fiscais e resultados locais são
offline. Não se presume VPN ou servidor interno inexistente no código.
