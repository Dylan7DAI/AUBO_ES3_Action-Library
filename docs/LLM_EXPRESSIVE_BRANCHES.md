# LLM 情感动作五方案分支

每个方案分支都复用同一套 LLM 通信、结构化输出校验、跨回合状态、轨迹检查、日志与软件停机逻辑。分支中的 `scripts/llm_expressive_scheme.py` 是唯一的方案适配器；对应 Prompt 位于 `config/llm_scheme_*_prompt.txt`。

| 分支 | LLM 输出 | 本地决定 | 笛卡尔新轨迹 |
|---|---|---|---|
| `feature/llm-scheme-1-parameterized` | 固定轨迹的幅度、速度、加速度、节奏和重复参数 | 关节姿态插值与执行 | 否 |
| `feature/llm-scheme-2-laban` | 统一 Shape、Effort、Rhythm 向量 | 拉班向量到已示教关节姿态基的映射 | 否 |
| `feature/llm-scheme-3-residual` | 参考动作上的归一化小残差 | 残差物理限幅、TCP 路径、连续种子 IK | 是 |
| `feature/llm-scheme-4-keyframe-ik` | preparation/apex 两个归一化末端关键帧 | 语义坐标轴、密集路径、连续种子 IK | 是 |
| `feature/llm-scheme-5-candidates` | 三个不同策略的动作质量候选 | 逐候选安全过滤和风险感知评分选择 | 否 |

## 预览

先设置 `DEEPSEEK_API_KEY`，切换到目标方案分支，然后运行：

```bash
python3 scripts/llm_expressive_preview.py
```

预览程序只调用 LLM、校验 JSON 和显示计划，不连接机械臂。

## 真机入口

只有在完成现场风险评估、低速验证、确认实体急停可触达后，才使用：

```bash
python3 scripts/llm_expressive_robot.py \
  --confirm-motion I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT
```

每一轮仍必须在看到计划和本地编译元数据后输入 `MOVE`。输入其他内容会取消本轮，且不把该轮写入情感历史。

方案三和方案四会创建新的笛卡尔路径。当前 `config/expressive_motion_limits.json` 默认要求经验证的碰撞模型，因此这两个分支会在真机执行前 fail closed。工作空间、关节限位和 IK 连续性检查不能代替自碰撞、环境碰撞或人与机械臂距离模型。

## 软件停机

在另一个终端运行：

```bash
python3 scripts/emergency_stop.py
```

该命令锁存停止文件；正在运行的进程会请求 AUBO SDK 的 `stopJoint` 和 `stopLine`。直接连接并请求停止可使用：

```bash
python3 scripts/emergency_stop.py --direct
```

确认物理区域安全后才能解除锁存：

```bash
python3 scripts/emergency_stop.py \
  --clear \
  --confirm-clear I_CONFIRMED_THE_PHYSICAL_AREA_IS_SAFE
```

软件停止不是安全等级急停，不能替代机械臂控制柜的实体急停、安全回路、限速模式或现场监护。

## 本地硬检查

LLM 输出首先由 Pydantic Schema 和方案语义规则拒绝非法字段、范围、情感方向和跨回合变化。编译后还会检查：

- 当前关节到全部关键帧的关节限位和段跨度；
- 五次多项式采样后的速度、加速度与 jerk；
- TCP 工作空间；
- 方案三、四的连续种子 IK 和 IK 解分支跳变；
- 急停锁存、信号中断、每关键帧监控和运动超时；
- 笛卡尔生成方案是否具备经验证的碰撞模型。

检查失败时，本轮不执行、不写入情感历史。执行日志写入被 Git 忽略的 `logs/`。
