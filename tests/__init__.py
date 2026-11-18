import logging

# IsolatedAsyncioTestCase runs the loop in debug mode, which logs every Playwright callback slower than 0.1 s.
logging.getLogger("asyncio").setLevel(logging.ERROR)
