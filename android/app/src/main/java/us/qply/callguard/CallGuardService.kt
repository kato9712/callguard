package us.qply.callguard

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.CallLog
import android.provider.ContactsContract
import android.telecom.Call
import android.telecom.CallScreeningService
import android.telecom.Connection
import android.util.Log

/**
 * Android asks this service about every incoming call from a number that isn't in your
 * contacts, before the phone rings. We either let it ring, or reject it. A rejected call
 * counts as "busy", so your carrier's forward-when-busy sends it to the Tower screener.
 */
class CallGuardService : CallScreeningService() {

    override fun onScreenCall(details: Call.Details) {
        if (details.callDirection != Call.Details.DIRECTION_INCOMING) {
            respondToCall(details, CallResponse.Builder().build())
            return
        }
        val store = Store(this)
        val number = details.handle?.schemeSpecificPart ?: ""
        val now = System.currentTimeMillis()
        val decision = try {
            Rules.decide(
                Rules.Input(
                    number = number,
                    isContact = isContact(number),
                    allow = store.allow,
                    block = store.block,
                    recentAttempts = store.recentAttempts(),
                    recentlyDialed = store.allowRecentlyDialed && dialedRecently(number),
                    now = now,
                    enabled = store.enabled,
                    repeatWindowMs = store.repeatMinutes * 60_000L,
                    callerIdFailed = callerIdFailed(details),
                    ownNumber = store.ownNumber,
                )
            )
        } catch (e: Exception) {
            Log.e(TAG, "Rule check failed, letting call ring", e)
            Rules.Decision(Rules.Action.RING, "Error: ${e.javaClass.simpleName}")
        }

        val response = if (decision.action == Rules.Action.RING) {
            CallResponse.Builder().setDisallowCall(false).build()
        } else {
            store.noteAttempt(number, now)
            CallResponse.Builder()
                .setDisallowCall(true)
                .setRejectCall(true)          // "busy" -> carrier forwards it to the screener
                .setSkipCallLog(false)
                .setSkipNotification(true)
                .build()
        }
        respondToCall(details, response)
        store.addLog(number, decision.action == Rules.Action.RING, decision.reason, now)
        Log.i(TAG, "$number -> ${decision.action} (${decision.reason})")
    }

    private fun isContact(number: String): Boolean {
        if (number.isBlank() || checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) return false
        val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
        return contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup._ID), null, null, null)
            ?.use { it.moveToFirst() } ?: false
    }

    private fun dialedRecently(number: String): Boolean {
        val n = Rules.normalize(number)
        if (n.isEmpty() || checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) return false
        val since = System.currentTimeMillis() - 7 * 24 * 3600_000L
        contentResolver.query(
            CallLog.Calls.CONTENT_URI, arrayOf(CallLog.Calls.NUMBER),
            "${CallLog.Calls.TYPE} = ? AND ${CallLog.Calls.DATE} > ?",
            arrayOf(CallLog.Calls.OUTGOING_TYPE.toString(), since.toString()),
            "${CallLog.Calls.DATE} DESC"
        )?.use { c ->
            while (c.moveToNext()) if (Rules.normalize(c.getString(0)) == n) return true
        }
        return false
    }

    private fun callerIdFailed(details: Call.Details): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
            details.callerNumberVerificationStatus == Connection.VERIFICATION_STATUS_FAILED

    companion object { const val TAG = "CallGuard" }
}
