# V11 业务工作台与流程验收

日期：2026-11-13。用户指出地图位置/成果搜索等功能不能正常使用，要求整体分析、参考Google Earth Engine重整流程，减少界面AI感。主负责人、产品架构监督、地图检索与前端专家并行实施，围绕真实点击路径验收。

## 已确认断点与修复

| 原断点 | 实际修复 |
| --- | --- |
| 搜索框承诺地点/结果，实际仅遍历visible overlays | 后端统一索引地名、坐标、全部成功任务、专题名/实际日期和用户数据；不要求先上图 |
| 新用户无保存任务时自动选择questions[0] | 默认专题入口，不自动打开最近unsupported或只读来源记录 |
| 客户主要面对聊天、指数名和多套工作台 | 四主入口：地图、专题分析、任务与成果、数据；8业务专题+1专业工具，文字需求折叠辅助 |
| 输入参数靠前端拼文本、缺专题谱系 | 后端校验并在launch前冻结application_ref；幂等hash含专题/版本/条件 |
| 导入后go(lab)被客户路由守卫重定向 | 从地图/数据导入后直接进入业务图层统计并选中导入数据 |
| 地图导出GeoJSON容易误作完整栅格 | 改名导出矢量范围；栅格从结果下载GeoTIFF，矢量统计提供直接GeoJSON |
| 点击栅格只回结果卡，不可核像元 | 核查像元开关→真实CRS→固定本地TIFF1×1窗；指数/差值/分类、有效性与实际日期可见 |
| 未关联执行创建期间取消可能留下孤立作业 | 关联失败时取消该执行；增加确定性并发交错回归，并做实际取消/重试HTTP |
| 业务图层结果把Python字典输出到客户摘要 | 中文要素统计与几何构成图；面要素存在才展示面积合计及方法限制 |
| 工程/产业未接入模块被描述成标准业务组件 | 移至更多工具的设计草案；不列入真实可执行专题 |

参考GEE的是数据、参数、地图、任务、Inspector和导出分工；不声称接入了GEE账户、具有其数据规模或云计算能力。官方资料、两轮专家讨论及独立签审见[V11_REVIEW.md](V11_REVIEW.md)。

## 自动检查

最终`npm run check` **214项通过，19.369秒**，同时检查全部自编浏览器模块语法。记录：[v11-final-check.txt](../output/playwright/v11-final-check.txt)。初轮212项19.005秒保留为`v11-initial-check.txt`；此后补取消竞态与中文统计/GeoJSON下载契约回归。

新增12项结构化应用、15项搜索、9项像元核查，连同此前178项执行、栅格、目录、生命周期与API检查。应用测试包括全部9模板到实际planner的工具选择、四类比较关系与小数精度、固定区域隔离、时段顺序、非有限阈值、用户数据、launch前冻结、同ID跨模板409及取消交错。离线fixture与以下实际远程执行分别记录。

## 九个真实专题运行与下载

隔离库`output/playwright/v11-preview.sqlite3`，公开S2测试选区为`[114.20,30.50,114.21,30.51]`，单期请求2025-11，前期2025-10。每次从`POST /api/application-runs`进入真实assistant/execution，再等待真实子任务成功。不是调用计划后直接显示预设卡片。

| 专题 | 真实子工具/观察 |
| --- | --- |
| 土地覆盖统计 | 新建landcover_4fe1c7b583464d，真实原生分类扫描；2021×2017固定口径，约250.95万亩参考Cropland |
| 植被长势观察 | production_fa9a69b9003746，单期NDVI实际读取与计算 |
| 植被变化排查 | production_f2c97c5b17d246，前后窗口NDVI实际差值、质量及条件格网 |
| 水体光谱线索 | 复用前端实际已生成production_d908eef65ac24a；2668有效格、198 MNDWI>0、7.92ha参考格网投影面积 |
| 植被水分光谱 | production_b2a7513985ed40，NDMI真实指数；不输出土壤实测含水率 |
| 可用影像查询 | discovery_14e0225559114f，三源实际8条目录（每源最多3条），不是像素解译 |
| 区域天气参考 | execution_c5baf8b7779d4e，提供方实际当前/逐日天气模型；非实测站和灾损 |
| 业务图层统计 | analysis_81dd1ca3c07942，对带出处的Natural Earth公开参考水系导入后计算2条线要素；不冒充权威业务水系 |
| 专业光谱分析 | production_30d69afd0c1d47，NBR实际波段与20m参考格网 |

所有运行成功、冻结context与实际execution/child对应，声明下载实际GET200。MNDWI复用真实子任务如实标记，不把0.42秒称重新调数。原始证据：[v11-real-applications-http.json](../output/playwright/v11-real-applications-http.json)。

## 搜索与像元独立验收

GeoNames官方cities15000快照裁剪为2123个中国城镇点（含湖北56个）加湖北省参考点，共2124，原ID/来源/时间/SHA和CC BY4.0保留。运行时没有外部地理编码依赖；不是完整行政界或地址/POI库。未收录地名、非法坐标与服务错误有分别说明。

架构监督者独立执行13组HTTP搜索、8组像元核查、3种MNDWI下载，与真实GeoTIFF直接读取比对：

- 武汉/湖北省武汉市/Wuhan、仙桃、武昌区、中文坐标、专题名、实际日期和任务ID可搜。
- 非有限/颠倒坐标不被猜成地点；未收录地点明确NO_MATCH，不填假命中。
- 同子任务多问题时恢复与查询匹配的分析问题；来源/下载追问不覆盖地图恢复意图。
- MNDWI点114.205,30.505返回−0.31780463457107544，row28/col25，与原TIF1格完全一致。
- padding/AOI外、共同无效、远投影域外点返回相应status/null；全部JSON严格有限值。
- LC使用归档市界重建该格中心掩膜，不把bbox当行政界。GeoNames仙桃参考点不在2017概化市界内，正确outside_aoi；不为对齐定位点修改市界或面积。

只读检索/核查前后生产任务数不变。记录：[v11-independent-audit.json](../output/playwright/v11-independent-audit.json)、[V11_SEARCH.md](V11_SEARCH.md)。

## 实际浏览器

前端使用自己的隐藏浏览器/隔离8017，未操作用户Safari或正式库。1440×1000与390×844：

- 四导航、八业务专题与单期/两期/用户数据表单可用，默认不自动绑最近错误任务。
- 武汉/仙桃/武昌区、合法/NaN坐标、无结果提示、键盘/点击定位通过。
- 水体表单→真实POST→结果、冻结条件、指标与PNG；再次复用条件可恢复表单。
- 全成果搜索→恢复原专题与一个真实栅格→刷新仍保留；真实独立LC图例不被无assistant ID挡住。
- 栅格点核查实际请求kind=production，popup为同一API真实值；不是浏览器按颜色估算。
- 实际下载并用rasterio检查6band GeoTIFF，EPSG32650/51×57/3.1-native。
- 目录0.5°大范围真实提交成功，9条候选/日期/云量与目录包络可见，保留原分析栅格；未误套光谱0.05°限制。
- 固定LC不继承选区；NDMI未填筛选阈值时关系禁用、主指标均值，默认阈值放折叠说明。
- 手机scrollWidth=390，无横向溢出。

完整证据：[V11_UI.md](V11_UI.md)。主截图已逐张查看：[桌面业务结果与像元](../output/playwright/v11-front-final-desktop.png)、[手机表单](../output/playwright/v11-front-mobile.png)、[实际地点检索](../output/playwright/v11-front-place-search.png)。

一次自动化等待误用旧选择器导致超时，纠正后同一实际目录流程重测通过，失败文件保留、不计为成功。外部Esri瓦片仍可能慢/缺片；实测截图包含真实底图与结果，不将提供方在线可用性等同本地分析成功。主负责人尝试Safari查看时用户正在操作其浏览页面，未继续争用该页面；不声称替用户打开了新版。正式浏览器效果由上述独立浏览器证据确认，正式API另作下述复核。

## 正式库与最后闭环

正式入口`http://127.0.0.1:8016/`，版本0.11.0，使用原`data/workspace.sqlite3`。升级前SQLite一致性备份，校验**全部107条原有记录**，含35条问题、19个execution、3个production、2个landcover、3个dataset及草案/报告等；逐条完整JSON SHA不变，未复制测试库到正式库。

正式再从专题API运行：

- 植被变化`question_51eb1c84a06448`→新建`production_673b280e3d1140`；两期真实读取与差值完成。
- 固定分类构成`question_4e48a9be502b4b`→已有完整真实LC成果，保持classes模式。
- 水体线索`question_94d8ff33bda644`→复用正式MNDWI已完成成果，不引用preview。
- 图层统计`question_36da7b3fac4648`→analysis_8b57ea6c11f840，中文统计与直接GeoJSON下载；Content-Type为application/geo+json。
- 同条件重复植被变化复用同一真实child，任务各自冻结。

正式搜索实际匹配专题名/地点/坐标/日期/用户数据；生产单期、两期差值和LC核查均与正式TIF单格相同；远点为null，全部声明下载200。记录：[v11-formal-applications.json](../output/playwright/v11-formal-applications.json)、[v11-formal-final-check.json](../output/playwright/v11-formal-final-check.json)。

另外真实取消专业NBR任务`question_2a5ae08a15544a`，其execution终态cancelled；重试`question_4c6bf53ba12345`在相同冻结专题/条件下实际成功，原问题仍cancelled且无答案。创建期间取消的特定交错亦有确定性回归，避免孤立作业。记录：[v11-formal-cancel-retry.json](../output/playwright/v11-formal-cancel-retry.json)。

## 适用范围

本版为本机真实业务工作台，主要流程已有可复现证据。省域现状分类、多景合成、完整地址检索、多租户权限及未授权工程/产业数据仍需后续独立接入和验收。现有分类年度、S2窗口/格网、地名参考、目录/像素和光谱/业务认定分别说明，未以菜单数量或设计草案冒充可执行应用。
