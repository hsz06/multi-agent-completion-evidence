# Verification Obligation PoC — 实验报告 (v4)

**研究问题**：当上游 Contract Change 使下游 CompletionClaim 失效后，仅重跑原有 verify-set 可能仍无法发现错误。能否分析验证覆盖缺口，从已有 verifier/test pool 中选择最小必要 Verification Obligations，使检测能力接近完整 Integration Testing，同时显著降低成本？

**入口**：`python3 v4/run_verification_obligation_poc.py [--selector deterministic|llm]`
**结果**：`v4/results/{obligation_selection,coverage_gap,detection_cost,threshold_sensitivity,coverage_sensitivity,failure_analysis,summary}.*`
**复用**：v3 的 5 个真实仓库、ContractNode、ContractChangeClassifier、mutation engine。**未新增仓库、未生成新测试**——obligation 只从已有 pytest 测试池选择。

报告严格区分 **Observed** / **Interpretation** / **Speculation**。

---

## 0. 反泄漏与独立性（诚信前置）

- **选择层**用变更**前** pristine 树的**行级 coverage** 建 obligation→contract 映射（`agent_visible_verification_pool/`）。一个测试"覆盖"某契约 ⟺ 它执行了该符号**函数体内**的行（排除 `def` 行——def 行在 import 时执行，会虚假覆盖）。
- **评价层**用 held-out "逐测试文件在变更树上的 PASS/FAIL 矩阵"（`evaluation_private_oracle/per_file.json`），**只用于打分，selector 永不读取**。
- 契约依赖来自 **AST + 变更前 coverage**，最终 detection 来自 **held-out 真实 pytest**。二者独立，**修复了 v3 的 oracle→建边→oracle 循环**。

> **此外发现并修复了一个 v3 遗留数据完整性 bug**：v3 `case_id` 用 `repo[0].upper()` 作前缀，导致 **tinydb 与 toolz（都以 T 开头）case_id 碰撞、toolz 覆盖 tinydb**。v4 改用唯一前缀（td/ce/bo/tz/pp）重跑。此 bug 曾使 v3 部分 per-repo tinydb/toolz 数据混淆——已在 v4 修正，v3 报告中相关 per-repo 数字应以本修正后数据为准。

## 1. 数据集

5 仓库 × 真实 mutation，per-file 校准：**49 个 applied case，28 个 true-break（存在某 pool 测试 FAIL）**。按 (case, 下游 claim=dev_b/testing) 展开 = **56 个 (case,claim) 评估样本，全部 gap-positive**。满足 spec ≥20 coverage-gap case。

| repo | applied | true-break | pool files |
|---|---|---|---|
| tinydb | 9 | 5 | 7 |
| cerberus | 10 | 10 | 9 |
| boltons | 10 | 3 | 10 |
| toolz | 10 | 7 | 8 |
| pyparsing | 10 | 3 | 5 |

---

## 2. 核心结果表（H1/H2/H3）

**Observed**（默认 coverage threshold = 0.8，56 样本，Integration 归一化 cost=1.0）：

| Strategy | Detection | VRR | Avg #Tests | RelCost |
|---|---|---|---|---|
| **Local**（仅重跑原 verify-set） | 0.286 | 0.286 | 1.0 | 0.112 |
| **DependencyOnly**（v3 invalidation + 重跑原 set） | 0.286 | 0.286 | 1.0 | 0.112 |
| **ObligationAware**（gap→选最小 obligation） | **0.661** | **0.661** | 2.05 | **0.234** |
| **IntegrationAll**（全测试池） | 1.000 | 1.000 | 8.07 | 1.000 |

- **H1 成立**：Local/DependencyOnly detection 仅 0.286——重跑原 verify-set 在 71% break case 上漏检。DependencyOnly 与 Local 完全相同（重跑相同文件），证实"v3 仅触发重验、不扩验证"无额外检测收益。
- **H2 成立**：IntegrationAll detection=1.000（此处为定义上限：数据集 = integration-可检测的 break），cost=1.0。
- **H3 部分成立**：ObligationAware 达到 **66% 的 Integration 检测能力，仅用 23% 成本**。是 Local 的 2.3× detection @ 2.1× cost，且处于 Pareto 前沿（Local/0.286/0.112、OA/0.661/0.234、Int/1.0/1.0 三点互不支配）。

**注**：IntegrationAll=1.000 是**构造性的**——样本定义为"某 pool 测试 FAIL"即 Integration 可检测。因此 1.0 是 ceiling，不是独立测量。ObligationAware 的 0.661 是**真实**的：被选测试在变更树上真 FAIL 的比例。

## 3. Coverage Gap 在多少真实 case 中存在？（§问题2）

**Observed**：56/56 = **100%** 的 (true-break, 下游 claim) 样本存在 coverage gap（existing verify-set 未覆盖被改文件的全部 public 契约）。`coverage_gap.csv` 记录每条 required/currently_covered/missing。例如 `tdc03/dev_b`: required 20, covered 20, missing 15 —— dev_b 的 `test_utils` 只调用过 table.py 5 个方法，其余 15 个契约（含被改的 Table.insert）未被其 verify-set 覆盖。

**Interpretation**：gap 几乎总存在，因为下游 claim 的 verify-set 通常只覆盖 producer 文件的部分契约。这正是 obligation selection 有空间的前提。

## 4. 阈值敏感性（§12）

**Observed**：

| threshold | Detection | #Tests | RelCost |
|---|---|---|---|
| 0.6 | 0.643 | 1.70 | 0.201 |
| **0.8（默认）** | 0.661 | 2.05 | 0.234 |
| 1.0 | **0.821** | 2.77 | **0.363** |

**Interpretation**：**threshold=1.0（覆盖全部 required 契约）时 detection 升到 0.821 @ cost 0.363** —— 即 82% 的 Integration 检测能力 @ 36% 成本，逼近 spec 的 YES 门槛（≥85-90% @ ≤50-60%）。默认 0.8 偏保守（66% @ 23%）。这说明 **选择覆盖率越高，检测越接近 Integration，但成本仍远低于 Integration**——Pareto 权衡明确。

## 5. Cross-repo 泛化（§答案4/5/7）

**Observed**（per-repo OA/Integration/Local detection rate）：

| repo | OA | Integration | Local |
|---|---|---|---|
| boltons | 1.000 | 1.000 | 0.000 |
| pyparsing | 1.000 | 1.000 | 0.333 |
| toolz | 0.857 | 1.000 | 0.286 |
| tinydb | 0.500 | 1.000 | 0.300 |
| cerberus | 0.400 | 1.000 | 0.350 |

**Interpretation**：**3/5 仓库 OA detection ≥0.857**（boltons/pyparsing 1.0、toolz 0.857）——满足"多仓库有效，非单一 tinydb"。tinydb(0.5)与 cerberus(0.4)偏弱，主因是 Type B（见 §6）。

## 6. 失败案例分析（§20）

**Observed**（`failure_analysis.json`）：A=37（成功），B=19，**未出现 C/D/E**（C/E 因样本定义为 integration-可检测而结构性不出现；D 未出现）。

- **类型 A（37, 66%）**：gap 正确发现 → 正确 obligation 被选 → 检测成功。例：boltons/pyparsing 多数 case。
- **类型 B（19, 34%）**：gap 正确，但**选错/选不全测试** → 漏检（Integration 能检测）。例：`tdc03/dev_b`，OA 选了 `test_tables.py`（它覆盖 Table.insert），但该测试对该 break（insert 返回类型变 dict）不敏感未 FAIL，而 Integration 通过另一测试检出。
- **未观察**：C（无 gap 却漏检）、D（无需扩大却扩大——见 §7 实为 100%）、E（Integration 也检测不出）—— **NOT OBSERVED**，未构造补齐。

**Interpretation**：**唯一失败类型是 B——"覆盖≠检测"**。selector 按行级 coverage 选测试，但覆盖被改代码 ≠ 对该 break 断言敏感。这是 obligation selection 的根本天花板：**coverage 是检测的必要不充分条件**。要突破需断言敏感性信号（如断言涉及被改符号的返回/参数）——超出本 PoC 边界（不生成/不改测试）。

## 7. False Expansion Rate（§15.6）

**Observed**：**FalseExpansionRate = 1.0**。在 Local 已能检测的 16 个 case 中，OA **100% 仍追加了测试**。

**Interpretation**：OA selector 是 coverage 驱动的，**无法预知 existing 是否已检测**（那需 held-out oracle，会泄漏），故总是填满 gap。结果：即便不需要也扩。好在追加成本小（仍仅 23% Integration），且 detection 提升。但这是真实的成本浪费——一个"先跑现有、若 FAIL 则停"的提前终止策略可消除这部分浪费（未实现，列为未来工作）。

## 8. Coverage Sensitivity（§19）

**Observed**（existing verify-set 宽度 LOCAL/MODULE/INTEGRATION）：

| regime | Local det | OA det | Int det |
|---|---|---|---|
| LOCAL | 0.286 | 0.661 | 1.000 |
| MODULE | 0.411 | 0.661 | 1.000 |
| INTEGRATION | 0.571 | 0.732 | 1.000 |

**Interpretation**：随 existing 变宽，Local 检测上升、OA 边际收益收窄但**每个 regime 下 OA 仍优于 Local**（INTEGRATION regime 下 OA 0.732 vs Local 0.571）。说明 **obligation-aware 在不跑完整 Integration 的前提下补齐了关键缺口**，即使 existing 已较宽。

## 9. 哪些 failure 即使扩大 verification 也无法发现？（§答案8）

**Observed**：在本数据集中**未出现**（E=0），因样本定义为 integration-可检测。Speculation：若纳入 integration 本身也检测不出的 break（如 cerberus 部分仅在更广 suite 才暴露的破坏），OA 同样无力——这属"verifier insufficiency"边界，非 obligation selection 范畴。

## 10. 最大失败原因（§答案9）

1. **"覆盖≠检测"（Type B, 34%）**：行级 coverage 能选出覆盖被改符号的测试，但选不出对该 break **断言敏感**的测试。这是主因，也是方法天花板。
2. **False Expansion 100%**：selector 无法预判 existing 是否已够，总扩。
3. **tinydb/cerberus 偏弱**：其 pool 中覆盖被改契约的测试恰好断言不敏感 → OA 选了仍漏（Type B 集中于此）。

## 11. Contract-level 是否仍有独立收益？（§答案7）

**Observed**：行级（符号级）coverage 是本 PoC 发现真实 gap 的关键——文件级 coverage 因 import 链"虚假覆盖"全部契约，gap 恒为空（开发期实测）。只有排除 `def` 行的**函数体行级** coverage 才能区分"调用过"与"仅 import"。这修复了 v3 的循环与 v2 的"边过宽"，是 contract-level 的真实独立收益。

## 12. LLM 接口（§21/22）

**Observed**：`--selector llm` 打印 `LLM_SELECTOR_NOT_AVAILABLE`（无 API 配置）并回退 deterministic。`SemanticObligationSelector` 接口预留。**LLM semantic selection not evaluated**——deterministic 实验标准未因此降低。

---

# 13. 最终研究结论

## **UNCERTAIN**（较 v3 上行，但未达 YES 门槛）

### 按 spec §26/27 核对
- ✅ **Obligation-aware Cost 为 Integration 的 23%（默认）/ 36%（threshold=1.0）** ≤ 50-60% 门槛。
- ✅ **多仓库有效**：3/5 仓库 OA detection ≥0.857。
- ✅ **Pareto 前沿**：OA 是 Local 与 Integration 之间的有效折中点；threshold=1.0 达 82% detection @ 36% cost。
- ✅ **修复 v3 循环 + 行级 coverage 独立性**：dependency 与 evaluation 来源分离。
- △ **Detection 未达 85-90% 门槛**：默认 0.8 下仅 66%；threshold=1.0 下 82%（接近但未过 85%）。
- △ **False Expansion 100%**：总是扩，成本浪费有限但真实。
- ✗ **唯一失败类型 B（34%）是"覆盖≠检测"**，属 obligation selection 的根本天花板，不扩 test pool 类边界内难突破。

### 为何从 v3 的 UNCERTAIN 仍是 UNCERTAIN（而非 YES/NO）
- **上行证据**：v3 detection≈0（保守门假预防）；v4 用真实 obligation 选择把 detection 拉到 0.66-0.82 @ 23-36% 成本——**机制被证明可检测**，不是假象。threshold=1.0 逼近 YES 画面（82%@36%）。这是相对 v3 的实质进步。
- **未达 YES**：默认阈值下 66% < 85%；Type B（覆盖≠检测）34% 是硬天花板；2/5 仓库（tinydb/cerberus）偏弱。
- **未跌 NO**：Integration 成本并非"很低"（8×测试、1.0 归一化成本），OA 节省真实（77% 成本）；selector 并未"频繁漏掉"——66% 命中。

### 下一步若推进（上行至 YES 的路径）
1. **断言敏感性信号**：在 obligation 排序中引入"测试断言是否涉及被改符号的返回/参数"（AST 解析 assert 语句），直击 Type B。
2. **提前终止**：先跑 existing，FAIL 即停 → 消除 False Expansion。
3. **threshold=1.0 作为默认**：若可接受 36% 成本，detection 0.82 已接近门槛。
4. 在更多仓库 + 真实 LLM agent 轨迹上验证 Type B 比例是否可压低。

若 (1) 将 Type B 从 34% 压到 <15% 且多仓库 detection 达 85%+，方向可上行至 **YES**。否则该方向的天花板由"**coverage 是检测的必要不充分条件**"主导，应承认 obligation selection 只能在该天花板下工作。

---

## 附录：科研诚信声明
- 未调整 verify-set/threshold 迎合 Proposed（threshold 0.8 为 spec 默认，1.0 单独报告）。
- 未删除 selector 失败 case（Type B 19 个全部保留并分析）。
- held-out oracle（per_file PASS/FAIL）**不参与** obligation ranking；coverage 仅来自 pristine 变更前树。
- 所有 test runtime 真实测量（`pool_baseline.json`）。
- 4 策略跑同一组 56 case。
- **不把 conservative NOT_READY 当作 detection**——detection = 被选测试真 FAIL（held-out pytest）。
- 区分 Triggered（invalidate）/ Detected（测试 FAIL）/ Resolved（VRR=正确终态）。
- LLM 未使用 → 明确 `LLM semantic selection not evaluated`；deterministic 实验标准未降低。
- 主动披露并修复 v3 case_id 碰撞 bug（tinydb/toolz），未掩盖。