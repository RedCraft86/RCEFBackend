"""Bounded process workers; all publication stays in the caller."""
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor


def default_jobs():
    return min(4, os.cpu_count() or 1)


def map_files(worker, paths, *, jobs, initializer=None, initargs=()):
    """Yield in source order with at most two tasks per worker outstanding."""
    if type(jobs) is not int or jobs < 1:
        raise ValueError('jobs must be a positive integer')
    if jobs == 1:
        if initializer:
            initializer(*initargs)
        for path in paths:
            yield worker(path)
        return
    # Improve: spawn avoids inheriting open images or writer state on Windows.
    with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context('spawn'),
                             initializer=initializer, initargs=initargs) as pool:
        iterator = iter(paths)
        pending = []
        for _ in range(2 * jobs):
            path = next(iterator, None)
            if path is None:
                break
            pending.append(pool.submit(worker, path))
        while pending:
            future = pending.pop(0)
            yield future.result()
            path = next(iterator, None)
            if path is not None:
                pending.append(pool.submit(worker, path))
