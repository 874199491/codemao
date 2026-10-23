# NCT 下一级考级报名等级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 NCT 考级表和学情表中维护并同步学生手工填写的“已报名下一级考级”等级。

**Architecture:** `scripts/update_nct_exam_sheet.py` 在写入前先读取现有 NCT 考级表，以学生 ID 保存该手工字段；CRM 刷新后将其合并回输出行。脚本再把合并后的字段按学生 ID 写入学情表文本列，保留空值和未匹配行。

**Tech Stack:** Python 3.10、钉钉 MCP（通过 `build_service_todo.mcp_call`）、现有 CSV 写入与表头读取工具。

---

### Task 1: 为手工报名等级保留逻辑写回归测试

**Files:**
- Create: `tests/test_update_nct_exam_sheet.py`
- Modify: `scripts/update_nct_exam_sheet.py`

- [ ] **Step 1: 写出合并时保留已填写等级的失败测试**

```python
from scripts.update_nct_exam_sheet import merge_rows


def test_merge_rows_keeps_manual_next_exam_registration():
    roster = {"1001": {"学生ID": "1001", "学生姓名": "小明", "上课时间": "周五晚"}}
    nct_rows = [{"学生ID": "1001", "是否购买NCT年卡": "是", "下一次推荐报考等级": "二级"}]
    existing = {"1001": "二级"}

    rows = merge_rows(roster, nct_rows, existing)

    assert rows[0]["已报名下一级考级"] == "二级"
    assert rows[0]["下一次推荐报考等级"] == "二级"
```

- [ ] **Step 2: 运行测试确认当前实现尚不支持参数**

Run: `py -3.10 -m pytest tests/test_update_nct_exam_sheet.py -q`

Expected: FAIL，提示 `merge_rows` 不接受第三个参数。

- [ ] **Step 3: 提供最小实现并让测试通过**

```python
NEXT_EXAM_REGISTRATION_HEADER = "已报名下一级考级"

def merge_rows(roster, nct_rows, registered_levels=None):
    registered_levels = registered_levels or {}
    # 每个 output 行在保留 CRM 字段后写入：
    output[NEXT_EXAM_REGISTRATION_HEADER] = registered_levels.get(student_id, "")
```

- [ ] **Step 4: 重新运行测试**

Run: `py -3.10 -m pytest tests/test_update_nct_exam_sheet.py -q`

Expected: PASS。

### Task 2: 保留 NCT 考级表手工字段并写入学情表

**Files:**
- Modify: `scripts/update_nct_exam_sheet.py:50-70,360-450`
- Test: `tests/test_update_nct_exam_sheet.py`

- [ ] **Step 1: 增加表头及读取现有 NCT 表的函数**

```python
NEXT_EXAM_REGISTRATION_HEADER = "已报名下一级考级"
HEADERS = [
    # 保留既有表头，且在“下一次推荐报考等级”之后插入：
    "下一次推荐报考等级",
    NEXT_EXAM_REGISTRATION_HEADER,
    "NCT ID",
]

def existing_registered_levels(node_id: str, sheet_id: str) -> dict[str, str]:
    result = mcp_call("get_range", {"nodeId": node_id, "sheetId": sheet_id, "range": "A1:Z1200"})
    values = result_rows(result)
    headers = header_row(values)
    id_index = headers.index("学生ID")
    level_index = headers.index(NEXT_EXAM_REGISTRATION_HEADER)
    return {
        str(row[id_index]).strip(): str(row[level_index]).strip()
        for row in values[1:]
        if len(row) > max(id_index, level_index) and str(row[id_index]).strip()
    }
```

- [ ] **Step 2: 在重写 NCT 表前读取并合并手工等级**

```python
sheet_id = locate_or_create_sheet(node_id)
registered_levels = existing_registered_levels(node_id, sheet_id)
rows = merge_rows(roster, nct_rows, registered_levels)
```

- [ ] **Step 3: 增加学情表文本列的创建、写入与回读**

```python
def sync_learning_registered_level(profile, rows):
    target, values = learning_sheet_values(profile)
    headers = header_row(values)
    id_index = required_column(headers, profile, "student_id")
    column = find_or_create_text_column(target, values, NEXT_EXAM_REGISTRATION_HEADER)
    level_by_id = {str(row["学生ID"]): str(row[NEXT_EXAM_REGISTRATION_HEADER]) for row in rows}
    cells = [[{"type": "text", "text": level_by_id.get(str(row[id_index]).strip(), "")}] for row in values[1:]]
    # 分批 set_cell_range 写入 column2:columnN，再 get_range 回读并逐行比较。
    return {"synced": len(cells), "column": column}
```

实现要求：不使用复选框；仅写入学情表已有学生行；找不到学生 ID 的 NCT 行不新建学情行；空值保持为空文本。

- [ ] **Step 4: 扩展测试覆盖空值和学情表映射**

```python
def test_merge_rows_leaves_registration_blank_when_no_manual_value():
    rows = merge_rows({"1001": {"学生ID": "1001"}}, [], {})
    assert rows[0]["已报名下一级考级"] == ""
```

- [ ] **Step 5: 运行测试与语法检查**

Run: `py -3.10 -m pytest tests/test_update_nct_exam_sheet.py -q; py -3.10 -m py_compile scripts/update_nct_exam_sheet.py`

Expected: 测试全绿且语法检查无输出。

### Task 3: 更新工作台说明并进行真实同步验证

**Files:**
- Modify: `apps/teacher_workbench/server.py:223-228`
- Test: `scripts/update_nct_exam_sheet.py` 运行输出和钉钉表格回读

- [ ] **Step 1: 更新任务说明**

将“更新 NCT 考级”说明补充为：同步年卡列和手工维护的“已报名下一级考级”文本列；CRM 刷新不覆盖该手工字段。

- [ ] **Step 2: 运行 NCT 同步任务**

Run: `py -3.10 scripts/update_nct_exam_sheet.py`

Expected: 输出包含 NCT 表及学情表同步统计、目标列字母和回读成功结果。

- [ ] **Step 3: 使用钉钉 MCP 回读验证**

验证 NCT考级 表与学情表均存在 `已报名下一级考级` 表头；随机选择一个手工填写等级的学生，两个表中的等级相同；未填写的学生仍为空。

- [ ] **Step 4: 仅提交本功能文件并上传**

```bash
git add scripts/update_nct_exam_sheet.py apps/teacher_workbench/server.py tests/test_update_nct_exam_sheet.py
git commit -m "feat: sync next exam registration level"
git push origin main
```
