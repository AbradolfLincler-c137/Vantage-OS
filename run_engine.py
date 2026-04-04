import sys
sys.stdout.reconfigure(encoding='utf-8')

import asyncio
from core.vantage_engine import VantageEngine

async def main():
    engine = VantageEngine()
    await engine.setup_browser(start_url="about:blank")
    
    try:
        await engine.run_loop()
    except KeyboardInterrupt:
        print("\nManual override (Panic Key) triggered.")
    finally:
        await engine.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
