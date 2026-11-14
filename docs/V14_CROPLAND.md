# V14 地图选区耕作地标注

本轮把“框选区域后输入一句话，自动标注农田”接入真实数据处理链。数据固定为ESA WorldCover2021 v200的类别40（Cropland）；该产品基于遥感影像生成。平台这次执行的是已有年度分类产品提取与GIS化，不是对当期影像运行新的农田识别模型。

## 用户操作

1. 在地图框选小范围，或在“专题分析 → 耕作地标注 · 2021参考”中输入WGS84矩形坐标。
2. 输入“要把某一选定区域内的农田通过遥感影像全部标注出来”，或点击专题运行。
3. 系统核验选区、定位官方COG瓦片、读取全部原生分类像元、提取全部四邻域耕作地连通图斑、独立核对数量和面积，再归档。
4. 同一张图显示绿色耕作地图斑；结果卡显示2021年份、面积、图斑数、有效覆盖。点击图斑查看编号、面积和像元数；开启像元核查后点击可读归档GeoTIFF分类码。
5. 下载GeoJSON、GeoTIFF、逐斑CSV、逐行复算CSV、PNG及JSON报告。任务历史和地图搜索可以恢复这份结果；重复同一范围、产品与方法可复用已完成真实任务，原问题与答案保留绑定。

缺少选区时只要求选区；明确请求今年、最新、其他年份、具体作物、确权或高标准农田时说明未接通，不能把2021历史产品或NDVI阈值当答案。用户可明确改用选区2021参考能力，平台创建新的问题记录。

## 输入和产物

```json
POST /api/application-runs
{
  "application_id": "cropland_delineation",
  "context": {"bbox": [113.3,30.3,113.31,30.31]},
  "parameters": {},
  "request_id": "my-cropland-request"
}
```

返回持久化问题ID。轮询`GET /api/assistant/questions/:id`，只有`answered`才读取答案和下载；真实子任务类型为`cropland`，执行工具版本`worldcover-aoi-patches-v1`。后续模型可以通过`GET /api/ai-tools`中的`delineate_cropland_2021`调用相同能力；通用模型服务尚未配置。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/cropland/capabilities` | 年份、方法、范围与运行依赖 |
| POST | `/api/cropland/tasks` | `{bbox, product?, requirement?}`，产品只允许`worldcover_2021_v200` |
| GET | `/api/cropland/tasks` | 真实任务列表 |
| GET | `/api/cropland/tasks/:id` | 冻结输入、节点状态、原生统计、图斑与来源 |
| POST | `/api/cropland/tasks/:id/cancel` | 取消未完成任务，未完成产物不开放下载 |
| POST | `/api/cropland/tasks/:id/retry` | 对失败/取消任务创建新任务 |
| GET | `/api/cropland/tasks/:id/:filename` | 成功任务的固定产物白名单；不允许任意文件或URL |

产物文件为`cropland.geojson`、`classification.tif`、`patches.csv`、`row_counts.csv`、`overlay.png`、`classes.png`、`result.json`。GeoJSON保留连通图斑和孔洞，每斑包含`patch_id`、`area_m2`、`area_ha`、`area_mu`、`pixel_count`、`class_code`和产品年份。

## 定量口径

每边不超过0.05°，最多1,048,576原生像元、20,000个图斑。超限明确要求缩小，不降采样、截断或筛除碎斑来冒充全部。

原生EPSG:4326像元面积按WGS84椭球纬带逐行加权，不能统一乘100平方米。像元中心在矩形选区内即纳入，保留整像元轮廓，因此输出边缘可能越出选区约半个像元。连通图斑不是宗地、田埂或确权地块。有效覆盖与缺测单独统计，缺测不是非农田；有效覆盖100%不表示分类精度100%。零耕作地是合法结果，保留选区和真实零值。

官方产品来源、许可和定义见[WorldCover数据说明](https://esa-worldcover.org/en/data-access)；本轮没有把其他年度探针结果列为已接通能力。完整年份、实际读取瓦片、选区指纹、分类窗口指纹及方法都保留在结果来源记录中。
