# 评测数据集

S3 开始建，先 20 题。

一条的形式：

```yaml
id: xxx-001
question: ...
expects:
  must_cite: true
  must_call_tools: [search_docs]
  must_contain_source: "..."
  should_refuse: false
```

**正解必须自己定**，不能让 AI 生成了就算。
