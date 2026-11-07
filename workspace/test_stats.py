"""stats 的测试。运行：python test_stats.py（全部通过输出 OK）"""

from stats import mean, variance

cases = [
    (mean([1, 2, 3]), 2.0),
    (mean([2.5, 3.5]), 3.0),
    (mean([]), 0.0),            # 修好后通过：类型必须是 float
    (variance([1, 2, 3, 4]), 5 / 3),
    (variance([5]), 0.0),
    (variance([]), 0.0),
]

for i, (got, want) in enumerate(cases, 1):
    assert isinstance(got, type(want)), f"case {i}: 类型不一致 {type(got)} != {type(want)}"
    assert abs(got - want) < 1e-9, f"case {i}: {got} != {want}"

print(f"Ran {len(cases)} tests ... OK")
