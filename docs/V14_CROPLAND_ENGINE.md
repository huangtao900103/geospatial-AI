# 选区耕作地参考标注引擎

`cropland_services.py` 是真实公开分类栅格的选区提取器，数据固定为 **ESA WorldCover 2021 v200** 的 Cropland 类别40。它不调用NDVI阈值猜测农田，也不把历史产品称为当期遥感影像新识别。WorldCover类别不是法定耕地、确权地块或作物细分类别。

## 输入与执行

`CroplandServices.create({bbox:[west,south,east,north], product:'worldcover_2021_v200', requirement:'…'})`。

`product`、`requirement` 可省略；选区必须显式提供。每边最多0.05度、原生格网最多1,048,576像元。API不接收任意URL、文件路径或任意代码。涉及非2021年份、相对当期、月季度、具体作物、法定或官方口径的请求会明确拒绝，不能被产品字段静默改成2021。空间用语“当前选区”“当前范围”可以使用。

1. 保存选区与2021产品的不可变计划快照。
2. 固定官方S3 COG地址读取选区全部原生分类像元，沿用既有WGS84椭球逐行面积统计。
3. 保留类别40的全部四邻域连通图斑，保留孔洞，不滤小斑、不做几何简化。超过20,000图斑则明确失败并建议缩小范围，不截断成果。
4. 逐图斑在其原生像元窗口复算像元数和逐行面积，核对全量栅格统计守恒。
5. 输出原生GeoTIFF、配准PNG、图斑GeoJSON、逐斑CSV、逐行CSV和结果JSON。

采用像元中心选区掩膜，图斑保留完整像元，可能超出选区边缘半个像元。分类覆盖率来自有效类别像元，缺测像元和面积单列。图斑数量是连通分类斑块数量，不是实际田块数量。

## 服务与结果契约

服务提供 `get/list/create/check/cancel/retry/artifact/close`，存储kind为 `cropland_task`，结果文件前缀 `/api/cropland/tasks/:id/`。构造服务默认把成果保存在当前store数据库目录下的 `cropland/`，测试库与正式库隔离。

`result` 包含：

- `bbox`、`boundary`：原始用户选区。
- `summary`：公顷、亩、参考年度、原生像元数、有效覆盖率、缺测、图斑数和面积核对状态。
- `geojson`：全部图斑，属性包括 `patch_id`、`class_code`、`reference_year`、`pixel_count`、`area_m2`、`area_ha`、`area_mu`、`connectivity`、`is_cadastral_parcel:false`。
- `image.bounds`：原生对齐栅格范围；PNG经EPSG:3857重投影只用于显示。
- `provenance`：固定产品、官方源覆盖块、分类窗口SHA256、方法、无统计重采样、无几何简化和未提供本地精度区间。
- `report`、`artifacts`：口径说明及实际文件下载入口。

## 验收证据

真实官方COG选区 `[113.3,30.3,113.31,30.31]` 在本机执行3.016秒：120×120原生像元，14,400个有效分类像元，类别40像元7,638，22个四邻域图斑，**56.560702399704公顷／848.41053599556亩**。这是2021参考产品结果。

完整记录：`output/v14-cropland/real-task.json`；成果目录：`output/v14-cropland/real/cropland_d34a168ee10849/`；独立复核：`output/v14-cropland/independent-proof.json`。

把导出的GeoJSON独立栅格化，所有7,638个类别40像元精确回烧，差异像元为0；用EPSG:6933等积投影独立重算面积，与报告差 **0.000028627平方米**；逐斑CSV的像元数和面积之和与原生栅格一致。

12项引擎单元／生命周期测试覆盖孔洞、四邻域、零农田、缺测、面积独立复算、源覆盖缺口、复杂度拒绝、取消、恢复、重试、文件白名单与输入口径防绕过。

## 数据口径

[ESA WorldCover数据访问说明](https://esa-worldcover.org/en/data-access)、[产品DOI](https://doi.org/10.5281/zenodo.7254221)。保留ESA WorldCover CC BY 4.0归属。全球产品精度不能替代当地独立验证；Cropland以一年生作物耕作为主，不涵盖木本果园、温室等全部农业类型。
