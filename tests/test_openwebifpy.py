"""Tests the api."""
# pylint: disable=protected-access
import unittest

import openwebif.api


class TestAPI(unittest.TestCase):
    """ Tests openwebif.api module. """

    def test_create(self):
        """ Test creating a new device. """
        # Bogus config
        self.assertRaises(TypeError, lambda: openwebif.api.OpenWebIfDevice())

    def test_get_picon_name(self):
        self.assertEqual(openwebif.api.CreateDevice.get_picon_name('RTÉ One'), "rteone")

