import unittest

from coverage_review.populations import HouseholdReceipt, observed


class PopulationTests(unittest.TestCase):
    def test_unknown_receipt_is_not_an_unacknowledged_household(self):
        self.assertFalse(observed(HouseholdReceipt("h1", "v1", True, None)))
