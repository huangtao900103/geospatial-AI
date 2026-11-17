# V13 遥感资源数据库与多传感器执行：架构审查

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

日期：2026-11-14。角色：数据与遥感架构监督。本文只记录本轮核实的一手资料、接口决策和审查结论；外部数据与算法权属仍属于相应提供方。

## 第一轮：数据库为实际分析服务

数据库需要能够回答“某个区域、时段、指标能使用哪些观测，所需波段/质量/定标是否齐全”，而不是仅列出供应商名称。建议以collection、observation/item、asset及不可变检索/执行快照组成统一资源层：

- Item身份以provider、collection、item_id为复合键；跨提供方同一观测可建立关联，不应删除第二个来源或将重复景数称独立观测数。
- 观测日期、产品处理日期、抓取日期分别保存；空间范围为WGS84/GeoJSON经度在前，原生栅格CRS和分辨率保留在资产层。
- 资产保存通用波段、物理单位、scale、offset、nodata、质量掩膜类型、角色、媒体类型、许可、认证需求、版本与可信原始地址。签名SAS与账户密钥只在服务端内存/专用私有配置使用，不进入可分享资源库。
- “已发现目录”“有像元读取适配”“具体资产授权可读”“算法兼容”“已实际验证”分别计量。地图瓦片、预览图、压缩产品档案不能伪装成单波段COG。
- 算法声明输入波段、单位、QA策略、传感器/产品族、日期与范围上限、输出物理量。只有全部硬约束满足后，才可给AI提供可执行契约；缺波段、未定标、缺权限等情况明确阻塞。
- 执行冻结实际item、资产元数据指纹、质量策略、算法及软件版本，结果保留来源；候选计划不能直接成为“本次实际使用”。无声替换来源、年份或分辨率不可接受。

本机可沿用SQLite与对象化业务记录，增加适当唯一键、时间/空间索引和版本快照；不应为了扩大数据源数量而迁移或改写原有任务、答案和已验证成果。

## 一手资料核实

| 数据源 | 核实入口 | 结论与限制 |
| --- | --- | --- |
| PC Landsat C2 L2 | [集合元信息](https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2)、[USGS比例因子](https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products)、[QA说明](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands) | 集合覆盖Landsat4/5/7/8/9，首版可计算适配应明确限制L8/9，不能把1982起的目录覆盖称全部年代已可执行。SR使用DN×0.0000275−0.2；质量使用QA_PIXEL与QA_RADSAT。 |
| Earth Search | [提供方README](https://github.com/Element84/earth-search)、[S2说明](https://github.com/Element84/earth-search/blob/main/docs/collections/sentinel-2-l2a.md)、[Landsat说明](https://github.com/Element84/earth-search/blob/main/docs/collections/landsat-c2-l2.md) | 固定v1接口；v2仍beta，collection名称语义变化。S2 COG的偏移是否已应用必须读资产scale/offset，不重复套PC XML定标。Landsat存在历史死链，requester-pays/s3地址不能硬改为匿名HTTPS可读。 |
| Copernicus GLO30 | [EarthSearch集合](https://earth-search.aws.element84.com/v1/collections/cop-dem-glo-30)、[AWS官方登记](https://registry.opendata.aws/copernicus-dem/) | 名义30m DSM含建筑/基础设施/植被，不是裸地DTM；公开版本不是所有国家每块都覆盖，海洋无瓦片。保存许可链接，不把STAC的proprietary字段擅改成CC BY。 |
| CDSE STAC | [当前目录文档](https://documentation.dataspace.copernicus.eu/APIs/STAC.html)、[S2集合](https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-l2a)、[S1集合](https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd)、[S3认证](https://documentation.dataspace.copernicus.eu/APIs/S3.html) | 使用https://stac.dataspace.copernicus.eu/v1/，旧catalogue路径已弃用。公开目录不等于像元免账户；资产auth:refs区分S3与OIDC。新STAC1.1字段含bands、eo:common_name、proj:code，需兼容规范差异。 |

本轮实际PC检索在武汉小范围获得LC09_L2SP_123039_20251129_02_T1，观测2025-11-29、landsat-9、30m、EPSG32649、景云量0.17%。其red/green/nir08/swir16/swir22资产均声明scale2.75e-05、offset−0.2、nodata0；qa_pixel与qa_radsat为uint16质量资产。此证据只证明目录和资产声明，像素读取由执行专家另行验收。

本机第一次EarthSearch S2与GLO30检索遇到超时，必须保留实际失败状态，不能因官方文档可读就声明本机目录/像元均已成功。CDSE collections以及具体S2/S1集合已实际取得，未登录或生成CDSE凭据。

## Landsat质量与定量边界

L8/9 QA_PIXEL的fill、dilated cloud、cirrus、cloud、cloud shadow、snow应按已声明规则剔除；water标记不能因其是水体就被错误剔除。QA_RADSAT可采用全非零剔除的保守策略，但必须如实说明较所需波段饱和掩膜更严格。像元QA不同于整景云量。

原始SR经定标可能出现负值或大于1，不能无声裁剪到0–1再计算指数。指数零分母、无效值、有效格网与投影面积分别处理；30m像元间隔不等于作物识别精度。不同传感器光谱响应和分辨率不同，本轮不得直接混景做连续变化序列或宣称融合后更准确。

S2的B08是10m宽近红外，B8A是20m窄近红外；不能将Landsat资产键nir08映射规则无差别应用到CDSE的S2。通用波段词汇必须与产品映射表一起使用。

## 第二轮实现与验收

已完成以下独立代码与数据审查，完整平台HTTP/浏览器及旧库保留仍以主负责人最终验收为准：

1. `observation_repository.py`新增观测、资产、检索成员和像元使用证据表，观测键包含来源、集合、条目和元数据指纹；重复检索仅更新last_seen，同条目元数据变化生成新版本，资产有独立索引。原通用records与历史答案不被此模块重写。
2. `algorithm_catalog.py`提供6指数×2具体来源的12个契约。匹配检查来源/集合/平台、带时区观测时刻、必需光谱与QA、可信无签名定位符、物理量/定标/分辨率及执行依赖；元数据通过仍标metadata_only、pixels_read=false。EarthSearch、CDSE、DEM、SAR没有相应执行适配时明确阻塞，不依赖两个类似波段名推导可执行。
3. 本模块11项有意义的隔离测试通过：两个来源全部指数、XML待定标与Landsat显式scale/offset/nodata/30m、缺资产/QA、旧传感器、错来源/集合、任意或带签名地址、错误单位/波段语义/媒体、缺运行时、无时区日期、重复资产键等。没有使用测试对象冒充真实像元。
4. 独立实际取得PC Landsat元数据，经过主负责人的normalizer及算法匹配得到25项资产、6个可请求指数，未读取像元；记录[v13-architect-metadata-audit.json](../../output/playwright/v13-architect-metadata-audit.json)。其内还记录匿名Copernicus GLO30资产Range请求实际206、TIFF文件头正确；此证据只证明资产字节可读，不是地形分析已接。
5. `landsat_reader.py`与`native_raster.py`明确只支持L8/9、实际30m米制北向格网，读取必需波段及两项QA；QA原始零值保留，非有限/小数/溢出/质量位失效剔除；不伪造SCL。真实NDVI产物保留已定标光谱与原QA，执行专家另行完成原DN/QA与导出逐格复算，记录`output/v13-landsat/real-ndvi-proof.json`。本架构审查读取其源代码与证据，没有将“查看证据”描述为亲自重跑像元分析。
6. 第二轮发现并关闭锁定版本缺口：仅固定item_id仍可能遇上游重处理。现在discover后、read前核对冻结元数据指纹、id/时刻/集合/来源/显式传感器及必要资产地址与已声明定标；漂移报OBSERVATION_VERSION_CHANGED，须重新入库，不向旧版本记像元已验证。
7. 像元使用证据须对应已成功任务、实际input_sha及相同observation_ref的id/metadata_sha，发布成功后登记。独立18项隔离输入通过：同瞬间异时区接受，各来源/版本/资产/定标变化拒绝；失败/取消/错输入哈希/错观测/错版本不得记像元使用，完全匹配才允许。见[v13-architect-binding-audit.json](../../output/playwright/v13-architect-binding-audit.json)。

## Earth Search本机网络诊断与授权范围

本轮按主负责人要求限3次诊断，没有改系统DNS或关闭TLS：curl IPv4及Python系统解析均无法解析官方EarthSearch域名；公共HTTPS DNS诊断也因本机出网失败/超时未取得可用解析。因此当前失败属于本机DNS/路由限制，不能单纯延长STAC读取时间或修改Accept宣告修复。记录[v13-earthsearch-network-audit.json](../../output/playwright/v13-earthsearch-network-audit.json)。可接入同产品的其他已可达官方目录，但必须以另一个来源保留出处，不能伪装成原EarthSearch请求成功。

CDSE在本轮只接公开元数据，没有创建、复制或猜测其账户/S3/OIDC凭据，不能把其COG样式href当免账户读取授权。新数据入口也没有把天地图浏览瓦片当原始光谱，或自动将行政参考界替换到已有2021耕地统计。


## 主负责人最终落地补充

本机EarthSearch两源DNS诊断失败后，新增独立source_id=pc_cop_dem_glo30，正式PC目录实际返回1项同产品地形资源；没有把此结果归到EarthSearch。正式库共入库5来源11个资源版本、261项资产，286项完整检查通过，原176条记录哈希未变，见QA_V13.md。

真实S2锁定补测发现RGB预览的red通道与B04通用名称重叠。必要输入校验已改为来源特定资产键，RGB预览按显示资产登记；新回归及实际S2任务通过。来源检查采用追加快照，避免升级后的检索覆盖旧验证记录。
