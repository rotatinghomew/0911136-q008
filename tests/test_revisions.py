"""验证资料修订后新增需求与旧账更正分开列示且可追回。"""
import json
import unittest
from pathlib import Path

from coverage_review.revisions import compare_revisions, NEW_DEMAND, OLD_DEBT_CORRECTION


ROOT = Path(__file__).resolve().parents[1]


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.previous = json.loads((ROOT / "fixtures/dataset.json").read_text(encoding="utf-8"))
        self.current = json.loads((ROOT / "fixtures/dataset-revised.json").read_text(encoding="utf-8"))
        self.revision = compare_revisions(
            self.previous["tables"], self.current["tables"],
            previous_generation=self.previous["generation"],
            current_generation=self.current["generation"])

    def test_generations_are_recorded(self):
        self.assertEqual(self.revision["generations"],
                         {"previous": "domain-8-v1", "current": "domain-8-v2"})

    def test_new_household_and_later_evacuation_are_new_demand(self):
        new_items = self.revision["classification"]["new_demand"]
        kinds = {(item["kind"], item["household_id"], item["move_id"]) for item in new_items}
        self.assertIn(("new_household", "h10", None), kinds)
        self.assertIn(("new_evacuation_event", "h10", "m10-a"), kinds)
        self.assertTrue(all(item["classification"] == NEW_DEMAND for item in new_items))

    def test_late_backfill_and_amendments_are_old_debt_corrections(self):
        corrections = self.revision["classification"]["old_debt_corrections"]
        by_move = {item["move_id"]: item for item in corrections}
        self.assertIn("m0-b", by_move)
        self.assertEqual(by_move["m0-b"]["classification"], OLD_DEBT_CORRECTION)
        self.assertIn("late_registration_after_previous_meal_cutoff",
                      by_move["m0-b"]["evidence"]["reasons"])
        amended = {item["move_id"] for item in corrections if item["kind"] == "amended_event"}
        self.assertEqual(amended, {"m1-b", "m1-c"})

    def test_contradiction_resolution_and_new_quality_issues_are_visible(self):
        changes = self.revision["issue_changes"]
        resolved = {(issue["code"], issue["household_id"]) for issue in changes["resolved"]}
        self.assertIn(("contradictory_trajectory", "h1"), resolved)
        introduced = {(issue["code"], issue.get("move_id")) for issue in changes["introduced"]}
        self.assertIn(("late_registration", "m0-b"), introduced)

    def test_meal_delta_distinguishes_full_evidence_from_as_of_knowledge(self):
        rows = {row["view"]: row for row in self.revision["meal_demand_delta"]}
        full = rows["full_evidence_view"]
        as_of = rows["as_of_cutoff_knowledge_view"]
        # 全证据：迟到返家使 h0 移出可能在住，上界少 2 个去重人员。
        self.assertEqual(full["possible_unique_persons_range"]["previous"], [0, 21])
        self.assertEqual(full["possible_unique_persons_range"]["current"], [0, 19])
        self.assertEqual(full["possible_unique_persons_range"]["upper_delta"], -2)
        self.assertEqual(full["possible_household_membership"]["removed"], ["h0"])
        # 截点可知：迟到登记当时不可见，排餐账目不变。
        self.assertEqual(as_of["possible_unique_persons_range"]["upper_delta"], 0)
        self.assertEqual(as_of["possible_household_membership"]["removed"], [])

    def test_every_revision_item_carries_traceable_household_and_moves(self):
        for item in (self.revision["classification"]["new_demand"]
                     + self.revision["classification"]["old_debt_corrections"]):
            self.assertIsNotNone(item["household_id"])
        trace = self.revision["trace"]
        self.assertEqual(set(trace["h10"]["move_ids"]), {"m10-a"})
        self.assertEqual(trace["h10"]["new_demand_items"], ["new_household", "m10-a"])
        self.assertIn("m0-b", trace["h0"]["old_debt_correction_items"])

    def test_retracted_event_is_old_debt_correction(self):
        current = {
            "generation": "domain-8-v3",
            "tables": json.loads(json.dumps(self.current["tables"])),
        }
        current["tables"]["moves"] = [
            row for row in current["tables"]["moves"] if row["move_id"] != "m10-a"]
        revision = compare_revisions(self.current["tables"], current["tables"],
                                     "domain-8-v2", "domain-8-v3")
        retracted = [item for item in revision["classification"]["old_debt_corrections"]
                     if item["kind"] == "retracted_event"]
        self.assertEqual(len(retracted), 1)
        self.assertEqual(retracted[0]["move_id"], "m10-a")
        self.assertIn("m10-a", revision["trace"]["h10"]["move_ids"])

    def test_roster_addition_is_new_demand_removal_is_correction(self):
        current = {
            "generation": "domain-8-v4",
            "tables": json.loads(json.dumps(self.current["tables"])),
        }
        h0 = next(row for row in current["tables"]["households"] if row["household_id"] == "h0")
        h0["member_ids"].append("person-99")
        h5 = next(row for row in current["tables"]["households"] if row["household_id"] == "h5")
        h5["member_ids"].remove("person-17")
        revision = compare_revisions(self.current["tables"], current["tables"],
                                     "domain-8-v2", "domain-8-v4")
        kinds = {(item["kind"], item["household_id"], item["classification"])
                 for item in (revision["classification"]["new_demand"]
                              + revision["classification"]["old_debt_corrections"])}
        self.assertIn(("roster_addition", "h0", NEW_DEMAND), kinds)
        self.assertIn(("roster_removal", "h5", OLD_DEBT_CORRECTION), kinds)


if __name__ == "__main__":
    unittest.main()
