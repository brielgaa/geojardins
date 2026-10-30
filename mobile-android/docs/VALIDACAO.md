# Validação da entrega Android

Versão: `0.2.0-android.1`, versionCode 1. Registro consolidado em 13/10/2026.
Código Android independente em `mobile-android/`; nenhuma alteração nos 116
arquivos rastreados do desktop em relação ao início do trabalho, incluindo as
12 alterações que já existiam na cópia do usuário. Comparação por SHA-256.

## Artefatos

- APK debug instalável: `app/build/outputs/apk/debug/app-debug.apk`.
- Cópia de entrega e checksum: `build/deliverables/`.
- Relatório de inspeção do APK: `build/reports/apk-inspection.json`.
- Relatório de execução no Android: `build/reports/instrumentation.txt`.
- Capturas locais de QA: `build/screenshots/`.
- Pacote para atualização local da base: `build/base-campo.zip`.

Os diretórios de build são ignorados pelo Git. O APK é assinado para depuração;
não é uma distribuição corporativa assinada. Não foi publicado externamente.
A configuração de assinatura de distribuição está pronta; geração e guarda da
chave corporativa seguem as instruções do [README](../README.md).

## Testes automatizados

| Validação | Resultado e alcance |
|---|---|
| Desktop, ambiente Python original | 169 testes e 48 subtestes aprovados; GUI Qt executada em modo offscreen |
| Adaptador Android, Python 3.10 isolado | 19 testes aprovados; um aviso esperado do teste que cria ZIP com entrada duplicada |
| Adaptador Android, ambiente Python moderno do desktop | Os mesmos 19 testes aprovados |
| Paridade entre os dois ambientes Python | 111 coordenadas da base canônica e 30 casos sintéticos de resposta de provider; decisões equivalentes, tolerância numérica de 1 mm |
| Build debug e APK de testes | Compilação concluída sem erros |
| Android Lint | Zero erros; 10 avisos de versões de dependências e sugestões KTX |
| Testes instrumentados | Dois fluxos aprovados no Android 15/API 35, x86_64, páginas de 4 KB, tela de 320 × 640 dp |
| Integridade do desktop | 116 arquivos com hashes idênticos ao registro inicial |
| Conteúdo do APK | Nenhum arquivo proibido encontrado pela inspeção de nomes; fontes e dados entram por lista explícita |
| Assinatura debug | Verificada com apksigner; instalação via adb concluída |

Os testes Python cobrem classificação canônica, coordenadas inválidas, cache
offline com acerto e ausência, importação, processamento, reprocessamento,
cancelamento e associação correta das linhas de origem, exportação XLSX/JSON,
restauração de sessão, ausência de segredos na persistência e validação de pacotes
de base alterados, duplicados ou com tentativa de escapar do diretório permitido.

O teste instrumentado usa o Python e as bibliotecas nativas dentro do APK. Navega
por Início, Consulta, Lotes, Mapa, Fiscais e Ajustes; consulta coordenadas sem rede;
verifica um ponto externo à UGR e um ponto interno a Tomé_1; recria a Activity e
verifica o resultado preservado; importa duas linhas coladas,
processa ausências de cache com erro explícito e confere as duas abas do XLSX.
O lote offline não demonstra sucesso de geocodificação remota. O teste de
exportação confere o arquivo gerado; a interação completa com provedores externos
do seletor de documentos depende de homologação no aparelho.

Os casos sintéticos estão restritos aos testes. O app de produção usa o motor,
os providers e a base reais. Nenhuma chamada paga foi realizada e nenhuma
credencial do computador foi incorporada ao APK.

## Verificações manuais e visuais

A inicialização instalada foi conferida no emulador, incluindo o carregamento
da base e a ausência de falha fatal do processo. Foram inspecionadas visualmente
seis capturas em tela pequena: dashboard, resultado territorial, lote, mapa,
fiscais e ajustes. A revisão levou à compactação dos controles do mapa para
ampliar sua área útil. O bloqueio de captura é suspenso
temporariamente apenas pelo APK de testes, sem alterar a proteção do aplicativo.

Não houve teste manual no Samsung nem medição de bateria, consumo de memória em
campo ou desempenho com planilhas de 5.000 linhas. Esses limites não devem ser
interpretados como certificação de desempenho.

## Diferenças em relação ao desktop

- A UI foi reconstruída em Compose; 19 módulos operacionais Python são copiados
  sem edição no build. Resolução de caminhos e armazenamento de credenciais são
  adaptadores exclusivos Android.
- Importação/exportação usa o seletor nativo de documentos, em vez de caminhos
  Windows. A saída XLSX conserva dados e ordem das linhas, mas reconstrói o arquivo:
  não mantém macros, fórmulas executáveis, estilos ou abas arbitrárias de origem.
- A preparação cartográfica (PDF, GCPs, georreferenciamento e diagnósticos) continua
  no desktop. Um ZIP validado disponibiliza a base atualizada ao Android.
- Lotes são executados fora da interface e podem ser cancelados. Não há serviço
  permanente em segundo plano: após encerramento do processo, recupera-se a fonte
  e o último estado salvo; o cache evita consultas repetidas ao reprocessar.
- DPAPI é substituído por AES-GCM com Android Keystore. Apenas INTERNET é
  necessária; a biblioteca AndroidX acrescenta uma permissão própria de assinatura
  para seu receiver interno. Não são pedidos GPS, câmera ou acesso amplo a arquivos.

## Pendências para homologação corporativa

**Compatibilidade de 16 KB não aprovada.** A inspeção encontrou 172 bibliotecas
nativas, das quais 32 têm segmentos ELF com alinhamento inferior a 16 KB. O
emulador validado usa páginas de 4 KB. Antes de homologar um aparelho com páginas
de 16 KB, será necessário atualizar/recompilar essas dependências ou substituir
a integração nativa e repetir os testes. O APK atual não comprova compatibilidade
com o Galaxy A57 5G. A existência da ABI ARM64 no APK não substitui esse teste.

O uso de Python 3.10, Shapely 1.8.5 e pyproj 3.4.1 decorre das distribuições
Android disponíveis para a integração adotada. É uma dívida de manutenção
documentada, isolada do desktop. A paridade observada não cobre todo caso possível.

Ainda dependem do ambiente corporativo:

1. Instalação ARM64 no Samsung, identificação da versão de Android e tamanho de
   página (`adb -d shell getconf PAGE_SIZE`, se depuração USB for autorizada).
2. Geoapify/LocationIQ com chaves inseridas no aparelho; chave inválida, falha de
   rede, reconexão, limites reais e uso de cache após consulta remota bem-sucedida.
3. Seletor de arquivos com os provedores corporativos efetivamente disponíveis,
   abertura de Google Maps e exportação para o destino permitido pela organização.
4. Lotes representativos, troca de aplicativo, encerramento do processo, atualização
   sobre instalação existente e edição de fiscais/configurações em uso prolongado.
5. Fonte ampliada, rotação, acessibilidade, teclado Samsung, consumo de bateria e
   eventuais regras de MDM/Knox. Não foi tentado contornar restrições administrativas.

## Reproduzir o teste Android

Na raiz do repositório, após preparar SDK e ferramentas conforme o README:

```powershell
powershell -ExecutionPolicy Bypass -File mobile-android\tools\setup-emulator.ps1
powershell -ExecutionPolicy Bypass -File mobile-android\tools\start-emulator.ps1
powershell -ExecutionPolicy Bypass -File mobile-android\build.ps1 -Mode check
powershell -ExecutionPolicy Bypass -File mobile-android\tools\test-emulator.ps1
```

O script de teste seleciona explicitamente `emulator-5554`, instala os dois APKs
e grava relatório e capturas locais. Não instala nada no aparelho físico.
