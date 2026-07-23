# AUBO ES3 动作库项目

现有 AUBO Python SDK 连接代码已整理为可复用模块，并提供只读诊断和默认 dry-run 的小范围关节测试工具。

> 安全提示：本项目不能替代 AUBO 官方安全功能、风险评估或现场监护。首次真机测试必须由熟悉设备的人员在场，保持急停可达，并清空机械臂工作空间。

## 当前环境

已检测到的本机解释器：

```text
Python 3.11.15
pyaubo-sdk 0.24.1
```

PowerShell 中当前没有全局 `python` 命令，因此下面示例使用解释器绝对路径。也可以在 PyCharm 中继续选择名为 `aubo` 的解释器。

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

当前还没有实现控制器级急停，也没有通过 Python 对机械臂施加硬实时安全保证。急停、保护停止和控制器自身安全机制仍然是最终防线。

## 8. 下一阶段



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
