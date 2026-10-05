"""Access tokens per LiveKit Tokens & grants + Python server SDK.

https://docs.livekit.io/frontends/reference/tokens-grants.md
https://docs.livekit.io/frontends/build/authentication/endpoint.md
https://docs.livekit.io/reference/python/livekit/api/
"""

from datetime import timedelta

from django.conf import settings
from livekit import api


# Camera and microphone only. An empty list would allow every source, including
# screen share. Participants always get this list. The host and co-hosts get it
# only while a deck is on screen.
PUBLISH_SOURCES_WITHOUT_SCREEN = ["camera", "microphone"]


def create_participant_token(
    *,
    identity: str,
    name: str,
    room_name: str,
    metadata: str = "",
    attributes: dict[str, str] | None = None,
    room_config=None,
    role: str = "participant",
    allow_screen_share: bool = True,
) -> str:
    # Official Python pattern: AccessToken(api_key, api_secret).with_*().to_jwt()
    # role is server-signed. Participants cannot rewrite it: can_update_own_metadata
    # is off, and host actions are checked against the database, not this claim.
    # Only the host gets room_admin. Co-hosts act through Django so they cannot
    # end the room or grant room admin to themselves.
    merged = {key: str(value) for key, value in (attributes or {}).items()}
    merged["role"] = role
    grants = api.VideoGrants(
        room_join=True,
        room=room_name,
        room_admin=role == "host",
        can_publish=True,
        can_publish_data=True,
        can_subscribe=True,
        can_update_own_metadata=False,
    )
    if not allow_screen_share:
        grants.can_publish_sources = list(PUBLISH_SOURCES_WITHOUT_SCREEN)
    token = (
        api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(name)
        .with_attributes(merged)
        .with_grants(grants)
        # Self-hosted servers do not revoke JWTs; keep TTL short.
        # https://docs.livekit.io/frontends/reference/tokens-grants.md#self-hosted-deployments
        .with_ttl(timedelta(minutes=10))
    )
    if metadata:
        token = token.with_metadata(metadata)
    if room_config:
        token = token.with_room_config(room_config)
    return token.to_jwt()
