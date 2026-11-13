"""验证现有领域摘要的业务边界。"""
import copy
import json
import unittest
from pathlib import Path

from coverage_review.analysis import household_profile


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tables = json.loads((Path(__file__).resolve().parents[1]/'fixtures/dataset.json').read_text())['tables']

    def test_repeated_member_is_counted_once_and_reported(self):
        result = household_profile(self.tables)
        self.assertEqual(30,result['unique_people'])
        self.assertEqual(['h0','h9'],result['identity_conflicts']['person-0'])

    def test_repeat_moves_are_not_collapsed(self):
        self.assertEqual(3,len(household_profile(self.tables)['household_moves']['h1']))

    def test_unknown_household_cannot_be_invented(self):
        tables = copy.deepcopy(self.tables)
        tables['moves'][0]['household_id'] = 'absent'
        with self.assertRaises(ValueError):
            household_profile(tables)

    def test_uncertain_interval_direction_is_checked(self):
        tables = copy.deepcopy(self.tables)
        tables['moves'][0]['time_max'] = '2020-01-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            household_profile(tables)
