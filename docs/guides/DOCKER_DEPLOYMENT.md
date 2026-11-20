# Docker部署

本仓库提供`Dockerfile`、`compose.yaml`和容器启动、健康检查脚本。前端在Node构建阶段生成静态文件，运行镜像包含Python服务、空间分析依赖和构建结果。部署端无需另装Node或Python。

当前仍是单工作空间应用。Docker封装与组织认证、多租户或分布式计算属于不同建设内容。

## 首次启动

在安装了Docker Engine/Desktop及Compose插件的设备上，将**包含本轮Docker文件及农业业务代码的完整源码**放入项目目录，执行：

```sh
docker compose config --quiet
docker compose up -d --build --wait
```

打开`http://127.0.0.1:8020/`。默认使用8020，便于与原本机8016服务区分。业务入口为`http://127.0.0.1:8020/#/map/operations`。

```sh
docker compose ps
docker compose logs --tail 100 -f workspace
docker compose stop
docker compose start
```

镜像构建需要从镜像仓库、npm及Python包仓库获取依赖；运行时查询外部地图和卫星数据也需要相应网络。Docker配置不会自动取得业务接口授权。

## 配置地址和端口

默认端口只发布到部署设备的`127.0.0.1`。如需变更端口：

```sh
cp deploy/docker/environment.example .env
```

编辑根目录`.env`中的`GEOAI_HTTP_PORT`后，运行`docker compose up -d`。

可信内网试用时，填写实际服务器地址，例如：

```dotenv
GEOAI_PUBLISH_IP=0.0.0.0
GEOAI_HTTP_PORT=8020
GEOAI_ALLOWED_HOSTS=192.168.1.50,agri.internal.example
```

运行`docker compose up -d`后，在另一台设备打开`http://192.168.1.50:8020/`。不要把示例地址原样当作本机地址。

`GEOAI_ALLOWED_HOSTS`填写精确IP或域名，不含协议、端口、路径和通配符。本机`127.0.0.1`和`localhost`始终允许，供健康检查使用；反向代理使用的真实Host也须加入白名单。服务继续校验修改请求的同源关系，不信任`X-Forwarded-Host`来绕过校验。

主机白名单不是用户登录或访问权限。当前内网模式仍为同一工作空间，正式多部门部署需增加组织身份、分级权限、HTTPS和相应运行管理，不应把当前配置描述为已经完成公网生产部署。

## 数据和服务配置

容器内数据库位于`/app/data/workspace.sqlite3`，成果、业务台账、农业研究和`integrations/`配置均随该目录保存。Compose通过命名数据卷`workspace-data`持久化整个目录；重建镜像或普通的`docker compose down`不会删除命名卷。

**不要对需要保留的部署执行`docker compose down --volumes`或删除数据卷。** CI只对一次性测试工作空间使用该命令。

`.dockerignore`采用构建输入白名单；本机`data/`、`output/`、`.env`、虚拟环境、依赖目录和Git资料不放入镜像。新容器因此从空工作空间启动。新设备的天地图服务配置可在页面“数据→地图服务设置”中完成，保存在数据卷中。

运行镜像不包含本机Codex CLI或本机账号登录状态。容器不会自动继承当前电脑的大模型能力；结构化专题、台账、GIS和已适配算法可独立使用，开放式模型能力须另行部署受支持的模型接入。复制`integrations/model.json`并不等于容器里模型已经可用。

## 迁移现有工作空间

先停止原工作空间写入，保留完整的数据库、成果文件和来源清单，再进行迁移。不要同时让本机服务和容器写同一个SQLite工作空间。

将待迁移资料放在独立目录`migration-data/`，它应直接包含`workspace.sqlite3`及`agriculture-atlas/`、`production/`等实际存在的成果子目录。私有配置按授权单独决定是否迁移。

在一个**新建且为空的数据卷**中导入：

```sh
docker compose build
docker compose run --rm --no-deps --user 0 \
  -v "$PWD/migration-data:/import:ro" --entrypoint sh workspace \
  -c 'cp -a /import/. /app/data/ && chown -R 10001:10001 /app/data'
docker compose up -d --wait
```

上述命令为macOS/Linux shell写法；Windows可在WSL中使用，或将挂载路径换成PowerShell对应写法。导入不是自动合并操作；已有目标工作空间应先备份并明确恢复方案。

容器运行用户为UID/GID `10001:10001`。恢复后的数据库、成果目录及服务配置必须允许该用户写入。容器根文件系统只读，持久写入集中在数据卷，临时文件位于`/tmp`。

旧结果的地图、像元点查和下载应逐项核验；历史任务重试可能引用原设备上的源文件或缓存绝对路径，这些输入需迁移或重新选源。不能只复制SQLite而忽略原生栅格和其他成果文件，也不应改写旧版本指纹来掩盖缺失输入。

## 备份和更新

暂停业务写入后备份整个工作空间卷，例如：

```sh
mkdir -p backups
docker compose stop
docker compose run --rm --no-deps --user 0 \
  -v "$PWD/backups:/backup" --entrypoint sh workspace \
  -c 'tar -C /app/data -czf /backup/workspace-backup.tgz .'
docker compose start
```

确认备份文件后，更新代码并重建：

```sh
docker compose up -d --build --wait
```

首次构建针对当前设备CPU架构。跨架构分发镜像需要在目标架构构建并验收；本配置不等同于已经完成x86和ARM全平台验证。

## 验证范围

- 应用测试覆盖默认本机限制、容器所需监听地址、额外Host白名单、同源校验、部署参数、临时数据库及SIGTERM退出。
- GitHub Actions增加独立Docker作业：检查Compose、构建镜像、启动容器、加载栅格依赖、验证空间入库/相交和重启后的数据持久化。只有实际CI运行成功，才能据此宣布对应镜像构建和容器测试通过。
- 当前编写配置的设备未安装Docker引擎，不能将本地应用测试或YAML校验称为容器实测；镜像构建、容器健康状态、目标架构和挂载行为仍需有Docker的环境完成验收。

配置语义参考Docker官方的[多阶段构建](https://docs.docker.com/build/building/multi-stage/)、[Compose应用模型](https://docs.docker.com/compose/intro/compose-application-model/)和[命名数据卷](https://docs.docker.com/reference/compose-file/volumes/)。
