<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/cover-dark.svg">
  <img src="assets/cover-light.svg" alt="kernelvale — 追到原因，做成工具，验证结果" width="100%">
</picture>

### 把问题追到根因，把工具做成可用的产品。

你好，我是 **kernelvale**。这里记录我在 Agent 系统、开发工具和日常工作流里的实践：追踪消息与状态如何流转，也把学习、阅读和信息整理做成能用起来的小工具。

[最近的系统修复](#最近的系统修复) · [项目](#项目) · [全部仓库](https://github.com/kernelvale?tab=repositories)

## 最近的系统修复

**[DeepSeek Harness · 三项后端修复](https://github.com/kernelvale/deepseek-harness-fixes)**

围绕父子任务隔离、模型选择和消息唤醒，完成复现、链路排查与修复，把代码和验证结果放进同一个公开仓库。

| 问题 | 修复目标 |
| --- | --- |
| 子代理重放父级排队任务 | 让继承的对话和本次委派各自保持正确的任务归属 |
| 继承历史覆盖新模型选择 | 让实际请求遵循本次捕获的服务商、模型与推理等级 |
| 正常结束窗口丢失唤醒 | 让交接期间接受的新消息进入后续处理 |

**1,044 项相关回归测试通过 · 16 个 CLI / SDK 回放通过**  
[复现与修复报告](https://github.com/kernelvale/deepseek-harness-fixes/blob/main/FIXES.zh.md) · [验证记录](https://github.com/kernelvale/deepseek-harness-fixes/blob/main/validation.json) · [上游 bug 反馈](https://github.com/deepseek-ai/deepseek-harness/discussions/12727)

## 项目

<table>
  <tr>
    <td width="50%" valign="top">
      <h3><a href="https://github.com/kernelvale/feishu-assistant">飞书个人助理</a></h3>
      <p>把论文精读、arXiv 推送和求职准备接进飞书，让日常信息流更有条理。</p>
      <p><sub>Python · 自动化工作流</sub></p>
      <p><a href="https://github.com/kernelvale/feishu-assistant">查看项目 →</a></p>
    </td>
    <td width="50%" valign="top">
      <h3><a href="https://github.com/kernelvale/leetcode-hot100">LeetCode Hot 100</a></h3>
      <p>本地刷题工具，支持双模式判题、错题复习和上下文问答，练习与复盘放在一起。</p>
      <p><sub>JavaScript · Python · C++</sub></p>
      <p><a href="https://github.com/kernelvale/leetcode-hot100">查看项目 →</a></p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3><a href="https://github.com/kernelvale/english-reader">英语阅读器</a></h3>
      <p>在浏览器里阅读、听读、查词与对照翻译，减少来回切换工具的打断。</p>
      <p><sub>JavaScript · 浏览器应用</sub></p>
      <p><a href="https://github.com/kernelvale/english-reader">查看项目 →</a> &nbsp; · &nbsp; <a href="https://kernelvale.github.io/english-reader/">在线体验 ↗</a></p>
    </td>
    <td width="50%" valign="top">
      <h3><a href="https://github.com/kernelvale/smart-customer-service-mvp">智能客服 MVP</a></h3>
      <p>从问题检索到会话管理，串起聊天端、管理端和未解决问题的知识补全流程。</p>
      <p><sub>Python · SQLite</sub></p>
      <p><a href="https://github.com/kernelvale/smart-customer-service-mvp">查看项目 →</a></p>
    </td>
  </tr>
</table>

## 关注的方向

- **Agent 系统的可靠性**：消息顺序、上下文继承、工具调用、取消与恢复。
- **可核实的工程结果**：从触发条件出发，保留复现、修复和回归验证的证据。
- **贴近日常的工具**：把论文阅读、学习练习和重复的信息整理接成可用的流程。

---

<sub>从具体问题开始，让每一次改动都有可检查的结果。</sub>
