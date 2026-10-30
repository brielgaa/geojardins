package br.com.geojardins.mobile

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material.icons.automirrored.outlined.List
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // Corporate addresses and credentials must not appear in app-switcher captures.
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(primary = Color(0xFF1D4939),
                secondary = Color(0xFF54675C), background = Color(0xFFF4F7F4),
                surface = Color.White, onSurface = Color(0xFF17372C))) { GeoJardins() }
        }
    }
}

fun JSONObject.text(key: String): String = if (isNull(key)) "" else optString(key)
fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
fun JSONArray.strings(): List<String> = (0 until length()).map { getString(it) }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GeoJardins(model: MobileModel = viewModel()) {
    val state by model.state.collectAsStateWithLifecycle()
    var screen by rememberSaveable { mutableStateOf("Início") }
    var outgoing by rememberSaveable { mutableStateOf("") }
    val screenState = rememberSaveableStateHolder()
    val saveXlsx = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")) { uri ->
        if (uri != null) model.saveExport(uri, outgoing)
    }
    val saveJson = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if (uri != null) model.saveExport(uri, outgoing)
    }
    LaunchedEffect(state.exportPath) {
        state.exportPath?.let {
            outgoing = it
            if (state.exportMime == "application/json") saveJson.launch("GeoJardins-relatorio.json")
            else saveXlsx.launch("GeoJardins-resultados.xlsx")
            model.exportLaunched()
        }
    }
    BackHandler(enabled = screen != "Início") { screen = "Início" }
    val tabs = listOf("Início", "Consulta", "Lotes", "Mapa", "Mais")
    val icons = listOf(Icons.Outlined.Home, Icons.Outlined.Search, Icons.AutoMirrored.Outlined.List, Icons.Outlined.LocationOn, Icons.Outlined.MoreVert)
    Scaffold(topBar = { TopAppBar(title = { Column {
        Text("GeoJardins", fontWeight = FontWeight.Bold)
        Text("UGR Jardins · $screen", style = MaterialTheme.typography.labelMedium)
    } }) }, bottomBar = { NavigationBar {
        tabs.forEachIndexed { i, label -> NavigationBarItem(selected = screen == label || (label == "Mais" && screen in listOf("Fiscais", "Ajustes")),
            onClick = { screen = label }, icon = { Icon(icons[i], label) }, label = { Text(label) }) }
    } }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).imePadding()) {
            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (state.progress.isNotBlank()) Text(state.progress, Modifier.padding(horizontal = 16.dp, vertical = 4.dp), style = MaterialTheme.typography.labelMedium)
            if (state.data.optBoolean("offline")) Text("Modo offline · somente dados locais", Modifier.fillMaxWidth().background(Color(0xFFECF1D7)).padding(12.dp))
            screenState.SaveableStateProvider(screen) { when (screen) {
                "Início" -> Dashboard(state) { screen = it }
                "Consulta" -> LookupScreen(state, model)
                "Lotes" -> BatchScreen(state, model)
                "Mapa" -> MapScreen(state.data.optJSONObject("fiscais") ?: JSONObject(), state.data.text("map_dir"))
                "Fiscais" -> FiscaisScreen(state, model)
                "Ajustes" -> SettingsScreen(state, model)
                else -> Page {
                    Heading("Organize sua operação", "Configurações e referências disponíveis no aparelho.")
                    Action("Fiscais por área", enabled = !state.busy) { screen = "Fiscais" }
                    Action("Ajustes e providers", enabled = !state.busy) { screen = "Ajustes" }
                    Text("GeoJardins ${BuildConfig.VERSION_NAME}\nDesenvolvido por Gabriel da Silva Bitencourt")
                    Text("A base cartográfica é preparada e validada no desktop. As ferramentas de extração PDF e edição de pontos de controle continuam disponíveis nele.")
                }
            } }
        }
    }
    state.error?.let { error -> AlertDialog(onDismissRequest = model::dismissError,
        title = { Text("Não foi possível concluir") }, text = { Text(error) },
        confirmButton = { TextButton(onClick = model::dismissError) { Text("Entendi") } }) }
    state.detail?.let { result -> ModalBottomSheet(onDismissRequest = model::dismissDetail) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(20.dp)) { ResultCard(result) }
    } }
}

@Composable fun Page(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp), content = content)
}
@Composable fun Heading(title: String, caption: String = "") {
    Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
    if (caption.isNotBlank()) Text(caption, style = MaterialTheme.typography.bodyMedium)
}
@Composable fun Action(label: String, enabled: Boolean = true, onClick: () -> Unit) {
    val focus = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    Button(onClick = { focus.clearFocus(); keyboard?.hide(); onClick() }, enabled = enabled, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text(label) }
}
@Composable fun Field(value: String, change: (String) -> Unit, label: String, numeric: Boolean = false, singleLine: Boolean = true) {
    OutlinedTextField(value, change, label = { Text(label) }, modifier = Modifier.fillMaxWidth(), singleLine = singleLine,
        minLines = if (singleLine) 1 else 3, maxLines = if (singleLine) 1 else 6,
        keyboardOptions = KeyboardOptions(keyboardType = if (numeric) KeyboardType.Decimal else KeyboardType.Text))
}

@Composable fun Dashboard(state: MobileState, navigate: (String) -> Unit) = Page {
    Heading("Pronto para o campo", "Consulte endereços e distribua demandas com a base territorial da UGR Jardins.")
    val counts = state.data.optJSONObject("counts") ?: JSONObject()
    val total = counts.keys().asSequence().sumOf { counts.optInt(it) }
    Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("$total", style = MaterialTheme.typography.displaySmall)
        Text("Consultas neste aparelho")
        listOf("CONFIRMADO", "PROXIMO_DA_DIVISA", "CONFERIR", "FORA_DA_UGR").forEach {
            Text("${it.replace('_', ' ')}: ${counts.optInt(it)}")
        }
    } }
    Action("Consultar endereço", !state.busy) { navigate("Consulta") }
    OutlinedButton(onClick = { navigate("Lotes") }, Modifier.fillMaxWidth().heightIn(min = 48.dp), enabled = !state.busy) { Text("Importar lote") }
    Text("Geoapify: ${if (state.data.optBoolean("geoapify")) "configurado" else "sem chave"}\nLocationIQ: ${if (state.data.optBoolean("locationiq")) "configurado" else "opcional, sem chave"}")
    if (state.data.text("notice").isNotBlank()) Text(state.data.text("notice"))
    Text("Área candidata exige conferência. Somente resultados confirmados apresentam área e fiscal definitivos.", style = MaterialTheme.typography.bodySmall)
}

@Composable fun LookupScreen(state: MobileState, model: MobileModel) = Page {
    var coordinate by rememberSaveable { mutableStateOf(false) }
    var street by rememberSaveable { mutableStateOf("") }
    var number by rememberSaveable { mutableStateOf("") }
    var lat by rememberSaveable { mutableStateOf("") }
    var lon by rememberSaveable { mutableStateOf("") }
    Heading("Consulta individual", "Endereço, cruzamento ou coordenadas informadas.")
    Row(verticalAlignment = Alignment.CenterVertically) {
        Switch(checked = coordinate, onCheckedChange = { coordinate = it })
        Text("Consultar coordenadas offline", Modifier.padding(start = 12.dp))
    }
    if (coordinate) {
        Field(lat, { lat = it }, "Latitude (ex.: -23.56)")
        Field(lon, { lon = it }, "Longitude (ex.: -46.67)")
    } else {
        Field(street, { street = it }, "Logradouro ou Rua A x Rua B")
        Field(number, { number = it }, "Número (dispensado em cruzamentos)")
    }
    Action("Consultar", !state.busy && (if (coordinate) lat.isNotBlank() && lon.isNotBlank() else street.isNotBlank())) {
        if (coordinate) model.command("coordinate", JSONObject().put("latitude", lat.replace(',', '.')).put("longitude", lon.replace(',', '.')))
        else model.command("lookup", JSONObject().put("street", street).put("number", number))
    }
    state.data.optJSONObject("last")?.let { result ->
        ResultCard(result)
        val suggestion = result.text("Sugestão de Correção")
        if (suggestion.isNotBlank()) OutlinedButton(onClick = { street = suggestion; coordinate = false }) { Text("Usar sugestão: $suggestion") }
    }
}

@Composable fun ResultCard(result: JSONObject) {
    val context = LocalContext.current
    var evidence by remember { mutableStateOf(false) }
    var linkError by remember { mutableStateOf(false) }
    val status = result.text("Status")
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = when (status) {
        "CONFIRMADO" -> Color(0xFFE2F2E6)
        "CONFERIR", "PROXIMO_DA_DIVISA" -> Color(0xFFFCF5DE)
        else -> Color(0xFFF0F3F0)
    })) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(status.replace('_', ' '), fontWeight = FontWeight.Bold)
        if (result.text("Logradouro Original").isNotBlank()) Text("${result.text("Logradouro Original")}, ${result.text("Número Solicitado")}")
        listOf("Área", "Fiscal", "Área Candidata", "Fiscal Candidato", "Distância Divisa (m)", "Confiança Final", "Motivo Status").forEach { key ->
            if (result.text(key).isNotBlank()) Text("$key: ${result.text(key)}")
        }
        if (result.text("Latitude").isNotBlank()) Text("${result.text("Latitude")}, ${result.text("Longitude")}")
        OutlinedButton(onClick = {
            val clip = ClipData.newPlainText("Resultado GeoJardins", result.keys().asSequence()
                .filter { it != "evidencias" }.joinToString("\n") { "$it: ${result.text(it)}" })
            (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(clip)
        }, Modifier.fillMaxWidth()) { Text("Copiar resultado") }
        if (result.text("Link Google Maps").isNotBlank()) OutlinedButton(onClick = {
            try { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(result.text("Link Google Maps")))) }
            catch (_: Exception) { linkError = true }
        }, Modifier.fillMaxWidth()) { Text("Abrir no Google Maps") }
        if (linkError) Text("Nenhum aplicativo disponível para abrir o link.")
        TextButton(onClick = { evidence = !evidence }) { Text(if (evidence) "Ocultar evidências" else "Ver evidências e candidatos") }
        if (evidence) {
            result.keys().asSequence().filter { it != "evidencias" && result.text(it).isNotBlank() }.forEach { key ->
                Text("$key: ${result.text(key)}", style = MaterialTheme.typography.bodySmall)
            }
            result.optJSONObject("evidencias")?.let { ev ->
                listOf("positivas", "negativas", "bloqueios").forEach { key ->
                    ev.optJSONArray(key)?.strings()?.forEach { Text("$key: $it", style = MaterialTheme.typography.bodySmall) }
                }
                ev.optJSONArray("candidatos")?.objects()?.forEach { c ->
                    Text("${c.text("provider")}: ${c.text("endereco")}\n${c.text("latitude")}, ${c.text("longitude")}\n${c.optJSONArray("motivos")?.strings()?.joinToString("; ") ?: ""}", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    } }
}

@Composable fun Choice(label: String, value: String, options: List<String>, onChange: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { expanded = true }, Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text("$label: ${value.ifBlank { "Selecionar" }}") }
        DropdownMenu(expanded, { expanded = false }, Modifier.heightIn(max = 320.dp)) {
            options.forEach { opt -> DropdownMenuItem(text = { Text(opt) }, onClick = { onChange(opt); expanded = false }) }
        }
    }
}

@Composable fun BatchScreen(state: MobileState, model: MobileModel) {
    var paste by rememberSaveable { mutableStateOf("") }
    var streetColumn by rememberSaveable(state.data.text("source_id")) { mutableStateOf(state.data.text("street_column")) }
    var numberColumn by rememberSaveable(state.data.text("source_id")) { mutableStateOf(state.data.text("number_column")) }
    var filter by rememberSaveable { mutableStateOf("Todos") }
    var search by rememberSaveable { mutableStateOf("") }
    var noCache by rememberSaveable { mutableStateOf(false) }
    var selected by remember(state.data.text("source_id")) { mutableStateOf(setOf<Int>()) }
    val import = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { if (it != null) model.importDocument(it) }
    val baseline = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { if (it != null) model.importDocument(it, true) }
    val rows = state.data.optJSONArray("rows")?.objects() ?: emptyList()
    val headers = state.data.optJSONArray("headers")?.strings() ?: emptyList()
    fun process(indices: Set<Int>? = null) {
        val args = JSONObject().put("street_column", streetColumn).put("number_column", numberColumn).put("use_cache", !noCache)
        if (indices != null) args.put("selected", JSONArray(indices.toList()))
        model.command("batch", args)
    }
    val visible = rows.filter { (filter == "Todos" || it.text("status") == filter) &&
        (it.text("street") + " " + it.text("number") + " " + it.text("area")).contains(search, ignoreCase = true) }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 20.dp).testTag("batch-list"), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(bottom = 24.dp)) {
        item { Heading("Processamento em lote", "Importe XLSX, CSV ou TXT; ou cole duas colunas. Até 5.000 linhas por lote.") }
        item { Action("Selecionar arquivo", !state.busy) { import.launch(arrayOf("*/*")) } }
        item { Field(paste, { paste = it }, "Logradouro e número (TAB, ; ou vírgula)", singleLine = false) }
        item { OutlinedButton(onClick = { model.command("import", JSONObject().put("text", paste)) }, Modifier.fillMaxWidth(), enabled = !state.busy && paste.isNotBlank()) { Text("Importar dados colados") } }
        if (headers.isNotEmpty()) {
            item { Text("${state.data.optInt("total")} linhas importadas") }
            item { Text(state.data.optJSONArray("preview")?.objects()?.joinToString("\n") { row -> headers.joinToString(" | ") { row.text(it) } } ?: "", style = MaterialTheme.typography.bodySmall) }
            item { Choice("Logradouro", streetColumn, headers) { streetColumn = it } }
            item { Choice("Número", numberColumn, headers) { numberColumn = it } }
            item { Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(noCache, { noCache = it }); Text("Consultar novamente sem cache") } }
            item { Action("Processar lote", !state.busy && streetColumn.isNotBlank() && numberColumn.isNotBlank()) { process() } }
            if (state.busy) item { OutlinedButton(onClick = model::cancel, Modifier.fillMaxWidth()) { Text("Cancelar após consulta atual") } }
        }
        if (rows.isNotEmpty()) {
            item { Heading("Resultados", "${rows.size} de ${state.data.optInt("total")} linhas processadas. Toque em um cartão para ver as evidências.") }
            item { Field(search, { search = it }, "Pesquisar endereço, número ou área") }
            item { Choice("Status", filter, listOf("Todos") + rows.map { it.text("status") }.distinct()) { filter = it } }
            item { Action("Exportar XLSX com resumo", !state.busy) { model.command("export") } }
            item { OutlinedButton(onClick = { model.command("export", JSONObject().put("kind", "json")) }, Modifier.fillMaxWidth(), enabled = !state.busy) { Text("Salvar relatório JSON") } }
            item { OutlinedButton(onClick = { baseline.launch(arrayOf("*/*")) }, Modifier.fillMaxWidth(), enabled = !state.busy) { Text("Comparar com relatório anterior") } }
            item { OutlinedButton(onClick = { process(selected) }, Modifier.fillMaxWidth(), enabled = !state.busy && selected.isNotEmpty()) { Text("Reprocessar ${selected.size} selecionadas") } }
            item { OutlinedButton(onClick = {
                val done = rows.associate { it.getInt("index") to it.text("status") }
                process((0 until state.data.optInt("total")).filter { done[it] !in listOf("CONFIRMADO", "PROXIMO_DA_DIVISA", "FORA_DA_UGR") }.toSet())
            }, Modifier.fillMaxWidth(), enabled = !state.busy) { Text("Reprocessar pendências") } }
            items(visible, key = { it.getInt("index") }) { row ->
                val index = row.getInt("index")
                Card(Modifier.fillMaxWidth().clickable(enabled = !state.busy) { model.command("detail", JSONObject().put("index", index)) }) {
                    Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(index in selected, { checked -> selected = if (checked) selected + index else selected - index })
                        Column(Modifier.weight(1f)) {
                            Text("${index + 1}. ${row.text("street")}, ${row.text("number")}", fontWeight = FontWeight.SemiBold)
                            Text(row.text("status").replace('_', ' '), style = MaterialTheme.typography.labelMedium)
                            Text(if (row.text("area").isNotBlank()) row.text("area") else "Área candidata: ${row.text("candidate_area").ifBlank { "—" }}")
                        }
                    }
                }
            }
            if (visible.isEmpty()) item { Text("Nenhum resultado corresponde ao filtro.") }
        }
    }
}

@Composable fun FiscaisScreen(state: MobileState, model: MobileModel) = Page {
    val current = state.data.optJSONObject("fiscais") ?: JSONObject()
    var names by remember(current.toString()) { mutableStateOf(current.keys().asSequence().associateWith { current.text(it) }) }
    var search by rememberSaveable { mutableStateOf("") }
    Heading("Fiscais por área", "Alterações são salvas neste aparelho. Não modificam a configuração do desktop.")
    Field(search, { search = it }, "Pesquisar área ou fiscal")
    names.toSortedMap().filter { (it.key + it.value).contains(search, true) }.forEach { (area, name) ->
        Field(name, { names = names + (area to it) }, area)
    }
    Action("Salvar fiscais", !state.busy) { model.command("settings", JSONObject().put("fiscais", JSONObject(names))) }
}

@Composable fun SettingsScreen(state: MobileState, model: MobileModel) = Page {
    val baseImport = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { if (it != null) model.importDocument(it, base = true) }
    val settings = state.data.optJSONObject("settings") ?: JSONObject()
    var margin by remember(settings.toString()) { mutableStateOf(settings.text("boundary_warning_m")) }
    var offline by remember(state.data.optBoolean("offline")) { mutableStateOf(state.data.optBoolean("offline")) }
    // Never save secrets in Bundle / SavedStateHandle.
    var geoapify by remember { mutableStateOf("") }
    var locationiq by remember { mutableStateOf("") }
    var reveal by remember { mutableStateOf(false) }
    var confirmClear by remember { mutableStateOf(false) }
    Heading("Ajustes", "Chaves são protegidas pelo Android Keystore e não são exibidas novamente.")
    Field(margin, { margin = it }, "Margem de divisa (metros)", numeric = true)
    Row(verticalAlignment = Alignment.CenterVertically) { Switch(offline, { offline = it }); Text("Modo offline", Modifier.padding(start = 12.dp)) }
    Text("Geoapify: ${if (state.data.optBoolean("geoapify")) "chave salva" else "sem chave"}")
    OutlinedTextField(geoapify, { geoapify = it }, label = { Text("Nova chave Geoapify") }, modifier = Modifier.fillMaxWidth(), singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        visualTransformation = if (reveal) VisualTransformation.None else PasswordVisualTransformation())
    Text("LocationIQ: ${if (state.data.optBoolean("locationiq")) "token salvo" else "opcional"}")
    OutlinedTextField(locationiq, { locationiq = it }, label = { Text("Novo token LocationIQ") }, modifier = Modifier.fillMaxWidth(), singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        visualTransformation = if (reveal) VisualTransformation.None else PasswordVisualTransformation())
    TextButton(onClick = { reveal = !reveal }) { Text(if (reveal) "Ocultar chaves digitadas" else "Mostrar chaves digitadas") }
    val parsedMargin = margin.replace(',', '.').toDoubleOrNull()
    if (parsedMargin == null || !parsedMargin.isFinite() || parsedMargin < 0) Text("Informe uma margem válida, maior ou igual a zero.", color = MaterialTheme.colorScheme.error)
    Action("Salvar ajustes", !state.busy && parsedMargin != null && parsedMargin.isFinite() && parsedMargin >= 0) {
        model.saveSettings(JSONObject().put("settings", JSONObject().put("boundary_warning_m", parsedMargin)).put("offline", offline), geoapify, locationiq)
        geoapify = ""; locationiq = ""; reveal = false
    }
    OutlinedButton(onClick = { model.saveSettings(JSONObject(), clear = true) }, Modifier.fillMaxWidth(), enabled = !state.busy) { Text("Remover credenciais salvas") }
    OutlinedButton(onClick = { confirmClear = true }, Modifier.fillMaxWidth(), enabled = !state.busy) { Text("Limpar cache de geocodificação") }
    if (confirmClear) AlertDialog(onDismissRequest = { confirmClear = false }, title = { Text("Limpar cache?") },
        text = { Text("Endereços já consultados precisarão de conexão para nova consulta. Resultados de lote serão preservados.") },
        confirmButton = { TextButton(onClick = { confirmClear = false; model.command("clear_cache") }) { Text("Limpar") } },
        dismissButton = { TextButton(onClick = { confirmClear = false }) { Text("Manter") } })
    Text("Somente a permissão de internet é utilizada. Importação e exportação usam os arquivos escolhidos por você. Não há sincronização automática.")
    OutlinedButton(onClick = { baseImport.launch(arrayOf("application/zip", "application/octet-stream")) }, Modifier.fillMaxWidth(), enabled = !state.busy) { Text("Atualizar base validada (.zip)") }
    Text("Use somente pacotes fornecidos pelo responsável pela base. A atualização verifica integridade e topologia e limpa o cache. Resultados anteriores permanecem históricos.", style = MaterialTheme.typography.bodySmall)
    Text("Para lotes longos, mantenha o app em primeiro plano. Se o Android encerrar o processo, importe/retome a fonte preservada usando o cache.")
}
