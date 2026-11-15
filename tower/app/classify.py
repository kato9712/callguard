"""Label what the caller said. This only adds a hint to the Telegram alert - robocalls are
already stopped by the 'press N' challenge before this runs."""
import json
import logging
import re
import urllib.request

from . import config

log = logging.getLogger("classify")

# Phrases that show up in telemarketing / scam calls. Matched on word boundaries, case-insensitive.
SPAM_PATTERNS = [
    r"(extended|vehicle|car|auto) warranty", r"warranty (department|expir\w*)",
    r"final (notice|attempt|reminder)", r"last (notice|attempt)",
    r"student loans?", r"loan forgiveness", r"debt (relief|consolidation)",
    r"lower (your )?(interest|rate|payments?)", r"interest rate", r"credit card (debt|balance|offer)",
    r"pre-?approved", r"business (funding|loan|line of credit)", r"merchant (services|cash advance)",
    r"medicare", r"health (insurance|plan) (options|quote)", r"open enrollment", r"\bACA\b",
    r"social security (number|administration|benefits)", r"\bIRS\b", r"tax (relief|debt)",
    r"arrest warrant", r"legal action", r"suspended", r"suspicious (activity|charge|order)",
    r"amazon (order|account|purchase)", r"apple (id|icloud)", r"refund department",
    r"google (listing|business|my business)", r"(your|the) website", r"\bSEO\b",
    r"solar", r"energy (bill|savings|provider)", r"home (security|warranty)", r"roof inspection",
    r"cash offer|buy your (house|home|property)", r"(selling|sell) your (home|house|property)",
    r"(won|winner|congratulations|you've been selected|selected for)", r"free (cruise|vacation|gift)",
    r"(press|hit) (one|1|two|2|nine|9)\b", r"this is not a sales call", r"courtesy call",
    r"limited time", r"qualif(y|ied) for", r"special (offer|promotion)", r"\bsurvey\b",
    r"political|campaign|donat(e|ion)", r"timeshare", r"life insurance", r"final expense",
    r"verify your (account|identity|information)", r"gift cards?",
]
_SPAM_RE = [re.compile(r"\b" + p, re.I) for p in SPAM_PATTERNS]

# Phrases that suggest a real, expected caller.
LEGIT_PATTERNS = [
    r"(doctor|dentist|clinic|pharmacy|hospital|office of dr)", r"appointment", r"prescription",
    r"(school|teacher|principal|daycare|coach)", r"deliver(y|ing)|package|driver|courier|fedex|ups\b",
    r"(returning|return) your call", r"you called", r"calling (you )?back", r"call(ed)? me",
    r"(your|the) (order|repair|estimate|quote|truck|car) (is )?ready", r"(pick ?up|picked up)",
    r"interview", r"(lab|isotek)", r"\bsample", r"reference", r"(neighbor|landlord|plumber|electrician)",
    r"(it's|it is|this is) (your )?(mom|dad|brother|sister|son|daughter|wife|husband|friend|cousin)",
    r"emergency", r"urgent", r"(bank|credit union) fraud (department|alert)",
]
_LEGIT_RE = [re.compile(r"\b" + p, re.I) for p in LEGIT_PATTERNS]


def rules(transcript):
    """Returns (verdict, reason). verdict: 'spam' | 'ok' | 'unclear' | 'silent'."""
    t = (transcript or "").strip()
    if not t:
        return "unclear", "no words caught"
    spam = [r.pattern for r in _SPAM_RE if r.search(t)]
    legit = [r.pattern for r in _LEGIT_RE if r.search(t)]
    if spam and len(spam) >= len(legit):
        return "spam", "sounds like a sales/scam pitch"
    if legit:
        return "ok", "sounds like a real reason"
    if len(t.split()) <= 2:
        return "unclear", "very short answer"
    return "unclear", "no clear signs either way"


def _llm(transcript):
    prompt = (
        "A caller reached an AI call screener and was asked for their name and reason for calling. "
        "Classify what they said. Reply with JSON only: "
        '{"verdict": "spam" | "ok" | "unclear", "summary": "<10 words max>"}. '
        "spam = telemarketing, sales, scams, surveys, political. ok = a specific personal or business reason. "
        f"Caller said: \"{transcript}\""
    )
    body = json.dumps({
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0, "max_tokens": 60,
    }).encode()
    req = urllib.request.Request(config.LLM_URL.rstrip("/") + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    data = json.loads(m.group(0))
    v = data.get("verdict", "unclear")
    if v not in ("spam", "ok", "unclear"):
        v = "unclear"
    return v, (data.get("summary") or "").strip()[:80] or "AI check"


def classify(transcript):
    if config.LLM_URL and (transcript or "").strip():
        try:
            return _llm(transcript)
        except Exception as e:
            log.info("LLM not reachable (%s); using rules", e)
    return rules(transcript)
