# codemao-course-data（脱敏版）

此目录只保留可公开分发的技能说明和配置模板。每位老师需要使用自己的 CRM Cookie、钉钉 MCP 凭证、班级 ID 和表格 ID，不能直接复制其他老师的运行数据。

## 安装本地运行版

如果老师已经安装了完整的 `codemao-course-data` 技能，将它放到以下任一位置即可：

- `副本目录/skills/codemao-course-data`
- `%USERPROFILE%\\.workbuddy\\skills\\codemao-course-data`
- `%USERPROFILE%\\.codex\\skills\\codemao-course-data`

也可以设置环境变量 `CODEMAO_COURSE_DATA_SKILL` 指向完整技能目录。

请不要把 `sync.py` 中的 MCP token、CRM Cookie、缓存 JSON 或个人钉钉表格 ID 提交到公共仓库。
