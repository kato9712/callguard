package us.qply.callguard

import android.Manifest
import android.app.Activity
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity() {
    private lateinit var store: Store
    private lateinit var root: LinearLayout
    private val dp by lazy { resources.displayMetrics.density }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        Sync.schedule(this)
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun px(v: Int) = (v * dp).toInt()

    private fun render() {
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(18), px(18), px(18), px(32))
        }
        setContentView(ScrollView(this).apply { addView(root) })

        title("CallGuard")
        note("Unknown callers get sent to your Tower screener instead of ringing. Contacts, numbers you've allowed, " +
            "numbers you called recently, and anyone who calls twice in a few minutes ring through.")

        // ---- setup checklist
        header("Setup")
        val rm = getSystemService(RoleManager::class.java)
        val hasRole = rm.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)
        check("Call screening app", hasRole, "Set as call screener") {
            startActivityForResult(rm.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING), 1)
        }
        val contacts = granted(Manifest.permission.READ_CONTACTS)
        check("Contacts access (so contacts always ring)", contacts, "Allow") {
            requestPermissions(arrayOf(Manifest.permission.READ_CONTACTS), 2)
        }
        val callLog = granted(Manifest.permission.READ_CALL_LOG)
        check("Call log access (numbers you dialed ring) — optional", callLog, "Allow") {
            requestPermissions(arrayOf(Manifest.permission.READ_CALL_LOG), 3)
        }

        // ---- settings
        header("Settings")
        root.addView(Switch(this).apply {
            text = "Screening on"
            isChecked = store.enabled
            textSize = 16f
            setPadding(0, px(6), 0, px(6))
            setOnCheckedChangeListener { _, on -> store.enabled = on }
        })
        val url = field("Tower address", store.towerUrl, "http://100.x.x.x:8099", InputType.TYPE_TEXT_VARIATION_URI)
        val token = field("API token", store.token, "same as API_TOKEN on Tower", InputType.TYPE_TEXT_VARIATION_PASSWORD)
        val own = field("Your cell number (spots spoofed calls)", store.ownNumber, "4055550100", InputType.TYPE_CLASS_PHONE)
        val repeat = field("Let repeat callers ring within (minutes, 0 = off)", store.repeatMinutes.toString(), "5", InputType.TYPE_CLASS_NUMBER)
        root.addView(Switch(this).apply {
            text = "Ring numbers I called in the last 7 days"
            isChecked = store.allowRecentlyDialed
            setOnCheckedChangeListener { _, on -> store.allowRecentlyDialed = on }
        })

        val status = note(syncLine())
        button("Save & sync") {
            store.towerUrl = url.text.toString()
            store.token = token.text.toString()
            store.ownNumber = own.text.toString()
            store.repeatMinutes = repeat.text.toString().toIntOrNull() ?: 5
            status.text = "Syncing…"
            Thread {
                val r = Sync.run(this)
                runOnUiThread { status.text = syncLine(); Toast.makeText(this, r, Toast.LENGTH_LONG).show() }
            }.start()
        }

        // ---- carrier forwarding
        header("Forward to the screener")
        note("Rejected calls only reach Tower if your carrier forwards busy/unanswered calls to your Callcentric number. " +
            "Enter that number, then tap your carrier type. Your phone app opens with the code filled in — press call.")
        val fwd = field("Callcentric number", getPref("fwd"), "6465550123", InputType.TYPE_CLASS_PHONE)
        row(
            "T-Mobile / AT&T / most others" to { dial("**004*1${Rules.normalize(fwd.text.toString()).also { setPref("fwd", it) }}#") },
            "Verizon" to { dial("*71${Rules.normalize(fwd.text.toString()).also { setPref("fwd", it) }}") },
        )
        row(
            "Turn off (T-Mobile/AT&T)" to { dial("##004#") },
            "Turn off (Verizon)" to { dial("*73") },
        )

        // ---- activity
        header("Recent calls")
        val fmt = SimpleDateFormat("MMM d, h:mm a", Locale.US)
        val log = store.log()
        if (log.isEmpty()) note("Nothing yet.")
        log.take(30).forEach {
            val n = it.optString("n").ifBlank { "No caller ID" }
            val what = if (it.optBoolean("rang")) "rang" else "→ screener"
            note("${fmt.format(Date(it.optLong("t")))}  $n  $what  · ${it.optString("why")}")
        }
    }

    private fun syncLine(): String {
        val t = store.lastSync
        val whenTxt = if (t == 0L) "" else " (" + SimpleDateFormat("MMM d, h:mm a", Locale.US).format(Date(t)) + ")"
        return store.lastSyncResult + whenTxt
    }

    private fun dial(code: String) {
        startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(code))))
    }

    private fun granted(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
    private fun getPref(k: String) = getSharedPreferences("callguard", MODE_PRIVATE).getString(k, "") ?: ""
    private fun setPref(k: String, v: String) = getSharedPreferences("callguard", MODE_PRIVATE).edit().putString(k, v).apply()

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        render()
    }

    // ---- tiny view helpers
    private fun title(t: String) = root.addView(TextView(this).apply {
        text = t; textSize = 26f; setTypeface(typeface, Typeface.BOLD); setPadding(0, 0, 0, px(6))
    })

    private fun header(t: String) = root.addView(TextView(this).apply {
        text = t; textSize = 18f; setTypeface(typeface, Typeface.BOLD); setPadding(0, px(22), 0, px(6))
    })

    private fun note(t: String): TextView = TextView(this).apply {
        text = t; textSize = 14f; setPadding(0, px(3), 0, px(3))
    }.also { root.addView(it) }

    private fun field(label: String, value: String, hint: String, type: Int): EditText {
        note(label)
        return EditText(this).apply {
            setText(value); this.hint = hint; isSingleLine = true
            inputType = if (type == InputType.TYPE_TEXT_VARIATION_PASSWORD || type == InputType.TYPE_TEXT_VARIATION_URI)
                InputType.TYPE_CLASS_TEXT or type else type
        }.also { root.addView(it) }
    }

    private fun button(label: String, onClick: () -> Unit) = root.addView(Button(this).apply {
        text = label; setOnClickListener { onClick() }
    })

    private fun row(vararg buttons: Pair<String, () -> Unit>) {
        val r = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        buttons.forEach { (label, action) ->
            r.addView(Button(this).apply {
                text = label; textSize = 12f; setOnClickListener { action() }
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        }
        root.addView(r)
    }

    private fun check(label: String, ok: Boolean, action: String, onFix: () -> Unit) {
        val r = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        r.addView(TextView(this).apply { text = (if (ok) "✅  " else "❌  ") + label; textSize = 15f },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        r.addView(Button(this).apply {
            text = action; visibility = if (ok) View.GONE else View.VISIBLE; setOnClickListener { onFix() }
        })
        root.addView(r)
    }
}
