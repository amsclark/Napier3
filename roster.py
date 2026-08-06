"""A clinic's list of clients, read off whatever staff paste in.

Napier has always been one client per sign in. Iowa Courts allows one session
per account and Iowa Legal Aid shares a handful of them, so a clinic with
twenty names on the list is twenty sign ins, twenty locked-account waits when
two people work the list at once, and twenty runs somebody has to sit and watch.
The searching is the same work each time; only the name changes.

So this reads a pasted list. It has to survive a paste out of a spreadsheet, a
Word table, or an email, which means blank lines, a header row, tabs, and a
date of birth column nobody meant to include. Anything it cannot make a name
out of comes back as a rejected line rather than being dropped quietly, because
a client missing from a clinic list is a client nobody sees that day.
"""

# A clinic list, not a mailing list. Past this the run holds the shared ICOS
# account for most of an afternoon, and a paste this big is nearly always a
# whole spreadsheet rather than a day's clients.
#
# Counted in searches rather than in clients, because what it is protecting is
# the time the account is held and a client with an aka costs two searches.
MAX_NAMES = 40

# Words a header row starts with. A pasted table brings its headings along and
# "Client Name" is not a person. "dob" is here because a table pasted one column
# per line puts it on a line of its own, where it otherwise reads as a
# surname-only search, which is a real thing staff do on purpose. "aka" and
# "alias" are here for the same reason: a spreadsheet with a spellings column
# brings that heading along too.
HEADERS = ('name', 'client', 'first', 'last', 'defendant', 'participant',
           'dob', 'aka', 'alias', 'akas', 'aliases')

# Anything after the name in a pasted row: a date of birth, a case number, a
# phone number, an appointment time.
SEPARATORS = ('\t', '|', ';')

# How a clinic list writes "the docket also spells them this way". Iowa Courts
# matches the name exactly as it appears on the case, so a client with two
# spellings used to be two lines on the list, two searches somebody ran on
# purpose and two workbooks they merged by hand.
ALIAS_MARKERS = (' aka ', ' a/k/a ', ' a.k.a. ', ' also known as ')


def _clean(line):
    """One pasted line down to just the name part."""
    for separator in SEPARATORS:
        line = line.split(separator)[0]
    return ' '.join(line.split())


def _is_header(name):
    first = name.split(',')[0].split()
    return bool(first) and first[0].lower().strip(':') in HEADERS


def split_name(name):
    """A name into first, middle and last, however it was written.

    Two forms, because clinic lists come both ways. With a comma, everything
    before it is the surname, which is the only form that can carry a surname
    of more than one word without guessing. Without one, the first word is the
    first name, the last word is the surname and anything between is the
    middle, so "Jane Marie Van Dyke" comes out with a surname of "Dyke" and has
    to be written "Van Dyke, Jane Marie" to search properly.
    """
    if ',' in name:
        surname, _, rest = name.partition(',')
        # A third column is a date of birth or a case number often enough that
        # taking it as part of the given names would break the search.
        rest = rest.split(',')[0]
        given = rest.split()
        return (given[0] if given else '',
                ' '.join(given[1:]),
                ' '.join(surname.split()))

    words = name.split()
    if len(words) == 1:
        # A surname on its own is a real search. Iowa Courts will answer it,
        # and the roster page is where somebody picks out of the answer.
        return '', '', words[0]
    return words[0], ' '.join(words[1:-1]), words[-1]


def split_spellings(name):
    """One cleaned line into the spellings it names, primary first.

    "Al Hameed, Ali aka Alhameed, Ali" is one client and two searches. Anything
    that does not carry a marker comes back as a list of one, which is what
    every line on every clinic list written so far is.
    """
    parts = [name]
    for marker in ALIAS_MARKERS:
        parts = [piece
                 for part in parts
                 for piece in _split_on(part, marker)]
    return [' '.join(part.split()) for part in parts if part.split()]


def _split_on(text, marker):
    """Split on a marker however it was capitalised, keeping the text as typed."""
    pieces, lowered, start = [], text.lower(), 0
    while True:
        at = lowered.find(marker, start)
        if at == -1:
            pieces.append(text[start:])
            return pieces
        pieces.append(text[start:at])
        start = at + len(marker)


def parse(text):
    """A pasted list into (people, rejected).

    people are dicts of raw, first, middle and last, plus the alternate
    spellings of the same client under 'aliases'. rejected are the lines that
    carried no name, reported back rather than swallowed so that a list of
    twenty that produced nineteen searches says which one it dropped.

    Duplicates are folded together. A clinic list assembled from two sources
    has them, and searching the same name twice costs a minute of a shared
    account for an answer we already have. A spelling that repeats the primary,
    or repeats another spelling on the same line, goes the same way.
    """
    people, rejected, seen = [], [], set()
    for line in (text or '').splitlines():
        name = _clean(line)
        if not name:
            continue
        if _is_header(name):
            # Reported rather than dropped. A line Napier decided was a heading
            # and a line it could not read are both lines it did not search,
            # and the page has to say so either way.
            rejected.append(line.strip())
            continue

        spellings, on_this_line = [], set()
        for spelling in split_spellings(name):
            first, middle, last = split_name(spelling)
            if not last:
                continue
            key = (first.lower(), middle.lower(), last.lower())
            if key in on_this_line:
                continue
            on_this_line.add(key)
            spellings.append({'raw': spelling, 'first': first,
                              'middle': middle, 'last': last})
        if not spellings:
            rejected.append(line.strip())
            continue

        # Against the primary only. A client already on the list under one
        # spelling should not be searched twice, but two clients who share an
        # aka are still two clients.
        primary = spellings[0]
        key = (primary['first'].lower(), primary['middle'].lower(),
               primary['last'].lower())
        if key in seen:
            continue
        seen.add(key)
        person = dict(primary)
        person['raw'] = name
        person['aliases'] = spellings[1:]
        people.append(person)
    return people, rejected


def spellings(person):
    """Every name to search for this client, primary first.

    The shape a search wants, and the one thing that has to agree with
    searches_count below: the account is held for as long as this list is long.
    """
    names = [{'first': person['first'], 'middle': person['middle'],
              'last': person['last']}]
    for alias in person.get('aliases') or []:
        names.append({'first': alias['first'], 'middle': alias['middle'],
                      'last': alias['last']})
    return names


def searches_count(people):
    """How many searches this list is, which is not how many clients it is."""
    return sum(len(spellings(person)) for person in people)


def describe(person):
    """The name as Napier will search it, for the page that asks staff to check."""
    said = ' '.join(part for part in
                    (person['first'], person['middle'], person['last']) if part)
    aliases = person.get('aliases') or []
    if aliases:
        said += " (also %s)" % ", ".join(
            ' '.join(part for part in
                     (alias['first'], alias['middle'], alias['last']) if part)
            for alias in aliases)
    return said
