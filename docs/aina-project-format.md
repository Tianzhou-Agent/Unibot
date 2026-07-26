# AINA 项目格式 1.0

AINA 项目是一个可复现的 ZIP 包。Unibot 只从包根目录读取一个 Manifest，源码和依赖文件均保留在包内，供后续托管运行时部署。AINA 不是独立 Agent；部署后仍只作为能力由 Unibot 选择和调用。

## 目录结构

```text
example.aina.zip
├── aina.yaml                 # 也可使用 aina.yml 或 aina.json，但只能存在一个
├── README.md
├── requirements.txt         # Node 项目使用 package.json
└── src/
    └── main.py              # Node 项目默认是 src/index.mjs
```

Manifest 继续使用 AINA Protocol 1.0。源码项目增加 `managed` 运行声明：

```yaml
protocol_version: "1.0"
aina:
  id: com.example.notes
  name: Notes AINA
  version: 0.1.0
  description: Process notes for the user.
  publisher:
    id: local
    name: Local developer
runtime:
  type: managed
  language: python
  entrypoint: src/main.py:invoke
  dependency_file: requirements.txt
capabilities:
  skills: []
  tools: []
  ui: []
  events: []
permissions: []
authentication:
  type: none
```

`entrypoint` 必须是包内相对 POSIX 路径和处理函数名。处理函数接收 AINA Protocol 1.0 request 对象，返回 Protocol 1.0 response 对象。当前阶段支持打包、校验和保存 `managed` 项目；项目必须在后续托管运行时完成部署，才能安装并被 Unibot 调用。

## API

- `POST /aina-projects/scaffold`：根据 AINA ID、名称、描述和语言生成确定性的项目 ZIP。
- `POST /aina-projects/validate`：上传项目 ZIP，校验 Manifest、JSON Schema、入口文件、依赖文件和归档安全性，并返回内容摘要和 SHA-256。
- `POST /aina-projects`：导入并保存通过校验的 managed 项目，同一用户下相同 AINA ID 和版本不可被不同内容覆盖。
- `GET /aina-projects`：列出当前用户导入的项目。
- `GET /aina-projects/{project_id}/archive`：校验 SHA-256 后下载原始项目包。
- `DELETE /aina-projects/{project_id}`：删除项目记录及其归档。

项目接口从受信任的请求上下文读取用户和租户身份，不接受客户端通过 query 参数指定 actor；本地未接入认证中间件时使用单用户身份 `anonymous/default`。

验证会拒绝绝对路径、父目录穿越、符号链接、加密文件、重复文件、超限文件和 ZIP 解压膨胀。

导入采用可恢复的两阶段保存：平台先创建 `importing` 元数据预留，再以不可覆盖的方式保存归档，最后将记录更新为 `validated`。中断后，相同内容可以继续完成导入；相同 AINA ID 和版本的不同内容仍返回冲突。`importing` 记录会显示在项目列表中，方便用户识别并删除未完成的导入，但不能下载。删除时先删除归档，成功后才删除元数据；归档删除失败会保留项目记录以便重试。

`validated` 只表示包结构和已保存归档通过完整性校验；项目不会因此自动获得权限、注册为 AINA 或进入 Unibot 的可调用列表。
