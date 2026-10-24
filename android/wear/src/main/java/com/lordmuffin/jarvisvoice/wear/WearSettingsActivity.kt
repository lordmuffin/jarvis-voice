package com.lordmuffin.jarvisvoice.wear

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import com.lordmuffin.jarvisvoice.wear.sync.SyncScheduler
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncState

/** Read-only status: the watch is signed in through the phone, so there is nothing to type here. */
class WearSettingsActivity : WearBaseActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_wear_settings)
        val graph = WearGraph.get(this)

        val server = graph.config.current()?.serverUrl
        findViewById<TextView>(R.id.tv_server).text = server ?: getString(R.string.wear_settings_not_paired)

        val items = graph.store.all()
        val waiting = items.count { it.state == SyncState.RECORDED || it.state == SyncState.AT_PHONE }
        val stuck = items.count { it.state == SyncState.BLOCKED || it.state == SyncState.QUARANTINED }
        findViewById<TextView>(R.id.tv_queue).text = getString(R.string.wear_settings_queue, waiting, stuck)

        findViewById<Button>(R.id.btn_sync).setOnClickListener {
            SyncScheduler.kick(this)
            finish()
        }
    }
}
