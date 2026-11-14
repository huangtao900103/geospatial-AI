# V12 天地图接入：架构与遥感专业审查

状态更新：2026-11-14已完成自有Key实际联网验收，见[接通记录](TIANDITU_LIVE_ACCEPTANCE_20261114.md)。本文保留2026-11-13架构审查时尚未配置Key的历史范围。

审查角色：架构与遥感监督；取文日期：2026-11-13。目标是让已有一张图工作台获得国内底图、中文注记和在线地名地址服务，并保留原遥感计算与成果来源链。

## 一手资料与访问证据

以下为天地图官方文档及官方文档引用的控制台，不使用博客中的演示密钥。部分页面裸 HTTPS 请求返回418；加入正常浏览器 User-Agent、官网 Referer及文档查询参数后，实际取得原文。官网首页或浏览器抓取失败不等于已取得开发者许可。

| 官方来源 | 本轮核实的事实 |
| --- | --- |
| [地图服务](https://lbs.tianditu.gov.cn/server/MapService.html) | OGC WMTS；`img_w/cia_w`影像及中文注记、`vec_w/cva_w`矢量及中文注记；`_w`是球面墨卡托，适合本项目现有地图；正式调用需要许可Key。 |
| [影像服务GetCapabilities](https://t0.tianditu.gov.cn/img_w/wmts?request=GetCapabilities&service=wmts) | 本轮无Key元数据请求200；图层img、矩阵集w、256像素，CRS标识EPSG::900913，级别1–18。不是已用许可Key成功取得瓦片的证据。 |
| [地名搜索V2](https://lbs.tianditu.gov.cn/server/search2.html) | `/v2/search`；普通搜索需要关键词、视野、级别及分页参数；返回POI、行政定位、统计或建议等不同类型，不能统一当成点或行政面。 |
| [地理编码](https://lbs.tianditu.gov.cn/server/geocodinginterface.html) | 国内结构化地址到经纬度；地址定位属于参考位置，不构成地块权属或用地分类。 |
| [逆地理编码](https://lbs.tianditu.gov.cn/server/geocoding.html) | `/geocoder`使用坐标返回地址，响应`status=0`才为成功，接口协议不同于搜索V2。 |
| [行政区划V2](https://lbs.tianditu.gov.cn/server/administrative2.html) | `/v2/administrative`；关键词或行政码、childLevel、extensions；`data.district`与`data.suggestion`分开，boundary为轮廓字符串。 |
| [行政区划V2官方响应示例](https://lbs.tianditu.gov.cn/server/codeDemo/code6-v2.json.text) | boundary示例为WKT MULTIPOLYGON；文档中的省略号只作说明，不能导入计算。真实返回才可解析并上图。 |
| [旧行政区划说明](https://lbs.tianditu.gov.cn/server/administrative.html) | 明确提示V1于2024-06-30下线；本轮不得以旧接口作正式实现。 |
| [许可申请说明](https://lbs.tianditu.gov.cn/authorization/authorization.html) | 注册、开发者认证、创建应用、取得许可Key；存在配额管理，高调用量单位按官方要求申请对应开发者资格。本轮未据此承诺当前精确配额。 |
| [官方应用控制台](https://cloudcenter.tianditu.gov.cn/center/development/myApp) | 当前官方导航`sysConsole`实际目标，可创建和管理应用；账号认证仍需用户真实身份。 |
| [开发者认证](https://cloudcenter.tianditu.gov.cn/center/development/developerAuth)、[注册](https://passport.tianditu.gov.cn/register) | 官方当前导航链接，不使用未经核实的console子域。 |

同次元数据检查还取得cia_w/vec_w级别1–18、cva_w级别1–19；本版采用影像/矢量到18级，避免把地图放大能力描述为新增分辨率。元数据所列地址与上游策略可能变化，最终以账户许可和实际响应为准。

## 第一轮：责任分工与接入决策

1. 国内地理参考服务交给天地图：底图、中文注记、明确提交的地名地址搜索、坐标地址参考；现有持久化成果/数据集检索继续由本机索引负责。
2. 将输入联想与明确网络搜索分开，避免每次键入都消耗第三方配额；保持键盘及搜索按钮可达，错误、无结果和未配置分别提示。
3. 以服务端固定域名和路径代理保护许可Key，浏览器只见同源地址；配置响应、错误、日志、下载与地图来源不得包含Key。禁止任意URL代理与跨域跳转。
4. 配置、连通性检查、实际调用分开计量；保存Key不能直接显示“全部服务已验证”。影像、搜索、逆地理、行政服务的可用性分别说明。
5. 保留底图和数据提供方署名，当前视窗调用有界，不引入全域批量瓦片打包。

## 第二轮：行政边界和遥感结果审查要求

前端方案已复核：行政查询单独提交；多个候选由用户选择；只有后端返回可用真实GeoJSON，才提供查看和下载。边界标明公开行政参考，不自动改写分析AOI，不把中心点、定位框或POI替换成行政多边形。

后端需要验证：WKT只能接受有限的POLYGON/MULTIPOLYGON几何；所有经纬度必须有限且在范围内；闭环、面/环层次、洞及多面不得丢失；限制响应大小、坐标数、嵌套与处理时间。空/畸形/过限几何不得改成bbox或生成替代面。返回行政码、名称、取数时间、官方来源，并把数据年代未给出写明。

根服务集成需要验证：底图与注记分开加载、保留署名，瓦片z/x/y受限；服务器秘密配置不放在通用业务记录中；旧库与旧任务不被升级构造器改写；路由只接固定服务；未配置时旧公开底图和本地成果检索仍能使用。

**遥感边界：**天地图WMTS是渲染后的浏览瓦片，不提供本项目指数计算所需的可定标NIR/SWIR/SCL。NDVI/NDMI等计算继续读取已接入的原生Sentinel-2观测。行政参考轮廓也不能替代确权地块、高标准农田验收边界或当期耕地分类。图上直观丰富与统计依据完整是两项验收，分别交代。

## 本轮验收口径

本轮尚未取得用户自有许可Key。官方文档与公开GetCapabilities可实际核对；有Key路径的请求参数、解析、错误和脱敏应通过隔离测试验证，但不称真实授权服务验收。待用户在平台本机配置合法Key后，按功能分别检查，任何失败原样提示，不切换为假地点、假行政面或预设遥感结果。

代码复核见下一节；授权上线检查须在本机取得并配置自有Key后按项完成。

## 第二轮代码复核记录

- 后端行政协议已按V2实现，未调用已下线V1；多个district独立返回，suggestion独立返回；真实WKT解析后才有GeoJSON和几何bbox，空/坏/过限轮廓保留失败原因，不以矩形补位。
- WKT采用有限层级解析，保留孔洞及MultiPolygon，不宣称全量拓扑修复。独立审查实际运行14项隔离输入，全部通过：有效面洞/多面，及闭环、无穷溢出、越界、Z/三维、垃圾字符、分隔、嵌套、退化点、EMPTY、尾随内容、点数上限。证据：[v12-architect-boundary-audit.json](../output/playwright/v12-architect-boundary-audit.json)。这些是解析测试输入，不是线上天地图返回。
- 连接检查由最初只验img改为img/cia/vec/cva分别验收；行政、搜索、逆地理独立状态，不能一张瓦片成功就推导所有服务可用。
- 配置generation随更新/清除递增；旧检查结果只在Key与generation均未变时保存，避免旧Key结果覆盖新配置。Key不通过状态、错误或来源URL返回。
- 搜索坐标标签改成天地图返回经纬度；本次所取得的搜索文档只说明x/y，未将其额外包装成已核实的特定坐标基准。地图投影则由WMTS元数据明确标识。
- 前端发现两处一致性问题已交由前端专家关闭：连接检查verified/partial的证据文案；仅返回suggestion时展示可继续查询的行政建议词。最终浏览器结果与整体检查以主负责人的验收记录为准。

一手资料的取文时间/字节数/SHA256见：[v12-official-source-audit.json](../output/playwright/v12-official-source-audit.json)。本轮没有自有Key的线上授权成功证据，不将协议隔离测试写成真实服务已通过。

## 最终架构签审

已复核`server.py`、`map_services.py`、`data_catalog_services.py`以及README、API和TIANDITU_SETUP：配置独立于通用业务记录；同源代理不将Key放入浏览器模板；未配置天地图保持不可用状态并保留原公开底图；天地图服务的可请求/已验证状态与光谱像元、业务分类ready分开；原生遥感分析链没有被浏览瓦片替代。没有发现需阻断本轮接入适配交付的后端或文档问题。

主负责人已提供完整检查记录：[v12-check.txt](../output/playwright/v12-check.txt)，246项、21.624秒通过；另有本机HTTP未配置、同源和阶段区分检查。架构专家未重复运行全部测试，独立执行的是上述14项解析审查。本次签审范围为适配实现和可复核的本机行为，**不包含未配置自有Key的线上授权服务验收**。前端已完成行政建议词、连接检查证据一致性与缺Key恢复底图的修复；真实无Key浏览器验收记录见V12_UI.md，未将有Key模拟路径称线上验收。
