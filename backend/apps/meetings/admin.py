from django.contrib import admin

from .models import AdmissionRequest, CohostInvite, Meeting, MeetingMember, Recording, ScheduledRoom


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("room_name", "ended_at", "created_at")
    search_fields = ("room_name",)


@admin.register(MeetingMember)
class MeetingMemberAdmin(admin.ModelAdmin):
    list_display = ("display_name", "role", "admitted", "identity", "meeting")
    list_filter = ("role", "admitted")
    search_fields = ("display_name", "identity")


@admin.register(AdmissionRequest)
class AdmissionRequestAdmin(admin.ModelAdmin):
    list_display = ("member", "status", "created_at", "decided_at")
    list_filter = ("status",)


@admin.register(CohostInvite)
class CohostInviteAdmin(admin.ModelAdmin):
    list_display = ("member", "status", "created_at", "decided_at")
    list_filter = ("status",)


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    list_display = ("egress_id", "meeting", "status", "object_key", "started_at", "ended_at")
    list_filter = ("status",)
    search_fields = ("egress_id", "object_key", "meeting__room_name")


@admin.register(ScheduledRoom)
class ScheduledRoomAdmin(admin.ModelAdmin):
    list_display = ("title", "room_name", "starts_at", "duration_minutes", "max_participants")
    search_fields = ("title", "room_name")
