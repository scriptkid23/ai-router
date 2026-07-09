from ai_router.adapters.gemini.selectors import GEMINI_URL
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


class GeminiPlanner:
    def plan(self, job: AskJob, *, recovery: bool = False) -> list[Command]:
        if recovery:
            return [
                Command("goto", {"url": GEMINI_URL}),
                Command("wait_idle"),
                *self._core(job),
            ]
        return self._core(job)

    def _core(self, job: AskJob) -> list[Command]:
        return [
            Command("wait_idle"),
            Command("clear_input"),
            Command("type", {"prompt": job.prompt}),
            Command("submit"),
            Command("wait_generating"),
            Command("wait_answer"),
        ]
