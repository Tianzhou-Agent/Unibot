# 文本统计 AINA

这是一个最小的 managed AINA 示例，用于验证 Unibot 的 Project 1.0 开发包流程。

输入：

```json
{"text": "Hello Unibot\n你好"}
```

输出包括字符数、非空字符数、按空白分隔的单词数和行数。当前平台只负责校验和保存源码包；接入 managed 运行器后，Unibot 才能实际调用该入口。
