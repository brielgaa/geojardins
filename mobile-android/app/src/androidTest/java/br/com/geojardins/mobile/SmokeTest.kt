package br.com.geojardins.mobile

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import org.junit.Rule
import org.junit.Test
import androidx.test.platform.app.InstrumentationRegistry
import android.graphics.Bitmap
import android.view.WindowManager
import java.io.File
import com.chaquo.python.Python
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Assert.assertNotNull
import java.util.zip.ZipFile
import androidx.lifecycle.ViewModelProvider
import android.content.Context
import android.view.inputmethod.InputMethodManager

/** Exercises the actual embedded Python runtime and real offline canonical data. */
class SmokeTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private fun closeSoftKeyboard() {
        compose.activityRule.scenario.onActivity {
            (it.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
                .hideSoftInputFromWindow(it.window.decorView.windowToken, 0)
        }
        compose.waitForIdle()
    }
    private fun ready() {
        compose.waitUntil(120_000) {
            compose.onAllNodesWithText("Consultar endereço").fetchSemanticsNodes().isNotEmpty() &&
                compose.onAllNodes(hasText("Consultar endereço") and isEnabled()).fetchSemanticsNodes().isNotEmpty()
        }
    }
    private fun capture(name: String) {
        compose.activityRule.scenario.onActivity { it.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
        compose.waitForIdle()
        // Window security changes reach SurfaceFlinger after Compose becomes idle.
        InstrumentationRegistry.getInstrumentation().waitForIdleSync()
        android.os.SystemClock.sleep(700)
        val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        assertTrue("Screenshot protected or blank: $name", (1..8).any {
            (bitmap.getPixel(bitmap.width / 2, bitmap.height * it / 10) and 0x00ffffff) != 0
        })
        val file = File(compose.activity.filesDir, "screenshots/$name.png")
        file.parentFile!!.mkdirs()
        file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        compose.activityRule.scenario.onActivity { it.window.addFlags(WindowManager.LayoutParams.FLAG_SECURE) }
    }
    @Test fun offlineCoordinateAndNavigationSurviveActivityRecreation() {
        ready()
        capture("dashboard")
        compose.onNodeWithText("Consultar endereço").performScrollTo().performClick()
        compose.onNode(isToggleable()).performClick()
        compose.onNodeWithText("Latitude (ex.: -23.56)").performTextInput("0")
        compose.onNodeWithText("Longitude (ex.: -46.67)").performTextInput("0")
        closeSoftKeyboard()
        compose.onNodeWithText("Consultar", useUnmergedTree = true).performScrollTo().performClick()
        compose.waitUntil(30_000) { compose.onAllNodesWithText("FORA DA UGR").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("FORA DA UGR").performScrollTo().assertIsDisplayed()
        capture("coordinate")
        compose.activityRule.scenario.recreate()
        compose.onNodeWithText("FORA DA UGR").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Latitude (ex.: -23.56)").performScrollTo().performTextReplacement("-23.605152209231875")
        compose.onNodeWithText("Longitude (ex.: -46.67)").performScrollTo().performTextReplacement("-46.63846029358753")
        closeSoftKeyboard()
        compose.onNodeWithText("Consultar", useUnmergedTree = true).performScrollTo().performClick()
        compose.waitUntil(30_000) { compose.onAllNodesWithText("Área: Tomé_1").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Área: Tomé_1").performScrollTo().assertIsDisplayed()
        capture("coordinate")
        compose.onNodeWithText("Mapa", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Mapa operacional").assertIsDisplayed()
        compose.waitUntil(15_000) { compose.onAllNodesWithTag("offline-map").fetchSemanticsNodes().isNotEmpty() }
        capture("map")
        compose.onNodeWithText("Mais", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Fiscais por área").performClick()
        compose.onNodeWithText("Salvar fiscais").performScrollTo().assertIsDisplayed()
        capture("fiscais")
        compose.onNodeWithText("Mais", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Ajustes e providers").performClick()
        compose.onNodeWithText("Margem de divisa (metros)").assertIsDisplayed()
        capture("settings")
    }
    @Test fun batchImportsAndProcessesWithoutNetwork() {
        ready()
        compose.onNodeWithText("Mais", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Ajustes e providers").performClick()
        if (compose.onAllNodesWithText("Modo offline · somente dados locais").fetchSemanticsNodes().isEmpty()) {
            compose.onNode(isToggleable()).performClick()
        }
        compose.onNodeWithText("Salvar ajustes").performScrollTo().performClick()
        compose.waitUntil(15_000) { compose.onAllNodesWithText("Modo offline · somente dados locais").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Lotes", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Logradouro e número (TAB, ; ou vírgula)").performTextInput("Logradouro;Numero\nRua Oscar Freire;1250\nRua Augusta;42")
        closeSoftKeyboard()
        compose.onNodeWithText("Importar dados colados").performScrollTo().performClick()
        compose.waitUntil(15_000) { compose.onAllNodesWithText("2 linhas importadas").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("batch-list").performScrollToNode(hasText("Processar lote"))
        compose.onNodeWithText("Processar lote").assertIsEnabled().performClick()
        val model = ViewModelProvider(compose.activity)[MobileModel::class.java]
        compose.waitUntil(30_000) { !model.state.value.busy }
        assertTrue(model.state.value.error ?: "Lote não produziu duas linhas", model.state.value.data.optJSONArray("rows")?.length() == 2)
        compose.onNodeWithTag("batch-list").performScrollToNode(hasText("Exportar XLSX com resumo"))
        compose.onNodeWithText("Exportar XLSX com resumo").assertIsEnabled()
        capture("batch")
        val exported = JSONObject(Python.getInstance().getModule("mobile_engine").callAttr("call", "export", "{}").toString())
        assertTrue(exported.toString(), exported.getBoolean("ok"))
        ZipFile(File(exported.getString("value"))).use {
            assertNotNull(it.getEntry("xl/worksheets/sheet1.xml"))
            assertNotNull(it.getEntry("xl/worksheets/sheet2.xml"))
        }
    }
}
