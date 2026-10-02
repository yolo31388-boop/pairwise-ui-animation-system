"""The shipped test classes use unittest assertions without subclassing
unittest.TestCase. Inject the plain assertion helpers so they work under
pytest without modifying the test files.
"""

import unittest

from tests.test_ui_animation import TestUIAnimationSystem

_HELPERS = (
    "assertIsNotNone",
    "assertIsNone",
    "assertEqual",
    "assertTrue",
    "assertFalse",
)

for _name in _HELPERS:
    setattr(TestUIAnimationSystem, _name,
            getattr(unittest.TestCase, _name))
