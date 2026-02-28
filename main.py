from goblin.utils.click_helpers import JSON
import click
from typing import Any
from dotenv import load_dotenv
import asyncio
from goblin.runner import WorkflowRunner


@click.command()
@click.argument('blueprint_path', type=str)
@click.option('--context', type=JSON, required=False, default="{}", help="the context that will be passed to the workflow. Must be a valid, unescaped JSON string")
def run(blueprint_path: str, context: Any):
    load_dotenv()
    runner = WorkflowRunner(blueprint_path)
    asyncio.run(runner.run(context))


if __name__ == "__main__":
    run()