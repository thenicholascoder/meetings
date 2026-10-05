import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from livekit.protocol.models import ParticipantPermission, TrackInfo, TrackSource, TrackType

from apps.meetings.services.room_control import set_screen_share_locked

LIVEKIT = dict(
    LIVEKIT_API_KEY="meetingskey",
    LIVEKIT_API_SECRET="dev_meetings_secret_change_me_32chars_min",
    LIVEKIT_URL="ws://localhost:7880",
    LIVEKIT_API_URL="http://localhost:7880",
)


class FakeRoomApi:
    def __init__(self, metadata=""):
        permission = ParticipantPermission(
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            can_publish_sources=[
                TrackSource.CAMERA,
                TrackSource.MICROPHONE,
                TrackSource.SCREEN_SHARE,
            ],
        )
        self.person = SimpleNamespace(
            identity="p_guest",
            attributes={"role": "host"},
            permission=permission,
            tracks=[
                TrackInfo(
                    sid="TR_screen",
                    source=TrackSource.SCREEN_SHARE,
                    type=TrackType.VIDEO,
                    muted=False,
                )
            ],
        )
        self.metadata = metadata
        self.metadata_updates = []
        self.participant_updates = []
        self.mutes = []

    async def list_rooms(self, _request):
        return SimpleNamespace(rooms=[SimpleNamespace(name="demo-room", metadata=self.metadata)])

    async def update_room_metadata(self, request):
        self.metadata_updates.append(request)
        self.metadata = request.metadata

    async def list_participants(self, _request):
        return SimpleNamespace(participants=[self.person])

    async def update_participant(self, request):
        self.participant_updates.append(request)

    async def mute_published_track(self, request):
        self.mutes.append(request)


class FakeClient:
    def __init__(self, room):
        self.room = room
        self.closed = False

    async def aclose(self):
        self.closed = True


@override_settings(**LIVEKIT)
class ScreenShareLockTests(TestCase):
    def test_lock_drops_screen_share_and_mutes_an_active_share(self):
        room = FakeRoomApi(metadata=json.dumps({"topic": "demo"}))
        client = FakeClient(room)
        with patch("apps.meetings.services.room_control._client", return_value=client):
            set_screen_share_locked("demo-room", True)

        self.assertTrue(client.closed)
        self.assertEqual(json.loads(room.metadata_updates[0].metadata), {"topic": "demo", "slides": True})
        sources = list(room.participant_updates[0].permission.can_publish_sources)
        self.assertEqual(sources, [TrackSource.CAMERA, TrackSource.MICROPHONE])
        self.assertEqual(room.participant_updates[0].attributes["slides"], "1")
        self.assertEqual(room.mutes[0].track_sid, "TR_screen")
        self.assertTrue(room.mutes[0].muted)

    def test_unlock_restores_every_publish_source(self):
        room = FakeRoomApi(metadata=json.dumps({"slides": True}))
        client = FakeClient(room)
        with patch("apps.meetings.services.room_control._client", return_value=client):
            set_screen_share_locked("demo-room", False)

        self.assertEqual(json.loads(room.metadata_updates[0].metadata)["slides"], False)
        self.assertEqual(list(room.participant_updates[0].permission.can_publish_sources), [])
        self.assertEqual(room.participant_updates[0].attributes["slides"], "")
        self.assertEqual(room.mutes, [])

    def test_unlock_keeps_screen_share_off_for_a_participant(self):
        room = FakeRoomApi(metadata=json.dumps({"slides": True}))
        room.person.attributes = {"role": "participant"}
        client = FakeClient(room)
        with patch("apps.meetings.services.room_control._client", return_value=client):
            set_screen_share_locked("demo-room", False)

        sources = list(room.participant_updates[0].permission.can_publish_sources)
        self.assertEqual(sources, [TrackSource.CAMERA, TrackSource.MICROPHONE])
        self.assertEqual(room.mutes[0].track_sid, "TR_screen")
