"""Command-line entry point.

    morx run       collect continuously (the service)
    morx sync      pull the stored backlog once, then exit
    morx info      probe each device and print what it reports
    morx config    show resolved settings (verify a new deployment)
    morx web       serve the read-only dashboard over what has been collected
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from types import FrameType

from . import __version__, logging_setup
from .collector import CollectorService, DeviceCollector
from .config import ConfigError, Settings
from .device import Device, DeviceError
from .storage import open_storage

logger = logging.getLogger("morx.cli")

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_DEVICE_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="morx",
        description="Attendance collector for ZK-protocol biometric devices.",
    )
    parser.add_argument("--version", action="version", version=f"morx-collector {__version__}")
    parser.add_argument("--log-level", help="override MORX_LOG_LEVEL (DEBUG, INFO, ...)")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="collect continuously until stopped")
    sub.add_parser("sync", help="pull stored backlog once and exit")
    sub.add_parser("info", help="probe devices and print what they report")
    sub.add_parser("config", help="print resolved configuration and exit")

    web = sub.add_parser("web", help="serve the read-only attendance dashboard")
    # Loopback by default: the dashboard has no authentication, so exposing it
    # on the LAN has to be a deliberate --host 0.0.0.0.
    web.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    web.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")
    web.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = Settings.load()
    except ConfigError as exc:
        # Config errors happen before logging is up; stderr is the right channel.
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    logging_setup.configure(
        level=args.log_level.upper() if args.log_level else settings.log_level,
        fmt=settings.log_format,
        log_file=settings.log_file,
    )

    if args.command == "web":
        return cmd_web(settings, args)

    handlers = {"run": cmd_run, "sync": cmd_sync, "info": cmd_info, "config": cmd_config}
    return handlers[args.command](settings)


# -- commands ---------------------------------------------------------------


def cmd_config(settings: Settings) -> int:
    print(f"storage backend : {settings.storage_backend}")
    print(f"data dir        : {settings.data_dir.resolve()}")
    if settings.storage_backend == "sqlite":
        print(f"sqlite path     : {settings.sqlite_path.resolve()}")
    print(f"sync history    : {settings.sync_history}")
    print(f"log level       : {settings.log_level} ({settings.log_format})")
    print(f"devices         : {len(settings.devices)}")
    for device in settings.devices:
        print(
            f"  - {device.label:<20} {device.host}:{device.port} "
            f"timeout={device.timeout}s udp={device.force_udp} "
            f"ping={'off' if device.omit_ping else 'on'} enc={device.encoding}"
        )
    return EXIT_OK


def cmd_info(settings: Settings) -> int:
    failures = 0
    for config in settings.devices:
        # flush: logs go to stderr, these go to stdout — without it the two
        # streams interleave out of order in a terminal.
        print(f"\n=== {config.label} ({config.host}:{config.port}) ===", flush=True)
        try:
            with Device(config).connect() as device:
                print(f"identity   : {device.describe()}")
                employees = device.fetch_employees()
                print(f"employees  : {len(employees)}")
                records = device.fetch_attendance({})
                print(f"attendance : {len(records)} stored")
                if records:
                    print(f"oldest     : {min(r.timestamp for r in records)}")
                    print(f"newest     : {max(r.timestamp for r in records)}")
        except DeviceError as exc:
            failures += 1
            print(f"FAILED     : {exc}")
    return EXIT_DEVICE_ERROR if failures else EXIT_OK


def cmd_sync(settings: Settings) -> int:
    failures = 0
    with open_storage(settings) as storage:
        for config in settings.devices:
            try:
                with Device(config).connect() as device:
                    employees = device.fetch_employees()
                    storage.save_employees(employees)
                    names = {e.employee_id: e.name for e in employees}

                    records = device.fetch_attendance(names)
                    new = storage.save_many(records)
                    logger.info(
                        "%s: %d employees, %d records on device, %d new",
                        config.label,
                        len(employees),
                        len(records),
                        new,
                    )
            except DeviceError as exc:
                failures += 1
                logger.error("%s", exc)
    return EXIT_DEVICE_ERROR if failures else EXIT_OK


def cmd_run(settings: Settings) -> int:
    logger.info("morx-collector %s starting", __version__)

    with open_storage(settings) as storage:
        service = CollectorService(settings, storage)
        _install_signal_handlers(service)
        service.start()
        service.wait()
        service.shutdown()

    logger.info("Stopped")
    return EXIT_OK


def cmd_web(settings: Settings, args: argparse.Namespace) -> int:
    # Imported here rather than at module scope so the collector commands never
    # pay for the dashboard, and a broken dashboard can't stop `morx run`.
    from .web import serve

    return serve(settings, host=args.host, port=args.port, open_browser=args.open)


def _install_signal_handlers(service: CollectorService) -> None:
    """Turn SIGINT/SIGTERM into a cooperative stop.

    Registered on the main thread only — signal delivery in Python always lands
    there, which is why the workers watch an Event instead of handling signals.
    """

    def handle(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received %s, stopping", signal.Signals(signum).name)
        for collector in service.collectors:
            collector.request_stop()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)


__all__ = ["main", "DeviceCollector"]


if __name__ == "__main__":
    sys.exit(main())
