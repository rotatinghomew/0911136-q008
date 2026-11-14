"""验证动态在住规模、往返、暴露与质量问题的业务边界。

核心原则：不补造唯一轨迹，所有不确定与矛盾必须可见，失联只给有依据的范围。
"""
import copy
import json
import unittest
from pathlib import Path

from coverage_review.timeline import (
    build_dynamic_analysis, trace_household, ISSUE_CONTRADICTION,
    ISSUE_LATE, ISSUE_LOST, ISSUE_UNCERTAIN, ISSUE_IDENTITY)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/dataset.json"


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.tables = json.loads(FIXTURE.read_text(encoding="utf-8"))["tables"]
        self.analysis = build_dynamic_analysis(self.tables)

    def test_repeated_evacuation_household_counted_once_per_segment(self):
        # h1 有两次撤离事件，但每个时段在住集合里只出现一次，不累计成两户。
        for segment in self.analysis["occupancy_timeline"]:
            bucket = segment["shelters"].get("a")
            if bucket is None:
                continue
            self.assertEqual(len(bucket["possible_households"]),
                             len(set(bucket["possible_households"])))
        # 矛盾时段（08:00 之后事件顺序不可判定）不允许出现确定在住。
        for segment in self.analysis["households"]["h1"]["segments"]:
            if segment["start"] >= "2025-06-02T08:00:00+00:00":
                self.assertIsNone(segment["definite_state"])
        self.assertEqual(
            self.analysis["round_trip_totals"]["per_household"]["h1"],
            {"evacuations": 2, "returns": 1,
             "evacuation_moves": ["m1-a", "m1-c"], "return_moves": ["m1-b"]})

    def test_uncertain_interval_never_resolves_to_single_route(self):
        # 迁移窗口内（含右端点，因具体时刻未知）只给可能集合，确定状态为 None。
        during = next(segment for segment in self.analysis["households"]["h0"]["segments"]
                      if segment["start"].endswith("T16:01:00+00:00"))
        self.assertIsNone(during["definite_state"])
        self.assertEqual(during["possible_states"], ["home", "shelter:a"])
        right_edge = next(segment for segment in self.analysis["households"]["h0"]["segments"]
                          if segment["start"].endswith("T17:00:00+00:00"))
        self.assertIsNone(right_edge["definite_state"])
        self.assertEqual(right_edge["possible_states"], ["home", "shelter:a"])
        # h0 之后再无事件：末状态不延续为确定事实，只保留可能状态。
        tail = next(segment for segment in self.analysis["households"]["h0"]["segments"]
                    if segment["start"].endswith("T17:02:00+00:00"))
        self.assertIsNone(tail["definite_state"])
        self.assertEqual(tail["possible_states"], ["shelter:a"])
        self.assertTrue(any(issue["code"] == ISSUE_UNCERTAIN and issue["move_id"] == "m0-a"
                            for issue in self.analysis["issues"]))

    def test_household_with_followup_event_keeps_definite_state_between_events(self):
        # h2 有后续返家事件，因此撤离完成后、返家窗口之前的时段是确定在安置点。
        settled = next(segment for segment in self.analysis["households"]["h2"]["segments"]
                       if segment["start"].endswith("T17:09:00+00:00"))
        self.assertEqual(settled["definite_state"], "shelter:a")

    def test_contradictory_overlap_keeps_both_states_visible(self):
        h1 = self.analysis["households"]["h1"]
        contradictory_segments = [
            segment for segment in h1["segments"]
            if segment["start"] >= "2025-06-02T08:00:00+00:00"
            and segment["start"] < "2025-06-02T10:00:00+00:00"]
        self.assertTrue(contradictory_segments)
        for segment in contradictory_segments:
            self.assertIsNone(segment["definite_state"])
            self.assertEqual(segment["possible_states"], ["home", "shelter:a"])
        contradictions = [issue for issue in self.analysis["issues"]
                          if issue["code"] == ISSUE_CONTRADICTION]
        self.assertEqual({issue["household_id"] for issue in contradictions},
                         {"h1", "h4", "h7"})

    def test_chain_break_is_flagged_as_contradiction(self):
        tables = copy.deepcopy(self.tables)
        # m2-b 的起点与 m2-a 的终点不能衔接，形成断链，不自动猜测中间状态。
        m2b = next(row for row in tables["moves"] if row["move_id"] == "m2-b")
        m2b["from_state"] = "other"
        analysis = build_dynamic_analysis(tables)
        flagged = [issue for issue in analysis["issues"]
                   if issue["code"] == ISSUE_CONTRADICTION and issue["household_id"] == "h2"]
        self.assertEqual(flagged[0]["move_ids"], ["m2-a", "m2-b"])

    def test_lost_follow_up_uses_bounded_range_not_extended_state(self):
        h9 = self.analysis["households"]["h9"]
        self.assertEqual(h9["lost_follow_up"]["person_count_range"], [0, 4])
        self.assertEqual(h9["lost_follow_up"]["last_observed_state"], "shelter:a")
        tail = h9["segments"][-1]
        self.assertIsNone(tail["definite_state"])
        self.assertEqual(tail["possible_states"], ["shelter:a"])
        self.assertTrue(any(issue["code"] == ISSUE_LOST and issue["household_id"] == "h9"
                            for issue in self.analysis["issues"]))

    def test_household_without_any_moves_is_lost_without_invented_state(self):
        tables = copy.deepcopy(self.tables)
        tables["households"].append({
            "household_id": "h-quiet", "member_ids": ["p-x"], "home_village": "v0"})
        analysis = build_dynamic_analysis(tables)
        quiet = analysis["households"]["h-quiet"]
        self.assertEqual(quiet["segments"], [])
        self.assertEqual(quiet["lost_follow_up"]["person_count_range"], [0, 1])
        self.assertIsNone(quiet["lost_follow_up"]["last_observed_state"])

    def test_risk_exposure_uses_interval_bounds(self):
        # v1 中所有返家窗口右端都是时段边界，确定 home 时长下界为 0；上界覆盖
        # 16:02→次日11:00（18060s，含不确定窗口右端的可能 home）。
        exposure = self.analysis["households"]["h2"]["risk_exposure_seconds"]
        self.assertEqual(exposure["max"], 18_060)
        self.assertEqual(exposure["min"], 0)
        self.assertEqual(exposure["risk_ids"], ["r2"])

    def test_risk_exposure_lower_bound_uses_only_definite_home(self):
        # 自包含小数据集：返家在 09:00 前确定完成，家庭最后一条事件延到风险窗
        # 之后（13:00），因此 09:00→12:00 是确定 home，下界 10800s；上界还包含
        # 撤离窗口右端的可能 home，严格大于下界。
        tables = {
            "households": [{"household_id": "hx", "member_ids": ["p1", "p2"],
                            "home_village": "vx"}],
            "moves": [
                {"move_id": "x-a", "household_id": "hx", "from_state": "home",
                 "to_state": "shelter:a", "time_min": "2025-06-01T16:00:00+00:00",
                 "time_max": "2025-06-01T17:00:00+00:00",
                 "known_at": "2025-06-01T18:00:00+00:00"},
                {"move_id": "x-b", "household_id": "hx", "from_state": "shelter:a",
                 "to_state": "home", "time_min": "2025-06-02T08:00:00+00:00",
                 "time_max": "2025-06-02T09:00:00+00:00",
                 "known_at": "2025-06-02T10:00:00+00:00"},
                {"move_id": "x-check", "household_id": "hx", "from_state": "home",
                 "to_state": "home", "time_min": "2025-06-02T10:00:00+00:00",
                 "time_max": "2025-06-02T10:00:00+00:00",
                 "known_at": "2025-06-02T10:05:00+00:00"},
                {"move_id": "x-c", "household_id": "hx", "from_state": "home",
                 "to_state": "other", "time_min": "2025-06-02T13:00:00+00:00",
                 "time_max": "2025-06-02T13:30:00+00:00",
                 "known_at": "2025-06-02T14:00:00+00:00"},
            ],
            "shelters": [{"shelter_id": "a",
                          "meal_cutoff": "2025-06-02T11:00:00+00:00", "capacity": 50}],
            "risk_windows": [{"risk_id": "rx", "village_id": "vx",
                              "start": "2025-06-01T14:00:00+00:00",
                              "end": "2025-06-02T12:00:00+00:00"}],
        }
        analysis = build_dynamic_analysis(tables)
        exposure = analysis["households"]["hx"]["risk_exposure_seconds"]
        # 确定 home 段 10:00→12:00（风险窗截止）= 7200s；上界还含更早的可能 home。
        self.assertEqual(exposure["min"], 7_200)
        self.assertGreater(exposure["max"], exposure["min"])
        self.assertEqual(exposure["risk_ids"], ["rx"])
        definite_home = [s for s in analysis["households"]["hx"]["segments"]
                         if s["definite_state"] == "home"]
        self.assertTrue(definite_home)

    def test_meal_views_separate_late_registration_from_full_evidence(self):
        meal = self.analysis["meal_planning"][0]
        full = meal["full_evidence_view"]["counts"]
        as_of = meal["as_of_cutoff_knowledge_view"]["counts"]
        # 截点 11:00 时 9 条 12:00 才可知的记录在 as-of 视图被排除。
        late_moves = meal["as_of_cutoff_knowledge_view"]["excluded_move_ids"]
        self.assertEqual(len(late_moves), 9)
        self.assertIn("m2-b", late_moves)
        self.assertEqual(full["possible_unique_persons_range"], [0, 21])
        self.assertEqual(as_of["possible_unique_persons_range"], [0, 30])
        # 排餐名册按家庭人数求和，身份冲突使名册口径高于去重口径。
        self.assertEqual(full["possible_persons_roster_range"], [0, 22])
        self.assertEqual(full["identity_conflict_persons"], ["person-0"])
        self.assertTrue(any(issue["code"] == ISSUE_LATE and issue["move_id"] == "m2-b"
                            for issue in self.analysis["issues"]))

    def test_identity_conflict_is_visible_in_counts_and_issues(self):
        self.assertTrue(any(issue["code"] == ISSUE_IDENTITY and issue["person_id"] == "person-0"
                            for issue in self.analysis["issues"]))
        first = next(segment for segment in self.analysis["occupancy_timeline"]
                     if segment["shelters"])
        bucket = first["shelters"]["a"]
        self.assertTrue(bucket["identity_conflict_present"])
        self.assertIn("person-0", bucket["identity_conflict_persons"])

    def test_every_aggregate_traces_back_to_households_and_moves(self):
        traced = trace_household(self.analysis, "h1")
        self.assertEqual(traced["household"]["move_ids"],
                         ["m1-a", "m1-b", "m1-c"])
        self.assertIn("meal_planning.a.full_evidence_view", traced["trace"]["appears_in"])
        self.assertIn("round_trip_totals", traced["trace"]["appears_in"])
        self.assertTrue(any(issue["code"] == ISSUE_CONTRADICTION
                            for issue in traced["supporting_issues"]))
        # 汇总中的家庭都能在溯源索引中追回。
        for segment in self.analysis["occupancy_timeline"]:
            for bucket in segment["shelters"].values():
                for household_id in bucket["possible_households"]:
                    self.assertIn(household_id, self.analysis["trace_index"])

    def test_roster_is_preserved_with_household_indivisibility(self):
        self.assertEqual(self.analysis["households"]["h0"]["members"],
                         ["person-0", "person-1", "person-2"])
        for section in self.analysis["households"].values():
            self.assertEqual(section["household_size"], len(section["members"]))

    def test_unknown_shelter_reference_is_reported_not_invented(self):
        tables = copy.deepcopy(self.tables)
        tables["moves"][0]["to_state"] = "shelter:ghost"
        analysis = build_dynamic_analysis(tables)
        flagged = [issue for issue in analysis["issues"]
                   if issue["code"] == "unknown_shelter_reference"]
        self.assertEqual(flagged[0]["shelter_id"], "ghost")
        # 未登记安置点不会出现在在住时间线里。
        self.assertTrue(all("ghost" not in segment["shelters"]
                            for segment in analysis["occupancy_timeline"]))


if __name__ == "__main__":
    unittest.main()
