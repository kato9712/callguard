package us.qply.callguard

import java.security.MessageDigest

/** Pure decision logic (no Android types) so it can be unit tested. */
object Rules {

    enum class Action { RING, SEND_TO_SCREENER }

    data class Decision(val action: Action, val reason: String)

    data class Input(
        val number: String,               // raw caller number, "" if hidden
        val isContact: Boolean,
        val allow: Set<String>,           // normalized numbers
        val block: Set<String>,
        val recentAttempts: Map<String, Long>, // normalized number -> last time we sent it to the screener (ms)
        val recentlyDialed: Boolean,      // you called this number in the last few days
        val now: Long,
        val enabled: Boolean,
        val repeatWindowMs: Long,
        val callerIdFailed: Boolean,      // carrier says the caller ID failed verification (spoofed)
        val ownNumber: String,            // your own number, for spotting neighbor spoofing
    )

    /** Same as the Tower side: last 10 digits for US numbers. */
    fun normalize(raw: String?): String {
        var d = (raw ?: "").filter { it.isDigit() }
        if (d.length == 11 && d.startsWith("1")) d = d.substring(1)
        return d
    }

    fun hash(raw: String): String {
        val bytes = MessageDigest.getInstance("SHA-256").digest(normalize(raw).toByteArray())
        return bytes.joinToString("") { "%02x".format(it) }
    }

    /** Same area code and exchange as you, but not you: the classic spoofing trick. */
    fun looksLikeNeighborSpoof(number: String, own: String): Boolean {
        val n = normalize(number)
        val o = normalize(own)
        return n.length == 10 && o.length == 10 && n != o && n.substring(0, 6) == o.substring(0, 6)
    }

    fun decide(i: Input): Decision {
        if (!i.enabled) return Decision(Action.RING, "Screening is off")
        val n = normalize(i.number)
        if (n.isEmpty()) return Decision(Action.SEND_TO_SCREENER, "No caller ID")
        if (n in i.block) return Decision(Action.SEND_TO_SCREENER, "Blocked")
        if (i.isContact) return Decision(Action.RING, "Contact")
        if (n in i.allow) return Decision(Action.RING, "Always allowed")
        if (i.recentlyDialed) return Decision(Action.RING, "You called them recently")
        val last = i.recentAttempts[n]
        if (last != null && i.now - last in 0..i.repeatWindowMs) {
            return Decision(Action.RING, "Called twice in a few minutes")
        }
        if (i.callerIdFailed) return Decision(Action.SEND_TO_SCREENER, "Caller ID failed carrier check")
        if (looksLikeNeighborSpoof(n, i.ownNumber)) return Decision(Action.SEND_TO_SCREENER, "Looks spoofed (matches your prefix)")
        return Decision(Action.SEND_TO_SCREENER, "Unknown number")
    }
}
