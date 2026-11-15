package us.qply.callguard

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

/** Settings + cached lists + a short activity log, all in SharedPreferences. */
class Store(ctx: Context) {
    private val p: SharedPreferences = ctx.getSharedPreferences("callguard", Context.MODE_PRIVATE)

    var enabled: Boolean
        get() = p.getBoolean("enabled", true)
        set(v) = p.edit().putBoolean("enabled", v).apply()

    var towerUrl: String
        get() = p.getString("towerUrl", "") ?: ""
        set(v) = p.edit().putString("towerUrl", v.trim().trimEnd('/')).apply()

    var token: String
        get() = p.getString("token", "") ?: ""
        set(v) = p.edit().putString("token", v.trim()).apply()

    var ownNumber: String
        get() = p.getString("ownNumber", "") ?: ""
        set(v) = p.edit().putString("ownNumber", Rules.normalize(v)).apply()

    var allowRecentlyDialed: Boolean
        get() = p.getBoolean("allowDialed", true)
        set(v) = p.edit().putBoolean("allowDialed", v).apply()

    var repeatMinutes: Int
        get() = p.getInt("repeatMinutes", 5)
        set(v) = p.edit().putInt("repeatMinutes", v.coerceIn(0, 60)).apply()

    var allow: Set<String>
        get() = p.getStringSet("allow", emptySet())!!.toSet()
        set(v) = p.edit().putStringSet("allow", v).apply()

    var block: Set<String>
        get() = p.getStringSet("block", emptySet())!!.toSet()
        set(v) = p.edit().putStringSet("block", v).apply()

    var lastSync: Long
        get() = p.getLong("lastSync", 0)
        set(v) = p.edit().putLong("lastSync", v).apply()

    var lastSyncResult: String
        get() = p.getString("lastSyncResult", "Never synced") ?: ""
        set(v) = p.edit().putString("lastSyncResult", v).apply()

    // ---- numbers recently sent to the screener (for the "called twice" rule)
    fun recentAttempts(): Map<String, Long> {
        val o = JSONObject(p.getString("recent", "{}") ?: "{}")
        return o.keys().asSequence().associateWith { o.getLong(it) }
    }

    @Synchronized
    fun noteAttempt(number: String, now: Long) {
        val n = Rules.normalize(number)
        if (n.isEmpty()) return
        val o = JSONObject(p.getString("recent", "{}") ?: "{}")
        val cutoff = now - 60 * 60_000L
        val keep = JSONObject()
        o.keys().forEach { k -> if (o.getLong(k) > cutoff) keep.put(k, o.getLong(k)) }
        keep.put(n, now)
        p.edit().putString("recent", keep.toString()).apply()
    }

    // ---- activity log shown in the app (last 50)
    fun log(): List<JSONObject> {
        val a = JSONArray(p.getString("log", "[]") ?: "[]")
        return (0 until a.length()).map { a.getJSONObject(it) }
    }

    @Synchronized
    fun addLog(number: String, rang: Boolean, reason: String, now: Long) {
        val a = JSONArray(p.getString("log", "[]") ?: "[]")
        val out = JSONArray()
        out.put(JSONObject().put("n", number).put("rang", rang).put("why", reason).put("t", now))
        for (i in 0 until minOf(a.length(), 49)) out.put(a.get(i))
        p.edit().putString("log", out.toString()).apply()
    }
}
