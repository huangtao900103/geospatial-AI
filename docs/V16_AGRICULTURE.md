# V16 农业原生栅格派生分析

本轮新增两项可执行能力：植被覆盖度（FVC）估算、逐地块遥感分区统计。两者读取平台已成功归档的真实原生 GeoTIFF；不从地图瓦片、预览 PNG、教学样本或失败任务推算业务结果。入口使用现有任务 ID 和已导入的用户图层 ID，不接受任意文件路径或 URL。

## 业务链路

1. 在一张图完成单期/两期遥感生产或年度耕作地产品提取，取得成功的原生栅格任务。
2. 选择“植被覆盖度估算”，明确输入土壤与全植被 NDVI 端元；或选择“逐地块遥感统计”，指定用户地块图层、统计字段及可选阈值。
3. 系统冻结源文件 SHA256、源任务方法/来源版本、用户地块 SHA256/版本，异步校验、读取、计算、归档。
4. 一张图呈现 FVC 配准覆盖层或带统计属性的地块图斑，提供完整 GeoTIFF/GeoJSON、CSV 与 JSON 来源报告。缺测数据保留为空，不补零。

## 服务与接口契约

模块 `agriculture_services.py`，版本 `agriculture-1.0`。任务类型为 `agriculture_task`，ID 前缀为 `agriculture_`。服务初始化为 `AgricultureServices(store, output_dir=None)`，支持 `capabilities()`、`prepare(body)`、`create(body)`、`get(id)`、`list()`、`cancel(id)`、`retry(id)`、`artifact(id,name)`、`close()`。

HTTP 路由由平台统一注册：

| 操作 | 接口 |
| --- | --- |
| 能力 | `GET /api/agriculture/capabilities` |
| 列表/创建 | `GET/POST /api/agriculture/tasks` |
| 查询 | `GET /api/agriculture/tasks/:id` |
| 取消/重试 | `POST /api/agriculture/tasks/:id/cancel`、`/retry` |
| 成果 | `GET /api/agriculture/tasks/:id/:filename` |

FVC 请求示例（端元数值仅说明 API，不是推荐的通用校准值）：

```json
{
  "algorithm_id": "fvc",
  "source_type": "production",
  "source_task_id": "production_<已有成功单期NDVI任务>",
  "soil_ndvi": 0.2,
  "vegetation_ndvi": 0.8
}
```

逐地块请求示例：

```json
{
  "algorithm_id": "zonal_statistics",
  "source_type": "production",
  "source_task_id": "production_<已有成功任务>",
  "dataset_id": "<已导入的用户Polygon或MultiPolygon图层>",
  "field": "after_index",
  "threshold": 0.4
}
```

`source_type=cropland` 时，`field` 只能为 `classification`，不允许阈值。两期 production 可显式选择 `delta_index`；其阈值域为源指数范围的有符号差值范围，例如源 EVI2 声明 `[-2,2]` 时差值域为 `[-4,4]`。FVC 不接受地块、字段或阈值等无关参数。

任务 `plan.algorithm_id`、`plan.request` 为公开规范输入，`plan.input_fingerprint` 用于兼容性比较。`plan.source` 含源文件 SHA256、源记录指纹、产品/年份/实际场景日期/范围/CRS 等；公开响应不含本地源文件路径。源文件、方法、来源或用户图层版本在执行前或归档前发生变化，任务直接失败，不能悄然重绑定。

成功任务 `result` 结构为：

```text
summary     定量汇总、单位、覆盖与质量
geojson     EPSG:4326结果图斑
image       FVC配准预览（zonal为null）
artifacts   归档文件名称/类型/相对API URL
provenance  冻结源引用、方法、实际日期/产品年份、面积方法
report      输入、发现、质量说明和方法局限
```

## 算法、单位与质量口径

### 植被覆盖度

仅接受 `mode=sentinel_cog`、`sampling=native`、来源为真实公共卫星像元、`goal=ndvi` 且 `index_id=ndvi` 的成功单期生产任务。两期共同有效掩膜、其他指数和人工样本均拒绝。

```text
FVC_raw = (NDVI - NDVI_soil) / (NDVI_vegetation - NDVI_soil)
FVC = clip(FVC_raw, 0, 1)
```

土壤/全植被端元必须显式填写，满足 `-1 <= soil < vegetation <= 1` 且间隔大于 `1e-6`。当前记录其来源为 `explicit_user_values_not_locally_calibrated`，不声称已完成本地校准。输出无量纲覆盖比例；统计未裁剪极值与低/高裁剪像元数。保留原始未裁剪 FVC，不隐去端元假设造成的越界。

`summary` 包括像元均值/中位数/P10/P90/最小/最大/标准差、面积加权均值、有效/未知像元与面积、裁剪计数、端元、原生 CRS 和格网尺寸。估计植被面积为有效像元内 `Σ(FVC×像元面积)`，单位 ha，不等同于法定耕地或某类作物面积。无有效像元时均值、分位数及估计植被面积为 `null`。

FVC 产物共 5 项：`analysis.tif`、`overlay.png`、`result.geojson`、`result.json`、`summary.csv`。TIFF 保留原生格网，波段描述依次为 `fvc`、`fvc_unclamped`、`common_valid_mask`、`aoi_mask`、`after_ndvi`。预览单独重投影至 EPSG:3857 并使用最近邻采样；`image.bounds` 为 EPSG:4326。所有统计来自原生格网而非预览图。

### 逐地块分区统计

只接受来源明确为 `user` 的 Polygon/MultiPolygon 图层。图斑自动由 EPSG:4326 转到源栅格 CRS，以像元中心纳入地块，统计区域为“地块 ∩ 源 AOI 掩膜”。不通过全接触像元放大边缘面积，不从地块未观测部分外推。

生产栅格支持单期 `after_index` 或两期 `delta_index`。每块输出有效像元均值/中位数/P10/P90/最小/最大/标准差；可选 `>= threshold` 像元数及面积。年度分类支持 IO2024（耕作地代码 5，掩膜外、云和未知类别不计为有效）及 WorldCover2021 v200（耕作地代码 40，使用归档选区边界）。它们是源年度产品的分类面积统计，不是当前年度独立训练识别。

覆盖状态按冻结源 WGS84 范围及像元交集报告 `full/partial/outside`。`partial` 不提供虚假的完整地块覆盖比例，`source_coverage_ratio=null`；`valid_ratio_covered` 仅为“已覆盖像元中的有效比例”，不能理解为整个地块的有效覆盖。小于像元且没有像元中心命中的地块保留 `covered_pixel_count=0`、`pixel_center_hit=false` 与说明；在源范围内的小地块不因没有像元而误标为域外。

每个结果图斑保留原始业务属性与 ID/名称，新增属性以 `agri_` 开头，包括：

| 属性（省略前缀） | 含义 |
| --- | --- |
| `zone_id`、`zone_name` | 原图斑 ID/名称 |
| `coverage_status`、`source_coverage_ratio` | 源范围覆盖状态/保守覆盖口径 |
| `covered_pixel_count`、`valid_pixel_count`、`unknown_pixel_count` | 源掩膜交集/有效/未知像元数 |
| `valid_ratio_covered` | 交集内有效比例，无交集为 null |
| `covered_area_ha`、`valid_area_ha`、`unknown_area_ha` | 原生格网面积 |
| `mean`、`median`、`p10`、`p90`、`min`、`max`、`std` | 生产指数统计，无有效值为 null |
| `selected_pixel_count`、`selected_area_ha` | 可选阈值命中统计 |
| `cropland_pixel_count`、`cropland_area_ha`、`cropland_share_of_valid` | 分类产品耕作地统计 |

地块可能重叠，`sum_zone_*` 明确为逐块累加、可重复计量；`unique_*` 独立按原生像元逻辑并集计算，不重复计入重叠。不能把逐地块累加值当作唯一耕地面积。Zonal 产物共 3 项：`result.geojson`、`zones.csv`、`result.json`。

### 面积与运行边界

北向上 EPSG:4326 格网采用 WGS84 椭球纬度带逐行像元面积；明确以米为单位的投影格网使用仿射像元面积。拒绝旋转格网、未知面积单位等不支持情况，不猜测单位。所有面积以 ha 输出，10,000 m² = 1 ha。

本机上限为 1,048,576 原生像元、200 个地块、同时最多 2 个农业任务。超限明确失败，不悄然降采样。取消、失败或中断任务不开放部分产物；重试前重新验证冻结来源。能力不表示已获得作物种类、LAI、产量、灾损或法定耕地认定。

## 验证证据

单元测试：

```sh
.venv/bin/python -m unittest discover -s tests -p test_agriculture_services.py -v
```

11 项通过，覆盖 FVC 数值与裁剪、显式端元/单期来源约束、源文件与图层版本变更、原生格网分区、孔洞/重叠/域外/小地块、缺测不当零、分类云/未知掩膜、指数和差值阈值域、任务取消/重试/产物权限。日志：`output/agriculture-unit-tests.txt`。

真实源独立复算记录：`output/v16-agri-derived/real-proof.json`，复算脚本：`output/v16-agri-derived/verify-real.py`。正式数据库只以 SQLite `mode=ro` 读取已成功任务元数据，未实例化正式 Workspace、未写入正式 DB；派生任务、分析地块与产物均隔离在测试存储和 output 目录。

| 验证 | 实际证据 |
| --- | --- |
| FVC | 真实 NDVI 源 `production_da40e01927d540`，709 个有效像元；明确示例端元 0.2/0.8，平均 FVC `0.2531458747025436`；与独立 NumPy 公式逐像元复算最大绝对差 `1.9868214962137642e-08`（float32 输出量化）。示例端元未本地校准，此值证明真实链路与数值实现，不证明当地植被覆盖实测精度。 |
| IO2024 分区 | 真实源 `cropland_9878968bb8b541`，独立分析用的西半区/东半区/完整 AOI 三个测试分区，耕作地像元分别 3,418 / 3,947 / 7,365。唯一并集 7,365 像元、73.65 ha；含重叠完整 AOI 的逐区累加 147.3 ha。独立读源复算完全一致。测试分区不是确权地块或真实农田边界。 |

实际任务 JSON 与 GIS/CSV/GeoTIFF 等成果保存在 `output/v16-agri-derived/real-*` 和 `output/v16-agri-derived/real/`。分类的 2024 年产品结果不表述为 2026 年现状。上述分析小范围技术证据不冒充仙桃全市统计。
