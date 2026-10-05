from django.urls import path

from . import views, views_admission, views_recordings

urlpatterns = [
    path("health", views.health),
    # LiveKit TokenSource endpoint is closed: a token from here would skip
    # the waiting room. Join through /api/meetings/<room>/join instead.
    path("getToken", views.get_token),
    path("connection-details", views.connection_details),
    path("meetings", views_admission.meetings_collection),
    path("meetings/<slug:room_name>/join", views_admission.meeting_join),
    path("meetings/<slug:room_name>/waiting", views_admission.meeting_waiting),
    path("meetings/<slug:room_name>/admission/<uuid:request_id>", views_admission.meeting_admission),
    path(
        "meetings/<slug:room_name>/admission/<uuid:request_id>/accept",
        views_admission.meeting_admission_accept,
    ),
    path(
        "meetings/<slug:room_name>/admission/<uuid:request_id>/decline",
        views_admission.meeting_admission_decline,
    ),
    path("meetings/<slug:room_name>/cohost-invites", views_admission.meeting_cohost_invites),
    path("meetings/<slug:room_name>/cohost-invite", views_admission.meeting_cohost_invite),
    path(
        "meetings/<slug:room_name>/cohost-invites/<uuid:invite_id>/accept",
        views_admission.meeting_cohost_invite_accept,
    ),
    path(
        "meetings/<slug:room_name>/cohost-invites/<uuid:invite_id>/decline",
        views_admission.meeting_cohost_invite_decline,
    ),
    path(
        "meetings/<slug:room_name>/participants/<str:identity>/role",
        views_admission.meeting_participant_role,
    ),
    path(
        "meetings/<slug:room_name>/participants/<str:identity>/remove",
        views_admission.meeting_participant_remove,
    ),
    path(
        "meetings/<slug:room_name>/participants/<str:identity>/mute",
        views_admission.meeting_participant_mute,
    ),
    path("meetings/<slug:room_name>/end", views_admission.meeting_end),
    path("record/start", views_recordings.record_start),
    path("record/stop", views_recordings.record_stop),
    path("record/template", views_recordings.recording_template),
    path("record/template.js", views_recordings.recording_template_script),
    path("recordings", views_recordings.recordings_collection),
    path("recordings/file", views_recordings.recordings_file),
    path("livekit/webhook", views_recordings.livekit_webhook),
    path("schedules", views.schedules),
    path("slides", views.slides_collection),
    path("slides/<uuid:deck_id>/page", views.slides_page),
    path("slides/<uuid:deck_id>/stop", views.slides_stop),
    path("slides/<uuid:deck_id>/notes", views.slides_notes),
    path("slides/<uuid:deck_id>/file", views.slides_file),
]
