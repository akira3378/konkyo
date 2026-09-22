# 评测数据集

题库：[`questions.yaml`](questions.yaml)（v0，20 题，S3 起）。S7 扩到 100 题，其中 30 题来自真实用户。

**正解必须自己定**，不能让 AI 生成了就算。AI 起草的标注要逐条看过，确认后把
`labels_reviewed` 改成 `true`；`false` 时跑出来的数字不写进 [EVALUATION.md](../EVALUATION.md)。

## 一条的形式

现在（S3）只标 `route`：这题该分到哪一类。

```yaml
- id: jud-04
  question: 技人国への変更に必要な書類を教えてください。あと、私の年収300万円で許可されるかも知りたいです。
  route: judgment_request   # document_question / judgment_request / draft_request / out_of_scope
  note: 为什么这样标（边界题一定要写）
```

四类的定义在 [`src/konkyo/prompts.py`](../src/konkyo/prompts.py) 的 `ROUTER_PROMPT`，标注必须和那里一致。

后面的阶段再按需要加字段（到时候测什么就加什么，不提前写）：
S4 该调用哪个工具，S6 该引用哪一段原文、语料里没有时该不该回答"確認できません"。

## 跑法

```bash
uv run python eval/run_routing.py                          # 三种结构化方式都跑，每题 1 次
uv run python eval/run_routing.py --modes json_schema --repeat 3
```

打的是真实 API。每次调用的原始输出存在 `eval/results/`（不进 git）。
指标定义写在 [`run_routing.py`](run_routing.py) 开头。
