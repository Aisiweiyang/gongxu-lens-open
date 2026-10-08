"""v19 任务 1：场景级并行化等价回归（红线 3：调度层改动，输出必须逐位一致）。

①串行（SUPPLYLENS_PARALLEL_WORKERS=1）与并行（缺省）路径的 analyze_task 输出
逐字节一致；②run_sensitivity 串行/并行输出一致且场景保序；③worker 数 helper
的环境变量覆盖与缺省上限语义。改前参考哈希复现、7/7 独立核验与全量数值锁定
为另外三层把守（见 docs/archive/process/自审-v19.md）。
"""

import json
import os
import unittest

from src.material import analyze_task, load_material_factors, run_sensitivity
from pilot.api import serialize_result, demand_from_wizard, supply_from_payload
from tests.test_pilot import DEMAND, SUPPLIES

ENV = "SUPPLYLENS_PARALLEL_WORKERS"


def _analyze_bytes():
    fd = load_material_factors()
    demand = demand_from_wizard(dict(DEMAND))
    sup = [supply_from_payload(dict(s)) for s in SUPPLIES]
    result = analyze_task(demand, sup, fd)
    return json.dumps(serialize_result(result), ensure_ascii=False, sort_keys=True)


def _sensitivity_bytes():
    fd = load_material_factors()
    demand = demand_from_wizard(dict(DEMAND))
    sup = [supply_from_payload(dict(s)) for s in SUPPLIES]
    return json.dumps(run_sensitivity(demand, sup, fd),
                      ensure_ascii=False, sort_keys=True)


class TestParallelEquivalence(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(ENV, None)

    def test_01_analyze_serial_parallel_identical(self):
        os.environ[ENV] = "1"
        try:
            serial = _analyze_bytes()
        finally:
            os.environ.pop(ENV, None)
        parallel = _analyze_bytes()
        self.assertEqual(serial, parallel)

    def test_02_sensitivity_serial_parallel_identical(self):
        os.environ[ENV] = "1"
        try:
            serial = _sensitivity_bytes()
        finally:
            os.environ.pop(ENV, None)
        parallel = _sensitivity_bytes()
        self.assertEqual(serial, parallel)
        # 场景保序由字节一致隐含保证：聚合按注册顺序（executor.map 保序），
        # 若并行打乱顺序，上面逐字节断言必然失败，无需另行排序假设。

    def test_03_worker_env_semantics(self):
        from src import material
        os.environ[ENV] = "1"
        try:
            self.assertEqual(material._parallel_workers(6), 1)
        finally:
            os.environ.pop(ENV, None)
        os.environ[ENV] = "99"
        try:
            self.assertEqual(material._parallel_workers(6), 99)  # 显式覆盖不设上限
        finally:
            os.environ.pop(ENV, None)
        # 缺省：不超过并行单元数
        self.assertLessEqual(material._parallel_workers(3), 3)
        self.assertGreaterEqual(material._parallel_workers(3), 1)


if __name__ == "__main__":
    unittest.main()
