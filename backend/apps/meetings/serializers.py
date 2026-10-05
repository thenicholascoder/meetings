from rest_framework import serializers

from .models import ScheduledRoom


class TokenSourceRequestSerializer(serializers.Serializer):
    """LiveKit TokenSource request body (snake_case, with camelCase aliases).

    https://docs.livekit.io/frontends/build/authentication/endpoint.md
    """

    room_name = serializers.CharField(required=False, allow_blank=True, default="")
    participant_identity = serializers.CharField(required=False, allow_blank=True, default="")
    participant_name = serializers.CharField(required=False, allow_blank=True, default="")
    participant_metadata = serializers.CharField(required=False, allow_blank=True, default="")
    participant_attributes = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        default=dict,
    )
    room_config = serializers.JSONField(required=False, allow_null=True, default=None)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            mapped = dict(data)
            aliases = {
                "roomName": "room_name",
                "participantIdentity": "participant_identity",
                "participantName": "participant_name",
                "participantMetadata": "participant_metadata",
                "participantAttributes": "participant_attributes",
                "roomConfig": "room_config",
            }
            for src, dest in aliases.items():
                if src in mapped and dest not in mapped:
                    mapped[dest] = mapped[src]
            data = mapped
        return super().to_internal_value(data)


class TokenSourceResponseSerializer(serializers.Serializer):
    server_url = serializers.CharField()
    participant_token = serializers.CharField()


class ScheduledRoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledRoom
        fields = (
            "id",
            "title",
            "room_name",
            "starts_at",
            "duration_minutes",
            "max_participants",
            "notes",
            "created_at",
        )
        read_only_fields = ("id", "created_at")
