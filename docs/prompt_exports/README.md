# 单智能体完整 LLM 输入

每个 `.txt` 文件从 `SYSTEM MESSAGE` 和 `USER MESSAGE` 两段组成；段标题只是阅读标记，标题后的内容是完整的实际消息文本，没有省略样本行或运行时插值内容。

`ALL_PROMPTS_COMPLETE.txt` 将两项目、两模式下的六份正式提示词以及共用预检提示词汇总在一个文件中；各原始文件仍分别保留，便于复制某一个 LLM 的完整输入。

| 文件 | 状态 | 正式 user prompt 的样本数 |
| --- | --- | ---: |
| `parquet_40_sampling.txt` | 新运行第一轮，先导入 40 条初始样本 | TOP-20 |
| `zero_start_sampling.txt` | 新运行第一轮，从空样本池启动 | 0 |
| `preflight.txt` | 正式请求前的独立连接预检 | 不含样本 |

`parquet_40_sampling.txt` 的 user prompt 已与 `O_2026_10_14_1930_ds_two_candidate` 中第一轮采样交互日志逐字符比对一致。零样本文件由当前代码离线渲染；不代表曾向外部 LLM 发送或执行 HFSS。

第二轮及以后会随真实 HFSS 样本变化，无法用一个固定文本代表所有轮次。连接失败时会重发当前输入；输出格式不合格时会生成带有具体错误详情的短纠错消息，并附带本轮先前的 user/assistant 对话。纠错文本由 `llmo/agents.py` 的 `_sampling_correction_prompt` 生成。

`manifest.json` 记录每份完整文件的字符数、样本数与 SHA-256。`export_examples.py` 可离线重新生成这些文件。
