package br.com.geojardins.mobile

import android.app.Application
import android.net.Uri
import android.provider.OpenableColumns
import androidx.annotation.Keep
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.chaquo.python.Python
import com.chaquo.python.PyObject
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors

data class MobileState(val data: JSONObject = JSONObject(), val busy: Boolean = true,
    val progress: String = "Preparando base local…", val error: String? = null,
    val detail: JSONObject? = null, val exportPath: String? = null, val exportMime: String = "")

@Keep
class ProgressBridge(private val onUpdate: (String) -> Unit) {
    @Keep fun update(message: String) = onUpdate(message)
}

class MobileModel(app: Application) : AndroidViewModel(app) {
    private val worker = Executors.newSingleThreadExecutor().asCoroutineDispatcher()
    private val mutable = MutableStateFlow(MobileState())
    val state = mutable.asStateFlow()
    private val app = app
    private var engine: PyObject? = null
    private val secrets = Secrets(app)
    private val privateDir = File(app.filesDir, "workspace").apply { mkdirs() }
    init { initialize() }

    private fun initialize() {
        viewModelScope.launch {
            try {
                val data = withContext(worker) {
                    if (!Python.isStarted()) Python.start(AndroidPlatform(app))
                    val base = File(privateDir, "base-${BuildConfig.VERSION_CODE}")
                    val backup = File(privateDir, base.name + ".backup")
                    if (!base.exists() && backup.exists()) check(backup.renameTo(base))
                    if (!File(base, "manifest.json").exists()) copyAssets("base", base)
                    engine = Python.getInstance().getModule("mobile_engine")
                    engine!!.callAttr("initialize", privateDir.absolutePath, base.absolutePath)
                    refreshSecrets()
                    JSONObject(engine!!.callAttr("call", "snapshot", "{}").toString()).getJSONObject("data")
                }
                mutable.value = MobileState(data, busy = false, progress = "")
            } catch (_: Exception) {
                mutable.value = mutable.value.copy(busy = false,
                    error = "Não foi possível iniciar o motor local. Verifique a instalação e a compatibilidade das bibliotecas nativas. Nenhum dado foi enviado.")
            }
        }
    }
    private fun copyAssets(path: String, dest: File) {
        val children = app.assets.list(path) ?: emptyArray()
        if (children.isEmpty()) {
            dest.parentFile!!.mkdirs()
            app.assets.open(path).use { input -> dest.outputStream().use { input.copyTo(it) } }
        } else {
            dest.mkdirs()
            // Manifest last: it is the completion marker for interrupted installation.
            children.sortedBy { it == "manifest.json" }.forEach { copyAssets("$path/$it", File(dest, it)) }
        }
    }
    private fun refreshSecrets() {
        engine!!.callAttr("credentials", secrets.get("geoapify"), secrets.get("locationiq"))
    }
    fun command(operation: String, args: JSONObject = JSONObject()) {
        if (mutable.value.busy || engine == null) return
        mutable.value = mutable.value.copy(busy = true, error = null, progress = "Processando…")
        viewModelScope.launch {
            try {
                val response = withContext(worker) {
                    JSONObject(engine!!.callAttr("call", operation, args.toString(), ProgressBridge {
                        mutable.value = mutable.value.copy(progress = it)
                    }).toString())
                }
                if (response.getBoolean("ok")) {
                    mutable.value = mutable.value.copy(data = response.getJSONObject("data"), busy = false,
                        progress = "", detail = if (operation == "detail") response.optJSONObject("value") else mutable.value.detail,
                        exportPath = if (operation == "export") response.getString("value") else null,
                        exportMime = if (args.optString("kind") == "json") "application/json" else
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                } else mutable.value = mutable.value.copy(busy = false, progress = "", error = response.optString("error"))
            } catch (_: Exception) {
                mutable.value = mutable.value.copy(busy = false, progress = "", error = "Não foi possível concluir a operação. Seus dados locais foram preservados.")
            }
        }
    }
    fun cancel() {
        viewModelScope.launch(Dispatchers.IO) { engine?.callAttr("cancel") }
        mutable.value = mutable.value.copy(progress = "Cancelando após a consulta em andamento…")
    }
    fun dismissError() { mutable.value = mutable.value.copy(error = null) }
    fun dismissDetail() { mutable.value = mutable.value.copy(detail = null) }
    fun exportLaunched() { mutable.value = mutable.value.copy(exportPath = null) }
    fun saveSettings(args: JSONObject, geoapify: String = "", locationiq: String = "", clear: Boolean = false) {
        if (mutable.value.busy || engine == null) return
        mutable.value = mutable.value.copy(busy = true, error = null)
        viewModelScope.launch {
            try {
                withContext(worker) {
                    if (clear) secrets.clear() else {
                        secrets.put("geoapify", geoapify); secrets.put("locationiq", locationiq)
                    }
                    refreshSecrets()
                }
                mutable.value = mutable.value.copy(busy = false)
                command("settings", args)
            } catch (_: Exception) {
                mutable.value = mutable.value.copy(busy = false, error = "Falha no Android Keystore. As credenciais não foram salvas em texto simples.")
            }
        }
    }
    fun importDocument(uri: Uri, baseline: Boolean = false, base: Boolean = false) {
        if (mutable.value.busy) return
        mutable.value = mutable.value.copy(busy = true, error = null, progress = "Importando documento…")
        viewModelScope.launch {
            try {
                val path = withContext(worker) {
                    var name = ""
                    app.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                        if (c.moveToFirst()) name = c.getString(0)
                    }
                    val ext = name.substringAfterLast('.', "").lowercase()
                    require(ext in (if (base) listOf("zip") else if (baseline) listOf("json", "xlsx") else listOf("xlsx", "csv", "txt"))) {
                        "Selecione um arquivo XLSX, CSV ou TXT compatível."
                    }
                    val folder = File(privateDir, "imports").apply { mkdirs() }
                    val target = File(folder, "${UUID.randomUUID()}.$ext")
                    try {
                        app.contentResolver.openInputStream(uri)!!.use { input ->
                            target.outputStream().use { output ->
                                val buffer = ByteArray(65536); var count = 0L
                                while (true) {
                                    val n = input.read(buffer); if (n < 0) break
                                    count += n; require(count <= 20 * 1024 * 1024) { "Arquivo excede 20 MB." }
                                    output.write(buffer, 0, n)
                                }
                            }
                        }
                    } catch (e: Exception) { target.delete(); throw e }
                    target.absolutePath
                }
                mutable.value = mutable.value.copy(busy = false)
                if (base) command("import_base", JSONObject().put("path", path))
                else if (baseline) command("export", JSONObject().put("kind", "json").put("baseline", path))
                else command("import", JSONObject().put("path", path))
            } catch (e: Exception) {
                mutable.value = mutable.value.copy(busy = false, progress = "", error =
                    if (e is IllegalArgumentException) e.message else "Não foi possível ler o documento selecionado.")
            }
        }
    }
    fun saveExport(uri: Uri, path: String) {
        viewModelScope.launch {
            try {
                withContext(worker) {
                    val source = File(path).canonicalFile
                    require(source.toPath().startsWith(privateDir.canonicalFile.toPath()))
                    app.contentResolver.openOutputStream(uri, "wt")!!.use { out -> source.inputStream().use { it.copyTo(out) } }
                }
                mutable.value = mutable.value.copy(progress = "Arquivo salvo no destino escolhido.")
            } catch (_: Exception) { mutable.value = mutable.value.copy(error = "Não foi possível salvar no destino. O resultado continua no app; tente exportar novamente.") }
        }
    }
    override fun onCleared() { super.onCleared(); worker.close() }
}
