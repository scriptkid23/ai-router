from ai_router.adapters.deepseek.selectors import DEEPSEEK_URL
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


class DeepSeekPlanner:
    def plan(self, job: AskJob, *, recovery: bool = False) -> list[Command]:
        return [
            Command("goto", {"url": DEEPSEEK_URL}),
            Command("new_chat"),
            Command("wait_idle"),
            *self._core(job),
        ]

    def _core(self, job: AskJob) -> list[Command]:
        return [
            Command("clear_input"),
            Command("type", {"prompt": job.prompt}),
            Command("submit"),
            Command("wait_generating"),
            Command("wait_answer"),
        ]
