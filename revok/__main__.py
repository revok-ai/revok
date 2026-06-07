# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""CLI entry point for the Revok proxy service."""

import argparse
import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list; defaults to sys.argv[1:] when None.

    Returns:
        Parsed namespace with ``config`` path attribute.
    """
    parser = argparse.ArgumentParser(
        prog="revok",
        description="Revok — transparent async HTTP proxy for AI-agent memory enrichment.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        required=True,
        help="Path to the YAML configuration file.",
    )
    return parser.parse_args(argv)


async def _run(config_path: str) -> None:
    """Load config, build the aiohttp app, and run until interrupted.

    Args:
        config_path: Filesystem path to the YAML config file.
    """
    # Imports deferred here so Phase-1 checkpoint works before
    # downstream modules are fully implemented.
    from revok.config import ConfigError, load_config  # noqa: PLC0415
    from revok.entity_matcher import EntityMatcher  # noqa: PLC0415
    from revok.proxy import build_app  # noqa: PLC0415
    from revok.scoring import ScoringEngine  # noqa: PLC0415
    from revok.state_store import SqliteStateStore  # noqa: PLC0415

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    logging.basicConfig(level=config.logging.level, format=config.logging.format)

    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store, scorer=scorer)
    await store.open()

    app = build_app(config, store, matcher, scorer)

    from aiohttp import web  # noqa: PLC0415

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.server.host, config.server.port)

    try:
        await asyncio.wait_for(
            site.start(),
            timeout=config.server.startup_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.critical(
            "Revok failed to bind to %s:%d within %ss (startup_timeout_seconds).",
            config.server.host,
            config.server.port,
            config.server.startup_timeout_seconds,
        )
        await runner.cleanup()
        await store.close()
        sys.exit(1)

    logger.info(
        "Revok listening on http://%s:%d", config.server.host, config.server.port
    )

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("Shutting down Revok…")
        await runner.cleanup()
        await store.close()


def main(argv: list[str] | None = None) -> None:
    """Main entry point invoked by ``python -m revok`` and the ``revok`` script.

    Args:
        argv: Optional argument list for testing; defaults to sys.argv.
    """
    args = _parse_args(argv)
    asyncio.run(_run(args.config))


if __name__ == "__main__":
    main()
