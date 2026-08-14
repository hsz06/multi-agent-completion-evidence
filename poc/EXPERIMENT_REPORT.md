# EXPERIMENT REPORT

PoC:长程多智能体协作中的完成证据失效与 Badcase 驱动依赖修复
日期: 2026-08-12 · 代码: `poc/` · 复现: `python3 run_experiments.py`(seed=42)
原始日志: `poc/logs/experiment_{1..4}.json` · 单元测试: **19 passed**

---

## 1. 是否成功复现 Global False Completion?

**是,稳定复现。** 轨迹(Experiment 1,无任何失效机制的 baseline):

| 步骤 | 状态 |
|---|---|
| 初始: Backend v1 / Frontend(消费 `user.name`)/ Testing 各自验证通过 | 三方 VERIFIED,Global = VERIFIED,hidden check = PASSED |
| Backend 把 API schema 从 v1 改为 v2(`name` → `username`) | Backend 自身单元测试仍 PASS |
| 无任何 evidence invalidation | Frontend/Testing 的 CompletionClaim **保持 VERIFIED** |
| Coordinator 聚合本地完成状态 | **Global = VERIFIED** |
| 运行 hidden integration check(oracle: 前端消费字段 ⊆ 当前 schema 字段) | **FAILED**(missing fields: `['name']`) |
| 结论 | **Global False Completion = TRUE** |

每次运行必现,非随机事件。

## 2. 普通状态聚合为什么失败?

`backend=done ∧ frontend=done ∧ testing=done` 推不出 Global Completion,因为聚合只检查
**claim 的标签**,不检查 **claim 的证据所绑定的 artifact 版本**。

Frontend 的 VERIFIED 是"基于 API_SCHEMA **v1** 的字段检查 PASS"这一证据;当 schema 变成
v2 后,该证据的对象已经不存在——claim 标签没有机制感知到自己赖以成立的证据失效了。
于是出现 *Local Completion 全部成立,Global Completion 实际错误*。
本质: **完成状态是关于<证据, artifact版本>二元组的命题,而聚合只看了命题的标签。**

## 3. Evidence Invalidation 是否能够阻止失败?

**能。** Experiment 2(change-aware 策略 + 完整依赖图):

```
API schema changed: v1 -> v2 (BREAKING)
Frontend claim: VERIFIED -> STALE
Testing claim:  VERIFIED -> STALE
Global Completion: NOT_READY
Frontend revalidation: FAILED            # 前端仍在读 user.name
False Completion Prevented: TRUE
```

依赖边 `API_SCHEMA -> {FRONTEND,TESTING}_COMPLETION` 把版本变化精准传播到受影响的 claim;
Global Gate 看到 STALE 后拒绝宣告完成(VERIFIED → NOT_READY),
随后重验证把真实的 BREAKING 兼容问题以 FAILED 形式表面化,而不是埋进"已完成"。

## 4. 全量失效和精准失效有什么差异?

Experiment 3:3 类变更加权 3 种策略,Ground Truth 由确定性 diff 规则给出:

| Strategy | Missed | False Invalidations | Revalidation Count | Precision | Recall |
|---|---|---|---|---|---|
| all_downstream | 0 | **4** | **6** | 0.33 | 1.00 |
| static (仅依赖图) | 0 | 2 | 4 | 0.50 | 1.00 |
| **change_aware (图+变更类型)** | 0 | 0 | **2** | 1.00 | 1.00 |

- **Case A(日志改动)**: all_downstream 白白重验 2 个 claim;static/change_aware 不失效;
- **Case B(name→username)**: 三者都正确失效(TP);
- **Case C(加可选字段)**: static 把兼容变更也当失效理由(2 次误失效),只有 change_aware
  利用边的 scope(仅 BREAKING 触发)保持 VERIFIED。

结论: 精准失效把重验证次数从 6 降到 2(↓67%),同时 missed=0、假失效=0。
代价是需要一条可靠的 change 分类规则——本 PoC 中是确定的 schema diff,
现实系统中这是主要工程难点。

## 5. Missing Dependency 是否可以从 Badcase 恢复?

**可以,全链路闭环。**

**Badcase**: 删除 `API_SCHEMA -> FRONTEND_COMPLETION` 后重放 breaking 变更:

```
Testing:  VERIFIED -> STALE -> revalidated VERIFIED   # 测试套件自适应新 schema,重新通过
Frontend claim stays: VERIFIED                        # 旧证据存活
Global Completion: VERIFIED  /  Hidden: FAILED  →  global_false_completion = TRUE
```

注意这里的失效模式比 Experiment 1 更隐蔽: 依赖图**部分存在**时,系统的失效机制照常运转、
测试也确实重新跑过且通过了——局部一切正常,唯独前端 claim 是旧证据。

**候选依赖**(规则+trace 生成,无 LLM):

```
API_SCHEMA -> FRONTEND_COMPLETION  (scope: BREAKING)
Reason: Frontend completion was VERIFIED against API_SCHEMA v1 and consumes it,
but API_SCHEMA changed to v2 (BREAKING) without invalidating the claim —
no dependency edge covers it.
```

生成规则: 在 after-trace 中找"仍 VERIFIED、based_on 版本落后于变更 artifact、且无依赖边覆盖"
的 claim(排除变更生产者自身——它生产时已重新自证)。

**Counterfactual Replay**(从 snapshot 确定性重建,仅改依赖图):

```
Without patch:              Global False Completion = TRUE
With candidate dependency:  Frontend VERIFIED -> STALE -> revalidation FAILED
                            Global False Completion = FALSE
Candidate prevented failure = TRUE
```

**回归门控**(3 用例: breaking / 日志 / 可选字段):

```
precision = 1.0, recall = 1.0, false_invalidation_rate = 0.0 (阈值 ≤ 0.2)
Patch decision: ACCEPTED
```

## 6. 当前 PoC 还不能证明什么?

如实列出:

1. **场景是人为构造的**——4 个 Agent、1 个 API、4 类 artifact,failure mode 是按设计注入的;
   未证明真实 LLM 多智能体系统中该问题的**发生频率**。
2. **Ground Truth 是确定性规则**,不是真实混沌环境;hidden oracle(字段子集检查)在现实中
   未必存在或昂贵——很多系统恰恰缺这个 oracle 才让假完成活到线上。
3. **候选依赖生成器很简陋**: "幸存 VERIFIED claim + 缺席的边"一条规则,只覆盖
   *缺失的 artifact→claim 边* 这一类图缺陷;边 scope 标错、传递性缺失、
   task→task 依赖缺失均未涉及。
4. **change 分类规则是手写的**;真实代码变更的语义分类(breaking vs compatible)
   本身就是一个难题,PoC 假设它免费可得。
5. **Testing Agent 的"自适应重验证"是刻意构造的便利假设**;现实中测试可能覆盖前端集成,
   从而在重验证阶段就拦截失败——那会使 Global False Completion 不那么容易存活。
6. 未与正式 baseline(EA-Graph 等)对比;未覆盖真实大型 repository、跨项目泛化;
   未度量规模增长时依赖图维护成本。
7. 回放只验证了"候选边能阻止这一条轨迹的失败",一条 badcase 一条边;
   未验证批量 badcase 下的候选合并与冲突处理。

---

## 最终结论

**该方向是否值得继续做毕业课题: YES**

理由(基于实验结果而非预设):

1. **问题机制被清晰分离**: 4 组实验各自独立成立——baseline 必现假完成、
   失效机制必能阻止、精准失效有可量化的收益(重验证 6→2、假失效 4→0)、
   badcase→候选→回放→回归 闭环可 ACCEPTED。机制层面因果链完整、确定、可重复。
2. **最有研究价值的点是 Experiment 4 揭示的"部分依赖图下的隐蔽存活"**:
   不是"没有失效机制"(太 trivial),而是"失效机制在运转、测试在重跑、一切看似正常,
   唯独一条旧证据存活"。这比 baseline 更接近真实事故形态,且候选生成+反事实回放+
   回归门控形成了一个可扩展的修复范式——这是可以继续做深的部分。
3. **风险也已看清**(见第 6 节): 最大的外部效度威胁是"真实任务中 change 分类与
   hidden oracle 的可得性"。毕业课题的下一步应直接攻这两点:
   在真实 agent 轨迹数据上测量该问题的发生率、用程序化静态分析近似 change 分类。
