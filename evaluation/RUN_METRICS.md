# 三类项目指标：可直接执行的 PowerShell 流程

以下命令都在项目根目录 `G:\JN_work\gy\ev_agent` 下运行。建议使用一个新的 PowerShell 窗口，从上到下逐段执行。

## 0. 初始化评测环境

```powershell
Set-Location "G:\JN_work\gy\ev_agent"
. .\portable_common.ps1
Set-PortableEnvironment
$Python = Get-BundledPython
& $Python --version
node --version
```

这里必须使用项目自带的 Python，不能随意换成系统 Python，否则可能缺少向量模型、Pydantic 或索引依赖。

## 1. 先校验三份数据集

```powershell
& $Python -X utf8 .\evaluation\validate_datasets.py
```

最后应看到 `"status": "passed"`。如果失败，先不要计算指标。

## 2. 计算 RAG Evidence Recall@K / Hit@K

先运行 5 条 smoke test，确认模型和索引能正常加载：

```powershell
& $Python -X utf8 .\evaluation\run_rag_eval.py `
  --split all `
  --top-k 5 `
  --max-cases 5 `
  --output .\evaluation\reports\rag_eval_smoke.json
```

再运行全部 120 条：

```powershell
& $Python -X utf8 .\evaluation\run_rag_eval.py `
  --split all `
  --top-k 5 `
  --output .\evaluation\reports\rag_eval_v1.json
```

报告位置：`evaluation\reports\rag_eval_v1.json`。重点看：

- `metrics_by_k.5.evidence_recall_percent`：Evidence Recall@5；
- `metrics_by_k.5.hit_rate_percent`：Hit@5；
- `metrics_by_k.5.complete_hit_rate_percent`：全部证据单元命中率；
- `hard_negative.no_answer_accuracy_percent`：20 条无答案问题的正确拒答率。

该脚本只做本地检索和拒答判断，明确禁用答案生成，不需要 API Key，也不会把知识库正文发送给外部模型。第一次加载向量模型通常明显慢于后续样本。

如果生产端实际只给回答使用 3 条证据，把两条命令中的 `--top-k 5` 改成 `--top-k 3`，简历中也只能写 Recall@3。

## 3. 计算规则路由覆盖率与 LLM 调用减少

### 3.1 不调用大模型的预评测

```powershell
& $Python -X utf8 .\evaluation\run_router_eval.py `
  --mode rule-stage `
  --split all `
  --output .\evaluation\reports\router_rule_stage_v1.json
```

重点看：

- `rule_stage.coverage_percent`：多少请求被规则直接处理；
- `rule_stage.precision_on_handled_percent`：被规则处理的请求中有多少路由正确；
- `llm_calls.projected_reduction_percent`：根据规则未命中数推算的 LLM 逻辑调用减少比例。

这里的 reduction 是“推算值”，不能写成“实际调用减少”。

### 3.2 配置 Qwen 后做真实 Hybrid 路由评测

如果还没有本地配置，先复制模板：

```powershell
Copy-Item .\config\local_llm_config.example.yaml .\config\local_llm_config.yaml
notepad .\config\local_llm_config.yaml
```

把 `api_key` 的占位值替换为真实 Key，保存后关闭记事本。不要把包含 Key 的文件提交到公开仓库。若当前 PowerShell 曾在配置文件不存在时运行过 `Set-PortableEnvironment`，重新执行：

```powershell
Remove-Item Env:QWEN_ENABLED -ErrorAction SilentlyContinue
Set-PortableEnvironment
```

先试 5 条：

```powershell
& $Python -X utf8 .\evaluation\run_router_eval.py `
  --mode hybrid `
  --split all `
  --max-cases 5 `
  --output .\evaluation\reports\router_hybrid_smoke.json
```

再运行全部 300 条：

```powershell
& $Python -X utf8 .\evaluation\run_router_eval.py `
  --mode hybrid `
  --split all `
  --output .\evaluation\reports\router_eval_v1.json
```

重点看：

- `quality.macro_f1_percent`：五分类 Macro-F1；
- `llm_calls.actual_hybrid_logical_calls`：真实进入 `QwenRouter.decide` 的逻辑调用数；
- `llm_calls.actual_hybrid_failed_calls`：必须为 0；
- `llm_calls.actual_reduction_percent`：相对“300 条全部调用 LLM”的实际逻辑调用减少比例。

当前生产代码中的 Qwen 路由提示词没有允许 `document_task` 标签，报告会持续显示该警告。完成正式五分类测试前，应先统一 `QwenRouter` 与 `RouteType` 的标签空间，否则文档类边界样本的比较不公平。

## 4. 计算真实 WebGL GIS Agent 端到端成功率

GIS 不能只运行 Python 后端测试。必须启动开发者模式页面。默认 `direct` 模式由真实 Chrome 向 Agent 发请求，再使用生产 `AgentUIEventQueue` 做对象解析并执行真实 WebGL Bridge receipt；它只绕过调试面板的显示层。

先停掉可能存在的客户模式服务，再以本地管理模式启动：

```powershell
.\STOP.cmd
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -LocalAdminMode
.\STATUS.cmd
Invoke-RestMethod http://127.0.0.1:8009/api/agent/health
```

先列出当前页面能够提供给 Agent 的空间对象，重点选择 `flyable_objects` 中 `can_fly_to/position_valid/bounding_sphere_valid` 至少一个为真的稳定对象：

```powershell
node .\evaluation\run_gis_e2e.js --probe-runtime --headed
```

先跑已经存在真实对象的单例闭环 smoke test：

```powershell
node .\evaluation\run_gis_e2e.js `
  --case-id gis_pos_037 `
  --transport direct `
  --headed `
  --output .\evaluation\reports\gis_e2e_smoke.json
```

smoke 通过后，跑全部 80 条。该过程会逐条重新加载真实 WebGL 页面，耗时较长：

```powershell
node .\evaluation\run_gis_e2e.js `
  --split all `
  --transport direct `
  --headed `
  --runtime-selection-name "黑龙江省" `
  --output .\evaluation\reports\gis_e2e_eval_v1.json
```

报告重点看：

- `metrics.e2e_task_success_rate_percent`：60 条可执行任务严格成功率；
- `metrics.expected_handling_accuracy_percent`：20 条异常任务的澄清、拒绝或未找到处理正确率；
- `results`：逐题失败原因、事件顺序、receipt 和后置条件；
- `evaluation\reports\gis_artifacts`：发生超时或浏览器异常时保存的截图。

如果无头模式支持良好，可以删除 `--headed`；如果公司演示环境选中对象不是“黑龙江省”，将 `--runtime-selection-name` 改为页面中能够唯一检索且带坐标的稳定对象。

若还要单独验证调试面板的 SSE/交互层，可在小样本上把 `--transport direct` 改为 `--transport ui`。这个结果建议作为前端链路指标单独报告，不要和默认的核心 GIS 指令执行结果混在一起。

当前种子集中的“黑龙江省”“龙王站”只是运行时绑定候选。本机实测已发现“黑龙江省”在当前对象源中可高亮但没有有效的定位包围体，“龙王站”没有进入可检索业务对象源；正式跑 80 条之前必须把相应用例替换或绑定为演示环境中真实可执行的稳定对象。

运行完成后可停止服务：

```powershell
.\STOP.cmd
```

## 5. 汇总三类指标

当三个正式报告都存在时运行：

```powershell
& $Python -X utf8 .\evaluation\build_resume_metrics.py `
  --rag-k 5 `
  --gis-report .\evaluation\reports\gis_e2e_eval_v1.json `
  --rag-report .\evaluation\reports\rag_eval_v1.json `
  --router-report .\evaluation\reports\router_eval_v1.json
```

汇总结果：

- `evaluation\reports\resume_metrics_v1.json`：机器可读版本；
- `evaluation\reports\resume_metrics_v1.md`：便于查看和写简历的版本。

如果三个 Runner 的数据还没有人工锁定，汇总页会显示“测试/复核未完成，不应直接写入简历”。这是保护机制，不是脚本失败。

## 6. 人工复核后运行最终版

目前三套数据的 `review_status` 仍是 seed/requires review。逐条人工确认并把正式数据版本锁定为 `human_locked` 后，在三个正式命令末尾增加：

```text
--require-human-locked
```

这样只要还有一条未锁定，Runner 就会直接拒绝产生可用于简历的数字。最终简历建议使用统一的一次冻结运行结果，并保留数据集 hash、知识库版本、模型名、运行时间和逐题报告。
