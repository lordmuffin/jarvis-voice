package com.lordmuffin.jarvisvoice.wear.tile

import android.content.ComponentName
import android.content.Context
import androidx.core.content.ContextCompat
import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders.argb
import androidx.wear.protolayout.DimensionBuilders.dp
import androidx.wear.protolayout.DimensionBuilders.expand
import androidx.wear.protolayout.DimensionBuilders.sp
import androidx.wear.protolayout.LayoutElementBuilders
import androidx.wear.protolayout.ModifiersBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TypeBuilders.StringProp
import androidx.wear.protolayout.TimelineBuilders.Timeline
import androidx.wear.protolayout.expression.DynamicBuilders
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders
import androidx.wear.tiles.TileService
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.lordmuffin.jarvisvoice.wear.R
import com.lordmuffin.jarvisvoice.wear.RecorderState
import com.lordmuffin.jarvisvoice.wear.ToggleActivity
import java.time.Instant

/**
 * One big round button. Tap to start, tap again to stop. The elapsed time is a dynamic expression
 * (platform clock minus the start instant), so it ticks on its own without the tile being rebuilt.
 */
class RecordTileService : TileService() {

    override fun onTileRequest(requestParams: RequestBuilders.TileRequest): ListenableFuture<TileBuilders.Tile> {
        val tile = TileBuilders.Tile.Builder()
            .setResourcesVersion(RESOURCES_VERSION)
            .setTileTimeline(Timeline.fromLayoutElement(layout(this, RecorderState.active.value)))
            .build()
        return Futures.immediateFuture(tile)
    }

    override fun onTileResourcesRequest(requestParams: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> =
        Futures.immediateFuture(ResourceBuilders.Resources.Builder().setVersion(RESOURCES_VERSION).build())

    companion object {
        private const val RESOURCES_VERSION = "1"

        fun requestUpdate(ctx: Context) = getUpdater(ctx).requestUpdate(RecordTileService::class.java)

        internal fun layout(ctx: Context, active: RecorderState.Active?): LayoutElementBuilders.LayoutElement {
            fun color(id: Int) = argb(ContextCompat.getColor(ctx, id))
            fun spOf(id: Int) = sp(ctx.resources.getDimension(id) / ctx.resources.displayMetrics.density / ctx.resources.configuration.fontScale)
            val recording = active != null

            val label = LayoutElementBuilders.Text.Builder()
                .setText(ctx.getString(if (recording) R.string.tile_stop else R.string.tile_record))
                .setFontStyle(
                    LayoutElementBuilders.FontStyle.Builder()
                        .setSize(spOf(R.dimen.inst_type_title))
                        .setWeight(LayoutElementBuilders.FONT_WEIGHT_BOLD)
                        .setColor(color(if (recording) R.color.inst_on_error else R.color.inst_on_accent))
                        .build(),
                ).build()

            val column = LayoutElementBuilders.Column.Builder().addContent(label)
            if (active != null) {
                val start = DynamicBuilders.DynamicInstant.withSecondsPrecision(Instant.ofEpochMilli(active.startedAtWallMs))
                val elapsed = start.durationUntil(DynamicBuilders.DynamicInstant.platformTimeWithSecondsPrecision())
                val minutes = elapsed.toIntMinutes()
                val seconds = elapsed.toIntSeconds().minus(minutes.times(60))
                val text = minutes.format().concat(DynamicBuilders.DynamicString.constant(":"))
                    .concat(seconds.format(DynamicBuilders.DynamicInt32.IntFormatter.Builder().setMinIntegerDigits(2).build()))
                column.addContent(
                    LayoutElementBuilders.Text.Builder()
                        .setText(StringProp.Builder("0:00").setDynamicValue(text).build())
                        .setFontStyle(
                            LayoutElementBuilders.FontStyle.Builder()
                                .setSize(spOf(R.dimen.inst_type_stat))
                                .setColor(color(R.color.inst_on_error))
                                .build(),
                        ).build(),
                )
            }

            val toggle = ModifiersBuilders.Clickable.Builder()
                .setId("toggle")
                .setOnClick(
                    ActionBuilders.LaunchAction.Builder()
                        .setAndroidActivity(
                            ActionBuilders.AndroidActivity.Builder()
                                .setPackageName(ctx.packageName)
                                .setClassName(ComponentName(ctx, ToggleActivity::class.java).className)
                                .build(),
                        ).build(),
                ).build()

            val button = LayoutElementBuilders.Box.Builder()
                .setWidth(dp(ctx.resources.getDimension(R.dimen.wear_tile_button) / ctx.resources.displayMetrics.density))
                .setHeight(dp(ctx.resources.getDimension(R.dimen.wear_tile_button) / ctx.resources.displayMetrics.density))
                .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
                .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
                .setModifiers(
                    ModifiersBuilders.Modifiers.Builder()
                        .setClickable(toggle)
                        .setBackground(
                            ModifiersBuilders.Background.Builder()
                                .setColor(color(if (recording) R.color.inst_error else R.color.inst_accent))
                                .setCorner(ModifiersBuilders.Corner.Builder().setRadius(dp(ctx.resources.getDimension(R.dimen.wear_tile_button) / 2 / ctx.resources.displayMetrics.density)).build())
                                .build(),
                        ).build(),
                )
                .addContent(column.build())
                .build()

            return LayoutElementBuilders.Box.Builder()
                .setWidth(expand()).setHeight(expand())
                .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
                .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
                .addContent(button)
                .build()
        }
    }
}
