# report.md — 增量作业报告

## 一、每个增量做完之后，哪件事变快了

### A. 技能系统：从“每轮都背全文”到“先看目录，按需加载”

第一次运行 `python -m app.inspect skills --out skill_report.json` 得到的数字是：

- 技能目录：249 tokens
- 三份 SKILL.md 全文：1618 tokens
- `disclosure_ratio`：6.5

也就是说，启动和每一轮请求只付目录的 249 tokens；真正需要某个流程时，才用
`use_skill` 把那一份正文加载进来。技能数量继续增加时，目录只线性增长，正文不会
在每一轮重复付费。8 条触发试验全部命中，其中两条“今天天气”“写一首诗”保持
`hit: null`，`false_positive: 0`。现在把流程写进 `skills/` 后，下一次遇到同类请求
不必再靠对话把它重新教一遍。

### B. 定制层：把“记得提醒”换成“执行前必过”

`python -m app.inspect custom --out custom_report.json` 的真实结果是：

- `AGENTS.md`：项目层 2012 bytes，`found: true`
- 拦截：`.env` 与 `rm -rf build/` 各 1 条，均带 `reason`
- 自动测试：4 次运行，2 红 2 绿，`red_to_green: 2`，每次只回注 3 行
- reviewer：2 次运行，发现 5 个问题，主上下文增量 101 tokens

变化不是“模型更听话了”，而是有些事不再依赖模型自觉：写密钥在工具执行前就失败，
测试红了会在同一个工具结果里把失败摘要送回去，reviewer 的 diff 阅读留在子上下文里。
主 agent 不需要先读一遍完整 diff，也不需要把 80 行 traceback 塞进历史。

## 二、一次判断错误

我一开始把 `review-diff` 的触发语写成“审一遍 diff”，以为只要抓住 diff 这个词就够了。
第一次做触发试验时，“把这段 diff 审一遍”因为语序不同没有命中；这说明描述里的触发语
不能靠我心里的同义词，而要写成用户真的会说出口的完整短句。

改法是把 description 里的引号触发语补成 “把这段 diff 审一遍”“审一遍 diff”
“review 这段改动” 等几种自然说法，并让 `skill_report.json` 的 trials 直接使用这些
句子做验证。改完后 8 条试验全过，误触发仍为 0。这个错误也解释了为什么不能拿目录名
或单个关键词猜技能：中文请求的顺序和省略都很多，触发条件必须写在 description 里。

## 三、如果只保留一个增量，留哪个

留 A（技能系统）。B 的 hook 和 reviewer 更像这个项目当前的安全带，价值明确，但换一个
仓库就要重新判断哪些命令危险、测试命令是什么；A 的技能文件是可以直接带走的流程资产，
而且渐进披露的收益随技能数量增加而增长。只保留一个时，我会先留下 `skills/` 和
`use_skill`，让 agent 下次仍然知道“这件事该怎么做”。
