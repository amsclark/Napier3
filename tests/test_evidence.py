"""What may leave the building when Napier keeps proof that ICOS was down.

The point of the feature is that Alex can forward a page to Iowa Courts and say
"this is what your system served us at 09:14." The point of these tests is that
doing so can never also forward a client's name.

The fixture below is the shape of a page ICOS really served on 2026-07-30,
rebuilt with a synthetic caption, a synthetic case number and a synthetic
account, because this repository is public. Its structure is faithful: the
caption sits in a bgcolor cell above a case number that belongs to a different
case than the one requested, the problem report text sits under it, and the
account is stamped into the footer.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evidence


OUTAGE_PAGE = b"""<HTML>
<head><title>Trial Court Case Summary</title></head>
<body bgcolor="white">
<table border="0" cellspacing="0" cellpadding="8" width="100%">
\t<tr>
\t\t<td width="731" height="40" colspan="5" align="left" bgcolor="#cccccc">
\t\t<b>Summary<br>
\t\t</b>Title:&nbsp;STATE OF IOWA VS SYNTHETIC DEFENDANT<br>
\t\tCase: 00000  FECR000000 (SYNTHETIC)<br>
\t\t</td>
\t</tr>
\t<tr>
\t\t<td colspan=7 bgcolor="#EDEDED" align="left"><br>
\t\t<u><b>Problem Report:</b></u> There was a communication problem.<br>
\t\t<br>
\t\t<u><b>Possible Cause:</b></u> The Web server may be too busy.<br>
\t\t<br>
\t\tThe Web server may be unable to contact the data source server.<br>
\t\t</td>
\t</tr>
\t<tr><td colspan=7><b>Disposition</b></td></tr>
\t<tr><td colspan=7>No Disposition records were found.</td></tr>
</table>
<font size="1">For exclusive use by the Iowa Courts<br>
CN=ila00,O=JUDICIAL</font>
</body></HTML>
"""

# A case page and a search results page are the two things that must never be
# packaged, because both are full of people. This is the shape of the first.
REAL_CASE_PAGE = OUTAGE_PAGE.replace(
    b"<u><b>Problem Report:</b></u> There was a communication problem.<br>",
    b"Filed: 01/14/2019<br>").replace(
    b"No Disposition records were found.",
    b"Guilty 03/02/2019")


class TestWhatMayBeSent:
    """Only ICOS's own outage page, and never anything else."""

    def test_the_outage_page_is_recognised(self):
        assert evidence.is_outage_page(OUTAGE_PAGE)

    def test_a_real_case_page_is_not(self):
        # It has the same heading, the same footer and the same table. What it
        # does not have is Iowa saying it could not reach its data, and that is
        # the only thing that makes a page safe to scrub with confidence.
        assert not evidence.is_outage_page(REAL_CASE_PAGE)
        assert evidence.package(REAL_CASE_PAGE, case_id="x", stamp="s") is None

    def test_an_empty_body_is_not(self):
        assert not evidence.is_outage_page(b"")
        assert not evidence.is_outage_page(None)

    def test_a_page_too_large_to_be_this_page_is_refused(self):
        # A search results page carrying the marker in a footnote is still a
        # list of everyone who matched, with their dates of birth.
        bloated = OUTAGE_PAGE + b"<!-- %s -->" % (b"x" * evidence.MAX_BYTES)
        assert not evidence.is_outage_page(bloated)


class TestWhatComesOutFirst:
    """Article 5 and Article 1.2, enforced on the bytes rather than promised."""

    @pytest.fixture
    def scrubbed(self):
        return evidence.scrub(OUTAGE_PAGE)

    def test_the_caption_does_not_survive(self, scrubbed):
        # On a clinic run this line is the client's own case caption, because
        # ICOS keeps serving the heading of whichever case was selected last.
        assert b"SYNTHETIC DEFENDANT" not in scrubbed
        assert b"STATE OF IOWA VS" not in scrubbed
        assert b"Title:" in scrubbed, "the field should stay, redacted"

    def test_the_account_does_not_survive(self, scrubbed):
        assert b"ila00" not in scrubbed
        assert b"CN=ila" not in scrubbed

    def test_a_date_does_not_survive(self):
        dated = OUTAGE_PAGE.replace(b"(SYNTHETIC)", b"(SYNTHETIC) 07/04/1971")
        assert b"07/04/1971" not in evidence.scrub(dated)

    def test_the_case_number_does_survive(self, scrubbed):
        # Court public record, and the whole technical argument. The page came
        # back wearing a different case than the one asked for, which is how a
        # court IT department can tell this was their session state and not
        # Napier asking for the wrong thing.
        assert b"00000  FECR000000" in scrubbed
        assert b"SYNTHETIC" in scrubbed

    def test_iowas_own_wording_does_survive(self, scrubbed):
        assert b"There was a communication problem" in scrubbed
        assert b"The Web server may be unable to contact the data source" in scrubbed
        assert b"No Disposition records were found." in scrubbed

    def test_a_clean_scrub_reports_no_leaks(self, scrubbed):
        assert evidence.leaks(scrubbed) == []


class TestTheScrubberIsCheckedRatherThanTrusted:
    """leaks() is the belt to scrub()'s braces, so it has to actually fire.

    Every regex here is written against a page template ICOS controls and can
    change without telling anybody. If that happens the failure is silent and
    the cost is a privileged name in an email to a court, so package() asks
    whether the scrub worked instead of assuming it did.
    """

    def test_a_missed_account_stamp_is_caught(self, monkeypatch):
        monkeypatch.setattr(evidence, 'ACCOUNT_STAMP',
                            evidence.re.compile(rb"WILL NOT MATCH ANYTHING"))
        assert 'account stamp' in evidence.leaks(evidence.scrub(OUTAGE_PAGE))

    def test_the_caption_survives_one_pattern_going_stale(self, monkeypatch):
        # Two passes remove the caption: the one that knows where it sits, and
        # the one that knows what it looks like. Losing either still leaves a
        # scrub that works, which is the whole reason there are two.
        monkeypatch.setattr(evidence, 'CAPTION',
                            evidence.re.compile(rb"WILL NOT MATCH ANYTHING"))
        scrubbed = evidence.scrub(OUTAGE_PAGE)
        assert b"SYNTHETIC DEFENDANT" not in scrubbed
        assert evidence.leaks(scrubbed) == []

    def test_a_missed_caption_is_caught_when_both_passes_go_stale(self, monkeypatch):
        monkeypatch.setattr(evidence, 'CAPTION',
                            evidence.re.compile(rb"WILL NOT MATCH ANYTHING"))
        monkeypatch.setattr(evidence, 'VERSUS',
                            evidence.re.compile(rb"WILL NOT MATCH ANYTHING"))
        assert 'case caption' in evidence.leaks(evidence.scrub(OUTAGE_PAGE))

    def test_a_missed_date_is_caught(self, monkeypatch):
        monkeypatch.setattr(evidence, 'DATE',
                            evidence.re.compile(rb"WILL NOT MATCH ANYTHING"))
        dated = OUTAGE_PAGE.replace(b"(SYNTHETIC)", b"(SYNTHETIC) 07/04/1971")
        assert 'date' in evidence.leaks(evidence.scrub(dated))

    def test_package_refuses_a_document_that_leaks(self, monkeypatch):
        # Nothing is sent rather than something is sent redacted-ish. An email
        # that does not arrive costs a follow-up; one that arrives carrying a
        # name cannot be recalled from a court's inbox.
        monkeypatch.setattr(evidence, 'ACCOUNT_STAMP',
                            evidence.re.compile(rb"WILL NOT MATCH ANYTHING"))
        assert evidence.package(OUTAGE_PAGE, case_id="x", stamp="s") is None


class TestTyingTheCopyToTheOriginal:

    def test_the_fingerprint_is_of_the_page_as_served(self):
        document = evidence.package(OUTAGE_PAGE, case_id="00000  FECR000000",
                                    stamp="20260730T213800Z")
        assert document['fingerprint'] == evidence.fingerprint(OUTAGE_PAGE)
        assert document['fingerprint'] != evidence.fingerprint(document['content'])
        assert document['original size'] == len(OUTAGE_PAGE)

    def test_the_filename_says_when_and_which_case(self):
        document = evidence.package(OUTAGE_PAGE, case_id="00000  FECR000000",
                                    stamp="20260730T213800Z")
        assert document['filename'] == (
            "iowa-courts-outage-20260730T213800Z-00000-FECR000000.html")

    def test_a_case_id_cannot_escape_into_the_path(self):
        document = evidence.package(OUTAGE_PAGE, case_id="../../etc/passwd",
                                    stamp="s")
        assert "/" not in document['filename'].replace(".html", "")
