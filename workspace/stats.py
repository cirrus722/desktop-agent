"""演示工作区里的示例项目：一个有 bug 的 stats 模块。

bug：mean 对空列表返回 int 0，应返回 float 0.0（保持类型一致）。
现场演示时，老师给的"当场需求"落在这种你没见过的文件上 ——
用这个小项目练手：agent 要 read -> edit -> bash 跑测试 -> 汇报。
"""


def mean(nums):
    if not nums:
        return 0
    total = 0
    for n in nums:
        total += n
    return total / len(nums)


def variance(nums):
    if len(nums) < 2:
        return 0.0
    m = mean(nums)
    return sum((x - m) ** 2 for x in nums) / (len(nums) - 1)
