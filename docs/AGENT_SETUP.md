# 论文雷达 · 给 AI agent 的安装指令

> **用法**：把本文件的内容整段复制给你的 AI agent（WorkBuddy / Claude Code / Cursor 等能读写文件、能建定时任务的都行），然后什么都不用管。
> **不要一次问一个问题**：先按「第 0 步」把该确认的一次问完，之后所有安装、配置、试跑、建任务都由你完成。

---

## 你的任务

在一台机器上把「论文雷达」装好并跑通一期，然后创建两条定时任务。**企业微信推送是可选功能，只有用户明确说要时才配置。**

这个仓库做三件事：每天从指定顶刊/顶会取 10 篇论文做成六问速读、挑 1 篇写成精读长文、每周日出一份综述。产物是单文件 HTML + 本地看板。

---

## 第 0 步 · 一次问完这三件事

**Q1. 研究方向和取文范围（必问）。** 这个仓库**刻意不预置任何方向**，`config.json` 也不在仓库里 —— 必须由用户回答后才能跑。请一次问清：

- **盯哪些研究方向？** 可以多个并列（建议 2–3 个），命中任一方向即入选。中文名即可，例如「图神经网络 / 因果推断」。
- **要盯哪些期刊和会议？** 写全名最稳（如 `IEEE Transactions on Automatic Control`、`Neural Information Processing Systems`），也可以直接给 ISSN 或 OpenAlex source id。
- **这份日报写给谁看？** 读者定位，会注入提示词。默认「该方向的研究生与研究者」。

**关键词不要问用户手写** —— 向导会调用模型把方向名自动展开成 20–30 个英文检索词和一条 arXiv 查询串。你只需在展开后把关键词列表给用户过一眼，用户说不对再手工替换。

**Q2. 模型接口。** 需要兼容 OpenAI 的 `base_url` + `api_key`，以及模型名。默认推荐 DeepSeek（`https://api.deepseek.com/v1`，主力 `deepseek-v4-pro`，轻量 `deepseek-flash`）。**API Key 必须由用户提供，不要编造、不要写进任何会被提交的文件。**

**Q3. 是否需要推送到企业微信。** 说明这是可选功能、不配也能正常出日报。只有用户说要，才走第 6 步。

问完先不要动手，把三件事复述一遍让用户确认。

---

## 第 1 步 · 安装

在项目根目录执行（Windows 用 `.venv\Scripts\python.exe`，macOS/Linux 用 `./.venv/bin/python`）：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe setup.py --check
```

`setup.py --check` 会打印环境体检结果。**要求 `pypdf` 和 `latex2mathml` 都是 ✓**（前者抓 PDF 全文，后者渲染公式）。`websockets` 显示 ○ 是正常的 —— 那是企业微信的可选依赖。

如遇 pip 下载失败，先换源重试，不要跳过依赖硬跑。

---

## 第 2 步 · 配置模型

```bash
.venv/Scripts/python.exe setup.py --base-url <URL> --api-key <KEY> --model <主力模型> --flash-model <轻量模型>
```

它会写入 `data/llm.json` 并自动跑一次连通测试。**必须看到「✓ 连通正常」再往下走。**

失败时按报错对症：`401` = Key 或 base_url 不匹配；`402/insufficient` = 余额不足；`404/model` = 模型名不存在；`timeout/connect` = 网络或 base_url 写错（一般结尾要带 `/v1`）。

> 这一步只写 `data/llm.json`。研究方向留给下一步 —— 需要模型可用了才能自动展开关键词。

---

## 第 3 步 · 配置研究方向与取文范围

模型通了之后，用用户在第 0 步给出的答案生成 `config.json`：

```bash
.venv/Scripts/python.exe setup.py --topic "图神经网络" --topic "因果推断" \
    --venue "Nature Machine Intelligence" --venue "IEEE Transactions on Pattern Analysis and Machine Intelligence" \
    --audience "课题组的研究生"
```

参数说明：

| 参数 | 说明 |
|---|---|
| `--topic` | 可重复，最多 5 个。中文或英文都行；模型会展开成 `key` / `name` / `short` / `keywords` / `arxiv` |
| `--venue` | 可重复。期刊名自动查 ISSN，会议名自动查 OpenAlex source id；也可写 `issn:0018-9286`、`S4210221977` |
| `--audience` | 读者定位，不填默认「该方向的研究生与研究者」 |
| `--daily` | 每天几篇，默认 10 |
| `--min-date` | 发表时间下限，默认 `2020-01-01` |

如果用户就在旁边，也可以直接跑交互式，让用户自己回答：

```bash
.venv/Scripts/python.exe setup.py --reconfigure
```

三种会失败的情况，按提示处理：

1. **关键词展开失败**（模型报错）→ 交互模式下会自动转为让用户手工填英文关键词；非交互模式下该方向会被跳过，配好模型后重跑 `--reconfigure`。
2. **期刊名查不到** → 让用户给全名或 ISSN（Crossref 按名称模糊匹配，简称常常匹配不上）。
3. **会议名查不到** → 会议查询走 OpenAlex，免费额度按 IP 共享、白天常被用光。此时要么稍后重跑，要么让用户提供 `S` 开头的 source id（在 openalex.org 搜该会议，URL 末段就是）。

**验收**：`config.json` 里 `topics` 与 `venues` 都非空、`field_name` 是用户给的方向名、每个 topic 的 `keywords` 不少于 8 个。少了就让用户补，**不要只留一两个宽泛词**，召回会崩。

---

## 第 4 步 · 试跑并验收

先不花钱地验证取文：

```bash
.venv/Scripts/python.exe pipeline.py --dry-run
```

看日志里每个方向各捞到几篇。**如果某个方向是 0 篇，先回去加关键词或放宽 `window_days_ladder`，不要直接跑正式流程。**

正式生成一期：

```bash
.venv/Scripts/python.exe pipeline.py
```

**验收标准（逐项检查，不要凭日志猜）：**

| 检查项 | 通过条件 |
|---|---|
| `data/reports/<今天>.json` | `papers` 数组长度 = `config.json` 里的 `daily_target` |
| 同上，每篇 `analysis` | `problem` / `data_method` / `key_result` / `innovation` / `open_problems` / `next_steps` 六个字段**均非空** |
| `data/reports/<今天>.html` | 存在且大于 20KB |
| `data/deepread/<今天>.html` | 存在，且正文里没有残留的裸 `$`（说明公式已转 MathML） |
| 方向覆盖 | 每个 topic 至少 1 篇（理想 ≥2） |

有空格或精读缺失时，用 `--resume --force` 补（复用已生成的速读，不重复消耗 token）：

```bash
.venv/Scripts/python.exe pipeline.py --resume --force
```

---

## 第 5 步 · 创建两条定时任务

时间默认 **每天 09:00** 与 **每周日 09:00**（与用户确认后再建）。

如果宿主支持自动化/定时任务（WorkBuddy 的自动化、`crontab`、任务计划程序等），建两条，prompt 用下面的原文（**把路径替换成实际项目路径**）：

### 任务一：每天 09:00 生成日报

```
生成「论文雷达」当天汇报：指定顶会顶刊的 10 篇文献六问调研 + 1 篇精读长文。

工作目录：<项目绝对路径>

步骤：
1. 运行 .venv/Scripts/python.exe -u pipeline.py（通常 3–6 分钟）。
2. 若日志最后一行是「已存在，跳过」，说明当天产物已生成，直接跳到第 4 步。
3. 校验产物（用工具实际读取，不要凭日志猜）：
   - data/reports/<今天 YYYY-MM-DD>.json 的 papers 数量等于 daily_target，且每篇 analysis 的
     problem / data_method / key_result / innovation / open_problems / next_steps 六个字段均非空；
   - data/reports/<今天>.html 存在且大于 20KB；
   - data/deepread/<今天>.html 存在。
   若有空项或精读缺失，执行 .venv/Scripts/python.exe -u pipeline.py --resume --force。
4. 回复用户 4–6 行，不要复述论文内容：
   - 本期 10 篇在各方向的分布；
   - 今日精读选了哪篇：期刊 + 中文标题 + 一句结论 + 是否拿到全文；
   - 数据源状态（OpenAlex 正常，或走了 Crossref 兜底）；
   - 若配置了企业微信，说明推送结果（读 data/wecom_log.jsonl 最后一条）；
   - 报告文件路径。

约束：不要手工改 config.json 或 prompts/。只有脚本非零退出且没有任何产物时，才报告失败原因（网络 / 配额 / 接口）。
```

### 任务二：每周日 09:00 生成周报

```
生成「论文雷达」本周综述：把最近 7 天日报聚合成技术脉络、本周 Top3 与空缺。

工作目录：<项目绝对路径>

步骤：
1. 运行 .venv/Scripts/python.exe -u pipeline.py --weekly（约 1–3 分钟）。
2. 校验 data/weekly/week-<今天>.html 存在且大于 5KB；同名 .json 里 threads / top3 / gaps 三个字段非空。
3. 回复用户 3–5 行：本周三条技术主线各一句话、本周最值得读的三篇（标题 + 期刊简称）、
   1–2 条「没人做但该做」的空缺。不要复述全文。

若报错「最近 7 天没有可用的日报数据」，说明本周日报不足，如实说明并结束，不要编造内容。
```

### 如果宿主不支持定时任务

用系统调度，并告诉用户在哪看日志：

```cron
0 9 * * *  cd <项目路径> && ./.venv/bin/python pipeline.py >> data/cron.log 2>&1
0 9 * * 0  cd <项目路径> && ./.venv/bin/python pipeline.py --weekly >> data/cron.log 2>&1
```

---

## 第 6 步 · 可选：企业微信推送

**只有用户明确要求时才做。** 不配置时所有推送调用会静默跳过，不影响日报。

```bash
.venv/Scripts/python.exe -m pip install websockets
.venv/Scripts/python.exe wecom_bind.py --bot-id <BotID> --secret <Secret>
```

绑定过程需要**用户本人操作**：脚本连上长连接后有 180 秒窗口，这期间请让用户去私聊机器人或在其所在群里 @ 它发一句话。抓到会话后脚本会自动存盘、回一条「绑定成功」、打开推送开关。

两个已知陷阱，遇到时不要误判：

- `errcode=846607 send msg frequency limit exceeded` **不是超频**，而是「该会话还没给机器人发过消息」这个前置条件没满足。`enter_chat` 这类事件回调不算交互。
- 服务端推送的 `aibot_msg_callback` 帧**也带 `headers.req_id`**，不能靠「有没有 req_id」区分响应与事件。

配置完成后，推送会自动跟随日报/周报，附件名为 `每日十篇泛读_MM.DD.html` 与 `每日精读_MM.DD.html`。

---

## 第 7 步 · 交付汇报

装完后向用户汇报（简洁，5–8 行）：

1. 环境状态（Python 版本、依赖是否齐全、模型连通结果）
2. 当前研究方向与取文范围（明确说明这是用户回答后生成的，仓库本身不带方向）
3. 首期产出：篇数、方向分布、精读选了哪篇、文件路径
4. 两条定时任务的时间与状态
5. 企业微信是否启用
6. 怎么手动跑一次、怎么看板

---

## 边界（不要越界）

- **绝不**把 `data/llm.json`、`data/wecom.json` 的内容打印到聊天里或提交到 git。
- **绝不**为了让流程"跑通"而编造论文、数字或结论；取文失败就如实报错。
- 不改 `pipeline.py` / `radar/*.py` 的业务逻辑，除非用户明确要求修 bug。
- `config.json` 的 `topics` 只按用户指示修改，不要自作主张扩写关键词。
- 企业微信是可选功能，**默认不配**。
