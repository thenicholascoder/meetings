import os
import sys
import threading

from django.apps import AppConfig


class MeetingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.meetings"
    label = "meetings"

    def ready(self) -> None:
        if os.environ.get("PPTX_WARMUP") == "0":
            return
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if command in {"test", "migrate", "makemigrations", "collectstatic", "shell", "check"}:
            return
        from apps.meetings.services.slides import warm_pptx_converter

        threading.Thread(target=warm_pptx_converter, name="pptx-warmup", daemon=True).start()
