# V4.2 Hybrid Semantic Verification Obligation Augmentation — 实验报告

**研究问题**：(RQ1) Private/internal contract 进入 ContractGraph 能否修复 deterministic blind spot？(RQ2) LLM 语义候选增强能否提名 deterministic 无法提名但真实相关的 verifier？

**入口**：`python3 -m v4.v42_experiments`（真实 LLM track，本环境有 Anthropic-compatible 端点 → GLM-5.2）
**结果**（不覆盖 v4/v4.1）：`v4/results/v42_*.csv|json|jsonl`
**复用**：5 仓库、49 applied / 28 true-break / **56 (case,claim) 样本**、原池、pristine 行级 coverage、held-out per_file PASS/FAIL 矩阵、v4 ContractChangeClassifier、v4.1 AssertionSensitivity + EarlyStop。
**单元测试**：`v4/tests/test_v42.py` → **7 passed**（含 spec 强制的 `test_llm_prompt_has_no_oracle_labels`、`test_llm_selector_cannot_open_evaluation_private`、`test_private_contract_extractor_no_oracle_dependency`）

报告严格区分 **Observed** / **Interpretation** / **Speculation**。

---

## 0. LLM 真实可用性（本环境）

**Observed**：检测到 `ANTHROPIC_BASE_URL=http://127.0.0.1:9618` + `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_MODEL=GLM-5.2`，Anthropic Messages API 可达。**v4.2 Hybrid LLM track 真实执行**（非 NOT EVALUATED）。temperature=0，prompt_version=`v42-semantic-001`。共 **241 次 LLM 调用**（去重后 31 个 case）。

**反泄漏验证**：单测强制 (a) prompt 不含 held-out PASS/FAIL 标签或 per_file 引用；(b) `llm_semantic`/`hybrid_selector` 代码不 reference `per_file.json`/`evaluation_private_oracle`；(c) `private_contract` 提取只读 REPOS_DIR pristine 源码 + 变更前 coverage，不读 oracle。最终 detection 仍由 held-out 真实 pytest 矩阵独立评判。

---

## RQ1：Private Contract Extraction 单独贡献

**Observed**：cerberus 提取出 **61 个 PRIVATE_BEHAVIOR_CONTRACT**（都有 CHANGED_SYMBOL/DYNAMICALLY_COVERED/PUBLIC_CALL_PATH 信号，`private_contracts.csv`）。但：

| Strategy | Detection | Cost | cerberus |
|---|---|---|---|
| v4.1 (AssertionTop1+EarlyStop) | 0.821 | 0.226 | 0.60 |
| **PrivateContract** | **0.821** | **0.226** | **0.60** |

**PrivateContract 与 v4.1 完全相同——PrivateContractRescueRate = 0**。

**Interpretation（重要纠偏）**：v4.1 报告曾把 cerberus 失败归因"私有符号未进 ContractGraph"。**v4.2 证伪了这个假设**：cerberus 的 10 个变更符号**全部是 public**（`BareValidator.clear_caches` 等），私有契约提取对它们**无新增信号**。真正瓶颈不是"私有符号漏建模"，而是 **candidate filtering**——`candidates_for_gap` 只选"覆盖 required 且被 existing 未覆盖的契约"的测试，漏掉了"覆盖 required 但 existing 也覆盖"的测试（如 `test_legacy`，它覆盖 7 个 required 契约，其中若干被 existing 的 test_utils/test_errors 也覆盖，故 legacy 不在 missing 集合 → 没进候选）。

> **诚实结论**：Private Contract Extraction 在本数据集**零贡献**。RQ1 = 否定。

## RQ2：LLM Semantic Augmentation 单独贡献

**Observed**：

| Strategy | Detection | Cost | LLM calls |
|---|---|---|---|
| LLMOnly（纯 LLM 排序） | **0.536** | 0.188 | 31 |
| Hybrid（det + LLM, top1） | 0.821 | 0.223 | 31 |
| HybridTop2（top2） | **0.839** | 0.295 | 31 |

- **LLMOnly = 0.536 < v4.1 的 0.821**：纯 LLM 语义排序**显著差于** deterministic。LLM 无 held-out 标签，其语义判断不如 coverage+assertion 可靠。
- **Hybrid(top1) = 0.821 = v4.1**：LLM 融合未改变 top-1 选择（因 det 候选置信度够时 top1 不变）。
- **HybridTop2 = 0.839**：仅比 v4.1 **+0.018**，靠 top-2 多选一个测试，未过 0.85 门槛。
- **SemanticRescueRate = 0/10 = 0**：**LLM 没修复任何 v4.1 漏检**。

**失败归因**（`v42_failure_analysis.json`）：10 个 Hybrid 仍漏检**全部归类 C = Ranking Failure**——相关 FAIL 测试**已在 LLM 候选池里**，但没被排进执行 top-K。例：cec02 `clear_caches`，FAIL 在 `test_legacy.py`，legacy 在 LLM 池里，但 LLM 给它的 semantic_relevance 低（它选了 test_assorted 0.9），legacy 没进 top-1/top-2。

**Prompt Ablation**（`v42_llm_ablation.csv`，8 个 v4.1-漏检样本 × prompt A/B/C）：**detection = 0/8 对所有三种 prompt**——无论给 LLM 仅名字、加 source snippet、还是加 assertion+caller 上下文，它都**救不回**这 8 个。

**Interpretation**：LLM 无法仅凭 contract+diff+测试源码+覆盖摘要判断"哪个测试对旈权威旧 API 行为隐式依赖"。cerberus 的 legacy 测试依赖的是历史兼容性而非显式调用——这种关系**超出静态 AST + LLM 语义推理的能力**，且我们**禁止 LLM 看 FAIL 标签**（合规），故无法直接命中。

> **诚实结论**：LLM Semantic Augmentation 在本数据集**净贡献接近零**（SRR=0，HybridTop2 仅 +0.018 来自 top-2 冗余而非 LLM）。RQ2 = 否定。

## 核心指标表（spec §24）

| Strategy | Detection | VRR | #Tests | RelCost | FalseExp | LLM calls | InvocRate | SRR |
|---|---|---|---|---|---|---|---|---|
| Local | 0.286 | 0.286 | 1.00 | 0.112 | — | 0 | 0 | — |
| IntegrationAll | 1.000 | 1.000 | 8.07 | 1.000 | — | 0 | 0 | — |
| v4.1 (AssertionTop1+EarlyStop) | 0.821 | 0.821 | 1.71 | 0.226 | 0.0 | 0 | 0 | — |
| PrivateContract | 0.821 | 0.821 | 1.71 | 0.226 | 0.0 | 0 | 0 | 0.0 |
| LLMOnly | 0.536 | 0.536 | 1.71 | 0.188 | 0.0 | 31 | 0.55 | — |
| Hybrid | 0.821 | 0.821 | 1.71 | 0.223 | 0.0 | 31 | 0.55 | 0.0 |
| **HybridTop2** | **0.839** | **0.839** | 2.29 | 0.295 | 0.0 | 31 | 0.55 | 0.0 |

**LLM InvocationRate = 0.55**（31/56）——在 spec 理想的 20-40% 之上，但 < 80% 阈值，LLM 仍是 fallback 而非默认 selector（spec §22 通过）。

## cerberus 专项（spec §19）

| Strategy | cerberus detection |
|---|---|
| v4.1 | 0.60 |
| PrivateContract | 0.60 |
| LLMOnly | **0.40**（更差） |
| Hybrid | 0.60 |
| HybridTop2 | 0.60 |
| IntegrationAll | 1.00 |

**Interpretation**：cerberus 在所有 deterministic + Hybrid 下**恒定 0.60**，LLMOnly 甚至退化到 0.40。这**直接回答 spec §19 的核心问题**：cerberus 低 detection **不是 extractor 问题（私有契约零帮助）、不是 semantic ranking 问题（LLM 也救不回），而是 verifier pool / semantic detectability 自身能力问题**——`test_legacy` 的 FAIL 依赖历史兼容契约，无静态/语义信号可提名，只有 Integration 全跑才暴露。

## 失败归因（spec §20）

**Observed**：Hybrid 仍漏 10 个，**全部 C = Ranking Failure**（FAIL 测试在候选池但未排进 top-K）。无 A/B/D/E。

**Interpretation**：但要诚实——这里的 "Ranking Failure" 更深层是 **verifier semantic detectability ceiling**：legacy 测试虽在候选池，但其对 break 的敏感性来自历史 API 兼容性，LLM/deterministic 都无法从前置信息推断。把它归 C 是按 spec 定义（在池未选中），但本质逼近 D/E 边界。

## Remaining failures 是否受 verifier pool ceiling 限制？

**Observed + Interpretation**：**是**。cerberus 的 10 个漏检中，IntegrationAll 能检出（legacy 在全池里），但 semantic selection 无法提名 legacy。这不是"pool 中没有能检测的 test"（pool 有），而是"无法用 permissible 信号（contract/diff/coverage/assertion/LLM-语义，**不含 FAIL 标签**）识别 legacy 是相关测试"。**这是 verifier 与 selector 之间的语义可探测性 gap**，是本方法的天花板。

## 成本（spec §16 Token/Latency）

**Observed**：241 次 LLM 调用。Prompt ablation token：A=7354、B=13278、C=9502（8 样本）。Hybrid 单次约 1-4s latency。RelCost：Hybrid 0.223、HybridTop2 0.295——**均 ≤ 0.50 门槛** ✓。

## 各版本演进与本阶段定位

- v4：obligation 选择 → detection 0.66–0.82 @ 23–36% cost
- v4.1：assertion ranking 信号有效但被 greedy 消解；EarlyStop 干净修复 False Expansion → 0.821 @ 27.6%
- **v4.2**：Private Contract **零贡献**；LLM Semantic **SRR=0**（救不回历史兼容型失败）；HybridTop2 仅 +0.018 → **0.839 @ 29.5%**

---

# 最终结论：**UNCERTAIN**（且建议停止继续增强，进入论文定型）

### 按 spec §25/26/27 核对
- ✅ Cost 0.223–0.295 ≤ 0.50 门槛
- ✅ LLM InvocationRate 0.55 < 0.80（fallback 设计未失败）
- ✅ 4/5 repo Hybrid ≥ v4.1（tinydb/boltons/pyparsing 1.0、toolz 0.857 不变；cerberus 0.6 不变）
- △ Detection **0.839 < 0.85**（HybridTop2 最优，差 0.011）
- ✗ PrivateContractRescueRate = **0**
- ✗ SemanticRescueRate = **0**
- ✗ cerberus **无改善**（恒定 0.60）

### 为何 UNCERTAIN 而非 YES
- **RQ1 否定**：Private contract extraction 单独零贡献。v4.1 的"私有符号盲区"归因被证伪——cerberus 变更符号是 public 的，真正瓶颈是 candidate filtering 与 verifier semantic detectability。
- **RQ2 否定**：LLM Semantic Augmentation 净贡献接近零。SRR=0，prompt A/B/C 全 0 检出。HybridTop2 的 +0.018 来自 top-2 冗余而非 LLM 语义价值（LLMOnly 反而更差 0.536）。
- **Detection 0.839 仍 < 0.85**，且全部剩余失败是"无法用合规信号提名 legacy 测试"——**verifier pool / semantic detectability 是主 ceiling**。

### 为何不是 NO
- Cost 侧持续满足（≤0.30 << 0.50）；
- v4.1 EarlyStop 已是干净真实收益（FER 1.0→0.0）；
- 4/5 仓库 detection = 1.0/0.857，机制在多数仓库有效；
- 仅 cerberus 一类历史兼容型 verifier 撞天花板。

### spec §27 明确指向
> 如果 PrivateContract 无明显改善、LLM rescue 接近 0、Detection 仍 ≈0.82 → **UNCERTAIN** 并停止做 v4.3/v5。

**本实验数据完全命中此条**。停止继续增强。

## LLM 是核心必要组件还是只处理长尾？（§问题9）

**Observed + Interpretation**：**既不是核心，也未有效处理长尾**。LLMOnly(0.536) 远劣于 deterministic(0.821)；Hybrid = v4.1（LLM 无增量）；SRR=0。在本数据集，LLM semantic augmentation **未证明任何增量价值**。这不排除 LLM 在更大/不同数据集有用，但**当前 deterministic(coverage+assertion+early-stop) 已是足够强的方法**，LLM 非必需。

## 当前课题是否可正式定型？（§问题10）

**可以定型，但定为"受 verifier pool capability 约束的 contract-level obligation selection 方法"**。

**核心可发表机制**（v4→v4.1 已证明）：
1. 行级 coverage（排除 def 行）发现真实验证缺口（修复 v3 循环）；
2. Obligation 选择从既有池选最小必要测试，detection 0.82 @ cost 0.28（Integration 的 28%）；
3. EarlyStop 把 False Expansion 从 1.0 降到 0.0，detection 不降——干净的成本优化。

**诚实的 ceiling**（v4.2 证明）：
1. Private contract extraction 对 public-symbol 变更零贡献；
2. LLM semantic augmentation（不含 FAIL 标签）救不回"历史兼容型 verifier"——这类失败的检测**超出 coverage/assertion/LLM-语义的能力边界**，只能靠 Integration 全跑；
3. 最终 detection 天花板受 **verifier pool 的 semantic detectability** 主导，而非 selector 算法。

论文应如实写：**"现有 verifier pool capability 与 semantic detectability 是主导 ceiling；obligation selection 能以 ~28% 成本达到 ~82-84% 的检测率，剩余 ~16% 是无法用合规信号提名的兼容性依赖型失败。"**

---

## 附录：科研诚信声明
- 同一 56 样本（单测强制）；v4/v4.1 原结果未覆盖。
- Private contract / LLM prompt / hybrid selector **不读** held-out per_file 矩阵（3 个反泄漏单测强制）。
- LLM prompt 不含任何测试 PASS/FAIL 标签、不含 per_file/evaluation_private 引用。
- LLM 权重 w=0.2/0.3/0.4 + alpha/beta 固定，**未根据 held-out detection 调权**；权重敏感性全相同（0.821），如实报告。
- prompt A/B/C 三种变体 detection=0，**未隐藏**；LLM 241 次调用 + token/latency 全记录。
- 失败归因 10 个全保留并分析，未删除；cerberus 未被剔除。
- 诚实纠偏：v4.1"私有符号盲区"归因被 v4.2 证伪，已明确标注。
- LLM 真实可用（GLM-5.2），非 NOT EVALUATED；但结论仍是 SRR=0。
- 区分 Observed / Interpretation / Speculation（已标注）。
- **停止做 v4.3/v5**（spec §27 命中）。