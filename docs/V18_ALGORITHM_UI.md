# V18 按需遥感算法与真实数据界面

本轮前端继续使用一张地图和既有专题/任务/数据导航，不新增顶层模块。新增算法在“更多分析 → 时序与影像处理”中选择。

## 客户入口

- **时段影像分析**：确认地图选区与时段，选择中位数合成、均值合成或线性趋势；指数为明确字段，影像来源、景数上限和景云量上限放在可选设置中，设置摘要始终可见。趋势至少要求场景上限为3，实际不同日期及有效观测次数仍由后台核验。NDRE不允许选择无红边波段的Landsat。
- **自适应阈值分区**：明确选择已成功归档的真实原生单期光谱成果，再选择阈值一侧与最小图斑像元数。沿用源成果的范围、实际日期和掩膜，不继承新地图框选。Otsu分区只表示光谱条件区域，不称为农田、水体、撂荒或灾损识别。

结果共用地图配准覆盖、实际日期/单位图例、原生像元点查、任务与成果、搜索恢复和任务绑定ZIP。结果主视图保留有效覆盖与方法边界；逐景统计、详细质量和原始来源按需查看。时间序列区域均值按各景有效像元计算，其有效范围可能不同，界面不把它直接解释为同一批地物的时间变化。

## 数据与方法来源边界

前端借鉴Google Earth Engine的逐像元集合归约和回归语义：在质量掩膜后进行mean/median，趋势以实际观测时间为自变量；未将本地实现称为调用Google云端，也不声称已取得其账户授权。GEE官方`ee.Reducer.histogram()`是直方图归约接口，本地Otsu另有明确实现和阈值报告，不能称为Google内置Otsu服务。

- [GEE：集合归约与中位数](https://developers.google.com/earth-engine/guides/ic_reducing)
- [GEE：逐像元集合统计](https://developers.google.com/earth-engine/guides/reducers_image_collection)
- [GEE：直方图归约](https://developers.google.com/earth-engine/apidocs/ee-reducer-histogram)
- [GEE：最小二乘线性回归](https://developers.google.com/earth-engine/guides/reducers_regression)

## 界面契约

模板ID为`temporal_analysis`和`adaptive_segmentation`。结果类型`raster_processing`；服务路径`/api/raster-processing/tasks/:id`，成果包路径`/api/results/raster_processing/:id/bundle.zip`。

地图上下文使用`rasterProcessingTasks`，源恢复使用`raster_processing:<id>`。图层元数据使用`raster_processing_task_id`、`raster_processing_result`、`processing_algorithm`。结果`image.url/bounds/legend`沿用现有配准覆盖契约，summary读取`algorithm_id/index_id/mean_value/valid_pixel_count/native_pixel_count/valid_ratio/actual_dates/scene_count/min_observations`；趋势读取`slope_unit=index/day`，合成读取`index_unit=index`。

可选JSON逐景记录为`result.time_series=[{date或datetime,mean_value,valid_ratio,...}]`，与导出的CSV保持同一实际观测来源。数值为0时显示0，无效/null显示未知。

## 不真实样例入口关闭

`map-workspace.js`在客户和专业模式均排除synthetic配置图层、合成分析与人工像元任务。`run:`旧合成工作流不再使用任意fallback读取和上图，通用图层构建也明确拒绝`source_type=synthetic`。数据与成果计数不包含旧演示runs或sample_pixels；真实公开影像、年度分类产品和明确用户导入数据保留。

后端存量删除、自动seed关闭及其他前端教学样本入口由主集成统一完成；界面防护不替代后端数据清理。

## 已完成的独立检查

两个修改的JS通过`node --check`。`scripts/verification/verify_v18_ui.mjs`使用隔离、明确标注的单元fixture，不创建任何平台记录，八项检查通过：集合表单及可选摘要、趋势最少景数、NDRE传感器阻断、结果单位/日期/像元数、0与未知、ZIP绑定、synthetic构建拒绝、计数排除合成内容。证据：`output/v18-ui/contract-checks.json`。

真实执行和浏览器整链验收由主集成在最终API就绪后完成，本文件不把上述隔离检查冒充真实影像计算。
