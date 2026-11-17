# V11 地点、坐标与成果搜索

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

## 问题与实现

V10地图搜索仅遍历已经可见的叠加层feature，界面写“位置或结果名称”却没有地名索引，也没有持久化成果索引。V11新增`SearchServices`，将地点、坐标、完整成功任务与用户数据分开返回；前端点击和Enter采用同一命中列表。服务不依赖显示图层，也不将定位范围自动当分析AOI。

根接口：`GET /api/map/search?q=武汉&limit=12`。服务调用`SearchServices(store).search(query,limit=12)`。query不超过200字符，limit为1–20；空输入清空结果。返回：

```json
{"query":"武汉","results":[{"id":"place:1791247","kind":"place","label":"武汉","subtitle":"湖北省 · 地名参考点，仅用于地图定位","center":[114.26667,30.58333],"zoom":11,"source":"GeoNames 地名参考点","source_url":"https://www.geonames.org/1791247/","aoi_eligible":false}],"warnings":[],"provider":"workspace+reference_places"}
```

| kind | 实际搜索对象 | 恢复/定位字段 |
| --- | --- | --- |
| place | 本地GeoNames参考点/中文与拼音别名 | center、zoom、source_url；无行政polygon，aoi_eligible=false |
| coordinate | WGS84经纬度输入 | center、zoom；只定位，不创建分析选区 |
| result | 全部成功production/landcover/analysis任务，及绑定问题/回答/专题名 | task_type/task_id、result_ref、center/bbox；有匹配原分析问题时question_id |
| dataset | 用户导入数据集名称与其要素名称 | dataset_id、center/bbox、source_url；合成fixture不包装成用户导入 |

任务按真实task_id去重。索引包含原问题、answer.title、application_ref.name/id、指数名、实际观测日期和任务区域。多个问题复用同任务时按搜索意图选择原分析问题，来源/下载追问不成为主恢复qid；LC保留匹配问题的classes/cropland模式。不存在原问题时仍给真实task引用，不冒造问题。

## 地点来源、许可与覆盖

实际下载GeoNames官方`cities15000.zip`，按**country_code第8列等于CN**裁剪，保留2123个城镇参考点，其中湖北56个；另外添加GeoNames湖北ADM1参考点，共2124点，约1.0MB。没有把全球数据表放进项目，也没有运行时全量加载全球库。

- [GeoNames数据与条款](https://www.geonames.org/export/)
- [官方Gazetteer格式与CC BY 4.0说明](https://download.geonames.org/export/dump/readme.txt)
- [实际数据下载](https://download.geonames.org/export/dump/cities15000.zip)
- [湖北省参考点](https://www.geonames.org/1806949/hubei.html)

源ZIP大小3,367,526字节，SHA256为`50ef2f0cf7a1dd6d67123aaa6cdd45a8cf48ff5d23b3677e896f009ccce768d7`，上游Last-Modified为2026-11-13 02:44:25 GMT。裁剪条件、取得时间、源SHA、快照SHA、许可和边界均保存在`map_sources/china_reference_places.metadata.json`；数据文件为`china_reference_places.json`。少量湖北中文显示名/行政驻地点别名作了本地对照补充，不改源坐标和ID。

GeoNames数据按CC BY4.0署名、as-is提供，不保证准确、及时或完整。点位只用于地图定位，既不是市政府精确坐标承诺，也不是行政区边界、统计范围或法定测绘成果。库不含所有区县/村镇/兴趣点；未收录地名明确提示并可用坐标定位。本轮没有接入Nominatim或其他公共在线autocomplete服务，也没有要求用户API密钥。

## 坐标与失败处理

支持`114.2,30.5`、中文逗号、空格、`经度114.2 纬度30.5`、反向顺序的明确经纬标签，以及`114.2°E,30.5°N`。无标签固定经度在前；纬度限−85至85以适配WebMercator。非法数字坐标返回`INVALID_COORDINATES`警示与空结果，不作为地名重新查询，也不残留旧坐标。

ISO日期/年份作为成果检索词，不被误识为坐标。地名无命中、源库缺失与坐标非法分别返回NO_MATCH、PLACE_INDEX_UNAVAILABLE、INVALID_COORDINATES；库缺失不阻断已有成果和坐标搜索。

## 验证

`tests/test_search_services.py`共15项通过（0.131秒），验证真实地点ID/坐标/非AOI声明、拼音/中文、坐标格式与非法输入、空态/limit、全持久化成果、观测日期、专题/回答名、同任务去重与匹配意图恢复、用户图层要素名、NBR命名、零宽线范围及旧null记录容错。记录`output/playwright/v11-search-tests.txt`。

另以SQLite `mode=ro`适配器读取正式库，**未构建Workspace或触发恢复/修改**。实际武汉、仙桃、武昌区、坐标、MNDWI及2025-11-14命中正确，查询约7–17毫秒；正式库当时无NBR任务时明确NO_MATCH，未拿预览库伪造正式结果。证据`output/playwright/v11-search-readonly-formal.json`。这些是服务级只读验证，最终HTTP和浏览器定位/恢复由根负责人及前端专家另验。

# 原生像元核查（Inspection）

另由根负责人明确委托新增`inspection_services.py`。调用`InspectionServices(store).inspect(kind,task_id,lon,lat)`，仅支持成功的production/landcover；根接口为`GET /api/map/inspect?kind=production&task_id=...&lon=114.205&lat=30.505`。

读取固定本地`analysis.tif`或`classification.tif`，不接收文件路径或外部地址，拒绝VRT等其他driver。先核对WGS84源栅格包络，远离UTM投影域的合法坐标直接返回域外；再转换到原生CRS，用1×1 Window读取各band该格。这里的1×1指读取窗口；底层GeoTIFF可能解压包含该格的本地压缩块，不是承诺物理I/O只读4字节。

返回task_id/kind/coordinates/inside_aoi/valid/status/message、value/value_band/metric_label、有限值或null的bands、row/column/crs，以及实际日期/年份/参考分辨率。production单期主value为after指数，双期为后减前delta；必须aoi_mask=1且common_valid_mask=1，只有末期有限而共同无效时主值仍为null。旧样本/代表网格没有原生核查文件，明确unsupported，不从GeoJSON代表格或PNG猜值。

分类GeoTIFF写入时将域外与缺测都裁成0，因此Inspector另外用成果归档boundary对该原生格的中心重建1格市界掩膜；仅已知WorldCover类别且非NODATA有效，0/255不作类别。省市统计范围与地图bbox分别判断。所有NaN/无限值及文件NODATA转为JSON null，不改变任务、文件、掩膜或统计数字。

`tests/test_inspection.py`共9项通过（0.089秒），覆盖原生单格/投影转换、共同无效但末期有限、AOI外与栅格外、非法坐标与未完成任务、旧代表样本拒绝、真实多边形域外、分类0/255与缺边界、源文件/任务不变，以及(27,0)/(-153,0)等远离UTM域的合法坐标不触发未处理CPL异常。

只读正式验证`v11-inspection-readonly-formal.json`：从正式MNDWI TIF选定有效格，核查值与独立rasterio单格值精确一致；padding格为outside_aoi/null，投影远点为outside_raster/null；正式LC取known像元读取code40一致。另发现GeoNames仙桃城市PPL参考点在该2017概化参考市界之外，核查正确返回outside_aoi：这正是“地点参考位置不能自动当作统计边界”的实际差异，本轮没有调整旧边界或面积使两者强行吻合。源码、单元和只读TIF验证之外，最终HTTP/地图点击由根负责人/前端专家验证。
