# GeoJardins Android

Aplicação nativa Kotlin/Jetpack Compose com o motor Python operacional executado
localmente por Chaquopy. O desktop é preservado: o build copia uma lista explícita
de 19 módulos sem alteração, os GeoJSONs canônicos, configurações e mapa.
Os adaptadores de caminhos, credenciais e apresentação existem somente aqui.

Consulte a [análise e matriz de compatibilidade](docs/ANALISE.md) e o
[registro de validação](docs/VALIDACAO.md) antes de homologar o aparelho corporativo.

## Uso

- **Consulta:** endereço/número, cruzamento ou latitude/longitude. Resultados
  conservam status, área candidata versus confirmada, fiscal, distâncias, confiança,
  flags, sugestões e candidatos. Cópia e abertura no Google Maps são explícitas.
- **Lotes:** importar XLSX/CSV/TXT ou colar duas colunas; conferir mapeamento;
  processar, filtrar, pesquisar, selecionar e reprocessar. Cancelamento cooperativo,
  deduplicação e detecção de coordenadas genéricas. Até 5.000 linhas e 20 MB por arquivo.
- **Exportação:** XLSX com todas as colunas/linhas de origem e aba Resumo; JSON de
  auditoria e comparação com relatório anterior. O XLSX de saída é reconstruído:
  não preserva macros, fórmulas executáveis, estilos ou abas arbitrárias do original.
  Linhas ainda não processadas recebem indicação explícita, sem deslocar endereços.
- **Mapa:** imagem operacional offline, zoom/pan e destaque de área com fiscal.
- **Fiscais e Ajustes:** edição local, margem de divisa, Geoapify/LocationIQ,
  remoção de credenciais, limpeza de cache e importação de base validada.

O modo offline usa apenas candidatos do cache. Um endereço ausente produz um
erro orientando reconexão; nunca um resultado fictício. Coordenadas, mapa, fiscais
e resultados persistidos dispensam internet e chave. Não há autenticação própria,
servidor, sincronização automática, localização GPS ou câmera no projeto original.

O build padrão do APK não incorpora nomes de fiscais do ambiente de desenvolvimento:
as áreas começam como `NÃO CONFIGURADO` e podem ser preenchidas no aparelho ou
por uma base privada distribuída pelo canal corporativo aprovado. Não use
`GEOJARDINS_INCLUDE_PRIVATE_DATA=1` em builds destinados a publicação ou
homologação fora desse canal.

As chaves são inseridas no aparelho e cifradas com AES-GCM/Android Keystore.
O APK não incorpora as credenciais do computador. Backup/transferência automática
do app e capturas de tela são bloqueados. A única permissão solicitada é INTERNET;
arquivos entram e saem pelo seletor de documentos Android. Consultas remotas usam
os mesmos providers HTTPS existentes no desktop.

## Compilar no Windows

Requisitos: JDK 17 recomendado (o ambiente deste projeto também pode usar JDK 24
com Gradle 8.14.3), Python 3.10 para o build Chaquopy e Android SDK 35. O Python
desktop e seu ambiente virtual não são alterados. Ferramentas locais são ignoradas
pelo Git e o cache Gradle fica fora do OneDrive, em `%TEMP%/GeoJardins-Android/gradle`.

Na raiz do repositório:

```powershell
.venv\Scripts\python.exe mobile-android\tools-bootstrap.py
powershell -ExecutionPolicy Bypass -File mobile-android\tools\setup-sdk.ps1
powershell -ExecutionPolicy Bypass -File mobile-android\build.ps1
```

Downloads exigem rede e o SDK requer aceite de sua licença. Se já tiver as
ferramentas, defina `JAVA_HOME`, `ANDROID_HOME` e `GEOJARDINS_BUILD_PYTHON`.
O Gradle também aceita `-PbuildPython=C:/caminho/python.exe` diretamente.

Saída debug: `app/build/outputs/apk/debug/app-debug.apk`.
Nesta entrega, há também uma cópia em
`build/deliverables/GeoJardins-0.2.0-android.1-debug.apk`, acompanhada de
`SHA256SUMS.txt`. Novos builds atualizam a saída padrão; a cópia de entrega e seu
checksum devem ser regenerados para cada distribuição.
O identificador debug é `br.com.geojardins.mobile.debug`, separado da distribuição
`br.com.geojardins.mobile`. Android mínimo 8.0/API 26; ABIs ARM64 e x86_64;
compile/target SDK 35. A configuração exata do Samsung ainda precisa ser homologada.

## Assinatura de distribuição

Crie e guarde o keystore fora do repositório, em local aprovado pela organização:

```powershell
keytool -genkeypair -v -keystore C:\LocalSeguro\geojardins.jks -alias geojardins -keyalg RSA -keysize 3072 -validity 10000
```

O `keytool` pede as senhas interativamente. Para compilar uma distribuição assinada,
forneça estas variáveis na sessão local/gerenciador de segredos:

```text
GEOJARDINS_SIGNING_STORE=<caminho do keystore>
GEOJARDINS_SIGNING_STORE_PASSWORD=<senha>
GEOJARDINS_SIGNING_ALIAS=geojardins
GEOJARDINS_SIGNING_KEY_PASSWORD=<senha>
```

```powershell
powershell -ExecutionPolicy Bypass -File mobile-android\build.ps1 -Mode release
```

Sem essas variáveis, `release` produz apenas APK não assinado. Não existe chave
privada de distribuição no projeto. Guarde a chave e sua cópia de recuperação:
atualizações exigem a mesma assinatura. Não use a assinatura debug para distribuição.

## Instalar e atualizar no Samsung

1. Use o APK debug para homologação e confira o SHA-256 fornecido com o artefato.
2. Transfira o arquivo por USB ou canal corporativo autorizado. Não publique o APK
   ou a base operacional em serviços públicos.
3. Abra o APK no aparelho. Se a política permitir, autorize essa origem de instalação.
   Se o MDM/Knox bloquear, solicite a liberação à TI; não contorne a política.
4. Abra GeoJardins. Em Mais → Ajustes, informe sua chave Geoapify; LocationIQ é opcional.
5. Valide coordenadas offline, consulta real, lote, exportação e recuperação de estado.

Com depuração USB autorizada, também é possível usar:

```powershell
adb -d install -r mobile-android\app\build\outputs\apk\debug\app-debug.apk
```

Para atualizar, aumente `versionCode` em `app/build.gradle.kts`, compile com o mesmo
applicationId/keystore e instale sobre a versão anterior. Não desinstale para
atualizar: a desinstalação remove dados privados e chaves locais. Debug e release
são aplicativos distintos; a passagem para release exige configurar as chaves
novamente. Exporte resultados antes de uma migração ou troca de aparelho.

## Atualizar a base sem servidor

Execute a preparação/validação cartográfica existente no desktop. Depois:

```powershell
.venv\Scripts\python.exe mobile-android\tools\export_base.py
```

Transfira `mobile-android/build/base-campo.zip` pelo canal interno aprovado. No app,
Mais → Ajustes → Atualizar base validada. A importação verifica nomes permitidos,
SHA-256, geometrias, partição sem sobreposição e consistência com o boundary/mapa.
O cache é limpo; reprocese resultados históricos para usar a nova base. Hashes
garantem integridade, não autenticam o remetente: aceite somente a fonte corporativa
responsável. Fiscais/margem personalizados no aparelho continuam locais.

Extração PDF, edição visual de GCPs FIT/VALIDATION, georreferenciamento e diagnósticos
cartográficos continuam no desktop. O pacote validado é a alternativa operacional
implementada para disponibilizar essas alterações ao Android.

## Testes

```powershell
.venv\Scripts\python.exe mobile-android\tools\stage_core.py
.venv\Scripts\python.exe -m pytest mobile-android\tests -q
.cache\mobile-tools\python310\tools\python.exe -m pytest mobile-android\tests -q
powershell -ExecutionPolicy Bypass -File mobile-android\build.ps1 -Mode check
```

O comando que usa Python 3.10 requer dependências instaladas nesse ambiente isolado
(Shapely 1.8.5, pyproj 3.4.1, openpyxl 3.1.5 e pytest). Não instale essas versões no
ambiente desktop. Execute a suíte desktop separadamente, com seu `.venv` original.

O corpus `tools/parity_probe.py` compara decisões nos dois ambientes; os testes
instrumentados em `app/src/androidTest` verificam navegação, coordenadas contra a
base real, lote offline e recriação de Activity. Eles usam somente entradas de teste e não
chamam providers pagos. Os screenshots de QA são capturados apenas pelo teste.

## Limites de homologação

A distribuição de bibliotecas Android disponíveis exige Python 3.10/Shapely 1.8.5/
pyproj 3.4.1. Essa combinação difere do desktop e é uma dívida de manutenção.
Os testes de paridade não comprovam todos os casos geométricos possíveis nem
substituem testes com os binários ARM64 reais.

A inspeção desta entrega encontrou 32 bibliotecas nativas com alinhamento ELF
inferior a 16 KB. O APK foi validado em emulador com páginas de 4 KB; suporte a
16 KB não está aprovado. Use `tools/inspect_apk.py` para conferir novos builds.
As bibliotecas incompatíveis exigem rebuild/upgrade antes de homologar um
dispositivo com essa configuração. Não se presume compatibilidade
com o Galaxy A57 apenas pela versão de Android. Não há certificação de desempenho,
bateria ou estabilidade em campo sem testes físicos.

Lotes rodam fora da UI, mas não há serviço permanente em segundo plano. Se o Android
encerrar o processo, a fonte e o último estado concluído são restaurados; reprocesse
com cache. Cancelamento aguarda a consulta/retries corrente. Cache e resultados são
privados, mas exportações passam a estar no destino escolhido pelo usuário.
