# EAI-Desktop 功能介绍与技术文档

> **版本**：v0.8.0
> **更新日期**：2026-07-11
> **适用范围**：`D:\Test\GUAN\EAI-Desktop\`
> **决策依据**：升级方案v3-决策版.md

---

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [技术栈](#3-技术栈)
4. [功能模块详解](#4-功能模块详解)
5. [数据模型](#5-数据模型)
6. [API 接口](#6-api-接口)
7. [前端页面](#7-前端页面)
8. [核心工作流](#8-核心工作流)
9. [部署与运行](#9-部署与运行)
10. [验证基线](#10-验证基线)
11. [约束与红线](#11-约束与红线)

---

## 1. 项目概述

### 1.1 定位

EAI-Desktop 是一款面向**具身智能（Embodied AI）研究图谱**的桌面应用，用于论文库的采集、组织、消化、调度、执行与回挂的全流程管理。

### 1.2 核心愿景

**"骨肉双层 + 个人消化层"**：

- **骨架层**：15 张 Atlas 研究图谱（A-N + WAM），覆盖 VLA、世界模型、强化学习、仿真数据、表征学习等方向
- **血肉层**：论文实体 + 关系连线 + 路由，构成可检索的知识网络
- **个人消化层**：笔记、高亮、成熟度、收藏、个人关系线，承载个人研究痕迹
- **AI 协作层**：AI Studio 调度 + AIS Workbench 执行 + 候选 op 审核回挂

### 1.3 设计哲学

| 原则 | 说明 |
| --- | --- |
| 蓝白玻璃视觉 | Apple 蓝 `#0071E3` + `backdrop-filter: blur()` + 渐变，全程锁定 |
| 屎山不堆叠 | 重构前必须写"原架构理解备忘录"，先理解再动手 |
| 红线不逾越 | 核心表结构 / 5 个 v2 JS / atlas 渲染逻辑 不可改 |
| 候选不直写 | AI 产出的 op 先落 `review_items` 待审区，accept 后才写正式表 |
| 验证不退步 | 每 Phase 交付必须跑 `_verify_interactions.py` 24/24 |

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        桌面外壳 (Shell)                        │
│  desktop.html + eai-desktop-shell.js + eai-shell-redesign.css │
│  ┌─────────┐  ┌──────────────────────────────────────────┐  │
│  │ Sidebar │  │            iframe 内容区                    │  │
│  │ + Cmd K │  │  Atlas Overview / AI Studio / Workbench …  │  │
│  │ + CtxBus│  │                                            │  │
│  └─────────┘  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │ fetch /data/*.bundle.json + /api/*
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI 后端 (backend/)                    │
│  17 个 Router │ 22 个 ORM Model │ 12 个 Service               │
│  ┌───────────┐  ┌───────────┐  ┌────────────┐  ┌─────────┐  │
│  │ AI Ops    │  │ Reviews   │  │ Bundle     │  │ Personal│  │
│  │ 12 阶流水线│  │ 候选审核  │  │ Builder    │  │ Layer   │  │
│  └───────────┘  └───────────┘  └────────────┘  └─────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │ SQLAlchemy ORM
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              SQLite (eai.db) + Alembic 迁移                    │
│  核心表 (papers/atlases/relations/entries...)                  │
│  + 个人层 (personal_links/paper_notes/paper_personal)         │
│  + AI 层 (ai_context_packs/ai_patch_runs/review_items)        │
│  + AIS 层 (ais_projects/ais_jobs/ais_artifacts...)            │
└─────────────────────────────────────────────────────────────┘
                              │ build_all_bundles
                              ▼
┌─────────────────────────────────────────────────────────────┐
│        web/data/*.bundle.json (前端数据源)                     │
│  15 张 atlas bundle + atlases.index + papers.index            │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 数据流（Phase 0B 反转后）

**关键设计**：前端不内联数据，所有数据通过 fetch 异步加载。

```
YAML 源 (data/papers/*.yaml, data/atlases/*.yaml)
    │ import_service.py
    ▼
SQLite (eai.db)
    │ bundle_builder.py
    ▼
web/data/{atlas_id}.bundle.json  ←── 前端 fetch 目标
    │ eai-atlas-data-loader.js
    ▼
Atlas HTML 页面渲染 (15 张)
```

- **写入路径**：YAML → DB → bundle JSON（后端构建）
- **读取路径**：bundle JSON → 前端 fetch → 渲染
- **个人层**：直接走 API（`/api/papers/{id}/notes` 等），不经 bundle

---

## 3. 技术栈

### 3.1 后端

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.12+ | 运行时（注意：3.14 因 pydantic-core 编译问题暂不支持） |
| FastAPI | ≥0.115 | Web 框架 |
| Uvicorn | ≥0.32 | ASGI 服务器（支持 `--reload` 热重载） |
| SQLAlchemy | ≥2.0 | ORM |
| Alembic | ≥1.14 | 数据库迁移 |
| Pydantic | ≥2.0 | 数据校验 |
| PyYAML | ≥6.0 | YAML 源数据解析 |
| httpx | ≥0.28 | HTTP 客户端（连接器调用） |
| pypdf / pdfminer | - | PDF 文本提取 |
| openai | ≥1.40 | AI API 调用（可选） |

### 3.2 前端

| 组件 | 用途 |
| --- | --- |
| 原生 HTML/CSS/JS | 无构建步骤，直接服务 |
| CSS 变量系统 | `eai-redesign-tokens.css` 定义 `--color-*` / `--radius-*` |
| 玻璃态 CSS | `backdrop-filter: blur()` + 半透明渐变 |
| iframe 架构 | Shell 通过 iframe 加载子页面 |
| fetch + bundle JSON | 异步数据加载 |

### 3.3 桌面打包

| 组件 | 用途 |
| --- | --- |
| PyInstaller | `eai-desktop.spec` 打包为单可执行文件 |
| desktop_entry.py | 桌面入口脚本 |

### 3.4 测试与验证

| 工具 | 用途 |
| --- | --- |
| Playwright | E2E 浏览器自动化测试 |
| `_verify_interactions.py` | 24/24 交互基线验证 |
| `_phase1*_test_*.py` | 各 Phase 专项测试脚本 |

---

## 4. 功能模块详解

### 4.1 Phase 0：基础设施

#### 4.1.1 Phase 0A — 基线录制
- 运行 `_verify_interactions.py`，记录 24/24 交互基线
- 任何重构不得退步

#### 4.1.2 Phase 0B — 数据流反转
- **目标**：15 张 atlas HTML 删除内联 `const PAPERS/EDGES/ROUTES`，改为 `fetch /data/{atlas_id}.bundle.json`
- **实现**：`eai-atlas-data-loader.js` 统一负责异步加载
- **收益**：数据与视图解耦，修改数据无需改 HTML

#### 4.1.3 Phase 0C — 个人层迁移
- 新增 3 张表：`personal_links` / `paper_notes` / `paper_personal`
- Alembic 迁移 `0006_personal_layer.py`，含完整 downgrade
- 个人层数据**不**从 YAML 导入（仅存 DB）

### 4.2 Phase 1：六工位主循环

#### 4.2.1 Phase 1A — 摄入工位（Intake）

**功能**：新论文双栏审核入库

- **后端**：
  - `GET /api/papers/candidates` — 获取候选论文列表（自动排除已入库）
  - `POST /api/atlases/{atlas_id}/entries` — 将论文加入指定 atlas
- **前端**：`web/intake.html` 双栏审核 UI（左列表 + 右详情）
- **设计**：复用现有 atlas 页面（iframe），入库后同步重建 bundle
- **测试**：`_phase1a_test_intake.py`（16 项）

#### 4.2.2 Phase 1B — 组织工位（Organize）

**功能**：Atlas CRUD + Route 编辑

- Atlas 增删改查
- Route（路由连线）可视化编辑
- 就地编辑能力（逐步替代 admin）

#### 4.2.3 Phase 1C — 消化工位（Digest）

**功能**：论文笔记 + 个人状态

- **后端 API**：
  - `GET/POST /api/papers/{paper_id}/notes` — 笔记 CRUD
  - `PATCH/DELETE /api/notes/{note_id}` — 单条笔记操作
  - `GET/PUT /api/papers/{paper_id}/personal` — 成熟度 / 收藏状态
  - `GET/POST /api/personal-links` — 跨 atlas 个人关系线
- **前端**：`eai-atlas-drawer-v2.js` + `eai-atlas-drawer-v2.css`
  - `loadPersonalAsync` 异步加载笔记/状态
  - `bindNoteAutoSave`（500ms 防抖）自动保存笔记
  - `bindStateButtons` 绑定成熟度/收藏按钮
- **CSS**：`.v2-personal-state` / `.v2-maturity-btn` / `.v2-star-btn`
  - 蓝色 `#0071E3`（成熟度）+ 金色 `#FF9500`（收藏）
- **测试**：17 项 E2E（笔记自动保存、成熟度/收藏持久化）

#### 4.2.4 Phase 1D — 调度工位（Schedule）⭐

**功能**：AI Studio + NextActionCard 北极星调度

- **NextActionCard**：基于当前状态推导下一步动作的卡片
  - 6 点进度条可视化
  - `prefers-reduced-motion` 兼容动画
- **后端**：状态轮询接口
- **前端逻辑**（`eai-ai-studio-v2.js`）：
  - `deriveNextAction()` — 推导下一步
  - `injectNextActionCard()` — 注入卡片
  - `updateNextActionCard()` — 更新状态
  - `handleNacAction()` — 处理动作触发
- **零侵入**：未修改核心表结构或受限 v2 JS
- **测试**：`_phase1d_test_next_action.py`（72 项）

#### 4.2.5 Phase 1E — 执行工位（Execute）

**功能**：候选 op 生成 + AI 执行

- **3 个候选 op**：
  | op 类型 | 用途 |
  | --- | --- |
  | `new_paper_candidate` | 建议新增论文 |
  | `new_entry_candidate` | 建议将论文加入 atlas |
  | `new_relation_candidate` | 建议新增关系连线 |
- **`expand_atlas` 任务类型**：允许上述 3 个候选 op
- **AI patch 12 阶流水线**：
  ```
  1-3   parse       解析 AI 输出
  4-10  validate    校验（候选 op 跳过实体检查，用 PROPOSED_FIELDS 白名单）
  11    dry-run     生成 diff（候选 op 每个 proposed_field 一个 diff，before=None）
  12    apply       应用（候选 op 仅落 review_items，status=pending）
  ```
- **`POST /api/ai/run`**：返回 `clipboard_fallback`，启用前端剪贴板工作流
- **风险等级**：候选 op 固定为 `CAUTION`
- **测试**：`_phase1e_test_ai_run.py`（66 项）

#### 4.2.6 Phase 1F — 回挂工位（Record Back）

**功能**：候选 op 审核 → 正式写入

- **`review_items` 表**：待审区，字段包括 `proposed_change` / `before_state` / `after_state` / `status`
- **审核流程**：
  ```
  review_items (pending)
      │ accept_review
      ▼
  _unpack_candidate_change  ← 解包 proposed_fields 为扁平 change dict
      │
      ▼
  _apply_*_change           ← target_id=None 走新建分支
      │
      ▼
  papers / relations / atlas_entries  ← 正式表写入
      │
      ▼
  build_all_bundles         ← 重建 bundle（失败不阻塞主流程）
  ```
- **关键函数**（`review_queue.py`）：
  - `CANDIDATE_REVIEW_TYPES` — 候选审核类型集合
  - `_is_candidate_review` — 判断是否候选 op
  - `_unpack_candidate_change` — 解包嵌套字段
- **容错**：bundle 重建失败不阻塞 accept 主流程（先 commit 正式表）
- **测试**：`_phase1f_test_record_back.py`（47 项）

### 4.3 AIS Workbench（AI 科学家工作台）

**功能**：项目管理 + Codex 任务执行

- **数据模型**：`AISProject` / `AISJob` / `AISArtifact` / `AISIdea` / `AISCodexTask`
- **linked_paper_id / linked_atlas_id**：支持从论文 Drawer 跳转并预填关联信息
- **9 个 Codex task-types**：source_map_inspector / event_emitter_patch / runner_wrapper / bfts_config_tuning / failed_node_debug / artifact_packaging / paper_review_disclosure / clipboard_format_repair / security_review
- **前端**：`ai_scientist_workbench.html` + `eai-ais-workbench-v2.js`
- **测试**：`_phase1g_test_workbench_paper_link.py`（69 项）

### 4.4 质量与证据

- **质量审查**：`quality_runs` + `quality_issues`，`quality_service.py` 执行检查
- **证据管理**：`evidence_items`，关联论文与实验结果
- **审计日志**：`audit_events`，记录所有变更操作

### 4.5 发布管理

- `releases` 表管理数据快照版本
- `release_manager.py` 负责快照创建与回滚

### 4.6 导入服务

- `import_service.py`：从 YAML 源导入论文/关系/atlas 数据
- `import_jobs` 表跟踪导入任务状态
- 连接器：arxiv / crossref / openalex / openreview / semantic_scholar

### 4.7 桌面文件访问

- `desktop_files_router`：本地 PDF / 文件读取
- 支持 PDF 文本提取（pypdf 优先，pdfminer 兜底）

---

## 5. 数据模型

### 5.1 核心表（不可修改）

| 表 | 用途 |
| --- | --- |
| `papers` | 论文实体 |
| `atlases` | Atlas 图谱元数据 |
| `routes` | 路由连线 |
| `atlas_entries` | Atlas 中的论文条目 |
| `relations` | 论文间关系（atlas 内） |
| `aliases` | 论文别名 |
| `evidence_items` | 证据项 |
| `source_snapshots` | 数据源快照 |
| `review_items` | 审核待办（候选 op 落点） |
| `import_jobs` | 导入任务 |
| `releases` | 发布版本 |
| `audit_events` | 审计日志 |
| `system_snapshots` | 系统快照 |

### 5.2 个人层表（Phase 0C 新增）

| 表 | 用途 | 关键字段 |
| --- | --- | --- |
| `personal_links` | 跨 atlas 个人关系线 | source_paper_id, target_paper_id, relation_type |
| `paper_notes` | 论文笔记 | paper_id, note_type, content, created_at |
| `paper_personal` | 论文个人状态 | paper_id, maturity, star, last_read_at |

### 5.3 AI 层表

| 表 | 用途 |
| --- | --- |
| `ai_context_packs` | AI 上下文包（prompt + 数据） |
| `ai_patch_runs` | AI patch 执行记录 |
| `ai_patch_operations` | 单个 patch 操作 |

### 5.4 AIS 层表

| 表 | 用途 |
| --- | --- |
| `ais_projects` | AIS 项目（含 linked_paper_id, linked_atlas_id） |
| `ais_jobs` | AIS 任务 |
| `ais_artifacts` | 任务产物 |
| `ais_ideas` | 研究想法 |
| `ais_codex_tasks` | Codex 任务定义 |

### 5.5 其他

| 表 | 用途 |
| --- | --- |
| `quality_runs` / `quality_issues` | 质量审查 |
| `api_keys` | API 密钥管理 |

### 5.6 Alembic 迁移历史

| 版本 | 说明 |
| --- | --- |
| 0001_baseline | 基线表结构 |
| 0002_quality | 质量审查表 |
| 0003_evidence | 证据表 |
| 0004_ai_ops | AI 操作表 |
| 0005_ais_workbench | AIS 工作台表 |
| 0006_personal_layer | 个人层 3 张表 |
| 0007_ais_paper_link | AIS linked_paper_id / linked_atlas_id |

---

## 6. API 接口

### 6.1 路由总览

后端注册 17 个 Router，所有接口带 `/api` 前缀（除静态文件）。

| Router | 前缀 | 功能 |
| --- | --- | --- |
| health | `/api/health` | 健康检查 |
| auth | `/api/auth` | 认证（桌面模式默认关闭） |
| atlases | `/api/atlases` | Atlas CRUD |
| papers | `/api/papers` | 论文 CRUD + candidates |
| relations | `/api/relations` | 关系 CRUD |
| maintenance | `/api/maintenance` | 维护操作 |
| reviews | `/api/reviews` | 审核队列（accept/reject） |
| imports | `/api/imports` | 导入任务 |
| build | `/api/build` | Bundle 构建 |
| releases | `/api/releases` | 发布管理 |
| quality | `/api/quality` | 质量审查 |
| evidence | `/api/evidence` | 证据管理 |
| ai_ops | `/api/ai` | AI 操作（run / context-packs） |
| ais_workbench | `/api/ais` | AIS 工作台 |
| api_keys | `/api/api-keys` | API 密钥 |
| desktop_files | `/api/desktop-files` | 桌面文件访问 |
| personal | `/api` | 个人层（notes / personal / personal-links） |

### 6.2 关键接口

#### 个人层
```
GET    /api/papers/{paper_id}/notes          获取笔记列表
POST   /api/papers/{paper_id}/notes          创建笔记
PATCH  /api/notes/{note_id}                  更新笔记
DELETE /api/notes/{note_id}                  删除笔记
GET    /api/papers/{paper_id}/personal       获取个人状态
PUT    /api/papers/{paper_id}/personal       更新个人状态
GET    /api/personal-links                   获取个人关系线
POST   /api/personal-links                   创建个人关系线
```

#### 摄入
```
GET    /api/papers/candidates                候选论文列表
POST   /api/atlases/{atlas_id}/entries       论文加入 atlas
```

#### AI 操作
```
POST   /api/ai/context-packs                 创建上下文包
POST   /api/ai/run                           执行 AI（返回 clipboard_fallback）
```

#### 审核
```
GET    /api/reviews                          审核队列列表
POST   /api/reviews/{id}/accept              接受候选 op
POST   /api/reviews/{id}/reject              拒绝候选 op
```

#### 构建
```
POST   /api/build/bundles                    构建所有 bundle
POST   /api/build/bundles/{atlas_id}         构建单个 atlas bundle
```

### 6.3 静态文件挂载

| 路径 | 目录 | 用途 |
| --- | --- | --- |
| `/data` | web/data | bundle JSON |
| `/assets` | web/assets | CSS/JS 资源 |
| `/atlases` | web/atlases | atlas 静态页 |
| `/admin` | web/admin | React admin（html=True） |
| `/site` | web | 前端站点（html=True） |

---

## 7. 前端页面

### 7.1 页面清单

| 页面 | 路径 | 用途 |
| --- | --- | --- |
| 桌面外壳 | `desktop.html` | Shell 主框架（Sidebar + iframe） |
| Atlas Overview | `index.html` | 15 张图谱总览 |
| 单张 Atlas | `paper/{id}.html`（15 张） | 论文图谱渲染 |
| AI Studio | `ai_studio.html` | AI 调度工作台 |
| AIS Workbench | `ai_scientist_workbench.html` | AI 科学家工作台 |
| 摄入审核 | `intake.html` | 双栏论文入库 |
| API Keys | `api_keys.html` | 密钥管理 |
| 训练骨干 | `embodied_ai_model_training_backbone_final.html` | 训练流程 |
| 总览骨干 | `embodied_ai_overview_backbone_final.html` | 总览流程 |
| 训练生命周期 | `training_lifecycle_pipeline_memory.html` | 生命周期管理 |
| 论文索引 | `paper/00_paper_index.html` | legacy 论文索引 |
| Admin | `admin/index.html` | React 管理后台 |

### 7.2 核心 JS 资源

#### 受限 v2 JS（5 个，不可修改）

| 文件 | 用途 |
| --- | --- |
| `eai-relation-stabilizer.js` | 关系线稳定化 |
| `eai-spatial-transition.js` | 空间过渡动画 |
| `eai-paper-relation-unifier.js` | 论文关系统一 |
| `eai-atlas-detail-v2.js` | Atlas 详情交互 |
| `eai-atlas-drawer-v2.js` | Atlas 抽屉（Phase 1C 解禁修改） |

#### 其他核心 JS

| 文件 | 用途 |
| --- | --- |
| `eai-desktop-shell.js` | Shell 主逻辑 |
| `eai-atlas-data-loader.js` | 数据异步加载（Phase 0B） |
| `eai-atlas-overview-v2.js` | Overview 逻辑 |
| `eai-ai-studio-v2.js` | AI Studio 逻辑 + NextActionCard |
| `eai-ais-workbench-v2.js` | Workbench 逻辑 |
| `eai-atlas-unified-interaction.js` | 统一交互 |
| `eai-desktop-bridge.js` | 桌面桥接 |

### 7.3 CSS 资源

| 文件 | 用途 |
| --- | --- |
| `eai-redesign-tokens.css` | 设计 token（颜色/圆角/阴影变量） |
| `eai-shell-redesign.css` | Shell 视觉层 |
| `eai-atlas-overview-v2.css` | Overview 视觉 |
| `eai-atlas-drawer-v2.css` | Drawer 视觉 |
| `eai-ai-studio-v2.css` | AI Studio 视觉 |
| `eai-ais-workbench-v2.css` | Workbench 视觉 |
| `eai-atlas-detail-v2.css` | Atlas 详情视觉 |

### 7.4 视觉设计规范

```
主色：#0071E3 (Apple 蓝)
背景：linear-gradient(180deg, #F7FAFE, #E5EEF8)
玻璃：backdrop-filter: blur(20px) saturate(1.08)
圆角：--radius-xl: 16px / --v2-radius-md: 8px
阴影：inset 0 1px 0 rgba(255,255,255,0.7), 0 1px 3px rgba(0,0,0,0.04)
文字：--v2-text1: #1D1D1F / --v2-text2: #6E6E73 / --v2-text3: #AEAEB2
字体：Inter (UI) / Source Serif 4 (display) / JetBrains Mono (mono)
```

---

## 8. 核心工作流

### 8.1 候选 op 全流程（Phase 1E + 1F）

```
用户在 AI Studio 发起 expand_atlas 任务
    │
    ▼
POST /api/ai/context-packs  →  创建 context pack
    │
    ▼
POST /api/ai/run  →  AI 生成候选 op（clipboard_fallback）
    │
    ▼
AI patch 12 阶流水线
    ├─ 1-3  parse：解析输出
    ├─ 4-10 validate：候选 op 跳过实体检查，用 PROPOSED_FIELDS 白名单
    ├─ 11   dry-run：每个 proposed_field 生成 diff（before=None）
    └─ 12   apply：仅落 review_items（status=pending, risk=CAUTION）
    │
    ▼
用户在审核界面查看 review_items
    │
    ├─ accept → accept_review
    │   ├─ _is_candidate_review → True
    │   ├─ _unpack_candidate_change → 扁平化 proposed_fields
    │   ├─ target_id=None → 走新建分支
    │   ├─ _apply_*_change → 写入 papers/relations/entries
    │   ├─ commit（先提交正式表）
    │   └─ build_all_bundles（失败不阻塞）
    │
    └─ reject → 更新 status=rejected
```

### 8.2 个人层数据流（Phase 1C）

```
用户在 Drawer 编辑笔记/状态
    │
    ▼
eai-atlas-drawer-v2.js
    ├─ bindNoteAutoSave（500ms 防抖）→ POST/PATCH /api/notes
    └─ bindStateButtons → PUT /api/papers/{id}/personal
    │
    ▼
personal_router → ORM → SQLite
    │
    ▼
下次打开 Drawer → loadPersonalAsync → GET /api/papers/{id}/notes + personal
```

### 8.3 Bundle 构建流程

```
数据变更（import / accept / 手动编辑）
    │
    ▼
POST /api/build/bundles  或  build_all_bundles(db)
    │
    ▼
bundle_builder.py
    ├─ 遍历所有 atlas
    ├─ 查询 papers / relations / entries / personal_links
    ├─ 组装 bundle JSON
    └─ 写入 web/data/{atlas_id}.bundle.json
    │
    ▼
前端 fetch /data/{atlas_id}.bundle.json → 增量渲染
```

### 8.4 桌面启动流程

```
用户双击可执行文件 / 运行 desktop_entry.py
    │
    ▼
启动 FastAPI (uvicorn) on 127.0.0.1:8000
    │
    ├─ lifespan: create_tables() → 若表空则 import YAML + build bundles
    │
    ▼
打开浏览器/内嵌窗口 → http://127.0.0.1:8000/site/desktop.html
    │
    ▼
Shell 加载 → iframe 加载子页面 → fetch bundle JSON → 渲染
```

---

## 9. 部署与运行

### 9.1 开发环境

```powershell
# 1. 进入项目
cd d:\Test\GUAN\EAI-Desktop\backend

# 2. 使用 Python 3.12（非 3.14）
# 推荐使用 codex-runtime 的 python
$py = "C:\Users\观\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

# 3. 安装依赖
& $py -m pip install -r requirements.txt

# 4. 数据库迁移
& $py -m alembic upgrade head

# 5. （可选）导入 YAML 源数据
& $py -m app.cli sync import-yaml

# 6. （可选）构建 bundle
& $py -m app.cli build bundles

# 7. 启动服务（热重载）
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 9.2 访问地址

| 入口 | URL |
| --- | --- |
| 桌面外壳 | http://127.0.0.1:8000/site/desktop.html |
| API 文档 | http://127.0.0.1:8000/docs |
| 健康检查 | http://127.0.0.1:8000/api/health |
| 根信息 | http://127.0.0.1:8000/ |

### 9.3 环境变量（.env）

```ini
EAI_DATABASE_URL=sqlite:///./eai.db
EAI_APP_HOST=0.0.0.0
EAI_APP_PORT=8000
EAI_APP_DEBUG=false
EAI_DATA_DIR=../data
EAI_PUBLIC_DIR=../web
EAI_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:8000,...
EAI_AUTH_ENABLED=false
EAI_ADMIN_TOKEN=change-me
```

### 9.4 桌面打包

```powershell
cd d:\Test\GUAN\EAI-Desktop\backend
& $py -m PyInstaller eai-desktop.spec
# 产物在 dist/ 目录
```

### 9.5 测试

```powershell
# 设置 Playwright 浏览器路径
$env:PLAYWRIGHT_BROWSERS_PATH = "d:\Test\GUAN\EAI-Desktop\.playwright-browsers"
& $py -m playwright install chromium

# 交互基线验证（24/24）
& $py d:\Test\GUAN\EAI\_verify_interactions.py

# 各 Phase 专项测试
& $py d:\Test\GUAN\EAI-Desktop\_phase1a_test_intake.py
& $py d:\Test\GUAN\EAI-Desktop\_phase1c_test_*.py
& $py d:\Test\GUAN\EAI-Desktop\_phase1d_test_next_action.py
& $py d:\Test\GUAN\EAI-Desktop\_phase1e_test_ai_run.py
& $py d:\Test\GUAN\EAI-Desktop\_phase1f_test_record_back.py
```

---

## 10. 验证基线

### 10.1 当前基线（24/24）

| 模块 | 验证项 | 数量 |
| --- | --- | --- |
| Desktop Shell | TitleBar + Sidebar + iframe + shell.js | 4 |
| Command Palette | ⌘K + 可见 + 5 mode tabs + 搜索框 | 4 |
| Atlas G | 92 paper-card + data-paper-id + bundle + .side + 点击响应 + eai-refactored | 6 |
| AI Studio | 10 面板 + eai-refactored + 15 atlas 分组 | 3 |
| AIS Workbench | eai-refactored + 10 面板 + 9 Codex task-types | 3 |
| API Keys | 页面 + SSE 200 | 2 |
| Admin | React 渲染 + JS 加载 | 2 |
| **合计** | | **24** |

### 10.2 Phase 1 专项测试

| Phase | 测试脚本 | 项数 |
| --- | --- | --- |
| 1A | `_phase1a_test_intake.py` | 16 |
| 1C | `_phase1c_test_*.py` | 17 |
| 1D | `_phase1d_test_next_action.py` | 72 |
| 1E | `_phase1e_test_ai_run.py` | 66 |
| 1F | `_phase1f_test_record_back.py` | 47 |
| 1G | `_phase1g_test_workbench_paper_link.py` | 69 |

### 10.3 验证规则

- 任何 PR/交付前必须跑 `_verify_interactions.py` → 24/24 通过
- 退步项必须修复后才能合并
- 新增功能应新增验证项（基线随之增长）

---

## 11. 约束与红线

### 11.1 硬约束

| 约束 | 说明 |
| --- | --- |
| 核心表结构不可改 | papers / atlases / routes / atlas_entries / relations 等列定义不变 |
| 5 个 v2 JS 不可改 | relation-stabilizer / spatial-transition / paper-relation-unifier / atlas-detail-v2 / atlas-drawer-v2（Phase 1C 解禁例外） |
| Atlas 渲染逻辑不变 | 屎山堆叠教训，不碰渲染核心 |
| 候选 op 不直写正式表 | 仅落 review_items，accept 后才写 |
| POST /api/ai/run 返回 clipboard_fallback | 启用前端剪贴板工作流 |
| Bundle 重建失败不阻塞 accept | 主流程先 commit 正式表 |

### 11.2 候选 op 校验规则

- 跳过实体 scope / 存在性检查
- 使用 `PROPOSED_FIELDS` 白名单
- 风险等级固定为 `CAUTION`

### 11.3 视觉风格宪法

```
--color-primary: #0071E3         /* Apple 蓝，唯一主色 */
--radius-xl: 16px                /* 卡片圆角 */
backdrop-filter: blur(14-22px)   /* 玻璃效果，保留 */
```

- 不得删除以上 token 的使用
- 新增组件必须复用这些 token
- 不得引入冲突色

### 11.4 备忘录流程

任何重构动手前，必须先输出"原架构理解备忘录"：
1. Read/Grep 读懂原架构
2. 写备忘录（文件身份 / 数据流 / 依赖 / 生效代码 / 重构方案 / 验证方式）
3. 保留 .bak 副本
4. 在原文件存在下写新代码
5. 跑 `_verify_interactions.py` → 24/24 不退步
6. 用户/PM 验收
7. 删 .bak

**严禁"先删后写"或"边删边写"。**

---

## 附录

### A. 项目目录结构

```
EAI-Desktop/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置
│   │   ├── db.py                # 数据库
│   │   ├── cli.py               # CLI 工具
│   │   ├── routers/             # 17 个路由
│   │   ├── models/              # 22 个 ORM 模型
│   │   ├── schemas/             # Pydantic Schema
│   │   ├── services/            # 12 个服务
│   │   ├── connectors/          # 数据源连接器
│   │   ├── middlewares/         # 中间件
│   │   └── data/                # AIS skills pack
│   ├── alembic/                 # 迁移脚本
│   ├── eai.db                   # SQLite 数据库
│   ├── requirements.txt
│   └── eai-desktop.spec         # PyInstaller 配置
├── web/
│   ├── desktop.html             # Shell 主页
│   ├── index.html               # Atlas Overview
│   ├── ai_studio.html           # AI Studio
│   ├── ai_scientist_workbench.html
│   ├── intake.html              # 摄入审核
│   ├── api_keys.html
│   ├── assets/                  # CSS + JS
│   ├── data/                    # bundle JSON
│   ├── paper/                   # 15 张 atlas HTML
│   └── admin/                   # React admin
├── data/                        # YAML 源数据
├── docs/                        # 文档
├── 升级方案v3-决策版.md
├── 开发交接.md
└── _verify_interactions.py
```

### B. 关键服务文件

| 服务 | 文件 | 职责 |
| --- | --- | --- |
| Bundle 构建 | `services/bundle_builder.py` | DB → bundle JSON |
| 导入 | `services/import_service.py` | YAML → DB |
| AI 上下文 | `services/ai_context_exporter.py` | 生成 context pack |
| AI Patch 解析 | `services/ai_patch_parser.py` | 解析 AI 输出 |
| AI Patch 校验 | `services/ai_patch_validator.py` | 校验 op |
| AI Patch Diff | `services/ai_patch_diff.py` | 生成 dry-run diff |
| AI Patch 应用 | `services/ai_patch_applier.py` | 应用 op |
| 审核队列 | `services/review_queue.py` | accept/reject 逻辑 |
| 发布管理 | `services/release_manager.py` | 快照管理 |
| 质量审查 | `services/quality_service.py` | 质量检查 |
| 审计读取 | `services/audit_reader.py` | 审计日志查询 |
| 漂移检查 | `services/drift_checker.py` | 数据漂移检测 |

### C. 版本历史

| 版本 | 主要变更 |
| --- | --- |
| v0.8.0 | Phase 1 六工位完成（1A-1F）+ AIS linked_paper_id |
| v0.7.x | Phase 0 数据流反转 + 个人层迁移 |
| v0.6.x | AI Ops + AIS Workbench 基础 |
| v0.5.x | 配置系统 + API Keys |
| v0.4.x | 认证 + 全局异常处理 |
| v0.3.x | 质量审查 + 证据管理 |
| v0.2.x | 导入服务 + 发布管理 |
| v0.1.x | 基线表结构 + Atlas 渲染 |

---

**文档结束** | EAI-Desktop v0.8.0 | 2026-07-11
