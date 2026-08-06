# 飞牛 fnOS 部署

该目录使用 GitHub Container Registry 中预构建的 Unibot 镜像，飞牛无需编译源码。默认地址针对 `192.168.1.8`：

- Web：<http://192.168.1.8:8080>
- API 文档：<http://192.168.1.8:8000/docs>

## SSH 一键启动

在飞牛上克隆仓库后运行：

```sh
sh deploy/fnos/start.sh
```

首次执行会在 `deploy/fnos/.env` 生成随机 MySQL 密码和认证密钥；再次执行会保留原配置和全部数据。

## Docker 图形界面

1. 在文件管理中创建 Unibot 项目目录，并放入本目录的 `docker-compose.yml` 与由 `.env.example` 复制得到的 `.env`；
2. 修改 `.env` 中所有 `CHANGE_ME`，至少填写模型 API Key；
3. 打开 **Docker → Compose → 新增项目**；
4. 选择“上传 docker-compose.yml”，项目路径选择上述目录；
5. 勾选“创建项目后立即启动”。

服务数据保存在 Docker 命名卷中。删除 Compose 项目时不要选择删除数据卷，否则会清空数据库、文档和用户代码工作区。

当前飞牛方案使用 CPU 运行 YOLO，并使用 `local` 代码沙箱。它适合受信任的家庭局域网用户，不应直接暴露到公网，也不适合让不可信用户执行脚本。
