# V9 真实自动执行验收

> 历史记录：本文保留原阶段的设计、实现和验收口径。路径、数量与能力可能已变化；当前说明见[文档导航](../README.md)、[API参考](../reference/API.md)和[架构参考](../reference/ARCHITECTURE.md)。

日期：2026-11-13。主负责人实施问答桥接与原生栅格内核，架构专家监督及独立复算，后端专家实现统一执行器并测试，前端专家完成地图交互与响应布局。模型服务按用户选择随后配置；本轮只验收已经实现的真实工具执行。

## 已完成的主链路

客户问题 → 冻结范围/时相/口径 → 有边界的规则规划 → 实际调用白名单工具 → 等待真实子任务 → 检查质量与产物 → 定量答案与GIS上图 → 来源/下载/常用问法。

缺范围时在图上两点框选并命名，自动继续原问题；缺日期一次补充，客户不必选择数据供应商、波段或算法。`execution_ref`跟踪真实处理节点，不将计划或目录查询冒充解译成功。

## 全量自动检查

`npm run check`通过 **158项，18.343秒**，同时检查全部自编前端模块语法。记录：[v9-final-check.txt](../../output/playwright/v9-final-check.txt)。

新增检查包含23项执行服务、11项原生数学/导出、8项问答执行桥接及1项目录年度证据回归。旧助手组件测试显式保留兼容组件模式；新增桥接测试覆盖默认实际ExecutionServices生命周期。单元fixture与下述真实网络/真实栅格证据分别记录。

重点覆盖：未完成工具前不回答、缺范围/时相澄清、补充文字日期保留、幂等/冲突、缓存版本与文件齐全、拒绝旧代表采样、同任务来源追问、新问题引用隔离、取消/重试、年份/范围/口径守卫、质量掩膜、严格阈值、投影面积与GeoTIFF导出。

## 真实网络与计算证据

| 实测链路 | 观察 |
| --- | --- |
| 空库直接执行 | `execution_3dac99e5159b4a`实际创建`production_b389b813ca544f`，自动检索S2、XML定标、读取红/NIR/SCL、配准、掩膜、计算和归档 |
| 客户API补范围 | `question_be4c14af83014b`缺bbox；补充后`question_d840d58f7d5849`自动成功且复用同原生子任务；相同request_id返回原qid |
| 全市冷库问答 | `question_5c2c2d4441b34c`自动创建`landcover_312fdc6767484b`，真实全原生分类统计返回167296.884667公顷；仍为2021分类×2017参考界 |
| 来源/下载 | `question_24c797f3b01441`锁定原生子任务，`map_effect=none`，没有重新核算 |
| 真目录执行 | `execution_700c5dd3acb74e`调用public_catalog_search，S2实际返回3条；明确只目录，不读像素或解译 |
| 真天气执行 | `execution_0cf8d87f459c4a`调用weather_forecast，实际返回有单位的模型气温/降水/风速；不替代田间实测或灾损 |
| 不支持业务 | 2026高标准农田面积无答案，不用历史Cropland结果代答 |

机器证据：[首次原生执行](../../output/playwright/v9-native-first.json)、[客户真实HTTP](../../output/playwright/v9-assistant-real-http.json)、[目录/天气真实工具](../../output/playwright/v9-multi-tool-real-http.json)。

## 原生栅格独立复算

选区`[113.30,30.30,113.31,30.31]`。实际观测为2025-10-28与2025-11-27，不是月平均合成。末期B04的EPSG32649参考格网为114×100，前期/NIR/SCL最近邻对齐；AOI按WGS84矩形的像元中心掩膜，统计不用预览图。

架构专家从GeoTIFF独立逆投影重建AOI，掩膜差异0；使用独立求和复算全部均值，并按投影四角面积复算。记录：[v9-native-independent-check.json](../../output/playwright/v9-native-independent-check.json)。

| 指标 | 结果 |
| --- | ---: |
| 原生参考格网AOI像元 | 10,665 |
| 共同有效像元 | 10,665 |
| 末期平均NDVI | 0.3090271766041493 |
| 前期平均NDVI | 0.2687531638284032 |
| 平均变化（后减前） | +0.04027401279440639 |
| 严格下降>0.1像元 | 1,472 |
| 阈值命中参考格网投影面积 | 14.72公顷 |

所有复算均值、计数和面积与JSON差异0。区域均值上升与局部像元下降同时成立。面积为光谱条件格网投影面积，不是农田分类面积、减产或灾损。共同有效率100%不是分类准确率。GeoTIFF含两期NDVI、差值、共同有效与AOI掩膜；不含原始红/NIR/SCL，独立复算不冒称再次验证了上游定标。

## 真实浏览器与界面

前端专家使用独立浏览器实际框选另一小窗、命名并自动继续，产生新任务`production_46c83229f1624c`：4,479像元，594个严格下降>0.1，对应5.94公顷。来源/下载保持原任务与地图视角；新全市问题不继承原生结果引用。原生常用问法保存业务context，回放新问题真实复用同任务。完整记录见 [V9_UI.md](V9_UI.md)。

主负责人另用独立浏览器验证全市直接问答、原生历史恢复、真实配准PNG/图例、实际日期、阈值面积主卡及桌面1440×1000/手机390×844均无横向溢出。最终截图已逐张查看：

- [正式桌面一张图](../../output/playwright/v9-final-map-desktop.png)
- [正式手机答案](../../output/playwright/v9-final-map-mobile.png)

Esri真实外部服务在部分请求出现timeout，首次截图曾空白；前端已增加主画面加载状态、重试和明确标注的NASA归档备用入口。前端通过受控中止自己浏览器请求验证失败提示，再移除拦截并真实重试恢复。最终正式截图有真实底图瓦片及分析overlay，不宣称在线提供方始终可用。未观察应用JavaScript异常。

## 正式服务与数据保留

正式入口 **http://127.0.0.1:8016/**，使用原`data/workspace.sqlite3`。升级前对原数据库做SQLite一致性备份，升级后16条历史问题完整记录SHA全部不变，未用测试库覆盖。记录：[v9-upgrade-retention.json](../../output/playwright/v9-upgrade-retention.json)。

正式问答`question_10b4d2a148c64c` → `execution_0f87adef45cc40` → **新建**`production_6c0079791b6646`，重新调用真实影像服务并计算，非复用测试任务。仍得到10,665/1,472/14.72公顷。三类产物HTTP200；从正式GeoTIFF再次独立计数/均值/面积，与正式JSON精确一致，所有栅格值也与独立预览运行一致。

记录：[正式执行及任务](../../output/playwright/v9-formal-native.json)、[正式独立复算](../../output/playwright/v9-formal-independent-check.json)、[正式浏览器](../../output/playwright/v9-formal-browser.txt)、[正式响应布局](../../output/playwright/v9-formal-responsive.txt)。

最终部署API另外验证无年份两期问题、原文11月与context10月冲突、2026高标准农田需求，分别needs_data/needs_data/unsupported，均无答案且生产任务数未增加：[v9-formal-scope-guards.json](../../output/playwright/v9-formal-scope-guards.json)。

## 本版边界

本版为可运行的本地真实分析系统，未配置通用模型、多租户认证或公网能力市场。S2原生执行限≤0.05°窗口和1,048,576参考格，每个日期窗口选一景；不提供全市现状耕地分类、多景拼接或任意业务解译。S1 RTC/Landsat是目录接入，其他政务/商业/物联网等保留待授权与待适配状态。能力发布/复用以当前本机工作空间为范围。
