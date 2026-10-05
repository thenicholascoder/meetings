import base64
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from apps.meetings.models import MeetingMember, SharedSlides

LIVEKIT = dict(
    LIVEKIT_API_KEY="meetingskey",
    LIVEKIT_API_SECRET="dev_meetings_secret_change_me_32chars_min",
    LIVEKIT_URL="ws://localhost:7880",
)


def _jwt_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


@override_settings(**LIVEKIT)
class AdmissionTests(TestCase):
    def setUp(self):
        self.livekit = [
            patch("apps.meetings.services.admission.set_participant_attributes"),
            patch("apps.meetings.services.admission.set_participant_role"),
            patch("apps.meetings.services.admission.remove_participant"),
            patch("apps.meetings.services.admission.mute_participant"),
            patch("apps.meetings.services.admission.delete_meeting_room"),
            patch("apps.meetings.services.admission.publish_waiting_count"),
        ]
        for mocked in self.livekit:
            mocked.start()
            self.addCleanup(mocked.stop)

    def _create(self, room_name="demo-room"):
        response = self.client.post(
            "/api/meetings",
            data={"room_name": room_name},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def _join(self, room_name, name, host_key="", participant_key=""):
        return self.client.post(
            f"/api/meetings/{room_name}/join",
            data={"participant_name": name},
            content_type="application/json",
            HTTP_X_HOST_KEY=host_key,
            HTTP_X_PARTICIPANT_KEY=participant_key,
        )

    def _promote(self, room_name, identity, host_key, participant_key=None, role="cohost"):
        response = self.client.post(
            f"/api/meetings/{room_name}/participants/{identity}/role",
            data={"role": role},
            content_type="application/json",
            HTTP_X_HOST_KEY=host_key,
        )
        if role != "cohost" or participant_key is None:
            return response
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "invited")
        return self.client.post(
            f"/api/meetings/{room_name}/cohost-invites/{response.json()['invite_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=participant_key,
        )

    def test_creator_is_the_host_and_enters_immediately(self):
        created = self._create()
        response = self._join("demo-room", "Nico", host_key=created["host_key"])

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "admitted")
        self.assertEqual(body["role"], "host")
        claims = _jwt_payload(body["participant_token"])
        video = claims["video"]
        self.assertTrue(video.get("roomAdmin") or video.get("room_admin"))
        self.assertEqual(claims["attributes"]["role"], "host")
        self.assertNotIn("host_key", body)

    def test_stranger_waits_and_cannot_admit_themselves(self):
        created = self._create()
        guest = self._join("demo-room", "Ada")

        self.assertEqual(guest.status_code, 200, guest.content)
        body = guest.json()
        self.assertEqual(body["status"], "pending")
        self.assertNotIn("participant_token", body)
        self.assertTrue(body["participant_key"])

        waiting = self.client.get(
            "/api/meetings/demo-room/waiting",
            HTTP_X_PARTICIPANT_KEY=body["participant_key"],
        )
        self.assertEqual(waiting.status_code, 403)

        accept = self.client.post(
            f"/api/meetings/demo-room/admission/{body['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=body["participant_key"],
        )
        self.assertEqual(accept.status_code, 403)

        host_view = self.client.get(
            "/api/meetings/demo-room/waiting",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        self.assertEqual(host_view.status_code, 200)
        self.assertEqual(host_view.json()["requests"], [{"id": body["request_id"], "name": "Ada", "created_at": host_view.json()["requests"][0]["created_at"]}])
        self.assertEqual(host_view.json()["requests"][0]["name"], "Ada")

    def test_host_accept_lets_the_joiner_in(self):
        created = self._create()
        guest = self._join("demo-room", "Ada").json()

        accept = self.client.post(
            f"/api/meetings/demo-room/admission/{guest['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        self.assertEqual(accept.status_code, 200)

        status = self.client.get(
            f"/api/meetings/demo-room/admission/{guest['request_id']}",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(body["status"], "admitted")
        self.assertEqual(body["role"], "participant")
        claims = _jwt_payload(body["participant_token"])
        video = claims["video"]
        self.assertFalse(video.get("roomAdmin") or video.get("room_admin"))

        again = self._join("demo-room", "Ada", participant_key=guest["participant_key"])
        self.assertEqual(again.json()["status"], "admitted")

    def test_joiner_cannot_publish_screen_share_while_slides_are_up(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, True)
        created = self._create()
        with override_settings(MEDIA_ROOT=media):
            deck = SharedSlides(
                room_name="demo-room",
                owner_identity="p_0123456789abcdef",
                owner_name="Nico",
                original_name="deck.pdf",
                page_count=1,
                active=True,
            )
            deck.file.save("deck.pdf", ContentFile(b"%PDF-1.4\n"), save=True)

        guest = self._join("demo-room", "Ada").json()
        self.client.post(
            f"/api/meetings/demo-room/admission/{guest['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        status = self.client.get(
            f"/api/meetings/demo-room/admission/{guest['request_id']}",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(status.status_code, 200, status.content)
        sources = _jwt_payload(status.json()["participant_token"])["video"]["canPublishSources"]
        self.assertEqual(sources, ["camera", "microphone"])

        host = self._join("demo-room", "Nico", host_key=created["host_key"])
        self.assertEqual(
            _jwt_payload(host.json()["participant_token"])["video"]["canPublishSources"],
            ["camera", "microphone"],
        )

        deck.active = False
        deck.save(update_fields=["active"])
        rejoined = self._join("demo-room", "Ada", participant_key=guest["participant_key"])
        self.assertEqual(rejoined.status_code, 200, rejoined.content)
        self.assertEqual(
            _jwt_payload(rejoined.json()["participant_token"])["video"]["canPublishSources"],
            ["camera", "microphone"],
        )
        host_again = self._join("demo-room", "Nico", host_key=created["host_key"])
        self.assertNotIn(
            "canPublishSources",
            _jwt_payload(host_again.json()["participant_token"])["video"],
        )

    def test_decline_then_the_same_person_can_ask_again(self):
        created = self._create()
        guest = self._join("demo-room", "Ada").json()

        decline = self.client.post(
            f"/api/meetings/demo-room/admission/{guest['request_id']}/decline",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        self.assertEqual(decline.status_code, 200)

        status = self.client.get(
            f"/api/meetings/demo-room/admission/{guest['request_id']}",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(status.json()["status"], "declined")

        again = self._join("demo-room", "Ada", participant_key=guest["participant_key"])
        self.assertEqual(again.json()["status"], "pending")
        self.assertNotEqual(again.json()["request_id"], guest["request_id"])

    def test_wrong_host_key_does_not_grant_host(self):
        self._create()
        response = self._join("demo-room", "Ada", host_key="not-the-host-key")
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.json()["role"] if "role" in response.json() else "pending", "pending")

    def test_cohost_title_waits_until_they_accept(self):
        created = self._create()
        self._join("demo-room", "Nico", host_key=created["host_key"])
        guest = self._join("demo-room", "Ada").json()
        self.client.post(
            f"/api/meetings/demo-room/admission/{guest['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        admitted = self.client.get(
            f"/api/meetings/demo-room/admission/{guest['request_id']}",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        ).json()

        invited = self._promote("demo-room", admitted["identity"], created["host_key"])
        self.assertEqual(invited.status_code, 200, invited.content)
        body = invited.json()
        self.assertEqual(body["status"], "invited")
        self.assertEqual(
            MeetingMember.objects.get(identity=admitted["identity"]).role,
            MeetingMember.Role.PARTICIPANT,
        )

        other = self._join("demo-room", "Grace").json()
        too_soon = self.client.post(
            f"/api/meetings/demo-room/admission/{other['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(too_soon.status_code, 403)

        stolen = self.client.post(
            f"/api/meetings/demo-room/cohost-invites/{body['invite_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        self.assertEqual(stolen.status_code, 403)

        mine = self.client.get(
            "/api/meetings/demo-room/cohost-invite",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(mine.status_code, 200, mine.content)
        self.assertEqual(mine.json()["invite"]["id"], body["invite_id"])
        self.assertEqual(mine.json()["invite"]["host_name"], "Nico")

        listing = self.client.get(
            "/api/meetings/demo-room/cohost-invites",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        self.assertEqual(listing.json()["invites"][0]["identity"], admitted["identity"])
        hidden = self.client.get(
            "/api/meetings/demo-room/cohost-invites",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(hidden.status_code, 403)

        declined = self.client.post(
            f"/api/meetings/demo-room/cohost-invites/{body['invite_id']}/decline",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(declined.status_code, 200, declined.content)
        self.assertEqual(
            MeetingMember.objects.get(identity=admitted["identity"]).role,
            MeetingMember.Role.PARTICIPANT,
        )
        self.assertIsNone(
            self.client.get(
                "/api/meetings/demo-room/cohost-invite",
                HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
            ).json()["invite"]
        )

        accepted = self._promote(
            "demo-room",
            admitted["identity"],
            created["host_key"],
            guest["participant_key"],
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertEqual(accepted.json()["role"], "cohost")
        self.assertEqual(
            MeetingMember.objects.get(identity=admitted["identity"]).role,
            MeetingMember.Role.COHOST,
        )
        admitted_other = self.client.post(
            f"/api/meetings/demo-room/admission/{other['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(admitted_other.status_code, 200, admitted_other.content)

        demoted = self._promote(
            "demo-room",
            admitted["identity"],
            created["host_key"],
            role="participant",
        )
        self.assertEqual(demoted.status_code, 200, demoted.content)
        self.assertEqual(
            MeetingMember.objects.get(identity=admitted["identity"]).role,
            MeetingMember.Role.PARTICIPANT,
        )

    def test_cohost_can_admit_but_cannot_appoint_or_end(self):
        created = self._create()
        guest = self._join("demo-room", "Ada").json()
        self.client.post(
            f"/api/meetings/demo-room/admission/{guest['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        admitted = self.client.get(
            f"/api/meetings/demo-room/admission/{guest['request_id']}",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        ).json()

        promoted = self._promote(
            "demo-room",
            admitted["identity"],
            created["host_key"],
            guest["participant_key"],
        )
        self.assertEqual(promoted.status_code, 200, promoted.content)
        self.assertEqual(promoted.json()["role"], "cohost")

        other = self._join("demo-room", "Grace").json()
        accepted = self.client.post(
            f"/api/meetings/demo-room/admission/{other['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)

        grace = self.client.get(
            f"/api/meetings/demo-room/admission/{other['request_id']}",
            HTTP_X_PARTICIPANT_KEY=other["participant_key"],
        ).json()
        denied = self.client.post(
            f"/api/meetings/demo-room/participants/{grace['identity']}/role",
            data={"role": "cohost"},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(denied.status_code, 403)

        denied = self.client.post(
            f"/api/meetings/demo-room/participants/{admitted['identity']}/role",
            data={"role": "participant"},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(denied.status_code, 403)

        ended = self.client.post(
            "/api/meetings/demo-room/end",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        )
        self.assertEqual(ended.status_code, 403)
        self.assertIn("Only the host", ended.json()["detail"])

    def test_cohost_cannot_remove_host_or_another_cohost(self):
        created = self._create()
        host = self._join("demo-room", "Nico", host_key=created["host_key"]).json()
        first = self._join("demo-room", "Ada").json()
        second = self._join("demo-room", "Grace").json()
        for guest in (first, second):
            self.client.post(
                f"/api/meetings/demo-room/admission/{guest['request_id']}/accept",
                data={},
                content_type="application/json",
                HTTP_X_HOST_KEY=created["host_key"],
            )
        first_id = self.client.get(
            f"/api/meetings/demo-room/admission/{first['request_id']}",
            HTTP_X_PARTICIPANT_KEY=first["participant_key"],
        ).json()["identity"]
        second_id = self.client.get(
            f"/api/meetings/demo-room/admission/{second['request_id']}",
            HTTP_X_PARTICIPANT_KEY=second["participant_key"],
        ).json()["identity"]
        self._promote("demo-room", first_id, created["host_key"], first["participant_key"])
        self._promote("demo-room", second_id, created["host_key"], second["participant_key"])

        remove_host = self.client.post(
            f"/api/meetings/demo-room/participants/{host['identity']}/remove",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=first["participant_key"],
        )
        self.assertEqual(remove_host.status_code, 403)

        remove_cohost = self.client.post(
            f"/api/meetings/demo-room/participants/{second_id}/remove",
            data={},
            content_type="application/json",
            HTTP_X_PARTICIPANT_KEY=first["participant_key"],
        )
        self.assertEqual(remove_cohost.status_code, 403)

    def test_host_remove_sends_the_person_back_to_the_waiting_room(self):
        created = self._create()
        guest = self._join("demo-room", "Ada").json()
        self.client.post(
            f"/api/meetings/demo-room/admission/{guest['request_id']}/accept",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        identity = self.client.get(
            f"/api/meetings/demo-room/admission/{guest['request_id']}",
            HTTP_X_PARTICIPANT_KEY=guest["participant_key"],
        ).json()["identity"]

        removed = self.client.post(
            f"/api/meetings/demo-room/participants/{identity}/remove",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        self.assertEqual(removed.status_code, 200, removed.content)

        again = self._join("demo-room", "Ada", participant_key=guest["participant_key"])
        self.assertEqual(again.json()["status"], "pending")

    def test_missing_meeting_and_direct_token_are_refused(self):
        missing = self._join("not-a-room", "Ada")
        self.assertEqual(missing.status_code, 404)

        token = self.client.post(
            "/api/getToken",
            data={"room_name": "demo-room", "participant_name": "Ada"},
            content_type="application/json",
        )
        self.assertEqual(token.status_code, 403)

    def test_ended_meeting_stops_new_joiners(self):
        created = self._create()
        ended = self.client.post(
            "/api/meetings/demo-room/end",
            data={},
            content_type="application/json",
            HTTP_X_HOST_KEY=created["host_key"],
        )
        self.assertEqual(ended.status_code, 200)
        guest = self._join("demo-room", "Ada")
        self.assertEqual(guest.status_code, 409)
        self.assertEqual(guest.json()["status"], "ended")

    def test_schedule_creator_receives_host_power(self):
        starts = datetime.now(timezone.utc) + timedelta(hours=1)
        response = self.client.post(
            "/api/schedules",
            data={
                "title": "Standup",
                "room_name": "standup-thursday",
                "starts_at": starts.isoformat(),
                "duration_minutes": 30,
                "max_participants": 0,
                "notes": "",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        host_key = response.json()["host_key"]
        listed = self.client.get("/api/schedules")
        self.assertNotIn("host_key", listed.content.decode())

        joined = self._join("standup-thursday", "Nico", host_key=host_key)
        self.assertEqual(joined.json()["role"], "host")
