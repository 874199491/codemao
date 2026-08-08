# 编程猫教师工作台模板版

这是一个可以分发给其他老师使用的教师工作台副本。它不依赖 Codex 登录，但需要本机有 Python 3.10、Node.js、Chrome，以及可用的钉钉/CRM 登录状态。

## 快速使用

1. 先双击 `一键安装运行环境.bat`，或英文入口 `install-runtime.bat`。
   - 它会检查并安装 Python 3.10、Node.js LTS、npm。
   - 它会先检查 winget；如果没有，会尝试自动安装 App Installer / winget。
   - 如果自动安装 winget 失败，再从 Microsoft Store 手动安装/更新 App Installer。
2. 双击 `启动教师工作台.vbs` 静默启动看板；英文入口是 `launch-workbench.vbs`。
   - 默认打开：`http://127.0.0.1:8876`
   - 不会弹出 cmd 窗口。
   - 如需查看命令行日志，再使用 `启动教师工作台.bat`。
3. 打开看板后进入配置区域，优先使用“其他老师配置生成助手”。
4. 生成后人工检查：
   - CRM 班级 `class_id / label / match_prefix`
   - 钉钉 `node_id / learning_sheet_id`
   - 学情表表头探测结果 `learning_sheet_schema`
   - 班级开始日期 `cohort_start`
   - 学员池 `class_pool_id`
5. 先运行“运行完整状态检查”，确认读取的是目标老师自己的配置。
6. 状态检查通过后，再运行完课、直播、接龙、邀约跟进、课后反馈等写入任务。

## 配置文件

主配置在：

`data/teacher-workbench-config.json`

其中最重要的是：

- `cohort_code`：看板显示用的班级/批次简称
- `cohort_start`：第一周开始日期
- `chrome_debug_port`：CRM 调试端口，默认 9223
- `profile.data_prefix`：本地数据文件前缀，例如 `0801`、`teacher-li-0801`
- `profile.crm.class_pool_id`：CRM 学员池 ID
- `profile.dingtalk.node_id`：钉钉文档节点 ID
- `profile.dingtalk.learning_sheet_id`：学情表工作表 ID
- `profile.classes`：目标老师自己的 CRM 班级列表
- `profile.files`：本地缓存名单/退费/完课文件路径

## 启动入口说明

- `一键安装运行环境.bat`：一键安装 Python 3.10 和 Node.js。
- `install-runtime.bat`：英文文件名的一键安装入口。
- `启动教师工作台.vbs`：推荐启动方式，无 cmd 窗口。
- `launch-workbench.vbs`：英文文件名的静默启动入口。
- `启动教师工作台.bat`：调试备用，会显示命令行窗口。
- `一键更新工作台.bat`：从 GitHub 更新工作台程序文件；没有 Git 的电脑也可以用。
- `update-workbench.bat`：英文文件名的一键更新入口。

## 一键更新说明

如果发布者把工作台代码放到 GitHub，其他老师后续不需要重新拿压缩包，只要双击 `一键更新工作台.bat` 即可更新。

模板已经内置默认更新源：

```json
{
  "repository_url": "https://github.com/874199491/codemao.git",
  "branch": "main",
  "zip_url": "https://github.com/874199491/codemao/archive/refs/heads/main.zip"
}
```

如果你后续换了发布仓库，再改 `data/workbench-update-source.json` 即可。

更新脚本会优先使用 Git；如果老师电脑没有安装 Git，就自动下载 GitHub 压缩包更新。

更新时会保留这些本地内容，不会覆盖：

- `data/teacher-workbench-config.json`
- `data/crm-cookies.json`
- `data/workbench-schedules.json`
- 每位老师自己的学员、完课、直播、接龙、反馈等运行缓存数据
- Chrome 登录目录

## 注意事项

- 看板不需要登录 Codex。
- CRM 抓取仍需要老师在 Chrome 中登录自己的 CRM。
- 钉钉写入仍需要当前电脑具备可用的钉钉文档访问权限。
- 第一次配置新老师时，不要直接运行写入任务；先跑状态检查。
