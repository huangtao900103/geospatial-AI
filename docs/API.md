# 本地业务 API

基础地址：`http://127.0.0.1:8016`。接口由 `server.py` 提供，遥感栅格生产依赖见 `requirements-raster.txt`。写请求为同源 `application/json`；不支持 CORS 外部写入。默认返回 JSON 对象或数组，错误统一为：

```json
{"error":{"code":"INVALID_FIELD","message":"可理解的错误说明"}}
```

## 目录与服务

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 服务、存储与引擎连接状态 |
| GET | `/api/bootstrap` | 工作空间、场景、组件、资源、集成、实际计数与能力限制 |
| GET | `/api/map` | 合成示例 FeatureCollection；支持 `scenario`、`region` 查询参数 |

地图为 EPSG:4326，经纬度 GeoJSON；36 个合成要素不代表真实仙桃地块或主体。`properties.kind` 为 farm、industry、alert、machine、water。区域支持仙桃市及张沟镇、彭场镇；镇级筛选使用合成数据中的 town 字段。

## 方案与运行

| 方法 | 路径 | 请求/返回 |
| --- | --- | --- |
| POST | `/api/plans` | `{requirement,region,period}` → 新方案 |
| GET | `/api/plans` | 历史方案数组 |
| GET | `/api/plans/:id` | 指定方案 |
| POST | `/api/runs` | `{plan_id}` → 新运行 |
| GET | `/api/runs` | 历史运行数组 |
| GET | `/api/runs/:id` | 当前状态、节点、日志、快照及成果 |
| POST | `/api/runs/:id/cancel` | `{}` → 取消未完成运行 |
| POST | `/api/runs/:id/retry` | `{}` → 为失败或取消任务创建新 runId |

方案保留需求原文、规则识别的多个 requirements、数据缺口 gaps、节点依赖及输入输出声明。方案不可原位更新；重新规划创建新方案。未知需求和不支持区域保留缺口，在实际执行时失败，不生成该区域的虚假成果。

运行状态为 queued、running、succeeded、failed、cancelled。节点依赖允许表达多个业务分支；本轮执行器按拓扑顺序串行执行，并不提供并行计算集群。重启后未完成记录标记 failed，可重试。

成功成果包括 `feature_count`、近似 `area_ha`、`geojson`、`summary`、`provenance`。来源信息关联 run_id、plan_version、region、period、source 与 review_status；实际执行模式为 `fixture-spatial-analysis`。面积使用局部等距近似，仅演示，不是测绘精度。

## 应用与版本

| 方法 | 路径 | 请求/返回 |
| --- | --- | --- |
| POST | `/api/apps` | 新建草案 |
| GET | `/api/apps` | 应用数组 |
| GET | `/api/apps/:id` | 指定应用 |
| PATCH | `/api/apps/:id` | 更新草案配置 |
| POST | `/api/apps/:id/publish` | `{scope:"workspace"}` → 发布不可变版本 |
| POST | `/api/apps/:id/clone` | `{}` → 新草案，关联 source_app_id 与 source_version |

草案配置：

```json
{
  "name": "仙桃农业一张图",
  "description": "专题应用示例",
  "modules": ["baseline", "eel", "coldchain"],
  "layer_ids": ["farm", "industry"],
  "theme": "midnight"
}
```

组件 ID 由 bootstrap 返回。空名称、空组件、重复/未知组件、无效主题及未知图层被拒绝。发布前版本为 0，发布后版本为 1 且不可 PATCH；更新时 clone 创建新应用，以来源版本建立谱系。本轮不提供同一应用 ID 下的历史版本分支编辑。

发布范围仅 workspace；这表示同一本机服务中共享模板，不具有用户权限隔离，不代表省域或公网开放。

## 集成草案

`POST /api/integrations/:id`：

```json
{"endpoint":"https://example.gov.cn/api","protocol":"REST API"}
```

只保存 endpoint 和 protocol，状态为草案。拒绝 URL userinfo、凭据参数和额外凭据字段。不访问地址、不验证服务、不接受访问密钥。

## 错误与边界

- 400：输入字段、JSON、图层、组件或服务地址不合法。
- 403：非本机 Host 或写请求跨源。
- 404：不存在的记录或路径。
- 409：终态不可取消、成功任务不可重试、发布版本不可修改。
- 413：请求体超出 256 KB。
- 415：写请求不是 JSON。
- 422：不支持的共享范围。
- 429：同时运行任务超过本机上限。

生产鉴权、租户、分页、大数据存储与通用外部模型连接不属于此 API 版本的已交付能力。后续 V5–V7 节说明实际接入的受限遥感适配器和本机并发上限。

## V3 数据与算法扩展

新增以下同源接口（旧路由保持兼容）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/datasets` | 数据集元数据列表，包括合成源 `xt-fixture` |
| POST | `/api/datasets` | `{name,geojson}` 导入并保存规范化GeoJSON |
| GET | `/api/datasets/:id` | 元数据及完整GeoJSON |
| GET | `/api/algorithms` | 三项算法与输入/限制说明 |
| POST | `/api/analysis` | `{dataset_id,algorithm_id,parameters}` 执行并保存成果 |
| GET | `/api/analysis` | 独立算法历史，不与旧runs混淆 |
| GET | `/api/analysis/:id` | 成果GeoJSON、统计、来源与参数 |
| GET | `/api/public/weather?lat=30.36&lon=113.45` | 公开模型资料，保留请求点/格点与数据时间 |
| GET | `/api/public/satellite?bbox=113.1,30.1,113.7,30.6&start=2025-09-01&end=2025-11-30&cloud=40` | Sentinel-2 STAC目录 |

`algorithm_id` 为 `spatial_summary`、`nearest` 或 `ndvi`。空间统计可传`group_by`（默认kind），最近邻可传`max_distance_m`（默认5000米），NDVI当前固定用red/nir属性且参数为空对象。

上传数据及算法成果在本机存储；结果保留dataset_id、dataset_version、sha256、algorithm_id、algorithm_version、parameters和生成时间。无适用要素、全部无效NDVI等返回422而非假成功。

V3写请求体上限统一为2MB；GeoJSON≤500要素、≤5000坐标点、单线/环≤300顶点。坏几何不会自动修复。公开来源仅固定HTTPS服务，5分钟缓存保留fetched_at、requested_location/grid_location或query；上游失败502，前端超时亦明确报错，不回退示例值。天气数值单位读取响应units，不自行猜测。此节替代前文V2的256KB说明。

## V4 真实影像与多源地图

| 方法 | 路径 | 返回 |
| --- | --- | --- |
| GET | `/api/map/config` | 初始中心/缩放、两项影像底图、分层来源与能力边界 |
| GET | `/api/map/layers/:id` | 独立来源GeoJSON；agriculture/industry/machinery/water/alerts/weather/rivers/satellite |
| GET | `/api/tiles/{source}/{z}/{x}/{y}` | 固定imagery或nasa源JPEG/PNG；错误为结构化JSON，不假装图像 |

imagery最大原生z18、nasa最大原生z9；前端放大NASA只作插值显示。z/x/y须为合法非负整数且x/y<2^z；任意URL参数拒绝。图层config明确default_visible、source_type、feature_count、mode、署名与源地址。水系仅为1:1000万概化公开快照；天气参考点按需读取模型，卫星覆盖按需读取公开目录。

瓦片只在内存作64条/16MB/最长5分钟缓存，尊重no-store/max-age/Age。最多6个外部请求，正常视窗突发进入有界48等待队列，队列满或12秒超时才503；上游图像错误也503并说明原因。无磁盘影像、离线打包或任意外部代理。

V4最终浏览器传输：底图配置的`url`保留固定本地代理，`browser_url`为代码内限定的供应商HTTPS模板，`browser_transport=direct_public_provider`。Leaflet优先原生直连，使用供应商缓存和浏览器连接复用，不受本地代理8秒读超时限制；NASA仍限定原生9级。上述LRU/队列/大小限制仅描述本地代理，不冒称控制供应商或浏览器缓存。

## V5 遥感生产

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/production/capabilities` | 当前环境、两种模式、网格大小与边界 |
| POST | `/api/production/tasks` | 一次提交生成不可变计划并交后台执行 |
| GET | `/api/production/tasks` | 本机任务与状态 |
| GET | `/api/production/tasks/:id` | 计划、节点、日志与结果 |
| POST | `/api/production/tasks/:id/cancel` | 取消未结束任务 |
| POST | `/api/production/tasks/:id/retry` | 创建新任务，保留原任务 |

请求含`requirement`、`mode=sample_pixels|sentinel_cog`；可选`region,bbox,goal,threshold,cloud,periods,direction`。受限规则识别年月、ISO日期、NDVI/两期变化、方向与绝对阈值比较。高级显式参数优先；未支持目标和相对百分比变化保留缺口，不静默执行相反任务。

`plan.selection`记录筛选字段、比较符、阈值及来源；`snapshot`保留完整计划。节点保存真实起止时间、输入输出摘要。成果含GeoJSON、summary、report、provenance、artifacts；report.steps来自实际节点。

公开模式使用Microsoft Planetary Computer固定目录与白名单COG。公开SAS仅在内存；红/NIR按产品XML校准，SCL筛选后统一到EPSG4326代表网格。当前16×16，人工样本8×8；共同有效率不足10%不发布，低于50%警示。bbox单边≤0.05°，原生读窗≤2048×2048。网格面积不是变化地块面积。旧`/api/public/satellite`仍仅返回目录。

## V6 全市分类面积

`GET /api/landcover/capabilities`返回真实边界、固定2021产品及运行依赖；`POST /api/landcover/tasks`接受`region=xiantao, product=worldcover_2021_v200, requirement`，自动后台执行。原文异年份、全省或其他城市422，不以产品字段静默覆盖。`GET /api/landcover/tasks`及`/:id`恢复任务；`POST /:id/cancel|retry`取消或新建重试。

完成结果含`summary/classes/boundary/image/report/provenance/artifacts`。`summary`区分cropland_ha/mu、total_area_ha、valid_area_ha、nodata_area_ha、coverage_ratio、cropland_share、原生像元计数与参考年度；ci95未提供，不将覆盖率称精度。`image`为EPSG3857配准PNG显示，不参与统计。

`GET /api/landcover/tasks/:id/{overlay.png|classes.png|classification.tif|row_counts.csv|result.json}`只允许固定命名、已完成任务产物，不开放任意文件路径。分类GeoTIFF保留原生网格；逐行CSV允许独立面积复算。来源固定官方WorldCover2021 COG和已核验本地参考边界，任务不接收任意URL。

## V7 一张图业务问答

客户前端只需提交问题并轮询状态。数据选取、结果复用、任务创建和回答组装在后端完成。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/assistant/capabilities` | 当前固定业务能力及示例问法 |
| POST | `/api/assistant/questions` | 创建独立问题记录，自动匹配能力并处理 |
| GET | `/api/assistant/questions` | 问题记录数组，含真实状态与回答快照 |
| GET | `/api/assistant/questions/:id` | 恢复指定问题、来源任务及当时答案 |
| POST | `/api/assistant/questions/:id/cancel` | `{}`，取消该问题的等待，不中断其他问题共享的任务 |
| POST | `/api/assistant/questions/:id/retry` | `{}`，为失败、取消或待补信息的问题新建记录 |
| GET | `/api/assistant/shortcuts` | 本机保存的常用问法 |
| POST | `/api/assistant/shortcuts` | `{name,question,source_question_id}`，仅保存已成功回答的原句 |

最小请求：

```json
{"question":"仙桃全市耕地有多少亩？"}
```

能力列表的 `available` 表示当前客户地图入口是否已接线。`vegetation_change` 在栅格依赖就绪时为true；客户可以直接在地图选范围自动续问。

可选 `capability_id` 只作为入口提示，不覆盖问题中明确的区域、年份或统计口径。V9可选 `context` 接受 `reference_question_id`、`source_task_id`、`bbox`、`periods`、`period`、`dataset_id`、`region`、`clarification_of`、`reference_execution_id`。追问通过 `reference_question_id` 锁定当时关联成果；引用冲突、记录缺失或成果文件缺失时明确失败，不改绑最新结果。

问题状态为 `processing`、`answered`、`needs_data`、`unsupported`、`failed`、`cancelled`。业务范围不支持通常返回已记录的问题及状态，并非服务器故障；字段或引用不合法仍返回结构化错误。`progress` 和 `logs` 来自后端任务，没有前端定时假进度。

`binding` 关联真实 `landcover` 或 `production` 任务；`answer` 含 `title`、`summary`、`metrics`、`source_year`、`boundary_year`、`warnings`、`chart`、`map_effect`、`result_ref`、`downloads`、`detail_ref` 和 `provenance`。`answer_snapshot` 保留回答时的不可变内容。多个问题可以共享任务，但不会覆盖彼此的答案。`result_ref.image_kind` 区分 `cropland` 与 `classes`；`map_effect=none` 表示来源/下载追问不改变当前地图。

普通全市问题优先复用产品、年度、市界指纹和统计方法一致且产物齐全的真实成果；无可用成果时自动创建实际核算。明确“重新核算”创建新任务，但数据仍为2021产品，不代表更新至当前年度。永久水体只对应分类80，树木覆盖只对应分类10；均不能替代全部水域、养殖水面或法定林地口径。

常用问法存储原句、能力和来源问题，不保存一个静态数字答案。重新调用仍经过相同问答链路和来源校验。本机工作空间共享不等于多租户权限体系或省域能力市场。

## V8 多源数据目录、发现与计划

本节是独立数据接入层。创建目录检索或选源计划不会创建面积/NDVI任务，不会将候选影像视为已分析成果。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/data-sources` | `{schema_version,sources,catalog_fingerprint,...}`，统一来源及能力阶段 |
| GET | `/api/data-sources/:id` | 指定来源元数据；动态用户数据源ID须按URL组件编码 |
| POST | `/api/data-sources/search` | 固定公开STAC集合的真实时空目录查询，保存发现快照 |
| GET | `/api/data-searches`、`/:id` | 检索历史与各来源的成功、无命中或失败信息 |
| POST | `/api/data-plans` | 从业务条件生成不执行分析的选源计划 |
| GET | `/api/data-plans`、`/:id` | 不可变计划、候选、缺口及实际使用证据 |
| POST | `/api/data-sources/drafts` | 保存未连接的接口意向；不访问地址或保存凭据 |

真实目录检索示例：

```json
{
  "source_ids":["pc_sentinel2_l2a","pc_sentinel1_rtc","pc_landsat_c2_l2"],
  "bbox":[112.922,30.094,113.832,30.537],
  "start":"2025-10-01",
  "end":"2025-11-30",
  "limit":2
}
```

当前只接受固定PC集合，最多3个不重复来源，每源1–6条、最多一页；bbox为WGS84经纬顺序，单边≤5°且不跨180°，日期窗口≤366天。返回 `discovery_only=true` 与每源 `results`，保留查询范围、抓取时间、返回条目和来源指纹。部分上游失败可以与其他成功项并存；无命中不同于网络失败。不返回SAS令牌，不自动跟随目录分页或下载资产。

来源 `stages` 独立声明 `registered/catalog_searchable/pixel_readable/pixel_applicable/analysis_ready`；`verification` 是本工作空间最近实测，不能由能力声明伪造成功。非栅格来源像素读取不适用。全球集合范围不表示本机分析执行器可处理全球或任意尺度。`license` 原值/条款、`cost`、`auth` 与适配器边界分别保存。

计划最小请求为 `{question}`；可选 `region,bbox,period:{start,end},goal,source_ids,context`。目前目标包括 `cropland_area/ndvi/vegetation_change/weather/flood_extent/general_discovery`。显式参数不能无声改写原文中明确的年份、地区或业务指标。`context` 仅用于关联实际任务：`{task_type:"landcover"|"production",task_id}`。

计划的 `selection` 与 `candidates` 是拟议来源；`used` 与 `execution_evidence` 仅来自已成功任务的真实provenance。目标或时空不匹配的旧任务列为 `related_existing_result`，不能成为本次已用数据。计划始终 `not_executed=true`，`executable` 仅指当前已实现接口能接受满足条件的执行契约，未证明网络、像元质量或最终精度。缺少授权、时段、范围、波段/模态或算法时，`gaps` 明确阻塞并不发出可执行合同。

计划保存 `source_snapshot/catalog_fingerprint`，不随当前目录或后续验证记录改写。新成功问答会将 `answer.data_plan_ref` 及问题 `data_plan_id` 一起写入答案快照；旧答案没有该字段仍可正常恢复。已有成功结果的溯源问题可以生成只描述来源的记录，不自动启动新分析。

不支持的高标准农田、法定/官方、播种及作物细分口径产生 `METRIC_UNSUPPORTED`；明示月份/ISO日期与结构化period冲突产生 `PERIOD_CONFLICT`；年度WorldCover不能声称回答具体月/日统计，产生 `ANNUAL_PRODUCT_ONLY`。这些业务缺口以计划的blocking gaps表达，不返回替代执行合同。

草案请求为 `{name,source_type:"ogc"|"business"|"iot"|"catalog",endpoint,protocol}`。保存后所有真实读取与分析阶段保持未连接；地址不能包含URL用户密码、token/API key或签名查询参数。正式对接所需凭据管理、许可核验与受控连接器仍属后续工作。

扩展原则和分阶段接入要求见 [MULTISOURCE_ARCHITECTURE.md](MULTISOURCE_ARCHITECTURE.md)。


## V9 统一真实执行

`POST /api/assistant/questions` 现在默认委托真实执行器，保留问答兼容契约。可选 `request_id`（≤80字符）用于客户端超时重试：同ID同输入返回原问题，改输入返回409。`context.clarification_of` 指向needs_data问题，保留原文并补充结构化范围/时相；可以在原文后追加时间说明，新执行保存父链。成功才创建答案快照；不支持/失败不会返回替代结果。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/executions/capabilities` | 已注册的真实工具、实际操作、限制与规划方式 |
| POST | `/api/executions` | `{question,context?,request_id?}`，持久化并开始执行 |
| GET | `/api/executions` | 全部执行记录 |
| GET | `/api/executions/:id` | 状态、不可变计划输入、工具调用、子任务步骤、来源与真实结果 |
| POST | `/api/executions/:id/cancel` | `{}`，停止独占子任务，共享子任务继续 |
| POST | `/api/executions/:id/retry` | `{}`，冻结原问题/业务输入，新建父子关联执行 |
| POST | `/api/executions/:id/continue` | `{context,request_id?}`，仅为needs_data补条件；原始问题不改变 |

执行context仅接受 `bbox`、`period`、`periods`、`dataset_id`、`region`、`reference_execution_id`；不接受任意URL或自定义工具代码。问题上下文/时期/输入版本固定后调用白名单函数，不通过执行计划中的HTTP地址任意取数。状态为queued/planning/running/succeeded/needs_data/unsupported/failed/cancelled。

每次执行保存 `plan_snapshot`、`steps`、`tool_calls`、`child_steps`、`binding`、`result_ref`、`result`、`summary`、`error`。`summary`为给人的说明字符串。问答中的 `execution_ref` 让前端跟踪实际节点，不使用计时器模拟完成。目录只返回真实目录发现，天气只返回提供方模型值，未实现的业务推断不会通过降级成目录查询而成功。

原生公开光谱工具始终提交 `{mode:"sentinel_cog",sampling:"native"}`。旧专业组件不指定sampling时保留代表网格兼容模式，不能作为V9原生缓存使用；缓存还校验当前算法版本与固定产物齐全。原生输出 `image`（配准PNG）、单AOI边界 `geojson` 与下载清单，不向浏览器生成数十万个像元多边形。

固定下载路径：`GET /api/production/tasks/:id/{analysis.tif|overlay.png|result.json}`，仅成功原生任务可用，拒绝任意路径。GeoTIFF以末期B04米制投影格网为参考，前期/NIR/SCL最近邻对齐；统计不从PNG复算。每个日期窗口选一景合格观测，实际日期保留在 `provenance.source_details.scenes`。有效比例是质量覆盖，不能当作分类准确率。

问答结果新增 `source_description` 显示实际观测日期；`data_plan_ref`与`execution_ref`区分来源规划和已运行记录。LC仍保留产品年/市界年。来源/下载引用原执行，map_effect为none；天气/目录卡片也不切换地图成果。原生常用问法保存业务context，不保存答案数字。


## V10 可注册光谱算法与读取效率

`GET /api/production/capabilities`新增/扩充 `indices`，声明六项真实算法：NDVI、NDWI（绿光/NIR）、MNDWI（绿光/SWIR1）、NDMI（NIR/SWIR1）、NDBI（SWIR1/NIR）、NBR（NIR/SWIR2）。各项含id、label、formula、positive_band、negative_band、required_bands（原生分辨率/XML band ID）、reference_band/reference_resolution_m、version、available、source_url与limitations。`goal=ndvi`继续作为单期指数目标的兼容ID，不将所有结果都称NDVI。

`POST /api/production/tasks`可带 `index_id`；显式问题和该字段冲突、多指数一次提交或未实现指数均拒绝用NDVI代替。新专业前端真实模式固定`sampling=native`。客户仍通过`/api/assistant/questions`提交问题，在显式bbox、年度和口径条件成立后实际执行；命名其他城市的“所选区域”采用明确bbox，不声称已解析该城市行政界。全市/全省请求不能用局部选区代替。

原生算法版本`3.1-native`。每期只读所需两光谱波段和SCL；单期只读取after。含SWIR指数使用B11/B12的20m原生格网为参考，其他波段最近邻对齐；NDWI为B03的10m参考。SCL4/5/6/7保留，反射率有限非负且分母>0才有效；SCL7未分类计数和不确定性独立报告。

新结果统一 `summary.index`、`index_id`、`mean_index`、`mean_before_index`、`index_histogram`、`min_index/max_index`；`mean_delta`仍后减前。NDVI保留旧别名，其他指数不生成mean_ndvi。grid声明实际reference_band/reference_resolution_m，图例随指数生成。阈值只是输入参数，不成为经验证业务分类。

新五指数的`analysis.tif`含指数/差值、common_valid_mask、aoi_mask，并追加每期两项已定标对齐反射率及SCL。例如单期MNDWI六band为 `after_mndwi,common_valid_mask,aoi_mask,after_green_reflectance,after_swir1_reflectance,after_scl`。per-band tags标来源与单位，`provenance.raster_evidence`说明这些是已对齐反射率而非原始DN；原始源窗口SHA与XML定标仍在source_details。旧NDVI3/5band保持兼容。

`source_details.efficiency`及summary同名字段记录 `source_windows`、`source_window_cache_hits`、`uncompressed_source_window_bytes`、`cache_hit_source_window_bytes`、`read_seconds`和每窗命中/原fetched_at/TTL。字节指原始float32数组，不是HTTP流量。源窗口缓存限64MiB数组/128条/30分钟，进程重启失效；仍可能进行目录检索、XML定标和COG元数据打开。未定标窗口只读复用，内容指纹不含缓存时刻或冷热指标。

SCL预检已不足10%时提前失败，不再读取两光谱波段。缓存异常/取消不会变成成功答案，也不会切换人工样本。多个算法共享窗口是读取优化，不能由此声称已完成传感器融合或省域集群计算。

## V11 结构化专题与地图工作台

`GET /api/applications`返回`{version,applications:[...],workflow,limitations}`。8项业务专题与1项专业模板各含id/name/category/description/ready/featured、scope/scope_label、form（requires_bbox、period_mode、dataset_required、fields与max_bbox_span_deg）、defaults、outputs、method及limitations。区别于旧`/api/apps`：后者是本机应用设计草案，不是已可执行的业务模板。

`POST /api/application-runs`：

```json
{
  "application_id":"water_clues",
  "context":{"bbox":[114.20,30.50,114.21,30.51],"period":{"start":"2025-11-01","end":"2025-11-30"}},
  "parameters":{"threshold":0,"operator":"gt"},
  "request_id":"unique-client-request-id"
}
```

返回完整assistant question协议，新增只读`application_ref={id,name,version,context,parameters,method,scope_label}`。在后台launch之前冻结，request_hash同时包含专题，避免不同专题生成同一句计算需求时错误合并。前端按原`/api/assistant/questions/:id`轮询、取消、重试；成功后沿实际execution_ref/result_ref呈现。retry保留原application_ref；修复取消发生于执行创建期间的未关联作业泄漏。

context只接受bbox/period/periods/dataset_id，parameters只接受该模板声明字段。固定土地覆盖拒继承bbox/任意日期或用户数据；单期与两期日期不可互换，前后窗口不重叠；每窗口≤183天。光谱bbox单边≤0.05°，目录/天气不受该像素跨度限制。未知指数/非法关系/非有限阈值/合成图层等在建立任务之前拒绝，不能用其他方法代答。

### 搜索

`GET /api/map/search?q=武汉&limit=12`返回`{query,results,warnings,provider,place_coverage?}`，limit为1–20。结果kind为place/coordinate/result/dataset，基本字段id/label/subtitle/center:[lon,lat]/source/source_type；可有bbox、zoom、source_url、task_id/task_type/result_ref/question_id/dataset_id。

地名来自带出处和CC BY4.0的本地GeoNames快照，2124个参考点含湖北56个城镇点。`aoi_eligible=false`明确其不是分析边界。坐标支持经度在前的逗号/中文逗号，以及明确经纬度标签；非法数值返回warnings中的INVALID_COORDINATES，不猜测颠倒坐标。warnings是`{code,message}`对象数组；无结果为NO_MATCH，说明地名覆盖限制。

成果搜索遍历全部持久化成功任务，不依赖前端显示状态；索引专题名、原需求、成果标题、实际观测日期与任务ID，按子任务去重，恢复匹配原问题的classes/cropland等地图模式。来源/下载追问不作为主地图恢复记录。用户图层名称和要素名可搜，合成来源明确标识。

### 像元核查

`GET /api/map/inspect?kind=production&task_id=...&lon=114.205&lat=30.505`。kind仅production/landcover；只允许成功任务的固定本地analysis.tif/classification.tif，按真实CRS转换和1×1窗口读取，无外网/新任务。

响应含task_id/kind/coordinates/status/message/inside_aoi/valid/value/value_band/metric_label/bands/row/column/crs/actual_dates/source_year/reference_resolution_m/limitations（按实际可用字段）。status为valid/invalid/outside_aoi/outside_raster。两期主值是delta_index（后减前）；共同无效时即使after有限也value=null。所有NaN/NODATA为null。LC以归档边界重建该格中心掩膜，不把栅格bbox当市界。旧代表网格/样本无原生文件则明确不支持。

`GET /api/analysis/:id/result.geojson`以`application/geo+json`直接返回实际结果FeatureCollection，统计与来源仍由`GET /api/analysis/:id`提供。业务图层卡片使用中文要素/几何统计，不显示Python字典repr；只有面要素存在时展示面积合计及近似/未去重说明。

## V12 天地图服务

配置与通用`integrations`草案分离，不写入`records`表。

| 方法与路径 | 输入 | 行为 |
| --- | --- | --- |
| `GET /api/integrations/tianditu` | 无 | Key存在与来源、各项连接检查、官方申请链接；不返回Key或其片段 |
| `POST /api/integrations/tianditu` | `{token}` | 原子保存0600私有配置，状态为configured；环境变量管理时409 |
| `POST /api/integrations/tianditu` | `{action:"clear"}` | 清除本机文件；环境变量仍生效时明确提示 |
| `POST /api/integrations/tianditu/check` | `{}` | 实际请求地图四图层、搜索、逆地址、行政区划；逐项状态，不生成替代数据 |
| `GET /api/tianditu/tiles/{layer}/{z}/{x}/{y}` | layer为img/cia/vec/cva，z为1–18 | 固定HTTPS WMTS上游，仅原始真实PNG/JPEG，无Key模板、无批量打包、no-store |
| `GET /api/map/search?q=...&remote=0/1` | remote默认1；输入联想用0 | 保留V11本地结果；显式remote且已配置时合并天地图地点，错误保留本地命中；纯坐标和精确工作空间命中无需外网 |
| `GET /api/tianditu/reverse?lon=...&lat=...` | 有限经纬度 | 原地址、组件、查询坐标、来源与时间；导航参考 |
| `GET /api/tianditu/administrative?keyword=...` | 1–100字符名称/编码 | V2候选、建议、独立参考轮廓及错误；不自动选首项或从定位框生成面 |

行政返回`results[]`每项包含`id/name/code/level/boundary_status/geojson/message/source/source_url/fetched_at/data_version`，有真实有效WKT时增加bbox与vertex_count；几何不可用则geojson=null。FeatureCollection保留原MultiPolygon与内环、来源和未知数据年份。建议候选只提供重新查询入口，不冒充已取得几何。

`status.services`区分tiles/search/reverse/administrative，tiles下可见img/cia/vec/cva各项检查。configured不等于verified；重启后需重新检查。无Key返回`TIANDITU_NOT_CONFIGURED`，配置不会使光谱像元/分类算法的ready状态变为true。

状态响应、地图配置、错误和可下载边界均不含Key。配置写入沿用JSON Content-Type、localhost Host和同源Origin校验。第三方HTTP错误用有界脱敏消息，不把包含tk的上游URL写入响应或日志；禁止跳转携带Key。

## V13 观测/资产库与AI工具契约

| 端点 | 输入与语义 |
| --- | --- |
| `POST /api/data-sources/search` | source_ids限登记的7集合，每源最多6条，最多7源；并行固定STAC请求，成功/无结果/失败逐源保留。现在保存assets定标、波段、质量与无签名地址。 |
| `POST /api/observations/ingest` | `{search_id,source_id?,item_ids?}`；仅接受真实已保存目录成员。返回inserted/updated/total/items，不访问任意URL。 |
| `GET /api/observations` | source_id/collection/start/end/cloud_max/q/bbox/limit/offset；bbox用逗号分隔。返回items/total/stats，stats包括observations/assets/sources/pixel_verified。 |
| `GET /api/observations/:id` | 完整资产版本、metadata_sha256、实际pixel_uses、来源与可准备分析条件。 |
| `GET /api/algorithm-contracts` | 六指数×两个来源的12份输入/定标/质量/尺度/输出声明；contracts与algorithms为兼容别名。 |
| `POST /api/algorithm-matches` | `{observation_id,index_id?}`；返回matches/ready_count，metadata_only=true、pixels_read=false。 |
| `GET /api/ai-tools` | 六项HTTP操作的JSON Schema、调用顺序、执行约束；model_service_configured=false。 |

光谱专题parameters新增`source_id: auto|pc_sentinel2_l2a|pc_landsat_c2_l2`；auto按时期/原文明示选择，优先S2，不静默失败换源。单期context可加observation_id，固定该资源的真实scene。修改来源/日期时前端明确解除，后端仍校验范围、时段、传感器、版本和必要资产。

production底层仍沿用兼容的mode=sentinel_cog名称，通过plan.source_id选择实际S2或L8/9读取器；实际provider、reference_resolution_m、质量与attribution来自实际执行。原生任务、请求幂等及旧接口保持。

V13 sourcecheck记录新增source_id，id为独立快照；当前状态按最新快照读取，历史检测记录不覆盖。EO表为追加结构，原records表不做结构迁移。


## V14 选区耕作地标注

`GET /api/cropland/capabilities`；`GET/POST /api/cropland/tasks`；`GET /api/cropland/tasks/:id`；`POST .../:id/cancel`与`retry`；`GET .../:id/:filename`。创建只接受`{bbox,product?,requirement?}`，产品固定`worldcover_2021_v200`。文件下载仅成功任务及固定文件白名单。

客户应优先调用`POST /api/application-runs`，application_id为`cropland_delineation`，context只传bbox，无需波段、日期或指数。`GET /api/ai-tools`新增`delineate_cropland_2021`的机器可读契约。结果引用type为`cropland`，空间搜索、来源计划、历史恢复与像元核查均支持。详见[V14输入、产物与方法](V14_CROPLAND.md)。


## V15 多产品、业务接入要求和完整成果包

- `GET /api/applications`新增capability_kind、capability_label、readiness_note、business_scenarios；耕作地模板method.product_profiles列出2021/2024的年份、类别和面积方法。
- `POST /api/application-runs`的cropland_delineation须明确parameters.product：`io_lulc_2024`或`worldcover_2021_v200`。未提交产品的旧表单返回APPLICATION_PRODUCT_REQUIRED，不静默改年度。直接`POST /api/cropland/tasks`旧缺省仍为WC2021。
- 自由提问context可带cropland_product，scope_confirmation需明确地图选区或市县整体。月份/日期窗口/季节不能由年度分类产品代答。
- `GET /api/business-scenarios`：六类业务接入准备清单，不创建虚构业务记录。
- `GET /api/results/:kind/:id/manifest`与`bundle.zip`：cropland/production/landcover/analysis成功任务的完整资料，逐文件SHA256，512MiB上限，缺失或变更则失败，ZIP下载名含任务ID。
- `GET /api/ai-tools`有明确产品的delineate_cropland_product与download_result_bundle契约；旧2021工具也要求显式WC2021参数，通用模型仍未配置。
