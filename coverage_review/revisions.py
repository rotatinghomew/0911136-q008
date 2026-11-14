"""对比两份完整同代快照，区分新增需求与旧账更正，不做事件流增量假设。

供数端只提供完整同代记录（见 docs/data-contract.md），因此修订分析以两份
快照的家庭、成员与迁移主键为基准做对账：

* 新增需求（new_demand）：新一代里出现的家庭、新成员，或发生在旧代观测时域
  之后的新撤离事件。
* 旧账更正（old_debt_correction）：对过去记录的补登、撤回、字段修订、名册
  纠正，以及矛盾轨迹的消除。迟到登记（known_at 晚于旧代截点）归入此类并
  单独标出。

每条差异都携带 household_id / move_id 证据，并给出餐食需求在两个截点视图
（全证据视图与截点可知视图）下的变化及贡献家庭，保证汇总可追回。
"""

from datetime import datetime

from .timeline import build_dynamic_analysis, SHELTER_PREFIX, HOME

NEW_DEMAND = "new_demand"
OLD_DEBT_CORRECTION = "old_debt_correction"


def _parse(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _horizon(tables):
    endpoints = []
    for row in tables["moves"]:
        endpoints.append(_parse(row["time_min"]))
        endpoints.append(_parse(row["time_max"]))
    if not endpoints:
        return None, None
    return min(endpoints), max(endpoints)


def _move_kind(move):
    if move["from_state"] == HOME and move["to_state"].startswith(SHELTER_PREFIX):
        return "evacuation"
    if move["from_state"].startswith(SHELTER_PREFIX) and move["to_state"] == HOME:
        return "return"
    return "other"


def _issue_key(issue):
    code = issue["code"]
    if code == "identity_conflict":
        return (code, issue["person_id"], tuple(issue["household_ids"]))
    if code == "contradictory_trajectory":
        return (code, issue["household_id"], tuple(issue["move_ids"]))
    if code == "late_registration":
        return (code, issue["move_id"], issue["shelter_id"])
    if code == "lost_follow_up":
        return (code, issue["household_id"])
    if code == "unknown_shelter_reference":
        return (code, issue["move_id"])
    return (code, issue.get("move_id") or issue.get("household_id") or issue.get("person_id"))


def _item(kind, classification, detail, household_id=None, move_id=None, evidence=None):
    return {"kind": kind, "classification": classification, "detail": detail,
            "household_id": household_id, "move_id": move_id,
            "evidence": evidence or {}}


def compare_revisions(previous_tables, current_tables,
                      previous_generation=None, current_generation=None):
    previous_households = {row["household_id"]: row for row in previous_tables["households"]}
    current_households = {row["household_id"]: row for row in current_tables["households"]}
    previous_moves = {row["move_id"]: row for row in previous_tables["moves"]}
    current_moves = {row["move_id"]: row for row in current_tables["moves"]}
    prev_horizon_start, prev_horizon_end = _horizon(previous_tables)
    # 迟到与否以旧代自身的餐食截点为准，metadata.cutoff 只是供数展示截点。
    prev_meal_cutoffs = [_parse(row["meal_cutoff"]) for row in previous_tables["shelters"]]

    new_demand, corrections = [], []
    household_changes = {}

    def change_row(household_id):
        return household_changes.setdefault(household_id, {
            "household_id": household_id, "status": "persisted",
            "added_moves": [], "removed_moves": [], "changed_moves": [],
            "roster_added": [], "roster_removed": []})

    for household_id in sorted(set(current_households) - set(previous_households)):
        row = current_households[household_id]
        entry = change_row(household_id)
        entry["status"] = "new"
        new_demand.append(_item(
            "new_household", NEW_DEMAND,
            "新一代新增家庭，整体构成新增安置需求", household_id,
            evidence={"member_ids": list(row["member_ids"]),
                      "move_ids": [move["move_id"] for move in current_tables["moves"]
                                   if move["household_id"] == household_id]}))

    for household_id in sorted(set(previous_households) - set(current_households)):
        entry = change_row(household_id)
        entry["status"] = "removed"
        corrections.append(_item(
            "removed_household", OLD_DEBT_CORRECTION,
            "旧代家庭在新一代被整体撤回，属历史名册更正", household_id,
            evidence={"previous_member_ids": list(previous_households[household_id]["member_ids"])}))

    for household_id in sorted(set(previous_households) & set(current_households)):
        entry = change_row(household_id)
        prev_members = list(previous_households[household_id]["member_ids"])
        cur_members = list(current_households[household_id]["member_ids"])
        entry["roster_added"] = sorted(set(cur_members) - set(prev_members))
        entry["roster_removed"] = sorted(set(prev_members) - set(cur_members))
        for person in entry["roster_added"]:
            new_demand.append(_item(
                "roster_addition", NEW_DEMAND, "家庭名册新增稳定人员标识",
                household_id, evidence={"person_id": person}))
        for person in entry["roster_removed"]:
            corrections.append(_item(
                "roster_removal", OLD_DEBT_CORRECTION, "家庭名册撤回人员标识，属历史名册更正",
                household_id, evidence={"person_id": person}))

    for move_id in sorted(set(current_moves) - set(previous_moves)):
        move = current_moves[move_id]
        entry = change_row(move["household_id"])
        entry["added_moves"].append(move_id)
        reasons = []
        if any(_parse(move["known_at"]) > cutoff for cutoff in prev_meal_cutoffs):
            reasons.append("late_registration_after_previous_meal_cutoff")
        if _move_kind(move) == "evacuation" and (
                prev_horizon_end is None or _parse(move["time_min"]) >= prev_horizon_end):
            new_demand.append(_item(
                "new_evacuation_event", NEW_DEMAND,
                "旧代观测时域之后的新撤离事件，构成新增需求", move["household_id"], move_id,
                evidence={"reasons": reasons, "time_min": move["time_min"],
                          "time_max": move["time_max"], "known_at": move["known_at"]}))
        else:
            kind = "backfilled_evacuation_event" if _move_kind(move) == "evacuation" \
                else "backfilled_event"
            corrections.append(_item(
                kind, OLD_DEBT_CORRECTION,
                "补登发生在旧代观测时域内或非撤离类迁移，修正历史在住账目",
                move["household_id"], move_id,
                evidence={"reasons": reasons, "from_state": move["from_state"],
                          "to_state": move["to_state"], "time_min": move["time_min"],
                          "time_max": move["time_max"], "known_at": move["known_at"]}))

    for move_id in sorted(set(previous_moves) - set(current_moves)):
        move = previous_moves[move_id]
        entry = change_row(move["household_id"])
        entry["removed_moves"].append(move_id)
        corrections.append(_item(
            "retracted_event", OLD_DEBT_CORRECTION,
            "旧代迁移事件在新一代被撤回，属历史记录更正", move["household_id"], move_id,
            evidence={"previous_from_state": move["from_state"],
                      "previous_to_state": move["to_state"],
                      "previous_time_min": move["time_min"],
                      "previous_time_max": move["time_max"]}))

    tracked_fields = ("from_state", "to_state", "time_min", "time_max", "known_at")
    for move_id in sorted(set(previous_moves) & set(current_moves)):
        previous, current = previous_moves[move_id], current_moves[move_id]
        changed = {field: {"previous": previous[field], "current": current[field]}
                   for field in tracked_fields if previous[field] != current[field]}
        if changed:
            entry = change_row(current["household_id"])
            entry["changed_moves"].append({"move_id": move_id, "fields": changed})
            corrections.append(_item(
                "amended_event", OLD_DEBT_CORRECTION,
                "同一事件主键的字段被修订，属旧账更正", current["household_id"], move_id,
                evidence={"changed_fields": changed}))

    previous_analysis = build_dynamic_analysis(previous_tables)
    current_analysis = build_dynamic_analysis(current_tables)
    meal_delta = _meal_delta(previous_analysis, current_analysis)
    issue_changes = _issue_changes(previous_analysis, current_analysis)

    return {
        "scope": "两代完整快照对账：新增需求与旧账更正分开列示，所有差异可追回家庭与事件",
        "generations": {"previous": previous_generation, "current": current_generation},
        "previous_horizon": {"start": prev_horizon_start.isoformat() if prev_horizon_start else None,
                             "end": prev_horizon_end.isoformat() if prev_horizon_end else None},
        "classification": {
            "new_demand": new_demand,
            "old_debt_corrections": corrections,
            "counts": {NEW_DEMAND: len(new_demand),
                       OLD_DEBT_CORRECTION: len(corrections)},
        },
        "household_changes": dict(sorted(household_changes.items())),
        "meal_demand_delta": meal_delta,
        "issue_changes": issue_changes,
        "trace": _revision_trace(new_demand, corrections, household_changes),
    }


def _meal_delta(previous_analysis, current_analysis):
    rows = []
    prev_meals = {item["shelter_id"]: item for item in previous_analysis["meal_planning"]}
    for current_meal in current_analysis["meal_planning"]:
        previous_meal = prev_meals.get(current_meal["shelter_id"])
        for view_name in ("full_evidence_view", "as_of_cutoff_knowledge_view"):
            current_view = current_meal[view_name]
            previous_view = previous_meal[view_name] if previous_meal else None
            if previous_view is None or previous_view["counts"] is None or current_view["counts"] is None:
                rows.append({"shelter_id": current_meal["shelter_id"], "view": view_name,
                             "comparable": False,
                             "reason": "截点落在某一代观测时域之外，人数不具可比性"})
                continue
            before, after = previous_view["counts"], current_view["counts"]
            rows.append({
                "shelter_id": current_meal["shelter_id"], "view": view_name,
                "comparable": True,
                "definite_unique_persons": {
                    "previous": before["definite_unique_persons"],
                    "current": after["definite_unique_persons"],
                    "delta": after["definite_unique_persons"] - before["definite_unique_persons"]},
                "definite_persons_roster": {
                    "previous": before["definite_persons_roster"],
                    "current": after["definite_persons_roster"],
                    "delta": after["definite_persons_roster"] - before["definite_persons_roster"]},
                "possible_unique_persons_range": {
                    "previous": before["possible_unique_persons_range"],
                    "current": after["possible_unique_persons_range"],
                    "lower_delta": after["possible_unique_persons_range"][0]
                                   - before["possible_unique_persons_range"][0],
                    "upper_delta": after["possible_unique_persons_range"][1]
                                   - before["possible_unique_persons_range"][1]},
                "possible_persons_roster_range": {
                    "previous": before["possible_persons_roster_range"],
                    "current": after["possible_persons_roster_range"]},
                "definite_household_membership": {
                    "added": sorted(set(after["definite_households"]) - set(before["definite_households"])),
                    "removed": sorted(set(before["definite_households"]) - set(after["definite_households"]))},
                "possible_household_membership": {
                    "added": sorted(set(after["possible_households"]) - set(before["possible_households"])),
                    "removed": sorted(set(before["possible_households"]) - set(after["possible_households"]))},
            })
    return rows


def _issue_changes(previous_analysis, current_analysis):
    previous_keys = {_issue_key(issue): issue for issue in previous_analysis["issues"]}
    current_keys = {_issue_key(issue): issue for issue in current_analysis["issues"]}
    return {
        "introduced": [current_keys[key] for key in sorted(set(current_keys) - set(previous_keys))],
        "resolved": [previous_keys[key] for key in sorted(set(previous_keys) - set(current_keys))],
        "counts": {
            "introduced": len(set(current_keys) - set(previous_keys)),
            "resolved": len(set(previous_keys) - set(current_keys))},
    }


def _revision_trace(new_demand, corrections, household_changes):
    index = {}
    for item in new_demand + corrections:
        household_id = item["household_id"]
        if household_id is None:
            continue
        bucket = index.setdefault(household_id, {"new_demand_items": [],
                                                 "old_debt_correction_items": [],
                                                 "move_ids": []})
        key = "new_demand_items" if item["classification"] == NEW_DEMAND \
            else "old_debt_correction_items"
        bucket[key].append(item["move_id"] or item["kind"])
        if item["move_id"]:
            bucket["move_ids"].append(item["move_id"])
    for household_id, entry in household_changes.items():
        bucket = index.setdefault(household_id, {"new_demand_items": [],
                                                 "old_debt_correction_items": [],
                                                 "move_ids": []})
        bucket["move_ids"] = sorted(set(bucket["move_ids"]) |
                                    set(entry["added_moves"]) | set(entry["removed_moves"]) |
                                    {row["move_id"] for row in entry["changed_moves"]})
    return dict(sorted(index.items()))
