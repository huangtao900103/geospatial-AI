# V6 仙桃全市面积核算产品

## 入口与范围

主导航新增“全市核算”，优先级在“一张图”后。地图顶栏有同名入口，原V5导航明确为“小窗分析”。小窗分析的仙桃耕地/农田面积自然语言需求会转入全市核算，并保留原文。

固定产品明确为 **ESA WorldCover 2021 v200，10米，Cropland Code40**；空间范围是 **2017年 geoBoundaries 公开参考市界**，不是局部NDVI代表网格。若原文包含2025/2026等其他年份，页面显眼说明目前仅可提供2021分类基线，主按钮明确改为“核算2021年历史基线”。

## 精确 API 接线

`LandcoverWorkspace` 仅调用冻结的 `/api/landcover` 契约：

- `GET /capabilities`，读取 available、product、region 与服务边界。
- `POST /tasks {region:'xiantao',product:'worldcover_2021_v200',requirement}`，自动提交后台任务。
- `GET /tasks` 与 `GET /tasks/:id`，读取实际状态、节点、日志及结果。
- `POST /tasks/:id/cancel {}` 与 `/retry {}`，按真实状态取消或创建独立重试。

轮询1.8秒，仅存在queued/running任务时请求。没有前端模拟统计、假进度或伪造数值。

## 结果呈现

全部数值直接读取 `task.result`：

- `summary.cropland_ha/cropland_mu`：主卡公顷、万亩与亩。
- `summary.total_area_ha/coverage_ratio/nodata_area_ha`：参考市界内面积、有效覆盖率与无分类面积。
- `classes[]`：真实各分类面积条形图、完整明细表，含面积、占比及原始像元数。
- `image.url/classes_url`：耕地分布与全部土地覆盖两种真实预览。
- `boundary`：实际市界，与预览采用相同WebMercator纵向投影归一；没有矩形代替市界。
- `provenance/report/artifacts`：来源年度、边界哈希、方法报告与可复核原始产物。

“导出面积明细”动态生成带UTF-8 BOM的CSV，可由Excel直接打开。列包括类别代码/中文名称/公顷/亩/占比/像元数/产品2021/边界2017/覆盖率/任务ID。原始分类GeoTIFF、逐行计数CSV、完整结果JSON使用后端artifact链接。

核算公式、误差边界、法定统计差异折叠展示；主界面不把解释堆在定量结果前。

## 地图接线

`onMap(task)` 使用 `landcover:<task.id>` 的独立持久图层引用：

- `L.imageOverlay(result.image.url, [[south,west],[north,east]])` 展示已重投影EPSG:3857的PNG。
- `L.geoJSON(result.boundary)` 叠加真实公开参考市界。
- 两者在同一Leaflet地图中配准，使用独立pane；不覆盖底图或其他数据。
- 地图点选该全市成果可见面积、覆盖率、来源/边界年度，并返回面积图表与报告。
- 成果中心收录全市任务，可重新读取并叠加；刷新也可通过持久引用恢复。
- ESA署名完整保留：© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium；同时注明geoBoundaries2017参考界。

地图PNG仅为降采样显示，定量不从该图重算。

## 文件与验证

新增 `dist/landcover-workspace.js`，其余接线在 `app.js/map-workspace.js/style.css/index.html`；缓存tag为V6。

已执行三份ES module语法检查，并用隔离API成功任务 `landcover_ccdccda62bf444` 做模板渲染验证：实际公顷、万亩、覆盖类别、2017边界年份与1536×866投影预览均正确渲染，没有硬编码业务统计或NaN坐标。

根代理负责最终真实浏览器验收及默认工作空间交付。
