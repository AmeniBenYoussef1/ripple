import asyncio
from dotenv import load_dotenv
from cominty_sdk import AsyncCominty

load_dotenv()

async def main():
    async with AsyncCominty() as client:
        run = await client.chat.start(
            agent_id="__cominty_agents::agent.chat",
            message="What is Cominty?",
        )
        print(await run.text())

asyncio.run(main())