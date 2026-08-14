# V3 Research Report — RealAgent / Contract-Level Study

**研究主问题**：当长程多智能体协作中的验证依赖结构不完整或粒度过粗时，能否发现缺失的契约级依赖，并精准触发必要的重新验证，从而减少 stale completion 导致的 Global False Completion？

**入口**：`python3 v3/run_v3_experiments.py --mode deterministic`（≈2 min，校准已缓存）；`--mode agent` 跑 real-LLM pilot。
**结果**：`v3/results/{natural_gfc,invalidation,recovery,coverage,contract_vs_file,ablation,cost}.csv` + `summary.json` + `real_agent_pilot.json`
**校准**：5 仓库 × 50 个真实 change case × 3 verify-set 制度，全部用真实 pytest 校准（一次性 ≈487s，磁盘缓存）。

本报告严格区分 **Observed Result**、**Interpretation**、**Speculation**。

---

## 0. 两轨严格分轨（诚信前置）

| 轨 | trajectory_source | 含义 | n |
|---|---|---|---|
| 确定性 simulated-agent | `simulated_agent` | 改代码+跑测试是真实 pytest；仅"选哪段代码改"是确定性的 | 50 cases × 3 regimes |
| real-LLM agent pilot | `real_llm_agent` | 真实 Claude coding-subagent 真改代码+真跑测试+自报 completion | 3 |

**绝不**把 simulated-agent 的 GFCR 当作"真实 LLM agent 中自然出现 GFC"的证据。real-LLM pilot 仅为可行性证据（n=3），不作频率主张。

## 1. Repository 信息

| repo | source | commit | 语言 | 真实/合成 |
|---|---|---|---|---|
| tinydb | msiemens/tinydb | 4aa5311 | Python | 真实开源（v2 continuity） |
| cerberus | pyeve/cerberus | 65e977d | Python | 真实开源（v2 continuity） |
| boltons | mahmoud/boltons | 580a9c2 | Python | 真实开源（v2 continuity） |
| toolz | pytoolz/toolz | 568c2b8 | Python | 真实开源（v3 新增） |
| pyparsing | pyparsing/pyparsing | 038ff39 | Python | 真实开源（v3 新增） |

5 仓库全部为真实开源（无合成）。新仓库选择标准为**契约依赖丰富度 + 零运行时依赖 + Python 3.9 兼容**，非代码量。测试均可稳定运行（pyparsing 全套 26s，其余 <3.3s）；v3 用聚焦测试子集作为 verify-set 以控制运行时。

**Contract 提取**：AST 提取 5 仓库共 **3235 个 ContractNode**（FUNCTION_SIGNATURE / RETURN_CONTRACT / PUBLIC_SYMBOL / TYPE_CONTRACT / CONFIG_KEY）。

## 2. Dependency Space 是否扩大（Goal A）

**Observed**：
- **288 个 DependencyInstance**（INTEGRATION 144 + LOCAL 144）≥ 100 ✓
- **75 个 critical（GT-invalidate）边** ≥ 50 ✓
- G* 边数：INTEGRATION 67、LOCAL 33
- 删除比例实验的 ≥50 边门槛：**INTEGRATION（67 边）满足**；LOCAL（33 边）低于门槛 → 在 `recovery.csv` 中标 `insufficient_graph=1`

**Interpretation**：v3 把 v2 的"A→C 边过少导致 ratio 维度坍缩"问题部分解决了——INTEGRATION 有 67 边，ratio 实验合法；但 LOCAL（GFC 真正发生处）仍只有 33 边，是一个真实的结构性限制。

## 3. Natural GFC 是否存在（Goal C 核心）

### 3a. Simulated-agent track（`natural_gfc.csv`）

**Observed** — 按制度分组的 GFCR：

| 制度 | tinydb | cerberus | boltons | toolz | pyparsing |
|---|---|---|---|---|---|
| LOCAL | 0.22 | 0.60 | 0.30 | 0.22 | 0.00 |
| MODULE | 0.00 | 0.60 | 0.00 | 0.00 | 0.00 |
| INTEGRATION | 0.00 | 0.40 | 0.00 | 0.00 | 0.00 |

stale_claim_rate（verify-set 在变更下 FAIL 的比例）随制度变宽**上升**（tinydb 0.19→0.44），而 GFCR **下降**——即"越宽的验证能检测越多破坏，但留下的隐蔽 GFC 越少"。cerberus 顽固（LOCAL/MODULE 都 0.60）说明其破坏即使在 MODULE 宽度下也逃逸 → 对 cerberus 而言是**验证不足**而非依赖缺失。

### 3b. Real-LLM pilot track（`real_agent_pilot.json`）

**Observed**：3 条真实 Claude coding-subagent 轨迹：
- tinydb: agent 新增 `Table.count_where`，142 测试通过，自报完成；随后对共享契约 `Table.search` 做确定性 follow-up 破坏 → agent 自身的 verify-set **FAIL（42 failed）** → completion 变 stale。
- toolz: 新增 `countby`（reuse groupby），50 通过；follow-up 破坏 `groupby` → **FAIL（8 failed）** → stale。
- boltons: 新增 `is_mapping`，50 通过；follow-up 破坏 `is_iterable` → **FAIL（4 failed）** → stale。

Real-LLM stale rate = 3/3，**但这是 pilot（n=3，follow-up 是确定性破坏），不作频率主张**——仅证明：真实 LLM agent 确实会产出可被后续共享契约变更击穿的 self-reported completion。

**Interpretation**：真实 LLM agent 的 stale completion 现象**机制上成立**（pilot 证据）；在 simulated-agent 上**统计上成立**且仅集中在 LOCAL（窄 verify-set）配置。GFC 不是"必须人为删边才出现"——它在 verify-set 不覆盖被破坏契约时自然出现。

## 4. Contract-Level 是否优于 File-Level（Goal B）

**Observed**（`invalidation.csv`，LOCAL 制度）：

| 策略 | 粒度 | Precision | Recall | F1 | FIR | Reverify | baseline |
|---|---|---|---|---|---|---|---|
| all_downstream | file | 0.16 | 1.00 | 0.27 | 1.00 | 96 | 96 × 1.0 FIR |
| static_file | file | 0.20 | 1.00 | 0.33 | 0.75 | 76 |  |
| change_aware_file | file | 0.45 | 0.67 | 0.54 | 0.15 | 22 |  |
| **static_contract** | contract | 1.00 | 1.00 | 1.00 | 0.00 | 15 |  |
| **change_aware_contract** | contract | 1.00 | 1.00 | 1.00 | 0.00 | 15 |  |

`contract_vs_file.csv`（候选恢复）：contract recall@3 = **0.333** vs file **0.267**；两者 cf_fix/accept 均因 conservative-gate 而相同。

**Interpretation（需谨慎）**：
- 契约级 invalidation 的 P=R=1.0、FIR=0、Reverify 仅 15 —— **部分是循环**：G* 的 CONTRACT→COMPLETION 边是从校准 oracle 派生的（哪条 verify-set FAIL 才建边），再用同一 oracle 判 invalidation，所以契约级天然命中。这不是独立证据。
- **但真正可比的是 Reverify Count**：契约 15 vs 文件 22 vs 全量 96——契约级把不必要的重验证从 96 降到 15（−84%），这是 v2 T2 "边正确但过宽" 问题的真实改善。
- 静态 vs change_aware 在契约级相同，是因为 registry 给每个 symbol 只配一种 kind，没有"同一 symbol 跨 scope"场景来锻炼 scope 过滤——一个真实限制。
- 恢复候选粒度：contract recall@3 略优于 file（0.333 vs 0.267），改善**有限**。

## 5. Missing Dependency Recovery 是否规模化成立（`recovery.csv`）

**Observed**：

| 制度 | ratio | n_edges | nBC | Recall@1 | Recall@3 | MRR | GFC预防率(保守门) | 检测率 | 接受率 | FIR |
|---|---|---|---|---|---|---|---|---|---|---|
| LOCAL | 5% | 33(insuf) | 65 | 0.631 | 0.631 | 0.000 | 1.00 | 0.000 | 1.00 | 0.028 |
| LOCAL | 30% | 33(insuf) | 71 | 0.296 | 0.310 | 0.033 | 1.00 | 0.056 | 1.00 | 0.028 |
| INTEG | 5% | 67 | 20 | 0.800 | 0.800 | 0.000 | 1.00 | 0.000 | 1.00 | 0.025 |
| INTEG | 30% | 67 | 20 | 0.000 | 0.000 | 0.000 | 1.00 | 0.000 | 1.00 | 0.025 |

**Interpretation**（这是本阶段最关键的诚实发现）：
- **GFC 预防率(保守门) = 1.0** 是**平凡的**：任何候选只要 invalidate 任意下游 completion，保守门就从 VERIFIED 转 NOT_READY，从而"阻止"了假完成。这不是候选质量的证明。
- **检测率 ≈ 0** 才是真相：GFC 按定义就是"所有 operative verify-set 都 PASS"，所以 invalidate→重跑同一 verify-set 仍 PASS → 验证器**无法确认** staleness。这正是规范的 §32 边界：**dependency recovery 解决"WHO/WHAT 要重验"，verifier 解决"能否检测到"**——在 GFC 场景，恢复能"触发"重验却几乎无法"检测"出错误。
- **Recall@1**：LOCAL 0.63→0.30（随删除增多下降，合理），INTEGRATION 0.8→0.0（INTEGRATION 的 badcase 集合小且 GT 边分布特殊）。
- **MRR ≈ 0**：候选排序很少把真正的缺失边排到第 1——ranking 质量弱（多半因 trace/dynamic 对两个下游 slot 都提名，tie 严重）。
- **接受率 = 1.0**：FIR ≈ 0.025–0.10 远低于 0.20 阈值 → 全部接受；但这是因为 registry 的 symbol 互不重复，回归门未被充分锻炼。

## 6. Strong Testing 是否替代本方法（`coverage.csv`）

**Observed**：完整 G* 下 GFCR：LOCAL 出现（cerberus 0.60、boltons 0.30、tinydb/toolz 0.22），INTEGRATION 大幅下降（cerberus 0.40，其余 0.00）。恢复 fix rate（保守门定义）在 LOCAL/MODULE 全 1.0，INTEGRATION 多为 0（因 INTEGRATION 完整图下多数 repo 已无 GFC → 无 badcase 可恢复）。

**Interpretation**：**强 testing 显著削弱了问题的发生**——INTEGRATION 下 4/5 仓库 GFCR=0。但 cerberus 在 INTEGRATION 仍 0.40：强 testing 没有完全解决，对 cerberus 是验证不足。所以"强 testing 替代本方法"是**部分成立**：在 testing 足够强的 repo，依赖恢复的边际价值确实趋近 0；在 testing 不足的 repo（cerberus），恢复仍有 conservent-gate 预防价值（但检测价值仍因验证不足而为 0）。

## 7. Verifier Coverage Boundary（§6）

**Observed**：把"恢复能阻止 GFC"拆成两件事后：
- **dependency incompleteness**：恢复可在 LOCAL 把 GFC 预防率（保守门）拉到 1.0；
- **verification insufficiency**：检测率 ≈ 0，即"该重验谁"被答对了，但"重验能否发现错"由验证器决定，而 GFC 场景下验证器答不出。

**Interpretation**：本阶段最稳健的结论是——**在 GFC 场景，瓶颈主要是 verification insufficiency 而非 dependency incompleteness**。依赖恢复的价值天花板被验证器覆盖率限定。"保守门"能以低 FIR（0.025）阻止假完成，但代价是系统进入"暂停重验"而非"发现 bug"——这对 CI 流水线是有用的（避免错误绿灯），但不能取代更强的验证。

## 8. Cross-repo 泛化（不能只给聚合数）

**Observed**（按 repo，LOCAL GFCR + LOCAL 30% Recall@1 + detection）：
- tinydb: GFCR 0.22, R@1 0.296-equivalent, detection ≈0
- cerberus: GFCR 0.60（最高，验证不足型）, detection ≈0
- boltons: GFCR 0.30, detection ≈0
- toolz: GFCR 0.22, detection ≈0
- pyparsing: GFCR 0.00（LOCAL 也无 GFC —— verify-set 恰好覆盖被破坏契约）

**Interpretation**：GFC 在 4/5 仓库的 LOCAL 出现（pyparsing 例外）；detection 在所有仓库对所有制度 ≈ 0。**泛化的是"机制"（GFC 可被预防但难被检测），不泛化的是"收益幅度"**——它强依赖每个 repo 的 verify-set 是否覆盖被破坏契约。pyparsing 是"verify-set 恰好够"的正例，说明问题对仓库结构敏感。

## 9. Cross-repo / LLM 的真实贡献（§8）

**Observed — Ablation（`ablation.csv`）**：

| variant | Recall@3 | MRR | TieRate |
|---|---|---|---|
| full | 0.333 | 0.067 | 0.6 |
| − static | 0.333 | 0.067 | 0.6 |
| − dynamic | 0.333 | 0.022 | 1.0 |
| − semantic | 0.333 | 0.033 | 1.0 |
| − trace | 0.333 | 0.067 | 0.6 |

**Interpretation**：
- **TieRate 0.6–1.0**：候选置信度并列极常见——v2 的 tie-break 失真问题在 v3 被**显式记录**（tie_rate 列），不再隐藏在字典序里。
- **dynamic 与 semantic 对 MRR 有贡献**（去掉后 MRR 从 0.067 降到 0.022/0.033，且 tie_rate 升到 1.0）；static/trace 在本规模边际不明显。
- **LLM 贡献**：本环境**未配置 LLM API**，semantic 走确定性启发式 fallback，故"LLM vs 无 LLM"无法对比。诚实标注：LLM 的候选生成贡献在本 PoC **未被检验**（NOT FEASIBLE IN CURRENT ENVIRONMENT for the semantic-LLM comparison）。

## 10. Failure Analysis（成功与失败并列）

**成功恢复 ×3**（候选命中 GT 边 + 保守门阻止假完成 + 回归接受）：tinydb Tc03/Bc 系、toolz 部分案例、pyparsing Pc04 —— Recall@1 命中、FIR 0.025、accept=1。但这些"成功"是 conservative-gate 意义上的，检测率为 0。

**恢复失败 ×3**：
- INTEGRATION 30% recall@1=0.0：候选完全没命中 GT 边（GT 边过少 + 候选粒度/排序不匹配）。
- cerberus LOCAL 30% detection≈0：候选命中并触发重验，但 cerberus verify-set 不足以检测破坏 → Triggered=true/Detected=false。
- 回归门虽接受但 MRR≈0：top-1 常是错目标 → 系统重验了错误的 completion（浪费），仍因保守门"预防"了 GFC。

**GFC 根本未发生 ×3**：pyparsing 全制度 GFCR=0；tinydb/boltons INTEGRATION GFCR=0 —— verify-set 覆盖了被破坏契约，问题不出现。**NOT OBSERVED**：独立的"依赖恢复正确但 verifier detection 失败"与"CF 成功但 regression 拒绝"在 TEMPLATE 中部分出现（detection≈0 即前者；regression 拒绝在本规模未出现，因 FIR 恒低 → 标 NOT OBSERVED 而非构造）。

## 11. Cost（`cost.csv`）

- 校准：≈487s 一次性（50 cases × 3 regimes × 真实 pytest），磁盘缓存后实验阶段 ≈2min/全跑。
- 确定性实验 wall time ≈120s。
- real-LLM pilot：3 条轨迹，每条 subagent ≈7–8 min（含真实编码+测试），tokens 由 subagent 计（未外部计费记录）。

## 12. 阈值敏感性

阈值 0.20/0.30 下 patch_acceptance 均为 1.0（FIR 0.025–0.10 恒低于三档阈值）；只有 0.10 阈值下 INTEGRATION 的 FIR=0.10 边缘。本规模下**阈值选择不改变结论**——因为 FIR 普遍很低（symbol 互不重复使回归门未被充分锻炼）。这是一个诚实的弱保护：阈值敏感性需更大、边更密集的图才能体现。

---

# 13. 最终研究结论

## Research Direction: **UNCERTAIN**

### 三层判定（按用户要求分离）

1. **Mechanism validity — 成立（部分）**
   - GFC 在 simulated-agent 与 real-LLM pilot 中均被观测到，机制清晰：verify-set 不覆盖被破坏契约 + 依赖边缺失 → 旧 completion 错误存活。
   - 契约级 invalidation 把不必要的重验证从 96 降到 15（Reverify −84%），改善了 v2 的"边过宽"问题。
   - 依赖恢复能在保守门下以低 FIR（0.025）阻止假完成。

2. **Simulated-agent external validity — 中等**
   - 5 仓库中 4 个在 LOCAL 出现 GFC；但 GFCR 强依赖 verify-set 宽度，INTEGRATION 下多数归零。
   - 恢复的"GFC 预防"是保守门意义上的（平凡 ≈1.0），**检测率 ≈0** 暴露了真正的瓶颈是验证不足。
   - 排序质量（MRR≈0）与 ratio 维度（LOCAL 不足 50 边）仍弱。

3. **Real-LLM external validity — 未证明**
   - 仅 3 条 pilot 轨迹，且 follow-up 破坏是确定性的。**不能**据此声明"真实 LLM agent 中 GFC 自然高频出现"。LLM 候选生成对比也因无 API 而 NOT FEASIBLE。

### 按 spec §30 的判据核对
- ✓ Natural GFC > 0 且非单一 repo（LOCAL 下 4/5 仓库出现）——但**仅在窄 verify-set 制度**。
- △ Contract-level 的 False Invalidation 下降（Reverify 96→15），但 P=R=1 部分循环；恢复候选粒度改善有限（recall@3 0.333 vs 0.267）。
- △ Recovery 的 Recall@K 中等（0.3–0.63）、CF 预防平凡（保守门）、**detection≈0**、接受率 1.0（回归门未锻炼）——"非偶然收益"证据弱。
- ✗ deletion ratio **在 LOCAL 仍坍缩**（33 边 <50）；INTEGRATION 满足但 detection=0。
- △ Strong testing 下：4/5 仓库 INTEGRATION GFCR=0，依赖恢复边际价值显著降低；cerberus 例外（验证不足）。
- △ LLM 自然轨迹与 mutation 方向**一致**（pilot），但样本太小。

### 为何不是 YES
- 恢复的"成功"高度依赖保守门定义；检测率≈0 表明**真正瓶颈是验证器覆盖率**，而非依赖图缺失——这是 spec §22 预言的边界，被数据证实为主因。
- 真实 LLM 外部效度未建立（n=3 pilot）。
- 排序质量弱、ratio 维度在关键制度下坍缩、回归门未被锻炼。

### 为何不是 NO
- 机制真实存在并在多仓库 LOCAL 复现；real-LLM pilot 证明 agent 自报 completion 可被后续共享契约变更击穿；契约级 Reverify −84% 是 v2 之上的真实改善。

### 下一步若推进
1. 在 10+ 个"verify-set 系统性不覆盖某些契约"的真实中型仓库上测量 **natural GFC 频率**（用真实多步 agent 轨迹）；
2. 把"verify-set 是否覆盖被破坏契约"显式建模为候选 scope，恢复+更强局部验证联合；
3. 用真实 LLM（可调 API）跑 ≥50 条轨迹，建立 real-LLM GFC 频率，并对比 semantic-LLM 候选 vs 启发式；
4. 让回归门图足够密（同 symbol 跨 scope）以真正锻炼接受阈值。

若 (1)(3) 显示 real-LLM GFC 非平凡频率且恢复带来**检测率**上的真实收益（而非仅保守门预防），方向可上行至 YES；否则维持 UNCERTAIN，且应承认该方向的天花板由验证器覆盖率主导。

---

## 附录：科研诚信声明
- G* 的 CONTRACT→COMPLETION 边从校准 oracle 派生（provenance=MANUAL，note 标注 case）；候选生成器只用 G_hat + static + dynamic + 启发式 semantic + trace，**不读** `deleted_edges` GT。
- 两轨严格分轨（simulated vs real_llm），不混用；real-LLM follow-up 破坏标注为 deterministic。
- 阈值 0.20 为规范默认；阈值敏感性单独报告且未因结果临时改阈值。
- 所有 tie 显式记录（ablation.csv 的 tie_rate）；失败案例与成功案例并列；NOT OBSERVED 显式标注未构造。
- 无 LLM 作 final/patch/Ground-Truth oracle；所有最终判定基于真实 pytest + 静态/动态证据 + 反事实回放 + 回归。
- v2 三个 continuity repo 与 v3 两个新 repo 分组保留，未用新 repo 掩盖旧 repo 表现。