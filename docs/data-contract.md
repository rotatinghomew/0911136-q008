# 领域资料与供数约定

## 数据含义

成员 member_ids 是稳定个人标识，跨家庭重复声明为冲突而非自动加两次。状态为 home、shelter:<id>、other、unknown。移动发生在 time_min 至 time_max 闭区间内，known_at 为登记可知时点，不能回填进入更早历史视图。meal_cutoff 为餐食截点，不是容量；不确定轨迹允许给人数上下界，不要求编造概率。

## 现行摘要

`coverage_review/analysis.py` 生成现行资料摘要。源码和运行结果中的 scope 说明其统计边界，不能把未提供的推断当成已有能力。`summarize` 保留原始家庭与迁移记录摘要；`dynamic_summary` 在其外增加 `coverage_review/timeline.py` 的动态分析，并通过 `coverage_review/revisions.py` 支持两代资料对账。

## 动态在住分析的语义边界

动态分析只依据家庭迁移区间，不补造唯一轨迹，所有输出都可经 `trace_index` 与 `trace_household` 追回支撑的家庭与事件：

* **时段在住规模**：时间轴由全部迁移的 `time_min`/`time_max` 端点切分。事件只知发生在闭区间内，区间内部（含右端点）家庭状态只能给“可能状态集合”，确定状态为 `null`；零宽事件（两端点相同）在该时刻视为完成。家庭在事件之间、且之后仍有事件的时段保持确定状态；家庭最后一条事件之后再无证据的尾段（失去后续记录），末状态不延续为确定事实，只保留为可能。
* **失联人数范围**：`lost_follow_up` 对失去后续记录的家庭给 `person_count_range = [0, 家庭人数]`，并记录最后证据事件与时点，不把末状态无限延续。无任何迁移的家庭同样按失联处理，不臆造初始状态。
* **矛盾轨迹**：同一家庭两条迁移区间重叠（端点相接不算）或首尾状态不能衔接，记 `contradictory_trajectory`；矛盾期间只给可能状态集合，不自动选择“最顺”的一条路线。
* **迟到登记**：`known_at` 晚于某安置点 `meal_cutoff` 的事件记 `late_registration`。餐食视图分两套：`as_of_cutoff_knowledge_view` 排除截点尚不可知的事件，`full_evidence_view` 使用全部证据；两者差异即迟到登记对排餐的影响。
* **人数口径**：家庭不可拆分，整体计入或排除。同时给名册口径（家庭人数求和）与去重口径（稳定人员标识去重）；跨家庭重复的人员标识记 `identity_conflict`，不静默择一。未登记安置点的迁移记 `unknown_shelter_reference`，不纳入在住规模。
* **往返与暴露**：往返次数按 home→shelter（撤离）与 shelter→home（返家）事件分别计数并保留 move_id；风险暴露时长仅统计 home 可能/确定状态与本村 `risk_windows` 的交集，给秒级上下界，不编造概率。

## 资料修订对账

`revisions.compare_revisions(previous, current)` 对两代完整快照按主键对账，输出 `new_demand`（新增家庭、新增成员、旧代观测时域之后的新撤离）与 `old_debt_corrections`（补登、撤回、字段修订、名册纠正、矛盾消除），并给餐食需求在两套视图下的变化及贡献家庭。迟到补登以旧代餐食截点判定，单独标记。同 generation 的内容改写由供数端拒绝，对账要求两代 generation 不同。

## 供数接口

`GET /manifest` 返回 generation、tables（每表 count 与 sha256）及 metadata。`GET /tables/<name>?generation=<generation>` 返回同代 rows。供数换代时旧 generation 返回 HTTP 409，不允许将新代记录接入旧分析；不存在的表返回 404。表内容为稳定键唯一的完整同代记录，不是增量事件流。客户端对清单摘要进行核验。摘要使用 UTF-8、排序对象键、紧凑 JSON 的 SHA256，数组顺序保留供数端顺序。

字段与主键声明位于 `coverage_review/schema.json`，合成资料位于 `fixtures/dataset.json`。`metadata.cutoff` 是供数展示截点，不自动为分析选择训练截止点。服务可将整个 dataset 原子替换为新一代，但不在同一 generation 下更改内容。

## 结果保存

`reports.py` 保存现有摘要及来源表摘要，report_id 是内容摘要，不是随机编号。相同内容生成相同编号，临时文件写入完成后原子替换目标文件；历史内容是否保留由调用者选择不同目标路径。服务只监听本机，不接入生产系统。
