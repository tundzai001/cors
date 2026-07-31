from __future__ import annotations

import logging
import queue
import unittest

from app.nonblocking_logging import DroppingQueueHandler


class NonBlockingLoggingTests(unittest.TestCase):
    def test_full_queue_drops_without_raising(self):
        log_queue = queue.Queue(maxsize=1)
        handler = DroppingQueueHandler(log_queue)
        first = logging.LogRecord("test", logging.INFO, __file__, 1, "one", (), None)
        second = logging.LogRecord("test", logging.INFO, __file__, 2, "two", (), None)

        handler.enqueue(first)
        handler.enqueue(second)

        self.assertEqual(log_queue.qsize(), 1)
        self.assertEqual(handler.dropped_count, 1)


if __name__ == "__main__":
    unittest.main()
