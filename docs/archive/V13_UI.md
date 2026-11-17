# V13 影像资源库与选源界面验收

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

2026-11-14。前端负责影像资源库、目录入库、算法匹配和指定观测进入分析表单。采用Playwright CLI独立浏览器会话`geo-v13-front`，只操作隔离8017预览库；不操作用户Safari窗口，不读取或配置正式天地图Key。

## 已实现的客户流程

数据页增加“影像资源库”，与“数据源目录、目录检索、需求选源计划”并列，保留四个主入口的一张图工作台。

1. 按来源、范围、时间实际检索目录，显式将本次命中入库。
2. 资源库按提供方/集合、日期、景云量、关键词筛选，查看观测版本和波段资产。
3. 详情展示原始比例、偏移、单位、无效值与质量资产；元数据缺失不补造默认值。
4. 匹配返回的ready=true才提供“准备分析条件”；仅波段相似或外部目录未适配时不提供执行按钮。
5. 准备分析会锁定observation_id、数据源、观测日期与指数，清除旧AOI，定位观测包络供用户选择小范围，随后明确提交运行。
6. 修改来源或日期会解除单景锁定并提示；切换专题也解除。包络不是有效像元范围，不直接用整景执行。

资源数称“资源版本”，明确同景跨提供方可能重复；目录已入库、像元有读取证据、匹配分析适配分别显示。返回资源页会重读本机资源状态，避免分析完成后继续显示旧缓存。

## 实际浏览器执行证据

初始预览资源库为空，显示0条而没有预置样本。选择PC Landsat，AOI `[114.2,30.5,114.21,30.51]`、2025-11-01至2025-11-30，实际返回3条Landsat8/9目录候选。点击“入库本次命中”后得到3个资源版本、75项资产。将景云量上限设1%后筛为2条。

选择`LC09_L2SP_123039_20251129_02_T1`，详情显示30m、比例0.0000275、偏移−0.2与QA资产；匹配返回6项可准备指数。选择NDVI，绑定`observation_2106108b824ac001d1d6b8f0`，明确输入上述小AOI，实际提交成功：

- 问题：`question_1fce8566dac24a`
- 新建真实任务：`production_dcdb997a175348`，reused=false
- 实际观测：2025-11-29
- 平均NDVI：0.23538663983345032（界面0.2354）
- 有效参考格网：1184
- 资源使用记录：该observation新增1个pixel_use，pixel_verified=true

冻结快照包含来源、observation_id、item_id、元数据SHA、30m与QA策略。重新加载页面后恢复同一答案、图层和指定单景，无重新计算。证据：`v13-front-real-flow.json`、`v13-front-result-initial.txt`、`v13-front-restored-result.txt`。

浏览器点击原生GeoTIFF下载，取得实际`analysis.tif`；独立读回EPSG:32649、34×39、30m、7个波段（NDVI、有效/AOI掩膜、NIR/Red反射率、QA_PIXEL、QA_RADSAT），有效1184，均值与界面一致。证据：`v13-front-browser-download.txt`、`v13-front-download-verification.json`、`v13-front-downloaded-landsat.tif`。

## 已关闭的界面问题

- 旧质量卡固定写Sentinel-2与SCL：改按实际source_id显示Landsat8/9和QA规则。
- 指数选项固定10/20m：移除传感器无关的固定分辨率，方法优先读取`index_profiles[index][source]`，实际结果以原生格网为准。
- Landsat结果沿用旧Copernicus地图署名：根据实际来源显示USGS/NASA Landsat；不改写已有栅格数值。
- 冻结观测仅显示内部UUID：优先展示提供方item_id，原始内部ID仍保留于来源记录。

初始署名截图保留为`v13-front-landsat-result-initial.png`；修正并重载后的桌面截图为`v13-front-landsat-result-desktop.png`。

## 最终环境复核

主负责人最终预览重启后已再次核验：数据目录显示7个检索适配来源，资源库来源下拉含“全部”及7源；最新Landsat方法显示30m和QA_PIXEL位0–5/QA_RADSAT规则。重新载入后旧Landsat答案、原图层和0.2354结果恢复，指定观测保留。复用表单将来源改为S2后，绑定卡消失、context中observation_id被移除，界面明确提示解除；没有再提交一个替代数据任务。

返回资源库显示3个资源版本、75项全库资产元数据、1项全库像元读取证据，首条观测标记有像元使用证据，其余仍为尚未核查。桌面1440×1000与手机390×844均已截图并视觉检查；手机document.scrollWidth=390，与viewport一致。筛选/重置同行，长记录ID换行，详情资产表可横向滚动，页签仅在自身容器内滚动。

最终证据：`v13-front-final-mobile-measures.txt`、`v13-front-binding-guard.txt`、`v13-front-unlocked.txt`、`v13-front-final-library.txt`；图片为`v13-front-library-desktop.png`、`v13-front-library-mobile.png`和`v13-front-landsat-result-desktop.png`。目录API及新锁定来源证据的最终后端复核由主负责人另行执行，本文不将旧记录改写为新版本。

浏览器会话已关闭，不留下额外窗口。浏览器期间Open-Meteo曾502、重启时有一次EMPTY_RESPONSE，Esri底图曾连接重置，均为真实外部失败记录，不记作代码错误或天地图已配置。一次引用已刷新页面的旧CLI控件失败，重新snapshot后恢复操作；未改变产品代码处理这一工具引用失效。
