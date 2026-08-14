# PoC: 长程多智能体协作中的完成证据失效与 Badcase 驱动依赖修复

最小但完整可运行的 PoC,验证 4 个研究假设:

1. 稳定制造 **Global False Completion**(Local 全部 VERIFIED,但全局实际已失败);
2. **Completion Evidence Invalidation** 能阻止这种假完成;
3. **精准失效(Change-Aware)** 比"所有下游全部重跑"减少不必要验证;
4. 删除一条跨 Agent 依赖产生 Badcase 后,系统能**提出候选依赖**并通过
   **Counterfactual Replay** 证明它能阻止原失败,再经回归门控 ACCEPTED。

## 运行

```bash
cd poc
python3 run_experiments.py      # 一次性输出 4 组实验,日志写入 logs/
python3 -m pytest tests/ -q     # 19 个单元测试
```

## 场景

- Backend 提供 `/user` API(`{"name": "hsz", "age": 25}`),Frontend 消费 `user.name`,
  Testing 做契约/集成验证。初始三方 VERIFIED、Global VERIFIED。
- 长程修改: `"name" -> "username"`。Backend 自身测试仍通过,但 Frontend 的完成证据已失效。

## 结构

```
core/    models / dependency_graph / world / invalidation(3策略)/ completion_gate / change_analysis
agents/  coordinator / backend / frontend / testing
badcase/ model / analyzer / candidate_generator / replay(反事实回放 + 回归门控)
scenarios/ shared.py(world 构建、依赖图、3 类变更用例及其确定性 Ground Truth)
tests/   19 个 pytest 用例
logs/    每次实验的结构化 JSON 日志
```

## 设计要点

- **Ground Truth 全部由确定性程序给出**(schema diff 规则: 删/改字段=BREAKING,
  加可选字段=BACKWARD_COMPATIBLE,仅日志行变化=NON_SEMANTIC),LLM 不参与判定;
- Testing Agent 的重验证是**自适应**的(从新 schema 重新生成契约测试)——
  只有它能复现"测试跟着 API 更新了,但前端 VERIFIED 仍是旧证据"的真实失效模式;
- 反事实回放从 Badcase 中存储的 world snapshot 确定性重建状态,
  只改依赖图,其他一切相同;
- 候选依赖接受条件: 回放阻止原失败 **且** 回归假失效率 <= 0.2。

详见 `EXPERIMENT_REPORT.md`。
