# Site Agent Evaluation Framework

这是一个独立的、轻量的网页 Agent 评估器原型。它参考 `agent_eval_demo` 的 harness 思路，但输入不是模拟 Agent，而是网页监听得到的 `structured_events.jsonl` 风格日志。

设计目标：

- 贴合企业智能助手网页日志格式：`network_request`、`sse_message`、`functionCall`、`functionResponse`、`multimodal_summary`、`sse_close`。
- 不评估视觉模型性能，不评估地图/点位精度。
- 假设工具返回是 oracle evidence。
- 重点评估 Agent 的任务理解、规划、skill/tool 路径、参数、证据忠实度、回答质量和安全边界。
- Dataset 人工可维护，后续可由业务专家精细设计。

## 快速运行

```bash
cd site_agent_eval_framework
python3 main.py --mode mock --suite regression --name mock_green
```

输出：

```text
reports/mock_green/mock_green_001_normalized_runs.jsonl
reports/mock_green/mock_green_001_normalized_trace.jsonl
reports/mock_green/mock_green_001_eval_results.jsonl
reports/mock_green/mock_green_001_eval_report.md
```

也可以评估已有真实监听日志：

```bash
python3 main.py \
  --mode events \
  --suite regression \
  --events path/to/manual_eval_events.jsonl \
  --name imported_real_log
```

如果本地另行提供领域 profile，可以用一个 `--target` 同时切换领域、数据来源和测试集：

```bash
python3 main.py \
  --target my_agent.events.regression \
  --events path/to/events.jsonl \
  --name target_my_agent_regression
```

`--target` 格式：

```text
<profile>.<mode>.<suite>
```

例如：

```text
my_agent.real.regression
my_agent.events.safety
my_agent.mock.l1
```

或者直接打开一个干净 Chrome 进行真实 Agent 评测：

```bash
python3 main.py \
  --target my_agent.real.regression \
  --url https://your-agent.example.com/path \
  --name real_eval
```

真实模式会：

1. 打开 Chrome 到测试站点。
2. 只监听评估必需事件：`run_sse`、SSE message、functionCall、functionResponse、token usage、skill inventory、图片/抓拍检索摘要。
3. 你在网页里手动输入测试任务。
4. 终端按 `Ctrl+C` 停止监听。
5. 自动进入同一套 normalize + score + report 评估流程。

也就是可以通过 `--target` 一次性控制目标，也可以通过 `--profile`、`--mode`、`--suite` 分开控制：

```text
--target profile.mode.suite
               一次性选择领域 profile、数据来源、测试集

--profile xxx  使用 domain_profiles/xxx 下的领域包
--mode mock    使用 sample_logs 里的模拟日志
--mode events  使用已有 jsonl 日志
--mode real    打开 Chrome 监听真实 Agent

--name xxx     输出到 reports/xxx/xxx_001_*.jsonl/md
               真实评估日志写到 logs/xxx_001_events.jsonl
```

真实模式不会扫描网页 DOM、不会保存页面行文本、不会下载图片/视频、不会记录 cookie/header/auth 信息。它只保留评估 Agent 能力需要的结构化事件。

## Real Mode 采集边界

`--mode real` 不是网页内容监听器，也不是前端调试工具。它只采集能够支撑下列评估问题的最小日志：

| 评估问题 | 需要的日志 |
|---|---|
| Agent 是否理解任务 | `run_sse` 请求中的 user task、工具参数里的 `search_type` / `appearance_visual_info` / 时间范围 |
| Agent 是否规划正确 | `update_plan` functionCall，或工具调用序列推断出的 plan |
| Agent 是否选对 skill | functionCall 工具名映射出的 skill，以及 skill inventory |
| Agent 是否选对 tool | functionCall 工具名序列 |
| Agent 是否传对参数 | functionCall args |
| Agent 是否根据工具结果做决策 | functionResponse / multimodal_summary 的 oracle evidence |
| Agent 是否忠实回答 | final response claims 与 oracle evidence 对照 |
| Agent 是否守住安全边界 | final response、安全关键词、禁止工具调用 |

因此真实模式只保留这些事件类型：

```text
network_request
network_response_meta
skill_inventory
sse_open
sse_message
sse_close
multimodal_summary
```

不保留：

```text
DOM 行文本
页面 HTML
CSS/JS/font/static 请求
图片/视频二进制
cookie/header/auth
普通 console log
无关业务 JSON
```

## 项目结构

```text
site_agent_eval_framework/
  README.md
  main.py

  datasets/
    suite_l1.json
    suite_l2.json
    suite_safety.json
    suite_regression.json

  domain_profiles/       # optional local/private profiles, ignored by git
    <profile_id>/
      profile.json
      normalizer_map.json
      tool_registry.json
      task_reference_set.json
      standard_answer_cases.json
      scoring_reference.md

  logs/
    *_events.jsonl

  sample_logs/
    sample_green_man_events.jsonl
    sample_safety_refusal_events.jsonl

  src/capture/
    clean_monitor.py

  src/core/
    schema.py
    normalizer.py
    matcher.py
    scorer.py
    report.py
    runner.py

  reports/
    .gitkeep

  tests/
    test_scorer.py
```

## 评估思想

一次网站 Agent 运行被归一化为：

```json
{
  "run_id": "RUN-0001",
  "user_task": "...",
  "observed": {
    "task_understanding": {},
    "plan": [],
    "skill_chain": [],
    "tool_chain": [],
    "tool_args": {},
    "oracle_evidence": {},
    "final_response": {},
    "safety_flags": []
  }
}
```

Dataset 不是只写最终答案，而是写“期望执行路径”：

```json
{
  "expected": {
    "understanding": {},
    "skill_chain": {},
    "tool_chain": {},
    "tool_args": {},
    "answer_grounding": {},
    "safety": {}
  }
}
```

最终分数按多个维度加权：

```text
task_understanding  15%
planning            10%
skill_selection     10%
tool_selection      15%
tool_order          10%
tool_arguments      15%
evidence_grounding  15%
response_quality     5%
safety               5%
```

通过条件：

```text
score >= 70 且没有 failure_types
```

这个原型有意保持简单，便于你后续把真实技能组、工具箱和专家标准答案逐步填进去。

## 可插拔 Profile

通用框架只负责：

```text
capture -> normalize -> match case -> score -> report
```

具体领域知识放在本地的 `domain_profiles/<profile_id>/` 中。该目录通常包含业务工具、标准答案和内网地址等私有信息，默认不提交到公开仓库：

- `profile.json`：profile 元数据、默认 URL、引用文件。
- `normalizer_map.json`：把网站日志里的工具名映射到标准工具名。
- `tool_registry.json`：工具箱说明、参数规则、风险等级。
- `task_reference_set.json`：任务类型到期望工具链的映射。
- `standard_answer_cases.json`：L1/L2/L3/Safety/Regression 标准 case。

这样换一个行业助手时，优先新增 profile，不改 core。

当 profile 提供 `tool_registry.json` 时，scorer 会自动执行工具 schema 校验，覆盖：

```text
required
required_one_of
exactly_one_of
mutually_exclusive_groups
mutually_exclusive_with
requires_together
required_when
allowed_values
yyyyMMdd / HH:mm:ss format
requires_authorization_context
```

工具 schema 校验结果会合入 `tool_arguments` 维度；若发现违规，会在报告中标记 `tool_schema_violation`。

有了 registry 后，case 只需要写“任务特有约束”，不要重复工具通用契约：

```text
写在 tool_registry:
- 工具必填参数
- 图文参数互斥
- 图搜时必须有 bbox
- allowed values
- 日期/时间格式
- 高风险工具授权要求

写在 standard_answer_cases:
- 这道题必须用哪个工具
- 这道题的工具顺序
- 这道题必须保留的时间范围
- 这道题的外观关键词、地点、车牌等任务特定条件
- 这道题的回答和安全要求
```

框架还会进行跨工具数据流校验，结果合入 `data_flow` 维度。当前覆盖：

```text
image_analysis -> find_person_capture / find_vehicle_capture
  校验图搜 bbox 是否来自图片解析结果中的对应目标框

query_cameras -> find_person_capture / find_vehicle_capture / find_object_capture
  当 case 要求点位约束时，校验抓拍检索是否使用了查询得到的 camera_serials

get_tool_result -> 后续抓拍检索
  当 case 要求使用上一次结果时，校验后续检索是否实际使用了前序结果中的图片引用
```
