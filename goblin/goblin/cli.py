from __future__ import annotations

import sys
from typing import Any

import click

from goblin.utils.click_helpers import JSON
from goblin.utils.config import get_config, load_dotenv_from_cwd
from goblin_common.logging import configure_logging


@click.group()
def cli():
    """GOBLIN — autonomous agent harness."""
    pass


@cli.command()
@click.argument("blueprint_path", type=str, required=False, default=None)
@click.option(
    "--context",
    type=JSON,
    required=False,
    default="{}",
    help="the context that will be passed to the blueprint. Must be a valid, unescaped JSON string",
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
@click.option(
    "--resume",
    type=click.Path(exists=True),
    default=None,
    help="Path to a .goblin/run-<id>.json checkpoint file to resume",
)
@click.option(
    "--run-dir",
    type=click.Path(),
    default=None,
    help="Directory for .goblin/ checkpoint files (default: current directory)",
)
def run(
    blueprint_path: str | None,
    context: Any,
    log_config: str | None,
    log_level: str | None,
    log_file: str | None,
    quiet: bool,
    resume: str | None,
    run_dir: str | None,
):
    """Execute a Blueprint."""
    from goblin.runner import BlueprintRunner

    load_dotenv_from_cwd()

    configure_logging(
        logger_name="goblin",
        config_path=log_config or get_config().log_config,
        log_level=log_level,
        log_file=log_file,
        quiet=quiet,
    )

    if resume and blueprint_path:
        raise click.UsageError("Cannot specify both blueprint_path and --resume")
    if not resume and not blueprint_path:
        raise click.UsageError("Must specify either blueprint_path or --resume")

    import asyncio

    if resume:
        asyncio.run(BlueprintRunner.resume(resume))
    else:
        assert blueprint_path is not None
        runner = BlueprintRunner(blueprint_path, run_dir=run_dir)
        asyncio.run(runner.run(context))


@cli.command()
@click.argument("blueprints", nargs=-1, required=True, type=click.Path(exists=True))
def validate(blueprints: tuple[str, ...]):
    """Validate one or more blueprint YAML files against the JSON Schema."""
    from goblin.validation import format_errors, validate_blueprint

    all_valid = True

    for path in blueprints:
        errors = validate_blueprint(path)
        if errors:
            has_error = any(e.get("severity", "error") == "error" for e in errors)
            if has_error:
                all_valid = False
                click.echo(f"{path}: invalid")
            else:
                click.echo(f"{path}: valid (with warnings)")
            click.echo(format_errors(errors))
        else:
            click.echo(f"{path}: valid")

    if not all_valid:
        sys.exit(1)


if __name__ == "__main__":
    cli()
