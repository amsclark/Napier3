"""Search parsing, and what the parsers write to disk.

`parse_search` decides who is who. A name search against ICOS returns every
person whose name matches, and on the real page behind
`tests/fixtures/search_results_sample.html` that was nine different people
sharing a surname. Staff pick a person off the results page and the CRS job
pulls only that person's cases, so if this function groups two people
together the workbook silently mixes their records.

The dump tests cover the other half: these parsers used to write the raw
court page to /tmp on every call, unconditionally, and a page like the one
in the fixture is the unredacted record for everyone the search matched.
"""

import os

import pytest

import case_parser


FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures',
                       'search_results_sample.html')


@pytest.fixture
def search_page():
    with open(FIXTURE, 'rb') as handle:
        return handle.read()


def ids(cases):
    return [c['id'] for c in cases]


def test_the_search_page_parses_into_one_row_per_party_case(search_page):
    cases, too_many = case_parser.parse_search(search_page)
    assert too_many is False
    # The INTERESTED PARTY and NO ACCESS NONPARTY FILER rows are dropped as
    # people who touched a case without being charged in it. The other four
    # are parties.
    assert ids(cases) == ['00000  FECR000000', '00000  SMCR000001',
                          '00000  SCSC000002', '00000  SRCR000005']


def test_a_date_of_birth_arrives_without_the_padding_icos_wraps_it_in(search_page):
    """ICOS pads the DOB cell with CRLFs and tabs and does not pad the name.

    Anything comparing this value to a real date, or writing it into a cell,
    gets the padding too unless the parser takes it off here.
    """
    cases, _ = case_parser.parse_search(search_page)
    by_id = {c['id']: c for c in cases}
    assert by_id['00000  FECR000000']['dob'] == '01/01/1900'
    assert by_id['00000  SMCR000001']['dob'] == '02/02/1901'
    # The blank cell is padded too, and must come back genuinely empty rather
    # than as whitespace that reads as truthy.
    assert by_id['00000  SCSC000002']['dob'] == ''


def test_two_people_sharing_a_surname_do_not_become_one_person(search_page):
    """The failure this fixture was captured to prevent."""
    import tasks
    cases, _ = case_parser.parse_search(search_page)
    grouped, keys = tasks.group_cases(cases)
    assert len(keys) == 3, keys
    pat = [k for k in keys if k.startswith('1900-01-01')]
    sam = [k for k in keys if k.startswith('1901-02-02')]
    unknown = [k for k in keys if k.startswith('DOB-UNKNOWN')]
    assert len(pat) == len(sam) == len(unknown) == 1
    assert grouped[pat[0]] == ['00000  FECR000000', '00000  SRCR000005']
    assert grouped[sam[0]] == ['00000  SMCR000001']
    assert grouped[unknown[0]] == ['00000  SCSC000002']


def test_a_pro_se_defendant_is_still_a_defendant(search_page):
    cases, _ = case_parser.parse_search(search_page)
    assert '00000  SRCR000005' in ids(cases)


def test_a_nonparty_filer_is_not_a_party(search_page):
    """Filing one document into a case is not being charged in it.

    This role turned up on a real search page on 2026-07-30 and was not in the
    suppression list, so the case was pulled as if the client were a party to
    it. `parse_case_charges` reads every charge row on a case page and does no
    filtering by person, because in the ordinary case the person searched for
    is the defendant, so the effect was to write the actual defendant's
    charges and court debt into this client's record summary.
    """
    cases, _ = case_parser.parse_search(search_page)
    assert '00000  LACV000004' not in ids(cases)


def role_row(role):
    """One results row carrying the given role, otherwise well formed."""
    return ("""<html><body><table>
      <tr><td>00000&nbsp;&nbsp;FECR000000</td><td>Case ID</td>
          <td>STATE VS TESTER</td><td>TESTER, PAT Q</td>
          <td>01/01/1900</td><td>%s</td></tr>
      </table></body></html>""" % role).encode('utf-8')


# ICOS pads the date of birth cell with CRLFs and tabs on the real page and
# leaves the role cell alone, which is the only reason matching roles against
# exact strings has ever worked. If that ever flips, every one of these roles
# starts being read as a party at once, and a suppression list that silently
# stops suppressing looks exactly like a list that had nothing to suppress.
PADDED_ROLES = [
    'NO ACCESS NONPARTY FILER',
    '\r\n\t\tNO ACCESS NONPARTY FILER\r\n\t\t',
    '&nbsp;NO ACCESS NONPARTY FILER',
    '  NO ACCESS  NONPARTY  FILER  ',
    '<font size="2">\r\n\tNO ACCESS NONPARTY FILER\r\n\t</font>',
    'INTERESTED PARTY',
    '\r\n\t\tINTERESTED PARTY\t\r\n',
]


@pytest.mark.parametrize('role', PADDED_ROLES)
def test_padding_around_a_role_does_not_turn_it_into_a_party(role):
    cases, _ = case_parser.parse_search(role_row(role))
    assert cases == []


def test_a_padded_defendant_is_still_read_as_a_defendant():
    """The normalizing must not swallow the roles we want to keep."""
    cases, _ = case_parser.parse_search(role_row('\r\n\tDEFENDANT\r\n\t'))
    assert ids(cases) == ['00000  FECR000000']
    assert cases[0]['role'] == 'DEFENDANT'


# -- what gets written to disk --------------------------------------------

def test_parsing_writes_nothing_to_disk_by_default(search_page, tmp_path,
                                                   monkeypatch):
    """The page holds every name and date of birth the search matched.

    Production has no reason to keep a copy, and the dyno that wrote one kept
    it for as long as it stayed up.
    """
    monkeypatch.setattr(case_parser, 'tmp_dir', str(tmp_path) + os.sep)
    monkeypatch.delenv('NAPIER_DUMP_HTML', raising=False)
    case_parser.parse_search(search_page)
    assert list(tmp_path.iterdir()) == []


def test_the_dump_is_there_when_a_developer_asks_for_it(search_page, tmp_path,
                                                        monkeypatch):
    monkeypatch.setattr(case_parser, 'tmp_dir', str(tmp_path) + os.sep)
    monkeypatch.setenv('NAPIER_DUMP_HTML', '1')
    case_parser.parse_search(search_page)
    written = [p.name for p in tmp_path.iterdir()]
    assert written == ['search_results.html']
    assert b'TESTER, PAT Q' in (tmp_path / 'search_results.html').read_bytes()


def test_a_case_id_cannot_write_outside_the_dump_directory(tmp_path,
                                                           monkeypatch):
    """Case ids come off scraped HTML and out of request forms."""
    monkeypatch.setattr(case_parser, 'tmp_dir', str(tmp_path) + os.sep)
    monkeypatch.setenv('NAPIER_DUMP_HTML', '1')
    case_parser._dump('../../etc/passwd_summary.html', '<html></html>')
    assert not (tmp_path.parent.parent / 'etc').exists()
    assert [p.name for p in tmp_path.iterdir()] == ['etc_passwd_summary.html']


# -- the 200 record notice -------------------------------------------------

# No capture of the real notice exists. ICOS only shows it for a name that
# matches more cases than it will list, and the one real search page we have
# came back with 120 rows. So these cover the shapes ICOS is known to produce
# rather than one recorded page, and the shapes are not guesses: every cell on
# the real fixture is a font tag padded with CRLFs and tabs, and the detection
# this replaced matched one exact unpadded text node and would have missed all
# of them.

def page_carrying(notice):
    return ("""<html><body>
      <table border="0" cellpadding="7">
      <tr><td><font size="2">Case ID</font></td><td><font size="2">Initiated
      Date</font></td><td><font size="2">Title</font></td><td><font size="2">
      Name</font></td><td><font size="2">DOB</font></td><td><font size="2">
      Role</font></td></tr>
      </table>
      %s
      </body></html>""" % notice).encode('utf-8')


NOTICES = [
    'Your query returned more than 200 records.',
    '<font size="2">\r\n\t\tYour query returned more than 200 records.\r\n\t\t</font>',
    '<font size="2">&nbsp;Your query returned more than 200 records.</font>',
    '<p>Your query returned more than 200 records</p>',
    '<p>Your query returned more than <b>200</b> records.</p>',
    '<td>  YOUR QUERY RETURNED MORE THAN 200 RECORDS.  </td>',
]


@pytest.mark.parametrize('notice', NOTICES)
def test_a_search_that_came_back_short_says_so(notice):
    """A list that is quietly missing cases is worse than one that admits it.

    Staff pick a client off this page and the workbook is built from what they
    picked, so a search truncated at 200 produces a criminal record summary
    that looks complete and is not.
    """
    cases, too_many = case_parser.parse_search(page_carrying(notice))
    assert too_many is True


@pytest.mark.parametrize('notice', NOTICES)
def test_the_number_is_read_off_the_page_rather_than_assumed(notice):
    soup = case_parser.BeautifulSoup(page_carrying(notice), 'html.parser')
    assert case_parser.truncation_limit(soup) == 200


def test_a_different_limit_would_still_be_read():
    """Nothing here should have to change if Iowa Courts moves the number."""
    soup = case_parser.BeautifulSoup(
        page_carrying('<font>Your query returned more than 500 records.</font>'),
        'html.parser')
    assert case_parser.truncation_limit(soup) == 500


def test_a_complete_search_is_not_called_short(search_page):
    """The real fixture is 120 rows, under the limit, and carries no notice.

    Paired with the tests above on purpose. A detection that never fires also
    passes this one on its own, which is how the broken version survived.
    """
    cases, too_many = case_parser.parse_search(search_page)
    assert too_many is False
    assert len(cases) > 0


def test_the_sentence_inside_a_script_is_not_a_notice():
    """ICOS ships its own JavaScript on these pages, alert text and all."""
    page = page_carrying(
        '<script>var msg = "Your query returned more than 200 records.";</script>')
    cases, too_many = case_parser.parse_search(page)
    assert too_many is False
