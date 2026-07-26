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

`entrypoint` 必须是包内相对 POSIX 路径和处理函数名。处理函数接收 AINA Protocol 1.0 request 对象，返回 Protocol 1.0 response 对象。当前阶段只校验和打包 `managed` 项目；项目必须在后续托管运行时完成部署，才能安装并被 Unibot 调用。

## API

- `POST /aina-projects/scaffold`：根据 AINA ID、名称、描述和语言生成确定性的项目 ZIP。
- `POST /aina-projects/validate`：上传项目 ZIP，校验 Manifest、JSON Schema、入口文件、依赖文件和归档安全性，并返回内容摘要和 SHA-256。

验证会拒绝绝对路径、父目录穿越、符号链接、加密文件、重复文件、超限文件和 ZIP 解压膨胀。项目包不会因此自动获得权限，也不会直接进入可调用列表。
