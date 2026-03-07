from goblin.utils.click_helpers import JSON
import os
import click
from typing import Any
from dotenv import load_dotenv
import asyncio
from goblin.runner import WorkflowRunner


@click.command()
@click.argument("blueprint_path", type=str)
@click.option(
    "--context",
    type=JSON,
    required=False,
    default="{}",
    help="the context that will be passed to the workflow. Must be a valid, unescaped JSON string",
)
@click.option(
    "--log-config",
    type=click.Path(exists=True),
    default=None,
    help="Path to a YAML logging configuration file",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    default=None,
    help="Override log level",
)
@click.option(
    "--log-file",
    type=click.Path(),
    default=None,
    help="Override log file path (default: goblin-run.jsonl)",
)
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress stderr handler",
)
def run(
    blueprint_path: str, context: Any, log_config: str | None, log_level: str | None, log_file: str | None, quiet: bool
):
    load_dotenv()

    from goblin.logging import configure_logging

    configure_logging(
        config_path=log_config or os.environ.get("GOBLIN_LOG_CONFIG"),
        log_level=log_level,
        log_file=log_file,
        quiet=quiet,
    )

    runner = WorkflowRunner(blueprint_path)
    asyncio.run(runner.run(context))


if __name__ == "__main__":
    run()
