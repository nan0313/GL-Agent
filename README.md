# EV WebGL Agent Portable Final

这是 Windows x64 本地可迁移版本，包含 Agent 源码运行集、完整 WebGL 运行时、本地 BGE 模型、已授权 active 知识资料和 conda-pack Python 归档。

## 首次安装

在普通 PowerShell 或资源管理器中运行：

```text
INSTALL.cmd
```

安装过程严格离线：校验静态文件、解压包内 Python、离线加载 BGE，并从包内知识资料重建 SQLite、embedding 和 HNSW。任何必需步骤失败都会返回非零状态。

## 启动与状态

```text
START.cmd
STATUS.cmd
STOP.cmd
```

正式入口：

```text
http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html#/index
```

Agent 监听 `127.0.0.1:8009`，WebGL 监听 `127.0.0.1:8090`。运行脚本只使用 `runtime/python/python.exe`，不调用系统 Python、Conda 或用户模型缓存。

## Qwen 边界

Qwen 是可选外部服务。未配置私有参数时状态为 `QWEN_DISABLED`；确定性 GIS、本地 RAG、BGE 和 HNSW 仍可运行。不要把密钥写入 HTML、脚本或清单。

## 业务资料

本包包含已授权业务资料，应通过受控介质传输。RAG 备份不替代原始业务文档归档。

## 完整性

`checksums.sha256` 覆盖全部静态交付文件（不含校验文件自身及安装后生成的 runtime）。`package_manifest.json`、`model_manifest.json`、`knowledge_manifest.json` 与 `runtime_dependency_inventory.json` 描述受控输入和运行边界。
