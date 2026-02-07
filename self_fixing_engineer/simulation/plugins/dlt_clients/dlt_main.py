# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import contextlib
import json
import logging
import sys
import uuid

import click

from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_base import (
    PRODUCTION_MODE,
    DLTClientConfigurationError,
    DLTClientError,
    DLTClientLoggerAdapter,
    _base_logger,
    alert_operator,
    scrub_secrets,
)
from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_factory import DLTFactory

# Use a logger adapter so formatter's client_type is always present
CLI_LOGGER = DLTClientLoggerAdapter(_base_logger, {"client_type": "CLI"})


def _run_async(coro):
    """
    Run an async coroutine from a sync Click command.
    Handles Windows event loop policy for compatibility.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


def _load_json_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@click.group()
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose (DEBUG) logging for the CLI session.",
)
def cli(verbose: bool):
    """DLT Clients Package CLI."""
    if verbose:
        _base_logger.setLevel(logging.DEBUG)  # Use logging.DEBUG instead of "DEBUG"
        CLI_LOGGER.debug("Verbose logging enabled.", extra={"client_type": "CLI"})


@cli.command("health-check")
@click.option(
    "--dlt-type",
    required=True,
    type=click.Choice(DLTFactory.list_available_dlt_clients()),
)
@click.option(
    "--config-file", required=True, type=click.Path(exists=True, dir_okay=False)
)
@click.option(
    "--correlation-id",
    required=False,
    default=None,
    help="Optional correlation ID for tracing; auto-generated if omitted.",
)
def health_check_command(dlt_type, config_file, correlation_id):
    """
    Performs a health check on a specified DLT client.
    """
    correlation_id = correlation_id or str(uuid.uuid4())
    CLI_LOGGER.info(
        f"Starting health-check for DLT type={dlt_type}",
        extra={
            "client_type": "CLI",
            "correlation_id": correlation_id,
            "config_file": config_file,
        },
    )

    async def _impl():
        dlt_client = None
        try:
            config = _load_json_file(config_file)

            if PRODUCTION_MODE:
                # Place for extra production config validation if needed.
                CLI_LOGGER.debug(
                    "Production mode: additional config validations can run here.",
                    extra={"correlation_id": correlation_id},
                )

            dlt_client = await DLTFactory.get_dlt_client(
                dlt_type, config, correlation_id=correlation_id
            )
            result = await dlt_client.health_check(correlation_id=correlation_id)

            if result.get("status"):
                click.echo(f"Health Check SUCCESS for {dlt_type} client.")
                click.echo(json.dumps(result, indent=2))
                return 0
            else:
                click.echo(f"Health Check FAILED for {dlt_type} client.")
                click.echo(json.dumps(result, indent=2))
                return 1

        except DLTClientConfigurationError as e:
            CLI_LOGGER.critical(
                f"CLI Configuration Error: {e}",
                extra={"correlation_id": correlation_id},
            )
            await alert_operator(
                f"CRITICAL: CLI Configuration Error for DLT client '{dlt_type}': {e}",
                level="CRITICAL",
            )
            click.echo(f"Error: {e}")
            return 1
        except DLTClientError as e:
            CLI_LOGGER.error(
                f"DLT Client Error: {e}", extra={"correlation_id": correlation_id}
            )
            click.echo(f"DLT Client Error: {e}")
            return 1
        except FileNotFoundError:
            CLI_LOGGER.critical(
                f"Configuration file not found at: {config_file}",
                extra={"correlation_id": correlation_id},
            )
            click.echo(f"Error: Configuration file not found at {config_file}")
            return 1
        except json.JSONDecodeError:
            CLI_LOGGER.critical(
                f"Invalid JSON in configuration file: {config_file}",
                extra={"correlation_id": correlation_id},
            )
            click.echo(f"Error: Invalid JSON in configuration file at {config_file}")
            return 1
        except Exception as e:
            CLI_LOGGER.critical(
                f"Unexpected error during health-check: {e}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            click.echo(f"An unexpected error occurred: {e}")
            return 1
        finally:
            if dlt_client is not None:
                with contextlib.suppress(Exception):
                    await dlt_client.close()

    exit_code = _run_async(_impl())
    if exit_code != 0:
        sys.exit(exit_code)


@cli.command("write-checkpoint")
@click.option(
    "--dlt-type",
    required=True,
    type=click.Choice(DLTFactory.list_available_dlt_clients()),
)
@click.option(
    "--config-file", required=True, type=click.Path(exists=True, dir_okay=False)
)
@click.option("--checkpoint-name", required=True, help="Name of the checkpoint chain.")
@click.option(
    "--hash", "hash_val", required=True, help="Cryptographic hash of the state."
)
@click.option(
    "--prev-hash",
    required=False,
    default="",
    help="Cryptographic hash of the previous state.",
)
@click.option(
    "--metadata",
    required=False,
    default="{}",
    help="On-chain metadata as a JSON string.",
)
@click.option(
    "--payload-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the file containing the off-chain payload blob.",
)
@click.option(
    "--correlation-id",
    required=False,
    default=None,
    help="Optional correlation ID for tracing; auto-generated if omitted.",
)
def write_checkpoint_command(
    dlt_type,
    config_file,
    checkpoint_name,
    hash_val,
    prev_hash,
    metadata,
    payload_file,
    correlation_id,
):
    """
    Writes a checkpoint to a specified DLT ledger.
    """
    correlation_id = correlation_id or str(uuid.uuid4())
    CLI_LOGGER.info(
        f"Starting write-checkpoint for DLT type={dlt_type}",
        extra={
            "client_type": "CLI",
            "correlation_id": correlation_id,
            "config_file": config_file,
            "payload_file": payload_file,
        },
    )

    async def _impl():
        dlt_client = None
        try:
            # Parse metadata JSON first to catch errors early
            try:
                metadata_dict = json.loads(metadata)
            except json.JSONDecodeError:
                click.echo("Error: Invalid JSON format for metadata.")
                return 1

            config = _load_json_file(config_file)
            with open(payload_file, "rb") as f:
                payload_blob = f.read()

            dlt_client = await DLTFactory.get_dlt_client(
                dlt_type, config, correlation_id=correlation_id
            )

            tx_id, off_chain_id, version = await dlt_client.write_checkpoint(
                checkpoint_name=checkpoint_name,
                hash=hash_val,
                prev_hash=prev_hash,
                metadata=metadata_dict,
                payload_blob=payload_blob,
                correlation_id=correlation_id,
            )

            click.echo(f"Write Checkpoint SUCCESS for {dlt_type} client.")
            click.echo(f"Transaction ID: {tx_id}")
            click.echo(f"Off-chain ID: {off_chain_id}")
            click.echo(f"Version: {version}")
            return 0

        except FileNotFoundError as e:
            click.echo(f"Error: Required file not found: {e}")
            return 1
        except DLTClientError as e:
            CLI_LOGGER.error(
                f"DLT Client Error during write-checkpoint: {e}",
                extra={"correlation_id": correlation_id},
            )
            click.echo(f"DLT Client Error: {e}")
            return 1
        except Exception as e:
            CLI_LOGGER.critical(
                f"Unexpected error during write-checkpoint: {e}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            click.echo(f"An unexpected error occurred: {e}")
            return 1
        finally:
            if dlt_client is not None:
                with contextlib.suppress(Exception):
                    await dlt_client.close()

    exit_code = _run_async(_impl())
    if exit_code != 0:
        sys.exit(exit_code)


@cli.command("read-checkpoint")
@click.option(
    "--dlt-type",
    required=True,
    type=click.Choice(DLTFactory.list_available_dlt_clients()),
)
@click.option(
    "--config-file", required=True, type=click.Path(exists=True, dir_okay=False)
)
@click.option("--checkpoint-name", required=True, help="Name of the checkpoint chain.")
@click.option(
    "--version",
    required=False,
    default="latest",
    help="Version of the checkpoint to read, or 'latest'.",
)
@click.option(
    "--output-file",
    required=False,
    type=click.Path(dir_okay=False),
    help="File to save the retrieved payload to.",
)
@click.option(
    "--correlation-id",
    required=False,
    default=None,
    help="Optional correlation ID for tracing; auto-generated if omitted.",
)
def read_checkpoint_command(
    dlt_type, config_file, checkpoint_name, version, output_file, correlation_id
):
    """
    Reads a checkpoint from a specified DLT ledger.
    """
    correlation_id = correlation_id or str(uuid.uuid4())
    CLI_LOGGER.info(
        f"Starting read-checkpoint for DLT type={dlt_type}",
        extra={
            "client_type": "CLI",
            "correlation_id": correlation_id,
            "config_file": config_file,
        },
    )

    async def _impl():
        dlt_client = None
        try:
            config = _load_json_file(config_file)
            dlt_client = await DLTFactory.get_dlt_client(
                dlt_type, config, correlation_id=correlation_id
            )

            result = await dlt_client.read_checkpoint(
                name=checkpoint_name,
                version=version,
                correlation_id=correlation_id,
            )

            click.echo(f"Read Checkpoint SUCCESS for {dlt_type} client.")
            click.echo("Metadata:")
            # Handle the metadata properly - ensure it's JSON serializable
            metadata = result.get("metadata", {})
            if isinstance(metadata, dict):
                # Call scrub_secrets properly or just dump the metadata directly
                try:
                    scrubbed = scrub_secrets(metadata)
                    click.echo(json.dumps(scrubbed, indent=2))
                except (TypeError, AttributeError):
                    # If scrub_secrets fails, just dump the metadata as-is
                    click.echo(json.dumps(metadata, indent=2))
            else:
                click.echo(json.dumps({"metadata": str(metadata)}, indent=2))

            if output_file:
                with open(output_file, "wb") as f:
                    f.write(result["payload_blob"])
                click.echo(f"Payload saved to: {output_file}")
            return 0

        except DLTClientError as e:
            CLI_LOGGER.error(
                f"DLT Client Error during read-checkpoint: {e}",
                extra={"correlation_id": correlation_id},
            )
            click.echo(f"DLT Client Error: {e}")
            return 1
        except Exception as e:
            CLI_LOGGER.critical(
                f"Unexpected error during read-checkpoint: {e}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            click.echo(f"An unexpected error occurred: {e}")
            return 1
        finally:
            if dlt_client is not None:
                with contextlib.suppress(Exception):
                    await dlt_client.close()

    exit_code = _run_async(_impl())
    if exit_code != 0:
        sys.exit(exit_code)


@cli.command("rollback-checkpoint")
@click.option(
    "--dlt-type",
    required=True,
    type=click.Choice(DLTFactory.list_available_dlt_clients()),
)
@click.option(
    "--config-file", required=True, type=click.Path(exists=True, dir_okay=False)
)
@click.option("--checkpoint-name", required=True, help="Name of the checkpoint chain.")
@click.option(
    "--rollback-hash", required=True, help="Hash of the state to roll back to."
)
@click.option(
    "--correlation-id",
    required=False,
    default=None,
    help="Optional correlation ID for tracing; auto-generated if omitted.",
)
def rollback_checkpoint_command(
    dlt_type, config_file, checkpoint_name, rollback_hash, correlation_id
):
    """
    Rolls back a checkpoint on a specified DLT ledger.
    """
    correlation_id = correlation_id or str(uuid.uuid4())
    CLI_LOGGER.info(
        f"Starting rollback-checkpoint for DLT type={dlt_type}",
        extra={
            "client_type": "CLI",
            "correlation_id": correlation_id,
            "config_file": config_file,
        },
    )

    async def _impl():
        dlt_client = None
        try:
            config = _load_json_file(config_file)
            dlt_client = await DLTFactory.get_dlt_client(
                dlt_type, config, correlation_id=correlation_id
            )

            result = await dlt_client.rollback_checkpoint(
                name=checkpoint_name,
                rollback_hash=rollback_hash,
                correlation_id=correlation_id,
            )

            click.echo(f"Rollback Checkpoint SUCCESS for {dlt_type} client.")
            click.echo("Result:")
            # Handle the result properly - ensure it's JSON serializable
            try:
                scrubbed = scrub_secrets(result)
                click.echo(json.dumps(scrubbed, indent=2))
            except (TypeError, AttributeError):
                # If scrub_secrets fails, just dump the result as-is
                click.echo(json.dumps(result, indent=2))
            return 0

        except DLTClientError as e:
            CLI_LOGGER.error(
                f"DLT Client Error during rollback-checkpoint: {e}",
                extra={"correlation_id": correlation_id},
            )
            click.echo(f"DLT Client Error: {e}")
            return 1
        except Exception as e:
            CLI_LOGGER.critical(
                f"Unexpected error during rollback-checkpoint: {e}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            click.echo(f"An unexpected error occurred: {e}")
            return 1
        finally:
            if dlt_client is not None:
                with contextlib.suppress(Exception):
                    await dlt_client.close()

    exit_code = _run_async(_impl())
    if exit_code != 0:
        sys.exit(exit_code)


def main():
    """
    Main entry point for the CLI.
    Uses Click sync commands; async work is run via asyncio.run within each command.
    """
    cli()


if __name__ == "__main__":
    main()
