# V12 天地图前端接入与浏览器验收

本轮使用独立 Playwright 会话 `v12-tdt-ui`，操作隔离预览 `http://127.0.0.1:8017/`。没有使用用户浏览器，没有取得或使用有效天地图 Key，没有把未授权的接口或服务标为已接通。`dist` 已冻结。

## 页面与操作

- 主导航保留地图、专题分析、任务与成果、数据。数据页改为“数据与地图服务”，顶部可直接管理天地图配置；地图“数据接入与覆盖”亦提供相同入口。
- 天地图影像与标准地图卡支持独立中文注记层，均使用同源代理 URL，不含 Key。未配置时卡片为“配置后使用”，点击进入配置，不会请求天地图瓦片或显示假底图。
- 配置弹窗列出影像/地图、地名地址、坐标地址、行政区参考范围四项真实状态。提供官方文档及已核实的控制台申请链接。使用允许服务端调用的单位应用 Key；如启用 IP 限制，需授权部署服务出口 IP。
- 保存配置、验证已有配置、清除本机配置均调用本地真实接口。Key 用 password 输入，不回显已有凭据；不写 localStorage 或地图 URL，提交失败会清空输入。
- 地图输入建议只请求 `remote=0`；按回车/搜索按钮才请求 `remote=1`。无 Key 时后端保留本地成果和 GeoNames 城镇参考检索。已有配置后才允许联网地名地址检索，多命中保留候选供选择。
- 地图底部“查地址”仅在用户点击时请求当前地图中心的一次逆地理查询；没有在移动/点击地图时自动消耗接口。无 Key 时引导配置，有返回时弹窗列出查询点、返回坐标、地址、来源与请求时间。
- 行政区查询在天地图卡内展开；未配置时明确禁用查询。后端返回候选与建议词，建议词可点击重查。只有实际返回 GeoJSON 的候选才允许“地图查看”和“下载范围”。地图参考边界为临时独立图层，可关闭、查看来源和下载，不会自动改变分析 AOI、固定仙桃历史界或将地名中心点/bbox当边界。
- 数据源目录的天地图三类服务单独展示服务适配、本机配置、连接检查和业务用途，避免以 STAC 目录、可计算像素或解译状态描述制图/定位服务。

## 实际浏览器结果

| 验收项 | 实际结果 | 证据 |
| --- | --- | --- |
| 地图未配置卡 → 配置 | 两张天地图卡均为配置后使用；点击可打开配置弹窗 | `v12-front-map-initial.txt`、`v12-front-key-dialog.txt` |
| 四项连接状态 | 无 Key 的四项均显示待配置，已有配置验证和清除按钮禁用 | `v12-front-key-final.txt` |
| 申请入口 | href 为 `https://cloudcenter.tianditu.gov.cn/center/development/myApp` | `v12-front-key-final.txt` |
| 非法 Key 校验 | 输入明确无效的非32位测试字符串，真实 POST 被400拒绝；未保存或发起授权成功假象 | `v12-front-invalid-key.txt` |
| 凭据 DOM 核查 | input 为 password、拒绝后为空；localStorage 没有 token/TDT/tianditu 键，页面 URL 无 Key | `v12-front-credential-dom.txt` |
| 数据页与手机布局 | 桌面1440×1000、手机390×844均可读；手机文档宽390=视口宽390，无横向溢出；配置弹窗可纵向滚动 | `v12-front-data-desktop.png`、`v12-front-data-mobile.png`、`v12-front-key-mobile.png`、`v12-front-mobile-measures.txt` |
| 行政区无 Key 路径 | 展开后可见名称框、禁用查询按钮及先配置提示；未出现伪造候选 | `v12-front-admin-unconfigured.txt` |
| 逆地理无 Key 路径 | 点击“查地址”打开待配置弹窗，无虚构地址 | `v12-front-reverse-unconfigured.txt` |
| 本地与显式搜索 | 武昌区输入建议请求remote=0；回车请求remote=1，两次HTTP200；无Key后端不请求天地图，返回GeoNames真实参考点且aoi_eligible=false | `v12-front-search-requests.txt` |
| 已存天地图、随后缺 Key 的重载 | 将隔离浏览器保存底图设为tdt_imagery，完整重新加载最新代码后，自动回退imagery，dialogOpen=false，无阻塞配置框 | `v12-front-key-removed-fallback.txt`、`v12-front-fallback-measures.txt` |
| 原业务入口 | 专题目录及既有8项业务能力入口正常呈现，地图与分析表单保持共用地图 | `v12-front-key-removed-fallback.txt` |

截图及文本均在 `output/playwright/` 下。浏览器会话已关闭，不留下额外预览窗口。

## 验收范围与限制

本轮没有有效 Key，因此天地图授权瓦片/中文注记、在线 POI/地址、逆地理实际地址与行政区真实在线返回仍待用户完成注册/认证并配置自有 Key 后验收。后端协议与错误路径的隔离测试不等于天地图生产授权验收；前端没有用 mock 返回伪装在线成功。

浏览器期间公开气象接口出现502，外部底图亦可能部分加载失败，界面按现有失败状态呈现；这不被记为天地图已连接。CLI 所带 `network` 命令不可用，改用 Playwright 实际响应事件记录搜索请求，最终证据为 `v12-front-search-requests.txt`，未依赖该失败命令的输出。
