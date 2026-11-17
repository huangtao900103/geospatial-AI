# V13 多源遥感数据库与真实算法执行验收

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

2026-11-14，正式入口http://127.0.0.1:8016/，版本0.13.0。最终完整检查286项、23.940秒通过，源代码在此后仅更新文档和交付文件。

## 正式数据与保留

升级前正式库176条记录建立一致性SQLite备份与逐条JSON哈希；新增EO五张表和索引后，完成真实目录入库、原生任务及复用验证，原176条记录全部未改。数据源检查改为追加快照，最近检测取新记录，旧检测原样保留。

正式资源库首批11个真实资源版本、261项资产元数据、5个来源，其中2个版本有实际成功像元任务证据。来源为PC S2、PC Landsat、PC S1RTC、PC GLO30、CDSE S2。影像库可继续按明确时空条件查询入库，没有将11条作为静态样例库或全域观测总量。

实际正式请求：

| 来源 | 实际目录命中 | 阶段 |
| --- | --- | --- |
| PC Landsat | 3 | 元数据入库，L8/9原生计算已验证 |
| PC S1 RTC | 2 | 公开目录入库，像元账户与SAR算法未接 |
| PC Copernicus GLO30 | 1 | 地形元数据入库，静态参考时刻，地形算子未接 |
| CDSE S2 | 3 | 新STAC公开目录入库，像元账户未接 |
| PC S2 | 2 | 元数据入库，S2原生计算已验证 |
| EarthSearch S2 / GLO30 | 各0、failed | 本机DNS/出网不可达，未伪装成空成功或换源成功 |

证据：[formal-validation-summary.json](../../output/v13-data/formal-validation-summary.json)。备份位于output/diagnostics/v13-pre-upgrade.sqlite3，不包含在源码/成果分享包中。

## 实际分析与读取证据

| 环境与工具 | 实际影像/任务 | 定量结果与文件 |
| --- | --- | --- |
| 正式Landsat NDVI | LC09_L2SP_123039_20251129_02_T1；production_a23fab4d3d944b | 30m，1184有效像元，均值0.23538662570248364；GeoTIFF/PNG/JSON实际下载200 |
| 正式S2 NDVI | S2B 2025-11-14；production_e4981e20877640 | 10m，10650有效像元，均值0.22132643965101623；实际下载200 |
| 隔离S2单景绑定 | production_36260b3387aa43 | 10m、EPSG32650、114×100参考格网；绑定版本和来源一致 |
| 隔离Landsat NDMI | production_a37294294e6244 | 30m、EPSG32649、39×34，1184有效，均值0.0429670396615067；NIR/SWIR1与真实QA导出 |
| 浏览器Landsat NDVI | production_dcdb997a175348 | 真实表单→新建任务→下载TIF，独立读取30m/7band/有效1184与结果一致 |

不同传感器与日期的均值不作为跨传感器变化结论。Landsat执行专家另从原COG重读DN与QA，以DN×0.0000275−0.2及QA规则逐格独立复算，与导出一致；独立WGS84像元中心核对AOI掩膜亦一致，见[real-ndvi-proof.json](../../output/v13-landsat/real-ndvi-proof.json)。

正式两项任务均锁定资源ID、item_id、metadata_sha256、来源、日期及小AOI；discover后/read前核对必要资产键、原始地址和声明定标。成功后eo_pixel_uses关联到正确任务/输入哈希/观测版本；再次同条件提交实际复用同一完整子任务。

Landsat来源证据不再写成S2：正式答案、来源计划used.source_id、实际reader、质量、格网与署名一致。原生分析保持按源质量规则，未把QA伪装为SCL。

## 接口与数据库检查

- 五表：eo_observations、eo_assets、eo_extents、eo_discovery_members、eo_pixel_uses。
- 版本与去重、资产索引、时间/来源/云量/bbox筛选、错误输入、重启读取、任务证据错绑拒绝、源/观测复用隔离、Landsat来源计划与新目录约束均有隔离用例。
- 12项算法契约表示6指数×2传感器输入适配，不能当12种独立业务分类。11项契约边界检查通过，另有架构独立版本/质量/元数据审查。
- 新库与AI工具HTTP测试验证真实路由、入库、详情、匹配和专题冻结；GET /api/ai-tools提供6个受控操作，不执行任意代码或URL。
- 源码及样本用例与真实API成果分别保存；自动测试不调用生产账户，正式数据验证另留真实请求记录。

完整自动结果：[v13-final-check.txt](../../output/playwright/v13-final-check.txt)。真实预览汇总：[preview-validation-summary.json](../../output/v13-data/preview-validation-summary.json)。

## 浏览器与问题关闭

桌面1440×1000、手机390×844完成真实PC Landsat检索→3资源/75资产入库→6指数匹配→指定观测→小AOI→NDVI→下载/恢复。来源/日期变更明确解除绑定；手机版scrollWidth=390，无横向溢出。原始CLI工具引用失效、重启连接中断与外部天气/底图错误如实保留，详见[V13_UI.md](V13_UI.md)。

真实锁定S2测试还发现RGB预览包含red通道，按通用名称寻找必要资产会产生多匹配；已改用来源特定资产键严格核对，新增回归用例。预览图语义登记为rgb_visualization；S2按波段元数据/固有分辨率保留10/20m，避免用项目级GSD覆盖SWIR。失败原问题保留，修复后新任务实际通过，未把失败记录改写成功。

## 天地图及专业范围

升级保留本机天地图Key，重启后一次完整真实检查再次通过四服务/七请求，见[formal-tianditu-check.json](../../output/v13-data/formal-tianditu-check.json)。Key、SAS、环境文件和数据库不包含在分享包中。

当前范围：S2与L8/9小窗原生光谱执行；CDSE、SAR像元账户、地形分析、跨源融合、多景全域分类和通用模型服务仍有各自接入条件。用户此前确定的“模型后续配置”保持，当前数据与JSON Schema工具可供后续模型调用。
