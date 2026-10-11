from datetime import UTC, date, datetime

from app.db.seed import DEMO_FIRM_CONFIG
from app.schemas.firm_config import FirmConfig

# Monday 2026-10-12 10:00 in Asia/Kolkata (the demo firm's timezone).
NOW = datetime(2026, 10, 12, 4, 30, tzinfo=UTC)
FIRM_TODAY = date(2026, 10, 12)
DEMO_SLUG = "northstar-legal"


def demo_config() -> FirmConfig:
    return FirmConfig.model_validate(DEMO_FIRM_CONFIG)
