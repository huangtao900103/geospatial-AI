# V15 联合评审整体验收

日期：2026-11-14。正式入口 http://127.0.0.1:8016/ ，当前版本0.15.0。

## 结论

四类专业AI完成独立评审、交叉复核、代码修复、真实浏览器与HTTP验收。新增2024年度分类产品、可复核完整成果包和业务接入清单；修复任务派发取消、来源冻结重试、普通选区提问及范围确认问题。当前仍为本机工作台，不声称全省部门系统、当期分类模型或通用模型服务已经接通。

## 完整检查

最终343项检查通过，作者模块语法检查通过，日志`output/v15-review/final-check.txt`。检查覆盖原生分类/光谱、多源版本/缓存隔离、年份/日期/季节拒绝、区域口径、云与缺测、真实零值、HTTP导出、包完整性/限额/路径、派发取消竞态、重试来源锁定。公开IO目录仅对可重试网络/服务错误作最多两次同源GET；永久错误及重试失败仍明确失败，不替换源或年度。

## 正式服务真实用例

| 场景 | 产品年度 | 任务 | 图斑 | 作物/耕作地参考面积 |
| --- | --- | --- | --- | --- |
| xiantao_io_nonzero | 2024 | `cropland_9878968bb8b541` | 1 | 73.65000000公顷 |
| xiantao_io_zero | 2024 | `cropland_0573d2a18dbf4b` | 0 | 0.00000000公顷 |
| xiantao_worldcover | 2021 | `cropland_b7e90a44a7d247` | 22 | 56.56070240公顷 |
| wuhan_io | 2024 | `cropland_e206ce84a65c4f` | 1 | 0.01000000公顷 |

IO2024面积采用原生UTM投影格网，WC2021采用WGS84椭球像元面积。有效覆盖不是分类准确率，连通图斑不是确权宗地。两个产品的差额不能直接解释为耕地增减；武汉案例是明确小选区结果，不能称武汉全市面积。

全部四例的完整ZIP均实际从正式HTTP下载、检查CRC和每文件SHA256；GeoJSON投回原生格网回烧差异均为0。包内含中文业务摘要、原始需求/范围、产品与年度、面积方法、质量限制和实际GIS。包文件名含任务ID，避免不同任务都叫bundle.zip。

另验收：普通“这个区域有多少亩耕作地”走选区而非仙桃全市；同产品同范围专题复用原成功任务；错误年度、具体月份与季节、错误产品不生成替代答案；市县/选区不明先确认口径；追问下载仍绑定原任务；名称/任务ID搜索恢复同一成果。

## 真实页面

独立预览中完整执行：无年份原句默认2024、真实非零/零值、显式切换WC2021、class5像元核查、实际ZIP下载与离线复算、接入清单展开、完整新问题独立提交、口径确认按钮续办。证据`output/playwright/v15-artist-browser-validation.json`及`v15-artist-bundle-verification.json`。

正式8016页面再次通过地图搜索恢复2024结果，显示73.65公顷、1个连通图斑、2024来源及完整成果包。1440桌面与390手机截图已检查，手机文档宽度390=视口390，最后页面控制台0错误。正式截图`output/playwright/v15-formal-desktop.png`、`v15-formal-mobile.png`。

## 旧库与配置

升级前228条旧记录逐条SHA256比对：228条全部保留，缺失0、变化0。备份`output/diagnostics/v15-pre-upgrade.sqlite3`。天地图私有配置仍已连接、权限0600；数据库与配置不包含在源码/成果包中。正式服务PID及日志见`output/diagnostics/server-v15.json`。

## 证据索引

- `output/v15-review/formal-http-proof.json`、`formal-main-question.json`、`formal-http-check.txt`
- `output/v15-review/formal-retention-proof.json`、`final-check.txt`
- `output/v15-review/formal-xiantao-io2024-bundle.zip`及其他三例完整包
- `output/v15-io/independent-proof.json`：遥感引擎独立原生/UTM几何复核
- `output/v15-architect/real-io-business-bundle-proof.json`：业务摘要与包内文件核验
- `docs/V15_EXPERT_REVIEW.md`：联合结论；各专业文档保留初轮问题、二轮确认与剩余边界

跨地市预览最初遇到一次IO目录临时失败，保留了失败记录；同源查询恢复后完成两地验收。本次并不保证外部提供方的永久可用性。
