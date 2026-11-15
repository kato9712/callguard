package us.qply.callguard

import android.Manifest
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Talks to the Tower screener: pulls allow/block lists, pushes hashed contact numbers. */
object Sync {

    /** Returns a human-readable result. Safe to call off the main thread only. */
    fun run(ctx: Context): String {
        val store = Store(ctx)
        if (store.towerUrl.isBlank() || store.token.isBlank()) return "Set the Tower address and token first".also { store.lastSyncResult = it }
        return try {
            val lists = JSONObject(request(store, "GET", "/api/lists", null))
            store.allow = lists.getJSONArray("allow").toSet()
            store.block = lists.getJSONArray("block").toSet()
            val hashes = contactHashes(ctx)
            var msg = "Synced: ${store.allow.size} allowed, ${store.block.size} blocked"
            if (hashes != null) {
                val body = JSONObject().put("hashes", JSONArray(hashes.toList())).toString()
                val r = JSONObject(request(store, "PUT", "/api/known", body))
                msg += ", ${r.getInt("count")} contact numbers"
            }
            store.lastSync = System.currentTimeMillis()
            msg
        } catch (e: Exception) {
            "Sync failed: ${e.message ?: e.javaClass.simpleName}"
        }.also { store.lastSyncResult = it }
    }

    private fun JSONArray.toSet(): Set<String> = (0 until length()).map { Rules.normalize(getString(it)) }.filter { it.isNotEmpty() }.toSet()

    private fun request(store: Store, method: String, path: String, body: String?): String {
        val conn = URL(store.towerUrl + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 8000
        conn.readTimeout = 15000
        conn.setRequestProperty("Authorization", "Bearer ${store.token}")
        if (body != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(body.toByteArray()) }
        }
        val code = conn.responseCode
        if (code == 401) throw RuntimeException("wrong token")
        if (code !in 200..299) throw RuntimeException("HTTP $code")
        return conn.inputStream.bufferedReader().use { it.readText() }
    }

    /** SHA-256 of each contact's normalized number. null if we can't read contacts. */
    private fun contactHashes(ctx: Context): Set<String>? {
        if (ctx.checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) return null
        val out = HashSet<String>()
        ctx.contentResolver.query(
            ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(ContactsContract.CommonDataKinds.Phone.NUMBER), null, null, null
        )?.use { c -> while (c.moveToNext()) {
            val n = c.getString(0) ?: continue
            if (Rules.normalize(n).length >= 7) out.add(Rules.hash(n))
        } }
        return out
    }

    fun schedule(ctx: Context) {
        val js = ctx.getSystemService(JobScheduler::class.java)
        val job = JobInfo.Builder(JOB_ID, ComponentName(ctx, SyncJob::class.java))
            .setPeriodic(3 * 3600_000L)
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPersisted(true)
            .build()
        js.schedule(job)
    }

    private const val JOB_ID = 4242
}

class SyncJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        Thread {
            Sync.run(this)
            jobFinished(params, false)
        }.start()
        return true
    }

    override fun onStopJob(params: JobParameters) = true
}
