"""Event submissions by email — see feature-specs/email-submissions.md."""
from zoneinfo import ZoneInfo

SOURCE_NAME = "Community submissions"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

MAX_EMAILS_PER_RUN = 50
MAX_EVENTS_PER_EMAIL = 20
MAX_EVENTS_PER_SENDER_PER_DAY = 20
MAX_LINKS_PER_EMAIL = 10
