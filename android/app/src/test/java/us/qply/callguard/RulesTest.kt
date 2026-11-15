package us.qply.callguard

import org.junit.Assert.assertEquals
import org.junit.Test
import us.qply.callguard.Rules.Action.RING
import us.qply.callguard.Rules.Action.SEND_TO_SCREENER

class RulesTest {
    private val now = 1_000_000_000L
    private fun input(
        number: String = "+1 (405) 555-0100",
        contact: Boolean = false,
        allow: Set<String> = emptySet(),
        block: Set<String> = emptySet(),
        recent: Map<String, Long> = emptyMap(),
        dialed: Boolean = false,
        enabled: Boolean = true,
        failed: Boolean = false,
        own: String = "4058881234",
    ) = Rules.Input(number, contact, allow, block, recent, dialed, now, enabled, 5 * 60_000L, failed, own)

    @Test fun normalizes() {
        assertEquals("4055550100", Rules.normalize("+1 (405) 555-0100"))
        assertEquals("4055550100", Rules.normalize("405.555.0100"))
        assertEquals("4055550100", Rules.normalize("14055550100"))
        assertEquals("", Rules.normalize(null))
        assertEquals("44207946000", Rules.normalize("+44 20 7946 000"))
    }

    @Test fun hashMatchesTowerSide() {
        // Must equal the Tower side: python3 -c "import hashlib;print(hashlib.sha256(b'9405550160').hexdigest())"
        assertEquals("6564d99466968e608cc1cf21ce60ebe64ff2e7cea194010b60880733daf739f1", Rules.hash("+1 940-555-0160"))
        assertEquals(Rules.hash("9405550160"), Rules.hash("+1 (940) 555-0160"))
    }

    @Test fun unknownGoesToScreener() = assertEquals(SEND_TO_SCREENER, Rules.decide(input()).action)
    @Test fun contactRings() = assertEquals(RING, Rules.decide(input(contact = true)).action)
    @Test fun allowedRings() = assertEquals(RING, Rules.decide(input(allow = setOf("4055550100"))).action)
    @Test fun blockedBeatsContact() = assertEquals(SEND_TO_SCREENER, Rules.decide(input(contact = true, block = setOf("4055550100"))).action)
    @Test fun hiddenNumberScreened() = assertEquals(SEND_TO_SCREENER, Rules.decide(input(number = "")).action)
    @Test fun offRingsEverything() = assertEquals(RING, Rules.decide(input(enabled = false, block = setOf("4055550100"))).action)
    @Test fun recentlyDialedRings() = assertEquals(RING, Rules.decide(input(dialed = true)).action)

    @Test fun repeatCallerWithinWindowRings() {
        val d = Rules.decide(input(recent = mapOf("4055550100" to now - 2 * 60_000L)))
        assertEquals(RING, d.action)
    }

    @Test fun repeatCallerAfterWindowScreened() {
        val d = Rules.decide(input(recent = mapOf("4055550100" to now - 10 * 60_000L)))
        assertEquals(SEND_TO_SCREENER, d.action)
    }

    @Test fun neighborSpoofFlagged() {
        val d = Rules.decide(input(number = "4058889999"))
        assertEquals(SEND_TO_SCREENER, d.action)
        assertEquals("Looks spoofed (matches your prefix)", d.reason)
    }

    @Test fun failedCallerIdFlagged() {
        assertEquals("Caller ID failed carrier check", Rules.decide(input(failed = true)).reason)
    }
}
