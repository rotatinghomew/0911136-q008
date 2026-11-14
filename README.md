# 重复撤离与返家记录下的动态安置人数及暴露时长

本项目组织领域资料并提供当前摘要，通过本地 HTTP 供数服务连接应用。业务代码在 `coverage_review`，字段与单位约定见 `docs/data-contract.md`。示例均为本地合成记录，不代表真实县域记录或业务结论。

## 环境

Python 3.11 及以上，业务运行仅使用标准库。构建工具及其依赖完整固定在 `requirements-build.txt`；先用 `python3 -m pip install -r requirements-build.txt` 安装。源码和业务测试无需访问外部服务，首次安装构建依赖需要访问 Python 包仓库。

## 当前摘要

在项目根目录运行，会临时启动真实本地 HTTP 服务、核验资料代际、计算现有摘要并完整打印 JSON：

```sh
python3 -m coverage_review.runner --output /tmp/coverage_review-summary.json
```

当前能力是源码 `analysis.py` 定义的摘要，不会自动给出其他统计推断。输出中的 scope 说明边界，sources 记录资料依据。

## 动态在住、往返与暴露分析

`dynamic_summary`（实现于 `coverage_review/timeline.py`）在保留现行摘要的同时，从家庭迁移事件构建：

- `occupancy_timeline`：随时段变化的在住规模，家庭不可拆分，每个时段给确定/可能家庭集合与名册、去重两套人数上下界；
- `meal_planning`：按安置点餐食截点分别给“全证据视图”和“截点可知视图”，迟到登记在后者中排除但始终可见，用于估算今晚餐食份数区间；
- `round_trip_totals`：按家庭统计撤离与返家次数（同一家庭重复撤离不重复计人），保留全部 move_id；
- `risk_exposure_totals`：home 状态与本村风险窗交集的暴露秒数上下界；
- `lost_follow_up`：失去后续记录家庭的有依据人数范围 `[0, 家庭人数]`，不延续末状态；
- `issues`：身份冲突、矛盾轨迹、时间不确定、迟到登记、失联、未登记安置点全部显式列示；
- `trace_index` 与 `trace_household`：从任何汇总反向追回支撑它的家庭、事件与质量问题。

分析不自动补出“最顺”的路线：迁移只知发生在 `[time_min, time_max]` 闭区间内，区间内只给可能状态集合。语义边界见 `docs/data-contract.md`。

## 资料修订对账

提供上一代完整资料文件即可区分新增需求与旧账更正：

```sh
python3 -m coverage_review.runner --dataset fixtures/dataset-revised.json \
  --baseline fixtures/dataset.json --output /tmp/coverage_review-revised.json
```

报告中的 `revisions` 把差异分为 `new_demand`（新家庭、新成员、旧观测时域之后的新撤离）与 `old_debt_corrections`（补登、撤回、字段修订、名册纠正、矛盾消除），并给两套餐食视图下的需求变化及贡献家庭。同 generation 内容改写已被供数端拒绝，对账要求两代 generation 不同。

## 分开运行供数与应用

```sh
python3 -m coverage_review.runner --serve --port 8765
```

另一个终端可运行：

```sh
python3 -m coverage_review.runner --source-url http://127.0.0.1:8765 --output /tmp/coverage_review-summary.json
```

## 测试与检查

```sh
python3 -m unittest discover -s tests -t . -v
python3 -m compileall -q coverage_review tests
python3 -m build --wheel --no-isolation
```

测试覆盖现有领域摘要、非法引用、真实 HTTP 换代拒绝、表清单内容核验及结果保存。展示现有产品可直接运行上述摘要命令，不需要回放历史日志。扩展分析时应同步说明新增能力的实际运行入口，保留现行摘要调用。

## 容器运行

Dockerfile 固定官方 Python 3.11.14 slim-bookworm 镜像摘要，不依赖本机私有镜像；首次构建需下载基础镜像和固定构建工具。构建与运行实际摘要：

```sh
docker build -t local/coverage-review .
docker run --rm local/coverage-review
```

容器内测试：

```sh
docker run --rm --entrypoint python3 local/coverage-review -m unittest discover -s tests -t . -v
```
