package br.com.geojardins.mobile

import android.graphics.BitmapFactory
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.roundToInt
import java.io.File

private data class MapAssets(val image: ImageBitmap, val overlays: JSONObject)

@Composable fun MapScreen(fiscais: JSONObject, mapDir: String) {
    val context = LocalContext.current
    var assets by remember { mutableStateOf<MapAssets?>(null) }
    var error by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf("Todas") }
    var zoom by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    LaunchedEffect(Unit) {
        try {
            assets = withContext(Dispatchers.IO) {
                val bitmap = File(mapDir, "operational_map.png").inputStream().use { BitmapFactory.decodeStream(it) }.asImageBitmap()
                val overlays = File(mapDir, "operational_map_overlays.json").bufferedReader().use { JSONObject(it.readText()) }
                MapAssets(bitmap, overlays)
            }
        } catch (_: Exception) { error = true }
    }
    Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Mapa operacional", style = MaterialTheme.typography.titleLarge)
        Choice("Área", selected, listOf("Todas") + fiscais.keys().asSequence().sorted().toList()) { selected = it }
        if (selected != "Todas") Text("Fiscal: ${fiscais.text(selected).ifBlank { "NÃO CONFIGURADO" }}")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { zoom = (zoom / 1.5f).coerceAtLeast(1f) }) { Text("− Zoom") }
            OutlinedButton(onClick = { zoom = (zoom * 1.5f).coerceAtMost(12f) }) { Text("+ Zoom") }
            TextButton(onClick = { zoom = 1f; offset = Offset.Zero }) { Text("Ajustar") }
        }
        if (error) Text("Não foi possível abrir o mapa local. Reinstale uma versão com os assets completos.")
        val map = assets
        if (map == null && !error) CircularProgressIndicator()
        if (map != null) {
            val transform = rememberTransformableState { factor, pan, _ ->
                zoom = (zoom * factor).coerceIn(1f, 12f)
                offset += pan
            }
            Canvas(Modifier.fillMaxWidth().weight(1f).clipToBounds().transformable(transform).testTag("offline-map")) {
                val scale = minOf(size.width / map.image.width, size.height / map.image.height)
                val width = (map.image.width * scale).roundToInt()
                val height = (map.image.height * scale).roundToInt()
                val left = (size.width - width) / 2
                val top = (size.height - height) / 2
                // Keep some of the map reachable, even after large pan gestures.
                val panX = offset.x.coerceIn(-size.width * zoom / 2, size.width * zoom / 2)
                val panY = offset.y.coerceIn(-size.height * zoom / 2, size.height * zoom / 2)
                withTransform({ translate(panX, panY); scale(zoom, zoom, center) }) {
                    drawImage(map.image, dstOffset = IntOffset(left.roundToInt(), top.roundToInt()), dstSize = IntSize(width, height))
                    val bounds = map.overlays.getJSONArray("page_bounds")
                    val sx = width / bounds.getDouble(2).toFloat()
                    val sy = height / bounds.getDouble(3).toFloat()
                    map.overlays.getJSONArray("features").objects().filter { it.text("area") == selected }.forEach { feature ->
                        val geometry = feature.getJSONObject("geometry")
                        val coords = geometry.getJSONArray("coordinates")
                        val polygons = if (geometry.getString("type") == "Polygon") listOf(coords) else (0 until coords.length()).map { coords.getJSONArray(it) }
                        for (polygon in polygons) {
                            val path = Path().apply { fillType = PathFillType.EvenOdd }
                            for (r in 0 until polygon.length()) {
                                val ring: JSONArray = polygon.getJSONArray(r)
                                for (i in 0 until ring.length()) {
                                    val point = ring.getJSONArray(i)
                                    val x = left + point.getDouble(0).toFloat() * sx
                                    val y = top + point.getDouble(1).toFloat() * sy
                                    if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
                                }
                                path.close()
                            }
                            drawPath(path, Color(0x66356E48))
                            drawPath(path, Color(0xFF17372C), style = Stroke(2f / zoom))
                        }
                    }
                }
            }
        }
        Text("Use dois dedos para ampliar e mover.", style = MaterialTheme.typography.bodySmall)
    }
}
