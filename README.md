# AUBO ES3 Action Library

这个项目提供一个可扩展的 AUBO ES3 机械臂动作库。当前版本是纯 `pyaubo_sdk` 版，不依赖 ROS2，适合当前 Ubuntu20 系统直接连接 AUBO ES3 控制器。

## 目录

- `aubo_es3_actions/`: 动作库核心代码
- `main.py`: 统一入口和简单交互菜单
- `scripts/run_action.py`: 按名称调用动作
- `scripts/keyboard_sdk_control.py`: 通过 `pyaubo_sdk` 键盘控制机械臂点动
- `scripts/keyboard_tcp_control.py`: 通过键盘控制末端 TCP 位姿点动
- `scripts/keyboard_gripper_control.py`: 通过 AUBO Modbus 控制乐白 RS485 夹爪
- `scripts/lebo_gripper_control.py`: 参考乐白协议整理的夹爪专用控制入口
- `scripts/tune_gripper_cup.py`: 交互式微调夹爪并保存玻璃杯参数
- `scripts/gripper_presets.py`: 保存、查看、应用夹爪预设参数
- `scripts/pick_glass.py`: 低夹力夹取玻璃杯并抬高
- `config/robot.example.json`: 配置模板
- `vendor/AUBO_ES3_Action-Library`: GitHub 参考动作库

## 运行前准备

1. 先确认 SDK 是否可用：

   ```bash
   python3 -c "import pyaubo_sdk; print('pyaubo_sdk ok')"
   ```

   当前 Ubuntu20/ARM64 宿主机已验证可用版本：

   ```bash
   python3 -m pip install --user pyaubo-sdk==0.27.1rc3
   ```

2. 配置文件已经默认接入项目里的本机配置：

   ```text
   /home/jetson/aubo_project1/config/robot.local.json
   ```

   当前现场验证过的连接信息是 `192.168.1.10:30004`，用户名 `aubo`。配置查找顺序：

   - 环境变量 `AUBO_ROBOT_CONFIG` 指定的路径
   - `config/robot.local.json`
   - 上面这份 Codex 附件里的 `robot.local.json`

   `config/robot.local.json` 包含真实连接信息，已加入 `.gitignore`，不要提交到公开仓库。

## 调用动作

列出动作：

```bash
python3 scripts/run_action.py list
```

也可以使用统一入口：

```bash
python3 main.py list
```

读取当前关节：

```bash
python3 scripts/run_action.py current_joints
```

读取当前 TCP 位姿：

```bash
python3 scripts/run_action.py current_pose
```

动作 `1.0`：夹爪从闭合变成最大张开。默认 dry-run，只读取反馈并预览写入目标：

```bash
python3 main.py 1.0
python3 scripts/run_action.py 1.0
```

真实执行：

```bash
python3 main.py 1.0 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

动作 `4.0`：夹爪从张开变成最小闭合。默认 dry-run：

```bash
python3 main.py 4.0
python3 scripts/run_action.py 4.0
```

真实执行：

```bash
python3 main.py 4.0 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

动作 `2.1`：沿用当前夹爪夹持状态，从当前 TCP 位姿先垂直上移 5cm，再保持末端姿态不变，按小段 waypoint 近似弧线继续向上 5cm、向后 10cm，停顿 3 秒后沿原路径反向回来，最后下放回原位。默认 dry-run，只检查位姿、夹爪反馈和每个 waypoint 的逆解：

动作 `2.0`：从任意位置回到保存的夹取成功位，并恢复保存时的夹爪参数。默认使用 `glass_good` 预设：

```bash
python3 main.py 2.0
python3 scripts/action_2_0_return_grasp.py
```

真实执行：

```bash
python3 main.py 2.0 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

如果只回机械臂，不改变夹爪：

```bash
python3 main.py 2.0 --no-gripper --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

注意：`2.0` 默认按记录的关节角回到夹取成功位，不做自动避障。当前位置离目标很远时，先确认路径安全。

动作 `3.0`：先完全张开夹爪，再回到收缩位姿。默认使用 `config/arm_poses.json` 里的 `tucked` 位姿：

```bash
python3 main.py 3.0
python3 scripts/action_3_0_release_tuck.py
```

真实执行：

```bash
python3 main.py 3.0 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

注意：默认 `tucked` 是一个通用收缩姿态，首次真实执行前请 dry-run，并在示教器确认路径；你也可以后续把示教好的收缩关节角写进 `config/arm_poses.json`。

```bash
python3 main.py 2.1
python3 scripts/action_2_1_lift_return.py
```

真实执行：

```bash
python3 main.py 2.1 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

可调参数：

```bash
python3 main.py 2.1 --vertical-lift 0.05 --arc-lift 0.05 --back-offset 0.10 --pause 3 --linear-vel 0.01 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

如果现场发现“向后”方向相反或不是基坐标 X 轴，可以调整：

```bash
python3 main.py 2.1 --back-axis y --back-sign 1
```

`--lift` 仍可作为兼容参数使用，表示总上移高度；传入后 `arc-lift = lift - vertical-lift`。

输出中的 TCP 位姿格式是：

```text
[x, y, z, rx, ry, rz]
```

其中 `x/y/z` 单位是米，`rx/ry/rz` 单位是弧度。

小步移动某个关节：

```bash
python3 scripts/run_action.py jog_joint --joint 0 --delta 0.02
```

默认只预览目标，不运动。真实移动必须加确认文本：

```bash
python3 scripts/run_action.py jog_joint --joint 0 --delta 0.02 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

末端位姿小步移动也默认只预览目标，并会先做逆解检查：

```bash
python3 main.py jog_tcp --axis 0 --delta 0.005
```

`--axis` 对应：

```text
0 x
1 y
2 z
3 rx
4 ry
5 rz
```

真实末端直线移动：

```bash
python3 main.py jog_tcp --axis 0 --delta 0.005 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

夹爪动作默认 dry-run，只打印将要发送的 Modbus RTU 指令：

```bash
python3 main.py gripper_status
python3 main.py gripper_feedback
python3 main.py gripper_force --force 50
python3 main.py gripper_position --position 100
python3 main.py gripper_position --position 0
python3 main.py gripper_move --width 80 --force 20
```

真实写夹爪寄存器必须加确认文本：

```bash
python3 main.py gripper_force --force 50 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
python3 main.py gripper_position --position 100 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
python3 main.py gripper_move --width 80 --force 20 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

夹爪默认使用现场已验证会动作的 Modbus 功能码 `0x06` 写单个保持寄存器；如果后续设备需要协议图里的 `0x10`，可加 `--function-code 0x10`。

也可以使用更贴近乐白协议命名的专用脚本：

```bash
python3 scripts/lebo_gripper_control.py status
python3 scripts/lebo_gripper_control.py feedback
python3 scripts/lebo_gripper_control.py open --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
python3 scripts/lebo_gripper_control.py move --width 50 --force 15 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

`feedback` 会通过 AUBO SDK 的 Modbus 信号接口读取当前位置、力矩、done 和速度；现场已读到当前位置反馈，例如 `position=50`。
`gripper_move` 会在写力度和幅度之间等待，并默认最多等 4 秒读取反馈确认 `position_reached`。现场测试中 `force + width` 已验证稳定；`speed` 寄存器反馈仍为 0，建议先单独测试速度，不要在夹玻璃杯动作里依赖它。

夹爪默认参数：

```text
Modbus 设备名: /dev/ttyRobotTool,115200,N,8,1
从机地址: 1
波特率: 115200, 8N1
位置寄存器: 0x9C40 / 40000
力度寄存器: 0x9C41 / 40001
当前位置寄存器: 0x9C45 / 40005
关闭自动找行程寄存器: 0x9C9A / 40090
找行程寄存器: 0x9C48 / 40008
当前力矩寄存器: 0x9C46 / 40006
完成状态寄存器: 0x9C47 / 40007
速度寄存器: 0x9C4A / 40010
```

如果 AUBO 控制器里的 Modbus 设备名不是默认值，可以加：

```bash
python3 main.py gripper_position --position 50 --modbus-device Modbus_1
```

## 夹取玻璃杯并抬高

先用交互式调参脚本慢慢夹稳杯子：

```bash
python3 scripts/tune_gripper_cup.py --name glass_good --width 70 --force 15 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

按键：

- `[` / `]`: width 减/加 2，小步收紧或放松
- `{` / `}`: width 减/加 10，大步收紧或放松
- `-` / `+`: force 减/加 2
- `m`: 执行当前 width/force
- `p`: 读取当前位置、力矩和完成状态
- `v`: 保存当前稳定参数到 `config/gripper_presets.json`
- `o` / `c`: 打开到 100 / 闭合到 0
- `q`: 退出

保存后可以查看：

```bash
python3 scripts/gripper_presets.py list
python3 scripts/gripper_presets.py show glass_good
```

也可以复用保存的参数：

```bash
python3 scripts/gripper_presets.py apply glass_good --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

这个动作假设你已经用键盘控制或示教把夹爪移动到玻璃杯外侧，杯壁在两指中间。当前项目还没有可靠的夹力/触碰反馈，所以动作采用低夹力、分阶段闭合、小幅试提、再沿基坐标 Z 方向抬高的策略。

先只预览，不发送真实动作：

```bash
python3 scripts/pick_glass.py
```

真实执行：

```bash
python3 scripts/pick_glass.py --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

默认参数偏保守：

```text
夹力 force: 18%
最终闭合位置 close-position: 55%
试提 test-lift: 0.005 m
最终抬高 lift: 0.05 m
线速度 linear-vel: 0.01 m/s
```

如果杯子滑动，建议优先把 `--close-position` 每次调小 3-5，例如：

```bash
python3 scripts/pick_glass.py --force 18 --close-position 50 --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

如果仍然滑动，再把 `--force` 每次只增加 2-3。第一次不要直接用真实玻璃杯，先用塑料杯或空杯标定；玻璃杯上方 `--lift` 高度内必须没有障碍，急停要在手边。

## 键盘控制

默认 dry-run，不运动：

```bash
python3 scripts/keyboard_sdk_control.py
```

真实运动必须显式确认：

```bash
python3 scripts/keyboard_sdk_control.py --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

末端 TCP 键盘控制 dry-run：

```bash
python3 scripts/keyboard_tcp_control.py
```

真实末端 TCP 键盘控制：

```bash
python3 scripts/keyboard_tcp_control.py --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

夹爪键盘控制 dry-run：

```bash
python3 scripts/keyboard_gripper_control.py
```

真实夹爪键盘控制：

```bash
python3 scripts/keyboard_gripper_control.py --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

`--joint` 是 0 到 5，对应：

```text
0 shoulder_joint
1 upperArm_joint
2 foreArm_joint
3 wrist1_joint
4 wrist2_joint
5 wrist3_joint
```

键盘按键：

- `1` 到 `6`: 选择关节
- `a`: 当前关节负方向小步移动
- `d`: 当前关节正方向小步移动
- `[` / `]`: 减小或增大步长
- `j`: 打印当前关节角
- `p`: 打印当前末端位姿
- `h`: 显示帮助
- `q`: 退出

夹爪键盘按键：

- `o`: 打开到 100%
- `c`: 闭合到 0%
- `m`: 移动到当前 position
- `f`: 设置当前 force
- `[` / `]`: position 减/加 10
- `-` / `+`: force 减/加 10
- `s`: 查看 Modbus 状态
- `q`: 退出

末端 TCP 键盘按键：

- `w` / `s`: X 正/负方向
- `a` / `d`: Y 正/负方向
- `r` / `f`: Z 正/负方向
- `i` / `k`: RX 正/负方向
- `j` / `l`: RY 正/负方向
- `u` / `o`: RZ 正/负方向
- `[` / `]`: 减小或增大步长
- `p`: 打印当前 TCP 位姿
- `h`: 显示帮助
- `q`: 退出

注意：键盘脚本直接向真机发送关节轨迹目标。运行前请确保机械臂周围安全、急停可用、速度足够低。

## LLM 情感判断与固定动作映射

安装 LLM 模块依赖：

```bash
python3 -m pip install --user -r requirements.txt
```

设置 DeepSeek API 密钥（不要把密钥写入代码或配置文件）：

```bash
export DEEPSEEK_API_KEY="your-key"
```

只调用 LLM 并预览本地固定动作映射，不连接机械臂：

```bash
python3 scripts/llm_primitive_preview.py
```

真机入口默认也是安全预览模式，不连接机械臂：

```bash
python3 scripts/llm_primitive_robot.py
```

真机执行必须明确确认，并且每轮还需要在终端输入 `MOVE` 才会运动：

```bash
python3 scripts/llm_primitive_robot.py \
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

该模块不会让 LLM 直接生成关节角或轨迹。LLM 只输出情绪状态和 0–3 级强度；动作类型、速度、重复次数、起始姿势和结束姿势均由本地代码固定映射。运行记录写入 `logs/llm_primitive_*.jsonl`。

## 添加新动作

在 `aubo_es3_actions/actions.py` 中新增函数，并用 `@action("动作名")` 注册即可。动作函数接收一个 `AuboSdkClient` 实例，可以读取关节、读取 TCP 位姿或发送关节目标。
