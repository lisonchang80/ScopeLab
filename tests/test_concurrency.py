"""吃 GPU 的事件（訓練 / 批次推論 / 單張推論）共用同一並發群組且上限 1，佇列有上限。"""


def _cfg(server, qualname):
    bf = server.blocks.fns[server.fn_index(qualname)]
    return getattr(bf, "concurrency_id", None), getattr(bf, "concurrency_limit", None)


def test_gpu_events_share_one_slot(server):
    for qn in ("build_train_tab.<locals>.on_train",
               "build_infer_tab.<locals>.on_infer",
               "build_infer_tab.<locals>.on_single"):
        cid, limit = _cfg(server, qn)
        assert cid == "gpu", qn
        assert limit == 1, qn


def test_queue_has_max_size(server):
    assert server.blocks._queue.max_size and server.blocks._queue.max_size <= 256
