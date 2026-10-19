export default {
  title: "让画布连接 Unibot",
  breadcrumb: "导航路径",
  plugins: "插件",
  addApp: "添加应用",
  intro:
    "画布是 Unibot 显示在对话旁的网页。单独使用时它只负责显示。要与 Unibot 协作，页面需要加载一个小脚本：@unibot/aina-ui。添加应用页面预览中的“已连接”就是指这个脚本已加载。",
  need: {
    title: "需要它吗？",
    withoutTitle: "不需要它也能做到",
    without: {
      show: "在对话旁显示页面",
      ownData: "页面通过自己的后端读取和保存数据",
      mcp: "智能体通过应用的 MCP 工具修改这些数据（页面能自行获取更新时）",
    },
    needsTitle: "需要它才能做到",
    needs: {
      context: "读取用户的语言、Unibot 的主题和应用设置",
      callTool: "在页面中调用应用的工具，并在需要时由 Unibot 征求用户确认",
      refresh: "智能体在对话中修改数据后自动刷新",
      canvasTools: "画布工具：智能体在页面上执行的操作，例如高亮图形",
      modelContext: "告诉智能体屏幕上显示的内容",
      prompt: "通过页面上的按钮向对话发送消息",
    },
    excalidraw:
      "例如：Excalidraw 白板不需要它也能使用。它的 MCP 服务器通过白板自身的服务绘图，页面会实时更新。加载脚本后，智能体还能看到你选中的内容，或替你高亮图形。",
  },
  packaged: {
    title: "应用包中的画布",
    body: "把 aina-ui.js（如需 Unibot 的样式，再加上 aina-ui.css）复制到画布的 index.html 旁边并导入。通过“下载入门项目”或 aina init 创建的项目已包含这两个文件。",
    note: "导入脚本后会立即连接。应用包中的画布无法访问网络，所有数据都通过 host.callTool 获取。",
  },
  webApp: {
    title: "网页应用画布（canvas.url）",
    body: "在应用启动时加载一次脚本。你的 Unibot 提供该脚本，开发时可以直接从那里导入：",
    vendor: "正式发布时，请把 aina-ui.js 复制到应用的静态文件中（或 npm install @unibot/aina-ui），避免依赖某个 Unibot 的地址。",
    identity: "如需在自己的后端识别用户，调用 host.getIdentityToken()，并用 Unibot 的 JWKS 校验令牌。",
  },
  agent: {
    title: "让智能体操作画布",
    manifest: "在 aina.json 中声明画布工具。智能体会像看到 MCP 工具一样看到它们，但由打开的画布执行：",
    code: "在页面中实现这些工具，并用一段简短的屏幕摘要让智能体了解当前内容：",
  },
  check: {
    title: "检查是否连接",
    step1: "打开 插件 → 添加，填写你的画布（网页地址；或上传静态包后点击“检查应用”）。",
    step2: "等待预览加载完成。",
    step3: "预览下方显示“已连接 Unibot”，表示脚本已加载，页面能与 Unibot 通信。",
  },
};
