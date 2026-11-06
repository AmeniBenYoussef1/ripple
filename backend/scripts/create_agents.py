"""Create Ripple's 4 Cominty agents and upload the shared skill.

Run once:  cd backend && .venv/Scripts/python scripts/create_agents.py
Then paste the printed lines into backend/.env.

Checked against the installed cominty-sdk 1.2.0 (agents.create, skills.create, capability classes).
"""
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from cominty_sdk import AgentCapabilities, AsyncCominty, SkillsCapability, WebCapability

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "backend" / ".env"
SKILL_DIR = ROOT / "skills" / "ripple-contracts"

COMMON = (
    "You are part of Ripple, an emergency co-pilot for event organizers. "
    "Always follow the ripple-contracts skill: reply with one JSON object only, never invent facts, "
    "and ask clarifying questions instead of guessing."
)

AGENTS = {
    "COMINTY_AGENT_IMPACT": dict(
        name="Ripple - Impact Analyst",
        description="Builds the event model from files and turns organizer messages into disruptions.",
        instructions=COMMON + (
            "\n\nYour job: (1) at setup, read the attached event files and return an `event_model`; "
            "(2) when the organizer reports a problem, return a `disruption` that points to a node id "
            "of the event model given in the message. Use only facts from the files and the message."
        ),
        web="never",
    ),
    "COMINTY_AGENT_FINDER": dict(
        name="Ripple - Resource Finder",
        description="Finds sourced alternatives for a lost or too-small resource.",
        instructions=COMMON + (
            "\n\nYour job: given a lost or insufficient resource and its requirements, search the web and "
            "return 3 to 5 `candidates`, each with a source url. Availability is always 'to confirm'."
        ),
        web="always",
    ),
    "COMINTY_AGENT_REPLANNER": dict(
        name="Ripple - Replanner",
        description="Turns affected schedule items and candidates into 2-3 concrete scenarios.",
        instructions=COMMON + (
            "\n\nYour job: given the affected schedule items and commitments (and candidates if any), "
            "return 2 or 3 `scenarios` as concrete changes with per-group burden estimates. "
            "Do not rank them; Ripple's code scores them."
        ),
        web="never",
    ),
    "COMINTY_AGENT_COORDINATOR": dict(
        name="Ripple - Coordinator",
        description="Prepares one message per affected group, the revised schedule and tasks.",
        instructions=COMMON + (
            "\n\nYour job: given the chosen scenario and the affected groups, return `messages` (one per "
            "group, status 'draft'), `revised_schedule` and `volunteer_tasks`. Use your sandbox to create "
            "revised_schedule.ics and attendee_update.csv and return them as files."
        ),
        web="never",
    ),
}


async def main() -> None:
    load_dotenv(ENV_FILE)  # explicit path, so it works from any working directory
    async with AsyncCominty() as client:
        skill = await client.skills.create(SKILL_DIR, display_name="Ripple contracts")
        print(f"# skill id: {skill.id}")
        for env_name, spec in AGENTS.items():
            agent = await client.agents.create(
                name=spec["name"],
                description=spec["description"],
                instructions=spec["instructions"],
                scope="private",
                capabilities=AgentCapabilities(
                    web=WebCapability(activation=spec["web"]),
                    skills=SkillsCapability(activation="always", include=[skill.id]),
                ),
            )
            print(f"{env_name}={agent.id}")


if __name__ == "__main__":
    asyncio.run(main())
