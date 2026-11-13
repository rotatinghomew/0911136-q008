# 重复撤离与返家记录下的动态安置人数及暴露时长

本项目组织领域资料并提供当前摘要，通过本地 HTTP 供数服务连接应用。业务代码在 `coverage_review`，字段与单位约定见 `docs/data-contract.md`。示例均为本地合成记录，不代表真实县域记录或业务结论。

## 环境

Python 3.11 及以上，仅使用标准库。`Dockerfile` 使用本机已备好的 Linux Python 环境，依赖清单无第三方项。源码和测试都无需访问外部服务。

## 当前摘要

在项目根目录运行，会临时启动真实本地 HTTP 服务、核验资料代际、计算现有摘要并完整打印 JSON：

```sh
python3 -m coverage_review.runner --output /tmp/coverage_review-summary.json
```

当前能力是源码 `analysis.py` 定义的摘要，不会自动给出其他统计推断。输出中的 scope 说明边界，sources 记录资料依据。

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
```

测试覆盖现有领域摘要、非法引用、真实 HTTP 换代拒绝、表清单内容核验及结果保存。展示现有产品可直接运行上述摘要命令，不需要回放历史日志。扩展分析时应同步说明新增能力的实际运行入口，保留现行摘要调用。

## 容器运行

本机已有 Dockerfile 所引用的基础镜像。构建与运行实际摘要：

```sh
docker build -t local/coverage-review .
docker run --rm local/coverage-review
```

容器内测试：

```sh
docker run --rm --entrypoint python3 local/coverage-review -m unittest discover -s tests -t . -v
```
