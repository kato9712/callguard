"""Rules classifier against realistic caller intros (the kind of thing Whisper returns)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", "/tmp/cg-unit")
from app.classify import rules  # noqa: E402

SPAM = [
    "Hi, I'm calling about your vehicle's extended warranty.",
    "Hello, this is Jessica with the warranty department regarding your auto.",
    "This is your final notice about your car's factory warranty.",
    "Hi, we're calling about your student loans, you may qualify for forgiveness.",
    "Hello, I'm calling from Medicare about your benefits.",
    "This is a courtesy call regarding your credit card interest rate.",
    "We can help lower your interest rate on your credit card debt.",
    "Hi, this is Mark, I'm calling about your Google business listing.",
    "Hi, I'm with Sunrun, we're offering free solar for homeowners in your area.",
    "We'd like to make you a cash offer on your property.",
    "Hi, are you interested in selling your home?",
    "This is the Social Security Administration, your number has been suspended.",
    "This is Amazon, we detected a suspicious order on your account.",
    "Congratulations, you've been selected for a free cruise.",
    "Hi, this is not a sales call, we're doing a quick survey.",
    "I'm calling on behalf of the campaign to ask for your support.",
    "Hi, I'm calling about business funding, you've been pre-approved.",
    "Hi, this is Dave, I'm calling about your website and SEO.",
    "Hello, this is regarding your health insurance plan options for open enrollment.",
    "Hi, I'm calling about final expense life insurance.",
]
LEGIT = [
    "Hi, this is Mike from Discount Tire, your truck is ready for pick up.",
    "Hey, this is Sarah from Dr. Patel's office about your appointment tomorrow.",
    "This is the pharmacy, your prescription is ready.",
    "Hi, I'm the FedEx driver, I have a package for you and I'm at the gate.",
    "Hey Ly, it's Tony, returning your call about the boat.",
    "This is Jason from the school, calling about your son.",
    "Hi, this is Mark with the dealership, the estimate on your car is ready.",
    "Hi, I'm calling about the interview on Monday.",
    "This is Chris from Isotek, I have a question about a sample.",
    "Hey, it's your neighbor, your garage door is open.",
    "Hi, this is the plumber, I'm running about ten minutes late.",
    "Hey, it's your brother, I got a new number, call me back.",
]
UNCLEAR = [
    "",
    "Hello?",
    "Hi, I need to talk to the owner.",
    "Yeah, is Ly there?",
]


def test_all():
    bad = []
    for t in SPAM:
        v, _ = rules(t)
        if v != "spam":
            bad.append(("expected spam", v, t))
    for t in LEGIT:
        v, _ = rules(t)
        if v != "ok":
            bad.append(("expected ok", v, t))
    for t in UNCLEAR:
        v, _ = rules(t)
        if v != "unclear":
            bad.append(("expected unclear", v, t))
    for b in bad:
        print(*b, sep=" | ")
    total = len(SPAM) + len(LEGIT) + len(UNCLEAR)
    print(f"{total - len(bad)}/{total} labeled as expected")
    assert not bad


if __name__ == "__main__":
    test_all()
