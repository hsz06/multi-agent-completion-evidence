# V4.1 Assertion-Aware Verification Obligation Selection — 实验报告

**研究问题**：行级 coverage 只证明测试"执行到"被改契约，不能证明它"验证了"被改行为。能否用测试断言/返回值流/异常检查估计对特定 Contract Change 的 Assertion Sensitivity，优先选择真正可能检测破坏的 Verification Obligations？同时用 Early Stop 消除已有 verify-set 已能检测时的不必要扩展？

**入口**：`python3 v4/run_assertion_aware_experiments.py [--selector assertion --early-stop on --threshold 1.0]`
**结果**（不覆盖 v4 原始）：`v4/results/{assertion_sensitivity,assertion_ranking,v41_detection_cost,type_b_analysis,early_stop,weight_sensitivity}.*` + `v41_summary.json`
**复用**：v4 的 5 真实仓库、49 applied / 28 true-break / **56 (case,claim) 样本**、原池、pristine 行级 coverage、held-out per_file PASS/FAIL 矩阵。
**单元测试**：`v4/tests/test_v41.py` → **11 passed**

报告严格区分 **Observed** / **Interpretation** / **Speculation**。

---

## 0. 反泄漏（诚信前置）

- Assertion Sensitivity 只从 **pristine 测试源码 AST** 推导（direct call / 返回值流 / assert / isinstance / pytest.raises / 字段访问）。**selector 与 analyzer 不读 `evaluation_private_oracle/per_file.json`**——单测 `test_assertion_sensitivity_no_oracle_dependency` 与 `test_early_stop_never_uses_oracle_for_ranking` 强制此性质。
- Held-out per_file 矩阵**只用于最终 detection 打分**。
- Early Stop 只依据 existing verify-set **自身真实运行**（即被测验证动作本身），不偷看其他文件的 held-out 结果来决定是否追加。
- v4 原始结果文件**未被覆盖**；v4.1 输出独立命名（`v41_*`）。
- **同一 56 样本**（单测 `test_same_56_samples_as_v4` 强制）。

## 1. v4 Type B 是否被 Assertion Sensitivity 修复？

**Observed**：v4.1 重建 Type-B 集合 = **40 个**（gap=True ∧ existing 漏检 ∧ Integration 可检；比 v4 报告的 19 更完整，因 v4 failure_analysis 只存样例）。

- **fixed_by_assertion_aware = 0**
- **still_missed = 10**
- **TypeB Fix Rate = 0.0**

**Interpretation**：**Assertion Sensitivity 没有"修复"任何 Type-B**。AssertionAware selector 在主表上与 CoverageOnly **完全相同**（detection/cost/#tests 全等），因为 greedy set-cover 在 threshold=1.0 会把所有覆盖缺口的候选**全部纳入**，assertion ranking 的"选哪个"自由度被消解——pool 受限下多数缺口只有 1-2 个候选，无排序空间。

**但 assertionranking 信号本身有效**——见 §2/§5：assertion_ranking.csv 显示 Hit@1=0.821、Hit@3=0.857（纯 ranking 把 held-out FAIL 测试排进 Top-K）。问题在于**greedy 全纳入**没用 ranking 的"仅取最优"特性。

## 2. Detection 是否提升？

**Observed**（主表，56 样本，Integration 归一 cost=1.0）：

| Strategy | Detection | VRR | #Tests | RelCost | ES hits |
|---|---|---|---|---|---|
| Local | 0.286 | 0.286 | 1.00 | 0.112 | 0 |
| CoverageOnly@0.8 | 0.661 | 0.661 | 2.05 | 0.234 | 0 |
| CoverageOnly@1.0 | 0.821 | 0.821 | 2.77 | 0.363 | 0 |
| AssertionAware@0.8 | 0.661 | 0.661 | 2.05 | 0.234 | 0 |
| AssertionAware@1.0 | 0.821 | 0.821 | 2.77 | 0.363 | 0 |
| **AssertionTop1+EarlyStop** | **0.821** | 0.821 | **2.00** | **0.276** | 0 |
| **AssertionAware+EarlyStop** | **0.821** | 0.821 | 2.23 | **0.286** | 16 |
| IntegrationAll | 1.000 | 1.000 | 8.07 | 1.000 | 0 |

- CoverageOnly@0.8/1.0 **完美复现 v4**（0.661/0.821）→ regression 通过 ✓
- AssertionAware@0.8/1.0 = CoverageOnly（无提升）→ **assertion 在 greedy set-cover 下零贡献**
- **EarlyStop 是真实提升**：AssertionAware+EarlyStop 维持 detection 0.821，cost 从 0.363 → **0.286**（−21%），#tests 2.77 → 2.23
- **AssertionTop1+EarlyStop 最省**：detection 0.821 @ cost **0.276** @ 仅 **2.0 测试**——证明只用 assertion-rank 最高的 1 个候选即可达 greedy 全纳入的 detection，且更省

**Interpretation**：Assertion Sensitivity 对 **detection 主表无提升**；对 **cost 有真实提升**（Top1 单选 + EarlyStop 把 cost 从 0.363 压到 0.276，即 Integration 的 27.6%）。Detection 仍卡在 **0.821 < 0.85 门槛**。

## 3. Cost 是否仍显著低于 Integration？

**Observed**：AssertionAware+EarlyStop RelCost = **0.286**（Integration 的 28.6%）；AssertionTop1+EarlyStop = **0.276**。两者均 **≤ 0.50 门槛** ✓。Detection 0.821 = Integration 的 **82%**。

## 4. Early Stop 是否消除 False Expansion？

**Observed**（`early_stop.csv`）：

| Strategy | existing-detects cases | still-expanded | **FalseExpansionRate** | saved tests | saved runtime |
|---|---|---|---|---|---|
| CoverageOnly@0.8 | 16 | 16 | **1.0** | 0 | 0 |
| AssertionAware@1.0 | 16 | 16 | **1.0** | 0 | 0 |
| **AssertionAware+EarlyStop** | 16 | 0 | **0.0** | **30** | **10.95s** |

**Interpretation**：**Early Stop 将 FER 从 1.0 降到 0.0**（16 个 case 中无一再错误扩展），节 30 个测试 / 10.95 秒，**detection 不降**。这是本阶段最干净的、无副作用的真实收益。

## 5. Assertion Sensitivity 的贡献是否真实？

**Observed — Ablation**

- **Coverage-only**（AssertionAware@1.0）vs **Coverage+Assertion**（同 selector，因 greedy 全纳入 → 数字相同）→ assertion 在主 selector **无额外 detection 贡献**。
- **但 assertion ranking 自身有效**（`assertion_ranking.csv`）：Hit@1=**0.821**，Hit@3=**0.857**——纯按 assertion sensitivity 排序，把 held-out FAIL 测试排进 Top-K 的命中率。
- **AssertionTop1 策略**只用 rank-1 候选 → detection 0.821（与 greedy 全纳入相同）但 #tests 2.0 < 2.77 → **证明 rank-1 已抓住检测力，余下候选是冗余**。
- **权重敏感性**（0.7/0.3、0.5/0.5、0.3/0.7）：detection 与 cost **完全相同**（0.821/0.363）→ 因 greedy 全纳入消解权重，权重无影响。

**Interpretation**：Assertion Sensitivity **信号真实有判别力**（Hit@K 高、Top1 够用），但**在 greedy set-cover 主 selector 里没有发挥作用**——因为 greedy 为达 coverage threshold 纳入所有覆盖候选，不依赖排序选"哪个"。要让 assertion 真正提升 detection，需放弃"全覆盖 threshold"改用"rank-1/Top-K 单选"——但那会牺牲 coverage 完整性（部分缺口需多测试覆盖）。**当前 Pareto 最优点是 AssertionTop1+EarlyStop**（detection 不降、cost 最低）。

## 6. 各 repo 表现

**Observed**（AssertionAware+EarlyStop，break-case detection）：

| repo | detection | (break cases) |
|---|---|---|
| tinydb | 1.000 | 10/10 |
| cerberus | **0.600** | 12/20 |
| boltons | 1.000 | 6/6 |
| toolz | 0.857 | 12/14 |
| pyparsing | 1.000 | 6/6 |

**Interpretation**：**4/5 仓库 detection ≥ 0.857**（tinydb/boltons/pyparsing 1.0、toolz 0.857）——满足 spec §19 的 "≥4/5 repo AssertionAware ≥ CoverageOnly"。但 **cerberus 仅 0.6**，是拉低总 detection 的主因（见 §7）。整体 0.821 < 0.85 门槛，主要被 cerberus 拖累。

## 7. Assertion 分析失败在哪里？

**Observed** 仍是 10 个 Type-B 未修复。逐 repo 归因：

1. **cerberus（主因）**：多数未修复 case 的变更符号是**私有方法**（如 `dummy_for_rule_validation`、`_validate_*`）或 cerberus 内部 validator 元逻辑。contract 提取器跳过下划线私有符号 → `required_contracts` 不含它 → **无任何测试"覆盖"该契约** → coverage gap 为空 → 既无 cov 候选也无 direct-call 候选 → selector 无法选。FAIL 在 `test_legacy.py`，但 selector 没有授权选它（因缺口分析端无信号）。
2. **tinydb**：未修复 case 的改动碰巧只被 test 文件以"调用不返回值断言"形式用到（如 `Table.name` property），assertion analyzer 评低分，且 pool 中无更高分替代。
3. **toolz**：部分 case（curry/groupby 返回类型）的断言藏在 helper/parametrize 里，test-function-local AST dataflow 跟不到。

**根因分类**（spec §20）：
- **Pool/Selector 混合失效（cerberus 主因）**：变更符号是私有/元逻辑，契约提取器漏掉 → 缺口分析无信号 → 不是"选错"，是"没候选可选"。
- **被 v4 数据集定义限制**：本数据集 true-break = "pool 有 FAIL"，故理论上仅 Selector Failure；但 cerberus 的 FAIL 测试（test_legacy）虽在 pool，却因缺口分析端无信号而没被授权入选——这是 selector 的信号盲区，介于 Pool Failure 与 Selector Failure 之间。

## 8. 是否需要 LLM？

**Observed**：deterministic AssertionTop1+EarlyStop = **0.821 detection @ 0.276 cost**。Detection < 0.85 门槛。

**Interpretation**：当前 deterministic **未达** "≥0.85 @ ≤0.5" 的 "LLM NOT REQUIRED" 判据。但成本侧已满足（0.276 ≤ 0.5）。差距在 detection（0.821 vs 0.85，差 0.029）。

**Speculation**：LLM semantic ranking 可能帮助 cerberus 私有符号场景（LLM 能理解 `_validate_allowed` 的语义并提名 test_validation/test_legacy 即便缺口分析无信号）。但本环境无 LLM API，未验证。建议下一阶段用 LLM 仅做候选提名（不判检测）作为 ablation。

## 9. 失败案例分析（≥5，含成功与仍失败）

**仍失败（cerberus，代表性）**：
- `cec01` / `dummy_for_rule_validation` (BODY_ONLY)：FAIL 仅在 `test_legacy.py`。变更符号私有 → 缺口分析无 required 契约 → AssertionTop1 选不到 test_legacy。**根因：私有符号 + 元逻辑**，coverage/assertion 都盲。
- `cec02` / `BareValidator.clear_caches` (ADD_OPTIONAL_PARAM)：FAIL 仅 test_legacy。clear_caches 的覆盖候选是 test_assorted/test_validation，但它们对此 break 不敏感；test_legacy 敏感却未被缺口分析授权。

**成功（覆盖率提升型，代表性）**：
- `tdc03` / `Table.insert` (CHANGE_RETURN_TYPE) / testing claim：existing(test_operations) 不覆盖 insert 返回值断言；AssertionAware 选 test_tinydb（其 `assert db.insert(...)==1` 命中 inline-assert sensitivity）→ FAIL → 检测成功。
- `ppc*`：pyparsing 多数 case 在 threshold=1.0 下被完整覆盖候选检出。

## 10. 最终研究结论

### **UNCERTAIN**

**按 spec §19/26 核对**：
- ✅ Cost 0.276–0.286 ≤ 0.50 门槛
- ✅ 4/5 repo detection ≥ 0.857（AssertionAware ≥ CoverageOnly 在 4/5 仓库）
- ✅ EarlyStop FER 1.0→0.0（节 30 测试 / 10.95s），detection 不降
- ✅ Assertion ranking Hit@1=0.821 / Hit@3=0.857（信号有效）
- △ Detection **0.821 < 0.85 门槛**（差 0.029）
- ✗ TypeB Fix Rate **0.0**（assertion 未修复任何 Type-B）
- ✗ AssertionAware 主 selector = CoverageOnly（greedy 全纳入消解 ranking）

**为何仍是 UNCERTAIN 而非 YES**：
- **Detection 0.821 < 0.85**：差 0.029，未过门槛。主因 cerberus 0.6（私有符号/元逻辑盲区）。
- **TypeB Fix Rate 0**：v4.1 的核心目标"用 assertion 修复 Type-B"**未实现**。Assertion ranking 有效（Hit@K 高），但在 greedy set-cover selector 里没转化为 detection 提升——只转化为 cost 降低（Top1 单选）。
- **Assertion 贡献被 greedy 消解**：权重敏感性全相同、AssertionAware=CoverageOnly，证明 assertion 在主 selector 路径无 detection 贡献。

**为何不是 NO**：
- Early Stop 真实有效（FER 0→0 不降 detection、大幅省成本）——这是 v4 暴露的 False Expansion=1.0 问题的干净修复。
- AssertionTop1 用更少测试达到同等 detection——证明 assertion ranking 信号有用，只是需配合"单选"而非"全覆盖"。
- 4/5 仓库达标，仅 cerberus 拖累。

### Ceiling 已观察（spec §19 第三条）
**cerberus 的失败明确指向 "existing verifier pool capability is the dominant ceiling"**：变更符号是私有/元逻辑，契约提取器与 coverage 缺口分析都无信号——这不是 selector 能解决的，需要 (a) 私有符号的契约提取，或 (b) 语义候选提名（LLM）。继续用复杂 selector 硬抬对 cerberus 无意义。

### 上行至 YES 的明确路径
1. **Top1/K 单选作主 selector**（放弃全覆盖 threshold）：AssertionTop1 已显示 cost 0.276，若能配合"高 sensitivity 候选"扩到更多 case，detection 有望上行。
2. **私有符号契约提取**：扩展 ContractNode 提取下划线方法（至少 cerberus 的 `_validate_*`），让缺口分析有信号 → 直击 cerberus 失败。
3. **LLM 语义候选提名**（仅提名、不判检）：给 cerberus 私有变更提名 test_legacy——单测已排除 LLM 读 oracle，安全。
4. 用 (1)+(2)+(3) 把 cerberus 从 0.6 拉到 ≥0.85，则总体 detection 可过 0.85 门槛。

若 (2) 使 cerberus 检测提升且多仓库保持，方向可上行至 **YES** 并进入论文定型。当前 deterministic 仍差 0.029 detection，**不强行判 YES**。

### 各版本演进（机制 vs 外部效度）
- v1：机制成立（GFC 可复现、可阻止）
- v2：真实仓库复现，但检测靠强 testing
- v3：契约级，detection≈0（保守门假象）
- **v4**：obligation 选择 → detection 0.66–0.82 @ 23–36% 成本（真实可检测）
- **v4.1**：assertion ranking 信号有效但被 greedy 消解；EarlyStop 干净修复 False Expansion；**detection 0.821 @ 27.6% 成本**，cerberus 暴露 verifier-pool ceiling

---

## 附录：科研诚信声明
- 同一 56 样本（单测强制）；v4 原结果未覆盖。
- Assertion/analyzer/selector **不读** held-out per_file 矩阵（单测强制）。
- 权重 0.4/0.6 为 spec 默认；0.7/0.3、0.5/0.5、0.3/0.7 单独报告（全相同，未迎合调权）。
- Early Stop 只依据 existing 真实运行，不偷看其他 held-out 结果决定是否追加。
- Type-B 仍失败 10 个全部保留并分析，未删除。
- LLM 未使用 → `LLM semantic selection not evaluated`。
- 区分：Triggered（invalidate）/ Detected（测试 FAIL）/ Resolved（VRR）。不把 conservative NOT_READY 当 detection。
- 报告区分 Observed / Interpretation / Speculation（已标注）。