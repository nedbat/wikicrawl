"""Helpers for multi-threading, etc."""

import concurrent.futures
import contextlib

import requests
import tqdm


@contextlib.contextmanager
def report_http_errors():
    """Wrap around api calls, so that HTTP errors will be reported usefully."""
    try:
        yield
    except requests.exceptions.HTTPError as err:
        resp = err.response
        tqdm.tqdm.write(f"Request for {resp.url!r} failed: status {resp.status_code}")
        raise


def work_in_threads(seq, fn, max_workers=10):
    """Distribute work to threads.

    `seq` is a sequence (probably list) of items.
    `fn` is a function that will be called on each item, on worker threads.
    `max_workers` is the maximum number of worker threads.

    This function will yield pairs of (item, fn(item)) as the work is completed.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {executor.submit(fn, item): item for item in seq}
        for future in concurrent.futures.as_completed(future_to_item):
            if future.exception() is not None:
                write_message(f"Exception in future: {future.exception()}")
                continue
            item = future_to_item[future]
            yield item, future.result()


def prog_bar(seq=None, desc="", **kwargs):
    return tqdm.tqdm(seq, desc=desc.ljust(35), leave=False, disable=None, **kwargs)

def write_message(text):
    """Write a message that won't interfere with progress bars."""
    tqdm.tqdm.write(text)
