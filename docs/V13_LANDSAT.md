# V13 Landsat 8/9可计算像元接入

新增`landsat_reader.py`与原生计算器的传感器质量分支，接入Microsoft Planetary Computer的`landsat-c2-l2`真实COG。此项是可计算波段接入，不是只增加目录条目。

## 执行契约

- 数据源ID：`pc_landsat_c2_l2`；本执行器只接受`landsat-8`与`landsat-9`。集合虽包含1982年以来的L4/5/7目录，不能据此宣布本执行器支持L4/5/7像元处理。
- 算法：NDVI、NDWI、MNDWI、NDMI、NDBI、NBR，均按实际30m参考格网。两期仍为后减前，先分别质量检查，再取共同有效格网。
- 每期从受限目录候选中选择一景满足日期、云量和bbox覆盖条件的影像；不是月平均或多景合成。`scene_id`可严格锁定单期影像，找不到不能默换景；单个scene_id不支持两期任务。
- 波段映射：green→SR_B3、red→SR_B4、nir→SR_B5、swir1→SR_B6、swir2→SR_B7；PC资产key分别为green/red/nir08/swir16/swir22。
- 每个所需反射率资产必须有实际STAC `raster:bands`的scale、offset、nodata和30m分辨率声明。当前契约为`DN × 0.0000275 − 0.2`、DN0无效；缺少或改变时失败，不猜测、不套S2尺度。
- 实际GeoTIFF亦须为30m米制北向格网，不能用目录标称值代替文件格网。只读选区窗口，原始DN窗口哈希、校准参数、配准方式和原生参考格网全部留痕。

## QA与数值口径

保留真实QA_PIXEL与QA_RADSAT，**不生成或伪装SCL**。

- QA_PIXEL bits0/1/2/3/4/5（fill、dilated cloud、cirrus、cloud、shadow、snow）任一置位即剔除；水体bit7不剔除。
- QA_RADSAT全非零均保守剔除，包含非本指数相关波段的饱和标记及terrain occlusion，因此可能比只检查相关波段更保守；不宣称严格无云或分类精度。
- QA bitfield零值是合法值，尤其QA_RADSAT=0代表未标饱和；读取不使用会把0误删的GeoTIFF nodata掩膜。源域外使用65535无效标记。
- 不clip负反射率或>1反射率。现有指数引擎明确剔除负输入和非正分母，分别统计质量剔除、无效值和无效反射率，避免把负值悄悄改成0。
- QA预检可用覆盖不足10%提前失败；最终指数共同有效覆盖不足10%不发布结果。阈值命中面积为投影格网条件面积，不是作物、法定耕地或灾损面积。

## 安全与产物

固定STAC、PC公开签名服务与`landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/`资产白名单；拒绝任意URL、query/fragment凭据与路径编码；目录和签名请求禁止跟随跳转。SAS仅内存缓存，不进入场景绑定、数据库或成果。网络错误只报告固定信息，不输出带签名的异常URL。

GeoTIFF对Landsat全部指数均额外导出已定标对齐反射率和QA_PIXEL/QA_RADSAT，以便独立复算；浮点32位可精确表示16位QA整数。报告保留reader版本`landsat-c2-l2-oli-1.0`与源场景指纹；现有S2原生版本和SCL分支保持，未更改旧产物。

## 本轮真实验收

武汉选区`[114.2,30.5,114.21,30.51]`，请求2025年11月，实际选择`LC09_L2SP_123039_20251129_02_T1`、观测2025-11-29。30m投影格网39行×34列，1184个选区像元中心全部有效；NDVI均值`0.23538662570248364`。

独立验收重新从原始COG读取红光、NIR、QA_PIXEL和QA_RADSAT DN，直接应用定标、位掩膜与指数公式，未调用计算器的grids方法；与导出TIFF指数和有效掩膜逐格完全一致。另以导出格网像元中心独立转回WGS84并直接比较请求经纬度矩形，1184个AOI像元与导出aoi_mask也完全一致（check-aoi-mask.py），不依赖引擎的AOI掩膜作为真值。证据：

- [原始DN复算程序](../output/v13-landsat/check-real-landsat.py)
- [真实运行及复算证明](../output/v13-landsat/real-ndvi-proof.json)
- [GeoTIFF](../output/v13-landsat/ndvi/analysis.tif)、[结果报告](../output/v13-landsat/ndvi/result.json)
- [新增12项隔离测试](../output/v13-landsat/tests.txt)覆盖传感器/日期/覆盖/锁景、定标、密钥不落盘、QA位、负反射率、GeoTIFF导出及实际格网冲突；合成fixture不是远端取得证据。

## 官方依据

- [PC Landsat Collection 2 L2集合与实际资产schema](https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2)
- [USGS尺度因子及偏移](https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products)
- [USGS Collection 2 QA](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands)
- [USGS Level 2产品](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products)

提供方目录许可字段与数据政策链接应原样保留。实际能获取一个小窗口不代表任意时空均有合格像元、服务永远可用、无配额限制，或已完成省域分类。
