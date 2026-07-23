# AUBO Python 动作库

这是一个面向 AUBO 六轴机械臂的 Python 动作库示例。项目将控制器连接、配置读取、安全校验和具体动作分层组织，并通过统一入口提供只读诊断、关节小步测试、点头动作和水杯位置往返动作。

项目默认采用保守策略：除只读状态读取外，动作命令默认只预演；只有显式授权后才会进入真机运动流程。

> **安全提示**：本项目不能替代 AUBO 官方安全功能、设备手册、风险评估或现场监护。任何真机动作都必须由熟悉设备的人员在场执行，确保工作空间无人、急停可达，并提前验证关节限位、工具、夹具、线缆和完整运动路径。

## 功能

| 功能 | 命令 | 默认行为 |
| --- | --- | --- |
| 只读诊断与当前位姿读取 | `diagnose` | 连接控制器，只读取状态，不上电、不运动 |
| 小范围关节测试 | `joint` | 读取当前位置并预览目标，不上电、不运动 |
| 点头动作 | `nod` | 完全离线生成轨迹，不连接控制器 |
| 水杯位置往返 | `cup` | 完全离线检查动作计划，不连接控制器 |

只读诊断会输出：

- 机器人名称和上电状态；
- 当前 J1～J6 关节角，单位为 rad 和 degree；
- 当前 TCP 位置 X/Y/Z，单位为 m；
- 当前 TCP 姿态 RX/RY/RZ，单位为 rad 和 degree。

## 项目结构

```text
AUBO/
├─ main.py                     # 唯一启动入口和交互菜单
├─ config/
│  └─ robot.example.json       # 配置模板
├─ docs/
│  └─ safety_checklist.md      # 真机运动安全检查清单
├─ host_tools/
│  ├─ diagnose_robot.py        # 只读诊断与位姿读取
│  ├─ safe_joint_test.py       # 小范围关节测试
│  ├─ nod_action.py            # 点头动作
│  └─ cup_action.py            # 水杯位置往返动作
├─ src/aubo_sdk_client/
│  ├─ client.py                # AUBO SDK 客户端封装
│  ├─ config.py                # 配置加载与校验
│  └─ safety.py                # 关节目标和增量安全检查
├─ tests/                      # 离线单元测试
├─ requirements.txt
└─ .gitignore
```

调用关系如下：

```text
main.py
  └─ host_tools：具体动作与工具流程
       └─ src/aubo_sdk_client：连接、配置和安全校验
```

## 环境要求

- Python 3.10 或更高版本；
- 与当前 Python 版本和操作系统匹配的 AUBO Python SDK；
- 可以访问机器人控制器的网络环境。

创建虚拟环境并安装依赖：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux/macOS：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

如果厂商 SDK 无法通过 `pip` 安装，请按照 AUBO SDK 随附文档，将与系统及 Python 版本匹配的 SDK 安装到当前环境中。

## 配置

复制配置模板：

Windows PowerShell：

```powershell
Copy-Item config/robot.example.json config/robot.local.json
```

Linux/macOS：

```bash
cp config/robot.example.json config/robot.local.json
```

然后修改 `config/robot.local.json`：

```json
{
  "robot": {
    "ip": "192.168.2.1",
    "port": 30004,
    "username": "aubo",
    "password": "CHANGE_ME",
    "request_timeout_ms": 3000
  },
  "safety": {
    "joint_count": 6,
    "joint_min_rad": [-3.14159265, -3.14159265, -3.14159265, -3.14159265, -3.14159265, -3.14159265],
    "joint_max_rad": [3.14159265, 3.14159265, 3.14159265, 3.14159265, 3.14159265, 3.14159265],
    "max_delta_rad": [0.05, 0.05, 0.05, 0.05, 0.05, 0.05],
    "max_velocity_rad_s": 0.10,
    "max_acceleration_rad_s2": 0.10,
    "power_on_wait_s": 3.0,
    "startup_wait_s": 2.0
  }
}
```

`config/robot.local.json` 已被 `.gitignore` 排除，不应提交真实密码。

也可以使用环境变量覆盖连接信息：

```text
AUBO_ROBOT_IP
AUBO_ROBOT_PORT
AUBO_USERNAME
AUBO_PASSWORD
AUBO_REQUEST_TIMEOUT_MS
```

> 模板中的 `±π` 只是示例值，不代表任何具体型号的官方关节限位。真机执行前必须根据机器人型号、控制器配置和现场碰撞空间填写正确的软限位。

## 快速开始

启动中文交互菜单：

```bash
python main.py
```

菜单包含：

```text
1. 只读连接诊断 + 当前位姿读取
2. 小范围关节测试
3. 点头动作
4. 水杯位置往返
0. 退出
```

查看所有子命令：

```bash
python main.py --help
```

查看某个动作的完整参数：

```bash
python main.py diagnose --help
python main.py joint --help
python main.py nod --help
python main.py cup --help
```

## 只读诊断与当前位姿

```bash
python main.py diagnose
```

输出 JSON：

```bash
python main.py diagnose --json
```

该功能只建立连接、登录并读取机器人状态，不调用上电、启动或运动接口。TCP 坐标系和工具定义以控制器当前配置为准。

## 小范围关节测试

预览 J6 相对当前位置增加 `0.02 rad`：

```bash
python main.py joint --delta 0,0,0,0,0,0.02
```

默认流程为：

1. 连接控制器并读取当前关节角；
2. 校验当前姿态、单次增量和目标软限位；
3. 打印当前值、增量和目标值；
4. 不上电、不启动、不运动。

程序会同时显示 rad 和 degree，方便执行前人工核对。

## 点头动作

全身协调点头：

```bash
python main.py nod --style full-body --scale 1.0 --count 1
```

单腕小幅动作：

```bash
python main.py nod --style wrist --joint 5 --amplitude 0.04 --count 1
```

默认仅使用示例起始姿态离线生成轨迹，不连接机器人。

全身模式由 J1～J6 协调参与，轨迹根据 `max_delta_rad` 自动拆分为多个小 waypoint，动作结束后返回执行前的起始姿态。动作倍率范围为 `0.25`～`1.5`。

默认速度为 `0.35 rad/s`，默认加速度为 `0.70 rad/s²`；代码限制最高速度为 `0.50 rad/s`，最高加速度为 `1.00 rad/s²`。

## 水杯位置往返动作

离线预演：

```bash
python main.py cup --cup-hold 2.0
```

真机执行时，动作顺序为：

```text
执行前的当前关节姿态 -> 水杯关节姿态 -> 执行前的当前关节姿态
```

当前示例水杯关节目标：

```text
[0.31908, -0.588733, 1.432912, -0.222756, 1.742074, 0.151014]
```

对应的参考 TCP 位姿：

```text
[0.413412, 0.028213, 0.154102, -2.450776, -0.03654, 1.6655]
```

到达水杯位置后默认停顿 `2s`，随后返回执行前姿态。程序会打印实际 TCP 与参考 TCP 的位置和姿态误差。

> 上述关节目标和 TCP 位姿来自特定示例环境，不能直接视为其他机器人、工作台、工具或坐标系的安全目标。使用者必须在 `host_tools/cup_action.py` 中替换并验证适合自己现场的目标值。

该动作使用两段直接关节运动，没有中间避障点。执行前必须确认两段完整关节空间路径均不会发生碰撞。

## 真机执行授权

命令行真实运动必须同时提供：

```text
--execute
--confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

例如：

```bash
python main.py nod \
  --style full-body \
  --scale 1.0 \
  --execute \
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

Windows PowerShell 可以写成一行：

```powershell
python main.py nod --style full-body --scale 1.0 --execute --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

交互菜单中只有输入大写 `MOVE` 才会把运动授权传递给动作工具。

真实运动路径仍会执行以下检查：

- 配置文件完整性；
- 六关节向量长度和有限数检查；
- 当前关节姿态软限位；
- 单次关节增量限制；
- 目标关节姿态软限位；
- 上电和启动后的二次状态读取；
- 速度和加速度硬上限；
- 固定确认文本。

软件检查不能替代控制器急停、保护停止和硬件安全功能。执行前请完成 [安全检查清单](docs/safety_checklist.md)。

## 添加新动作

建议按以下方式扩展动作库：

1. 在 `host_tools/` 中新增动作模块，例如 `wave_action.py`；
2. 将轨迹生成与参数校验写成可离线测试的函数；
3. 复用 `src/aubo_sdk_client/` 中的连接、配置和安全校验；
4. 默认使用 dry-run，真实运动必须要求显式授权；
5. 在 `main.py` 中注册新的子命令和菜单项；
6. 在 `tests/` 中添加不连接真机的单元测试。

不要把控制器密码、未经验证的现场坐标或缺少限位检查的运动代码提交到公开仓库。

## 测试

单元测试默认不连接真机：

```bash
python -m unittest discover -s tests -v
```

语法检查：

```bash
python -m compileall -q src host_tools tests main.py
```