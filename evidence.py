"""What Napier may keep and send when it wants to prove Iowa Courts was down.

An alert says something failed. It is not evidence. A staffer reporting a bad
morning to Iowa Courts has Napier's word for it and nothing else, and Napier's
word is exactly the thing in question. The page ICOS serves when it cannot
reach its own data source says so in Iowa's own wording, under Iowa's own
heading, over Iowa's own footer. That is worth keeping and worth sending.

Keeping it is only safe because of what that page is. It carries the problem
report text, an empty disposition table, and the heading of whichever case the
session had selected last. In a clinic that heading is the client's case
caption, which is the privileged part under Article 5, so it does not survive
this module. Nor does the account: ICOS stamps the signed-in distinguished name
into the corner of every page it serves, and Article 1.2 keeps credentials out
of anything Napier transmits.

What survives is what actually proves an outage. Iowa's own wording. The case
number that was asked for, and the different case number the page came back
wearing, both of which are court public record and neither of which is a
person. The empty disposition table, which is why every parser accepted the
page. None of that identifies anybody, and none of the identifying parts prove
anything about Iowa's outage, so scrubbing costs nothing evidential.

Nothing else is ever packaged. A real case page names a defendant and a search
results page lists every person who matched along with their dates of birth.
The marker check below is not a classification convenience. It is the reason
this module is allowed to exist at all: only a page positively identified as
ICOS's own outage page has a shape known well enough to scrub with confidence,
and a scrubber applied to a page whose shape is unknown is a guess about
whether it worked.
"""

import hashlib
import re

# ICOS answers with this when its web tier cannot reach its data source. It
# lives here rather than in icos.py because recognising this page and being
# allowed to send it are the same question, and two copies of the string would
# be two things to keep in step.
PROBLEM_REPORT_MARKER = "There was a communication problem"

# Article 5. The caption is the parties, and on a clinic run the stale heading
# this page wears is the client's own case: "STATE VS <client>".
CAPTION = re.compile(rb"(Title:(?:&nbsp;|\s)*)([^<]*)", re.I)
# Belt and braces for a caption that reaches the page some other way. A problem
# report page has exactly one "X VS Y" on it and it is the one above, so this
# costs nothing here and catches a page shape that changes under us.
VERSUS = re.compile(rb"[A-Z][A-Z0-9 .,'&/-]{2,}\s+VS\.?\s+[A-Z][A-Z0-9 .,'&/-]{2,}")
# Article 1.2. "CN=ila04,O=JUDICIAL", bottom right of every authenticated page.
ACCOUNT_STAMP = re.compile(rb"CN=[^<\s]*", re.I)
# Nothing on this page type should carry a date at all. If one appears, the
# page is not the shape this module was written against, and a date next to a
# name is the combination Article 5 exists for.
DATE = re.compile(rb"\b\d{1,2}/\d{1,2}/\d{2,4}\b")

WITHHELD = b"[caption withheld under Article 5]"
ACCOUNT_WITHHELD = b"CN=[account withheld under Article 1.2]"
DATE_WITHHELD = b"[date withheld]"

# The second look, and deliberately not the patterns above. Checking the output
# with the same regex that produced it asks whether a regex agrees with itself,
# which it always does: the template change that stops CAPTION matching stops
# the check noticing, and nothing says so. So these are written from the other
# end. They describe what a clean page looks like and fire on anything else,
# and each one keys on a different part of the text than the scrubber does.
#
# They are allowed to be blunter than the scrubbers, because a false alarm here
# costs one unsent email and a missed one costs a client's name.
LEAKED_VERSUS = re.compile(rb"\bVS\.?\b", re.I)
LEAKED_TITLE = re.compile(rb"Title:((?:&nbsp;|\s)*)([^<]*)", re.I)
LEAKED_ACCOUNT = re.compile(rb"O=JUDICIAL|CN=(?!\[account)", re.I)
LEAKED_DATE = re.compile(rb"\b\d{1,4}\s*[/-]\s*\d{1,2}\s*[/-]\s*\d{1,4}\b")

# A problem report page is about 3.4 KB. Anything an order of magnitude past
# that is not the page this module knows how to handle, whatever it says.
MAX_BYTES = 64 * 1024


def _bytes(body):
    return body if isinstance(body, bytes) else body.encode("utf-8", "ignore")


def is_outage_page(body):
    """Whether ICOS said, in its own words, that it could not reach its data."""
    if not body:
        return False
    raw = _bytes(body)
    return len(raw) <= MAX_BYTES and PROBLEM_REPORT_MARKER.encode() in raw


def scrub(body):
    """Strip the parties, the account and any date. Keep the rest verbatim."""
    out = _bytes(body)
    out = CAPTION.sub(lambda m: m.group(1) + WITHHELD, out)
    out = VERSUS.sub(WITHHELD, out)
    out = ACCOUNT_STAMP.sub(ACCOUNT_WITHHELD, out)
    out = DATE.sub(DATE_WITHHELD, out)
    return out


def leaks(scrubbed):
    """Anything the scrub was supposed to remove and did not.

    Checked rather than assumed, because the cost of a regex that stopped
    matching after ICOS changed a template is a privileged name in Alex's inbox
    and then in a complaint to the court, and nothing would say so. package()
    refuses to hand back a document that fails this.
    """
    raw = _bytes(scrubbed)
    found = []
    if LEAKED_ACCOUNT.search(raw):
        found.append("account stamp")
    titled = [m.group(2).strip() for m in LEAKED_TITLE.finditer(raw)]
    if (LEAKED_VERSUS.search(raw)
            or any(t and not t.startswith(WITHHELD) for t in titled)):
        found.append("case caption")
    if LEAKED_DATE.search(raw):
        found.append("date")
    return found


def fingerprint(body):
    """sha256 of the page as ICOS served it.

    The original never leaves the machine, so this is what ties the redacted
    copy in an email to the bytes it was made from, if anyone ever has to ask.
    """
    return hashlib.sha256(_bytes(body)).hexdigest()


def _slug(case_id):
    return re.sub(r"[^A-Za-z0-9]+", "-", (case_id or "unknown")).strip("-")


def package(body, case_id=None, stamp=None):
    """A sendable copy of an ICOS outage page, or None if it is not one.

    stamp is a filename-safe timestamp supplied by the caller, because this
    module should not be the thing deciding what time it is.
    """
    if not is_outage_page(body):
        return None
    scrubbed = scrub(body)
    if leaks(scrubbed):
        return None
    return {
        "filename": "iowa-courts-outage-%s-%s.html" % (stamp or "unstamped",
                                                       _slug(case_id)),
        "content": scrubbed,
        "fingerprint": fingerprint(body),
        "original size": len(_bytes(body)),
    }
