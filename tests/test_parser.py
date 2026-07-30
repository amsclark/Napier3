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
    # The INTERESTED PARTY row is dropped; the other four are parties.
    assert ids(cases) == ['00000  FECR000000', '00000  SMCR000001',
                          '00000  SCSC000002', '00000  LACV000004',
                          '00000  SRCR000005']


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
    assert grouped[pat[0]] == ['00000  FECR000000', '00000  LACV000004',
                               '00000  SRCR000005']
    assert grouped[sam[0]] == ['00000  SMCR000001']
    assert grouped[unknown[0]] == ['00000  SCSC000002']


def test_a_pro_se_defendant_is_still_a_defendant(search_page):
    cases, _ = case_parser.parse_search(search_page)
    assert '00000  SRCR000005' in ids(cases)


@pytest.mark.xfail(reason='NO ACCESS NONPARTY FILER is missing from '
                          'non_party_designations, so a case where the subject '
                          'only filed something is scraped as if they were a '
                          'party. Found on a real search page 2026-07-30; '
                          'left failing until Ari confirms it should be '
                          'suppressed.',
                   strict=True)
def test_a_nonparty_filer_is_not_a_party(search_page):
    cases, _ = case_parser.parse_search(search_page)
    assert '00000  LACV000004' not in ids(cases)


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
