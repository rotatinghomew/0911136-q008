"""从家庭迁移事件构建随时段变化的在住规模、往返与风险暴露，不补造轨迹。

领域约定（与 docs/data-contract.md 一致）：

* 家庭是不可拆分的分析单位，成员清单随家庭整体计入；跨家庭重复出现的个人
  标识记为身份冲突，人数同时给出名册口径与去重口径，不静默择一。
* 每条迁移只知道发生在 ``time_min`` 至 ``time_max`` 闭区间内，不假设区间内的
  具体发生时刻。时间轴由全部区间端点切分，家庭在每个时段上的状态只能是
  “确定”或“可能”集合，绝不产出唯一的推测路线。
* 同一家庭两条迁移的不确定区间重叠，或首尾状态不能衔接，记为矛盾轨迹；
  矛盾期间状态只能给可能集合。
* 家庭最后一条事件 ``time_max`` 之后没有新证据：末状态不延续为确定事实，
  仅作为可能性保留，并由 lost_follow_up 给出有依据的人数范围 [0, 家庭人数]。
* 迟到登记（known_at 晚于餐食截点）在“截点可知视图”中排除，但始终可见。
"""

from collections import defaultdict
from datetime import datetime

HOME = "home"
SHELTER_PREFIX = "shelter:"

# 数据质量问题代码，全部随证据标识输出，不做自动修复。
ISSUE_IDENTITY = "identity_conflict"
ISSUE_CONTRADICTION = "contradictory_trajectory"
ISSUE_UNCERTAIN = "uncertain_interval"
ISSUE_LATE = "late_registration"
ISSUE_LOST = "lost_follow_up"
ISSUE_UNKNOWN_SHELTER = "unknown_shelter_reference"


def _parse(value):
    return datetime.fromisoformat(value)


def _iso(value):
    return value.isoformat()


def _shelter_id(state):
    return state[len(SHELTER_PREFIX):] if state.startswith(SHELTER_PREFIX) else None


def _validate(tables):
    """核验引用与时间区间，返回建索引后的原始记录。结构性错误直接拒绝。"""
    households = {row["household_id"]: row for row in tables["households"]}
    memberships = defaultdict(list)
    for row in tables["households"]:
        for member in row["member_ids"]:
            memberships[member].append(row["household_id"])
    identity_conflicts = {
        person: sorted(household_ids)
        for person, household_ids in memberships.items()
        if len(household_ids) > 1
    }

    known_shelters = {row["shelter_id"] for row in tables["shelters"]}
    events = defaultdict(list)
    unknown_shelters = set()
    for row in tables["moves"]:
        if row["household_id"] not in households:
            raise ValueError("迁移记录引用未知家庭")
        time_min = _parse(row["time_min"])
        time_max = _parse(row["time_max"])
        if time_max < time_min:
            raise ValueError("迁移时间区间反向")
        for state in (row["from_state"], row["to_state"]):
            shelter = _shelter_id(state)
            if shelter is not None and shelter not in known_shelters:
                unknown_shelters.add((row["move_id"], shelter))
        events[row["household_id"]].append({
            "move_id": row["move_id"],
            "household_id": row["household_id"],
            "from_state": row["from_state"],
            "to_state": row["to_state"],
            "time_min": time_min,
            "time_max": time_max,
            "known_at": _parse(row["known_at"]),
        })
    for household_events in events.values():
        household_events.sort(key=lambda event: (event["time_min"], event["time_max"], event["move_id"]))
    return households, dict(memberships), identity_conflicts, dict(events), sorted(unknown_shelters)


def _contradictions(household_events):
    """返回矛盾事件对（move_id 二元组）。端点相接（前.max==后.min）不算重叠。"""
    pairs = set()
    for previous, current in zip(household_events, household_events[1:]):
        if previous["time_max"] > current["time_min"]:
            pairs.add((previous["move_id"], current["move_id"]))
        if previous["to_state"] != current["from_state"]:
            pairs.add((previous["move_id"], current["move_id"]))
    return pairs


def _state_at(events, contradicting_move_ids, t, last_evidence_end=None, horizon_end=None):
    """评估家庭在时刻 t 的状态。

    约定：事件只知发生在闭区间 [time_min, time_max] 内，具体时刻未知。因此
    t == time_min 或 t == time_max（区间宽度大于 0 时）都属于进行中，只有
    time_max 之后才确定进入 to_state；零宽事件（time_min == time_max）视为
    该时刻已完成。失联判定：当 t 已越过家庭最后一条事件、且该家庭在 t 之后
    再无任何事件时，末状态不延续为确定事实，只保留为可能状态。
    ``horizon_end``（全局观测时域终点）处的尾段不降级——观测到此为止，不臆断
    之后。返回 (确定状态或 None, 去重排序的可能状态, 支撑 move_id)。
    """
    # 零宽事件在该时刻即视为完成；非零宽事件只有越过 time_max 才确定完成，
    # 闭区间内部（含右端点，因具体时刻未知）都属于进行中。
    resolved = [event for event in events
                if event["time_min"] == event["time_max"]
                or event["time_max"] < t]
    active = [event for event in events
              if event["time_min"] <= t <= event["time_max"]
              and event["time_min"] != event["time_max"]]
    evidence = sorted(event["move_id"] for event in resolved + active)

    if not resolved and not active:
        return None, [], evidence

    # 已解决事件之间若存在矛盾，顺序无法判定，不能给确定状态；进行中事件与已
    # 解决事件的矛盾不推翻已确定的历史，只在可能集合中保留两种状态。
    resolved_ids = {event["move_id"] for event in resolved}
    active_ids = {event["move_id"] for event in active}
    resolved_contradicted = any(
        left in resolved_ids and right in resolved_ids
        for left, right in contradicting_move_ids
    )
    active_contradicts_resolved = any(
        (left in active_ids or right in active_ids)
        and (left in resolved_ids or right in resolved_ids)
        for left, right in contradicting_move_ids
    )

    if not active and not resolved_contradicted and not active_contradicts_resolved:
        last = resolved[-1]
        # 失联：t 越过家庭最后事件，且家庭在 t 之后再无事件（t 之后不存在
        # time_min 更晚的事件）。时域终点尾段不降级。
        has_future_event = any(event["time_min"] >= t and event["move_id"] not in resolved_ids
                               for event in events)
        stale_tail = (last_evidence_end is not None and t > last_evidence_end
                      and not has_future_event
                      and (horizon_end is None or t < horizon_end))
        if stale_tail:
            return None, sorted({last["to_state"]}), evidence
        return last["to_state"], [last["to_state"]], evidence

    possible = set()
    if resolved:
        if resolved_contradicted:
            for event in resolved:
                possible.add(event["from_state"])
                possible.add(event["to_state"])
        else:
            possible.add(resolved[-1]["to_state"])
    for event in active:
        possible.add(event["from_state"])
        possible.add(event["to_state"])
    return None, sorted(possible), evidence


def _risk_windows_by_village(tables):
    windows = defaultdict(list)
    for row in tables["risk_windows"]:
        windows[row["village_id"]].append({
            "risk_id": row["risk_id"],
            "start": _parse(row["start"]),
            "end": _parse(row["end"]),
        })
    return windows


def _intersection_seconds(start, end, window):
    left = max(start, window["start"])
    right = min(end, window["end"])
    return max(0, int((right - left).total_seconds()))


def _build(tables, *, move_predicate=None, scope_note=None):
    households, memberships, identity_conflicts, all_events, unknown_shelters = _validate(tables)
    known_shelters = {row["shelter_id"] for row in tables["shelters"]}

    issues = [
        {"code": ISSUE_IDENTITY, "person_id": person, "household_ids": household_ids,
         "detail": "同一人员标识出现在多个家庭，名册口径与去重口径人数不同"}
        for person, household_ids in sorted(identity_conflicts.items())
    ]
    issues.extend(
        {"code": ISSUE_UNKNOWN_SHELTER, "move_id": move_id, "shelter_id": shelter_id,
         "detail": "迁移记录引用了安置点表中不存在的安置点"}
        for move_id, shelter_id in unknown_shelters
    )

    events_by_household = {}
    all_endpoints = []
    for household_id, household_events in all_events.items():
        kept = [event for event in household_events if move_predicate is None or move_predicate(event)]
        events_by_household[household_id] = kept
        for event in kept:
            all_endpoints.extend((event["time_min"], event["time_max"]))
            if event["time_min"] != event["time_max"]:
                issues.append({
                    "code": ISSUE_UNCERTAIN, "household_id": household_id,
                    "move_id": event["move_id"],
                    "width_seconds": int((event["time_max"] - event["time_min"]).total_seconds()),
                    "detail": "迁移只知发生在时间区间内，未登记具体时刻",
                })

    cutoffs = [
        {"shelter_id": row["shelter_id"], "cutoff": _parse(row["meal_cutoff"]),
         "capacity": row["capacity"]}
        for row in tables["shelters"]
    ]
    for cutoff in cutoffs:
        for household_events in events_by_household.values():
            for event in household_events:
                if event["known_at"] > cutoff["cutoff"]:
                    issues.append({
                        "code": ISSUE_LATE, "household_id": event["household_id"],
                        "move_id": event["move_id"],
                        "known_at": _iso(event["known_at"]),
                        "cutoff": _iso(cutoff["cutoff"]),
                        "shelter_id": cutoff["shelter_id"],
                        "detail": "登记可知时点晚于餐食截点，截点排餐时不可见",
                    })

    horizon_start = min(all_endpoints) if all_endpoints else None
    horizon_end = max(all_endpoints) if all_endpoints else None
    endpoints = sorted(set(all_endpoints))
    segments = [(left, right) for left, right in zip(endpoints, endpoints[1:]) if left < right]

    risk_by_village = _risk_windows_by_village(tables)
    household_sections = {}
    occupancy = []
    round_trip_rows = {}
    lost_follow_up = []

    for household_id in sorted(households):
        row = households[household_id]
        household_events = events_by_household.get(household_id, [])
        contradictions = _contradictions(all_events.get(household_id, []))
        for left, right in sorted(contradictions):
            issues.append({
                "code": ISSUE_CONTRADICTION, "household_id": household_id,
                "move_ids": [left, right],
                "detail": "迁移区间重叠或首尾状态不能衔接，事件顺序不可判定",
            })

        segment_rows = []
        exposure_min = 0
        exposure_max = 0
        supporting_risks = set()
        last_evidence_end = household_events[-1]["time_max"] if household_events else None
        if household_events:
            for start, end in segments:
                definite, possible, evidence = _state_at(
                    household_events, contradictions, start,
                    last_evidence_end, horizon_end)
                segment_rows.append({
                    "start": _iso(start), "end": _iso(end),
                    "definite_state": definite,
                    "possible_states": possible,
                    "evidence_move_ids": evidence,
                })
                for window in risk_by_village.get(row["home_village"], []):
                    overlap = _intersection_seconds(start, end, window)
                    if overlap:
                        supporting_risks.add(window["risk_id"])
                        if definite == HOME:
                            exposure_min += overlap
                        if HOME in possible:
                            exposure_max += overlap

        evacuations = [event["move_id"] for event in household_events
                       if event["from_state"] == HOME and event["to_state"].startswith(SHELTER_PREFIX)]
        returns = [event["move_id"] for event in household_events
                   if event["from_state"].startswith(SHELTER_PREFIX) and event["to_state"] == HOME]
        round_trip_rows[household_id] = {
            "evacuations": len(evacuations), "returns": len(returns),
            "evacuation_moves": evacuations, "return_moves": returns,
        }

        last_event = household_events[-1] if household_events else None
        is_lost = last_event is None or (horizon_end is not None and last_event["time_max"] < horizon_end)
        lost_row = None
        if is_lost:
            size = len(row["member_ids"])
            lost_row = {
                "household_id": household_id,
                "household_size": size,
                "person_count_range": [0, size],
                "last_evidence_move": last_event["move_id"] if last_event else None,
                "last_evidence_time": _iso(last_event["time_max"]) if last_event else None,
                "last_observed_state": last_event["to_state"] if last_event else None,
                "basis": "末条记录之后无新证据，不延续末状态；在住人数只能取 0 到家庭人数之间",
            }
            lost_follow_up.append(lost_row)
            issues.append({
                "code": ISSUE_LOST, "household_id": household_id,
                "last_evidence_move": lost_row["last_evidence_move"],
                "person_count_range": lost_row["person_count_range"],
                "detail": "家庭失去后续记录，末状态不延续为确定事实",
            })

        household_sections[household_id] = {
            "household_id": household_id,
            "members": list(row["member_ids"]),
            "household_size": len(row["member_ids"]),
            "home_village": row["home_village"],
            "move_ids": [event["move_id"] for event in household_events],
            "segments": segment_rows,
            "round_trips": round_trip_rows[household_id],
            "risk_exposure_seconds": {
                "min": exposure_min, "max": exposure_max,
                "risk_ids": sorted(supporting_risks),
                "village_id": row["home_village"],
            },
            "lost_follow_up": lost_row,
        }

    for start, end in segments:
        per_shelter = {}
        for household_id, section in household_sections.items():
            matching = [item for item in section["segments"]
                        if item["start"] == _iso(start) and item["end"] == _iso(end)]
            if not matching:
                continue
            segment = matching[0]
            for state in set(filter(None, [segment["definite_state"], *segment["possible_states"]])):
                shelter = _shelter_id(state)
                if shelter is None or shelter not in known_shelters:
                    # 未登记安置点只在质量问题中可见，不纳入在住规模，不凭空造点。
                    continue
                bucket = per_shelter.setdefault(shelter, {"definite": [], "possible": []})
                if segment["definite_state"] == SHELTER_PREFIX + shelter:
                    bucket["definite"].append(household_id)
                if SHELTER_PREFIX + shelter in segment["possible_states"]:
                    bucket["possible"].append(household_id)
        shelter_rows = {}
        for shelter, bucket in sorted(per_shelter.items()):
            definite_households = sorted(bucket["definite"])
            possible_households = sorted(set(bucket["possible"]))
            shelter_rows[shelter] = _person_counts(
                households, memberships, identity_conflicts,
                definite_households, possible_households)
        occupancy.append({"start": _iso(start), "end": _iso(end), "shelters": shelter_rows})

    meal_planning = []
    for cutoff in cutoffs:
        meal_planning.append(_meal_view(
            tables, cutoff, households, memberships, identity_conflicts, events_by_household,
            all_events, horizon_start, horizon_end, move_predicate))

    totals_evacuations = sum(item["evacuations"] for item in round_trip_rows.values())
    totals_returns = sum(item["returns"] for item in round_trip_rows.values())
    exposure_totals = {
        "min_seconds": sum(section["risk_exposure_seconds"]["min"]
                           for section in household_sections.values()),
        "max_seconds": sum(section["risk_exposure_seconds"]["max"]
                           for section in household_sections.values()),
        "per_household": {
            household_id: section["risk_exposure_seconds"]
            for household_id, section in sorted(household_sections.items())
        },
    }

    analysis = {
        "scope": scope_note or "基于家庭迁移区间的时段在住规模、往返次数与风险暴露上下界，不补造唯一轨迹",
        "horizon": {"start": _iso(horizon_start) if horizon_start else None,
                    "end": _iso(horizon_end) if horizon_end else None},
        "reference_points": [
            {"kind": "meal_cutoff", "shelter_id": item["shelter_id"], "time": _iso(item["cutoff"])}
            for item in cutoffs],
        "households": household_sections,
        "occupancy_timeline": occupancy,
        "meal_planning": meal_planning,
        "round_trip_totals": {
            "evacuations": totals_evacuations,
            "returns": totals_returns,
            "per_household": dict(sorted(round_trip_rows.items())),
        },
        "risk_exposure_totals": exposure_totals,
        "lost_follow_up": lost_follow_up,
        "issues": issues,
    }
    analysis["trace_index"] = _trace_index(analysis)
    return analysis


def _person_counts(households, memberships, identity_conflicts,
                   definite_households, possible_households):
    """名册口径按家庭人数求和；去重口径按稳定人员标识去重，两者差异即身份冲突。"""
    def roster(household_ids):
        return sum(len(households[household_id]["member_ids"]) for household_id in household_ids)

    def unique(household_ids):
        people = set()
        for household_id in household_ids:
            people.update(households[household_id]["member_ids"])
        return len(people)

    counted_people = set()
    for household_id in possible_households:
        counted_people.update(households[household_id]["member_ids"])
    conflict_in_scope = sorted(person for person in counted_people if person in identity_conflicts)

    return {
        "definite_households": definite_households,
        "possible_households": possible_households,
        "possible_only_households": sorted(set(possible_households) - set(definite_households)),
        "definite_persons_roster": roster(definite_households),
        "possible_persons_roster_range": [roster(definite_households), roster(possible_households)],
        "definite_unique_persons": unique(definite_households),
        "possible_unique_persons_range": [unique(definite_households), unique(possible_households)],
        "identity_conflict_persons": conflict_in_scope,
        "identity_conflict_present": bool(conflict_in_scope),
    }


def _meal_view(tables, cutoff, households, memberships, identity_conflicts,
               events_by_household, all_events, horizon_start, horizon_end, outer_predicate):
    time = cutoff["cutoff"]
    shelter_id = cutoff["shelter_id"]
    out_of_horizon = horizon_end is None or time > horizon_end or time < horizon_start

    def evaluate(predicate, label):
        definite_households, possible_households, reasons = [], [], defaultdict(list)
        late_excluded = []
        for household_id in sorted(households):
            full_events = all_events.get(household_id, [])
            kept = [event for event in full_events if predicate is None or predicate(event)]
            excluded = [event["move_id"] for event in full_events if event not in kept]
            if excluded:
                late_excluded.extend(excluded)
            if not kept:
                continue
            contradictions = _contradictions(full_events)
            last_evidence_end = kept[-1]["time_max"] if kept else None
            definite, possible, _evidence = _state_at(
                kept, contradictions, time, last_evidence_end, horizon_end)
            shelter_state = SHELTER_PREFIX + shelter_id
            if definite == shelter_state:
                definite_households.append(household_id)
            if shelter_state in possible:
                possible_households.append(household_id)
                if definite != shelter_state:
                    # 末事件已全部结束仍只是可能：失联尾段；否则是矛盾/进行中。
                    stale = all(event["time_max"] <= time for event in kept)
                    reasons["lost_follow_up" if stale else "contradiction_or_active"].append(
                        household_id)
        counts = _person_counts(households, memberships, identity_conflicts,
                                sorted(definite_households), sorted(set(possible_households)))
        return {
            "view": label,
            "out_of_observation_horizon": out_of_horizon,
            "counts": None if out_of_horizon else counts,
            "excluded_move_ids": sorted(set(late_excluded)),
            "possible_only_reasons": {key: sorted(value) for key, value in sorted(reasons.items())},
        }

    knowable = evaluate(lambda event: event["known_at"] <= time, "as_of_cutoff_knowledge")
    full = evaluate(outer_predicate, "full_evidence")
    return {
        "shelter_id": shelter_id,
        "cutoff": _iso(time),
        "capacity": cutoff["capacity"],
        "full_evidence_view": full,
        "as_of_cutoff_knowledge_view": knowable,
        "evidence": {
            "household_ids": sorted(households),
            "knowable_move_ids": sorted(
                event["move_id"] for household_events in events_by_household.values()
                for event in household_events if event["known_at"] <= time),
        },
    }


def _trace_index(analysis):
    """登记每个家庭出现在哪些汇总位置，支持从汇总反向追回家庭与事件。"""
    index = {}
    for household_id, section in analysis["households"].items():
        locations = ["households." + household_id]
        for segment in analysis["occupancy_timeline"]:
            if any(household_id in bucket["possible_households"]
                   for bucket in segment["shelters"].values()):
                locations.append("occupancy_timeline@" + segment["start"])
        for meal in analysis["meal_planning"]:
            for view_name in ("full_evidence_view", "as_of_cutoff_knowledge_view"):
                view = meal[view_name]
                if view["counts"] and household_id in view["counts"]["possible_households"]:
                    locations.append("meal_planning." + meal["shelter_id"] + "." + view_name)
        if section["round_trips"]["evacuations"] or section["round_trips"]["returns"]:
            locations.append("round_trip_totals")
        if section["risk_exposure_seconds"]["max"]:
            locations.append("risk_exposure_totals")
        if section["lost_follow_up"]:
            locations.append("lost_follow_up")
        issue_codes = sorted({issue["code"] for issue in analysis["issues"]
                              if issue.get("household_id") == household_id
                              or household_id in issue.get("household_ids", [])})
        index[household_id] = {
            "move_ids": section["move_ids"],
            "risk_ids": section["risk_exposure_seconds"]["risk_ids"],
            "issue_codes": issue_codes,
            "appears_in": locations,
        }
    return index


def trace_household(analysis, household_id):
    """从家庭标识追回其全部事件、时段状态与它所支撑的汇总位置。"""
    if household_id not in analysis["households"]:
        raise KeyError("未知家庭：" + household_id)
    section = analysis["households"][household_id]
    supporting_issues = [
        issue for issue in analysis["issues"]
        if issue.get("household_id") == household_id
        or household_id in issue.get("household_ids", [])
    ]
    return {
        "household": section,
        "trace": analysis["trace_index"][household_id],
        "supporting_issues": supporting_issues,
    }


def build_dynamic_analysis(tables):
    """对外主入口：形成动态在住规模、往返、暴露、质量问题与溯源索引。"""
    return _build(tables)
