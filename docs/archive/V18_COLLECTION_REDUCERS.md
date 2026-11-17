# V18 真实多时相影像集合归约

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

本轮在本机实现中位数合成、均值合成、逐像元线性趋势三个固定算子，参考Google Earth Engine的公开集合归约和回归方法。原有十二个指数均可作为集合输入；Landsat不具备NDRE所需红边波段时仍明确阻断。这是本机执行，不表示取得OpenGE或GEE账号、授权、云算力或调用了这些云服务。

## 方法依据

- [GEE ImageCollection reductions](https://developers.google.com/earth-engine/guides/ic_reducing)：集合按时间进行统计归约，输出须明确投影和尺度。本实现冻结最新所选场景的原生参考格网，并保存全部逐景指数及有效掩膜，避免把预览分辨率当统计格网。
- [GEE Linear regression](https://developers.google.com/earth-engine/guides/reducers_regression)：以时间为自变量、指数为因变量的带截距最小二乘。本实现采用真实UTC时间间隔的“天”，不以第1/2/3景序号代替不规则过境间隔。
- OpenGE集合获取、过程执行等平台组织方式由联合研究记录另行说明；这两页GEE文档是本模块中位数/均值/OLS的直接方法参考，不将其他网站没有说明的算法归给它。

## 数据与选景

只访问固定Microsoft Planetary Computer STAC集合：Sentinel-2 L2A或Landsat8/9 Collection2 L2。单次WGS84矩形单边≤0.05°、日期窗口≤183天、最多6个独立UTC日期。目录最多遍历4页×50条；仍存在未遍历分页时明确失败，请缩短时段，不使用不完整候选集假称全时段分析。

候选必须满足来源、日期、景云量和完整bbox覆盖；每个UTC日期选择最低景云量场景，ID解决同分，超过上限时按合格日期序号等间距抽样，包含首末日期。它是**最多六日期的观测抽样统计**，不是逐日实测、全部影像无遗漏合成或完整月平均。

每个选中场景再通过既有单景锁定读取器核对ID和实际日期，S2从各景XML读取比例与偏移，Landsat从实际STAC资产读取scale/offset和QA。各景光谱与质量窗口最近邻对齐至最新景的原生参考格网：S2根据指数为10/20m，Landsat30m，不跨两类传感器合成。S2A/B/C或Landsat8/9家族成员会分别记录，没有额外跨卫星偏差校准。

完整集合manifest包含候选页指纹、确定日期/场景/定标与URI、选景规则及选择SHA256，重试沿用原集合；输入或manifest变化会阻断。实际窗口读取指纹单独保留，提供方未保证公开URI永久内容版本不变。

## 有效支持和趋势含义

SCL保留4/5/6/7（7不是严格无云保证），Landsat沿用QA_PIXEL bits0–5与所有非零QA_RADSAT剔除。各景独立质量掩膜，无效波段、负反射率和公式域无效值均排除。读取成功但0有效像元的场景保留在集合、时序和观测计数中；网络、文件或定标读取失败则整任务失败，不暗中删景或换源。

合成每像元至少2次有效独立日期，趋势至少3次。支持不足的值为NaN/JSON null/透明；若全区都不足支持，仍归档真实计数与透明图，均值不填0，并明确警告。没有插值或缺测填充。SCL7数量/质量条件仍见来源明细。

OLS拟合 `index = intercept + slope * elapsed_UTC_days`。趋势输出单位为**指数/日**；截距对应全部所选日期的首时刻，不保证该像元在首景有有效观测。常量指数序列的R²为null而非捏造为1；斜率不是统计显著性、未来预测或灾损认定。预览按有效斜率绝对值98分位拉伸，只有颜色截断，GeoTIFF及统计不裁剪。

逐景CSV分别给出该景所有有效像元均值、各日期固定共同有效像元均值以及有效数量/比例。前者的空间支持会变化；后者可能为空。不能把缺共同支持的null折线画成0，也不能仅用变动范围的区域均值认定同地块变化。

## 核心接口与产物

模块 `collection_raster.py`，版本 `collection-native-1.0`：

- `make_plan(body)` 纯验证，支持 `algorithm_id/source_id/index_id/bbox/start/end/scene_limit/cloud/requirement`，不联网。算法ID为`temporal_median`、`temporal_mean`、`temporal_trend`。
- `discover_collection(plan, reader, check)` 返回冻结manifest。
- `run_collection(plan, reader, directory, check=lambda:None, progress=None)` 返回标准result，允许任务服务附加request/input_fingerprint；不得覆盖core的plan_fingerprint。已存在的`plan.collection`必须有效，不能因空值静默重选。
- `reduce_stack(stack, times_days, aoi_mask, algorithm_id, check)` 为固定纯数值归约，便于独立离线核查。

固定5文件：`analysis.tif`、`overlay.png`、`result.geojson`、`series.csv`、`result.json`。TIFF包括主结果、有效观测次数、有效掩膜、AOI掩膜、每个场景的指数和有效掩膜；趋势另有截距、R²、有效观测跨度。它保存可重算归约的逐景指数，而非重复打包全部原始DN或全部未配准光谱；原波段资产、定标及窗口SHA256在来源中保留。统计不来自PNG。

## 2026-11-14真实公开源验证

验证范围 `[114.2,30.5,114.21,30.51]`，时段2025-10-01至2025-11-30，景云量≤30。这是算法流程选区，不是仙桃全市、实测地块或农业诊断。

S2目录23候选、12个合格独立日期，选4景：2025-10-05、10-28、11-21、11-29；含UTM49/50不同源格网，统一到最新景EPSG:32650原生格网。

| 来源 / 算子 | 统计格网 | 输出有效像元 | 有效结果均值 | 独立逐像元值差 |
| --- | --- | ---: | ---: | ---: |
| S2 NDVI中位数 | 10m | 10650 | 0.2359060347 | 0 |
| S2 NDVI均值 | 10m | 10650 | 0.2339639192 | 0 |
| S2 NDVI OLS趋势 | 10m | 10650 | 0.0002854971781 指数/日 | 0 |
| Landsat9 EVI2均值，3景 | 30m | 1184 | 0.1364677247 | 0 |
| S2 NDRE均值，2景 | 20m | 2661 | 0.1468439731 | 0 |

逐有效像元从导出TIFF读取各景指数，以独立标量均值/中位数、中心化OLS复算；观测计数、有效掩膜和直方图平衡完全一致。趋势R²最大差1.46e-11。不同日期、不同传感器和格网的均值不能直接作跨源差异或年度增减解释。

证据：`output/v18-collection/independent-proof.json`及各case原生TIFF/JSON；核心8项离线测试（包括稀疏、0值、全无效、冻结变化、错误日期/范围、重复日期和不规则时间）通过，日志`output/v18-collection/unit-check.txt`。测试夹具仅在临时目录生成，不安装到业务库。

重跑真实验证：`.venv/bin/python scripts/verification/verify_collection_v18.py`。该脚本重新访问真实公开目录/资产并独立复算，不实例化Workspace、不写正式数据库。提供方或网络不可用时会如实失败。
