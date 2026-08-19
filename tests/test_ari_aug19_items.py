"""Two things Iowa Legal Aid found in the workbook on 19 August.

Both are the same client, a Polk defendant with seven AMCR cases, and both are
column G disagreeing with itself about what a case is.

  Three violation-of-parole cases disposed NOT FILED read NOTF, while three
  more of the same statute disposed CHANGE OF VENUE read CIV. Napier was
  reading the clerk's word rather than the statute, and standing down from
  the civil reading on any word that clears the EXPUNGEMENT & 910.7 sheet.
  Iowa Legal Aid had already settled that trade on 18 August: a civil case is
  not eligible for dismissed-or-acquitted expungement in the first place, so
  the cleared code was only buying a YES that no attorney could act on.

  One case cites 820.14, arrest without a warrant, which nobody had seen in
  the corpus before. It carries no count-level adjudication at all, so column
  G was reading the summary's DISMISSED. 820.14 is a hold under the same
  extradition chapter as 820.2 -- an officer arresting somebody believed to be
  a fugitive before the other state's warrant arrives -- so it reads civil for
  the same reason 820.2 does.

Synthetic pages throughout, shaped from the real ones. The repository is
public and a real charges page is one person's unredacted criminal record.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
from test_ari_aug5_items import _cells, _case, _row


def unruled_page(statute, description):
    """The arrest-without-warrant shape, which no other fixture here has.

    ICOS filled the adjudication charge and the adjudication date and left
    the adjudication wording empty, so the count carries a statute and no
    result. That is not the same page as a genuinely pending count, where
    the whole adjudication block is blank, and the difference is the whole
    question: the statute is the only thing on this page that says what the
    case is.
    """
    html = ['<html><body><table>']
    html.append(_cells('Count 01', 'Original Charge'))
    html.append(_cells('Charge:', statute, 'Description:', description))
    html.append(_cells('Offense Date:', '07/17/2022', 'Arrest Date:', ''))
    html.append(_cells('Adjudication'))
    html.append(_cells('Charge:', statute, 'Description:', description))
    html.append(_cells('Adjudication:', '', 'Adjudication Date:', '07/18/2022'))
    html.append('</table></body></html>')
    return ''.join(html).encode('utf-8')


def unruled_case(statute, description, status='DISMISSED'):
    case = {'id': '00000  SMSM000000', 'county': 'SYNTHETIC',
            'financials': [], 'summary_categories': [], 'sentences': [],
            'summary_created_date': '07/18/2022',
            'summary_disposition_date': '07/18/2022',
            'summary_dispo_status': status}
    case_parser.parse_case_charges(unruled_page(statute, description), case)
    return case

# What the clerk typed on the three cases Iowa Legal Aid named, and on the
# three of the same statute that were already coming out right.
NOT_FILED = 'NOT FILED'
TRANSFERRED = 'CHANGE OF VENUE'


class TestParoleViolationsAgreeWithEachOther:
    """The three Polk parole violations Iowa Legal Aid named against the
    three of the same statute that were already right. Same statute, same
    court, same client."""

    @pytest.mark.parametrize('outcome', [NOT_FILED, TRANSFERRED])
    def test_both_ways_the_clerk_cleared_it_read_civil(self, outcome):
        cells, _ = _row(_case([('908.1', 'VIOLATION OF PAROLE - 1985',
                                outcome)], status='CLOSED',
                              dispo_date='04/27/2019'))
        assert cells['G'] == 'CIV'

    def test_the_case_level_status_does_not_override_it(self):
        """CLOSED is the summary status on all six, and case_level_code reads
        the summary before the civil rule gets its say."""
        cells, _ = _row(_case([('908.1', 'VIOLATION OF PAROLE - 1985',
                                NOT_FILED)], status='CLOSED'))
        assert cells['G'] == 'CIV'

    def test_it_still_reads_the_statute_off_the_pre_filter_list(self):
        """NOT FILED empties column F, so the civil reading has to fall back
        to the statutes ICOS listed. If it did not, this would be NOTF."""
        cells, _ = _row(_case([('908.1', 'VIOLATION OF PAROLE - 1985',
                                NOT_FILED)]))
        assert cells['F'] == ''
        assert cells['G'] == 'CIV'


class TestArrestWithoutWarrant:
    """The aggravated misdemeanour Iowa Legal Aid had not seen before. No
    adjudication on the count, DISMISSED on the summary, and 820.14 the only
    thing on the page that says what it is."""

    def test_it_reads_civil(self):
        cells, _ = _row(unruled_case('820.14', 'ARREST WITHOUT WARRANT'))
        assert cells['G'] == 'CIV'

    def test_without_the_statute_the_summary_still_speaks(self):
        """What column G was doing before, and what it still does for any
        unruled count whose statute nobody has vouched for. The summary said
        DISMISSED and that is all there was to read."""
        cells, _ = _row(unruled_case('123.45', 'SYNTHETIC UNRULED'))
        assert cells['G'] == 'DISM'

    @pytest.mark.parametrize('outcome', ['GUILTY', 'DISMISSED', 'WITHDRAWN',
                                         'CHANGE OF VENUE', 'NOT FILED'])
    def test_whatever_a_clerk_might_have_typed_instead(self, outcome):
        cells, _ = _row(_case([('820.14', 'ARREST WITHOUT WARRANT', outcome)]))
        assert cells['G'] == 'CIV'

    def test_a_conviction_alongside_it_still_wins(self):
        """Every count, not any count -- the rule the other civil sections
        already answer to."""
        cells, _ = _row(_case([('820.14', 'ARREST WITHOUT WARRANT', 'GUILTY'),
                               ('124.401', 'SYNTHETIC CONTROLLED', 'GUILTY')]))
        assert cells['G'] == 'GTR'


class TestTheSectionAndNotTheChapter:
    """820.14 is a section, and the trailing-digit rule is what keeps it one.

    The same trap 908.11 set for 908.1. Nothing in chapter 820 reads civil on
    the strength of the chapter alone.
    """

    @pytest.mark.parametrize('statute', ['820.140', '820.145', '820.1',
                                         '820.4', '820.21'])
    def test_a_neighbour_keeps_its_own_code(self, statute):
        cells, _ = _row(_case([(statute, 'SYNTHETIC CHAPTER 820', 'GUILTY')]))
        assert cells['G'] == 'GTR'

    def test_a_subsection_of_820_14_is_still_820_14(self):
        cells, _ = _row(_case([('820.14(1)', 'SYNTHETIC HOLD', 'GUILTY')]))
        assert cells['G'] == 'CIV'
