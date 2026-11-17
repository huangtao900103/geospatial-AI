# Vue前端

技术栈：Vue3单文件组件（Composition API / `<script setup lang="ts">`）、TypeScript严格检查、Pinia、Vite；Leaflet保留本地资源及原许可。依赖使用根目录`package-lock.json`锁定。

```sh
npm ci
npm start                 # 类型检查、构建并启动Python本机服务8016
npm run build             # 生成dist，不启动服务
npm run dev:server        # 开发终端一：Python API
npm run dev               # 开发终端二：Vite 5173，代理/api到8016
npm run typecheck
npm run test:frontend
npm run check:ui
npx playwright install chromium
npm run test:e2e
```

| 位置 | 职责 |
| --- | --- |
| `src/App.vue`、`main.ts` | Vue应用、导航、错误与加载状态 |
| `src/components/` | 上传、弹窗、我的数据、来源目录、任务历史、矢量分析、集成及专业模块宿主 |
| `src/stores/workspace.ts` | Pinia共享数据、任务、选区与交互状态；兼容原浏览器存储键 |
| `src/services/`、`src/types/` | 类型化API、运行时响应校验、错误/取消/超时、路由解析和共享类型 |
| `src/adapters/runtime.ts` | 专业模块与Vue之间的类型边界、地图结果和生命周期协调 |
| `src/workspaces/` | 保留的Leaflet地图、遥感表单、来源/观测/时序等已验证业务模块 |
| `src/styles/` | 原视觉样式与Vue组件补充样式 |
| `src/tests/` | API边界、选区持久化、失败处理和路由兼容测试 |
| `e2e/`、`playwright.config.ts` | 临时工作空间的Chromium回归；端口8096独占 |
| `public/vendor/` | Leaflet本地资源及许可 |

迁移边界：Vue已接管应用入口与通用流程，旧的全局`app.js`调度代码已移除。复杂科学面板目前仍使用原JavaScript渲染模块，通过组件宿主接入；没有把这些模块伪称为已全部完成Vue模板和TypeScript重写。动态类型集中在适配器中，新的共享逻辑和界面应写在组件、store或service中。

生命周期：离开地图时停止地图尺寸观测和界面轮询；返回时恢复原实例和图层。应用销毁时清理事件、定时器与Leaflet对象。上传、选区和实际成果仍由后端校验，框架不会改变分析口径或成果版本。

安全与数据：API请求仍为同源`/api`；不在前端保存服务密钥。`dist/`为可重建构建文件，不进入Git。`npm run check`会构建前端并运行后端与前端隔离检查；它不生产真实地市数据。

资源刷新：每项资源独立维护加载、错误和上次成功时间。失败与格式异常保留旧值，合法空列表允许清空；AbortController与请求代次防止迟到结果覆盖上传或更新。`TaskHistory.vue`在地图侧栏独立挂载，筛选状态在切换页面时保留，离开后暂停轮询。

浏览器回归：`npm run test:e2e`先构建，再启动`serve_browser_tests.py`创建临时数据库和明确标注的测试格网。GeoJSON、栅格和成果包走真实本机接口；异常响应仅在测试浏览器拦截。测试结束清理临时工作空间，不使用生产数据库或服务配置。CI执行相同命令；失败时保留截图和trace。
