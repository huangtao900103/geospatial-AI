# V16 农业光谱指数：公式、执行契约与真实像元验证

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

本次增加 EVI2、SAVI、MSAVI2、GNDVI、NDRE、BSI 六个固定公式执行器，与原有 NDVI、NDWI、MNDWI、NDMI、NDBI、NBR 合计十二个指数。Sentinel-2 支持十二个，Landsat 8/9 支持除 NDRE 外的十一个，共二十三个可执行来源契约。它们提供植被、土壤背景与裸土等光谱线索，不能直接认定作物种类、法定耕地、产量、叶绿素或氮含量，也不生成施肥或灾损结论。

## 固定定义与来源

N、R、G、B、S 分别表示已经定标的近红外、红、绿、蓝、SWIR1 反射率；Nn、Re 为 Sentinel-2 B8A、B05。公式由 Python 固定分支执行，不接收用户表达式，不使用 `eval`。

| 指数 | 固定公式 | Sentinel-2 输入与统计格网 | Landsat 8/9 输入与统计格网 | 原始论文或官方技术资料 |
|---|---|---|---|---|
| EVI2 | `2.5*(N-R)/(N+2.4*R+1)` | B08/B04，10m | SR_B5/SR_B4，30m | [Jiang 等，2008，DOI](https://doi.org/10.1016/j.rse.2008.06.006)；[NOAA 技术评审材料第142页](https://www.star.nesdis.noaa.gov/jpss/documents/AMM/NPP/VIIRS-GVF_SRR_Prov.pdf) |
| SAVI | `1.5*(N-R)/(N+R+0.5)`，L 固定 0.5 | B08/B04，10m | SR_B5/SR_B4，30m | [USGS SAVI](https://www.usgs.gov/landsat-missions/landsat-soil-adjusted-vegetation-index) |
| MSAVI2 | `(2*N+1-sqrt((2*N+1)^2-8*(N-R)))/2` | B08/B04，10m | SR_B5/SR_B4，30m | [USGS MSAVI 根号公式](https://www.usgs.gov/landsat-missions/landsat-modified-soil-adjusted-vegetation-index)；本实现命名为 MSAVI2，区分迭代式 MSAVI |
| GNDVI | `(N-G)/(N+G)` | B08/B03，10m | SR_B5/SR_B3，30m | [Sentinel Hub GNDVI](https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/gndvi/) |
| NDRE | `(Nn-Re)/(Nn+Re)` | **B8A/B05，两者原生20m** | **无红边，阻断** | [Esri NDVIre 定义](https://pro.arcgis.com/en/pro-app/latest/arcpy/image-analyst/ndvire.htm)；[Sentinel-2 波段官方说明](https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/bands/) |
| BSI | `((S+R)-(N+B))/((S+R)+(N+B))` | B11/B04/B08/B02，以 B11 原生20m统计 | SR_B6/SR_B4/SR_B5/SR_B2，30m | [Sentinel Hub Barren Soil 明确的 B11 四波段公式](https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/barren_soil/) |

EVI2 采用 Jiang 式15的 2.5 增益、红波段 2.4 系数，不采用部分网页示例中不一致的系数。BSI 明确采用 SWIR1 四波段定义，不引入展示脚本的亮度倍率，也不采用其更新版对统计值的 0–1 裁剪。NDRE 并不存在跨传感器通用的波段编号，本版本冻结为 B8A/B05，后续不能悄悄改成 B08/B05 或用 Landsat 波段替代。

## 定标、质量、格网与数值域

- Sentinel-2 从**实际选中场景的产品 XML**逐波段读取 BOA_QUANTIFICATION_VALUE 与 BOA_ADD_OFFSET：`reflectance=(DN+offset)/quantification`；DN=0 为无效。新波段 XML band_id：B02=1、B05=4、B8A=8。[Copernicus S2 产品说明](https://sentiwiki.copernicus.eu/web/s2-products)
- Landsat 从**实际选中 STAC 光谱资产**读取比例因子 0.0000275、偏移 -0.2、nodata=0 和原生30m声明，并在 COG 读取时核对实际格网；不套用 Sentinel-2 定标。[USGS 定标因子说明](https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products)
- 原有质量策略保持：S2 保留 SCL 4/5/6/7，SCL7 数量单列；Landsat 排除 QA_PIXEL bits0–5 及所有非零 QA_RADSAT，保留 water bit7。该保守 QA 不能等同严格无云或分类准确率。[USGS QA 位定义](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands)
- 每个所需反射率波段都参与有限值和非负值掩膜。新指数还核对实际输入 COG 的原生分辨率。统计始终使用最粗输入光谱波段的原生参考格网，其他输入最近邻对齐；预览缩放不参与统计。
- EVI2、SAVI、MSAVI2 使用 float64 中间运算，输出 float32；已定标反射率及结果均不裁剪至 0–1 或 -1–1。MSAVI2 负根号项、非有限结果、非正或接近零分母被掩膜，不把负根号项强行改成0。EVI2/SAVI/BSI 的分母门限为 `>1e-12`；归一化差值沿用旧版 `>0`。
- 新增 `invalid_denominator`、`invalid_radicand`、`nonfinite_formula_result` 原因计数。它们是诊断子项，可能与 `invalid_reflectance` 总拒绝数重叠，不能把所有原因简单相加作为无效总数。
- 非归一化三指数的 `value_range=[null,null]`，`threshold_range=[-2,2]` 仅为前端/请求参数策略，不是数学边界。直方图随实际有效最小/最大值生成，保存 `outside_normalized_range_count`，验证全部有效像元进入直方图。
- 每个新指数的 GeoTIFF 包含结果、共同有效掩膜、AOI掩膜、**全部所需已定标且对齐后的反射率**以及 SCL 或两个 QA 波段，允许独立复算；这些输入不是原始 DN 或未重采样源影像。S2 NDVI 的原有单期三波段、双期五波段导出保持兼容。

## 可供模型调用的契约

`spectral_indices.specification()` 保持旧字段，新增 `formula_kind`、`band_keys`、`supported_source_ids`、`value_range`、`threshold_range`、`change_threshold_range`、`visualization`、`visualization_range`、`histogram_policy`。`DEFINITIONS` 仍为 dict，值保留七项 tuple；旧六指数的算法版本仍为1.0，新六个为2.0。原生执行引擎为 `3.2-native`，机器契约目录版本为2.0。

`algorithm_catalog.contract()` 提供 `available`、`source_supported`、`blocking_reasons`。直接请求 Landsat NDRE 返回完整的不可用契约及 `SOURCE_INDEX_UNSUPPORTED`，匹配结果 `ready=false`；不会抛出 KeyError、让目录宕机或悄悄替换算法。目录只列二十三个实际支持的来源契约。匹配元数据仍不等于像元已读或分析已成功。

Sentinel-2 资产归一化将 B02/B05/B8A 固定映射为 blue/rededge/nir_narrow，并保留10m/20m/20m原生分辨率，避免 B8A 的供应商通用名或场景10m gsd误导匹配。蓝波段 Landsat 资产键为 `blue`，源波段为 SR_B2。

## 真实公开数据验证（2026-11-14）

选区 WGS84 `[114.2,30.5,114.21,30.51]`，请求时段 2025-11-01 至 2025-11-30，条件为单期指数 `>0`。以下是窗口试验，**不是仙桃市行政范围或农业专项诊断**；S2 与 Landsat 使用不同日期、不同格网，数值不能被当成传感器对比或变化监测。

| 来源 / 指数 | 真实日期 | 原生格网 | AOI像元 / 有效像元 | 均值 | >0像元 | 阈值命中格网投影面积 ha |
|---|---|---|---|---|---|---|
| S2 / EVI2 | 2025-11-14 | 114×100，10m | 10650 / 10650 | 0.1256515117 | 9521 | 95.21 |
| S2 / NDRE | 2025-11-14 | 57×51，20m | 2668 / 2668 | 0.1406183928 | 2277 | 91.08 |
| S2 / BSI | 2025-11-14 | 57×51，20m | 2668 / 2668 | -0.0147389672 | 1395 | 55.80 |
| Landsat9 / EVI2 | 2025-11-29 | 39×34，30m | 1184 / 1184 | 0.1190095801 | 1157 | 104.13 |
| Landsat9 / BSI | 2025-11-29 | 39×34，30m | 1184 / 1184 | 0.0204878789 | 749 | 67.41 |

实际场景：

- S2：`S2B_MSIL2A_20251114T025859_R032_T50RKU_20251114T052538`
- Landsat9：`LC09_L2SP_123039_20251129_02_T1`

五次均完成：真实 STAC 标准化→所需波段/质量/定标/原生分辨率契约匹配→锁定场景与元数据哈希→`verify_bound_scene` 核验实际资产→真实 COG 窗口读取→来源特定定标→完整原生 AOI 中心与质量掩膜→统计/地图预览/GeoTIFF。独立脚本只用导出数据重新计算 AOI 像元中心、全部有效掩膜、固定公式、均值、阈值像元数、投影面积和直方图；**五次全部有效像元的 float32 公式误差均为0，掩膜逐像元一致**。真实数据来源为 Microsoft Planetary Computer 上的 ESA S2 L2A / USGS Landsat C2 L2 SR。

证据：`output/v16-agriculture-indices/verification-summary.json`；各来源-算法子目录包含 `analysis.tif`、`overlay.png`、`result.json`、`verification.json`。保存无签名资产地址、定标/窗口/输入哈希、实际读取时间与缓存命中信息；未持久化 SAS。整个试验没有写入正式业务数据库。

复现命令：

```sh
.venv/bin/python -W ignore::PendingDeprecationWarning scripts/verify_v16_indices.py
.venv/bin/python -W ignore::PendingDeprecationWarning -m unittest tests.test_agricultural_indices_v16
```

新增离线测试覆盖六个公式的独立标量计算、非归一化尾部值与直方图、公式域拒绝、四波段完整掩膜、XML逐波段偏移、S2红边资产语义、Landsat阻断、全部新算法导出复算，以及真实本地GeoTIFF参考/输入格网错误的阻断。原六个指数的数值、质量掩膜及NDVI导出回归均通过。全后端353项回归通过后另新增一项原生红边COG格网测试，该新增测试通过。

现有限制保持透明：单次小窗跨度≤0.05°、≤1,048,576参考像元、每时段一景、最多183天窗口；不进行跨传感器变化计算、整市自动分块、时序物候/分类模型训练或未经当地样本验证的定量农学反演。
