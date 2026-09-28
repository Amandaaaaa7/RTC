"""时钟统一入口：生产代码一律经本模块取时间，便于测试替换为假时钟。

face_tracker 的“无脸时长/有脸时长”需要在测试里冻结时钟。CPython 3.10+
把 time.monotonic / time.monotonic_ns 做成 module 级 C 函数，直接给 time 模块
属性赋值在多个平台不生效，因此这里用一层可替换的 indirection。
"""

import time as _time

monotonic = _time.monotonic
monotonic_ns = _time.monotonic_ns
time = _time.time
