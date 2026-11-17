# API参考

基础地址为`http://127.0.0.1:8016`。根`server.py`是启动入口，HTTP路由与Workspace装配在`backend/server.py`，领域实现位于`backend/`各包。接口路径不随本次目录整理改变。

本页列当前正式功能。字段和工具的机器可读契约优先查询`GET /api/applications`、`GET /api/ai-tools`及各专题`capabilities`；[旧API与阶段变更](../archive/API.md)只作追溯。

## 请求、状态和错误

写请求采用同源`application/json`，不开放外部CORS写入；服务绑定本机回环地址，并验证Host与Origin。返回对象或数组，错误格式为：

```json
{"error":{"code":"INVALID_FIELD","message":"可理解的错误说明"}}
```

问答提交后返回持久化问题ID，查询至`answered`才读取完成答案。`processing`、`needs_data`、`failed`、`cancelled`均不表示完成。执行和底层影像任务另有自己的状态与引用，不将目录结果或计划当作像元分析成果。

## 状态与目录

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 本机服务、模型、引擎与集成状态；不保证所有外部源实时可用 |
| GET | `/api/bootstrap` | 工作空间当前配置、业务资源和能力限制 |
| GET | `/api/applications` | 已登记专题、表单、默认值、执行能力与限制 |
| GET | `/api/executions/capabilities` | 已登记执行工具及参数约束 |
| GET | `/api/ai-tools` | 受控HTTP工具的JSON Schema与调用语义 |
| GET | `/api/assistant/model` | 模型CLI安装、登录、配置和实际推理状态，不返回凭据 |
| GET | `/api/business-scenarios` | 业务接入准备要求，不创建虚构监管记录 |

## 专题与问答

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/application-runs` | `{application_id,context,parameters,request_id?}`，校验专题参数并启动标准问答任务 |
| POST | `/api/assistant/questions` | `{question,context?,request_id?}`，组织已适配工具或说明缺口 |
| GET | `/api/assistant/questions`、`/api/assistant/questions/{id}` | 历史问题、状态和冻结答案 |
| POST | `/api/assistant/questions/{id}/cancel`、`retry` | 取消或另建重试记录 |
| GET/POST | `/api/assistant/shortcuts` | 读取或保存常用问题 |
| GET/POST | `/api/executions` | 读取或提交受控执行 |
| GET | `/api/executions/{id}` | 工具计划、绑定任务、状态与真实结果 |
| POST | `/api/executions/{id}/cancel`、`retry`、`continue` | 执行生命周期，参数按工具契约验证 |

推荐由专题接口发起客户分析。以下请求仅为使用格式示例，明确执行时会读取实际数据：

```json
{
  "application_id": "cropland_delineation",
  "context": {"bbox": [113.30,30.30,113.31,30.31]},
  "parameters": {"product": "io_lulc_2024"}
}
```

直接问询示例：`{"question":"统计天门市2024年水稻分布参考面积并上图","context":{}}`。模型解释与固定工具执行分开；最终数字、地图和下载来自已成功的真实任务。

## 共用数据源与观测资产

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/data-sources`、`/api/data-sources/{id}` | 来源元数据、接入阶段、年份、许可、用途及最近实测 |
| POST | `/api/data-sources/search` | `{source_ids,bbox,start,end,limit?}`，仅固定STAC集合，返回真实目录快照 |
| GET | `/api/data-searches`、`/api/data-searches/{id}` | 读取已保存检索，不重新下载像元 |
| POST | `/api/data-sources/drafts` | 登记服务意向；不访问任意URL，不保存密钥，不标为已连接 |
| POST/GET | `/api/data-plans` | 创建或读取选源计划；创建计划不隐式执行新分析 |
| GET | `/api/data-plans/{id}` | 冻结候选、来源、缺口及可用执行契约 |
| POST | `/api/observations/ingest` | `{search_id,source_id?,item_ids?}`，从已保存的真实目录记录入库 |
| GET | `/api/observations`、`/api/observations/{id}` | 筛选观测和资产版本、实际像元使用证据 |
| GET | `/api/algorithm-contracts` | 波段、定标、质量、传感器与输出契约 |
| POST | `/api/algorithm-matches` | `{observation_id,index_id?}`，检查兼容性；匹配不等于完成像元读取 |

文章来源清单由`data_sources/agriculture_reference_candidates.json`加载为共用来源。`platform_shared=true`及`usage_policy.mode="on_demand"`表示按需使用；`available_years`为声明收录年份，`connected_years`为已有适配年份，二者不可混用。查看目录不联网读取像元、不批量预取、不启动分析。

## 地图、导入与核查

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/map/config`、`/api/map/layers/{id}` | 地图配置和已登记图层 |
| GET | `/api/map/search?q=...&remote=0或1` | 地点、坐标、用户数据与成果检索；在线查询由服务端固定适配 |
| GET | `/api/map/inspect` | 按明确成果类型、ID、坐标及相应版本条件读取归档原生像元 |
| GET/POST | `/api/datasets` | 读取或导入WGS84 GeoJSON FeatureCollection |
| GET | `/api/datasets/{id}` | 用户数据、属性、几何与版本 |
| POST | `/api/analysis` | 对用户数据执行已登记矢量分析 |
| GET | `/api/analysis/{id}/result.geojson` | 下载分析几何 |
| GET | `/api/scene-workflows` | 台账场景绑定及历史分析 |
| POST | `/api/scene-workflows/{id}/scope`、`run` | 按筛选台账生成参考外包范围或运行真实台账统计 |

导入最多500要素、5000坐标点，请求体上限2MiB；不直接接收Excel、Shapefile或GeoTIFF上传。台账外包矩形不是地块掩膜；图层属性和来源真实性仍由提供方负责。

## 栅格分析与作物产品

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/production/capabilities`、`/api/cropland/capabilities`、`/api/landcover/capabilities` | 原生光谱、年度耕作地与固定参考区域的能力及限制 |
| GET/POST | `/api/production/tasks`、`/api/cropland/tasks`、`/api/landcover/tasks` | 查询或提交对应真实任务；优先使用专题表单契约 |
| GET | `/api/{production或cropland或landcover}/tasks/{id}` | 状态、输入、实际来源和结果 |
| POST | `/api/{production或cropland或landcover}/tasks/{id}/cancel`、`retry` | 有条件取消或另建重试 |
| GET/POST | `/api/agriculture/tasks`、`/api/raster-processing/tasks` | FVC、地块统计、集合与阈值等受控算法 |
| GET | `/api/agriculture/capabilities`、`/api/raster-processing/capabilities` | 算法声明、输入与上限 |
| GET | `/api/crop-reference/capabilities` | 已接通的年度水稻产品与范围约束 |
| POST | `/api/crop-reference/analyses` | 直接读取GlobalRice20 2024分类窗口或行政参考界，返回标准区域成果 |

选区水稻请求：`{"source_id":"globalrice20_2024_esa","bbox":[西,南,东,北]}`。行政范围请求：`{"source_id":"globalrice20_2024_esa","scope":"administrative","region":"天门市"}`；使用完整名称或行政编码，不同时传bbox。行政全域最多2000万原生像元；超限提示缩小范围，不降采样冒充全域统计。旧`scope:"xiantao_city"`保留2017公开参考市界口径。

对应专题请求使用`application_id:"crop_reference_analysis"`：选区为`parameters:{source_id:"globalrice20_2024_esa",analysis_scope:"selected_aoi"}`和`context:{bbox:[西,南,东,北]}`；行政范围为`parameters:{source_id:"globalrice20_2024_esa",analysis_scope:"administrative",region:"天门市"}`和空`context:{}`。

GlobalRice20按原生EPSG:4326角度格网逐行WGS84椭球积分，0为有效非水稻、1为水稻、255为未知。全部未知时面积为null；有效范围内确实无水稻才返回0。公开历史类别不是独立本地监督分类结果，不替代当期或官方统计。

## 区域成果、时序与下载

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/agriculture-atlas`、`/api/agriculture-atlas/{id}` | 当前安装环境的区域成果、指标、图层、范围与来源 |
| GET | `/api/agriculture-atlas/{id}/files/{file}`、`/api/agriculture-atlas/{id}/bundle.zip` | 固定`product_sha256`的文件或完整成果包 |
| GET | `/api/results/{kind}/{id}/manifest`、`/api/results/{kind}/{id}/bundle.zip` | 成功任务的文件清单或逐文件校验成果包 |
| GET | `/api/agri-research/catalog`、`/api/agri-research/series/{id}` | 实际观测序列、质量支持、样本/模型门槛和核查目录 |
| GET | `/api/agri-research/series/{id}/files/{declared-path}` | 使用`series_sha256`固定的声明文件 |
| POST | `/api/agri-research/samples`、`/api/agri-research/train` | 样本来源登记、实际特征提取和受门槛约束的研究基线训练 |
| POST | `/api/agri-research/reviews` | 按明确观测对、阈值、方向及最小像元数生成变化候选 |
| GET | `/api/agri-research/reviews/{id}`、`/api/agri-research/reviews/{id}/geojson` | 核查记录及已显示候选 |
| POST | `/api/agri-research/reviews/{id}/status` | 追加人工核查状态与说明，不自动转为独立训练真值 |

版本不符返回409，不自动下载或显示另一份内容。水稻成果包含原生分类与AOI掩膜GeoTIFF、统计CSV、来源STAC、范围GeoJSON和预览PNG。更多样本与时序字段见[作物与时序指南](../guides/AGRICULTURE_RESEARCH.md#接口与复算)。

## 集成与旧接口

天地图状态/配置为`GET/POST /api/integrations/tianditu`，真实检查为`POST /api/integrations/tianditu/check`；地图、地名、逆地理及行政范围分别代理固定官方服务。配置内容不回显密钥，细节见[天地图指南](../guides/TIANDITU_SETUP.md)。

本机模型配置位于数据库同级`integrations/model.json`，目前提供方为`codex_cli`，变更后重启。状态中的`available`表示CLI与登录检查通过，`inference_verified`表示本进程实际完成过推理，不表示所有业务问题都有执行能力。

旧合成方案、模拟运行、样例应用发布与复制等写入口返回410，仅保留历史兼容读取。人工样本像元与代表网格生产入口已停用；新任务须明确真实来源、范围和日期。不要根据历史归档API示例创建新的模拟业务成果。
