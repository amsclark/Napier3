"""Shared setup for the whole suite.

The account registry is module-level state, the way the job store and the
session store are, and it is the only one of the three a test can leave dirty
without noticing. A client that signs in and is then abandoned, which is most
of the clients in these files, holds its entry until something logs it off. In
the app that always happens, because every run logs off in a finally. Here it
does not, and a leftover entry changes what the next test's sign in is told.

So each test starts with an empty registry, and a test that wants an account
held says so itself.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts


@pytest.fixture(autouse=True)
def _empty_account_registry():
    accounts._reset()
    yield
    accounts._reset()
