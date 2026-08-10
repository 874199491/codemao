---
name: codemao-course-data
description: Fetch student course completion data from CodeMao CRM and sync to DingTalk spreadsheet. Supports auto-probing spreadsheet structure. When user asks to sync/update course completion data, use this skill. The AI Agent drives the MCP calls via WorkBuddy's built-in DingTalk MCP.
---

# CodeMao CRM → 钉钉表格 同步

## 工作流（AI Agent 驱动）

```
用户: "同步课节 27-30 的完课数据"
         ↓
    AI Agent 执行
         ↓
  ┌─────────────────────────────────────┐
  │  python sync.py 27 28 29 30         │
  │                                     │
  │  [1/3] 抓取 CRM → courses_temp.tsv │
  │  [2/3] 生成批次（reset+update）      │
  │  [3/3] 输出 Agent 推送指令           │
  └─────────────────────────────────────┘
         ↓
  ┌─────────────────────────────────────┐
  │  AI Agent 通过 WorkBuddy 钉钉 MCP   │
  │                                     │
  │  1. get_range → 读钉钉 ID 列        │
  │  2. update_range → 推送 Reset 批次  │
  │  3. update_range → 推送 Update 批次 │
  │  4. get_range → 验证 false 数量     │
  └─────────────────────────────────────┘
         ↓
       完成 ✅
```

---

## 首次使用（3步）

```bash
# 1. 填写配置（MCP 不需要填！）
copy config.template.json config.json
# 编辑 config.json，填入：
#   - classes: term_id、class_id（来自编程猫 CRM）
#   - courses: 课节号 → course_id 映射
#   - dingtalk: node_id（钉钉表格链接）、sheet_id（工作表名称）

# 2. 全自动获取 Cookie（无需手动操作）
python get_cookies.py
# 浏览器自动打开 → 人工登录 → 自动检测登录态 → 保存

# 3. 让 AI Agent 同步！
python sync.py 27 28 29 30
```

---

## 命令参考

| 命令 | 说明 |
|------|------|
| `python sync.py 27 28 29 30` | 一键同步（CRM抓取 → 批次生成 → Agent推送） |
| `python sync.py --probe` | 仅探测钉钉表格结构（首次或换表时） |
| `python get_cookies.py` | 全自动 Cookie 获取（Playwright） |
| `python dingtalk_sync.py --tsv xxx.tsv --courses 27 28` | 单独生成批次 |

---

## 配置文件说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `classes` | ✅ | 班级列表，含 term_id、class_id |
| `courses` | ✅ | 课节号→course_id 映射 |
| `dingtalk.node_id` | ✅ | 钉钉表格 URL 或 doc ID |
| `dingtalk.sheet_id` | ✅ | 工作表 ID 或**名称**（支持名称自动解析） |
| `dingtalk.course_columns` | ❌ | 留空则**自动探测**（推荐） |
| `dingtalk.name_col_letter` | ❌ | 姓名列字母，自动识别"姓名"/"孩子姓名"等 |
| `dingtalk.data_start_row` | ❌ | 数据起始行，默认 2 |
| `mcp.url/auth_token` | ❌ | 不需要！由 WorkBuddy 钉钉 MCP 提供 |

> **MCP 不需要配置**：AI Agent 直接使用 WorkBuddy 内置的钉钉 MCP 读写表格。

---

## 自动探测

每个人的钉钉表格结构不同，脚本会自动探测：

```bash
python sync.py --probe
```

探测内容：
- 表头行 → 数字课节列（如"27"、"课27"、"C++27"）
- 姓名列（匹配"姓名"/"孩子姓名"/"学号"等）
- 结果缓存到 `dingtalk_structure.json`，后续同步复用

---

## 课节 course_id 参考

| 课节 | course_id | 课节 | course_id |
|------|-----------|------|-----------|
| 25 | 9750 | 29 | 9754 |
| 26 | 9751 | 30 | 9755 |
| 27 | 9752 | 31 | 9756 |
| 28 | 9753 | 32 | 9757 |

> course_id = 9725 + 课节号

---

## 常见问题

| 现象 | 解决 |
|------|------|
| Cookie 过期 / 401 | 运行 `python get_cookies.py` 全自动刷新，或手动更新 cookie |
| 课节列未找到 | 运行 `python sync.py --probe` 重新探测 |
| 某个课节 0 条 | 确认 cookie 有效且 config 中 course_id 正确 |
| 推送失败 | 检查 WorkBuddy 钉钉 MCP 连接是否正常 |

### ⚠️ Cookie 文件路径说明

`sync.py` 从 `config.json` 中的 `cookies_file` 字段读取 cookie。
当前配置指向：**桌面路径**（不是 skill 目录下）

```
C:/Users/PC/Desktop/上课/完课/crm_cookies.json
```

更新 token 时需要更新**这个文件**，而不是 skill 目录下的 `crm_cookies.json`。

---

## 当前配置状态

- **班级**：周五（92人）、周六午（60人）、周六晚（79人），共 231 人
- **课节范围**：25-32，对应 course_id 9750-9757
- **钉钉表格**：小九工作表，ID列A，姓名列B，数据从第2行开始
- **课节列映射**：25→AQ, 26→AR, 27→AS, 28→AT, 29→AU, 30→AV, 31→AW, 32→AX
- **Cookie 有效期**：需定期检查，过期会返回 0 条

---

## 文件清单

```
codemao-course-data/
  ├── SKILL.md                    ← 本文档
  ├── sync.py                     ← 一键同步（CRM抓取 → 批次生成 → MCP推送）
  ├── dingtalk_sync.py            ← 批次生成核心
  ├── get_cookies.py              ← 全自动 Cookie 获取（Playwright）
  ├── config.template.json        ← 配置模板
  ├── config.json                 ← 实际配置
  ├── dingtalk_structure.json     ← 表格结构探测缓存
  ├── crm_cookies.json            ← CRM Cookie（桌面那个才是实际使用的）
  ├── userid_externalid_mapping.json ← 学生 userId→externalUserId 映射
  ├── reset_batches.json          ← Reset 批次（自动生成）
  ├── update_batches.json         ← Update 批次（自动生成）
  └── scripts/
      └── quick_excel.py
```
