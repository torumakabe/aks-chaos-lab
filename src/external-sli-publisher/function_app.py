from __future__ import annotations

import logging
import os

import azure.functions as func
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.sdk.resources import Resource

if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor(
        resource=Resource.create(
            {
                "service.name": os.environ.get(
                    "EXTERNAL_SLI_TELEMETRY_ROLE_NAME",
                    "external-sli-publisher",
                ),
                "service.instance.id": os.environ.get(
                    "WEBSITE_INSTANCE_ID",
                    os.environ.get("WEBSITE_SITE_NAME", "external-sli-publisher"),
                ),
                "service.version": "0.1.0",
            }
        )
    )

from external_sli_publisher.publisher import Settings, run_once  # noqa: E402

app = func.FunctionApp()


@app.timer_trigger(
    schedule="%EXTERNAL_SLI_CRON_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def publish_external_sli(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logging.warning("external SLI publisher timer is past due")

    run_once(Settings.from_env())
