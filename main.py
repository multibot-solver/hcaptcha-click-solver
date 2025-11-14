import asyncio
import time

from patchright.async_api import async_playwright

from core.logger import log
from core.solver import HCaptchaSolver


APIKEY = "Your MultiBot Key" # Your MultiBot Key
ATTEMPT = 10 # Number of attempts
INTERCEPT_TOKEN = False # True - Intercept the token


async def main() -> None:
    async with async_playwright() as patchright:
        browser = await patchright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        #-------------------
        #
        # Your code is here
        #
        #-------------------

        join_url = "https://store.steampowered.com/join/"
        await page.goto(join_url, wait_until="domcontentloaded")

        solver = HCaptchaSolver(
            page,
            APIKEY,
            attempt=ATTEMPT,
            intercept_token=INTERCEPT_TOKEN,
        )
        
        start_time = time.time()
        token = await solver.solve()
        end_time = time.time()

        if token:
            log.captcha(f"Captcha solved. Token: {token[:35]}", start_time, end_time)
        else:
            log.failure("Couldn't get hCaptcha token", start_time, end_time)

        #-------------------
        #
        # Your code is here
        #
        #-------------------
        
        await browser.close()
        input("Press Enter to exit...")


if __name__ == "__main__":
    asyncio.run(main())


