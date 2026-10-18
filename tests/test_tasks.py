import asyncio

from jarvis.config import BackConfig
from jarvis.core.tasks import TaskManager
from jarvis.core.worker import parse_result


def test_task_lifecycle_with_question_and_approval():
    events = []

    async def runner(task, mgr):
        ans = await mgr.request(task, "question", "Qual nome?")
        ok = await mgr.request(task, "approval", "git push")
        task.spoken_summary = f"feito com {ans}, push={ok}"

    async def main():
        async def on_event(task, kind):
            events.append(kind)
            if kind == "question":
                asyncio.get_running_loop().call_soon(lambda: print(mgr.answer(None, "foo")))
            if kind == "approval":
                asyncio.get_running_loop().call_soon(lambda: mgr.approve(task.id, True))

        mgr = TaskManager(BackConfig(), on_event, runner=runner)
        t = mgr.dispatch("faz algo", None)
        await t.handle
        return t

    t = asyncio.run(main())
    assert events == ["started", "question", "approval", "done"]
    assert t.status == "concluída" and t.spoken_summary == "feito com foo, push=True"


def test_cancel_and_parallel():
    async def runner(task, mgr):
        await asyncio.sleep(10)

    async def main():
        kinds = []

        async def on_event(task, kind):
            kinds.append(kind)

        mgr = TaskManager(BackConfig(max_parallel=2), on_event, runner=runner)
        a, b = mgr.dispatch("a", None), mgr.dispatch("b", None)
        await asyncio.sleep(0.05)
        assert len(mgr.active()) == 2
        mgr.cancel(a.id)
        mgr.cancel("#" + b.id)
        await asyncio.gather(a.handle, b.handle, return_exceptions=True)
        return a, kinds

    a, kinds = asyncio.run(main())
    assert a.status == "cancelada" and kinds.count("cancelled") == 2


def test_parse_result():
    s, r = parse_result("Mudei a.py e rodei os testes.\n\nRESUMO_FALADO: Pronto, testes passando.")
    assert s == "Pronto, testes passando." and r == "Mudei a.py e rodei os testes."
    s, r = parse_result("Fiz tudo. Detalhes aqui.")
    assert s == "Fiz tudo."


def test_task_detail_and_id_normalization():
    async def runner(task, mgr):
        task.status = "executando"
        task.log_activity("editando soma.py")
        task.log_activity("rodando: pytest -q")
        await asyncio.sleep(10)

    async def main():
        async def on_event(task, kind):
            pass

        mgr = TaskManager(BackConfig(), on_event, runner=runner)
        mgr.dispatch("primeira", None)
        t2 = mgr.dispatch("cria testes para soma", None)
        await asyncio.sleep(0.05)
        found = [mgr.get(x) for x in ("2", "#2", "tarefa 2", "dois", "Tarefa Dois")]
        detail = t2.detail()
        for t in mgr.tasks.values():
            t.handle.cancel()
        return found, t2, detail

    found, t2, detail = asyncio.run(main())
    assert all(f is t2 for f in found)
    assert "executando" in detail and "rodando: pytest -q" in detail and "cria testes para soma" in detail
    assert "Tempo:" in detail
