# 文档导航

本目录将现行说明、操作指南和阶段记录分开维护。先阅读[项目首页](../README.md)完成启动；当前软件行为以代码、接口返回和实际执行结果为准。

## 现行参考

| 文档 | 内容 |
| --- | --- |
| [产品与能力边界](reference/PRODUCT.md) | 一张图的客户流程、已实现能力、按需数据调用及业务边界 |
| [系统架构与目录职责](reference/ARCHITECTURE.md) | 前后端分层、任务生命周期、数据目录、版本与扩展位置 |
| [API参考](reference/API.md) | 当前正式接口、最小请求、查询执行状态及固定版本下载 |

## 操作与专题指南

| 文档 | 内容 |
| --- | --- |
| [天地图配置](guides/TIANDITU_SETUP.md) | 新环境的Key配置、验证、服务分工与许可边界 |
| [行业场景](guides/SCENE_MODULES.md) | 农业、林业、水利、交通场景配置、台账统计与地图操作 |
| [作物与时序](guides/AGRICULTURE_RESEARCH.md) | 实际观测、质量共同范围、样本门槛、变化候选及接口 |
| [仙桃农业规则与案例](guides/XIANTAO_AGRICULTURE_RULES.md) | 带日期的公开数据生产案例与业务核查规则；不作为其他地区的事实底数 |

## 历史设计与验收

[archive/](archive)保存V3—V18的设计、评审、浏览器记录和各阶段验收。原数据、日期、测试数量和代码路径属于记录时点；部分引用的`output/`证据仅存在于原本机，不随Git分发。

常用索引：[旧API与变更记录](archive/API.md)、[多源接入设计](archive/MULTISOURCE_ARCHITECTURE.md)、[多轮产品评审](archive/MULTI_ROUND_PRODUCT_REVIEW.md)、[地图阅读设计](archive/MAPPED_DESIGN_REVIEW.md)、[区域生产核查](archive/QA_XIANTAO_PRODUCTION.md)。这些文档用于追溯，不覆盖现行参考中的能力说明。

## 维护约定

- 改动功能或接口时，同时更新`reference/`及受影响的操作指南；根README保留启动、目录与文档入口。
- 新的阶段评审、生产核查和验收记录写入`archive/`，注明日期、实际范围和证据来源。
- 数据源登记与实现能力分开描述；来源、年份、质量和版本以每次真实结果为准。
- 文档不保存密钥、本机登录凭据或真实业务个人信息；本机配置与成果保存在Git忽略的数据目录中。
