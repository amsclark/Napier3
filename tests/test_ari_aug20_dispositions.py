"""Three dispositions Iowa Legal Aid settled on 20 August, and the two they
did not.

A clinic run turned up CONSENT DECREE, OTHER JUDGMENT and TRANSFERRED as
wordings Napier had never seen, so all three coded OTH and alerted. Iowa Legal
Aid answered for the juvenile docket: a consent decree and an other judgment
are both dispositions of the delinquency petition and rank as JUV, and a
transfer off the juvenile docket is the child being waived up to adult court,
which is JWV.

All three wordings also exist on adult and civil dockets and mean different
things there, and the answer given was explicitly about juveniles, so the
juvenile reading is gated on a JV case number.

That leaves two from the same run. TRANSFERRED on an adult criminal case is a
change of venue, which has coded TNSF since 3 August, so that one is settled by
the code that was already there. OTHER JUDGMENT on a civil case is not settled
and stays uncoded on purpose: the code a civil judgment would want is CIV, CIV
is in the expungement sheet's cleared set, and guessing it would clear every
fee column on the row.

Synthetic case numbers throughout. The repository is public.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import crs
from test_ari_aug5_items import charges_page

CONSENT_DECREE = 'CONSENT DECREE'
OTHER_JUDGMENT = 'OTHER JUDGMENT'
TRANSFERRED = 'TRANSFERRED'

JUVENILE = '00000  JVJV000000'
ADULT = '00000  AMCR000000'
CIVIL = '00000  DRCV000000'


def _case(case_id, counts, status=''):
    case = {'id': case_id, 'county': 'SYNTHETIC', 'financials': [],
            'summary_categories': [], 'sentences': [],
            'summary_created_date': '01/01/1900',
            'summary_disposition_date': '',
            'summary_dispo_status': status}
    case_parser.parse_case_charges(charges_page(counts), case)
    return case


def _code(case_id, outcome):
    """Column G for a one-count case disposed with this wording."""
    case = _case(case_id, [('123.45', 'SYNTHETIC OFFENCE', outcome)])
    charge = crs.get_dominant_charge(case['charges'], case['id'])
    return charge['disposition'], charge['unknown_dispositions']


class TestOnTheJuvenileDocket:
    def test_consent_decree_is_an_adjudication(self):
        assert _code(JUVENILE, CONSENT_DECREE) == ('JUV', [])

    def test_other_judgment_is_an_adjudication(self):
        assert _code(JUVENILE, OTHER_JUDGMENT) == ('JUV', [])

    def test_transferred_is_a_waiver_up_to_adult_court(self):
        assert _code(JUVENILE, TRANSFERRED) == ('JWV', [])

    @pytest.mark.parametrize('case_id', ['00000  JVJV000000',
                                         '00000  JVCV000000',
                                         '00000  JVDV000000'])
    def test_the_whole_JV_family_counts(self, case_id):
        """is_juvenile_case takes JV, not JVJV, and this reading inherits it."""
        assert _code(case_id, TRANSFERRED)[0] == 'JWV'

    def test_an_adjudication_still_outranks_a_waiver(self):
        """A case with a real adjudication on one count reads as adjudicated.

        JWV is ranked with the non-convictions, so it cannot demote a count
        the juvenile court actually decided.
        """
        case = _case(JUVENILE, [('123.45', 'SYNTHETIC OFFENCE', TRANSFERRED),
                                ('123.46', 'SYNTHETIC OFFENCE', 'ADJUDICATED')])
        charge = crs.get_dominant_charge(case['charges'], case['id'])
        assert charge['disposition'] == 'JUV'


class TestOffTheJuvenileDocket:
    def test_transferred_on_an_adult_case_is_a_change_of_venue(self):
        """The wording Iowa Legal Aid's 20 August run alerted on, on an AMCR.

        CHANGE OF VENUE has produced TNSF since 3 August and means the same
        thing: the charge left this court and was decided elsewhere.
        """
        assert _code(ADULT, TRANSFERRED) == ('TNSF', [])

    def test_it_agrees_with_change_of_venue(self):
        assert _code(ADULT, TRANSFERRED)[0] == _code(ADULT,
                                                     'CHANGE OF VENUE')[0]

    def test_transferred_never_reads_as_a_waiver_off_the_juvenile_docket(self):
        for case_id in (ADULT, CIVIL, '00000  FECR000000'):
            assert _code(case_id, TRANSFERRED)[0] != 'JWV'

    def test_other_judgment_on_a_civil_case_stays_uncoded(self):
        """Uncoded and visibly so, rather than guessed at.

        CIV is what a civil judgment would want and CIV clears the fee
        columns, so this one waits for Iowa Legal Aid rather than moving money
        on a guess. The wording travels out through unknown_dispositions,
        which is what puts it in column V and alerts the run.
        """
        code, unknown = _code(CIVIL, OTHER_JUDGMENT)
        assert code == 'OTH'
        assert unknown == [OTHER_JUDGMENT]

    def test_consent_decree_off_the_juvenile_docket_stays_uncoded(self):
        code, unknown = _code(ADULT, OTHER_JUDGMENT)
        assert code == 'OTH'
        assert unknown == [OTHER_JUDGMENT]


class TestTheCaseLevelStatusReadsTheSameDocket:
    """ICOS prints these three as case-level summary statuses too, and column
    G reads the summary when a count carries no adjudication of its own. A
    wording cannot mean one thing per count and another for the case."""

    def test_transferred_is_a_waiver_on_a_juvenile_case(self):
        assert crs.case_level_code(TRANSFERRED, JUVENILE) == 'JWV'

    def test_transferred_is_a_change_of_venue_on_an_adult_case(self):
        assert crs.case_level_code(TRANSFERRED, ADULT) == 'TNSF'

    def test_no_case_number_cannot_invent_a_waiver(self):
        """Omitting the number reads as not juvenile, which is the way round
        that cannot put JWV on an adult case."""
        assert crs.case_level_code(TRANSFERRED) == 'TNSF'

    def test_other_judgment_is_an_adjudication_on_a_juvenile_case(self):
        assert crs.case_level_code(OTHER_JUDGMENT, JUVENILE) == 'JUV'

    def test_other_judgment_is_still_untranslated_elsewhere(self):
        assert crs.case_level_code(OTHER_JUDGMENT, CIVIL) is None
        assert crs.case_level_code(CONSENT_DECREE, CIVIL) is None
