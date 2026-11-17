# 目录整理前的README快照

> 历史记录：以下正文保存2026-11-17整理目录前的项目说明，包括本机成果、观测与验收快照；命令和代码路径对应旧目录。当前说明见[文档导航](../README.md)与[项目首页](../../README.md)。

# 时空智能工作台

面向农业、林业、水利、交通等业务的一张图工作台。以遥感影像为地图背景，将位置搜索、业务数据、分析需求、真实计算、成果核查和下载放在同一界面中。

平台支持结构化专题和文字问询。后端调用已登记的数据源与算法，保存实际观测日期、输入条件、质量掩膜和成果版本；范围、时段、来源或数据不足时说明缺口，不用预设数字代替分析。

**本地入口：<http://127.0.0.1:8016/>**。当前软件版本为`0.18.0`，能力以本文件及当前接口为准；`docs/`中的V6—V18文档是对应阶段的设计和验收记录。

## 快速启动

需要Python 3.9或兼容环境；使用npm启动和检查时需要Node.js/npm。以下为macOS/Linux命令，在项目根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-raster.txt
npm start
```

栅格依赖为Rasterio、NumPy和Pillow，版本约束见[requirements-raster.txt](../../requirements-raster.txt)。前端为`dist/`中的静态HTML、CSS和原生JavaScript模块，使用Leaflet，无需打包，也没有需要通过`npm install`安装的第三方依赖。

`npm start`与`npm run dev`均调用`scripts/serve.py`，优先使用项目`.venv`。也可以直接启动：

```sh
.venv/bin/python server.py --port 8016 --db data/workspace.sqlite3
```

启动参数可以通过npm转交，例如`npm start -- --port 8020`。服务仅绑定`127.0.0.1`，日志默认输出到终端。启动后检查：

```sh
curl -fsS http://127.0.0.1:8016/api/health
```

健康接口表示本机服务状态；外部数据、天地图授权和模型推理是否可用，应分别查看对应状态与实际请求结果。

**克隆仓库不会自带本机业务库、已生产影像或私有配置。** `data/`、`output/`及`.venv/`均已被Git忽略。新环境需要恢复自己的数据备份，或通过导入、公开数据分析和生产脚本创建成果。

## 使用方式

主导航按“地图 / 专题分析 / 任务与成果 / 数据”组织，共用一张地图。

1. **定位与选区**：搜索地点、地址、经纬度、用户数据或已完成成果。地点搜索用于导航，分析范围须明确选择；地名点不等于行政边界。
2. **选择专题或提问**：填写范围、年度、观测窗口及必要参数。文字入口位于“新建分析 / 查询分析”，已完成问题可保存为常用查询。
3. **运行与查看**：后台检索真实影像、检查质量、执行兼容算法并归档，显示任务状态、定量指标、地图及数据支持范围。
4. **核查与下载**：点击要素查看属性，开启“核查像元”读取归档原生栅格值；从结果下载GIS、统计和来源文件。
5. **恢复与复用**：在“任务与成果”查看历史、复用分析条件；来源和下载追问继承原结果版本，不自动替换为另一年份或数据源。

读取归档与新计算分别说明，读取耗时不作为重新生产影像的耗时。底图用于定位和显示，指数与面积来自独立分析栅格。

## 当前功能

| 能力 | 已实现内容 | 使用条件与边界 |
| --- | --- | --- |
| 地图与搜索 | 影像/标准底图、中文注记、坐标定位、本地成果检索、天地图地名/逆地理/行政参考范围 | 在线服务依赖网络与许可；参考范围不等于地籍或工程边界 |
| 数据上传 | WGS84 GeoJSON点、线、面直接上图，保留属性、定位、开关图层与空间统计 | FeatureCollection最多500要素、5000坐标点、请求体2MiB；不直接导入Excel、Shapefile或GeoTIFF |
| 耕作地参考提取 | 按明确年度读取WorldCover2021或IO Annual LULC2024，提取连通图斑、计算面积并导出 | 年度产品类别提取，不是当期影像新分类；连通图斑不等于确权宗地 |
| 原生光谱分析 | Sentinel-2或Landsat 8/9的单期指数、两期后减前差值、阈值线索与有效覆盖 | 明确小范围与时段，使用真实波段、定标和SCL/QA掩膜 |
| 时序与影像处理 | 均值/中位数合成、逐像元OLS线性趋势、Otsu阈值与四连通分区 | 合成至少2次、趋势至少3次有效日期；指数分区不等于作物分类 |
| 农业栅格分析 | 显式端元FVC估算、用户地块指数/分类分区统计 | 沿用所选原生源范围，部分覆盖、未知与重叠分别报告 |
| 行业台账 | 农业、林业、水利、交通22项场景，上传/绑定、筛选分组、字段汇总、面积/长度计算及上图下载 | 使用实际提供的数据；目录存在不代表监管接口已接通 |
| 作物与时序 | NDVI/NDMI实际观测曲线、质量共同范围、样本特征提取、训练门槛、变化候选及人工核查 | 独立农业入口，不是第23项通用台账场景；当前未发布水稻/油菜分类 |
| 目录与资源库 | 多源STAC检索、观测及波段资产版本入库、算法契约匹配、绑定版本分析 | 目录命中和契约匹配不等于已下载像素或完成解译 |
| 问答与执行 | 本机Codex测试桥接、受控规划、固定规则执行、来源/下载追问 | 模型不执行任意代码、URL或Shell；工具、范围与版本仍由服务校验 |

当前登记18项分析专题，清单由`GET /api/applications`返回；22项台账场景目录定义于`dist/scene-catalog.js`，绑定与历史分析由`GET /api/scene-workflows`读取。场景目录及前端组件可复用，已完成答案可保存常用问法；旧样例应用的创建、发布、复制入口已停用，不代表已提供省域低代码应用市场。

### 算法与数据源

已注册12个光谱指数：**NDVI、NDWI、MNDWI、NDMI、NDBI、NBR、EVI2、SAVI、MSAVI2、GNDVI、NDRE、BSI**。Sentinel-2支持12项，Landsat 8/9支持除NDRE外的11项；按所需输入波段的原生参考分辨率计算，S2通常为10/20米，Landsat为30米。

交互原生光谱选区单边不超过`0.05°`，最多`1,048,576`个参考格网像元。交互时序处理最多183天、6个独立观测日期，一次使用同一传感器系列。已登记区域栅格的复用读取有独立上限，不能据此承诺省域任意范围在线计算。

| 来源 | 当前接入程度 |
| --- | --- |
| Microsoft Planetary Computer | S2 L2A、Landsat C2 L2目录与已适配原生像元分析；S1 RTC、Copernicus DEM GLO30目录 |
| Earth Search | S2 L2A与DEM GLO30目录适配；网络失败如实记录 |
| Copernicus Data Space | S2 L2A目录与资产元数据适配，后续像元授权和读取需另行适配 |
| ESA WorldCover2021、IO Annual LULC2024 | 年度分类产品原生读取与耕作地参考提取 |
| 天地图 | 服务端Key代理底图、注记、地名、逆地理和行政参考轮廓，不提供本项目指数计算所需光谱波段 |
| GeoNames、geoBoundaries、Natural Earth | 本地地点索引、版本化参考市界及公开矢量参考资料 |
| Open-Meteo | 明确位置的天气模型与预报，不作为现场气象站实测 |

算法实现参考集合归约、线性回归、光谱指数与阈值分区方法，计算在本项目已适配引擎中执行；**未直接接入Google Earth Engine或OpenGE云端算力**。来源条款、署名、授权及适用范围随对应成果保留。

农业来源清单见[共用来源登记](../../data_sources/agriculture_reference_candidates.json)，运行时通过`GET /api/data-sources`读取，不依赖某个地市或原工作空间的草案记录。目录加载只读取元数据；提交分析后才按范围、年度和用途选择适配器并读取所需窗口，已归档的相同输入可校验后复用。收录年份、已接通年份与本次实际读取证据分别保留。GlobalRice20的2024年数据已通过ESA ECDC公开COG接通，支持实际窗口读取、WGS84逐行椭球面积、上图、像元核查和GIS下载；EARice10、CN_Wheat10及LGISer-CFD仍分别受下载网络、账户或许可/覆盖条件限制，不标为可计算。历史分类产品不作仙桃2026独立作物真值。

## 区域农业成果与时序

2026-11-16本机归档快照提供**25个成果入口、22份物理生产记录**；分类记录的耕作地、水域、树木等视图共享源文件，不按入口数冒称独立分析次数。当前安装清单以`GET /api/agriculture-atlas`为准，新克隆环境不会自动拥有这些成果。

- **年度分类参考**：IO2024 Crops类别参考面积126,865.22公顷；WorldCover2021及两产品交叉核查亦保留。采用2017公开参考市界，跨产品差异不能解释为耕地年度增减或分类准确率。
- **已归档单期及两期光谱**：2026年单期NDVI、NDMI、MNDWI与两期NDVI差值，实际日期、覆盖及质量分别标注。
- **业务参考资料**：农资、养殖、黄鳝、冷链、农机等地址候选为有界关键词检索线索；高标准农田公告无工程边界时仅作为资料，不推测空间位置。

### 水稻分类产品分析

入口：农业 → 粮食与重要农产品 → 遥感与影像 → 水稻分类产品分析。可选择“地图选区”或“市县参考范围”，按实际覆盖跨地市使用；行政界按完整名称/编码精确匹配并冻结，不明年份按未知显示。也可问“统计仙桃全市2024年水稻分布参考面积并上图”。本机已实际读取7,294,238个参考市界内像元，其中2,237,695个为水稻类别，参考面积77,042.7155公顷（1,155,640.7324亩）。这是GlobalRice20 2024模型产品参考，不是当期或官方面积；有效覆盖100%不是分类精度。

`POST /api/crop-reference/analyses`接受`{source_id:"globalrice20_2024_esa",bbox:[西,南,东,北]}`，或用`{source_id:"globalrice20_2024_esa",scope:"administrative",region:"天门市"}`读取市县参考界（不同时传bbox）。行政全域最多2000万原生像元，超限提示框选子区。旧`scope:"xiantao_city"`保留2017参考市界口径。数据按原生角度格网计量，不将名义20米统一乘400平方米；下载固定`product_sha256`。其他年度、月份、确权与跨年增减不会静默换成2024产品。

### 作物与时序

入口：**专题分析 → 农业 → 作物与时序**。输入框也可直接问：**“查看仙桃市2026年5月至10月长势时序”**。

| 实际观测日期 | NDVI有效覆盖 | 比较处理 |
| --- | ---: | --- |
| 2026-05-16 | 22.57% | 低覆盖参考，不纳入合格共同范围曲线 |
| 2026-06-01 | 75.92% | 合格观测 |
| 2026-08-04 | 91.50% | 合格观测 |
| 2026-09-05 | 0% | 无有效像元，均值为null |
| 2026-10-07 | 99.96% | 合格观测 |

序列固定Landsat 8 Tier1、30米米制格网；7月只有Tier2候选，本轮排除。5次观测全部共同有效范围为0，6月、8月、10月三次合格观测的NDVI及NDMI共同范围为1,896,056个像元，占参考市界67.9166%。这是空间支持范围，不是农田面积。每月窗口选择实际代表观测，不称月均或逐日连续监测，不插补缺测。

变化核查支持6月1日→8月4日、8月4日→10月7日的实际差值，按显式阈值、方向与最小像元数生成候选，上图并追加核查状态与说明。候选尚未限定耕地范围，不能直接认定灾损、违法或作物变化。最多显示1500个，超出数量单列，GeoJSON下载与显示面积对应已显示候选。

真实样本点可登记来源、许可、核实人员和训练/验证划分，后台从合格日期原生影像提取NDVI/NDMI，检查空间分组、地块隔离和样本量。已实现纯NumPy正则化LDA研究基线及模型版本校验，**当前未登记本地独立核实样本，5—10月也未覆盖油菜完整越冬季，因此不发布水稻、油菜分类面积或精度**。详见[农业时序与核查说明](../guides/AGRICULTURE_RESEARCH.md)。

## 服务配置

配置位于数据库同级的`integrations/`目录。默认数据库为`data/workspace.sqlite3`，对应`data/integrations/`；使用`--db`时随其父目录迁移。

### 天地图

在“数据”的天地图服务卡填写自有服务器端Key并执行连通检查。Key保存在`integrations/tianditu.json`，也可通过服务进程环境变量`TIANDITU_TOKEN`提供，环境变量优先。Key由服务端保管，不下发浏览器或加入成果包；不要提交到Git。

本机已有配置不代表新环境可直接使用；许可、出口IP限制、配额及各类服务连通性需按实际部署检查。详见[接入与许可配置](../guides/TIANDITU_SETUP.md)。

### 本机Codex测试桥接

当前仅实现`codex_cli`提供方，复用本机Codex已保存登录。先安装CLI并完成`codex login`，用`codex login status`检查；在`integrations/model.json`保存：

```json
{"provider":"codex_cli","enabled":true,"timeout":180}
```

可选`model`指定该账号可用的模型ID，省略使用CLI默认模型。缺少配置文件时默认禁用，修改后重启服务。`GET /api/assistant/model`查看安装、登录与接入状态，`inference_verified`仅表示本进程已实际推理成功。

桥接并发为1，超时默认180秒，使用临时目录、只读模式和结构化输出，禁用Shell、浏览器、插件与MCP等能力；不复制登录凭据到业务库。模型支持业务解释和任务规划，结果仍由固定工具产出。无需模型的结构化专题与固定归档查询可独立执行；模型失败时按实际能力回退或说明错误，不承诺所有开放问题均能自动完成。

## 代码结构与持久化

| 路径 | 职责 |
| --- | --- |
| `server.py`、`scripts/serve.py` | 本机HTTP路由、SQLite存储、启动与模型配置 |
| `dist/` | 地图、行业场景、成果选择、上传、问答和农业时序界面 |
| `assistant_services.py`、`codex_*.py` | 问答生命周期、模型桥接与受控规划 |
| `application_services.py`、`execution_*.py` | 专题参数校验、真实工具执行、状态与答案归档 |
| `catalog_adapters.py`、`data_catalog_services.py`、`observation_repository.py` | 多源目录、选源计划及观测/波段资产版本 |
| `native_raster.py`、`landsat_reader.py`、`spectral_indices.py` | 原生波段读取、定标、质量检查与指数计算 |
| `cropland_services.py`、`landcover_services.py` | 年度分类提取与固定区域底数核算 |
| `raster_processing_*`、`agriculture_*`、`regional_raster.py` | 时序/阈值处理、FVC、地块统计与已发布区域栅格复用 |
| `agri_temporal.py`、`agri_research_services.py`、`agri_series_read.py`、`crop_model.py` | 市级实际观测、样本/核查、版本固定读取与分类研究基线 |
| `scene_workflow_services.py`、`search_services.py`、`inspection_services.py`、`result_bundle_services.py` | 台账场景、搜索、像元核查与下载包 |
| `scripts/`、`tests/`、`docs/` | 生产/发布/验证脚本、隔离测试及专题文档 |

默认数据布局：

```text
data/
├── workspace.sqlite3
├── integrations/             # 天地图与模型私有配置
├── landcover/                # 固定区域年度分类成果
├── cropland/                 # 选区分类提取成果
├── production/               # 原生光谱成果
├── agriculture/              # FVC与地块统计
├── raster-processing/        # 时序、合成与阈值分区
├── agriculture-atlas/        # 区域成果及地图产品
└── agri-research/series/      # 已发布观测序列及来源文件
```

备份时先停止服务，再保存整个数据库父目录，包含成果文件和配置。仅复制SQLite不足以恢复栅格、地图及固定版本下载。`output/`用于生产中间结果和验收证据，按需另行保存；默认日志不固定写入该目录。当前未安装系统自启动，也未提供公网、多租户或省级权限部署。

## 数据生产与检查

日常小范围分析从界面提交；全市成果生产与发布由脚本执行。脚本可能下载真实影像，先用`--help`核对参数，选择新输出目录，保留原版本：

```sh
.venv/bin/python scripts/produce_xiantao_series.py --help
.venv/bin/python scripts/publish_agri_series.py --help
```

分类、日光谱、跨产品核查和地址线索脚本分别为`scripts/produce_xiantao_{classification,spectral,agreement,pois}.py`。时序发布需先完成质量比较与独立验证，同ID已发布成果不覆盖。保留完整生产目录时，可离线复核：

```sh
.venv/bin/python scripts/produce_xiantao_series.py --output output/agri-research/series-2026 --validate-only
```

代码检查：

```sh
npm run check
```

该命令检查非vendor浏览器模块语法并运行隔离Python单元/HTTP测试，不等于实际浏览器或外部数据联网验收。界面契约可另行运行：

```sh
node scripts/verification/verify_agri_research.mjs
node scripts/verification/verify_reviewed_atlas.mjs
node scripts/verification/verify_map_reading.mjs
node scripts/verification/verify_scene_ui.mjs
```

最后一个脚本需要本机服务，默认读取8016，可通过`SCENE_TEST_URL`指定地址。2026-11-16本机验收记录为656项后台检查、51项前端契约检查、80项发布数据检查及桌面/390px手机实测；报告索引为`output/agri-research/release-proof.json`。这些是该次验收记录，实际测试数以当前执行结果为准，证据文件不随Git分发。

## 接口与扩展入口

| 接口 | 用途 |
| --- | --- |
| `GET /api/health`、`GET /api/assistant/model` | 服务与模型状态 |
| `GET /api/applications`、`POST /api/application-runs` | 已登记专题与运行 |
| `POST /api/assistant/questions`、`GET /api/assistant/questions/{id}` | 提问、状态和冻结答案 |
| `POST /api/executions`、`GET /api/executions/{id}` | 受控工具执行 |
| `GET /api/data-sources`、`POST /api/data-sources/search` | 数据源与目录查询 |
| `POST /api/observations/ingest`、`GET /api/observations` | 观测/资产版本入库及筛选 |
| `GET /api/algorithm-contracts`、`POST /api/algorithm-matches`、`GET /api/ai-tools` | 算法兼容性与机器工具契约 |
| `GET /api/agriculture-atlas`、`GET /api/agriculture-atlas/{id}` | 区域成果、地图及版本固定下载 |
| `GET /api/agri-research/catalog`、`GET /api/agri-research/series/{id}` | 时序、样本、模型与核查目录 |
| `POST /api/agri-research/samples`、`POST /api/agri-research/train` | 样本特征提取及受控基线训练 |
| `POST /api/agri-research/reviews`、`POST /api/agri-research/reviews/{id}/status` | 变化候选与人工核查 |
| `GET /api/scene-workflows`、`POST /api/scene-workflows/{id}/run` | 台账绑定与分析 |

写请求使用同源`application/json`。区域成果下载使用`product_sha256`，时序文件使用`series_sha256`固定版本；内容不符返回409。详情见[API文档](API.md)及[农业时序接口](../guides/AGRICULTURE_RESEARCH.md#接口与复算)。旧合成方案、运行及样例应用发布/复制等写入口返回410，不用于新业务。

## 适用范围与后续接入

光谱指数和阈值是核查线索，有效覆盖率是数据支持程度，均不等于分类精度、官方面积、产量、灾损或违法结论。未知与有效范围内零命中分别保存；预览PNG不用于定量统计，原生GeoTIFF及来源记录可独立复核。

监管工程边界、北斗农机实时轨迹、禁捕执法、确权延包、养殖主体、尾水实测、冷库容量和交易数据仍需授权与业务适配。S1、DEM目录接入不表示其专项算法已实现；国内高分、无人机、省域现状分类、分布式计算集群、多租户权限及外部应用市场尚未完成。后续扩展应登记数据/算法契约，校验范围、年度、传感器、质量和版本后再开放执行。

相关文档：[多源接入架构](MULTISOURCE_ARCHITECTURE.md) · [行业场景](../guides/SCENE_MODULES.md) · [农业业务规则](../guides/XIANTAO_AGRICULTURE_RULES.md) · [多轮评审与验收](MULTI_ROUND_PRODUCT_REVIEW.md) · [地图阅读设计](MAPPED_DESIGN_REVIEW.md)。历史文档中的数据数量、模型未接入及旧样例流程说明仅代表其记录时点，不覆盖当前代码行为。
