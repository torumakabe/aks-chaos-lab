from __future__ import annotations

from typing import Any

from locust import HttpUser, between, task


class BasicUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def safe_get(self, path: str, name: str | None = None) -> Any | None:
        """
        Wraps GET requests to handle potential AttributeError exceptions that may occur
        within the Locust client (e.g., 'NoneType' object has no attribute 'url').

        If an error occurs, notifies Locust's events.request_failure and returns None.
        On success, returns the response object.
        """
        try:
            return self.client.get(path, name=name)
        except AttributeError as exc:
            # Record the failure in Locust's statistics (continue test execution even after exception)
            try:
                self.environment.events.request_failure.fire(
                    request_type="GET",
                    name=name or path,
                    response_time=0,
                    response_length=0,
                    exception=exc,
                )
            except Exception:
                # Log if the event notification itself fails
                self.logger.exception(  # ty: ignore[unresolved-attribute]
                    "Failed to fire request_failure event for %s", path
                )
            self.logger.exception("AttributeError during GET %s", path)  # ty: ignore[unresolved-attribute]
            return None
        except Exception as exc:
            # Record other exceptions similarly
            try:
                self.environment.events.request_failure.fire(
                    request_type="GET",
                    name=name or path,
                    response_time=0,
                    response_length=0,
                    exception=exc,
                )
            except Exception:
                self.logger.exception(  # ty: ignore[unresolved-attribute]
                    "Failed to fire request_failure event for %s", path
                )
            self.logger.exception("Unhandled exception during GET %s", path)  # ty: ignore[unresolved-attribute]
            return None

    @task(5)
    def get_root(self):
        # Use relative paths; base host is provided via --host
        self.safe_get("/")

    @task(1)
    def get_health(self):
        self.safe_get("/health")
