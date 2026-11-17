# 测试导航

在项目根目录执行 `npm run check:backend`，或 `.venv/bin/python -m unittest discover -s tests -v`。

| 文件名称 | 重点 |
| --- | --- |
| `test_api*`、`test_project_layout.py` | HTTP边界、启动入口、包导入和资源路径 |
| `test_data_catalog*`、`test_observation*`、`test_algorithm_catalog.py` | 来源目录、资产版本及算法契约 |
| `test_native*`、`test_production*`、`test_spectral*`、`test_landsat*`、`test_io*` | 原生像元、定标、质量、格网及年度分类 |
| `test_collection*`、`test_processing*`、`test_adaptive*` | 时序合成、趋势、阈值分区及异步处理 |
| `test_agri*`、`test_crop*`、`test_city*`、`test_regional*` | 农业统计、区域产品、样本隔离及研究方法 |
| `test_assistant*`、`test_execution*`、`test_applications.py`、`test_codex*` | 问答、专题参数、受控规划和真实执行交接 |
| `test_scene*`、`test_result*`、`test_inspection.py`、`test_search*` | 台账、下载、版本固定、像元核查和检索 |

HTTP测试使用临时数据库，算法测试使用明确的隔离输入，不向本机业务库写入验证样例。部分栅格测试需要 `requirements-raster.txt` 中的依赖。共享夹具仍放在同级测试模块，保持 `unittest discover` 的单一入口。

前端隔离契约在 `scripts/verification/*.mjs`，运行 `npm run check:ui`。需要联网、正式数据或实际业务库的验证脚本须单独按说明调用，不属于默认检查。
