from __future__ import annotations

import os
import re
import threading
from collections.abc import MutableSequence, Sequence
from typing import NoReturn

import click
import sentry_sdk

from sentry.runner.commands.devservices import get_docker_client
from sentry.runner.decorators import configuration, log_options

# NOTE: These do NOT start automatically. Add your daemon to the `daemons` list
# in `devserver()` like so:
#     daemons += [_get_daemon("my_new_daemon")]
#
# If you are looking to add a kafka consumer, please do not create a new click
# subcommand. Instead, use sentry.consumers.
_DEFAULT_DAEMONS = {
    "worker": ["sentry", "run", "worker", "-c", "1", "--autoreload"],
    "celery-beat": ["sentry", "run", "cron", "--autoreload"],
    "server": ["sentry", "run", "web"],
    "taskworker": ["sentry", "run", "taskworker"],
    "taskworker-scheduler": ["sentry", "run", "taskworker-scheduler"],
}

_SUBSCRIPTION_RESULTS_CONSUMERS = [
    "events-subscription-results",
    "transactions-subscription-results",
    "generic-metrics-subscription-results",
    "metrics-subscription-results",
    "eap-spans-subscription-results",
]


def add_daemon(name: str, command: list[str]) -> None:
    """
    Used by getsentry to add additional workers to the devserver setup.
    """
    if name in _DEFAULT_DAEMONS:
        raise KeyError(f"The {name} worker has already been defined")
    _DEFAULT_DAEMONS[name] = command


def _get_daemon(name: str) -> tuple[str, list[str]]:
    return name, _DEFAULT_DAEMONS[name]


@click.command()
@click.option(
    "--reload/--no-reload",
    default=True,
    help="Autoreloading of python files.",
)
@click.option(
    "--watchers/--no-watchers",
    default=True,
    help="Watch static files and recompile on changes.",
)
@click.option(
    "--workers/--no-workers",
    default=False,
    help="Run celery workers (excluding celerybeat).",
)
@click.option(
    "--celery-beat/--no-celery-beat",
    default=False,
    help="Run celerybeat workers.",
)
@click.option(
    "--ingest/--no-ingest",
    default=False,
    help="Run ingest services (including Relay).",
)
@click.option(
    "--occurrence-ingest/--no-occurrence-ingest",
    default=False,
    help="Run ingest services for occurrences.",
)
@click.option(
    "--prefix/--no-prefix",
    default=True,
    help="Show the service name prefix and timestamp",
)
@click.option(
    "--dev-consumer/--no-dev-consumer",
    default=False,
    help="Fold multiple kafka consumers into one process using 'sentry run dev-consumer'.",
)
@click.option(
    "--pretty/--no-pretty",
    default=False,
    help="Stylize various outputs from the devserver",
)
@click.option(
    "--environment",
    default="development",
    help="The environment name.",
)
@click.option(
    "--debug-server/--no-debug-server",
    default=False,
    required=False,
    help="Start web server in same process",
)
@click.option(
    "--experimental-spa/--no-experimental-spa",
    default=False,
    help="This enables running sentry with pure separation of the frontend and backend",
)
@click.option(
    "--client-hostname",
    default="localhost",
    help="The hostname that clients will use. Useful for ngrok workflows eg `--client-hostname=alice.ngrok.io`",
)
@click.option(
    "--ngrok",
    default=None,
    required=False,
    help=(
        "The hostname that you have ngrok forwarding to your devserver. "
        "This option will modify application settings to be compatible with ngrok forwarding. "
        "Expects a host name without protocol e.g `--ngrok=yourname.ngrok.app`. "
        "You will also need to run ngrok."
    ),
)
@click.option(
    "--silo",
    default=None,
    type=click.Choice(["control", "region"]),
    help="The silo mode to run this devserver instance in. Choices are control, region, none",
)
@click.option(
    "--taskworker/--no-taskworker",
    default=False,
    help="Run kafka-based task workers",
)
@click.option(
    "--taskworker-scheduler/--no-taskworker-scheduler",
    default=False,
    help="Run periodic task scheduler for taskworkers.",
)
@click.argument(
    "bind",
    default=None,
    metavar="ADDRESS",
    envvar="SENTRY_DEVSERVER_BIND",
    required=False,
)
@log_options()  # needs this decorator to be typed
@configuration  # needs this decorator to be typed
@click.pass_context
def devserver(
    ctx: click.Context,
    reload: bool,
    watchers: bool,
    workers: bool,
    celery_beat: bool,
    ingest: bool,
    occurrence_ingest: bool,
    experimental_spa: bool,
    prefix: bool,
    pretty: bool,
    environment: str,
    debug_server: bool,
    dev_consumer: bool,
    bind: str | None,
    client_hostname: str,
    ngrok: str | None,
    silo: str | None,
    taskworker: bool,
    taskworker_scheduler: bool,
) -> NoReturn:
    "Starts a lightweight web server for development."
    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DEVSERVICES_DSN", ""),
        traces_sample_rate=1.0,
    )
    with sentry_sdk.start_transaction(op="command", name="sentry.devserver"):
        passed_options = {
            p.name: ctx.params[p.name]
            for p in ctx.command.params
            if p.name is not None
            and ctx.get_parameter_source(p.name) == click.core.ParameterSource.COMMANDLINE
        }

        for option_name, option_value in passed_options.items():
            sentry_sdk.set_tag(f"devserver.{option_name}", option_value)

        if bind is None:
            bind = "127.0.0.1:8000"

        if ":" in bind:
            host, port_s = bind.split(":", 1)
            port = int(port_s)
        else:
            raise SystemExit(f"expected <host>:<port>, got {bind}")

        # In a siloed environment we can't use localhost because cookies
        # cannot be shared across subdomains of localhost
        if silo and client_hostname == "localhost":
            click.echo(
                "WARNING: You had a client_hostname of `localhost` but are using silo modes. "
                "Switching to dev.getsentry.net as the client hostname"
            )
            client_hostname = "dev.getsentry.net"
        # We run webpack on the control server, not the regions.
        if silo == "region" and watchers:
            click.echo("WARNING: You have silo=region and webpack enabled. Disabling webpack.")
            watchers = False

        os.environ["SENTRY_ENVIRONMENT"] = environment
        # NODE_ENV *must* use production for any prod-like environment as third party libraries look
        # for this magic constant
        os.environ["NODE_ENV"] = "production" if environment.startswith("prod") else environment

        # Configure URL prefixes for customer-domains.
        client_host = f"{client_hostname}:{port}"
        os.environ["SENTRY_SYSTEM_URL_PREFIX"] = f"http://{client_host}"
        os.environ["SENTRY_SYSTEM_BASE_HOSTNAME"] = client_host
        os.environ["SENTRY_ORGANIZATION_BASE_HOSTNAME"] = f"{{slug}}.{client_host}"
        os.environ["SENTRY_ORGANIZATION_URL_TEMPLATE"] = "http://{hostname}"
        if ngrok:
            os.environ["SENTRY_DEVSERVER_NGROK"] = ngrok

        from django.conf import settings

        from sentry.services.http import SentryHTTPServer

        uwsgi_overrides: dict[str, int | bool | str | None] = {
            "protocol": "http",
            "uwsgi-socket": None,
            "http-keepalive": True,
            # Make sure we reload really quickly for local dev in case it
            # doesn't want to shut down nicely on it's own, NO MERCY
            "worker-reload-mercy": 2,
            # We need stdin to support pdb in devserver
            "honour-stdin": True,
            # accept ridiculously large files
            "limit-post": 1 << 30,
            # do something with chunked
            "http-chunked-input": True,
            "thunder-lock": False,
            "timeout": 600,
            "harakiri": 600,
            "workers": 1 if debug_server else 2,
        }

        if reload:
            uwsgi_overrides["py-autoreload"] = 1

        daemons: MutableSequence[tuple[str, Sequence[str]]] = []
        kafka_consumers: set[str] = set()
        containers: set[str] = set()
        with get_docker_client() as docker:
            containers = {c.name for c in docker.containers.list(filters={"status": "running"})}

        if experimental_spa:
            os.environ["SENTRY_UI_DEV_ONLY"] = "1"
            if not watchers:
                click.secho(
                    "Using experimental SPA mode without watchers enabled has no effect",
                    err=True,
                    fg="yellow",
                )

        # Configure ports based on options and environment vars
        # If present, webpack is given the 'bind' address as it proxies
        # requests to the server instances.
        # When we're running multiple servers control + region servers are offset
        # from webpack and each other.
        ports = {
            "webpack": port,
            "server": port + 1,
            "region.server": port + 10,
        }
        if not watchers:
            ports["server"] = ports["webpack"]
            ports.pop("webpack")

        # Set ports to environment variables so that child processes can read them
        os.environ["SENTRY_BACKEND_PORT"] = str(ports.get("server"))
        if silo == "region":
            os.environ["SENTRY_BACKEND_PORT"] = str(ports.get("region.server"))

        server_port = os.environ["SENTRY_BACKEND_PORT"]

        # We proxy all requests through webpacks devserver on the configured port.
        # The backend is served on port+1 and is proxied via the webpack
        # configuration.
        if watchers:
            daemons += settings.SENTRY_WATCHERS
            os.environ["FORCE_WEBPACK_DEV_SERVER"] = "1"
            os.environ["SENTRY_WEBPACK_PROXY_HOST"] = str(host)
            os.environ["SENTRY_WEBPACK_PROXY_PORT"] = str(ports["webpack"])

            # webpack and/or typescript is causing memory issues
            os.environ["NODE_OPTIONS"] = (
                os.environ.get("NODE_OPTIONS", "") + " --max-old-space-size=4096"
            ).lstrip()

        os.environ["SENTRY_USE_RELAY"] = "1" if settings.SENTRY_USE_RELAY else ""

        if ingest and not workers:
            click.echo("--ingest was provided, implicitly enabling --workers")
            workers = True

        if taskworker:
            daemons.append(_get_daemon("taskworker"))

        if taskworker_scheduler:
            daemons.append(_get_daemon("taskworker-scheduler"))

        if workers and not celery_beat:
            click.secho(
                "If you want to run periodic tasks from celery (celerybeat), you need to also pass --celery-beat.",
                fg="yellow",
            )

        if celery_beat and silo != "control":
            daemons.append(_get_daemon("celery-beat"))

        if workers and silo != "control":
            kafka_consumers.update(settings.DEVSERVER_START_KAFKA_CONSUMERS)

            if settings.CELERY_ALWAYS_EAGER:
                raise click.ClickException(
                    "Disable CELERY_ALWAYS_EAGER in your settings file to spawn workers."
                )

            daemons.append(_get_daemon("worker"))

            from sentry import eventstream

            if eventstream.backend.requires_post_process_forwarder():
                kafka_consumers.add("post-process-forwarder-errors")
                kafka_consumers.add("post-process-forwarder-transactions")
                kafka_consumers.add("post-process-forwarder-issue-platform")

            daemons.extend([_get_daemon(name) for name in settings.SENTRY_EXTRA_WORKERS])

            if settings.SENTRY_DEV_PROCESS_SUBSCRIPTIONS:
                kafka_consumers.update(_SUBSCRIPTION_RESULTS_CONSUMERS)

            if settings.SENTRY_USE_METRICS_DEV and settings.SENTRY_USE_RELAY:
                kafka_consumers.add("ingest-metrics")
                kafka_consumers.add("ingest-generic-metrics")
                kafka_consumers.add("billing-metrics-consumer")

            if settings.SENTRY_USE_UPTIME:
                kafka_consumers.add("uptime-results")

            if settings.SENTRY_USE_RELAY:
                kafka_consumers.add("ingest-events")
                kafka_consumers.add("ingest-attachments")
                kafka_consumers.add("ingest-transactions")
                kafka_consumers.add("ingest-monitors")
                kafka_consumers.add("ingest-feedback-events")

                kafka_consumers.add("monitors-clock-tick")
                kafka_consumers.add("monitors-clock-tasks")
                kafka_consumers.add("monitors-incident-occurrences")

                if settings.SENTRY_USE_PROFILING:
                    kafka_consumers.add("ingest-profiles")

                if settings.SENTRY_USE_SPANS_BUFFER:
                    kafka_consumers.add("process-spans")
                    kafka_consumers.add("ingest-occurrences")
                    kafka_consumers.add("process-segments")

            if occurrence_ingest:
                kafka_consumers.add("ingest-occurrences")

        # Create all topics if the Kafka eventstream is selected
        if kafka_consumers:
            use_old_devservices = os.environ.get("USE_OLD_DEVSERVICES") == "1"
            valid_kafka_container_names = ["kafka-kafka-1", "sentry_kafka"]
            kafka_container_name = "sentry_kafka" if use_old_devservices else "kafka-kafka-1"
            kafka_container_warning_message = (
                f"""
    Devserver is configured to work with `sentry devservices`. Looks like the `{kafka_container_name}` container is not running.
    Please run `sentry devservices up kafka` to start it."""
                if use_old_devservices
                else f"""
    Devserver is configured to work with the revamped devservices. Looks like the `{kafka_container_name}` container is not running.
    Please run `devservices up` to start it."""
            )
            if not any(name in containers for name in valid_kafka_container_names):
                raise click.ClickException(
                    f"""
    Devserver is configured to start some kafka consumers, but Kafka
    don't seem to be running.

    The following consumers were intended to be started: {kafka_consumers}

    Make sure you have:

        SENTRY_USE_RELAY = True

    or:

        SENTRY_EVENTSTREAM = "sentry.eventstream.kafka.KafkaEventStream"

    {kafka_container_warning_message}

    Alternatively, run without --workers.
            """
                )

            from sentry_kafka_schemas import list_topics

            from sentry.utils.batching_kafka_consumer import create_topics
            from sentry.utils.kafka_config import get_topic_definition_from_name

            for topic in list_topics():
                topic_defn = get_topic_definition_from_name(topic)
                create_topics(topic_defn["cluster"], [topic_defn["real_topic_name"]])

            if dev_consumer:
                daemons.append(
                    ("dev-consumer", ["sentry", "run", "dev-consumer"] + list(kafka_consumers))
                )
            else:
                for name in kafka_consumers:
                    daemons.append(
                        (
                            name,
                            [
                                "sentry",
                                "run",
                                "consumer",
                                name,
                                "--consumer-group=sentry-consumer",
                                "--auto-offset-reset=latest",
                                "--no-strict-offset-reset",
                            ],
                        )
                    )

        # A better log-format for local dev when running through honcho,
        # but if there aren't any other daemons, we don't want to override.
        if daemons:
            uwsgi_overrides["log-format"] = "%(method) %(status) %(uri) %(proto) %(size)"
        else:
            uwsgi_overrides["log-format"] = "[%(ltime)] %(method) %(status) %(uri) %(proto) %(size)"

        # Prevent logging of requests to specified endpoints.
        #
        # TODO: According to the docs, the final `log-drain` value is evaluated as a regex (and indeed,
        # joining the options with `|` works), but no amount of escaping, not escaping, escaping the
        # escaping, using raw strings, or any combo thereof seems to actually work if you include a
        # regex pattern string in the list. Docs are here:
        # https://uwsgi-docs.readthedocs.io/en/latest/Options.html?highlight=log-format#log-drain
        if settings.DEVSERVER_REQUEST_LOG_EXCLUDES:
            filters = settings.DEVSERVER_REQUEST_LOG_EXCLUDES
            filter_pattern = "|".join(map(lambda s: re.escape(s), filters))
            uwsgi_overrides["log-drain"] = filter_pattern

        server_port = os.environ["SENTRY_BACKEND_PORT"]

        if silo == "region":
            os.environ["SENTRY_SILO_DEVSERVER"] = "1"
            os.environ["SENTRY_SILO_MODE"] = "REGION"
            os.environ["SENTRY_REGION"] = "us"
            os.environ["SENTRY_REGION_SILO_PORT"] = str(server_port)
            os.environ["SENTRY_CONTROL_SILO_PORT"] = str(ports["server"] + 1)
            os.environ["SENTRY_DEVSERVER_BIND"] = f"127.0.0.1:{server_port}"
            os.environ["UWSGI_HTTP_SOCKET"] = f"127.0.0.1:{ports['region.server']}"
            os.environ["UWSGI_WORKERS"] = "8"
            os.environ["UWSGI_THREADS"] = "2"

        server = SentryHTTPServer(
            host=host,
            port=int(server_port),
            extra_options=uwsgi_overrides,
            debug=debug_server,
        )

        # If we don't need any other daemons, just launch a normal uwsgi webserver
        # and avoid dealing with subprocesses
        if not daemons and not silo:
            server.run()

        import sys
        from subprocess import list2cmdline

        from honcho.manager import Manager

        os.environ["PYTHONUNBUFFERED"] = "true"

        if debug_server:
            threading.Thread(target=server.run).start()
        else:
            # Make sure that the environment is prepared before honcho takes over
            # This sets all the appropriate uwsgi env vars, etc
            server.prepare_environment()

            if silo != "control":
                daemons += [_get_daemon("server")]

        cwd = os.path.realpath(os.path.join(settings.PROJECT_ROOT, os.pardir, os.pardir))

        from sentry.runner.formatting import get_honcho_printer

        honcho_printer = get_honcho_printer(prefix=prefix, pretty=pretty)

        manager = Manager(honcho_printer)
        for name, cmd in daemons:
            quiet = bool(
                name not in (settings.DEVSERVER_LOGS_ALLOWLIST or ())
                and settings.DEVSERVER_LOGS_ALLOWLIST
            )
            manager.add_process(name, list2cmdline(cmd), quiet=quiet, cwd=cwd)

        if silo == "control":
            control_environ = {
                "SENTRY_SILO_DEVSERVER": "1",
                "SENTRY_SILO_MODE": "CONTROL",
                "SENTRY_REGION": "",
                "SENTRY_CONTROL_SILO_PORT": server_port,
                "SENTRY_REGION_SILO_PORT": str(ports["region.server"]),
                "SENTRY_DEVSERVER_BIND": f"127.0.0.1:{server_port}",
                "UWSGI_HTTP_SOCKET": f"127.0.0.1:{ports['server']}",
                "UWSGI_WORKERS": "8",
                "UWSGI_THREADS": "2",
            }
            merged_env = os.environ.copy()
            merged_env.update(control_environ)
            control_services = ["server"]
            if workers:
                control_services.append("worker")
            if celery_beat:
                control_services.append("celery-beat")

            for service in control_services:
                name, cmd = _get_daemon(service)
                name = f"control.{name}"
                quiet = bool(
                    name not in (settings.DEVSERVER_LOGS_ALLOWLIST or ())
                    and settings.DEVSERVER_LOGS_ALLOWLIST
                )
                manager.add_process(name, list2cmdline(cmd), quiet=quiet, cwd=cwd, env=merged_env)

        manager.loop()
        sys.exit(manager.returncode)
