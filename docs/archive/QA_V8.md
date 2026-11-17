# V8 多源数据接入验收

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

日期：2026-11-13。架构专家监督，后端与前端专家分工；主负责人独立执行真实HTTP、浏览器、正式服务与数据保留验证。方案见 [MULTISOURCE_ARCHITECTURE.md](MULTISOURCE_ARCHITECTURE.md)，讨论与源码签审见 [V8_REVIEW.md](V8_REVIEW.md)。

## 自动检查与最后语义复核

最终完整 `npm run check` 通过 **115项，12.897秒**，包括17项新增多源目录/计划测试。最终记录为 `output/playwright/v8-final-check.txt`。初轮113项12.782秒和中间113项12.860秒分别保留在 `v8-check.txt`、`v8-intermediate-check.txt`。

主负责人用隔离临时数据库复现并要求补齐两类选源语义：高标准农田等业务口径不能给出可执行的普通土地覆盖统计合同；明示月份不能被结构化period覆盖。新增回归已纳入最终115项，部署接口验证见末节，不将初轮113项当作这两类已经覆盖的证明。

## 真实目录与HTTP

独立 [49项真实HTTP验收记录](../../output/playwright/v8-http-acceptance.json) 全部通过，覆盖：

| 场景 | 实际观察 |
| --- | --- |
| 三类真实目录 | 同一仙桃bbox、2025-10-01至2025-11-30、每源limit2，S2、S1RTC、Landsat全部成功返回，共6个目录条目，保留原始ID与response SHA256 |
| 阶段与授权 | S1目录可搜但像素/分析false、账户需求true；Landsat只目录；天气像素字段不适用 |
| 实现与实测分开 | 检索更新verification；源metadata/catalog指纹不因一次网络状态变化而更改 |
| 公开发现边界 | 只允许固定集合；草案源、非法bbox/limit等被拒绝；不追随外部任意URL |
| 年份/地域 | 2026、今日/目前、武汉、湖北省/全湖北面积不被替换为仙桃2021 |
| 模态与目标 | SAR不能替代光学red/NIR，goal不能将明确的农田面积需求改成NDVI |
| 可执行合同 | 单期NDVI合同携带请求的观测期；只有未连接源时没有空source_ids可执行合同；两期变化缺完整窗口明确阻塞 |
| 不启动分析 | 目录与计划请求前后，landcover/production任务数量不变 |
| 真实使用证据 | 新成功crop/water/source/download问答关联计划，used来自绑定的真实成功LC任务；当前请求附历史任务仅列related，不算used |
| 快照与历史 | 问答快照、选源计划和发现快照可原样恢复，后续计划不改写旧答案 |
| 动态源 | 用户GeoJSON自动入目录，编码后的冒号ID详情可访问；导入日期不作为观测日期 |
| 接入草案 | 保存后检索/像素/分析均false；假token、Azure/AWS/Google签名query被拒绝，未进行网络探测 |

主负责人另有早期真实检索 [v8-live-catalog-search.json](../../output/playwright/v8-live-catalog-search.json)，三集合各2条、约3.39秒完成。前端真实检索再一次成功生成 `discovery_e28daee4a6ea4d`。以上是同一PC托管服务下的三种数据集合，不称作三个独立服务商，也不将多条目录命中称作空间完整覆盖。

首次冷库问答 `question_c94acb9d26ab44` 自动完成真实LC核算并附 `data_plan_ref`，两项used为WorldCover2021和仙桃2017参考市界；归档见 `v8-actual-answer.json`、`v8-actual-answer-plan.json`。这些早期开发记录保留原样，不向其中补写后来才添加的指纹或快照字段。

## 独立浏览器验收

桌面1440×1000，手机390×844。使用主负责人独立会话；前端专家自己的会话与根会话隔离。

| 场景 | 结果 |
| --- | --- |
| 一图问答主线 | 面积仍为约250.95万亩，来源说明打开图内数据依据弹窗；不会让客户选择供应商/波段/算法 |
| 实际使用与方案 | 弹窗显示2个真实used、2021数据/2017市界及完成任务；lineage_only标“已归档实际来源”，不误报“需补条件” |
| 专业目录 | 从弹窗显式进入专业工作台；源能力矩阵、覆盖、分辨率、许可、认证与最近实测分别显示 |
| 规划边界 | 高分/商业、无人机、DEM、OGC、业务和IoT的规划项均不冒充已连接；非栅格像素显示不适用 |
| 来源详情 | S1账户与仅目录边界可见；提供方许可有可点击入口；导入用户源详情通过实际编码URL访问 |
| 需求计划 | 输入2026全市面积，显示年份缺口和未关联真实成功任务，不显示已用数据或成功数值 |
| 真实目录检索 | 页面勾选3集合、limit2，实际请求返回6行，标“未读取像素”；目录元数据有独立详情与来源 |
| 手机 | 来源弹窗宽358px，2个used均可读、无横向溢出；目录显示完整卡片；恢复同一6条目录记录无横向溢出 |
| 返回地图 | 专业页返回客户一张图，原答案仍可恢复，来源与任务绑定不变 |

页面切换包含动画，最终截图在DOM与有限动画稳定后采集；历史检索的异步刷新以实际GET完成为准。最初过早截到切换中页面或尚未选中目录的画面已重新采集，不将其作为最终布局证据。

最终截图已逐张查看：

- [正式多源目录](../../output/playwright/v8-final-catalog.png)
- [正式一张图内来源依据](../../output/playwright/v8-final-map-evidence.png)
- [真实目录发现](../../output/playwright/v8-real-discovery.png)
- [不支持年度的缺口](../../output/playwright/v8-plan-gaps.png)
- [手机来源依据](../../output/playwright/v8-mobile-evidence.png)
- [手机目录](../../output/playwright/v8-mobile-catalog.png)
- [手机目录记录](../../output/playwright/v8-mobile-discovery.png)

浏览器核心链路未捕获JavaScript异常；外部Esri底图与公开目录服务仍可能超时，不据此宣称影像服务可用性保证。首次获取/显示底图与本地真实分类成果分开处理。

证据：`v8-browser-desktop.txt`、`v8-browser-mobile.txt`、`v8-mobile-catalog-check.txt`、`v8-mobile-discovery-check.txt`，均位于 `output/playwright/`。

## 正式服务与已有数据

8016使用原有 `data/workspace.sqlite3` 启动V8。升级前保存的V7问题 `question_b851824467cb4e` 在升级后整个记录完全一致，未复制测试库到正式数据。证据：`v8-upgrade-retention.json`。

正式三源查询 `discovery_8d28bf38cdef45` 全部成功；新问题 `question_4ee9ae1031dc43` 具有图内可查看的2项真实用源，目录实际条目数16（包含原工作空间用户数据，不按测试库数量凑齐）。证据：`v8-formal-discovery.json`、`v8-formal-browser.txt`。

## 已交付与后续范围

当前交付统一目录、固定公开发现、非执行式计划、任务使用证据、快照、草案及前端入口。已有WorldCover与有界S2计算保留；S1/Landsat本轮新增的是目录，未增加它们的像素读取或解译算法。国内高分、无人机、DEM、政务与物联连接需后续取得数据/授权、开发适配器并独立验收。平台目录参考STAC/OGC边界，不声称标准认证或通用融合引擎已经实现。

## 最终补充检查

最后两类修复在正式8016与隔离8017各执行4类真实API回归，另对两份真实LC上下文执行月度请求回归，共10次全部通过：高标准农田、官方耕地口径返回 `METRIC_UNSUPPORTED`，月份冲突返回 `PERIOD_CONFLICT`，年度分类产品收到月度统计请求返回 `ANNUAL_PRODUCT_ONLY`。均 `not_executed=true`、无可执行合同；月度上下文的 `used=[]`，旧年度任务仅列相关历史。记录：[v8-final-semantic-regression.json](../../output/playwright/v8-final-semantic-regression.json)。

正式页面再次验证业务指标阻塞、回到一图及刷新恢复同一新问题，见 `v8-final-ui-regression.txt`。新问题和来源计划在最后一次服务重启后仍存在，旧V7问题完整记录仍完全一致。最终问答与计划分别保存为 `v8-final-answer.json`、`v8-final-answer-plan.json`。

最终服务使用原数据库，测试库保持隔离；源代码包不含数据库、运行资产、凭据或虚拟环境。未把规划项变成连接成功、未执行的计划变成已分析结果，或将目录数量作为数据质量证明。
