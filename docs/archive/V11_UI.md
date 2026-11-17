# V11 一张图业务工作台

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

日期：2026-11-13。前端范围：`dist/app.js`、`dist/map-workspace.js`、`dist/map-copilot.js`、`dist/index.html`、`dist/style.css`。验证环境为独立 `http://127.0.0.1:8017/` 与隔离预览数据库，不触及正式数据库或用户浏览器。

## 产品逻辑

主导航收敛为“地图 / 专题分析 / 任务与成果 / 数据”，共用一个地图实例。文字需求作为折叠的辅助入口，固定业务专题作为主要入口。专题目录和表单由 `/api/applications` 的实际元数据驱动，不用前端拼问句冒充工作流。当前八项业务入口和一个专业指数工具，执行均通过 `/api/application-runs` 进入统一执行协议。

结构化表单包含明确范围、单期/两期时间、数据集和条件。仙桃土地覆盖统计明确固定2021分类产品及2017参考市界，不继承武汉地图选区。光谱任务按元数据限制小范围；影像目录与中心天气允许更大范围。未接入的高标准农田、黄鳝业务不能作为可执行业务专题。

任务主标题使用冻结的业务名称，显示版本、实际范围、日期及条件。“复用条件，再次分析”恢复原表单；原成果和输入保持绑定。任务列表支持按进行中/完成/需处理查看，保留取消和重试。新浏览器没有有效保存的任务编号时展示专题目录，不自动选择服务器最新问题。

## 搜索与地图

搜索接入 `/api/map/search`：地点、经纬度、全部持久化成果和用户数据集。支持防抖、加载、错误、空态、警告、键盘选择与回车。地点定位明确不构成行政统计边界；坐标按经度、纬度。成果选择恢复真实原任务及图层，不只搜索当前可见覆盖物。中文城市索引为后端已接入的覆盖范围，不宣称任意POI可用。

图层面板保留真实导入数据及独立任务。土地覆盖和原生光谱结果有对应图例，下载入口区分“导出矢量范围”和成果GeoTIFF。影像检索显示真实目录条目、日期与云量；包络定位明确只表示元数据范围，不代表无云有效覆盖，也不覆盖现有分析栅格。

“核查像元”开关开启后点击栅格，调用 `/api/map/inspect?kind=production|landcover...`，显示原生导出栅格的数值/类别、质量波段、行列、投影、参考尺度和实际观测日期。关闭后恢复普通成果点击。未覆盖/无效像元显示后端状态，不从预览PNG或底图取颜色推断数值。

## 参考依据

参考 [Earth Engine Code Editor](https://developers.google.com/earth-engine/guides/playground) 的地图、搜索、几何范围、图层、任务与Inspector协作方式；面向业务用户以结构化专题表单替代代码编辑器。参考 [Image Overview](https://developers.google.com/earth-engine/guides/image_overview) 的范围、日期筛选、波段/尺度/掩膜/投影明确原则。当前使用本项目实际后端数据源与算法，不表示接入Google Earth Engine。

## 已验证证据

- 新隐藏浏览器在服务器已有历史任务时仍显示8张真实专题卡，0条自动绑定结果。四主导航可见。
- 武汉、武昌区、仙桃、坐标定位均通过；NaN坐标和未收录地点有可读提示，没有 `[object Object]`。见 `output/playwright/v11-front-search-final.txt`。
- 水体光谱线索由真实表单提交，`question_3b70d2e3781241` 绑定 `production_d908eef65ac24a`。范围114.20,30.50,114.21,30.51；2025-11-01至30日；MNDWI>0。原生20m格网2668个有效、198个条件命中、投影面积7.92公顷。见 `v11-front-water-final.txt`。这些是光谱条件线索，不是经确认水体或耕地面积。
- 像元点击114.204962,30.505022返回MNDWI=-0.3178046346，实际观测2025-11-14，行28列25；UI弹窗使用同一返回值。见上述文本与 `v11-front-pixel-inspection.png`。
- 搜索MNDWI恢复原专题、冻结条件和一个原生栅格；刷新后仍一致。见 `v11-front-results-search-final.txt`。
- 用户点击“下载结果”实际取得GeoTIFF，保存为 `v11-front-downloaded-result.tif`。Rasterio读取为EPSG:32650、51×57、6波段：after_mndwi、common_valid_mask、aoi_mask、green、swir1和SCL。

外部影像瓦片与在线服务可能受网络可用性影响。失败的自动化等待及重测文本保留在证据目录，成功记录使用 `final` 文件；不将失败尝试计为通过。正式运行环境部署和后端全专题测试由主代理单独完成。

补充最终UI验收：`v11-front-business-final-v2.txt` 显示0.5°目录范围成功，返回9条Sentinel-2 / Sentinel-1 RTC / Landsat真实条目，场景日期及云量可见，目录包络定位后原分析栅格仍保留。固定土地覆盖表单无坐标范围输入。NDMI空阈值时筛选关系禁用，填入阈值后启用。390×844下页面scrollWidth=390，无横向溢出，见 `v11-front-mobile.png`。`v11-front-pixel-final.txt` 与 `v11-front-final-desktop.png` 为像元弹窗最终截图，已等待动画完成，弹窗透明度1，桌面无横向溢出。先前自动化的 `.mc-catalog-results`（真实类名为 `.ap-catalog-results`）和目录包络popup（实际为tooltip）错误选择器超时已纠正，不属于运行失败。
