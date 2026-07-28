# AUBO ES3 仿真与智能化实施计划（历史提案，已停止）

> **状态：不再执行。** 本文件记录的是早期设想，不表示其中的勾选项已经实现，
> 也不属于当前 HCI 猜杯研究系统的待办事项。项目明确不开发 ROS 2/MoveIt、
> Gazebo 或其他三维仿真、视觉分拣、模仿学习、强化学习或 VLA。
> 当前实现范围、部署步骤和验收状态以
> [`study_system.md`](study_system.md)、[`requirements_traceability.md`](requirements_traceability.md)
> 和仓库根目录 [`README.md`](../README.md) 为准。
> 本历史提案中出现的 Ubuntu 22.04 也是已停止路线的原始记录；当前实际部署目标
> 已改为 Ubuntu 20.04 + Conda Python 3.10。

以下内容仅为历史记录，不应据此安排开发或部署。

## 1. 项目目标

建立一套“上层业务代码基本不变、底层可在假硬件 / 物理仿真 / 真机之间切换”的 AUBO ES3 平台，并逐步实现视觉分拣、智能抓取、数据采集和学习策略。

最终目标链路：

```text
RGB-D 相机 / 任务指令
        ↓
感知与任务规划（Python / ROS 2）
        ↓
MoveIt 2（IK、碰撞检查、轨迹规划）
        ↓
ros2_control
        ↓
FakeSystem | Gazebo | AUBO ES3 真机
```

## 2. 当前项目基线

当前目录中已有：

- `main.py`：唯一的根目录入口，提供菜单并把请求转交给 `host_tools/`。
- `host_tools/diagnose_robot.py`：通过 `pyaubo_sdk.RpcClient` 只读读取机器人名称、上电状态、关节角和 TCP 位姿。
- `host_tools/safe_joint_test.py`：默认 dry-run 的小范围关节测试。
- `host_tools/nod_action.py` 与 `host_tools/cup_action.py`：点头和水杯往返动作实现。

当前需要优先处理的问题：

1. IP、用户名和密码硬编码在源码中。
2. 中文注释存在编码乱码，需要统一为 UTF-8。
3. 运动脚本没有独立的安全确认、软限位、目标预检和异常后的安全退出。
4. 当前目录尚未形成 Git 仓库，也没有依赖清单和自动化测试。
5. 当前 Windows PowerShell 环境未检测到全局 `python`，Python 解释器目前由 PyCharm 中名为 `aubo` 的 SDK 管理。
6. 尚无 ROS 2 工作空间、URDF、MoveIt 配置或仿真场景。

## 3. 技术路线决策

### 3.1 主技术栈

- 主系统：Ubuntu 22.04
- ROS：ROS 2 Humble
- 机械臂集成：AUBO ES3 ROS 2 驱动/描述包
- 规划：MoveIt 2
- 控制抽象：ros2_control
- 第一层仿真：RViz + FakeSystem
- 第二层仿真：Gazebo
- 感知：OpenCV + Open3D/PCL + YOLO
- 深度相机：优先 RealSense 系列，实际型号按现有硬件确定
- AI：先模仿学习，后强化学习/VLA
- 高级仿真：在基础链路稳定后再评估 Isaac Sim / Isaac Lab

### 3.2 Windows 与 Ubuntu 的分工

- Windows/PyCharm：保留现有 `pyaubo_sdk` 真机连接程序，用于 SDK 验证和独立诊断。
- Ubuntu/ROS 2：承担 RViz、MoveIt 2、Gazebo、视觉、任务规划以及最终真机统一控制。
- 优先使用原生 Ubuntu 或独立 Ubuntu 工作站。WSL2 可用于部分开发，但机器人网络、ROS 2 DDS、USB 相机和 GUI/GPU 仿真会增加额外配置成本。

### 3.3 控制边界

- Python 主要负责任务逻辑、视觉、AI 推理、状态机和数据记录。
- MoveIt 2 负责 IK、碰撞检查和轨迹生成。
- ros2_control/AUBO 驱动负责轨迹执行和状态反馈。
- 不使用普通 Python 循环承担未经验证的高频硬实时关节控制。
- AI 输出不能直接下发真机，必须经过动作裁剪、速度限制、IK、碰撞检查和安全状态机。

## 4. 里程碑计划

## M0：工程治理与真机安全基线

预计：1～2 个工作日。

### 任务

- [ ] 初始化 Git 仓库并建立 `.gitignore`。
- [ ] 创建 Python 虚拟环境或明确记录 PyCharm 解释器路径。
- [ ] 生成 `requirements.txt`/锁定依赖版本。
- [ ] 将机器人配置迁移到 `.env` 或 YAML；仓库只提交示例配置。
- [ ] 修复现有 Python 文件编码为 UTF-8。
- [ ] 把连接、读取状态、上电、运动拆成独立模块。
- [ ] 增加连接超时、登录失败、无机器人、非运行状态等异常处理。
- [ ] 增加只读诊断命令，默认禁止运动。
- [ ] 运动前打印当前关节角、目标关节角、角度变化和预计速度。
- [ ] 增加 `--confirm-motion` 显式参数；无参数时只做 dry-run。
- [ ] 记录 ES3 各关节软限位、最大速度和工作空间限制。
- [ ] 明确急停、保护停止、远程控制允许条件和现场安全区。

### 验收

- `diagnose` 命令可以稳定读取机器人状态且不会触发运动。
- 密码不再出现在 Git 跟踪文件中。
- 运动测试必须经过显式确认，默认低速、小范围、单关节执行。
- 任何异常都能停止继续下发后续动作并打印可定位的错误。

### 交付物

```text
src/aubo_sdk_client/
config/robot.example.yaml
.env.example
requirements.txt
README.md
```

## M1：ROS 2 + RViz + FakeSystem 运动学仿真

预计：2～5 个工作日。

### 任务

- [ ] 安装 Ubuntu 22.04、ROS 2 Humble、colcon、rosdep 和 MoveIt 2。
- [ ] 建立 `ros2_ws`。
- [ ] 引入与 ES3/控制器版本匹配的 AUBO ROS 2 包。
- [ ] 确认 `aubo_ES3` 机器人描述可以加载。
- [ ] 检查关节名称、关节顺序、旋转方向、零位和单位。
- [ ] 使用 FakeSystem 发布 `/joint_states`。
- [ ] 在 RViz 中加载 RobotModel、TF、PlanningScene 和 MotionPlanning。
- [ ] 启动 MoveIt 2，验证交互标记规划。
- [ ] 编写 Python `rclpy` 示例：发送关节目标。
- [ ] 编写 Python MoveIt 示例：发送末端位姿目标。
- [ ] 保存一组安全的 named poses：`home`、`observe`、`pre_grasp`。

### 验收

- RViz 中 ES3 模型无断裂、无异常跳变。
- 六个关节能按预期方向运动。
- 能从 `home` 规划到至少三个安全姿态。
- 障碍物加入 PlanningScene 后，规划轨迹不会穿过障碍物。
- 同一个 Python API 能分别接收关节目标和 TCP 位姿目标。

### 关键测试

- FK：给定关节角，末端位姿与 SDK/示教器结果对比。
- IK：典型工作空间内目标成功率。
- 单位：全部关节使用 rad，线性距离使用 m。
- 坐标系：固定 `base_link`、`tool0`、`tcp` 的含义。

## M2：Gazebo 物理仿真和基础工作站

预计：4～8 个工作日。

### 任务

- [ ] 选择与 ROS 2 Humble 和 AUBO 包兼容的 Gazebo 版本。
- [ ] 配置 Gazebo 与 ros2_control 的控制器连接。
- [ ] 校验 ES3 的惯量、碰撞模型和关节阻尼。
- [ ] 建立基础世界：地面、工作台、安全围栏/禁入区域。
- [ ] 添加简单二指夹爪；如果已有真实夹爪，使用对应模型和接口。
- [ ] 添加方块、圆柱、料箱等测试物体。
- [ ] 实现夹爪开合和 attach/detach 或真实接触抓取。
- [ ] 接入虚拟 RGB-D 相机。
- [ ] 实现 MoveIt 2 与 Gazebo 的联合启动文件。
- [ ] 建立 `fake`、`sim`、`real` 三种模式的统一参数。

### 验收

- MoveIt 轨迹可以通过控制器驱动 Gazebo 中的 ES3。
- Gazebo 与 ROS `/joint_states` 状态一致。
- 机械臂不会在静止时明显漂移或爆炸。
- 夹爪可以完成至少 20 次重复抓放测试。
- 碰撞物体和 PlanningScene 的位置基本一致。

### 风险

- URDF 惯量或碰撞体不合理导致物理仿真不稳定。
- Gazebo 版本与厂商包不一致。
- 夹爪不是 AUBO 本体的一部分，需要单独建模和控制。

## M3：仿真/真机统一控制接口

预计：3～7 个工作日，部分工作可与 M2 并行。

### 首选方案

直接使用 AUBO 的 ros2_control 硬件接口，让 MoveIt 通过 `FollowJointTrajectory` 控制真机。

### 备用方案

如果公开驱动与当前 ARCS/控制器版本不兼容，则将现有 `pyaubo_sdk` 封装成受控的 ROS 2 Bridge，但仍保持统一的上层接口。

### 标准接口

状态：

```text
/joint_states
/tf
/tf_static
/aubo/robot_state
/aubo/safety_state
/aubo/io_states
```

命令：

```text
/follow_joint_trajectory
/aubo/gripper_command
/aubo/io_command
```

服务：

```text
/aubo/connect
/aubo/power_on
/aubo/startup
/aubo/stop
/aubo/recover
```

### 任务

- [ ] 明确真机控制器、ARCS 和 SDK 的准确版本。
- [ ] 明确公开驱动兼容矩阵。
- [ ] 比较 SDK 与 ROS 的关节顺序、TCP 定义和时间戳。
- [ ] 实现 `robot_mode:=fake|sim|real`。
- [ ] 实现运动前置条件检查。
- [ ] 实现轨迹执行超时、取消和保护停止处理。
- [ ] 实现真机心跳/看门狗。
- [ ] 增加仿真轨迹与真机轨迹对比记录。

### 验收

- 同一个 `move_to_named_pose` 程序无需修改业务代码即可运行在 Fake、Gazebo 和真机。
- 真机以低速执行 `home → observe → home`，轨迹无明显突跳。
- 状态断开、保护停止或急停后，不会继续发送缓存动作。

## M4：RGB-D 视觉和手眼标定

预计：1～2 周。

### 任务

- [ ] 确定相机型号和安装形式：眼在手上或眼在手外。
- [ ] 完成相机内参和深度对齐。
- [ ] 发布 RGB、Depth、CameraInfo 和点云话题。
- [ ] 定义 `camera_link`、`camera_optical_frame` 等 TF。
- [ ] 使用 ArUco/AprilTag 完成手眼标定。
- [ ] 记录并版本化标定结果。
- [ ] 实现像素 + 深度 → 相机三维点。
- [ ] 实现相机坐标 → `base_link` 坐标转换。
- [ ] 在 PlanningScene 中加入桌面和相机视野障碍物。
- [ ] 实现目标检测/分割节点。

### 验收

- 在工作区多个位置测量目标，机器人基坐标误差满足项目要求。
- 推荐初始目标：静态物体定位误差不超过 10 mm；稳定后争取 3～5 mm。
- 相机或标定失效时系统拒绝抓取，而不是使用过期 TF。

## M5：视觉分拣 MVP

预计：1～2 周。

### 任务状态机

```text
IDLE
 → OBSERVE
 → DETECT
 → ESTIMATE_POSE
 → PLAN_PREGRASP
 → APPROACH
 → CLOSE_GRIPPER
 → LIFT
 → PLACE
 → OPEN_GRIPPER
 → RETREAT
 → VERIFY
```

### 任务

- [ ] 第一版仅使用单个、无遮挡、已知尺寸物体。
- [ ] 生成预抓取位姿、抓取位姿和退回位姿。
- [ ] 使用 MoveIt Task Constructor 或显式阶段状态机。
- [ ] 对抓取位姿进行可达性和碰撞过滤。
- [ ] 支持按颜色或类别分到不同料箱。
- [ ] 记录每次任务的图像、点云、目标位姿、规划结果和故障原因。
- [ ] 加入抓取结果验证。
- [ ] 在 Gazebo 中连续回归后再部署真机。

### 验收

- 仿真中至少连续完成 50 次抓放，成功率达到 90% 以上。
- 真机低速完成至少 20 次测试，无碰撞和越界。
- 失败时进入可恢复状态，不通过盲目重试扩大风险。

## M6：数据平台和智能抓取

预计：2～4 周。

### 数据格式

每个时间步至少记录：

```text
时间戳
RGB / Depth
关节位置、速度
TCP 位姿
夹爪状态
目标类别和位姿
规划轨迹
执行结果
安全状态
```

### 智能化顺序

1. 检测/分割模型。
2. 6D 位姿估计或点云抓取候选。
3. 抓取质量评分。
4. 规则与学习结果融合。
5. 模仿学习。
6. 强化学习。
7. VLA 任务理解。

### 验收

- 数据集有版本号、场景标签和训练/验证划分。
- AI 只生成候选动作，动作仍经过安全过滤层。
- 新模型能够在固定回归场景中与旧模型量化比较。

## M7：模仿学习 / Isaac Lab（可选高级阶段）

预计：4 周起。

### 模仿学习路线

- 通过示教器、SpaceMouse、键盘微调或拖动示教采集演示。
- 先训练输出低频末端增量的 Behavior Cloning/ACT/Diffusion Policy。
- 使用动作裁剪与 MoveIt Servo/安全控制层执行。
- 先仿真评估，再低速真机评估。

### 强化学习路线

- 将 ES3 模型导入 Isaac Sim 或 MuJoCo。
- 建立 reach/grasp/insert 等单一任务。
- 进行质量、摩擦、相机、控制延迟和噪声随机化。
- 真机部署前设置独立安全监督器。

### VLA 路线

仅让 VLA 输出任务步骤、目标对象或受限技能调用，例如：

```json
{
  "skill": "pick_and_place",
  "object": "red_block",
  "destination": "left_bin"
}
```

不让语言模型直接输出未经检查的高速关节命令。

## 5. 建议仓库结构

```text
AUBO/
├─ README.md
├─ .gitignore
├─ .env.example
├─ requirements.txt
├─ config/
│  ├─ robot.example.yaml
│  ├─ safety_limits.yaml
│  └─ named_poses.yaml
├─ docs/
│  ├─ implementation_plan.md
│  ├─ safety_checklist.md
│  ├─ coordinate_frames.md
│  └─ calibration.md
├─ host_tools/
│  ├─ diagnose_robot.py
│  └─ safe_joint_test.py
├─ src/
│  └─ aubo_sdk_client/
├─ ros2_ws/
│  └─ src/
│     ├─ aubo_es3_bringup/
│     ├─ aubo_es3_moveit_config/
│     ├─ aubo_es3_gazebo/
│     ├─ aubo_es3_perception/
│     └─ aubo_es3_tasks/
├─ models/
├─ datasets/
├─ scripts/
└─ tests/
```

大型模型、rosbag、训练数据和仿真缓存不提交到普通 Git；使用独立数据目录、Git LFS 或对象存储。

## 6. 每周推进建议

### 第 1 周

- 完成 M0。
- 准备 Ubuntu 22.04 + ROS 2 Humble。
- 明确 ARCS/控制器/SDK/夹爪/相机版本。
- RViz 显示 ES3。

### 第 2 周

- FakeSystem + MoveIt 2 跑通。
- Python 发送关节和 TCP 目标。
- 建立坐标系和安全姿态。

### 第 3 周

- Gazebo 工作站、夹爪和测试物体。
- MoveIt 驱动 Gazebo。
- 开始 fake/sim/real 统一配置。

### 第 4 周

- 真机 ROS 控制低速验证。
- 加入轨迹日志、安全状态机和回归测试。

### 第 5～6 周

- RGB-D 相机、手眼标定和物体三维定位。
- 完成仿真视觉抓取。

### 第 7～8 周

- 真机视觉分拣 MVP。
- 建立数据集与成功率统计。

### 第 9 周以后

- 智能抓取、模仿学习、Isaac Lab、强化学习或 VLA。

## 7. 项目成功指标

### 基础平台

- 仿真与真机共用任务 API。
- 所有坐标、单位、关节顺序有文档和自动检查。
- 任何学习模型都不能绕过安全过滤。

### 规划与控制

- 常用目标规划成功率 ≥ 95%。
- 真机轨迹无不连续目标和明显突跳。
- 断联、急停、保护停止能被正确识别。

### 视觉分拣

- 仿真抓放成功率 ≥ 90%。
- 真机基础分拣成功率初始目标 ≥ 80%，稳定后 ≥ 95%。
- 每次失败均可根据日志归因到感知、规划、抓取或执行环节。

## 8. 立即执行清单

在进入 ROS 2 安装前，先完成以下信息和代码准备：

1. 查询并记录 ES3 控制器/ARCS 版本。
2. 查询 `pyaubo_sdk` 版本及当前 Python 解释器绝对路径。
3. 记录夹爪型号、控制方式和是否已有 URDF。
4. 记录相机型号；没有相机时先使用 Gazebo 虚拟相机。
5. 整理现有 Python 程序：配置外置、UTF-8、安全 dry-run。
6. 准备 Ubuntu 22.04 环境。
7. 引入 AUBO ROS 2 包并完成 RViz + FakeSystem 首次启动。
