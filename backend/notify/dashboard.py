from backend.contracts import EmotionState
from backend.notify.base import NotifyResult


class DashboardOnlyNotifier:
    async def send(self, state: EmotionState, jpeg: bytes | None) -> NotifyResult:
        return NotifyResult("dashboard_only", "dashboard", "would send to owner")
