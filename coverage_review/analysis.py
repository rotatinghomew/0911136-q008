"""检查家庭身份与原始迁移区间，不补造轨迹。"""
from collections import defaultdict
from datetime import datetime


def household_profile(tables):
    memberships = defaultdict(list)
    households = {row["household_id"]: row for row in tables["households"]}
    for row in tables["households"]:
        for member in row["member_ids"]:
            memberships[member].append(row["household_id"])
    moves = defaultdict(list)
    for row in tables["moves"]:
        if row["household_id"] not in households:
            raise ValueError("迁移记录引用未知家庭")
        if datetime.fromisoformat(row["time_max"]) < datetime.fromisoformat(row["time_min"]):
            raise ValueError("迁移时间区间反向")
        moves[row["household_id"]].append(row["move_id"])
    return {"unique_people": len(memberships), "identity_conflicts": {key: value for key, value in memberships.items() if len(value) > 1},
            "household_moves": dict(sorted(moves.items()))}


def summarize(tables):
    return {**household_profile(tables), "scope": "原始家庭和迁移记录摘要，不形成在住人数时间线"}
