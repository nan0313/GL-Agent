# EV WebGL Agent 项目评测数据集 v1

本目录提供三套与当前项目代码和知识库绑定的离线评测种子集，用于后续计算 GIS Agent 端到端成功率、RAG Evidence Recall@K，以及混合路由的 LLM 调用减少比例。

> 重要：这些数据已经通过结构、数量、去重和证据溯源校验，但仍是“项目特定种子集”，不是已经由业务专家签字确认的最终金标。正式写入简历前，应完成一次人工复核、锁定测试集，再运行被测系统。不要把本目录中的样本数量、`gold_model_required` 或静态校验通过率误写成模型效果。

## 已生成文件

| 文件 | 规模 | 主要用途 |
|---|---:|---|
| `datasets/gis_e2e_v1.jsonl` | 80 条：60 个可执行任务 + 20 个异常任务 | E2E GIS Task Success Rate、异常处理正确率 |
| `datasets/rag_gold_v1.json` | 120 条：100 个可回答问题 + 20 个 hard negative | Evidence Recall@K、Hit@K、完整证据命中率、拒答正确率 |
| `datasets/router_v1.jsonl` | 300 条：5 类各 60 条 | 路由 Macro-F1、规则覆盖率、LLM 调用减少比例 |
| `datasets/manifest_v1.json` | 1 份 | 数据来源、数量和内容摘要哈希 |
| `reports/dataset_validation_v1.json` | 1 份 | 最近一次静态校验结果 |

生成脚本为 `generate_datasets.py`，校验脚本为 `validate_datasets.py`。

完整的逐条运行命令见 [`RUN_METRICS.md`](RUN_METRICS.md)。对应 Runner 为：

- `run_rag_eval.py`：Evidence Recall@K、Hit@K、CompleteHit@K、hard-negative 拒答正确率；
- `run_router_eval.py`：路由 Macro-F1、规则覆盖/精度、Hybrid LLM 逻辑调用减少；
- `run_gis_e2e.js`：真实 Chrome、生产 UIEvent Queue 与 WebGL Bridge receipt 的 GIS 严格成功率；
- `build_resume_metrics.py`：合并三份报告并生成简历指标汇总。

## 1. GIS Agent 数据

### 数据依据

样本来自当前项目的客户侧 WebGL 操作契约、确定性 GIS Skill、行政区 Provider、事件队列和已有 smoke case。覆盖对象查找、定位、高亮、属性读取、行政区、图层显隐、相机、坐标定位、截图、缓冲区、附近查询和多步任务。

每条包含：

- `turns`：单轮或多轮用户指令；
- `preconditions`：运行前必须满足的场景状态；
- `gold.ui_events`：预期事件及顺序；
- `gold.postconditions`：必须通过真实 Cesium/WebGL 状态验证的结果；
- `gold.expected_handling`：`execute`、`clarify`、`not_found` 或 `reject`；
- `timeout_ms`：单例超时时间。

带 `${runtime.*}` 的值是运行时绑定变量，不能原样发送给 Agent。E2E Runner 应先从真实页面选择唯一对象，再把对象 ID、坐标等写入请求上下文。`contract_gap_probe` 标签专门覆盖当前前后端契约容易遗漏的图层列表、图层显隐和视角复位能力。

### 推荐指标

可执行任务严格成功率：

```text
E2E-TSR = 所有必需步骤、Bridge receipt 和真实场景后置条件均成功的任务数 / 可执行任务总数
```

一个任务中的任一步失败，整条任务记为失败。不能只检查 HTTP 200、`status=success` 或后端是否生成了 UIEvent。

20 条异常任务单独计算：

```text
Expected Handling Accuracy = 正确澄清、拒绝或报告未找到且未误操作的异常任务数 / 异常任务总数
```

## 2. RAG 数据

### 数据依据与溯源

100 个可回答问题从当前 `runtime/rag/rag.sqlite3` 中的 10 份 active 知识文件抽取，每份文件 10 题；其中 20 题需要两个原子证据单元。另有 20 个在语义上接近业务、但当前知识库不包含答案的 hard negative。

每个证据单元都保存：

- `source_name` 与 `source_sha256`；
- `chunk_id` 与 `chunk_content_hash`；
- `page_start/page_end` 或 `section_path`；
- 可在当前索引分块中归一化定位的 `quote`。

这比只使用易随切分策略变化的 `chunk_id` 更稳定。校验报告确认当前 120 个 gold evidence spans 均能回溯到索引原文。

### 推荐指标

生产配置如果最终向答案提供 3 条证据，主指标使用 Evidence Recall@3；若固定提供 5 条，则使用 Evidence Recall@5。K 必须和真实生产配置一致。

```text
Evidence Recall@K = 对每题 Top-K 覆盖的 gold evidence unit 数 / 该题全部 gold evidence unit 数，再对题目取平均
Hit@K             = 至少命中一个 gold evidence unit 的问题比例
CompleteHit@K     = 全部 gold evidence unit 都命中的问题比例
```

hard negative 应计算无答案识别准确率或错误回答率。引用正确率还需要在生成答案后，把每个事实主张和相邻 `[E#]` 引用进行支持性标注；本数据集提供引用核验所需原文，但静态数据本身不能产生“引用正确率”。

## 3. 路由数据

### 数据设计

5 个代码中的真实 RouteType 各 60 条：

- `anchor_task`
- `rag_qa`
- `document_task`
- `general_chat`
- `unknown`

数据包含附件状态、选中对象上下文、`family_id`、期望路由、是否应进入模型判断的 `gold_model_required`，并包含 120 条边界或对抗样本。`family_id` 用于保证同一模板/语义家族不会跨 dev/test 泄漏。

当前 `QwenRouter` 的提示词没有 `document_task`，而 Pydantic RouteType 包含该类。在做五分类对比前，应先统一规则路由、LLM 路由与评测集的标签空间；否则 `document_task` 的 LLM-only baseline 不公平。

### 推荐指标

```text
LLM Call Reduction = 1 - Hybrid 实际路由/语义规划 LLM 调用数 / LLM-only 实际调用数
```

必须用 runner 中的计数器记录实际进入 `QwenRouter` 或语义规划器的逻辑调用；如果声称 HTTP 请求减少，则应计算包含重试在内的真实 POST 次数。`gold_model_required` 是评测金标，不是实际调用结果，也不是可直接写简历的“减少 60%”。

同时报告 Macro-F1、规则命中精度和相对 LLM-only 的质量变化。该集是类别均衡的诊断集，不代表线上自然流量分布；简历中应写“在 N 条均衡离线测试集上”。如能取得脱敏真实日志，应另建 traffic-weighted 集合测线上预期节省。

## 生成与校验

在项目根目录执行：

```powershell
& .\runtime\python\python.exe -X utf8 .\evaluation\generate_datasets.py
& .\runtime\python\python.exe -X utf8 .\evaluation\validate_datasets.py
```

第二条命令应输出 `"status": "passed"`。它检查：

- JSON/JSONL 格式、必填字段、数量和唯一 ID；
- 同一 `family_id` 不跨 dev/test；
- GIS 正向/异常分布和非误操作约束；
- RAG 的 source/chunk hash、页码以及 quote 对当前 SQLite 索引的可定位性；
- 路由标签分布、问题去重和边界样本比例；
- 三份数据与 manifest SHA-256 摘要一致。

重新构建 RAG 索引后，旧的 chunk hash 可能失效；应重新生成数据或经人工迁移证据，再更新版本号。不要为了让指标更高而在最终测试集上反复调参。

## 简历使用前的人工锁定流程

1. 由你逐条复核 GIS 的真实对象名、图层名和后置条件，把 `${runtime.*}` 绑定到公司演示环境中的稳定对象。
2. 对 RAG 的 100 个答案和 20 个 hard negative 做业务复核；建议至少抽取 20% 由第二人复核。
3. 对路由边界样本确认主意图；尤其检查“文档 + GIS 动作”“概念解释 + 高亮/图层关键词”类冲突。
4. 修改完成后将 `review_status` 改为 `human_locked`，更新数据集版本和 manifest，测试集只运行最终一次。
5. 保存逐题结果、配置、代码版本、知识库 hash、模型版本和置信区间，再从 held-out `test` 子集计算简历数字。

知识文件可能受公司资料管理要求约束。对外上传、公开仓库发布或交给第三方模型前，应先确认授权并进行脱敏。
