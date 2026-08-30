"""What comes off an ICOS charges page, and what column F is allowed to say.

Column F is the adjudicated statutory code. The expungement sheet reads it in
792 formulas, so a code sitting there is a charge that sheet treats as
adjudicated against the client. A count that was dismissed, acquitted, never
filed or withdrawn was not adjudicated and its statute does not belong there.

Three of those four were filtered. Withdrawn was not, because the filter
spelled it WITHD in two places and the map spells it WTHD, so the test at the
bottom of this file is the one that matters most: it checks the filter against
the map rather than against another copy of the filter.

The pages here are synthetic. This repo is public and a real charges page is
one person's unredacted criminal record.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser


def _row(*cells):
    return '<tr>%s</tr>' % ''.join(
        '<td><font size="2">%s</font></td>' % cell for cell in cells)


def charges_page(*counts):
    """An ICOS charges page. Each count is (statute, description, outcome)."""
    html = ['<html><body><table>']
    for number, (statute, description, outcome) in enumerate(counts, start=1):
        html.append(_row('Count %d' % number))
        html.append(_row('Offense Date:', '01/01/1900', ''))
        html.append(_row('Adjudication'))
        html.append(_row('Charge:', statute, '', description))
        html.append(_row('Adjudication:', outcome, '', '02/02/1901'))
    html.append('</table></body></html>')
    return ''.join(html).encode('utf-8')


def parse(*counts):
    case = {'id': '00000  FECR000000'}
    case_parser.parse_case_charges(charges_page(*counts), case)
    return case['charges'][0]


GUILTY = ('124.401', 'SYNTHETIC FELONY', 'GUILTY')


# -- what column F may carry ----------------------------------------------

# Every outcome that means the count was not adjudicated, paired with the ICOS
# wording that produces it. Parametrized rather than written out once, because
# the bug was one member of this set behaving differently from the other three
# and a test covering only DISMISSED passed the whole time.
NOT_ADJUDICATED_WORDINGS = [
    ('WITHDRAWN', 'WTHD'),
    ('DISMISSED', 'DISM'),
    ('DISMISSED BY COURT', 'DISM'),
    ('ACQUITTED', 'ACQ'),
    ('NOT GUILTY', 'ACQ'),
    ('NOT FILED', 'NOTF'),
    # The same six as ICOS prefixes them. Polk County served a DNU-ACQUITTED
    # and the map had entries for DNU-GUILTY and DNU-DISMISSED but not that
    # one, so an acquitted domestic abuse assault coded OTH and kept its
    # statute in column F. Everything above was already covered and passing.
    ('DNU-WITHDRAWN', 'WTHD'),
    ('DNU-DISMISSED', 'DISM'),
    ('DNU-ACQUITTED', 'ACQ'),
    ('DNU-NOT FILED', 'NOTF'),
    # And the prefix as Story County spaces it: 'DNU -', space before the
    # hyphen. A literal replace of 'DNU-' cannot see it and the wording fell
    # through to OTH, which is how 'DNU -JCS OTHER ADJ OTHER COURT' alerted
    # on 13 August 2026.
    ('DNU -DISMISSED', 'DISM'),
]


@pytest.mark.parametrize('outcome,code', NOT_ADJUDICATED_WORDINGS)
def test_a_count_that_was_not_adjudicated_leaves_column_f_empty(outcome, code):
    assert parse(('321.218', 'SYNTHETIC OFFENSE', outcome))['charge'] == ''


@pytest.mark.parametrize('outcome,code', NOT_ADJUDICATED_WORDINGS)
def test_it_is_dropped_from_a_case_that_also_has_a_conviction(outcome, code):
    """The plea deal shape: plead to one count, the rest go away.

    The surviving count's statute is the client's. The others are not, and on
    this path they were being joined onto it with a semicolon.
    """
    charge = parse(GUILTY, ('321.218', 'SYNTHETIC OFFENSE', outcome))
    assert charge['charge'] == '124.401'
    assert '321.218' not in charge['charge']
    # The count is still described, so nothing is hidden from a reader. It is
    # only the statutory code the analysis sheets read that it stays out of.
    assert code in charge['description']


def test_a_conviction_keeps_its_statute():
    """Guard against a filter that passes the tests above by dropping all of them."""
    assert parse(GUILTY)['charge'] == '124.401'


def test_a_single_count_keeps_its_disposition_suffix():
    """Column E should identify the result consistently for one or many counts."""
    assert parse(GUILTY)['description'] == 'SYNTHETIC FELONY[GTR]'


def test_two_convictions_both_survive():
    charge = parse(GUILTY, ('321J.2', 'SYNTHETIC OWI', 'GUILTY BY COURT'))
    assert charge['charge'] == '124.401;321J.2'


@pytest.mark.parametrize('outcome', ['DNU-GUILTY', 'DNU-GUILTY BY COURT',
                                     'DNU-DEFERRED'])
def test_a_prefixed_conviction_still_keeps_its_statute(outcome):
    """The other direction, so stripping the prefix cannot empty column F."""
    assert parse(('321J.2', 'SYNTHETIC OWI', outcome))['charge'] == '321J.2'


def test_the_two_maps_agree_on_what_a_prefixed_wording_means():
    """case_parser codes the count; crs codes the case. They read one page.

    They are separate maps with separate spellings, and only one of them
    stripped the prefix. So ICOS could hand over a count that crs called ACQ
    for column N while case_parser called it OTH and left the statute in
    column F, and the workbook contradicted itself on the same row with no
    test looking at both sides.
    """
    import crs
    for wording in case_parser.charge_code_dict:
        # Both prefix shapes ICOS has actually served: DNU-GUILTY from Polk
        # and 'DNU -JCS OTHER ADJ OTHER COURT' from Story, space before the
        # hyphen. Both sides strip with the same function now, but the test
        # keeps feeding both shapes so a regression to a literal replace
        # fails here instead of in an alert email.
        for form in (wording, 'DNU-' + wording, 'DNU -' + wording):
            ours = case_parser.disposition_code(form)
            theirs = crs.charge_code_map.get(case_parser.strip_dnu(form))
            if theirs is None:
                continue
            assert ours == next(iter(theirs)), form


def test_neither_map_knows_a_wording_the_other_does_not():
    """Walking one map's keys cannot see a wording only the other learned.

    GUILTY - OTHER went into crs.charge_code_map alone, so a Hamilton public
    intoxication coded GTR in column G while its description read [OTH], and
    the agreement test above never looked because it iterates case_parser's
    keys. The key sets have to match before agreement on shared keys means
    anything.
    """
    import crs
    assert set(case_parser.charge_code_dict) == set(crs.charge_code_map)


def test_the_juvenile_table_is_the_same_table_in_both_modules():
    """The same trap one table over. JUVENILE_DISPOSITIONS overrides the code
    map on a JV docket, and it is written out twice: crs decides column G and
    case_parser decides the [CODE] suffix on column E. A wording learned in one
    copy and not the other puts the two columns of the same row in
    disagreement, which is exactly what GUILTY - OTHER did above."""
    import crs
    assert set(case_parser.JUVENILE_DISPOSITIONS) == set(
        crs.JUVENILE_DISPOSITIONS)
    for wording, code in case_parser.JUVENILE_DISPOSITIONS.items():
        assert next(iter(crs.JUVENILE_DISPOSITIONS[wording])) == code, wording


def test_a_discharged_juvenile_case_is_an_adjudication():
    """A JVJV docket carried the case status DISCHARGE on 2026-08-26 and went
    out uncoded. Iowa Legal Aid said JUV: a juvenile case is JUV unless it was
    waived to adult court. The adult reading is unchanged, because a
    discharged adult case does not say how it was adjudicated."""
    import crs
    assert crs.case_level_code('DISCHARGE', '07701  JVJV003889') == 'JUV'
    assert crs.case_level_code('DISCHARGE', '07701  FECR003889') is None
    assert case_parser.disposition_code('DISCHARGE', '07701  JVJV003889') == 'JUV'


def test_a_deferred_judgment_is_adjudicated():
    """A deferred judgment is still an adjudication and still carries debt."""
    assert parse(('321J.2', 'SYNTHETIC OWI', 'DEFERRED'))['charge'] == '321J.2'


# -- the DNU prefix ---------------------------------------------------------

@pytest.mark.parametrize('form,bare', [
    ('DNU-GUILTY', 'GUILTY'),
    ('DNU -JCS OTHER ADJ OTHER COURT', 'JCS OTHER ADJ OTHER COURT'),
    ('DNU - GUILTY', 'GUILTY'),
    ('  DNU-GUILTY  ', 'GUILTY'),
])
def test_strip_dnu_takes_off_the_prefix_however_it_is_spaced(form, bare):
    assert case_parser.strip_dnu(form) == bare


def test_strip_dnu_only_reads_the_front_of_the_wording():
    """DNU is a prefix, not a substring. A wording that merely contains the
    letters keeps them."""
    assert case_parser.strip_dnu('GUILTY') == 'GUILTY'
    assert case_parser.strip_dnu('REDNU-CTION') == 'REDNU-CTION'
    assert case_parser.strip_dnu('') == ''
    assert case_parser.strip_dnu(None) == ''


class TestTheDeferredMistrialWording:
    """'DEFERRED MISTRIAL': one juvenile count, alerted 27 August 2026.

    The two words disagree. DEFERRED alone is DEF, a deferred judgment four
    sheets test for by name; a mistrial reached no verdict at all and belongs
    with the dismissals. Neither guess is cheap, so the wording keeps the OTH
    the row was already getting as an unknown, and the entry exists only so it
    stops mailing an unrecognised-disposition alert on every run that touches
    the case. Column V still carries the ICOS wording, and
    OPEN_QUESTIONS.md carries it for Iowa Legal Aid to overrule.
    """

    def test_it_codes_oth_in_both_maps(self):
        assert case_parser.disposition_code('DEFERRED MISTRIAL') == 'OTH'
        import crs
        assert crs.charge_code_map['DEFERRED MISTRIAL'] == \
            {'OTH': crs.OTH_RANK}

    def test_it_is_not_read_as_a_plain_deferred_judgment(self):
        """The failure this entry is guarding against: prefix matching, or
        anyone later 'tidying' it into the DEFERRED row, would put a deferred
        judgment on a client whose trial produced no verdict."""
        import crs
        assert case_parser.disposition_code('DEFERRED MISTRIAL') != 'DEF'
        assert crs.charge_code_map['DEFERRED'] == {'DEF': 2}

    def test_a_conviction_alongside_it_still_wins_the_case_code(self):
        charge = parse(GUILTY,
                       ('321J.2', 'SYNTHETIC OWI', 'DEFERRED MISTRIAL'))
        assert '[OTH]' in charge['description']
        import crs
        dominant = crs.get_dominant_charge([charge])
        assert dominant['disposition'] == 'GTR'
        # And no alert: the wording is known now, not an unknown coded OTH.
        assert dominant['unknown_dispositions'] == []


class TestTheStoryCountyWording:
    """'JCS OTHER ADJ OTHER COURT': another court adjudicated, JCS is relaying.

    A Story County OWI served it with the spaced DNU prefix on 13 August 2026
    and Napier alerted on every run because neither map knew the wording. It
    codes OTH: the adjudication happened somewhere ICOS is not showing, so the
    row moves no money and the note still travels.
    """

    def test_it_codes_oth_in_both_maps(self):
        assert case_parser.disposition_code('JCS OTHER ADJ OTHER COURT') == \
            'OTH'
        assert case_parser.disposition_code(
            'DNU -JCS OTHER ADJ OTHER COURT') == 'OTH'
        import crs
        assert crs.charge_code_map['JCS OTHER ADJ OTHER COURT'] == \
            {'OTH': crs.OTH_RANK}

    def test_a_conviction_alongside_it_still_wins_the_case_code(self):
        """The OWCR056327 shape: the count another court took stays OTH, the
        count this court adjudicated codes the case."""
        charge = parse(GUILTY,
                       ('321J.2', 'SYNTHETIC OWI',
                        'DNU -JCS OTHER ADJ OTHER COURT'))
        assert '[OTH]' in charge['description']
        import crs
        dominant = crs.get_dominant_charge([charge])
        assert dominant['disposition'] == 'GTR'
        # And no alert: the wording is known now, not an unknown coded OTH.
        assert dominant['unknown_dispositions'] == []


class TestTheJuvenileAdmissionWording:
    """'JUVENILE ADMISSION': the juvenile court's guilty plea.

    Two JVJV delinquency cases served it on 18 August 2026 and both coded OTH
    and alerted, because neither map knew the wording. An admission is the
    adjudication reached by the child admitting the allegation rather than the
    court finding it, so it earns JUV at the rank ADJUDICATED already earns,
    and the demotion on non-juvenile case numbers keys on the code, so it
    rides along without being restated.
    """

    def test_it_codes_juv_in_both_maps(self):
        assert case_parser.disposition_code('JUVENILE ADMISSION') == 'JUV'
        assert case_parser.disposition_code('DNU-JUVENILE ADMISSION') == 'JUV'
        import crs
        assert crs.charge_code_map['JUVENILE ADMISSION'] == {'JUV': 1}

    def test_a_jvjv_case_reads_juv_and_stops_alerting(self):
        """The alerted shape: a delinquency case whose only adjudication
        is the admission. The case number is synthetic; both real ones
        were JVJV dockets."""
        charge = parse(
            ('232.2', 'SYNTHETIC DELINQUENCY', 'JUVENILE ADMISSION'))
        import crs
        dominant = crs.get_dominant_charge([charge], '00000  JVJV000000')
        assert dominant['disposition'] == 'JUV'
        # And no alert: the wording is known now, not an unknown coded OTH.
        assert dominant['unknown_dispositions'] == []

    def test_on_an_adult_case_it_loses_to_a_real_conviction(self):
        """The clerks enter juvenile vocabulary on adult cases too; the
        JUV_RANK_ADULT_CASE demotion has to catch this wording the same as
        it catches ADJUDICATED."""
        charge = parse(GUILTY,
                       ('232.2', 'SYNTHETIC ADMISSION', 'JUVENILE ADMISSION'))
        import crs
        dominant = crs.get_dominant_charge([charge], '00000  FECR000000')
        assert dominant['disposition'] == 'GTR'


# -- the invariant the typo broke ------------------------------------------

def test_every_not_adjudicated_code_is_one_the_map_can_produce():
    """The check the old code could not do, because it compared two copies.

    NOT_ADJUDICATED held WITHD and the map produced WTHD, so withdrawn counts
    were never filtered and no test noticed for as long as the filter was only
    ever compared against itself. Anchoring it to the map means a rename on
    either side fails here instead of quietly widening column F.
    """
    produced = set(case_parser.charge_code_dict.values())
    assert case_parser.NOT_ADJUDICATED <= produced, (
        case_parser.NOT_ADJUDICATED - produced)


def test_the_outcomes_that_are_convictions_are_not_in_it():
    """The other direction: filtering a guilty code would empty column F."""
    convictions = {'GTR', 'GPL', 'DEF', 'JUV'}
    assert not (case_parser.NOT_ADJUDICATED & convictions)
