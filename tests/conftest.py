"""跑测试不该依赖真实 API key。

server.py 在 import 的时候就会构造 LLM()（见其模块顶层的 `llm = LLM()`），
这一步只是建 OpenAI/AsyncOpenAI 客户端对象，不发请求，但缺 LLM_API_KEY 会
在 import 阶段就抛 ConfigError——测试套件在没配 .env 的机器（比如 CI、
别人 clone 下来第一次跑）上会直接炸在 import 这一步，看起来像所有测试
都挂了，其实是配置问题不是代码问题。

这里在任何测试模块 import 之前把假值垫上。conftest.py 保证在 pytest
收集测试之前就执行，比在某个 test 文件顶部写 os.environ 更可靠——
不用管 pytest 按什么顺序收集文件。
"""

import os

os.environ.setdefault("LLM_API_KEY", "test-key-never-used")
os.environ.setdefault("LLM_BASE_URL", "http://test.invalid")
