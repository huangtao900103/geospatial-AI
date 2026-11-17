# 场景应用与地图易用性验收

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

本轮由架构与政府业务、地图交互、遥感科学三个专项评审和根代理集成验证。实际研读[mapped.earth](https://mapped.earth/)、[年度遥感颜色](https://mapped.earth/average-color)、[河流流域](https://mapped.earth/basins)，借鉴地图对象查询、有限操作入口、数据含义与来源说明，不复制素材或声称已接入其三维和动态数据。

## 实际执行

22项场景均经过隔离HTTP运行、保存及来源检查。算法为scene-ledger-1.1，绑定保存在scene_binding，结果保存在analysis。scope与run共用筛选规则，错误ID、不适用几何、空筛选不会冒充完成。

- 高标准农田浏览器验证：上传2个隔离工程面，状态分组、唯一项目数、投资合计300万元、几何面积约2.1351公顷；运行、上图、刷新恢复成功。
- 农机浏览器验证：上传3条隔离轨迹，2个设备，长度约220.36米；地图3条线路，页面真实下载ZIP成功。
- 上述台账是独立QA库的计算验收输入，不是真实政府工程或运营数据，未进入正式库。
- 正式8016使用原库保留的真实Natural Earth参考水系，analysis_adce2e33017d4b：2个原始河流线要素，累计330385.162米。这不是仙桃市权威河湖长度或监测结论。成果包9个SHA条目核验，原数据未改。证据：output/scene-formal-rivers-proof.json和scene-formal-rivers-bundle.zip。

## 场景到真实遥感

从隔离工程台账筛选“在建”1条，影像表单范围实际为[113.4,30.3,113.401,30.301]，排除另一已完工面；表单注明外包矩形而非地块掩膜。通过页面运行2025年10月、11月两期NDVI，调用实际公开Sentinel-2 COG，经XML定标、SCL检查和配准后成功。

question_6314a1c0c63949对应production_1b6a912b7c3b46：99个共同有效像元，平均NDVI变化0.07225754714，条件命中0为真实零而非缺测；成果包7个SHA条目核验。几何只是流程选区，影像和像元计算真实发生，不将结果认定为真实工程或农田变化。证据：output/scene-real-remote-proof.json和scene-real-remote-bundle.zip。

## 联合评审修复

- 输入单位失焦重绘吞掉首次运行点击：简单字段不再重绘，首次点击POST201复测。
- 有场景成果仍显示空态、刷新漏掉场景：合并空态、计数和刷新。
- 场景接口暂时失败遮住全部工具：改成局部错误，保留通用工具。
- 同毫秒随机ID顺序恢复旧结果：按绑定latest_analysis_id恢复。
- 非法dataset_id进入SQLite报500：严格验证并返回422。
- 筛选后遥感仍用全台账范围：后台统一筛选并生成scope。
- 计算字段覆盖原属性：原属性完整保留，新字段自动避碰并记录computed_fields。
- 成果包缺输入与专业口径：增加source.geojson，核验版本、元数据及实际规范化内容哈希，缓存包不能绕过核查。说明纳入面积未去重、长度非作业面积等口径。

## 检查与视觉

node scripts/check.cjs：440项检查通过，含14项场景和3项输入包复核。另有verify_scene_ui.mjs的8项前端回归通过，覆盖任务空态、刷新、局部失败、恢复、北京时间、有效工具映射和分类图例。日志：output/scene-workflows-final-check.txt、scene-ui-regressions.json。

桌面1440×1000、手机390×844实测：非聊天页提问区折叠，可选参数折叠，结果优先；手机无横向溢出。地图按实际面板宽度留白，冻结feature_groups确保颜色和图例一致，前六类独立色、其余灰色，不表示风险评级。正式截图等待网络完成后60块瓦片已加载、0块待加载。独立QA公开Esri底图曾有网络挂起及缺块，未冒称完整底图或零网络错误；正式天地图配置未改。

截图：output/playwright/scene-project-result.png、scene-machinery-result.png（隔离QA），scene-categorical-legend.png、scene-formal-desktop.png、scene-formal-mobile.png。

## 保留边界

真实业务资料仍由授权方上传，不自动伪造政府资料。北斗实时定位、船舶视频识别、权属和交易系统尚未连接；轨迹长度不等于作业面积，统计不等于执法结论。底图依赖外部网络。勾选配置限当前浏览器，数据绑定和结果保存在后端。当前部署限本机，不宣称已具备多用户生产运维体系。
