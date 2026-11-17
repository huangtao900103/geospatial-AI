# V8 多源数据与选源依据体验

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

## 两轮专家讨论

第一轮确定客户仍保持一张图问答，不让客户自行选择数据/算法。专业工具承载来源目录、固定公开STAC发现和需求选源；实现阶段不等同于实际运行。

第二轮冻结“实际使用”与“方案推荐”分栏：used仅来自真实成功任务provenance；候选、推荐和执行契约均未执行新分析。天气、边界、用户GeoJSON的像素维度标为不适用；适配器实现状态与近期验证证据分开。许可声明与服务账户/费用条件分别展示。

## 专业多源视图

新增 `SourcesWorkspace`，替换专业“数据资源”默认页为“多源数据”。原用户GeoJSON导入、数据集管理和独立算法仍通过“我的数据集”入口保留。

三个页签：

1. 数据源目录：分类/搜索、四维能力矩阵、覆盖/时间/分辨率/数据许可/授权与费用/版本。
2. 真实目录检索：Sentinel-2、Sentinel-1 RTC、Landsat固定公开STAC来源，多源独立结果、失败或无命中明确保留；不声称读过像素。
3. 需求选源计划：问题自动匹配来源、推荐原因、处理步骤、阻塞/警告缺口；明确只规划、不执行新分析。

“登记接入草案”只保存registered_not_connected，不请求地址，不保存密钥，不显示已连通。

## 精确接口

- `GET /api/data-sources` 与 `/:id`。
- `POST /api/data-sources/search {source_ids,bbox,start,end,limit}`，limit前端1–6；`GET /api/data-searches` 与 `/:id`。
- `POST /api/data-plans {question,region?,bbox?,period?,goal?}`，`GET /api/data-plans` 与 `/:id`。
- `POST /api/data-sources/drafts {name,source_type,endpoint,protocol}`。

目录搜索使用专门45秒请求上限，与普通本地记录请求分开；不阻塞其他地图交互。结果仅目录元数据，无前端伪造影像。

## 客户一张图接线

答案来源折叠区读取 `answer.data_plan_ref.id` 或 `question.data_plan_id`，打开当前地图上的只读数据依据dialog。旧答案无此字段时正常保留原有来源信息。

readonly展示：真实used来源、成功任务引用、方案推荐、缺口。`provenance.purpose=lineage_only` 单独显示“已归档实际来源”或“关联历史不匹配”，不会把正常已完成来源回执误说成缺执行条件。

图层面板“数据接入与覆盖”提供只读目录概览；客户无需处理技术表单。进入专业完整方案/目录必须显式点链接，保持V7专业模式护栏。

## 状态与真实性

- Sentinel-1/Landsat：目录可检索，像素与对应算法未接；S1账户要求单独可见。
- WorldCover2021：固定年度产品直读与既有全市适配，不假称当前年度产品。
- Sentinel-2：有界像素读取及既有NDVI能力，不把该能力泛化为全市耕地分类器。
- 天气：模型资料，不作为现场实测；数据许可不等同于免费商业API。
- 参考界/用户GeoJSON：像素不适用，空间要素与许可按原来源。
- 政务、高分、UAV、IoT、DEM等未接资源：明确规划或待授权/待对接，无虚构成功连接。

近期验证优先使用后端verification；其次使用真实目录搜索记录；成功任务来源证据仅在实际used绑定存在时展示。未验证不写成功。

## 文件与验证

新增 `dist/sources-workspace.js`；app.js负责路由/数据集保留与只读入口，map-copilot.js负责data_plan_ref按钮，map-workspace.js负责覆盖入口。缓存tagV8。

ES module语法检查通过。真实API模板验证通过：S1目录only/账户要求、天气像素N/A、业务接口未连接、真实used与未执行selection分栏。

使用独立浏览器 `geoai-v8-front` 完成客户来源说明→本次数据依据→专业完整方案链路，没有操作root浏览器会话。浏览器仅观察到一条真实Esri瓦片网络超时，不是应用脚本错误；地图保留了来源与结果。最终桌面/手机由root独立验收。
