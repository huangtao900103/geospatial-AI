# 连续分析与稳健趋势诊断

对应版本：0.19.0。客户继续在一张图提出问题，处理过程可展开查看，不增加主导航。

## 影像指数与自动分区

当前固定流程为 `spectral_partition`：明确单期观测 → 原生指数 → Otsu自动阈值分区 → 校验、上图与下载。支持NDVI、NDWI、NDMI；也可从已有成功原生指数成果开始。Otsu分区表示满足光谱阈值的区域，不能据此认定农田、水体或农业灾损。

1. 在地图框选单边不超过0.05°的分析范围，指定日期。
2. 从影像检索导入并选择真实Sentinel-2或Landsat 8/9观测。明确观测版本后不自动换景；缺少兼容波段、定标、质量数据或有效像元时如实停止。
3. 输入“对选中影像计算NDVI并执行Otsu自动阈值分区”。已有成果可用“对所选成果计算NDVI并执行Otsu自动阈值分区”。
4. 完成后直接查看阈值、条件面积、连通分区、配准地图及完整GIS成果包。“分析过程”展示两步的实际状态和来源文件校验。

问答由`POST /api/assistant/questions`接收，地图选区、观测和日期通过现有`context`传入。以下是直接服务调用的等效输入，ID须替换为本工作空间真实观测：

```json
{
  "template_id": "spectral_partition",
  "context": {
    "observation_id": "observation_REPLACE_WITH_REAL_ID",
    "source_id": "pc_sentinel2_l2a",
    "bbox": [113.36, 30.30, 113.37, 30.31],
    "period": {"start": "2025-11-14", "end": "2025-11-14"}
  },
  "parameters": {"index_id": "ndvi", "direction": "above", "min_patch_pixels": 1},
  "request_id": "your-unique-request-id"
}
```

坐标和日期仅说明字段格式，不作为平台默认业务输入。已有成果路线将`context`换为`{"source_type":"production","source_task_id":"实际成功任务ID"}`；不叠加另一个时期或观测。`above`表示保留大于等于自动阈值的像元，`below`表示小于阈值。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/analysis-workflows/capabilities` | 固定模板、指标与范围限制 |
| POST | `/api/analysis-workflows/prepare` | 校验并冻结计划；不创建计算任务 |
| POST | `/api/analysis-workflows` | 创建异步流程；相同request_id及输入返回原流程 |
| GET | `/api/analysis-workflows`、`/{id}` | 列表与真实节点状态、证据、结果 |
| POST | `/api/analysis-workflows/{id}/cancel` | 取消独占计算，保留其他活跃请求共享的子任务 |
| POST | `/api/analysis-workflows/{id}/retry` | 为失败或取消流程创建保留原计划的新流程 |

只有`status=succeeded`才有最终结果。每个节点保存冻结请求、真实任务绑定、来源内容与产物SHA256。恢复前重新核验成功前缀；前缀变化时返回冲突，要求重新确认输入。服务重启可重新关联已经记录的子任务；若中断发生在派发与保存绑定之间，则明确失败，避免自动重复派发。底层原生任务本身已因重启失败时，需显式重试。

这是一条有界两节点链，不是任意DAG执行器；尚未实现自由组合算法、自动换源或无人值守大区域生产。模型不能提供Shell、Python、URL、文件路径或任意输出表达式。

## 实际观测的稳健趋势

输入“分析仙桃已有归档NDVI时序的稳健趋势”，或对明确年度及完整归档时段提出同类问题。存在多个匹配归档时要求明确选择，不默认采用最新归档。当前问答适配仙桃已发布同源序列；直接工具使用明确系列ID与SHA，其他地市需先按同一质量契约建立归档。

工具在质量合格日期的**同一共同有效范围**内，以归档NDVI或NDMI均值计算：

`Sen斜率 = median((value[j] - value[i]) / 实际间隔日数)`

使用实际日期，不用观测序号代替时间；缺测不补零、不插值。报告包括按日斜率、按30日速率折算、实际日期清单、跨度、最大间隔、共同覆盖、排除原因和来源SHA。30日折算是速率表达，不是预测未来30日。

`POST /api/agri-trends/prepare`和`POST /api/agri-trends`接收：

```json
{"series_id":"实际已发布序列ID","series_sha256":"实际64位内容SHA256","index_id":"ndvi"}
```

`GET /api/agri-trends`和`GET /api/agri-trends/{id}`返回独立派生记录与JSON报告。源序列不改写；派生结果及整条记录分别校验SHA256，答案中的`trend_ref`固定对应报告。工具读取已有归档并做标量计算，没有重新读取全部原生影像。

当前只提供描述性诊断，`significance_status=not_assessed`。未建模季节与自相关，不给出显著性、置信区间、全年或年际趋势；没有农田或作物掩膜时，不把全市共同范围当作农田长势。没有生成逐像元斜率地图，前端不会用最近一景NDVI图冒充趋势图。

## 借鉴与验证

参考[Earth-Agent公开项目](https://github.com/opendatalab/Earth-Agent)的工具分工，以及[Earth-Agent-Pro论文](https://arxiv.org/abs/2609.12533)的执行证据、依赖与受影响后缀修复思路。采用本项目已有科学计算服务重新实现；未将其基准缓存、硬编码文件或预计算答案接入业务。Sen方法参见[SciPy官方方法说明](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.theilslopes.html)。

测试覆盖微型原生栅格的定量/GIS结果、观测绑定、幂等、版本冲突、失败前缀保留、共享取消和临时SQLite重启；浏览器验证真实两步处理、证据、成果包及地图。合成输入仅在临时工作空间用于测试，不写入业务库。外网COG可用性仍取决于具体提供方和实际请求，隔离测试不能代替在线可用性证明。
