# V4 地图核心版 · 专家讨论与架构复核

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

记录日期：2026-11-13（Asia/Shanghai）。本文件记录实际讨论、官方来源核验及后续签审，不以建议代替实现证据。

## 第一轮：围绕“一张图”重组平台

总体负责人将本轮目标确定为：以真实遥感/卫星影像为底图、多源空间数据为图层、AI与分析工具围绕地图展开；用户无需自行提供密钥或补充数据。

架构/地图监督专家只读检查了V3前端与README。V3虽有实际坐标与算法，但主地图仍为自绘Mercator画布，没有地理影像底图，不能满足此次产品目标。

建议并已分别反馈总体负责人、平台工程师和前端工程师：

- 使用本地vendor的Leaflet稳定版1.9.4；真实 `L.tileLayer` 与 `L.geoJSON` 在同一个EPSG:3857地图实例中绘制，不能使用CSS背景照片叠加原SVG位置。
- GeoJSON输入仍为WGS84的经度、纬度；Leaflet `setView` / `LatLng` 接口为纬度、经度。图层转换由GeoJSON适配器完成，不应用GCJ-02偏移补偿到这些WGS84源上。
- Esri World Imagery为高分辨率首选；NASA GIBS为明确日期、低分辨率的真彩备选。影像显示与遥感像素计算分开，不能因存在影像底图就称已完成解译。
- 默认工作面应是地图，数据、需求与分析工具以可折叠面板辅助；各来源独立开关与署名，示例业务要素必须持续标注合成属性。
- 外网失败时保留用户要素与查询，显示真实故障/切换当前底图；不能用生成图片或静态景观冒充在线底图。

## 官方资料与真实HTTP核验

此轮不仅阅读文档，还对仙桃参考点经度113.45、纬度30.36计算XYZ瓦片并实际请求、解码、查看返回JPEG。

| 对象 | 当次结果 | 判断 |
| --- | --- | --- |
| Esri `services.arcgisonline.com` 元数据及瓦片 | 本机请求超时 | 域名/网络路径当时不可用，不据此宣称Esri整体不可用 |
| Esri `server.arcgisonline.com` 元数据 | HTTP200约0.56秒；EPSG102100/latest3857、256px；copyrightText为Esri、Vantor、Earthstar Geographics、GIS User Community | 可作为匹配Web Mercator的真实瓦片源 |
| Esri仙桃 `z12/y1685/x3338` | HTTP200约0.46秒，JPEG256×256、19121字节；实际查看可见城区、道路与田块 | 高分辨率真实影像显示可用，非生成背景图 |
| NASA GIBS MODIS Terra真彩，2025-11-07，`z8/y105/x208` | HTTP200约1.5秒，JPEG256×256、5660字节；实际查看为云覆盖 | 瓦片有效但当日有云；云白不等于服务故障，不能伪造成无云影像 |
| ArcGIS官方World Imagery item | HTTP200，item `10df2279f9684e4a9f6a7f08febac2a9`，access=public | 公开读取不等于公有领域；保留Esri许可与署名边界 |

测试瓦片URL：

- `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/12/1685/3338`
- `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/2025-11-07/GoogleMapsCompatible_Level9/8/105/208.jpg`

建议模板：

```text
Esri:
https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}

NASA:
https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/2025-11-07/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg
```

这些是当次网络验证，不代表SLA或未来持续可达。Esri为多日期、多来源拼接，不应使用页面日期冒充拍摄日期，也不能仅根据瓦片级别声称全国同样分辨率。NASA备选日期与较低分辨率应始终可查，`maxNativeZoom=9`后继续放大不增加真实信息。

### 官方资料与使用边界

- [Esri World Imagery官方服务](https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer)：EPSG3857、256px、JPEG、来源与图层元数据。
- [World Imagery官方条目](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9)：当次通过sharing REST读取其accessInformation和licenseInfo，适用Esri Master License Agreement；该条目说明不用于离线瓦片导出。本轮按需在线显示，不提供影像离线打包功能。
- [Esri底图署名说明](https://support.esri.com/en-us/knowledge-base/what-is-the-correct-way-to-cite-an-arcgis-online-basema-000012040)：应保留底图来源署名；当前具体来源优先使用服务元数据。
- [NASA GIBS官方介绍与署名指引](https://nasa-gibs.github.io/gibs-api-docs/)：数据为NASA观测的预生成可视化，客户端应注明NASA GIBS/ESDIS。
- [NASA GIBS访问基础](https://nasa-gibs.github.io/gibs-api-docs/access-basics/)、[地图客户端说明](https://nasa-gibs.github.io/gibs-api-docs/map-library-usage/)：EPSG3857 WMTS与XYZ访问，日期及矩阵级别需匹配产品。
- [Leaflet1.9.4官方API](https://leafletjs.com/reference-1.9.4.html)：当前稳定版为1.9.4；2.0为预发布。不能混用2.x示例与1.9.4代码。本地vendor应保留原license。

## 第二轮：实施反馈与架构确认

### 前端工程师反馈

前端工程师提出将overview和map切为独立MapWorkspace，使用Leaflet1.9.4和固定源tile代理；每个空间来源分别建立 `L.geoJSON`；地图实例创建一次，切页时保留DOM并调用 `invalidateSize`，避免每次重渲染销毁；左侧图层/底图、右侧需求分析面板、下方成果记录；应用工坊仍为明确固定fixture的预览。

架构师确认该方案，并强调：多源开关不得伪造尚未返回的图层；切换数据不强制跳回仙桃；联合fitBounds只在用户请求适应范围或首次加载时使用；底图错误与覆盖数据状态独立；署名始终可见。

### 平台工程师反馈

平台工程师采用实测可用的Esri `server`域名主源和NASA dated MODIS备用；计划提供固定源代理、z/x/y校验、单瓦片1MB、8秒超时、JPEG/PNG magic检查、256瓦片/64MB/24小时受限缓存，只预热默认视窗约30张瓦片。

新增公开矢量选择Natural Earth维护者仓库的河流水系，保存源URL、内容哈希、许可与坐标基准，按仙桃周边筛选或裁剪，不生成虚假农田或权威行政边界。

架构师确认受控代理方案，并发回具体边界：

- 不开放任意URL代理，不扫全域或批量下载；失败返回真实错误，不偷偷更换源后仍标原名称。
- Natural Earth的“10m”是1:10,000,000制图比例尺，不能写成10米遥感分辨率；应标注“1:1000万概化水系”，不冒充农村沟渠或权威水利边界。
- 仅筛选bbox相交线段不等于真正几何裁剪，成果应准确命名；避免外伸线拉远视图。
- 公开水系、模型天气点、影像整景footprint与合成农业业务应分组和独立计数，不能合并为“农业主体数量”。

## 最终签审门槛（第三轮按实现与证据复核）

1. 主工作面确为可平移缩放的地理地图，有真实加载成功的遥感/影像瓦片，不再依赖自绘景观画布。
2. 已知WGS84点、Polygon与影像随平移缩放保持同一地图坐标；GeoJSON孔、多面、点属性均正确。
3. Esri/NASA各自署名、日期/拼接属性、最大原生层级、当前状态可查；影像失败有明确状态与备用入口。
4. 公开矢量为实际来源快照，标明比例尺/时间/许可/哈希；合成业务与真实公开数据不混标。
5. 用户数据、已存在分析成果、天气模型点和STAC覆盖范围均按各自空间位置叠加；跨地区不套固定仙桃图形。
6. 页面切换后图层、视图与来源不串线；Leaflet实例、事件与请求受控，面板不遮挡署名与必要操作。
7. 桌面/窄屏、地图初始化/尺寸变化、底图失败和多图层开关有真实浏览器验证；服务单测不替代视觉/配准验收。

上述为首轮建立的验收基线；实现与证据复核见下列第三轮及其闭环补充。

## 第三轮：最终源码架构复核

### 复核范围与证据

架构/地图监督专家只读检查 `map_services.py`、`server.py`、`tests/test_map_services.py`、`dist/map-workspace.js`、主入口 `dist/app.js`、Leaflet本地来源清单与独立记录 `docs/QA_V4.md`。本节为源码与服务证据签审，真实浏览器的影像显示、配准、交互及窄屏结果由总体负责人另行记录，不在此提前宣称通过。

| 重点 | 源码复核结论 |
| --- | --- |
| 真实地理配准 | MapWorkspace以Leaflet1.9.4实例承载 `L.tileLayer` 与各来源 `L.geoJSON`；默认EPSG:3857，GeoJSON仍为WGS84经纬度，不再将原自绘SVG压在背景图片上。孔与多面由Leaflet几何适配器处理。 |
| 底图与层级 | 固定Esri server域名、NASA MODIS Terra真彩两源。Esri原生级别上限18；NASA固定2025-11-07且maxNativeZoom=9，继续放大只是插值。NASA署名包含产品与日期，来源说明区分低分辨率归档影像。 |
| 多源隔离 | 合成农业、公开天气参考点、公开水系、STAC目录覆盖各自独立接口与图层；用户dataset、analysis、run使用带类型前缀的独立覆盖组，不把合成业务混入用户GeoJSON。现有分析成果可作为新图层加入，保留来源与参数上下文。 |
| 状态与生命周期 | overview/map共用MapWorkspace DOM与Leaflet实例，切换其他工具页不销毁覆盖组；重新挂载时invalidateSize。底图tileload/tileerror单独统计，错误提示可重试或选备用，矢量仍保留。网络状态与来源正确性不由“页面存在”推断。 |
| 属性安全 | 名称、属性、错误与详情均escapeText；tooltip传入已转义文本。来源链接来自受控配置，未开放用户任意HTML或脚本。 |
| 公开水系 | Natural Earth快照包含Han/Yangtze两段，以Liang-Barsky实际裁剪到区域。来源URL、原源/快照哈希、CRS84、Public domain与1:1000万比例尺保留。默认关闭，配置建议缩放不高于9；不能当作村级沟渠或权威边界。 |
| 署名 | 底图署名由Leaflet tile layer attribution管理，Esri提供方列明；NASA署名包含日期。天气实际数值显示旁保留Open-Meteo链接。CSS未将地图署名设置为隐藏；实际遮挡与窄屏可见性仍需浏览器确认。 |
| 代理安全与缓存 | z/x/y和source先校验，URL仅固定两域名，跳转限原HTTPS主机；响应限制1MB/8秒并检查JPEG或PNG签名/MIME；错误不改写为成功影像。缓存已调整为纯内存64瓦片/16MiB、最长5分钟，尊重上游no-store/no-cache/private、max-age与Age。没有影像磁盘包、全域扫描、预热或离线导出。 |

上述最终缓存方案取代第二轮最初提出的较长缓存与视窗预热计划。Leaflet本地vendor保留BSD-2-Clause license及版本/文件指纹来源清单；不能将影像条款与开源地图库许可混为一谈。

独立QA已在隔离临时服务执行HTTP、LRU、错误、范围、来源及完整回归，初轮记录42项通过；有界排队补充后总体负责人报告46项全部通过。真实影像成功记录来自架构/工程的当次HTTP请求及JPEG查看；模拟上游测试不替代实际内容验证，也不构成供应商SLA。

### 三审发现与跟进

**导出ID与并发门槛均已闭环。** 架构师最后只读确认 `dist/map-workspace.js` 导出使用 `layer-{layerIndex}-feature-{featureIndex}` 短ID，并保留来源：属性数量允许时写 `_source_layer`、`_source_type`、`_source_feature_id`；原属性已满时不突破40项属性限制，来源保留于要素foreign member `geoai_source`。集合metadata另列各源，不修改地图中的原始要素。

总体负责人完成实际浏览器导出与HTTP再导入复验：默认37要素、用户数据10要素及其NDVI10要素叠加，导出共57要素、57个唯一ID、最长ID17字符，sources metadata包含8层；POST再次导入返回201并保留57要素。此项为主负责人实测，架构师复核源码与报告，没有冒称独立重复浏览器操作。

浏览器联调发现全局SVG图标尺寸规则误伤Leaflet矢量画布，总体负责人已修复并报告画布1634×1123、57条路径正常。该修复与真实配准/视觉效果的完整浏览器记录由总体负责人维护。

并发初版“第7个请求即拒绝”会使正常视窗突发加载出现空瓦片，已由工程师与QA改为**6个上游活动请求 + 最多48个等待请求，排队最长12秒**。架构师只读确认 `map_services.py` 使用独立的admission与network有界信号量，正常请求有界等待，满额或超时明确失败，原1MB/8秒单次上游约束不变。总体负责人/独立QA报告24并发全部成功，补充后46项测试全过。

前端对暂时瓦片失败最多额外重试2次；仍失败继续保留真实错误与备用入口，不生成替代影像。公开上游仍可能少量503，不将最终验收写成“零网络错误”或供应商可用性保证。

### 本轮签审结论

核心地图架构具备进入最终浏览器验收的条件：真实影像瓦片与WGS84多源要素在同一地理地图配准，来源、日期与示例属性分离，网络失败不伪造，受限内存缓存与署名边界清楚。导出ID互操作与正常视窗并发门槛已按上述源码和实证闭环，未发现仍阻断本轮本地试用的核心地图架构问题。

真实大模型规划、专业遥感像素解译、真实农业业务数据授权、生产认证与跨机构共享仍不能因底图升级而表述为已完成。最终可视体验和真实浏览器证据另行审阅。

## 最终真实网络讨论与决议

真实浏览器验收暴露部分缺瓦片后，架构师针对同一失败瓦片逐一比较生产代理、urllib与curl。`11/1669/844`本地代理8.338秒返回503 `TILE_UNAVAILABLE`，同源同瓦片urllib7.710秒、curl11.692秒成功返回17888字节JPEG；不是无覆盖或错误坐标。Python urllib每请求Connection:close，重复TLS开销与8秒预算放大慢网失败。总体负责人浏览器Image实测两张分别11.213秒和4.487秒正常解码256像素。

专家最终支持Leaflet优先两家代码内固定HTTPS`browser_url`，复用浏览器原生连接和供应商缓存，保留固定代理供API兼容；NASA日期、原生9级、署名与错误提示保持一致。没有加入任意URL入口、影像批量下载或跨域canvas导出。整视窗和生命周期最终结果见浏览器记录。

最终真实浏览器结果：Esri桌面1440×1000，24/24瓦片完整解码；手机390×844，15/15完整解码；NASA显示级别10全部12/12完整解码且实际请求原生9级。前端修正初次零尺寸/跨页恢复与ResizeObserver校准，桌面和手机来回保持视角至像素舍入精度；8个入口均无页面级横向溢出。总体负责人`npm run check`最终47项全过，证据见BROWSER_QA_V4.md。
