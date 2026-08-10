# LLM 情感动作五方案与实机测试指南

本文说明五个 LLM 情感动作方案的输入、输出、本地动作映射与本地决策边界，并给出按风险递增排列的 AUBO 机械臂实机测试步骤。

五个方案共享同一套 LLM 通信、结构化输出校验、跨回合状态、安全检查、日志和软件停止逻辑。每个 Git 分支通过自己的 `scripts/llm_expressive_scheme.py` 定义输出 Schema、语义校验和动作编译器。

## 1. 五个方案共同的 LLM 输入

五个方案不得删除或替换 main 模式中的任何基础输入。程序每轮都会向 LLM 发送：

| 字段 | 含义 | 本地来源 |
|---|---|---|
| `current_result` | 本轮结果，只能为 `correct` 或 `incorrect` | 终端输入 `1` 或 `0` 后转换 |
| `result_history` | 包含本轮在内的全部结果历史 | 本地程序维护；失败或取消的轮次不写入 |
| `transition_type` | `continuation`、`accumulation`、`reversal` 或 `fluctuation` | 本地根据历史计算，LLM 不得修改 |
| `previous_emotion_state` | 上一轮情感状态 | 上一轮成功执行后的 `emotion_state`；第一轮为中性状态 |
| `previous_decision` | 上一轮完整 LLM 判断 | 上一轮成功执行后的完整 JSON；第一轮为 `null` |

`transition_type` 的本地定义为：

- `continuation`：第一轮；
- `accumulation`：本轮结果与上一轮相同；
- `reversal`：本轮结果与上一轮不同；
- `fluctuation`：最近三轮形成连续交替。

基础输入示例：

```json
{
  "current_result": "correct",
  "result_history": ["incorrect", "correct"],
  "transition_type": "reversal",
  "previous_emotion_state": {
    "emotion_name": "disappointment",
    "valence": -0.45,
    "arousal": 0.30,
    "intensity": 0.40,
    "persistence": 0.35,
    "unexpectedness": 0.20
  },
  "previous_decision": {
    "emotion_state": {},
    "transition_type": "continuation",
    "brief_reason": "上一轮猜错，形成适度失望"
  }
}
```

每个方案可以在这五项之后增加必要的参数范围或本地选择信息，但不能减少基础输入。

## 2. 五个方案总览表

| 方案与分支 | LLM 输入 | LLM 输出 | 输出到动作的映射 | 本地代码最终决定 | 当前真机状态 |
|---|---|---|---|---|---|
| 方案一：`feature/llm-scheme-1-parameterized` | 共同五项 + `parameter_limits` | 情感状态、变化类型、理由、`motion_profile` | 用 `spatial_extent` 等参数在已示教正向或负向关节姿态之间插值 | 动作家族、关键帧顺序、实际关节角、速度上限、轨迹检查和是否执行 | 可进行低速单轮验证 |
| 方案二：`feature/llm-scheme-2-laban` | 共同五项 + Laban `parameter_limits` | 情感状态 + Shape、Effort、Rhythm 向量 | 将 Laban 向量映射到少量共享的已示教关节姿态基 | 正负动作基、侧向基、关节插值、速度/加速度、振荡轨迹和安全拒绝 | 可在方案一验证后低速测试 |
| 方案三：`feature/llm-scheme-3-residual` | 共同五项 + `allowed_normalized_limits` | 情感状态 + 参考动作家族 + onset/apex 归一化小残差 | 对参考 TCP 增加小幅平移/旋转残差，生成密集 TCP 点，再做连续种子 IK | 物理限幅、参考姿态、语义方向、TCP 路径、IK、跳解检查和碰撞门控 | 当前禁止真实运动，只做 FK/IK 验证 |
| 方案四：`feature/llm-scheme-4-keyframe-ik` | 共同五项 + `normalized_keyframe_limits` + `required_phases` | 情感状态 + preparation/apex 两个归一化末端关键帧 | 将归一化位置和朝向 token 换算为 TCP 关键帧，插值后连续 IK | up/away/lateral 方向、厘米级尺度、朝向偏移、路径采样、IK 和碰撞门控 | 当前禁止真实运动，只做 FK/IK 验证 |
| 方案五：`feature/llm-scheme-5-candidates` | 共同五项 + 规则基线、近期动作向量、要求的三种策略和选择原则 | 情感状态 + Shape/Rhythm/Path 三个动作质量候选 | 本地分别按方案二式关节姿态基编译三个候选 | 逐候选安全过滤；按语义、新颖性、平滑度和安全裕度评分并选择；LLM 无权指定胜者 | 可在方案一、二验证后低速测试 |

## 3. 方案一：固定轨迹连续参数化

### 3.1 LLM 附加输入

`parameter_limits` 提供：

- `spatial_extent`：0.55–1.00；
- `speed_scale`：0.70–1.10；
- `acceleration_scale`：0.65–1.10；
- `hold_scale`：0.70–1.30；
- `rhythmic_accent`：0.00–1.00；
- `repeat_count`：0–2；
- `repeat_amplitude`：0.50–0.90；
- `return_speed_scale`：0.70–1.00。

### 3.2 LLM 输出

```json
{
  "emotion_state": {},
  "transition_type": "continuation",
  "brief_reason": "首次猜对，形成幅度适中的高兴回应",
  "motion_profile": {
    "spatial_extent": 0.78,
    "speed_scale": 0.94,
    "acceleration_scale": 0.90,
    "hold_scale": 0.95,
    "rhythmic_accent": 0.35,
    "repeat_count": 1,
    "repeat_amplitude": 0.68,
    "return_speed_scale": 0.88
  }
}
```

### 3.3 动作映射与本地决定

猜对时：

```text
curiosity_down
→ joy_lift_max 的幅度插值
→ joy_shout_peak_max 的幅度插值
→ 可选 peak/lift 脉冲
→ anticipation_look_down
```

猜错时：

```text
curiosity_down
→ disappointment_turn_away 的幅度插值
→ disappointment_retreat_max 的幅度插值
→ 可选小幅呼吸循环
→ anticipation_look_down
```

LLM 不选择动作名称、动作顺序、关节角或坐标。本地代码根据 `current_result` 选择正负动作家族，读取 `config/emotion_poses_new_es3.json`，生成关键帧并实施安全上限。

当前 `return_speed_scale` 已进入输出和日志元数据，但通用执行器仍使用单一全局速度，尚未为返回阶段单独应用该参数。

## 4. 方案二：统一 Laban 动作参数

### 4.1 LLM 附加输入

`parameter_limits` 提供 Shape、Effort 和 Rhythm 范围，包括：

- `vertical_shape`、`radial_shape`、`depth_shape`；
- `suddenness`、`freedom`、`directness`、`lightness`、`curvature`；
- `duration_scale`、`hold_ratio`、`oscillation_count`、`oscillation_scale`。

### 4.2 LLM 输出

```json
{
  "emotion_state": {},
  "transition_type": "reversal",
  "brief_reason": "连续猜错后猜对，以克制舒展表现释然",
  "motion_quality": {
    "vertical_shape": 0.42,
    "radial_shape": 0.35,
    "depth_shape": -0.08,
    "suddenness": 0.64,
    "freedom": 0.58,
    "directness": 0.42,
    "lightness": 0.68,
    "curvature": 0.56,
    "duration_scale": 1.02,
    "hold_ratio": 0.12,
    "oscillation_count": 1,
    "oscillation_scale": 0.18
  }
}
```

### 4.3 动作映射与本地决定

本地使用共享姿态基：

- 正向上扬：`joy_lift_max`；
- 正向舒展：`joy_shout_peak_max`；
- 负向下沉：`disappointment_retreat_max`；
- 负向回避：`disappointment_turn_away`；
- 曲线中间姿态：`curiosity_left` 或 `curiosity_right`。

Shape 决定主要关节插值，`curvature` 和 `directness` 影响侧向中间姿态，`suddenness` 与 `duration_scale` 影响速度和加速度，振荡参数生成有限脉冲。正负动作基、最终关节角和安全拒绝均由本地决定。

目前 `freedom` 和 `lightness` 主要用于语义约束，尚未各自形成独立的底层动力学映射。

## 5. 方案三：参考动作加受限笛卡尔残差

### 5.1 LLM 附加输入

`allowed_normalized_limits` 告知 LLM 归一化残差及本地换算上限：

- 上下修正最大约 2.5 cm；
- 展开/收拢方向最大约 2.5 cm；
- 侧向最大约 1.5 cm；
- 沿已示教回撤方向最大约 2.0 cm；
- 单轴朝向残差最大约 0.12 rad；
- 路径弧度最大约 1 cm。

### 5.2 LLM 输出

```json
{
  "emotion_state": {},
  "transition_type": "reversal",
  "brief_reason": "连续猜错后猜对，在正向参考动作上加入克制释放",
  "reference_family": "positive_reference",
  "onset_modifier": {
    "up_down": 0.20,
    "open_close": 0.12,
    "lateral": 0.05,
    "yaw": 0.04,
    "pitch": -0.05,
    "roll": 0.03,
    "away_from_user": 0.10
  },
  "apex_modifier": {
    "up_down": 0.35,
    "open_close": 0.28,
    "lateral": 0.08,
    "yaw": 0.05,
    "pitch": -0.02,
    "roll": 0.04,
    "away_from_user": 0.08
  },
  "path_curvature": 0.38,
  "duration_scale": 1.02,
  "apex_hold_ratio": 0.12,
  "onset_accent": 0.62,
  "local_pulse_count": 1,
  "pulse_scale": 0.18
}
```

### 5.3 动作映射与本地决定

本地先根据结果选择已示教正向或负向参考姿态，对参考姿态做 FK，再把归一化残差换算成受限 TCP 修正。onset 和 apex 之间分别生成十个 TCP 采样点，每个点使用上一个 IK 解作为种子，并检查相邻 IK 解是否跳分支。

LLM 不输出绝对坐标、关节角或 IK。物理尺度、参考动作、语义方向、TCP 工作空间、IK、速度和碰撞门控均由本地决定。

该方案会创建新的笛卡尔路径，当前 `require_collision_model_for_cartesian=true`，因此真机入口会在运动前失败关闭。

## 6. 方案四：归一化末端关键帧与连续 IK

### 6.1 LLM 附加输入

- `normalized_keyframe_limits`：归一化位置范围和本地物理换算上限；
- `required_phases`：固定为 `preparation`、`apex`。

本地物理上限为：上下 4.5 cm、侧向 3 cm、回撤 4 cm、朝向 token 0.12 rad、路径弧度 1 cm。

### 6.2 LLM 输出

```json
{
  "emotion_state": {},
  "transition_type": "continuation",
  "brief_reason": "一般情境下猜对，以适度上扬和轻微弧线回应",
  "keyframes": [
    {
      "phase": "preparation",
      "position_norm": {
        "vertical": 0.18,
        "lateral": -0.08,
        "away_from_user": 0.08
      },
      "orientation_token": "look_up_soft",
      "path_type": "vertical_arc",
      "duration_weight": 0.92,
      "hold_ratio": 0.04,
      "speed_emphasis": 0.45
    },
    {
      "phase": "apex",
      "position_norm": {
        "vertical": 0.52,
        "lateral": 0.10,
        "away_from_user": 0.05
      },
      "orientation_token": "open_wrist_soft",
      "path_type": "release_arc",
      "duration_weight": 1.02,
      "hold_ratio": 0.10,
      "speed_emphasis": 0.55
    }
  ]
}
```

### 6.3 动作映射与本地决定

本地从 `curiosity_down` 到已示教回撤姿态的方向推导 `away`，以基坐标系 z 轴作为 `up`，通过叉乘建立 `lateral`。归一化位置和有限朝向 token 被换算为 TCP 关键帧，每段生成 12 个采样点后进行连续种子 IK。

LLM 不能直接输出米制坐标、任意朝向或关节角。语义坐标轴、尺度、路径插值、IK 和安全拒绝均由本地决定。

该方案当前同样被笛卡尔碰撞模型门控。并且密集 IK 点仍由通用执行器逐点阻塞式 `moveJoint` 执行；接入碰撞模型后，还应增加经过验证的 spline/blend 执行器再考虑真实运动。

## 7. 方案五：多候选与本地风险感知选择

### 7.1 LLM 附加输入

- `rule_baseline_signature`：固定规则动作的质量基线；
- `recent_motion_signatures`：从 `previous_decision` 提取的近期候选向量；
- `required_strategies`：`shape_dominant`、`rhythm_dominant`、`path_dominant`；
- `selection_policy`：本地安全过滤和评分原则。

### 7.2 LLM 输出

LLM 输出共同情感状态和恰好三个候选：

```json
{
  "emotion_state": {},
  "transition_type": "accumulation",
  "brief_reason": "连续猜对，以三种动作质量候选表现逐步增强的兴奋",
  "candidates": [
    {
      "candidate_id": "shape_1",
      "strategy": "shape_dominant",
      "motion_quality": {}
    },
    {
      "candidate_id": "rhythm_1",
      "strategy": "rhythm_dominant",
      "motion_quality": {}
    },
    {
      "candidate_id": "path_1",
      "strategy": "path_dominant",
      "motion_quality": {}
    }
  ]
}
```

每个 `motion_quality` 使用与方案二相同的 Shape、Effort 和 Rhythm 字段。

### 7.3 动作映射与本地决定

本地分别将三个候选映射到共享关节姿态基，并独立检查关节限位、关键帧跨度、速度、加速度和 jerk。未通过安全检查的候选直接淘汰。

安全候选按以下权重评分：

| 本地评分项 | 权重 |
|---|---:|
| 情感语义一致性 | 0.40 |
| 相对规则基线的新颖性 | 0.25 |
| 相对近期动作的新颖性 | 0.15 |
| 平滑度 | 0.10 |
| 安全裕度 | 0.10 |

LLM 无权输出 `selected_candidate`。执行候选、拒绝原因和所有得分都会写入 `compiled_metadata`。

## 8. 所有方案共有的本地安全门

LLM 输出首先经过 Pydantic Schema 和语义规则检查，拒绝：

- 缺失或多余字段；
- 非法参数范围；
- 情感正负方向错误；
- 不符合历史的 `transition_type`；
- 不符合正负动作家族的输出；
- 不满足 fluctuation 或 reversal 限制的输出。

动作编译后还会检查：

- 当前关节到第一个关键帧的跨度；
- 全部关键帧的关节上下限和段跨度；
- 五次多项式采样后的速度、加速度和 jerk；
- 方案三、四的 TCP 工作空间；
- 方案三、四的连续种子 IK 和 IK 解分支跳变；
- 笛卡尔生成方案是否有经过验证的碰撞模型；
- 急停锁存、信号中断、每个关键帧监控和运动超时。

检查失败时本轮不运动，也不写入结果历史。

这些检查目前不包含完整的机械臂自碰撞、桌面/杯子环境碰撞或人与机械臂距离模型。方案一、二、五虽然使用已示教关节姿态基，姿态之间的插值路径仍必须通过仿真和现场风险评估。

# 实机测试步骤

## 9. 测试范围与推荐顺序

当前推荐顺序：

```text
方案一单轮正向
→ 方案一单轮负向
→ 方案二单轮正向/负向
→ 方案五单轮正向/负向
→ 方案一、二、五跨回合
→ 方案三、四只做真实控制器 FK/IK 验证，不运动
```

方案三、四在完成碰撞模型和连续轨迹执行器前，不得通过修改配置绕过门控。

## 10. 第一步：确认现场安全条件

测试前确认：

- 当前机械臂、控制器和末端工具无故障；
- TCP、负载、安装方向和基坐标系配置正确；
- `config/emotion_poses_new_es3.json` 来自当前机械臂和当前工具；
- 桌面、杯子、线缆和人员不在测试轨迹内；
- 实体急停可触达，并有一名操作员专门负责急停；
- 已根据现场风险评估配置安全 I/O、关节限制和 Reduced Mode；
- 首轮使用空场、最低速度、单回合测试。

AUBO 官方说明安全功能应根据具体应用进行风险评估，Reduced Mode 使用单独的缩减参数：

- [AUBO Safety Information](https://docs.aubo-robotics.cn/user_manual/en/2_aubo_scope/01-preface/04-security-information.html)
- [AUBO Safety I/O](https://docs.aubo-robotics.cn/user_manual/en/2_aubo_scope/05-configure/02-safety/01-io.html)
- [AUBO Robot Arm Emergency Response](https://developer.aubo-robotics.cn/en/hardware/user_manual/robot_arm_i_user_manual.html)

软件停止不是安全等级急停，不能替代实体急停、安全回路、限速模式或现场监护。

## 11. 第二步：更新并切换目标分支

```bash
git fetch origin
git switch feature/llm-scheme-1-parameterized
git pull --ff-only
```

如果本地尚无该分支：

```bash
git switch --track origin/feature/llm-scheme-1-parameterized
```

测试其他方案时替换分支名称。

## 12. 第三步：检查 Python 和 AUBO SDK 环境

```bash
python3 -c "import pyaubo_sdk; print('pyaubo_sdk OK')"
python3 -c "import pydantic, openai; print('LLM dependencies OK')"
```

如果只缺 LLM 依赖：

```bash
python3 -m pip install --user "pydantic>=2.0" "openai>=1.0"
```

`pyaubo_sdk` 应使用机械臂电脑已有的 AUBO SDK、Conda 或 Docker 环境版本。

## 13. 第四步：创建低速本地配置

```bash
cp config/robot.example.json config/robot.local.json
```

编辑 `config/robot.local.json`，确认控制器 IP、端口、账号、密码、真实关节范围和现场安全限制。第一次测试建议先设为：

```json
"max_velocity_rad_s": 0.05,
"max_acceleration_rad_s2": 0.05
```

不要在第一次测试中直接使用 `config/robot.curiosity_fast.json`。示例配置中的 `±2π` 只是宽泛占位范围，应替换为当前机械臂和工位允许的真实关节范围。

## 14. 第五步：设置 LLM 环境变量

```bash
export DEEPSEEK_API_KEY="你的API密钥"
export DEEPSEEK_MODEL="你的账户支持的模型名称"
```

不要把 API 密钥写入代码、Prompt、配置文件或 Git。

## 15. 第六步：运行本地自动测试

每切换一个方案分支都运行：

```bash
python3 -m unittest \
  tests.test_expressive_safety \
  tests.test_llm_expressive_scheme \
  -v
```

测试失败时不得进入真机阶段。

## 16. 第七步：运行纯 LLM 预览

```bash
python3 scripts/llm_expressive_preview.py --max-rounds 3
```

输入规则：

```text
1 = 猜对
0 = 猜错
e = 退出
```

预览程序只调用 LLM、验证 JSON 和展示计划，不连接机械臂。依次检查以下历史：

```text
1
1, 1, 1
0
0, 0, 0
0, 0, 1
1, 1, 0
1, 0, 1
```

确认 `transition_type`、情感方向和方案参数符合 Prompt。

## 17. 第八步：只读连接控制器

```bash
python3 - <<'PY'
from aubo_es3_actions import load_config
from aubo_es3_actions.sdk_client import AuboSdkClient

config = load_config("config/robot.local.json")

with AuboSdkClient(config) as client:
    snapshot = client.snapshot()
    print("robot:", snapshot.robot_name)
    print("power_on:", snapshot.power_on)
    print("joints:", snapshot.joint_positions_rad)
    print("tcp:", snapshot.tcp_pose)
PY
```

这一步只读取状态，不调用运动命令。连接、登录、机器人状态或 TCP 异常时停止测试。

## 18. 第九步：比较当前位置与起始姿态

所有方案都会先前往 `curiosity_down`。运行以下只读检查：

```bash
python3 - <<'PY'
import json
from pathlib import Path

from aubo_es3_actions import load_config
from aubo_es3_actions.expressive_common import load_joint_pose
from aubo_es3_actions.sdk_client import AuboSdkClient

poses = json.loads(
    Path("config/emotion_poses_new_es3.json").read_text()
)
start = load_joint_pose(poses, "curiosity_down")

with AuboSdkClient(
    load_config("config/robot.local.json")
) as client:
    current = client.current_joints()

deltas = [abs(a - b) for a, b in zip(current, start)]
print("每个关节与起始姿态的差值:", deltas)
print("最大差值:", max(deltas))
PY
```

如果当前位置明显远离 `curiosity_down`，不要依赖程序自动跨越。先通过示教器低速手动模式验证并规划到安全附近。

## 19. 第十步：在无运动条件下测试软件停止链路

终端 A 启动：

```bash
python3 scripts/llm_expressive_robot.py \
  --config config/robot.local.json \
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

程序停在 `第1轮结果 [1/0/e]：` 时不要输入结果。

终端 B 执行：

```bash
python3 scripts/emergency_stop.py
```

终端 A 应检测到锁存并停止。确认现场安全后才能解除：

```bash
python3 scripts/emergency_stop.py \
  --clear \
  --confirm-clear I_CONFIRMED_THE_PHYSICAL_AREA_IS_SAFE
```

如果情感动作进程失联，可直接连接控制器请求 SDK 停止：

```bash
python3 scripts/emergency_stop.py \
  --direct \
  --config config/robot.local.json
```

异常运动时优先使用实体急停。

## 20. 第十一步：方案一低速单轮测试

```bash
git switch feature/llm-scheme-1-parameterized
git pull --ff-only

python3 scripts/llm_expressive_robot.py \
  --config config/robot.local.json \
  --max-rounds 1 \
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

先输入 `1`。程序会调用 LLM、编译轨迹、完成安全检查，然后显示 JSON 和 `compiled_metadata`。

输入 `MOVE` 前确认：

- `spatial_extent` 没有接近 1.00；
- `speed_scale` 没有接近 1.10；
- `repeat_count` 最好为 0 或 1；
- 没有任何安全检查错误；
- 现场无人员和障碍物；
- 实体急停操作员已经准备。

不满意时输入任意其他内容取消。确认安全后才输入大写 `MOVE`。

正向动作完成后，重新启动程序，以同样方式单独输入 `0` 测试负向动作。初次测试不要连续累积多个回合。

## 21. 第十二步：方案二低速单轮测试

方案一正向和负向动作都安全完成后：

```bash
git switch feature/llm-scheme-2-laban
git pull --ff-only

python3 scripts/llm_expressive_robot.py \
  --config config/robot.local.json \
  --max-rounds 1 \
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

检查：

- 猜对时 `vertical_shape >= 0.15`、`radial_shape >= 0.10`；
- 猜错时 `vertical_shape <= -0.10`、`radial_shape <= -0.10`、`depth_shape <= 0`；
- 初次测试 `oscillation_count` 最好不超过 1；
- `compiled_metadata` 中关键帧数量合理；
- 实际动作基和姿态方向符合预期。

确认后才输入 `MOVE`。

## 22. 第十三步：方案五低速单轮测试

方案二验证完成后：

```bash
git switch feature/llm-scheme-5-candidates
git pull --ff-only

python3 scripts/llm_expressive_robot.py \
  --config config/robot.local.json \
  --max-rounds 1 \
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

重点检查：

```json
"compiled_metadata": {
  "selected_candidate": {},
  "safe_candidate_scores": [],
  "rejected_candidates": []
}
```

确认最终候选已经通过硬性安全检查、被拒候选不会执行、风险裕度没有异常偏低、三个候选具有可解释差异，再输入 `MOVE`。

## 23. 第十四步：方案三、四真实控制器 FK/IK 测试

方案三：

```bash
git switch feature/llm-scheme-3-residual
```

方案四：

```bash
git switch feature/llm-scheme-4-keyframe-ik
```

纯 LLM 预览：

```bash
python3 scripts/llm_expressive_preview.py
```

连接真实控制器并验证 FK/IK：

```bash
python3 scripts/llm_expressive_robot.py \
  --config config/robot.local.json \
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

输入一轮结果后，程序可以调用控制器 FK/IK，但应在运动前报告：

```text
该方案生成新的笛卡尔轨迹，但项目尚未集成经验证的碰撞模型
```

此时不会出现可执行的 `MOVE`。输入 `e` 退出。不要把 `require_collision_model_for_cartesian` 改成 `false` 来绕过检查。

方案三、四真实运动前至少还需要：

1. 机械臂自碰撞检查；
2. 桌面、杯子、工具和用户区域环境碰撞检查；
3. 奇异点和 IK 可操作度检查；
4. 连续 spline/blend 执行器；
5. 对实际 spline 重新检查速度、加速度和 jerk；
6. 仿真、空场、低速和单步真机验证。

## 24. 第十五步：跨回合测试

只有单轮正向和负向都验证完成后，才将 `--max-rounds` 调整为 3 或 4，依次测试：

```text
1, 1, 1
0, 0, 0
0, 0, 1
1, 1, 0
1, 0, 1
```

每轮都必须重新检查计划并输入 `MOVE`。取消、校验失败或运动失败的轮次不会进入历史。

## 25. 日志与通过标准

日志位于被 Git 忽略的 `logs/`，每轮记录：

- LLM 输入；
- LLM 输出；
- 编译元数据；
- 是否完成执行。

一轮测试至少满足以下条件才视为通过：

- JSON Schema 和语义校验通过；
- 本地轨迹安全检查通过；
- 现场操作员认可计划和路径；
- 低速运动过程中无异常振动、突跳、碰撞趋势或线缆拉扯；
- 软件停止和实体急停保持可用；
- 日志中的 `completed` 为 `true`；
- 机械臂最终安全返回 `anticipation_look_down`。

任何异常都应立即停止实验、保存日志并重新进行风险分析，不应通过放宽限制或删除检查来强行执行。
