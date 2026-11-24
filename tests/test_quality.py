"""Проверки первой волны качества без вызовов модели."""

from __future__ import annotations

import unittest

from app.llm.gigachat import _retryable, call_chat, explain_gigachat_error
from app.pipeline.anti_llm import keeps_story
from app.pipeline.card import infer_card_flags
from app.pipeline.chapter import chapter_contract_header, split_scene_contracts
from app.pipeline.continuity import chapter_for_memory
from app.pipeline.plan import plan_block_reason, plan_issues, rows_from_plan_text
from app.pipeline.prompts import pack_priority


def _chapter(number: int, *, weak: bool = False) -> str:
    if weak:
        return f"## Глава {number}. Коротко\nМало текста.\n"
    return (
        f"## Глава {number}. Дело {number}\n"
        "Теги: дорога, разговор\n"
        f"Кратко: герой доходит до места {number}, спорит и принимает одно решение, "
        "но раскрытие тайны ещё рано и откладывается на потом.\n"
    )


def _full_plan() -> str:
    return "\n\n".join(_chapter(number) for number in range(1, 51))


class PackPriorityTest(unittest.TestCase):
    def test_optional_shrinks_before_required_tail(self) -> None:
        required = ["ЗАДАНИЕ\n" + ("я" * 80) + "\nКОНЕЦ-СЦЕНЫ"]
        optional = ["карточка " + ("а" * 5000)]
        packed = pack_priority(required, optional, limit=500)
        self.assertIn("КОНЕЦ-СЦЕНЫ", packed)
        self.assertLess(packed.index("КОНЕЦ-СЦЕНЫ"), len(packed))
        self.assertTrue(packed.rstrip().endswith("КОНЕЦ-СЦЕНЫ"))

    def test_oversized_required_keeps_its_ending(self) -> None:
        packed = pack_priority(["START" + ("x" * 5000) + "TAILMARK"], [], limit=400)
        self.assertIn("TAILMARK", packed)
        self.assertNotIn("START", packed)


class PlanGateTest(unittest.TestCase):
    def test_full_plan_passes(self) -> None:
        self.assertEqual(plan_issues(_full_plan()), [])
        self.assertEqual(plan_block_reason(_full_plan()), "")

    def test_gap_and_ellipsis_are_rejected(self) -> None:
        text = "\n\n".join(_chapter(number) for number in list(range(1, 11)) + [50])
        text += "\n\n...\n"
        issues = plan_issues(text)
        self.assertTrue(any("многоточие" in item for item in issues))
        self.assertTrue(any("Нет глав" in item for item in issues))
        self.assertIn("11", plan_block_reason(text))

    def test_weak_chapter_needs_an_event(self) -> None:
        text = _full_plan().replace(_chapter(3), _chapter(3, weak=True))
        self.assertTrue(any("Глава 3" in item for item in plan_issues(text)))

    def test_plan_ends_where_the_chapters_end(self) -> None:
        text = "\n\n".join(_chapter(number) for number in range(1, 13))
        self.assertEqual(plan_issues(text), [])
        gapped = "\n\n".join(_chapter(number) for number in (1, 2, 3, 4, 5, 12))
        reason = plan_block_reason(gapped)
        self.assertIn("6", reason)
        self.assertNotIn("13–50", reason)
        self.assertNotIn("Нет глав: 13", reason)

    def test_book_size_rejects_a_plan_outside_its_range(self) -> None:
        import tempfile
        from pathlib import Path

        from app.pipeline.plan import _store, book_plan_block_reason, rows_from_plan_text
        from app.storage.project import ARTIFACTS, BookProject, default_state

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "chapters").mkdir()
        book = BookProject(root)
        book.write_state(default_state("razmer", "Размер"))
        for name in ARTIFACTS:
            book.write_text(name, "")
        text = "\n\n".join(_chapter(number) for number in range(1, 13))
        _store(book).replace_plan(rows_from_plan_text(text))
        book.patch_state(book_size="mini")
        self.assertEqual(book_plan_block_reason(book), "")
        book.patch_state(book_size="large")
        self.assertIn("60", book_plan_block_reason(book))
        tmp.cleanup()

    def test_chapter_brief_keeps_every_sentence(self) -> None:
        text = "\n".join(
            [
                "## Глава 1. Дом",
                "Теги: ночь, комната",
                "Кратко: Полковник входит в дом вечером.",
                "С чего положение начинается: в комнате пусто и пыльно.",
                "Какой один новый поворот успевает случиться: он узнаёт мать.",
                "Чем глава кончается: он остаётся у двери, и это уже не прежний дом.",
            ]
        )
        row = rows_from_plan_text(text)[0]
        self.assertIn("входит в дом", row["event"])
        self.assertIn("пусто и пыльно", row["event"])
        self.assertIn("узнаёт мать", row["event"])
        self.assertIn("не прежний дом", row["event"])

class CardFlagsTest(unittest.TestCase):
    def test_headings_do_not_override_original_and_rating(self) -> None:
        text = """
# Название
Дорога
# Оригинал или вселенная
Оригинал
# Канон или AU
Канон
# Тон и рейтинг
PG-13, нет 18+
"""
        flags = infer_card_flags(text, {"is_original": False, "canon": False, "adult": True})
        self.assertTrue(flags["is_original"])
        self.assertTrue(flags["canon"])
        self.assertFalse(flags["adult"])

    def test_body_can_choose_au_and_adult(self) -> None:
        text = """
# Оригинал или вселенная
Вселенная: Ведьмак
# Канон или AU
AU, другой исход войны
# Тон и рейтинг
18+, жестокость
"""
        flags = infer_card_flags(text)
        self.assertFalse(flags["is_original"])
        self.assertFalse(flags["canon"])
        self.assertTrue(flags["adult"])


class MemoryTest(unittest.TestCase):
    def test_chapter_excerpt_keeps_ending(self) -> None:
        text = "НАЧАЛО " + ("середина " * 800) + "ФИНАЛ-РАНА"
        excerpt = chapter_for_memory(text, 500)
        self.assertIn("ФИНАЛ-РАНА", excerpt)
        self.assertIn("середина главы пропущена", excerpt)


class ContractTest(unittest.TestCase):
    def test_scene_contracts_split_without_mixing(self) -> None:
        text = """
# Контракт главы 1
Участники: Марк
Изменение: Марк решает остаться
### Сцена 1
Место и время: тракт ночью
### Сцена 2
Место и время: постоялый двор
"""
        blocks = split_scene_contracts(text)
        self.assertEqual(len(blocks), 2)
        self.assertIn("тракт", blocks[0])
        self.assertNotIn("постоялый", blocks[0])
        self.assertIn("Марк", chapter_contract_header(text))
        self.assertNotIn("Сцена 1", chapter_contract_header(text))


class ProseGuardTest(unittest.TestCase):
    def test_rejects_lost_name_change_or_length(self) -> None:
        source = "Марк шёл дождём и решил остаться у огня. " * 20
        contract = """
# Контракт главы 1
Участники: Марк
Изменение: решает остаться
### Сцена 1
Участники: Марк
"""
        self.assertTrue(keeps_story(source, source, contract))
        self.assertFalse(keeps_story(source, "Анна ушла.", contract))
        rewritten = source.replace("Марк", "Анна")
        self.assertFalse(keeps_story(source, rewritten, contract))
        without_change = source.replace("остаться", "уйти")
        self.assertFalse(keeps_story(source, without_change, contract))


class GigaChatCallTest(unittest.TestCase):
    def test_prefers_callable_chat_over_create(self) -> None:
        class Chat:
            def __init__(self, owner: "Client") -> None:
                self.owner = owner

            def __call__(self, payload: dict) -> dict:
                self.owner.used = "call"
                return payload

            def create(self, **kwargs: object) -> dict:
                self.owner.used = "create"
                return kwargs

        class Client:
            def __init__(self) -> None:
                self.used = ""
                self.chat = Chat(self)

        client = Client()
        call_chat(client, {"messages": []})
        self.assertEqual(client.used, "call")

    def test_error_text_distinguishes_causes(self) -> None:
        self.assertIn("429", explain_gigachat_error(RuntimeError("HTTP 429")))
        self.assertIn("401", explain_gigachat_error(RuntimeError("401 Unauthorized")))
        self.assertIn("/api/v2", explain_gigachat_error(RuntimeError("403 /api/v2/chat/completions")))
        self.assertIn("TLS", explain_gigachat_error(RuntimeError("UNEXPECTED_EOF_WHILE_READING")))
        self.assertTrue(_retryable(RuntimeError("UNEXPECTED_EOF_WHILE_READING")))
        self.assertTrue(_retryable(TimeoutError("timed out")))
        self.assertFalse(_retryable(RuntimeError("401 Unauthorized")))


if __name__ == "__main__":
    unittest.main()
