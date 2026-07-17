# 工程经验

## 为什么需要 clean 包

原工程经历了多轮快速试错，功能方向逐渐清晰，但代码逐渐堆到少数大文件中。继续直接堆功能会让 UI、状态、数据写入和旧功能吸收互相缠住。clean 包的目标是先把“可运行最小必要集”和“历史判断”分离出来，再开始下一轮结构化升级。

## 目前最危险的技术债

- 前端 `src/main.jsx` 过大，状态和视图混在一起。
- 服务端 `app/main.py` 过大，schema、storage、Task Pack、API 调用、路由都在一起。
- 旧功能吸收是补丁式推进，容易出现“入口有了，但体验不像一个系统”。
- 多轮 UI 修改留下了样式残留，导致纸质感、方框感、字号不一致反复出现。
- 乱码曾经多次进入前后端文案，后续必须统一 UTF-8 文案常量。
- Atlas 性能和滚动交互对布局结构很敏感，不能用巨型高度容器硬撑。

## 已经验证过的边界

- `npm run build` 可以作为前端最低验收。
- `python -m unittest discover -s app/service/tests -v` 可以作为服务端最低验收。
- Atlas bundle 可以只读复用旧 `web/data/*.bundle.json`。
- 个人数据用 JSON 文件层足够支撑第一阶段。
- 复制给 Codex 的路径必须保留；API 只能作为用户确认后的辅助。

## 后续开发守则

1. 先拆结构，再加大功能。
2. 所有新增数据先进入 `data/personal`，不碰旧 SQLite 和旧 bundle。
3. 所有写盘都走原子写。
4. 不把 key、完整大包、原始 API 响应写进个人数据。
5. 普通输入必须是消息，只有 `/` 才是命令。
6. UI 修改要跑浏览器截图或手动验收，不要只看编译通过。
7. 右栏是 inspector，不是后台表单。
8. Tools 是运行回看和高频入口，不是旧功能垃圾桶。
9. 每吸收一个旧功能，都要回答：它服务 Atlas、Canvas、线程、对象记忆还是实验运行？
10. 不要为了一次演示把永久数据模型做重。

## 推荐重构节奏

### 第 1 步：拆前端

- 先抽 API client 和文案常量。
- 再抽 `Sidebar`、`RightRail`、`AtlasSurface`、`CanvasSurface`、`PaperReadingPage`、`LabRunPage`。
- 最后再改视觉。

### 第 2 步：拆服务端

- 先抽 `core/storage.py` 和 `core/paths.py`。
- 再抽 Pydantic schemas。
- 最后按 router 拆接口。

### 第 3 步：补体验闭环

- 论文卡 → 阅读页 → 论文聊天 → 结构化对象记忆。
- Atlas 更新 → 虚框候选 → 审查应用。
- Canvas 任务/假设 → 实验运行 → 结果回写 Canvas。

## 测试建议

服务端至少覆盖：

- 旧线程兼容读取。
- 线程消息追加。
- Task Pack 预览不写盘。
- 结果 preview 不写盘。
- 结果 confirm 才写入。
- 对象记忆不会保存密钥。
- atlas update 应用候选不改 bundle。
- lab run confirm 写入 stages/findings/artifacts/messages。

前端至少人工验收：

- 首页普通输入生成消息。
- `/` 命令面板可用，未知命令不静默失败。
- Atlas 纵向滚动、Shift 横向滚动、横向拖拽、空白取消聚焦。
- 右栏总览、Detail、Context、模板、更新、实验入口切换不跳空。
- 论文阅读页从右侧展开，不遮左栏，无悬浮关闭层。
- 实验运行页从右侧展开，阶段树可编辑。
