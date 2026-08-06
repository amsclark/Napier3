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
        for form in (wording, 'DNU-' + wording):
            ours = case_parser.disposition_code(form)
            theirs = crs.charge_code_map.get(form.replace('DNU-', ''))
            if theirs is None:
                continue
            assert ours == next(iter(theirs)), form


def test_a_deferred_judgment_is_adjudicated():
    """A deferred judgment is still an adjudication and still carries debt."""
    assert parse(('321J.2', 'SYNTHETIC OWI', 'DEFERRED'))['charge'] == '321J.2'


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
