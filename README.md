# AUBO ES3 动作库项目

## HCI 多轮猜杯研究系统（Ubuntu 20.04）

> Ubuntu 20.04 已结束标准维护期。2026 年部署应启用 Ubuntu Pro/ESM 并安装当前
> 安全更新；支持周期见 [Ubuntu Releases](https://wiki.ubuntu.com/Releases)。

本仓库现在包含需求文档 v0.9 对应的可运行研究平台：

- `robot_game/`：多轮状态机、三种实验条件、动作白名单、LLM 输出验证、
  单机械臂动作队列和 AUBO/夹爪适配器；
- `robot_game/static/`：研究人员使用的 WebSocket Wizard-of-Oz 控制台；
- `config/study.example.json`：实验、安全、工位、轨迹和 LLM 配置；
- `logs/<session_id>/`：按会话组织的可读日志、摘要和可校验的哈希链 JSONL；
- `vendor/pyaubo_sdk/`：Ubuntu 20.04 / Conda CPython 3.10 / x86-64 的官方
  `pyaubo-sdk 0.24.1` wheel 与 SHA-256 校验文件；
- `scripts/setup_ubuntu20.sh`：Ubuntu 20.04 部署环境安装与离线 SDK 安装；
- `scripts/verify_event_log.py`：实验事件日志完整性验证。

### 完整运行一次不连接机械臂的实验（mock + 可选真实 OpenAI）

下面这套命令不会连接机械臂，适用于 Ubuntu 20.04 x86-64，也适用于 Apple
Silicon 上的 Ubuntu ARM64 虚拟机。首次运行时，在项目根目录执行：

```bash
conda env create --name aubo --file environment.yml
conda activate aubo
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cp config/study.example.json config/study.local.json
cp config/robot.example.json config/robot.local.json
```

如果名为 `aubo` 的环境已经存在，使用下面的更新命令代替 `conda env create`：

```bash
conda env update --name aubo --file environment.yml
conda activate aubo
python -m pip install -r requirements.txt
```

#### 申请并配置 OpenAI API Key

OpenAI API 与 ChatGPT Plus/Pro 使用不同的计费系统；已有 ChatGPT 订阅不等于已经
开通 API 额度。按以下步骤申请：

1. 登录或注册 [OpenAI API Platform](https://platform.openai.com/)；
2. 打开 [API Billing Overview](https://platform.openai.com/settings/organization/billing/overview)，
   按页面提示添加付款方式或购买 API credits；
3. 打开 [API Keys](https://platform.openai.com/api-keys)，选择用于本研究的 Project，
   点击 `Create new secret key`，建议命名为 `aubo-es3-study`；
4. 创建后立即把完整密钥保存到受保护的密码管理器。完整 secret key 只在创建时显示，
   丢失后需要撤销旧密钥并创建新密钥；
5. 不要把密钥写入 `study.local.json`、Prompt、源码、Git、实验日志或聊天消息。只在
   启动服务器的终端中设置环境变量：

```bash
export OPENAI_API_KEY="粘贴刚创建的密钥"
```

官方参考：[Developer quickstart](https://platform.openai.com/docs/quickstart/make-your-first-api-request)、
[API key help](https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key)、
[API key permissions](https://help.openai.com/en/articles/8867743-assign-api-key-permissions)。

设置完成后，先做一次不会连接机械臂的付费预检：

```bash
export OPENAI_API_KEY="你的OpenAI API密钥"
export AUBO_ROBOT_MODE=mock
python scripts/verify_openai_llm.py --study-config config/study.local.json
```

然后启动研究人员控制网页：

```bash
export AUBO_ROBOT_MODE=mock
python main.py study \
  --study-config config/study.local.json \
  --robot-config config/robot.local.json \
  --host 127.0.0.1 \
  --port 8000
```

其中 `--robot-config`、`--host` 和 `--port` 可以省略，因为它们的默认值分别是
`config/robot.local.json`、`127.0.0.1` 和 `8000`。推荐的最短启动命令是：

```bash
export AUBO_ROBOT_MODE=mock
python main.py study --study-config config/study.local.json
```

不要在需要本地实验配置时省略 `--study-config`：单独运行 `python main.py study`
会读取仓库提供的 `config/study.example.json`，而不是 `config/study.local.json`。
所有默认值和说明也可通过 `python main.py study --help` 查看。

保持终端运行，浏览器打开 `http://127.0.0.1:8000`。输入 Participant ID 和唯一的
Session ID，选择 `Task-only`、`History-aware rule` 或 `LLM-based`，保持
`Formal study` 不勾选，然后点击 `Start locked session`，按页面亮起的按钮完成每轮。
`AUBO_ROBOT_MODE=mock` 会强制使用内存中的软件机械臂和软件夹爪，因此即使
`robot.local.json` 中存在机器人 IP，也不会建立机械臂连接。

如果不想产生 OpenAI 费用，不设置 `OPENAI_API_KEY`，只测试 Task-only 和
Rule-based 条件；LLM-based 条件会在会话开始前明确拒绝缺少密钥的请求。

Ubuntu 20.04 x86-64 真机部署也可以用一键脚本完成相同 Conda 环境和 SDK 安装：

```bash
chmod +x scripts/*.sh
./scripts/setup_ubuntu20.sh
conda activate aubo
```

安装脚本要求预先安装 Miniconda 或 Anaconda，并根据 `environment.yml` 创建统一命名为
`aubo` 的 Conda 环境。项目不再使用 `python -m venv` 或 `source .venv/bin/activate`。
若尚未安装 Conda，按
[Conda Linux 安装指南](https://docs.conda.io/projects/conda/en/stable/user-guide/install/linux.html)
安装并重新打开终端；若 `conda activate` 提示 shell 未初始化，执行 `conda init bash`
后重新打开终端。

示例配置固定为 `robot_mode=mock`、`formal_study=false`、`time_scale=0.02`。
LLM 条件默认使用 `gpt-5.6-terra`、低推理强度和严格 Structured Outputs；模型、推理
强度、输出token上限及5秒阶段预算均可在 `config/study.local.json` 调整。密钥只从
环境变量读取，不写入配置、prompt或实验日志。需要完全离线调试时，可把
`llm.provider` 临时改为 `mock`，但正式研究禁止Mock LLM。

OpenAI 的完整 Prompt 位于 `config/openai_prompt.txt`。角色说明、约束、Few-shot
示例、重试说明以及动态上下文/Schema 占位符都在这一个文件中；Python 代码只读取
并填充模板。修改模板后无需修改代码，模板 SHA-256 会自动写入实验日志。

若研究人员不在本机访问，应通过反向代理
提供 HTTPS，并设置高强度 `AUBO_RESEARCHER_TOKEN`；服务拒绝在无 token 时绑定到
非本机地址。

### 切换真机前的强制门

不要直接把示例轨迹用于真机。复制为 `config/study.local.json` 后，至少完成：

1. 先离线审查，再在清空工位、最低安全速度和实体急停可达时逐点验证三只杯的
   `pre/pick/lift`、放置、撤回和 Home；
2. 在清空工位、低速、急停可达且有人监护的情况下逐点标定真机；
3. 标定五种表达动作的相对关节模板，确认无自碰撞、桌面/杯子/用户碰撞和奇异位形；
4. 按 ES3、控制器和现场工作空间更新两份配置中的关节限位、速度、加速度和单命令增量；
5. 配置实际夹爪。当前真机适配器支持控制器标准数字输出，可选数字输入闭合反馈；
6. 将 `workcell.calibrated` 与 `expressive_calibrated` 设为 `true`，
   `runtime.robot_mode` 设为 `real`，`time_scale` 设为 `1`；
7. 设置研究人员 token，并在正式研究配置中锁定 `formal_study=true` 和 condition。

任一校准标志缺失、夹爪仍是 mock、正式模式使用 mock LLM、正式模式加速时间、
或正式模式没有 token 时，系统会在连接机器人之前拒绝启动。软件
`emergency_stop` 调用 SDK `stopJoint`，但它不能替代控制器和实体急停。

### 运行时日志

每次会话生成：

```text
logs/S001/
├── events.jsonl          # 原始、顺序化、SHA-256 链式事件（每条写入后 fsync）
├── runtime.log           # 便于调试的旋转文本日志
└── session_summary.json  # 当前状态、轮次、结果、invalid 原因
```

`events.jsonl` 记录会话/轮次、WoZ 发送与 ack、状态转换、LLM prompt 版本/hash/
上下文/原始输出/解析/延迟、动作函数/variant/请求与校验后参数、SDK 返回码、
动作真实起止时间，以及每次动作前可获得的机器人安全模式、碰撞、软限位、关节
位置/速度/加速度/力矩/电流/温度、TCP、控制柜温湿度和电压电流。旧 SDK 不支持的
字段以 `null` 或 `read_error` 明确记录，不会伪造数值。

验证一份日志：

```bash
python scripts/verify_event_log.py logs/S001/events.jsonl
```

在 mock 状态机中将每条固定杯子路径各运行 20 次，并核对无错误事件且最终返回
Home：

```bash
python scripts/run_mock_acceptance.py
```

完整架构、消息协议、配置和现场投产步骤见
[研究系统部署与校准指南](docs/study_system.md)。
用于组内讲解、情感动作调参与 WoZ 现场操作的中文说明见
[代码架构、情感动作调试与 WoZ 实验指南](docs/CODE_ARCHITECTURE_WOZ_GUIDE_CN.md)。
每个维护文件的职责见 [Project file guide](FILE_GUIDE.md)，需求覆盖关系见
[Requirements traceability](docs/requirements_traceability.md)。

---

## 原有独立动作工具

现有 AUBO Python SDK 连接代码已整理为可复用模块，并提供只读诊断和默认 dry-run 的小范围关节测试工具。

> 安全提示：本项目不能替代 AUBO 官方安全功能、风险评估或现场监护。首次真机测试必须由熟悉设备的人员在场，保持急停可达，并清空机械臂工作空间。

## Python 环境

研究部署以 Ubuntu 20.04 x86-64、Conda CPython 3.10 和仓库内锁定的
`pyaubo-sdk 0.24.1` 为准。本节保留的 PowerShell 命令仅用于原有独立工具；
正式研究应使用上方 Ubuntu `aubo` Conda 环境和 `study` 服务。

## 目录

```text
AUBO/
├─ main.py                     # 唯一的根目录入口和中文交互菜单
├─ config/
│  ├─ robot.example.json       # 可提交的配置模板
│  └─ robot.local.json         # 本机配置，已被 .gitignore 忽略
├─ docs/
│  ├─ implementation_plan.md
│  └─ safety_checklist.md
├─ host_tools/
│  ├─ diagnose_robot.py        # 只读诊断实现
│  ├─ safe_joint_test.py       # 小范围关节测试实现
│  ├─ nod_action.py            # 点头动作实现
│  └─ cup_action.py            # 水杯往返动作实现
├─ src/aubo_sdk_client/        # 配置、SDK 客户端和安全校验
├─ tests/
├─ requirements.txt
└─ .gitignore
```

## 1. 统一入口

根目录现在只保留一个 Python 入口：

```text
main.py
```


菜单可以选择：

1. 只读连接诊断，并读取当前六轴关节位置和 TCP 位姿；
2. 小范围关节测试；
3. 点头动作；
4. 水杯位置往返。

菜单只负责收集选择和参数，真正的功能分别位于 `host_tools/`，底层配置、SDK 客户端和安全校验位于 `src/aubo_sdk_client/`。其中第 1 项会通过只读状态接口同时显示当前 J1～J6 关节角，以及 TCP 的 X/Y/Z 和 RX/RY/RZ；不会上电、启动或发送运动命令。

也可以使用统一入口的子命令模式：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py diagnose --json
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py joint --delta 0,0,0,0,0,0.02
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py nod --scale 1.0
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py cup --cup-hold 2.0
```

## 2. 配置

已创建本地配置：

```text
config/robot.local.json
```

该文件已被 `.gitignore` 排除。请检查其中：

- IP 和端口；
- 用户名和密码；
- 请求超时；
- 关节软限位；
- 单次运动最大增量；
- 测试速度和加速度。

### 重要：软限位仍需核实

模板中的 ±π 只是 M0 的保守占位范围，**不是对 ES3 官方关节限位的声明**。在执行真实运动前，请根据以下材料更新：

1. 当前 ES3 型号的官方手册；
2. 当前控制器/ARCS 中显示的实际软限位；
3. 现场工装、夹爪、线缆和安全区域形成的更严格限制。

敏感配置也可以用环境变量覆盖：

```text
AUBO_ROBOT_IP
AUBO_ROBOT_PORT
AUBO_USERNAME
AUBO_PASSWORD
AUBO_REQUEST_TIMEOUT_MS
```

## 3. 只读诊断

本命令只连接、登录和读取状态，不会调用上电、启动或运动接口：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py diagnose
```

JSON 输出：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py diagnose --json
```

也可以直接运行 `main.py`，在菜单中选择第 1 项。

## 4. 安全运动预览

下面只连接机器人并读取当前位置，然后预览 J6 增加 0.02 rad 的目标。默认不会上电、启动或运动：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py joint `
  --delta 0,0,0,0,0,0.02
```

也可以在 `main.py` 菜单中选择第 2 项并直接回车使用默认增量。

程序会打印：

- 当前关节角；
- 关节增量；
- 目标关节角；
- rad 与 degree；
- 测试速度和加速度；
- 是否违反单次增量或软限位。

## 5. 真实运动

只有同时提供以下两个参数才可能进入真实运动路径：

```text
--execute
--confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

完整命令示例：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py joint `
  --delta 0,0,0,0,0,0.02 `
  --execute `
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

执行顺序为：

1. 连接并读取当前位置；
2. 第一次软限位和增量检查；
3. 打印完整运动预览；
4. 检查显式确认文本；
5. 上电和启动；
6. 重新读取当前位置；
7. 第二次软限位和增量检查；
8. 使用配置中的低速度、低加速度调用 `moveJoint`。

真实运动前必须完成 [安全检查清单](docs/safety_checklist.md)。

## 6. 离线测试

测试不会连接真机：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe -m unittest discover -s tests -v
```

语法检查：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe -m compileall src host_tools tests
```

## 7. 安全设计

M0 当前包含：

- 凭据移出 Git 跟踪源码；
- 配置校验；
- SDK 延迟导入，允许离线测试；
- 连接、登录、机器人发现和状态读取异常封装；
- 上下文管理和自动断开；
- 默认 dry-run；
- 显式 `--execute`；
- 固定确认文本；
- 六关节向量长度检查；
- NaN/无穷大检查；
- 当前姿态软限位检查；
- 单次增量限制；
- 目标姿态软限位检查；
- 上电/启动后重新读取并验证目标；
- 低速度、低加速度配置。

原有独立动作工具没有控制器级急停。新的研究运行时会绕过普通动作队列调用
SDK `stopJoint`，但 Python 和网络调用不提供硬实时保证；实体急停、保护停止和
控制器自身安全机制仍然是最终防线。

## 8. 研究系统范围

本研究不要求ROS 2、MoveIt、Gazebo、三维仿真、视觉分拣、模仿学习或强化学习。
[早期完整实施计划](docs/implementation_plan.md)仅作为历史路线图保留，不属于当前
HCI猜杯研究系统的交付范围。
## 9. 全身点头动作

直接运行根目录的 `main.py`，在菜单中选择第 3 项；或者使用统一入口子命令：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py nod --scale 1.0
```

默认命令只做离线预演，不连接、不上电、不运动。全身模式让 J1～J6 协调参与：J2、J3、J5 形成明显的抬头/俯身，J1、J4、J6 配合摆动。动作的最大单关节相对偏移约为 `0.12 rad`（约 `6.88°`），整体轨迹会根据 `max_delta_rad` 自动拆分成多个小 waypoint，动作结束后回到本次动作的起始姿态。

动作倍率可以设置为 `0.25`～`1.5`；全身点头默认速度为 `0.35 rad/s`、加速度为 `0.70 rad/s²`。代码硬性限制最高速度为 `0.50 rad/s`、最高加速度为 `1.00 rad/s²`。过渡 waypoint 不额外停顿，五个关键姿态的停顿合计约 `0.55s`。原来的单腕小幅点头仍可通过以下命令使用：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py nod --style wrist
```

真实运动必须在菜单中输入 `MOVE`，或显式提供 `--execute` 和固定确认文本。注意：程序以启动后的当前姿态作为动作中位，不会盲目运动到 `[0,0,0,0,0,0]`。固定 Home 复位必须先提供现场确认过的 J1～J6 Home 关节角。`config/robot.local.json` 里的 ±π 仍是占位软限位，未核实真实限位和碰撞空间前不应执行真机动作。

## 10. 水杯位置往返动作

直接运行根目录的 `main.py`，在菜单中选择第 4 项；或者使用统一入口子命令：

```powershell
& C:\Users\Lenovo\anaconda3\envs\aubo\python.exe main.py cup --cup-hold 2.0
```

默认命令只做离线预演。真实执行时，程序会在真正开始运动前读取并记录当前六关节姿态，然后按以下顺序执行：

```text
启动时正常姿态 -> 水杯关节姿态 -> 启动时正常姿态
```

水杯关节目标固定为：

```text
[0.31908, -0.588733, 1.432912, -0.222756, 1.742074, 0.151014]
```

参考 TCP 位姿为：

```text
[0.413412, 0.028213, 0.154102, -2.450776, -0.03654, 1.6655]
```

默认速度为 `0.35 rad/s`、加速度为 `0.70 rad/s²`。程序只在到达水杯位置后停顿 `2s`，随后立即返回；途中和返回到位后都不设置人为停顿。到达水杯后会打印实际 TCP 与参考 TCP 的位置/姿态误差，但仍以关节到位为主要运动判据。

> 该动作是两段直接关节运动，没有中间避障点。第一次真机运行前，必须确认从当前正常姿态到水杯姿态的整条关节空间路径不会碰撞。当前 `config/robot.local.json` 中的 ±π 软限位仍需按真机核实。
