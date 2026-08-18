"""What happens to a case ICOS words in a way Napier has never seen.

charge_code_map holds every outcome anyone has watched ICOS produce. Anything
else becomes OTH, and OTH is not GTR, GPL or DEF, which is the test the
expungement, bankruptcy, exemption and licence sheets run before they say
anything about a case. So an unrecognised word does not fail. It codes a client
with a real conviction as somebody with none, on four sheets at once, in a file
that is about to be used to advise them.

The old rank made that worse than it had to be: OTH was 3, above a conviction,
so a single stray count on a nine count case took the whole case with it.

Nothing here can make Napier understand a word it has not been taught. What it
can do is stop the guess from overruling what Napier does know, and make the
guess impossible to miss. Three ways, because a workbook and an inbox are read
by different people at different times: the rank, the note on the row, and the
email.
"""

import os
import sys

import pytest
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts
import crs
import tasks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, 'CRS 3.5.5.xlsx')

# Not a real ICOS wording, which is the point: it stands for whatever Iowa
# Courts starts writing next.
NOVEL = 'SYNTHETIC OUTCOME NOBODY HAS TAUGHT NAPIER'


def _charge(*dispositions):
    return [{'description': 'SYNTHETIC OFFENSE', 'charge': '124.401',
             'disposition': list(dispositions),
             'offenseDate': '01/01/1900', 'dispositionDate': '02/02/1901'}]


# -- the rank ---------------------------------------------------------------

def test_a_stray_code_no_longer_takes_the_conviction_with_it():
    """The bug. One unreadable count on a case that also has a guilty plea.

    This is the whole of it: the case is a conviction, ICOS said so on one of
    the counts, and Napier used to throw that away because it could not read a
    different count.
    """
    charge = _charge('GUILTY - NEGOTIATED/VOLUN PLEA', NOVEL)
    assert crs.get_dominant_charge(charge)['disposition'] == 'GPL'


def test_it_does_not_take_a_deferred_judgment_either():
    """DEF outranks a plain guilty, so it is the one furthest from OTH."""
    assert crs.get_dominant_charge(
        _charge('DEFERRED', NOVEL))['disposition'] == 'DEF'


def test_a_case_that_is_only_unreadable_still_reads_as_unreadable():
    """The other direction, and the reason OTH is not ranked below everything.

    A case Napier cannot read at all must not pass itself off as dismissed.
    Staff seeing OTH go and look; staff seeing DISM do not.
    """
    assert crs.get_dominant_charge(_charge(NOVEL))['disposition'] == 'OTH'
    assert crs.get_dominant_charge(
        _charge('DISMISSED BY COURT', NOVEL))['disposition'] == 'OTH'


def test_the_rank_sits_between_a_dismissal_and_a_conviction():
    """Pinning the two tests above to the constant they both depend on."""
    dismissals = [rank for entry in crs.charge_code_map.values()
                  for rank in entry.values() if rank == 0]
    convictions = [rank for entry in crs.charge_code_map.values()
                   for rank in entry.values() if rank >= 1]
    assert max(dismissals) < crs.OTH_RANK < min(convictions)


def test_the_unreadable_wording_comes_back_out():
    charge = _charge('GUILTY', NOVEL)
    assert crs.get_dominant_charge(charge)['unknown_dispositions'] == [NOVEL]


def test_a_case_napier_can_read_reports_nothing():
    charge = _charge('GUILTY', 'DISMISSED BY COURT')
    assert crs.get_dominant_charge(charge)['unknown_dispositions'] == []


def test_the_dnu_prefix_is_stripped_before_the_map_is_asked():
    """Otherwise every DNU- case reports itself as unreadable and the alert is noise."""
    assert crs.get_dominant_charge(
        _charge('DNU-GUILTY'))['unknown_dispositions'] == []


# -- the note on the row ----------------------------------------------------

def _case(*dispositions):
    return {
        'id': '00000  FECR000000',
        'county': 'SYNTHETIC',
        'charges': _charge(*dispositions),
        'financials': [],
        'summary_categories': [],
    }


def _row(case):
    sheet = load_workbook(FULL)['CASE DATA']
    unknown = crs.process_case(case, sheet, crs.FIRST_CASE_ROW)
    row = str(crs.FIRST_CASE_ROW)
    return unknown, sheet['G' + row].value, sheet['V' + row].value


def test_the_row_says_it_was_coded_on_a_guess():
    """The workbook outlives the alert and goes to whoever runs the clinic."""
    unknown, code, note = _row(_case(NOVEL))
    assert unknown == [(NOVEL, True)]
    assert code == 'OTH'
    assert NOVEL in note
    assert 'OTH' in note


def test_the_note_names_every_wording_it_could_not_read():
    other = 'SECOND SYNTHETIC OUTCOME'
    _, _, note = _row(_case(NOVEL, other))
    assert NOVEL in note and other in note


def test_a_case_napier_can_read_gets_no_note():
    """Column V is not empty here. process_financials has its own say about
    where the fee figures came from, and that is not this note's business."""
    unknown, code, note = _row(_case('GUILTY'))
    assert unknown == []
    assert code == 'GTR'
    assert NOVEL not in (note or '')
    assert 'OTH' not in (note or '')


def test_the_note_does_not_displace_what_the_money_put_there():
    """process_financials owns column V and writes it first. Both notes matter.

    A row can be coded on a guess and have its fee figures come from a fallback
    at the same time, and the fee note is the one staff have been trained to
    look for. Appending rather than assigning is why both survive.
    """
    _, _, money_only = _row(_case('GUILTY'))
    _, _, both = _row(_case(NOVEL))
    assert money_only, 'this case is supposed to carry a fee note'
    assert money_only in both
    assert NOVEL in both


# -- the email --------------------------------------------------------------

class FakeJob(object):
    id = 'synthetic-job-id'
    kind = 'crs'

    def __init__(self):
        self.progress = []
        self.messages = []

    def log(self, message, **kwargs):
        self.messages.append(message)


@pytest.fixture(autouse=True)
def clean_alerts():
    alerts.reset()
    yield
    alerts.reset()


def _recorded(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, 'record',
                        lambda *a, **kw: sent.append((a, kw)))
    return sent


def test_an_unreadable_disposition_is_mailed_out(monkeypatch):
    """The map only gets the missing word added if it reaches Alex.

    Every other signal here lives in a file on somebody else's desk.
    """
    sent = _recorded(monkeypatch)
    job = FakeJob()
    tasks.report_unknown_dispositions(job, {(NOVEL, True): ['00000  FECR000000']})
    assert len(sent) == 1
    args, kwargs = sent[0]
    assert args[2] == alerts.UNKNOWN_DISPOSITION
    assert kwargs['disposition'] == NOVEL
    assert '00000  FECR000000' in kwargs['cases']


def test_the_alert_carries_no_defendant(monkeypatch):
    """Article 5. The wording and the case number are public record; a client is not.

    Both of those are needed to fix the map: the word is what is missing from
    it and the case is the ICOS page to read the wording off. Nothing else is.
    """
    sent = _recorded(monkeypatch)
    tasks.report_unknown_dispositions(
        FakeJob(), {(NOVEL, True): ['00000  FECR000000']})
    _, kwargs = sent[0]
    assert set(kwargs) <= {'progress', 'disposition', 'cases'}


def test_the_run_says_so_on_the_progress_page_too(monkeypatch):
    """Staff read the finish page. Alex reads the mail. Both need to know."""
    _recorded(monkeypatch)
    job = FakeJob()
    tasks.report_unknown_dispositions(job, {(NOVEL, True): ['00000  FECR000000']})
    assert any(NOVEL in message for message in job.messages)
    assert any('OTH' in message for message in job.messages)


def test_a_clean_run_says_nothing_anywhere(monkeypatch):
    sent = _recorded(monkeypatch)
    job = FakeJob()
    tasks.report_unknown_dispositions(job, {})
    assert sent == [] and job.messages == []


def test_one_alert_per_wording_not_per_case(monkeypatch):
    """A clinic pulling seventy cases through one new ICOS wording is one email."""
    sent = _recorded(monkeypatch)
    tasks.report_unknown_dispositions(
        FakeJob(), {(NOVEL, True): ['00000  FECR000000', '00000  FECR000001',
                            '00000  FECR000002']})
    assert len(sent) == 1
    assert sent[0][1]['cases'].count(',') == 2


# -- and that a real run carries it through --------------------------------

def test_the_workbook_build_hands_the_wordings_back(tmp_path, monkeypatch):
    """process_case can report all it likes if build_workbook drops it."""
    monkeypatch.setattr(tasks, 'tmp_dir', str(tmp_path) + os.sep)
    _, unknown, _, _ = tasks.build_workbook(
        [_case(NOVEL), _case('GUILTY')], 'TEST CLIENT', '01/01/1980', False)
    assert unknown == {(NOVEL, True): ['00000  FECR000000']}
