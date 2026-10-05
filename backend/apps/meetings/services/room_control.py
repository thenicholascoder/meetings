"""LiveKit room actions for host and co-host controls.

Permission checks live in admission.py. These calls use the server API key,
so a co-host never receives room_admin in their own token.
"""

import json
import logging

from django.conf import settings
from livekit.api import (
    DeleteRoomRequest,
    ListParticipantsRequest,
    ListRoomsRequest,
    MuteRoomTrackRequest,
    RoomParticipantIdentity,
    TwirpError,
    UpdateParticipantRequest,
    UpdateRoomMetadataRequest,
)
from livekit.protocol.models import ParticipantPermission, TrackSource, TrackType

from .egress import _client

# Camera and microphone only. An empty source list means every source, so
# screen share has to be left out explicitly.
_PUBLISH_WITHOUT_SCREEN = [TrackSource.CAMERA, TrackSource.MICROPHONE]
_SCREEN_SOURCES = (TrackSource.SCREEN_SHARE, TrackSource.SCREEN_SHARE_AUDIO)

logger = logging.getLogger(__name__)


class RoomControlError(Exception):
    def __init__(self, detail: str, *, not_found: bool = False):
        super().__init__(detail)
        self.detail = detail
        self.not_found = not_found


def _raise_server_error(exc: TwirpError) -> None:
    if exc.status == 404 or exc.code in {"not_found", "not_found_error"}:
        raise RoomControlError(exc.message or "Participant is not in the room", not_found=True) from exc
    raise RoomControlError(exc.message or "LiveKit request failed") from exc


def set_participant_attributes(room_name: str, identity: str, attributes: dict[str, str]) -> None:
    from asgiref.sync import async_to_sync

    async_to_sync(_set_participant_attributes)(room_name, identity, attributes)


def set_participant_role(
    room_name: str,
    identity: str,
    role: str,
    allow_screen_share: bool | None = None,
) -> None:
    from asgiref.sync import async_to_sync

    async_to_sync(_set_participant_role)(room_name, identity, role, allow_screen_share)


def remove_participant(room_name: str, identity: str) -> None:
    from asgiref.sync import async_to_sync

    async_to_sync(_remove_participant)(room_name, identity)


def mute_participant(room_name: str, identity: str) -> None:
    from asgiref.sync import async_to_sync

    async_to_sync(_mute_participant)(room_name, identity)


def delete_meeting_room(room_name: str) -> None:
    from asgiref.sync import async_to_sync

    async_to_sync(_delete_meeting_room)(room_name)


def publish_waiting_count(room_name: str, count: int) -> None:
    """Tell everyone in the room how many people are waiting.

    The person knocking is not in the room yet, so the host cannot see them
    as a participant. Room metadata is the push LiveKit already delivers.
    """
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        return
    from asgiref.sync import async_to_sync

    try:
        async_to_sync(_merge_room_metadata)(room_name, {"waiting": count})
    except Exception:
        logger.warning("Could not publish the waiting count for %s", room_name, exc_info=True)


def set_screen_share_locked(room_name: str, locked: bool) -> None:
    """Stop screen publishing while slides are up, including for people who join later.

    People already in the room have their grant updated. New tokens omit screen
    share on their own. Room metadata tells a joiner's client to disable the
    button before the deck finishes loading.
    """
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        return
    from asgiref.sync import async_to_sync

    try:
        async_to_sync(_set_screen_share_locked)(room_name, locked)
    except Exception:
        logger.warning("Could not update screen share permission in %s", room_name, exc_info=True)


async def _set_participant_attributes(room_name: str, identity: str, attributes: dict[str, str]) -> None:
    lk = _client()
    try:
        await lk.room.update_participant(
            UpdateParticipantRequest(
                room=room_name,
                identity=identity,
                attributes=attributes,
            )
        )
    except TwirpError as exc:
        _raise_server_error(exc)
    finally:
        await lk.aclose()


async def _set_participant_role(
    room_name: str,
    identity: str,
    role: str,
    allow_screen_share: bool | None = None,
) -> None:
    lk = _client()
    try:
        target = None
        if allow_screen_share is not None:
            try:
                listed = await lk.room.list_participants(ListParticipantsRequest(room=room_name))
            except TwirpError as exc:
                if not _is_not_found(exc):
                    raise
                listed = None
            if listed is not None:
                target = next((person for person in listed.participants if person.identity == identity), None)
        permission = None
        if (
            allow_screen_share is not None
            and target is not None
            and target.permission is not None
            and target.permission.can_publish
        ):
            permission = ParticipantPermission()
            permission.CopyFrom(target.permission)
            del permission.can_publish_sources[:]
            if not allow_screen_share:
                permission.can_publish_sources.extend(_PUBLISH_WITHOUT_SCREEN)
        update = UpdateParticipantRequest(
            room=room_name,
            identity=identity,
            attributes={"role": role},
        )
        if permission is not None:
            update.permission.CopyFrom(permission)
        try:
            await lk.room.update_participant(update)
        except TwirpError as exc:
            _raise_server_error(exc)
        if allow_screen_share is not False or target is None:
            return
        for track in target.tracks:
            if track.source not in _SCREEN_SOURCES or track.muted:
                continue
            await lk.room.mute_published_track(
                MuteRoomTrackRequest(
                    room=room_name,
                    identity=identity,
                    track_sid=track.sid,
                    muted=True,
                )
            )
    except TwirpError as exc:
        _raise_server_error(exc)
    finally:
        await lk.aclose()


async def _remove_participant(room_name: str, identity: str) -> None:
    lk = _client()
    try:
        await lk.room.remove_participant(RoomParticipantIdentity(room=room_name, identity=identity))
    except TwirpError as exc:
        _raise_server_error(exc)
    finally:
        await lk.aclose()


async def _mute_participant(room_name: str, identity: str) -> None:
    lk = _client()
    try:
        listed = await lk.room.list_participants(ListParticipantsRequest(room=room_name))
        target = next((person for person in listed.participants if person.identity == identity), None)
        if target is None:
            raise RoomControlError("That person is not in the meeting", not_found=True)
        muted_any = False
        for track in target.tracks:
            if track.type != TrackType.AUDIO or track.muted:
                continue
            await lk.room.mute_published_track(
                MuteRoomTrackRequest(
                    room=room_name,
                    identity=identity,
                    track_sid=track.sid,
                    muted=True,
                )
            )
            muted_any = True
        if not muted_any:
            return
    except TwirpError as exc:
        _raise_server_error(exc)
    finally:
        await lk.aclose()


async def _merge_room_metadata(room_name: str, fields: dict) -> None:
    lk = _client()
    try:
        listed_rooms = await lk.room.list_rooms(ListRoomsRequest(names=[room_name]))
        room = next((item for item in listed_rooms.rooms if item.name == room_name), None)
        if room is None:
            return
        try:
            data = json.loads(room.metadata) if room.metadata else {}
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.update(fields)
        await lk.room.update_room_metadata(
            UpdateRoomMetadataRequest(room=room_name, metadata=json.dumps(data))
        )
    except TwirpError as exc:
        if _is_not_found(exc):
            return
        raise
    finally:
        await lk.aclose()


async def _set_screen_share_locked(room_name: str, locked: bool) -> None:
    lk = _client()
    try:
        # Metadata is best-effort. A joiner still needs the participant attribute
        # below, which arrives with the people already in the room.
        try:
            listed_rooms = await lk.room.list_rooms(ListRoomsRequest(names=[room_name]))
            room = next((item for item in listed_rooms.rooms if item.name == room_name), None)
            if room is not None:
                await lk.room.update_room_metadata(
                    UpdateRoomMetadataRequest(
                        room=room_name,
                        metadata=_slides_metadata(room.metadata, locked),
                    )
                )
        except TwirpError as exc:
            if not _is_not_found(exc):
                logger.warning("Could not mark slides on room %s", room_name, exc_info=True)
        try:
            listed = await lk.room.list_participants(ListParticipantsRequest(room=room_name))
        except TwirpError as exc:
            if _is_not_found(exc):
                return
            raise
        for person in listed.participants:
            permission = None
            if person.permission is not None and person.permission.can_publish:
                permission = ParticipantPermission()
                permission.CopyFrom(person.permission)
                del permission.can_publish_sources[:]
                # Empty sources allow every track, including screen share.
                # Only the host and co-hosts get that back when slides stop.
                if not _may_publish_screen(person, locked):
                    permission.can_publish_sources.extend(_PUBLISH_WITHOUT_SCREEN)
            update = UpdateParticipantRequest(
                room=room_name,
                identity=person.identity,
                attributes={"slides": "1" if locked else ""},
            )
            if permission is not None:
                update.permission.CopyFrom(permission)
            await lk.room.update_participant(update)
            if _may_publish_screen(person, locked):
                continue
            for track in person.tracks:
                if track.source not in _SCREEN_SOURCES or track.muted:
                    continue
                await lk.room.mute_published_track(
                    MuteRoomTrackRequest(
                        room=room_name,
                        identity=person.identity,
                        track_sid=track.sid,
                        muted=True,
                    )
                )
    except TwirpError as exc:
        if _is_not_found(exc):
            return
        raise
    finally:
        await lk.aclose()


def _may_publish_screen(person, locked: bool) -> bool:
    if locked:
        return False
    attributes = getattr(person, "attributes", None) or {}
    role = attributes.get("role", "") if hasattr(attributes, "get") else ""
    return role in ("host", "cohost")


def _is_not_found(exc: TwirpError) -> bool:
    return exc.status == 404 or exc.code in {"not_found", "not_found_error"}


def _slides_metadata(raw: str, locked: bool) -> str:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["slides"] = locked
    return json.dumps(data)


async def _delete_meeting_room(room_name: str) -> None:
    lk = _client()
    try:
        await lk.room.delete_room(DeleteRoomRequest(room=room_name))
    except TwirpError as exc:
        _raise_server_error(exc)
    finally:
        await lk.aclose()
