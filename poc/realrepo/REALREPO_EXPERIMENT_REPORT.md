# RealRepo-PoC v2 实验报告

**研究问题**：当长程多智能体协作中的验证依赖结构不完整时，能否从真实失败轨迹中发现缺失的跨 Agent 验证依赖，并利用变化作用域感知的失效传播避免旧完成证据导致全局假完成？

**入口**：`python3 run_realrepo_experiments.py`（一次性产出 4 组实验 → `results/*.csv` + `results/summary.json`）
**单元测试**：`python3 -m pytest tests/ -q` → **19 passed**
**随机种子**：删边种子固定 42 / 43 / 44；G* 构建确定性；oracle 由真实 pytest 校准。

报告中严格区分 **observed result**（实验数字）、**interpretation**（对数字的解释）、**speculation**（无法从本实验直接得出的推断）。

---

## 1. Repository 信息

| repo | 语言 | source | commit | source/test 文件 | test 命令 | test 耗时 | 真实/合成 |
|---|---|---|---|---|---|---|---|
| tinydb | Python | github.com/msiemens/tinydb | `4aa5311` | 7 src / 7 test | `pytest` | 0.64s | **真实开源**（shallow clone） |
| cerberus | Python | github.com/pyeve/cerberus | `65e977d` | 5 src / 5 test | `pytest cerberus/tests` | 0.35s | **真实开源** |
| boltons | Python | github.com/mahmoud/boltons | `580a9c2` | 2 src(被覆盖的)/ 多 test | `pytest` | 3.26s | **真实开源** |

Manifest: `repo_manifest.json`。三库均为真实开源项目、测试全绿、单仓库规模远小于 monorepo，符合选题约束。**未使用任何合成仓库。**

每个仓库固定四角色：Coordinator / Developer A(producer 模块) / Developer B(consumer 模块) / Testing。Developer A 修改的 artifact 被 B 的 artifact 或 completion 实际依赖（见 G*）。

**Ground Truth 全部由程序化规则确定**：变更分类用 AST 签名 diff；oracle 用真实 pytest 子集；G* 的 ARTIFACT→COMPLETION 边由校准 oracle 派生（某 producer 的破坏性变更使某下游 verify-set FAIL ⇒ 存在依赖边）。LLM 未配置，候选生成器的 semantic 路径走确定性 heuristic fallback。

---

## 2. RealRepo 中 Global False Completion 是否真实出现

**Observed**：在 base 配置（每个 completion 的 verify-set 较窄、模拟真实"agent 自验只覆盖自己模块"）下，16 个 change case 中 **2 个产生 GFC**，GFCR = 2/16 = **0.125**，**仅出现在 tinydb**：

| case | producer | 变更 | agent_a | agent_b | testing | oracle | GFC |
|---|---|---|---|---|---|---|---|
| T2_all_breaks | tinydb/table.py | `Table.all()` 返回 list→dict | PASS | PASS | PASS | **FAIL** | **TRUE** |
| T3_insert_sig | tinydb/table.py | `insert()` 增加必选 kwarg | PASS | PASS | PASS | **FAIL** | **TRUE** |

**Interpretation**：T2/T3 的破坏只被 oracle-only 的 `test_tinydb.py`/`test_tables.py` 暴露，而 agent_b(`test_utils.py`，只测 LRUCache) 和 testing(`test_operations.py`) 的 verify-set 对这两类破坏不敏感 → 三个本地 completion 全部 VERIFIED → 全局门判 VERIFIED → 真实集成 FAIL。这正是"Local Completion 聚合 != Global Completion"在真实仓库中的复现。

cerberus/boltons 的破坏性变更都被各自 testing/agent_b 的 verify-set 直接捕获（local 即 FAIL），**未产生 GFC**——这本身是 findings 的一部分（见 §8）。

---

## 3. 精准 Invalidation 实验（Phase 2A）

4 策略 × 16 case，Ground Truth = 每个下游 completion slot 的 verify-set 在该变更下真实 FAIL 的集合。

| Strategy | Precision | Recall | F1 | False Inv Rate | Missed Inv Rate | Reverify Count | GFCR |
|---|---|---|---|---|---|---|---|
| all_downstream | 0.25 | 1.00 | 0.40 | **1.00** | 0.00 | **32** | 0.12 |
| static | 0.42 | 1.00 | 0.59 | 0.46 | 0.00 | 19 | 0.12 |
| freshness | 0.42 | 1.00 | 0.59 | 0.46 | 0.00 | 19 | 0.12 |
| **change_aware** | **0.67** | 1.00 | **0.80** | **0.17** | 0.00 | **12** | 0.12 |

**Interpretation**：
- change_aware 把 Reverification Count 从 32 降到 12（−62.5%），False Invalidation Rate 从 1.00 降到 0.17，F1 从 0.40 升到 0.80。**精准失效相比全量失效有实质且可量化的收益**，假设 3 在真实仓库成立。
- static 与 freshness 数字相同：在本场景两者都只用 G* 边而不做语义区分，因此 freshness（任意内容变化即失效）相对 static 没有额外信息——freshness baseline 的假失效成本与 static 持平，但远高于 change_aware。说明"作用域感知"才是收益来源，而非"是否挂钩依赖图"本身。
- **GFCR 在四种策略下都是 0.12**：无论失效策略如何，完整 G* 下那 2 个 GFC 都无法被阻止。原因：T2/T3 的破坏性变更类型（T2 是 body-only 返回类型变更→分类为 POTENTIALLY_BREAKING；T3 是 kwonly-required→分类正确为 BREAKING）在 G* 中 table.py→testing 边的 scope 内，但 testing 的 verify-set 对该破坏**不敏感**——invalidator 正确 invalidated 了 testing，重验证却 PASS，testing 仍回到 VERIFIED，gate 仍 OK。**这说明：依赖图完整 + 失效机制正确，仍不能阻止 GFC，当且仅当下游 verify-set 本身未覆盖被破坏行为。** 这是本阶段最重要的真实发现之一。

---

## 4. Missing Dependency Recovery（Phase 2B）

使用 **extended 配置**（testing 的 verify-set 扩展到也覆盖 contract 测试，模拟"集成测试 agent 职责更全"），使部分 GFC 可经 missing-edge 恢复。10% / 20% / 30% 删边，种子 42/43/44。

| ratio | n_badcases | Recall@1 | Recall@3 | Recall@5 | Precision@5 | MRR | CF Fix Rate | Patch Accept Rate | RegFailRate | FIR |
|---|---|---|---|---|---|---|---|---|---|---|
| 10% | 11 | 1.00 | 1.00 | 1.00 | 0.59 | 1.00 | 1.00 | **0.73** | 0.18 | 0.06 |
| 20% | 11 | 1.00 | 1.00 | 1.00 | 0.59 | 1.00 | 1.00 | **0.73** | 0.18 | 0.06 |
| 30% | 11 | 1.00 | 1.00 | 1.00 | 0.59 | 1.00 | 1.00 | **0.73** | 0.18 | 0.06 |

**Observed**：Recall@K = 1.00、Counterfactual Fix Rate = 1.00——combined 候选生成器在所有 33 个 badcase 上把正确的缺失边排进 Top-K，且反事实真 pytest 回放确认加边后阻止了原失败。

**两个必须诚实说明的局限**：

1. **三档 ratio 数字完全相同**。原因：tinydb 只有 1 条 ARTIFACT→COMPLETION 边（table.py→testing_completion），10/20/30% 删除都删同一条；cerberus 仅在 seed 43/44 删对边时产生 1 个 badcase；boltons 因 breaking 变更总被 local 直接捕获、删边后仍不构成 GFC，**0 badcase**。所以"删除比例"维度在本阶段仓库规模下没有区分度——这是真实的结构性限制，不是 bug。要检验 ratio 的真实影响需更大的、A→C 边更多的仓库。

2. **Patch Acceptance Rate = 0.73 < Fix Rate = 1.00**。27% 的 badcase（全部是 T2_all_breaks）虽然反事实回放阻止了失败，但**回归门拒绝了**该补丁：加 testing 边后，T2 的 regF = 0.33 超过 0.20 阈值——意思是该边让一些**非破坏性**变更也错误地失效了 testing。这正是回归门存在的意义：**"能阻止这一次失败"不等价于"是安全的依赖"**。

---

## 5. Counterfactual Replay 统计

**Observed**：33 个 badcase × Top-1 候选，真 pytest 回放结果——Counterfactual Fix Rate = **1.00**（每个 Top-1 候选都阻止了原失败）。但同时，Top-1 候选并非都"等于 Ground Truth 边"：在 tinydb，Top-1 始终是 `testing_completion`（与 GT 一致）；

更关键的反例在同一 badcase 的次优候选上：
- T3_insert_sig 的 `agent_b_completion` 候选 → 真 pytest 回放 **prevent=False**（agent_b 的 verify-set `test_utils.py` 对 insert 签名变化不敏感，重验证 PASS，gate 仍 OK，oracle 仍 FAIL）。
- T3_insert_sig 的 `testing_completion` 候选 → 真 pytest 回放 **prevent=True**。

**Interpretation**：counterfactual gate 成功**区分了"看起来合理的候选"与"真能阻止失败的候选"**。如果不做真回放、只比对候选是否等于 GT 边，会误判——也只有真回放能暴露"加 agent_b 边救不了 GFC"这一事实。这验证了规范要求的"不要只检查 candidate 是否等于 Ground Truth edge"。

---

## 6. Regression 统计

**Observed**：被 CF gate 接受的补丁，其回归 False Invalidation Rate 均值 0.06、Regression Failure Rate 均值 0.18。被拒绝的 T2 补丁 regF=0.33（超阈值）。**回归门阻止了"修复一处、破坏多处"的过度失效**。

**Interpretation**：新依赖确实会带来额外的 invalidation 成本——加 `table.py→testing` 边让所有"作用域内"的 table.py 变更都 invalidate testing，包括那些 body-only、testing verify-set 本不该关心的变更。门控在 0.20 阈值下拒绝了 27% 的补丁，说明阈值选择保守且有效。

---

## 7. Ablation

| variant | Recall@3 | CF Fix Rate | Patch Accept Rate | RegFailRate |
|---|---|---|---|---|
| full | 1.00 | 1.00 | 0.73 | 0.18 |
| − static | 1.00 | 1.00 | 0.73 | 0.18 |
| − dynamic | 1.00 | 1.00 | 0.73 | 0.18 |
| − semantic | 1.00 | **0.18** | 0.18 | **0.82** |
| − trace | 1.00 | 1.00 | 0.73 | 0.18 |
| − cf_gate | 1.00 | 1.00 | **1.00** | 0.00 |

**Interpretation（需谨慎）**：

- **− cf_gate**：Accept 升到 1.00、RegFail 降到 0.00——看似更好，实际是**取消了门控就接受所有补丁**（包括 T2 的有害补丁）。这是定义上的"假高分"：`no_cf_gate` 分支直接 accept=True 且 regf 记 0。**这恰恰证明 CF gate 不是冗余**：没有它，T2 的 regF=0.33 的有害补丁会被接受。规范要求的不做 cf_gate 对照，本应体现为"接受率上升但回归质量下降"——本实验按定义实现，结论是 **CF gate 不可省**。

- **− semantic**：Fix Rate 从 1.00 暴跌到 0.18。但是——**这不是 semantic 信号本身的贡献**。经过调查：去掉 semantic 后，`testing_completion` 与 `agent_b_completion` 的融合 confidence 恰好平局（都 0.8），按 target 字典序稳定排序把 `agent_b_completion` 排到 Top-1，而 agent_b 候选 prevent=False → Fix 暴跌。**tinydb 的 dynamic coverage 因 import 链覆盖了 table.py，对两个 slot 都触发，无法先验区分**。所以 no_semantic 的暴跌本质是**排序平局 tiebreak 的脆弱性**，而非 semantic 信号提供了关键依赖信息。这是一个诚实的负面发现：**在 small-repo / import-chain-coverage 场景下，候选排序对单信号移除非常敏感，且该敏感性主要来自 tiebreak 而非信号贡献差异。**

- **− static / − dynamic / − trace 与 full 完全相同**：在本阶段仓库规模下，trace 与 dynamic 任一单独就足以把 testing_completion 推到 Top-1（target 相同、CF 结果相同）。**单信号边际贡献在本 PoC 规模下不可观测**——需要更大仓库、更多 A→C 边才能区分各信号贡献。这是真实局限。

**净结论**：Ablation 在本规模下只能可靠证明两件事——(a) CF gate 不可省（否则接受有害补丁）；(b) 候选排序对信号缺失敏感，但敏感性来源混杂（tiebreak vs 信号贡献），无法干净归因。其余信号贡献需更大规模实验。

---

## 8. Test Coverage Sensitivity

在 tinydb 上对比 LOW（base 配置，testing 只测 test_operations.py）与 HIGH（extended 配置，testing 也覆盖 test_tables.py）：

| regime | GFCR(完整 G*) | n_badcases(删边后) | recovered | recovery_rate |
|---|---|---|---|---|
| LOW | **0.33** | 3 | 3 | 1.00 |
| HIGH | **0.00** | 3 | 3 | 1.00 |

**Interpretation**：
- HIGH 下完整 G* 的 GFCR = **0.00**——当 Testing Agent 强（verify-set 覆盖 contract）时，breaking change 在 local 阶段就被 testing 捕获，**全局假完成问题在本仓库被根治**。这如实支持"强 testing agent 会削弱研究问题"的担忧。
- 但 HIGH 下删边后仍产生 3 个 badcase 且全部恢复——说明**即使 testing 强，一旦依赖图边缺失，GFC 仍会重新出现**（删 table→testing 边后 testing 不再被 invalidate → 又 GFC）。恢复机制在此仍有价值。
- LOW 与 HIGH 的 recovery_rate 都是 1.00——recovery 在两 regime 下都有效，但 HIGH 下"需要 recovery 的场景"本就由完整图阻止了，recovery 是在**人为删边**后才需要的。

**Speculation**：更强的 testing 会降低 GFC 的**发生频率**，但不降低"依赖结构缺失时 GFC 再次出现"的**机制**。两者是否在真实长程任务中此消彼长，本 PoC 规模无法判定。

---

## 9. 失败案例分析

### 成功 recovery × 2

**S1 — T3_insert_sig (tinydb, seed 42, r10)**
- 删边：`tinydb/table.py → testing_completion`
- 现象：insert() 加必选 kwarg 后，agent_a/b/testing 三个 verify-set 全 PASS（agent_b 测 LRUCache、testing 的 test_operations 调 db.insert({'int':1}) 不传 kwarg 却因 tinydb 实现细节仍 PASS），oracle 的 test_tables FAIL → GFC=TRUE
- 候选：combined 把 `testing_completion` 排到 Top-1（confidence 0.85，trace+dynamic+semantic 融合）
- 反事实：真 pytest 回放，加边后 testing 被 invalidate → 重验证 test_tables/test_operations → **FAILED** → gate 转 FAILED → GFC=FALSE
- 回归：regF=0.17 ≤ 0.20 → **ACCEPTED**
- 完整闭环成功

**S2 — C3_remove_allowed (cerberus, seed 43, r10)**
- 删边：`cerberus/validator.py → testing_completion`
- 候选 Top-1 = testing_completion，CF 回放 prevent=True，regF=0.00 → **ACCEPTED**
- 注：cerberus 只在 seed 43/44 产生该 badcase（seed 42 删的是 agent_b 边，但 C3 的 agent_b verify-set PASS，删它不产生 GFC）——种子多样性真实生效

### 失败 recovery × 2

**F1 — T2_all_breaks (tinydb)：修复有效但被回归门拒绝**
- 删边同上，Top-1 = testing_completion，CF 回放 prevent=**True**（加边确实阻止了 GFC）
- 但回归门 regF=**0.33** > 0.20 → **REJECTED**
- 原因：加 testing 边让所有 POTENTIALLY_BREAKING/BREAKING 的 table.py 变更都 invalidate testing。T2 本身是 body-only 返回类型变更（被分类为 POTENTIALLY_BREAKING），testing 边对一些**与 testing verify-set 无关**的破坏也触发了失效。
- **解读**：这个候选确实是"缺失的依赖"，但它过于宽泛——恢复器的候选是 `scope=[BREAKING]`，但课 G* GT 也是 BREAKING scope；regF 高是因为本 repo testing verify-set 覆盖窄，加边后 false-invalidation 放大。**恢复器目前无法区分"窄而精确的边"与"宽而昂贵的边"**，这是改进空间。

**F2 — T3 的 agent_b_completion 候选：候选生成器共识但 CF 拒绝**
- 同一 T3 badcase，combined 候选列表里 `agent_b_completion` 排第 2（confidence 0.80）
- CF 真回放：加 agent_b 边 → agent_b 被 invalidate → 重验证 test_utils.py → **PASS**（test_utils 对 insert 签名完全不敏感）→ agent_b 回 VERIFIED → gate 仍 OK → oracle 仍 FAIL → **prevent=False**
- **解读**：trace/dynamic 信号都"合理地"提议了 agent_b（GFC 时 agent_b 存活 + dynamic 覆盖了 table），但只有 CF 真回放能证明加 agent_b 边**救不了 GFC**。这正是否则只靠候选排序会犯的错——验证了 CF gate 的必要性。

---

## 10. 当前研究结论

### 10.1 真实仓库是否确实存在 stale completion / missing dependency 问题？

**Yes（有限）**。base 配置下真实复现 GFC（tinydb T2/T3，GFCR=0.125），且 GFC 的成因被精确刻画为"下游 verify-set 不覆盖被破坏行为 + 依赖边缺失"。但它只出现在 1/3 仓库、且依赖一个"verify-set 覆盖窄"的人为配置；在 cerberus/boltons 的真实 verify-set 下，breaking 变更被 local 直接捕获，问题不出现。

### 10.2 Proposed 方法相比 Static / Freshness baseline 是否有额外收益？

**Yes**。Phase 2A：change_aware 的 FIR 0.17 vs static/freshness 0.46 vs all_downstream 1.00；F1 0.80 vs 0.59 vs 0.40；Reverify 12 vs 19 vs 32。**精准失效的收益在真实仓库可复现且显著**。

### 10.3 Badcase recovery 是否能泛化到多个 dependency？

**Partially**。在同一 producer→不同 slot 的多候选上，CF gate 能正确接受有效候选、拒绝无效候选（T3 的 testing=accept、agent_b=reject）。但跨仓库泛化弱：cerberus 仅 C3 单一 badcase、boltons 零 badcase，**3/3 仓库中只有 1 个产生了可恢复的多依赖场景**。至多说明机制正确，不足以证明泛化。

### 10.4 最大失败原因是什么？

1. **A→C 边过少导致实验维度坍缩**：三 repo 的 ARTIFACT→COMPLETION 边分别只有 1/2/4 条，使 deletion ratio 维度、多依赖恢复维度都无法展开。tinydb 三个 ratio 数字完全相同即是此症。
2. **verify-set 覆盖决定 GFC 是否可恢复**：当破坏只被 oracle-only 测试暴露时（base 配置），无论恢复何种 dependency 边都救不了 GFC——这是 Phase 2A GFCR 在四策略下不变的根本原因。recovery 的有效性**以上游 verify-set 覆盖被破坏行为为前提**。
3. **候选排序对单信号移除敏感且来源混杂**：ablation 的 no_semantic 暴跌被证实是 tiebreak 伪影而非信号贡献，说明在 small-repo 规模下无法干净归因各信号贡献。
4. **回归门与恢复目标存在张力**：T2 显示"能阻止失败的候选"可能 regF 超阈被拒，恢复器无法生成"窄而精确"的边。

### 10.5 是否值得进入正式硕士论文阶段？

## **Research Direction: UNCERTAIN**

**支持继续的证据（observed）**：
- GFC 在真实开源仓库（tinydb）被确定性复现，机制清晰；
- change-aware 失效相对全量/静态/freshness baseline 有可量化、可复现的收益（FIR 0.17、Reverify −62.5%）；
- counterfactual gate 能在真 pytest 上区分有效/无效依赖候选，且能拒绝"修复一处破坏多处"的有害补丁；
- "依赖边缺失 ⇒ GFC 重现"在 coverage 实验中机制性成立。

**反对此刻下 YES 的证据（observed）**：
- 问题只在小规模、窄 verify-set 配置下出现；只要 testing agent 略强（HIGH），GFCR → 0，问题的**现实频率存疑**；
- 3 个仓库中 2 个**不产生**可恢复 badcase，泛化证据极弱；
- Ablation 在本规模下无法干净归因信号贡献，no_semantic 的"暴跌"是 tiebreak 伪影；
- deletion ratio 这一核心实验维度因边数过少而坍缩。

**下一步若推进应直接攻**：(a) 在 5–10 个 A→C 边较多、verify-set 覆盖差异明显的真实中型仓库上重测，检验 GFC 发生频率与 ratio 维度；(b) 把"verify-set 是否覆盖被破坏行为"显式建模为候选 scope，缓解 T2 型 regF 问题；(c) 用真实 LLM agent 轨迹数据替代当前手工 change case，外部效度才有可能从 UNCERTAIN 上行到 YES。

---

## 附录：科研诚信声明

- Ground Truth 由程序化规则（AST diff + 真实 pytest）确定，**无 LLM 作 Oracle**。
- 删除边的 `deleted_edges.json`（在 `results/phase2b.csv` 的 `deleted_gt` 列）**未泄漏给候选生成器**——候选生成器只用 G_hat、coverage、static、semantic、badcase trace；ground truth 仅用于 Recall@K 评估。
- 所有随机种子记录（42/43/44）；所有 baseline 真实实现并参与同一指标计算。
- 报告区分 observed result / interpretation / speculation（已显式标注）。
- 失败案例（F1/F2）与成功案例并列呈现，未删除任何失败 run。
- 未因追求方向成立而调整 G*、verify-set 或阈值；0.20 阈值为规范默认值。