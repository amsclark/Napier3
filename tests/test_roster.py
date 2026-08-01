"""Reading a clinic list off whatever staff paste in.

The names here are invented. The shapes they are in are not: a paste out of a
spreadsheet brings its heading row and its date of birth column with it, and a
list assembled from two sources has the same client on it twice.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import roster


def names(text):
    people, _ = roster.parse(text)
    return [roster.describe(person) for person in people]


class TestSplitName:
    def test_comma_form(self):
        assert roster.split_name("Doe, Jane") == ('Jane', '', 'Doe')

    def test_comma_form_with_a_middle_name(self):
        assert roster.split_name("Doe, Jane Marie") == ('Jane', 'Marie', 'Doe')

    def test_a_two_word_surname_survives_the_comma_form(self):
        # The only form that can carry one without guessing, which is why the
        # placeholder on the page asks for it.
        assert roster.split_name("Van Dyke, Jane") == ('Jane', '', 'Van Dyke')

    def test_space_form(self):
        assert roster.split_name("Jane Doe") == ('Jane', '', 'Doe')

    def test_space_form_middle_is_everything_between(self):
        assert roster.split_name("Jane Marie Ann Doe") == (
            'Jane', 'Marie Ann', 'Doe')

    def test_a_surname_on_its_own_is_a_surname(self):
        # Iowa Courts will answer a surname-only search, and the roster page is
        # where somebody picks their client out of the answer.
        assert roster.split_name("Doe") == ('', '', 'Doe')

    def test_a_third_comma_column_is_not_part_of_the_given_names(self):
        assert roster.split_name("Doe, Jane, 01/01/1900") == ('Jane', '', 'Doe')


class TestParse:
    def test_a_plain_list(self):
        assert names("Doe, Jane\nRoe, John") == ['Jane Doe', 'John Roe']

    def test_blank_lines_are_not_clients(self):
        assert names("\n\nDoe, Jane\n\n\n") == ['Jane Doe']

    def test_both_forms_on_one_list(self):
        assert names("Doe, Jane\nJohn Roe") == ['Jane Doe', 'John Roe']

    def test_a_tab_separated_paste_keeps_only_the_name(self):
        assert names("Doe, Jane\t01/01/1900\t10:30 AM") == ['Jane Doe']

    def test_a_pipe_or_semicolon_column_goes_the_same_way(self):
        assert names("Doe, Jane | 01/01/1900\nRoe, John; 2pm") == [
            'Jane Doe', 'John Roe']

    def test_padding_is_squashed(self):
        assert names("   Doe,    Jane   Marie  ") == ['Jane Marie Doe']

    def test_the_same_client_twice_is_searched_once(self):
        # A list assembled from two sources has duplicates, and searching one
        # twice costs a minute of a shared Iowa Courts account for an answer we
        # already have.
        assert names("Doe, Jane\nDoe, Jane") == ['Jane Doe']

    def test_the_duplicate_check_ignores_case(self):
        assert names("Doe, Jane\nDOE, JANE") == ['Jane Doe']

    def test_two_clients_who_differ_by_a_middle_name_are_two_clients(self):
        assert names("Doe, Jane\nDoe, Jane Marie") == ['Jane Doe',
                                                       'Jane Marie Doe']

    def test_order_is_the_order_it_was_pasted(self):
        assert names("Roe, John\nDoe, Jane\nPoe, Alex") == [
            'John Roe', 'Jane Doe', 'Alex Poe']


class TestRejected:
    def _rejected(self, text):
        _, rejected = roster.parse(text)
        return rejected

    def test_a_heading_row_is_reported_not_searched(self):
        people, rejected = roster.parse("Client Name\tDOB\nDoe, Jane")
        assert [roster.describe(person) for person in people] == ['Jane Doe']
        assert rejected == ['Client Name\tDOB']

    def test_the_other_headings_a_paste_brings_along(self):
        for heading in ("Name", "First Last", "Last, First", "Defendant",
                        "Participant:", "client"):
            assert self._rejected(heading) == [heading], heading

    def test_a_line_with_no_name_in_it_is_reported(self):
        assert self._rejected(", 01/01/1900") == [', 01/01/1900']

    def test_a_rejected_line_does_not_end_the_list(self):
        # Twenty clients pasted, one unreadable line, nineteen still get seen
        # that day.
        assert names("Client Name\nDoe, Jane\n, \nRoe, John") == [
            'Jane Doe', 'John Roe']

    def test_nothing_readable_is_no_people_and_no_exception(self):
        people, rejected = roster.parse("Name\nDOB")
        assert people == []
        assert rejected == ['Name', 'DOB']

    def test_an_empty_paste(self):
        assert roster.parse('') == ([], [])
        assert roster.parse(None) == ([], [])
