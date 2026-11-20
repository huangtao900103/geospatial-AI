# 后端代码导航

HTTP服务和工作空间装配位于 `server.py`；项目根目录的 `server.py` 只保留启动入口。所有资源路径由 `paths.py` 的 `PROJECT_ROOT` 定位，不依赖模块所在子目录。

| 目录 | 职责 | 主要入口 |
| --- | --- | --- |
| `core/` | 空间几何、地图、搜索、观测存储、像元核查和成果下载 | `geo_services.py`、`map_services.py`、`observation_repository.py` |
| `catalog/` | 数据源登记、公开目录适配、算法输入契约及业务能力目录 | `data_catalog_services.py`、`catalog_adapters.py`、`algorithm_catalog.py` |
| `integrations/` | 天地图代理与本机模型桥接、受控规划 | `tianditu_services.py`、`codex_gateway.py`、`codex_planner.py` |
| `analysis/` | 原生栅格读取、定标与质量掩膜、光谱指数、年度分类和时序处理 | `production_services.py`、`native_raster.py`、`raster_processing_services.py` |
| `agriculture/` | 农业分析、公开作物产品、区域成果、样本和时序研究 | `crop_reference_services.py`、`agriculture_atlas.py`、`agri_research_services.py`、`operations_services.py` |
| `workflows/` | 专题表单、文字问询、执行生命周期、台账和答案组装 | `application_services.py`、`assistant_services.py`、`execution_services.py` |

模块使用明确的包导入，如 `from backend.analysis.spectral_indices import specification`。测试中的补丁也应指向实际定义所在模块，不要指向根启动器。

连续光谱分析位于`workflows/analysis_workflows.py`，问答适配在`workflow_intents.py`；归档稳健趋势位于`agriculture/agri_trend_services.py`。构造流程服务不启动恢复，Workspace装配完成后调用`resume_pending()`，关闭时先停止流程再关闭底层服务。固定模板和HTTP接口见[连续分析指南](../docs/guides/ANALYSIS_WORKFLOWS.md)。

数据来源清单和参考文件保留在根目录 `data_sources/`、`map_sources/`；运行数据和本机配置位于 `data/`，随 `--db` 指定的工作空间组织。代码归类不代表已拆成独立微服务或分布式计算集群。
