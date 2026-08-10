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

每个方案都在这五项之后增加必要的参数范围或本地选择信息，没有减少基础输入。

## 2. 五个方案总览表

| 方案与分支 | LLM 输入 | LLM 输出 | 输出到动作的映射 | 本地代码最终决定 | 当前真机状态 |
|---|---|---|---|---|---|
| 方案一：`feature/llm-scheme-1-parameterized` | 共同五项 + `parameter_limits` | 情感状态、变化类型、理由、`motion_profile` | 用 `spatial_extent` 等参数在已示教正向或负向关节姿态之间插值 | 动作家族、关键帧顺序、实际关节角、速度上限、轨迹检查和是否执行 | 可进行低速单轮验证 |
| 方案二：`feature/llm-scheme-2-laban` | 共同五项 + Laban `parameter_limits` | 情感状态 + Shape、Effort、Rhythm 向量 | 将 Laban 向量映射到少量共享的已示教关节姿态基 | 正负动作基、侧向基、关节插值、速度/加速度、振荡轨迹和安全拒绝 | 可在方案一验证后低速测试 |
| 方案三：`feature/llm-scheme-3-residual` | 共同五项 + `allowed_normalized_limits` | 情感状态 + 参考动作家族 + onset/apex 归一化小残差 | 对参考 TCP 增加小幅平移/旋转残差，生成密集 TCP 点，再做连续种子 IK | 物理限幅、参考姿态、语义方向、TCP 路径、IK、跳解检查和碰撞门控 | 当前禁止真实运动，只做 FK/IK 验证 |
| 方案四：`feature/llm-scheme-4-keyframe-ik` | 共同五项 + `normalized_keyframe_limits` + `required_phases` | 情感状态 + preparation/apex 两个归一化末端关键帧 | 将归一化位置和朝向 token 换算为 TCP 关键帧，插值后连续 IK | up/away/lateral 方向、厘米级尺度、朝向偏移、路径采样、IK 和碰撞门控 | 当前禁止真实运动，只做 FK/IK 验证 |
| 方案五：`feature/llm-scheme-5-candidates` | 共同五项 + 规则基线、近期动作向量、要求的三种策略和选择原则 | 情感状态 + Shape/Rhythm/Path 三个动作质量候选 | 本地分别按方案二式关节姿态基编译三个候选 | 逐候选安全过滤；按语义、新颖性、平滑度和安全裕度评分并选择；LLM 无权指定胜者 | 可在方案一、二验证后低速测试 |

### 2.1 与基线对比

本文所说的“现有方案”特指 main 中的固定情感等级映射模式，而不是五个新方案之间互相比较。

现有方案中，LLM 只输出：

```json
{
  "emotion_state": {},
  "intensity_level": 1,
  "brief_reason": "首次猜对，形成适度积极的结果回应"
}
```

本地程序随后固定决定：

- 猜对使用 `joyshout`，猜错使用 `disappointment`；
- 开始姿态固定为 `curiosity_down`；
- 结束姿态固定为 `anticipation_look_down`；
- 强度等级 0–3 固定映射到四档速度和重复次数；
- 猜对只重复 `arm_pulse`，猜错只重复 `breathe`；
- LLM 不输出动作质量、幅度、关键帧、坐标或多个候选。

它的核心特点是“LLM 负责情感判断，本地查表播放动作”。优势是实现简单、可预测、容易排查；局限是同一强度等级的几何动作高度相似，难以明显区别于人工规则驱动表达，也很少利用 LLM 输出的不确定性。

### 2.2 五个新方案相对现有方案的差异、好处与劣势

| 方案 | 相对现有方案的核心区别 | 主要好处 | 主要劣势 | 更适合回答的研究问题 |
|---|---|---|---|---|
| 方案一：固定轨迹连续参数化 | 从四档 `intensity_level` 查表，改为让 LLM 输出同一轨迹内部的连续幅度、速度、加速度、停留和节奏参数 | 改动最小；能产生比四档速度/重复更细的连续变化；仍围绕已示教姿态，易于解释和低速验证 | 几何结构仍固定；情感之间可能只是“同一动作放大或加速”；部分参数尚未完全进入执行器 | 连续参数调节是否比离散强度等级更自然、更能体现跨回合累积？ |
| 方案二：统一 Laban 参数 | LLM 不再输出情感等级，而是输出与所有情感共享的 Shape、Effort、Rhythm 向量 | 参数具有动作理论解释；少量共享姿态基可生成更多组合；joy、relief、excitement 不需要各自维护大动作库 | 需要人工设计 Laban 到轨迹的映射；某些参数目前只参与语义约束；参数之间可能耦合，LLM 输出正确不等于动作可辨识 | 理论驱动的动作质量参数是否能提高情感可解释性和跨情感复用能力？ |
| 方案三：参考动作加受限残差 | 不仅改变既有轨迹的幅度，还允许 LLM 在示教参考 TCP 周围提出小幅空间和朝向残差 | 在保留示教动作骨架的同时增加几何新颖性；连续种子 IK 比完全从零生成姿态更不容易跳到完全不同的关节构型 | 引入 IK、奇异点和碰撞风险；依赖坐标轴和参考动作质量；需要碰撞模型；当前不能真实运动 | 在保留人工示教风格的前提下，LLM 生成的小残差能否带来可感知但安全的个性差异？ |
| 方案四：归一化关键帧与 IK | LLM 从调节既有动作转为提出 preparation/apex 两个抽象末端关键帧和路径类型 | 几何表达空间最大；不需要为每种情感维护完整动作文件；可研究 LLM 是否能构造新的空间隐喻 | 最容易产生不自然关节构型、奇异姿态或环境碰撞；连续 IK 成本高；当前逐点 `moveJoint` 不够流畅；必须先补碰撞和 spline 执行 | 更自由的关键帧规划能否产生超越示教库的情感表达，同时仍保持可理解和可执行？ |
| 方案五：多候选与本地选择 | 现有方案每轮只有一个确定结果；方案五让 LLM 生成三个不同候选，再由本地安全过滤和评分选择 | 直接利用 LLM 的采样不确定性；安全候选之间可以兼顾语义与新颖性；LLM 无权绕过安全检查指定胜者 | 每轮调用和编译成本更高；评分权重本身是人工规则；若候选都相似或都不安全，收益有限；新颖性指标未必等于人类感知差异 | “LLM 提案、本地安全选择”是否比单一输出更能产生安全、多样且不机械重复的表达？ |

### 2.3 方案一的比较分析
方案一让 LLM 当“动作调节器”，本地程序负责按照 LLM 给出的幅度、速度、节奏和重复次数，调整预设动作的表现强弱。

**区别：** 现有方案把连续情感强度压缩为 0–3 四个等级，再查表得到速度和重复次数；方案一保留相同的正负固定动作骨架，但让 LLM 直接控制多个受限连续参数。

**好处：**

- 最容易从现有系统迁移，仍然使用同一批已示教关节姿态；
- 可以分别改变幅度、节奏重音、停留和重复，不再只有四档速度；
- 输出参数与跨回合情感状态可以形成更细的对应关系；
- 风险和实现复杂度在五个新方案中最低，适合作为实验基线。

**劣势：**

- 动作顺序和姿态基没有变化，多样性上限较低；
- 观察者可能把差异主要理解为“更快/更大”，而不是不同情感品质；
- `return_speed_scale` 当前只进入元数据，尚未独立控制返回阶段；
- 连续参数增多后，仍需要实验确认哪些变化能被用户稳定感知。

**相关论文与本方案关系：**

- [Generative Expressive Robot Behaviors using Large Language Models（Mahadevan et al., HRI 2024）](https://arxiv.org/abs/2401.14673)：让 LLM 根据社会情境，使用机器人已有技能生成参数化控制代码，是“LLM 设定表达参数、本地已有技能执行”的直接先例。该工作允许生成和组合控制代码，方案一则把 LLM 权限进一步收紧为固定动作内部的连续参数。
- [Designing Behaviors of Robots Based on the Artificial Emotion Expression Method in Human–Robot Interactions（Li and Zhao, 2023）](https://www.mdpi.com/2075-1702/11/5/533)：使用偏移、加速度和时间间隔等运动参数表达不同情感，为方案一选择幅度、速度、加速度和节奏作为可调维度提供依据；该工作不是 LLM 方法。

因此，参数化情感动作和 LLM 调用参数化技能均已有文献先例；本项目的组合创新是让 LLM 根据跨回合游戏历史直接输出一组受限连续参数，并在执行前由本地程序校验和限幅。

### 2.4 方案二的比较分析
方案二让 LLM 当“动作风格导演”，本地程序负责把上扬、舒展、突然、弯曲等风格参数，安全地翻译成基于示教姿态的机械臂关节动作。

**区别：** 现有方案输出离散强度，方案二输出所有情感共享的 Laban 动作质量向量，再由本地生成关节动作。

**好处：**

- Shape、Effort、Rhythm 为动作差异提供统一解释框架；
- 情感不再与单个动作文件一一绑定，可用少量姿态基覆盖多种情感；
- 可以区分“强度相同但动作质量不同”的 joy、relief 和 excitement；
- 比直接生成坐标更容易控制和说明。

**劣势：**

- Laban 概念不能自动变成机械臂轨迹，映射仍由设计者实现；
- `freedom`、`lightness` 当前还没有完全独立的运动学效果；
- 参数较多时可能出现语义正确但动作视觉差异不明显的情况；
- 需要用户研究验证映射是否真的被观察者理解为预期情感。

**相关论文与本方案关系：**

- [Employing Laban Shape for Generating Emotionally and Functionally Expressive Trajectories in Robotic Manipulators（Raghu et al., RO-MAN 2025）](https://arxiv.org/abs/2505.11716)：直接将 Laban Effort 和 Shape 用于机械臂表达轨迹，并研究 Happy、Sad、Shy 和 Angry，是方案二最接近的机械臂文献。
- [Affective Movement Generation using Laban Effort and Shape and Hidden Markov Models（Samadani et al., 2020）](https://arxiv.org/abs/2006.06071)：使用 Laban 表示动作质量，并在保留目标动作路径的同时生成带有目标情感的调制动作，支持“统一动作质量空间可以跨情感复用”的思路；该工作使用 HMM 和动作数据，而不是 LLM。
- [Laban Effort and Shape Analysis of Affective Hand and Arm Movements（Samadani et al., ACII 2013）](https://research.monash.edu/en/publications/laban-effort-and-shape-analysis-of-affective-hand-and-arm-movemen/)：研究位置、速度、加速度、jerk 和轨迹曲率等可测量特征与 Laban Effort/Shape 的关系，可作为将抽象 Laban 参数落实为轨迹特征时的理论参考。

因此，Laban 到情感动作的理论和机器人实现并非本项目首创；本项目的组合创新是让 LLM 根据跨回合情境输出统一 Laban 向量，再由受限的共享关节姿态基本地编译和安全拒绝。当前代码仍是部分映射原型，不应声称完整复现了上述论文中的 Laban 运动生成方法。

### 2.5 方案三的比较分析
方案三让 LLM 当“参考动作修改器”，本地程序先选择一个安全的基础动作，再将 LLM 提出的微小位置、方向和路径调整叠加上去。

**区别：** 现有方案只能选择固定动作和等级；方案三允许在已示教参考动作附近修改 TCP 空间与朝向，但不允许 LLM直接输出物理坐标。

**好处：**

- 比单纯调整速度和幅度具有更明显的几何差异；
- 参考动作保留人工设计的动作骨架，有助于维持风格和关节构型；
- 归一化残差经过厘米级本地限幅，LLM 无法无限扩大；
- 连续种子 IK 和跳解检查降低不同 IK 分支导致的突变概率。

**劣势：**

- IK 连续不代表动作一定拟人，也不代表路径没有碰撞；
- 残差叠加后可能削弱原参考动作的情感可读性；
- 对基坐标、TCP、工具和示教参考的一致性要求高；
- 当前缺少经过验证的碰撞模型，因此只能测试 FK/IK，不能真实运动。

**相关论文与本方案关系：**

- [Affective Movement Generation using Laban Effort and Shape and Hidden Markov Models（Samadani et al., 2020）](https://arxiv.org/abs/2006.06071)：在保留期望运动路径的同时叠加目标情感，是“参考动作骨架＋情感调制”的直接概念先例，但其调制来自数据和 HMM，不是 LLM 输出的小幅 TCP 残差。
- [Transferring Human Emotions to Robot Motions using Neural Policy Style Transfer（Fernandez-Fernandez et al., 2023）](https://doi.org/10.1016/j.cogsys.2023.05.010)：使同一个机器人动作以 angry、happy、calm 或 sad 等不同风格执行，支持“动作内容保持、表达风格变化”的思路；它使用神经策略风格迁移，不是结构化 LLM 残差。
- [Cost Functions for Robot Motion Style（Zhou and Dragan, 2018）](https://arxiv.org/abs/1809.00092)：在正常任务成本和约束上增加风格成本，使任务轨迹表达特定风格或情感，说明可以在不替换任务主体的情况下调制运动风格。

目前没有发现与本方案完全相同的“LLM 输出归一化厘米级 TCP 残差、本地限幅并连续种子 IK”的情感机械臂论文。因此，参考动作调制有充分先例，但具体的 LLM 残差接口与安全分层属于本项目较明显的组合创新。

### 2.6 方案四的比较分析
方案四让 LLM 当“空间动作编舞者”，由 LLM 描述机械臂末端需要经过的几个相对位置和朝向，本地程序通过 IK、安全检查和轨迹规划将其转换成关节动作。

**区别：** 现有方案从动作库查表，方案四让 LLM 直接提出两个抽象末端关键帧及其路径质量，本地再换算为厘米级 TCP 目标并反解关节轨迹。

**好处：**

- 对现有动作文件的依赖最小，潜在动作空间最大；
- preparation/apex 结构仍为生成过程提供了最小叙事骨架；
- 归一化坐标和朝向 token 比任意绝对坐标更容易约束；
- 能测试 LLM 是否会提出人工规则没有预设的新空间表达。

**劣势：**

- IK 得到的关节姿态可能不像人手或不符合预期身体逻辑；
- 两个末端点不足以保证中间姿态、肘部构型和路径风格自然；
- 碰撞、奇异点和执行连续性风险最高；
- 当前通用执行器逐点等待 `moveJoint`，不能充分表现连续弧线；
- 当前只能做生成和 FK/IK 验证，不能真实运动。

**相关论文与本方案关系：**

- [EMOTION: Expressive Motion Sequence Generation for Humanoid Robots with In-Context Learning（Huang et al., 2024）](https://arxiv.org/abs/2410.23234)：让 LLM 生成手部笛卡尔位置、朝向和手指状态组成的连续表达动作序列，再通过 IK、轨迹插值和跟踪执行，是方案四最直接的“LLM 末端序列＋IK”先例。论文中的实验动作在部署前还经过仿真平台和研究人员验证选择，不能等同于无人复核的直接实机生成。
- [Enabling Waypoint Generation for Collaborative Robots using LLMs and Mixed Reality（Fang et al., 2024）](https://arxiv.org/abs/2403.09308)：使用 LLM 理解工作空间并生成协作机械臂 waypoint，同时用增强现实预览结果，证明 LLM waypoint 生成可以接入真实机械臂部署流程；主要任务是机器人编程，不专门研究情感表达。
- [Swarm-GPT: Combining Large Language Models with Safe Motion Planning for Robot Choreography Design（Jiao et al., 2023）](https://arxiv.org/abs/2312.01059)：由 LLM 产生创意 waypoint，再由模型驱动规划器保证轨迹可行和无碰撞，支持“LLM 负责空间创意、本地规划器负责物理安全”的分层设计；对象是无人机群，不是机械臂。
- [IKLink: End-Effector Trajectory Tracking with Minimal Reconfigurations（Wang et al., 2024）](https://arxiv.org/abs/2402.16154)：为每个末端 waypoint 生成多个 IK 解并连接成重构次数较少的关节轨迹，说明末端轨迹反解时需要显式处理 IK 分支连续性；该工作不是 LLM 或情感表达方法。

因此，方案四的总体技术路线已有明确先例。本项目的特点是将输出压缩为 preparation/apex 两个归一化情感关键帧，并把物理尺度、用户方向、路径采样、连续 IK 和安全拒绝全部保留在本地。不过，当前实现尚未达到 EMOTION 的连续轨迹执行能力，也尚未具备用于真实运动的碰撞模型。

### 2.7 方案五的比较分析
方案五让 LLM 当“动作创意提案者”，一次提出多个不同风格的动作方案，再由本地程序担任“安全评委”，排除危险动作并选择安全、合适且与以往不同的方案。

**区别：** 现有方案每轮由 LLM 给出一个情感等级，方案五让 LLM 给出三个具有不同侧重点的候选，本地再决定实际执行者。

**好处：**

- 将 LLM 的随机性转化为可选择的候选集合，而不是直接执行随机结果；
- 所有候选分别接受硬性安全过滤，安全优先于新颖性；
- 可以主动选择区别于固定规则基线和近期动作的方案；
- 评分过程和拒绝原因写入元数据，便于实验分析和复现。

**劣势：**

- 三个候选意味着更长的输出、更高的 token 和本地编译成本；
- 0.40/0.25/0.15/0.10/0.10 权重仍是人工设计，需要实验校准；
- LLM 可能生成表面数值不同但视觉效果相似的候选；
- 当前近期新颖性来源于上一轮候选集合，不完全等同于上一轮实际执行动作；
- 相比方案一、二，失败和调试路径更复杂。

**相关论文与本方案关系：**

- [SayCan: Grounding Language in Robotic Affordances（Ahn et al., 2022）](https://say-can.github.io/)：将语言模型判断的任务相关性与本地技能价值函数给出的可执行概率结合，再选择最终技能，是“LLM 提案、本地可行性评分并决定执行者”的经典先例；它选择任务技能，而不是多个情感动作质量候选。
- [Swarm-GPT: Combining Large Language Models with Safe Motion Planning for Robot Choreography Design（Jiao et al., 2023）](https://arxiv.org/abs/2312.01059)：采用生成模型提供创意、模型规划器保证安全可行性的分层方式，为方案五中“创造性不能绕过本地安全门”提供参考。
- [Generative Expressive Robot Behaviors using Large Language Models（Mahadevan et al., HRI 2024）](https://arxiv.org/abs/2401.14673)：证明 LLM 可以利用社会情境生成可组合的表达行为，但它没有采用本方案的三个候选、近期新颖性和本地加权选择机制。

因此，“LLM 提案、本地可行性评分”已有成熟先例；目前没有发现与本方案完全相同的“三个情感风格候选＋逐候选安全过滤＋语义、规则基线差异、近期新颖性、平滑性和风险裕度加权选择”。后者主要是本项目为了利用 LLM 输出不确定性而提出的组合设计。

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
方案二让 LLM 当“动作风格导演”，本地程序负责把风格参数安全地翻译成基于示教姿态的机械臂关节动作。
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

**当前实现状态：部分实现，可执行，但不是完整的 Laban 轨迹生成器。**

方案二已经实现了从 LLM 输出的 `motion_quality` 到 `CompiledMotion` 关节关键帧、速度、加速度和停留时间的编译。因此，它不是“全部待实现”：通过通用执行器完成安全检查后，这些关键帧可以被发送给机械臂。但是，当前实现依赖少量已示教关节姿态作为共享动作基，并不是从 Laban 向量独立生成任意几何轨迹；部分输出字段也尚未产生独立的底层运动效果。

本地使用以下共享姿态基：

- 正向上扬：`joy_lift_max`；
- 正向舒展：`joy_shout_peak_max`；
- 负向下沉：`disappointment_retreat_max`；
- 负向回避：`disappointment_turn_away`；
- 曲线中间姿态：`curiosity_left` 或 `curiosity_right`。

实际映射如下：

| LLM 输出参数 | 当前本地映射 | 实现状态与限制 |
|---|---|---|
| `vertical_shape` | 取绝对值并限制到 `0.25–1.00`，作为起始姿态向正向或负向 vertical basis 的关节插值比例 | 已生效。参数正负号本身不选择动作方向；本地根据 `current_result` 选择 `joy_lift_max` 或 `disappointment_retreat_max`，符号主要由输出校验保证 |
| `radial_shape` | `abs(radial_shape) * 0.35` 后限制到 `0.05–0.45`，作为舒展或回避姿态基的混合权重 | 已生效。猜对映射到 `joy_shout_peak_max`，猜错映射到 `disappointment_turn_away`；不是对末端径向距离的直接控制 |
| `depth_shape` | 当值小于或等于 `-0.25` 时选择 `curiosity_left`，否则选择 `curiosity_right` 作为侧向中间姿态基 | 部分生效，但目前没有实现真实的“接近用户/远离用户”深度轴映射；它当前更接近离散的左右路径选择器 |
| `curvature` | 与 `(1 - directness)` 相乘，再乘最大权重 `0.12`，决定 preparation 姿态向侧向姿态基弯折多少 | 已生效，但产生的是一个侧向中间关节关键帧，不是笛卡尔空间中的连续圆弧或样条 |
| `directness` | 抑制上述侧向弯折；越接近 `1.00`，中间姿态越接近直接路径 | 已生效，但只通过一个中间关键帧间接表示 Direct/Indirect |
| `suddenness` | `speed = (0.16 + 0.12 * suddenness) / duration_scale`；`acceleration = 0.28 + 0.22 * suddenness` | 已生效，分别改变通用关节执行速度和加速度；仍会受到本地安全上限约束 |
| `duration_scale` | 降低或提高全局速度，同时参与顶点停留时间计算 | 已生效，但不是逐段独立时长规划 |
| `hold_ratio` | 顶点停留时间为 `0.30 * hold_ratio * duration_scale` 秒 | 已生效；按当前范围约为 `0.012–0.094` 秒 |
| `oscillation_count` | 在 apex 后插入相应次数的“apex → 小幅回落 → apex”关键帧对 | 已生效，最多两次 |
| `oscillation_scale` | 回落姿态在 apex 到 preparation 方向上的插值比例为 `0.12 * oscillation_scale` | 已生效；当前最大只移动两者差值的 `6%`，属于受限小幅脉冲 |
| `freedom` | 当前不参与 `compile_motion()` 的关键帧、速度、加速度或停留时间计算 | 尚未形成独立运动效果，只存在于 LLM 输出、范围校验和日志元数据中 |
| `lightness` | 当前不参与 `compile_motion()` 的动力学或轨迹计算 | 尚未形成独立运动效果，只存在于 LLM 输出、范围校验和日志元数据中 |

当前关节动作生成顺序为：

```text
固定起始姿态 curiosity_down
→ 根据 current_result 选择正向或负向姿态基
→ vertical_shape / radial_shape 生成 preparation 与 apex 关节插值姿态
→ curvature / directness 加入一个受限侧向中间姿态
→ 按 oscillation_count / oscillation_scale 插入有限脉冲
→ 固定返回姿态 anticipation_look_down
→ 通用安全检查
→ 用户输入 MOVE 后由 moveJoint 逐关键帧执行
```

其中，正负动作家族、共享姿态基、最终关节角、执行顺序以及是否拒绝执行均由本地代码决定。LLM 只提供情感状态和动作质量参数，不能输出动作名称、关节角、末端坐标或控制指令。

### 4.4 尚未完成的映射

如果要把方案二称为“完整的统一 Laban 动作参数生成器”，仍需补全以下部分：

- 将 `depth_shape` 映射到经过用户坐标系标定的真实接近/回撤方向，而不是左右姿态二选一；
- 将 `freedom` 映射到分段融合、路径连续性或受限的 blend/spline 参数；
- 将 `lightness` 映射到经过安全限制的速度曲线、加速度、减速度或 jerk/easing，而不是简单提高速度；
- 将 `curvature` 和 `directness` 映射到经碰撞检查的连续曲线路径，而不只是一个侧向关键帧；
- 对各参数做消融实验和用户感知实验，确认参数变化确实产生预期的 Laban 质量与可识别情感；
- 在实机标定后重新设置速度、加速度、关节范围、工作空间和人与机械臂距离限制。

因此，当前方案二适合定义为“可运行的关节姿态基映射原型”。它已经足以进行低速、单变量的初步实机比较，但不应把 `depth_shape`、`freedom`、`lightness` 或连续曲率声称为已经完整实现。

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
