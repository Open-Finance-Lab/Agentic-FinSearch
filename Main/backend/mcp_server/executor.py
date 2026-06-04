"""Async executor for running blocking operations in thread pool."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, TypeVar


T = TypeVar('T')

# Global thread pool executor (singleton)
_executor: ThreadPoolExecutor | None = None


def get_executor() -> ThreadPoolExecutor:
    """Get or create the global thread pool executor.

    Returns:
        ThreadPoolExecutor instance (singleton)
    """
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yfinance")
    return _executor


async def run_in_executor(func: Callable[..., T], *args: Any) -> T:
    """Run blocking function in thread pool executor.

    Args:
        func: The blocking function to execute
        *args: Arguments to pass to the function

    Returns:
        The result from the function
    """
    loop = asyncio.get_running_loop()
    executor = get_executor()
    return await loop.run_in_executor(executor, partial(func, *args))
