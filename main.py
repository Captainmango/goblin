from dotenv import load_dotenv
import asyncio
from goblin.runner import WorkflowRunner


async def main():
    load_dotenv()
    runner = WorkflowRunner("workflows/test.yaml")
    await runner.run(context={"epic": "PROJ-123"})


asyncio.run(main())