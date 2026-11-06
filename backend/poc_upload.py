"""Proof of concept: upload one file, ask a built-in Cominty agent for 3 facts as JSON.

Run from backend/:  .venv/Scripts/python poc_upload.py
"""
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from cominty_sdk import AsyncCominty, ComintyError

load_dotenv(Path(__file__).parent / ".env")  # the SDK reads COMINTY_API_KEY / COMINTY_USER_ID from env

CONTRACT = Path(__file__).parent / "demo_data" / "catering_contract.txt"
AGENT_ID = "__cominty_agents::agent.chat"

PROMPT = (
    "Read the attached catering contract. Reply with ONE JSON object and nothing else, "
    'with exactly these keys: "delivery_time", "delivery_address", "quantity" '
    "(quantity as an integer). Use only values that appear in the file."
)


async def main() -> None:
    async with AsyncCominty() as client:
        up = await client.chat.upload_file(CONTRACT)
        print(f"Uploaded {up.name} -> id={up.id}")

        run = await client.chat.start(
            agent_id=AGENT_ID,
            message=PROMPT,
            name="PoC: catering contract",
            file_ids=[up.id],
            max_steps=4,
        )
        print(f"Thread: {run.thread.id}\n--- events ---")

        async for ev in run:
            data = getattr(ev, "data", None)
            content = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
            print(f"{type(ev).__name__} [{ev.status}]: {content}")

        reply = await run.result()
        print("--- final reply ---")
        print(reply.content)
        print("--- reply files ---")
        print([f.model_dump(mode="json") for f in reply.files] or "(none)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ComintyError as e:
        # print only the error type and message, never the request headers or key
        print(f"Cominty error: {type(e).__name__}: {e}")
