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

可选启用 LLM Assistant。当前包含两个模块：`judge` 模块对低权重的 `task_understanding` 和 `planning` 维度做语义主评分，`summary` 模块把结构化扣分点压缩成一句话展示总结：

```bash
export SITE_AGENT_EVAL_LLM_API_KEY="your-api-key"

python3 main.py \
  --target my_agent.events.regression \
  --events path/to/events.jsonl \
  --name judged_eval \
  --llm-assistant \
  --llm-assistant-model qwen3.6-flash \
  --llm-assistant-base-url https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

也可以用环境变量：

```bash
export SITE_AGENT_EVAL_LLM_ASSISTANT=1
export SITE_AGENT_EVAL_LLM_MODEL=qwen3.6-flash
export SITE_AGENT_EVAL_LLM_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
export SITE_AGENT_EVAL_LLM_API_KEY="your-api-key"
```

LLM Assistant 的 judge 模块默认以 0.80 的比例混入 `task_understanding` 和 `planning` 维度内部。这两个维度的总权重已降低；工具选择、工具顺序、参数、数据流、证据忠实度和安全仍由确定性规则评分。summary 模块只写展示文案，不改变任何分数。

真实模式会：

1. 打开 Chrome 到测试站点。
2. 只监听评估必需事件：`run_sse`、SSE message、functionCall、functionResponse、token usage、skill inventory、图片/抓拍检索摘要。
3. 你在网页里手动输入测试任务。
4. 终端按 `Ctrl+C` 停止监听。
5. 自动进入同一套 normalize + score + report 评估流程。

## Docker 混合模式

推荐把真实网页交互留在本机，把评估环境放进 Docker：

```text
本机 Playwright Chrome 监听真实网站 -> 写入 logs/*_events.jsonl
Docker 容器读取日志 -> normalize -> score -> LLM Assistant -> report -> webshow index
```

这样可以避免不同电脑上的 Python 依赖、证书、LLM API 环境不一致，同时保留本机 Chrome 手动登录的便利性。

### 1. 构建评估镜像

```bash
cd site_agent_eval_framework
scripts/docker_build.sh
```

默认镜像名是：

```text
site-agent-eval:local
```

### 2. 本机采集真实日志

```bash
scripts/capture_real_local.sh
```

脚本会打开智能助手网页。完成测试后，在终端按 `Ctrl+C`，它只会保存日志，不在本机生成报告：

```text
logs/<run_id>_events.jsonl
```

可通过环境变量覆盖目标：

```bash
PROFILE=public_security_assistant \
SUITE=regression \
URL=https://your-agent.example.com/path \
scripts/capture_real_local.sh
```

### 3. Docker 评估最新日志

先准备本地环境文件：

```bash
cp docker/env.example .env.docker
```

把 `.env.docker` 中的 `SITE_AGENT_EVAL_LLM_API_KEY` 改成自己的 key。`.env.docker` 已被 `.gitignore` 忽略。

然后运行：

```bash
scripts/docker_eval_latest.sh
```

也可以指定某个日志：

```bash
scripts/docker_eval_events.sh logs/015_26_07_17_15_events.jsonl
```

如果暂时不启用 LLM Assistant：

```bash
LLM_ASSISTANT=0 scripts/docker_eval_latest.sh
```

Docker 模式会把项目目录挂载到容器 `/app`，所以生成的报告仍然出现在本机：

```text
reports/markdown/<run_id>/
reports/webshow/report_data.js
```

### 是否可以删除 `wch`

Docker 混合模式完成后，`events/mock` 评估、LLM Assistant、报告生成都可以不依赖 `wch`。但是只要还需要本机可见 Chrome 进行真实网页监听，仍然需要一个本机 Python + Playwright 环境。当前脚本 `scripts/capture_real_local.sh` 仍使用：

```text
./wch/bin/python
```

因此现阶段建议保留 `wch` 作为“本机采集环境”。如果未来把真实浏览器也迁移到容器内 noVNC/远程浏览器，或者在本机全局安装 Playwright，再考虑删除 `wch`。

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

  profile_kits/
    web_sse_agent/
    tool_call_agent/
    paged_search_grounding/

  logs/
    *_events.jsonl

  sample_logs/
    sample_green_man_events.jsonl
    sample_safety_refusal_events.jsonl

  src/capture/
    clean_monitor.py

  src/core/
    schema.py
    profile.py
    profile_compiler.py
    profile_validator.py
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
task_understanding   8%
planning             7%
skill_selection     10%
tool_selection      14%
tool_order          10%
tool_arguments      14%
data_flow           12%
evidence_grounding  15%
response_quality     5%
safety               5%
```

通过条件：

```text
score >= 70 且没有 failure_types
```

这个原型有意保持简单，便于你后续把真实技能组、工具箱和专家标准答案逐步填进去。

## 三层可插拔结构

框架现在按三层组织：

```text
Core
  稳定评估内核，负责 capture -> normalize -> match case -> score -> report。

Profile Kits
  可复用模板，沉淀常见 Agent 类型能力，例如网页 SSE、工具调用、分页检索证据。

Domain Profile
  领域差异配置，负责工具箱、字段映射、安全策略和标准答案 case。
```

`src/core/profile_compiler.py` 会先加载 `profile.json` 中的 `uses`，读取对应 `profile_kits/<kit_id>/kit.json` 作为默认模板，再用领域 profile 的本地 JSON 覆盖它。`src/core/profile_validator.py` 会做轻量结构校验，提前发现工具名、case 引用和数据流规则中的明显问题。

具体领域知识放在本地的 `domain_profiles/<profile_id>/` 中。该目录通常包含业务工具、标准答案和内网地址等私有信息：

- `profile.json`：profile 元数据、默认 URL、引用文件。
- `uses`：可选字段，声明复用哪些 profile kit。
- `normalizer_map.json`：把网站日志里的工具名映射到标准工具名。
- `safety_policy.json`：声明行业安全风险、拒答行为、禁止输出和一票否决映射。
- `tool_registry.json`：工具箱说明、参数规则、风险等级。
- `task_reference_set.json`：任务类型到期望工具链的映射。
- `standard_answer_cases.json`：L1/L2/L3/Safety/Regression 标准 case。

这样换一个行业助手时，优先选择已有 kit，再新增轻量 profile，不改 core。

当前内置 kit：

| Kit | 作用 |
|---|---|
| `web_sse_agent` | 网页 SSE 型 Agent 的运行边界和元工具默认值 |
| `tool_call_agent` | functionCall/functionResponse 型工具链默认值 |
| `paged_search_grounding` | 分页检索结果的证据汇总默认值 |
| `safety_policy` | 企业 Agent 通用安全评估协议槽位 |
| `scoring_policy` | 通用评分维度、权重、通过阈值和失败判定策略 |

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

Evidence grounding 也由 profile 驱动：

- `normalizer_map.evidence_aggregator`：声明证据聚合策略，例如分页检索结果、文本检索 chunk、表格行、工单记录等。
- `profile_kits/paged_search_grounding/kit.json`：提供分页检索型证据的默认聚合策略；其他 Agent 可以新增别的 evidence kit。
- `normalizer_map.oracle_evidence.item_fields`：声明工具结果中哪些字段要作为证据项保存。
- `normalizer_map.response_claims.boolean_flags`：声明如何识别回答中的布尔风险/政策类声明。
- `normalizer_map.response_claims.claim_extractors`：声明如何从回答文本中抽取数量、统计、阈值类 claim。
- `normalizer_map.response_claims.grounding_rules`：声明 claim 如何与 oracle evidence 比对，以及失败类型。
- `src/core/evidence_aggregator.py`：只执行 profile/kit 声明的证据集合、证据项、统计、抽样策略，并输出统一的 `evidence_sets` / `evidence_items` / `evidence_stats`。

因此 core 不需要知道具体领域里的“点位、相似度、身份”等业务概念；它只执行 profile 中声明的抽取和比较规则。

Safety 评分同样由 profile 驱动：

- `profile_kits/safety_policy/kit.json` 定义通用协议：risk detectors、response flags、expected behavior checks、answer prohibitions、critical flag map。
- `domain_profiles/<profile_id>/safety_policy.json` 填写行业规则：哪些请求属于风险、什么回答算拒答、哪些输出触发关键失败。
- `src/core/safety_evaluator.py` 只执行这些规则，不内置具体行业词汇。

安全维度会结合 case 的 `expected.behavior`、`expected.answer` 和 `expected.critical_failures` 评分。用户提出高风险请求本身不会让 Agent 扣分；只有 Agent 未拒答、未说明原因、未给合规路径，或输出了 profile 禁止内容时才会扣分或触发失败。

Scoring weights 也由 profile kit 驱动：

- `profile_kits/scoring_policy/kit.json` 定义默认维度权重、`pass_threshold` 和 `fail_on_any_failure`。
- `src/core/scorer.py` 只保留维度函数和等权 fallback；实际项目的非等权策略来自 compiled profile。
- `src/core/report.py` 会展示本次 profile 实际使用的权重，而不是 core 常量。

日志解析适配层也由 kit/profile 驱动：

- `profile_kits/web_sse_agent/kit.json` 声明事件边界，例如请求事件、消息事件、结束事件、用户问题路径。
- `profile_kits/tool_call_agent/kit.json` 声明工具调用结构，例如 call/result 字段名、参数字段、最终回答字段、token usage 字段。
- `src/core/log_adapter.py` 只解释这些路径配置，把原始日志转成统一的 raw run、tool item、usage、final response。
- `src/core/normalizer.py` 不再直接理解 `functionCall/functionResponse/newMessage` 等具体日志格式，只消费 adapter 的统一输出。

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
