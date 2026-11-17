# V13 遥感资源数据库与算法调用

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

本机以SQLite保存目录、观测和资产索引，按窗口读取COG像元，计算成果保存为GeoTIFF/地图预览/来源报告。新增表与接口沿用现有工作空间，不迁移或改写历史业务记录。

## 客户流程

“数据 → 目录检索”查询真实提供方；点击“入库本次命中”，在“影像资源库”筛选并查看资产。选择“匹配分析 → 准备分析条件”后绑定指定观测，在地图明确选择小范围并提交。修改来源或日期会解除绑定并提示。

```mermaid
flowchart LR
 A[固定STAC服务] --> B[真实检索快照]
 B --> C[观测与资产版本库]
 C --> D[算法输入条件匹配]
 D --> E[冻结观测 资产 时段 范围]
 E --> F[按源定标与质量检查]
 F --> G[原生格网统计与GeoTIFF]
 G --> H[答案 地图 来源与读取证据]
```

## 数据层

| 表/对象 | 作用 |
| --- | --- |
| eo_observations | 来源、集合、item_id、观测时刻、平台、云量、完整元数据SHA；同元数据重复发现只更新last_seen，变更生成独立版本 |
| eo_assets | 每个版本的资产键、通用波段、原始地址、媒体、单位、比例/偏移、nodata、原生分辨率与CRS |
| eo_extents | 带索引的空间包络，用于本地bbox粗筛；真实执行仍核验范围和有效覆盖 |
| eo_discovery_members | 资源版本与真实检索快照的关联，保留最初目录条件、时间与来源 |
| eo_pixel_uses | 已成功任务、实际输入哈希及锁定观测版本一致后登记的读取证据 |
| data_search / data_source_check | 原有业务记录中的目录快照和最近检测；V13检测改为追加快照，原检测保留 |

入库不等于下载全部原始影像；metadata_ready、像元已验证和算法成功分别记录。跨提供方同景会保留各自来源，资源版本数不是独立物理观测数。SAS与账户Key不保存到资产库或可分享报告；运行时由固定读取器申请必要公开访问令牌。

RGB预览可能包含red通道，算法锁定采用来源特定资产键（如B04、red）核对输入，不用预览通道替代分析波段。S2产品的XML定标在执行时核查，元数据未声明scale/offset时保持缺失。

## 来源与实现阶段

| source_id | 产品 | 当前实现 |
| --- | --- | --- |
| pc_sentinel2_l2a | Sentinel-2 L2A | 目录、资产元数据、XML定标、SCL、10/20m原生指数与变化 |
| pc_landsat_c2_l2 | Landsat C2 L2 | 全集合目录；L8/9有30m SR/QA/指数执行；L4/5/7仍仅目录 |
| pc_sentinel1_rtc | Sentinel-1 RTC | 公开目录与资产索引；像元需账户及SAR算法适配 |
| pc_cop_dem_glo30 | Copernicus GLO-30 DSM | 实际空间目录与资产索引；无地形算法执行器 |
| cdse_sentinel2_l2a | CDSE Sentinel-2 L2A | 新版公开STAC目录及STAC1.1资产索引；像元另需账户 |
| earthsearch_sentinel2_l2a | Earth Search S2 | 固定目录适配；本机当前DNS失败，保留失败状态 |
| earthsearch_cop_dem_glo30 | Earth Search GLO-30 | 固定目录适配；本机当前DNS失败，地形目录优先用PC通道 |

GLO-30为包含建筑/植被的地表模型，目录参考时刻不是任意请求年度的实时地形；检索该静态产品不应用观测日期过滤，返回明确temporal_filter_applied=false。许可、分辨率与授权保留提供方声明。天地图仍用于显示、地名和公开行政参考，不提供NIR/SWIR计算输入。

## 算法契约与实际执行

已注册NDVI、NDWI、MNDWI、NDMI、NDBI、NBR六类指数，按S2与L8/9分别形成12份执行契约。契约列出所需光谱/质量资产、单位、定标、传感器、窗口/日期上限、参考格网和输出。

- S2读取所需两个波段与SCL，按实际产品XML定标；NDVI/NDWI用10m参考，涉及SWIR的指数用20m参考。
- L8/9读取实际STAC声明的SR比例0.0000275、偏移−0.2、nodata0；QA_PIXEL位0–5和非零QA_RADSAT排除，water位保留；30m原生格网。负反射率不裁剪后伪装有效。
- 每个时期选一景；两期来自同一传感器适配器，不进行跨源混景。单景资源锁定只用于单期。
- 请求范围单边≤0.05°，原生格网≤1,048,576；超限不会用参考小窗替代全市/全省。
- 单景绑定后，读取前核对真实scene、时刻、集合、平台、必要资产键/地址、已声明定标及元数据指纹；变化报OBSERVATION_VERSION_CHANGED，要求重新检索入库。
- 任务成功后才登记像元使用证据；重复完整相同条件可复用真实成果。来源、观测版本、算法、格网与质量策略均参与复用判断。

这些指标用于光谱线索。耕地现状分类、全域多景合成、作物识别及其精度评价仍需独立数据与算法，不能由指数个数推导已完成。

## 给后续AI的调用入口

`GET /api/ai-tools`提供六个受控HTTP操作及JSON Schema：发现目录、入库、检索本地资源、匹配算法、绑定观测运行、查询完成状态。`GET /api/algorithm-contracts`提供12份执行契约。客户端不需要生成或提交Shell/Python代码。

顺序为：

1. `POST /api/data-sources/search`，明确source_ids、bbox、start/end。
2. `POST /api/observations/ingest`，引用真实search_id。
3. `GET /api/observations`，按来源、日期、云量、bbox筛选。
4. `POST /api/algorithm-matches`，指定observation_id和index_id。
5. `POST /api/application-runs`，application_id=spectral_lab，提交观测ID、小AOI、包含观测日期的period及明确来源/指数。
6. 查询返回问题ID，只有answered才读取数值、地图图层和downloads。

完整请求样例可按`/api/ai-tools`生成；真实运行输入见[正式验收数据](../../output/v13-data/formal-validation-summary.json)。通用模型服务仍未配置，当前是可供模型调用的数据与执行基础，已有业务规则和结构化专题可直接执行。
