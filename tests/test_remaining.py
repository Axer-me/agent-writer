"""Остаток аудита: заметки канона, контракт, отбор строк, ходы, вычитка абзацев, журнал."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.llm.base_types import LLMResult
from app.pipeline.anti_llm import de_llm, editor_indexes, marked_indexes, splice_paragraphs
from app.pipeline.overlap import neighboring_duplicates
from app.pipeline.score import chapter_report
from app.pipeline.bounds import acceptance_block, contract_issues
from app.pipeline.chapter import _context_parts
from app.pipeline.jobs import bind_job, note_call, reset_job
from app.pipeline.plan import parse_spine
from app.pipeline.select import lines_about
from app.storage.project import ARTIFACTS, BookProject, default_state
from app.storage.story import StoryStore

CONTRACT = """# Контракт главы 1
Место и время: ночная кухня
Участники: Марк
Изменение: он прячет письмо
Нельзя раскрывать: имя отправителя письма
### Сцена 1
Место и время: кухня
"""


class RemainingAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "chapters").mkdir()
        self.book = BookProject(self.root)
        self.book.write_state(default_state("proba", "Проба"))
        for name in ARTIFACTS:
            self.book.write_text(name, "")
        self.store = StoryStore(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_canon_notes_keep_continuity(self) -> None:
        self.store.replace_canon_notes("книги: Паветта погибла у Бездны\nигры: это не книжный канон")
        text = self.store.canon_text()
        self.assertIn("книги: Паветта погибла у Бездны", text)
        self.assertIn("игры: это не книжный канон", text)
        self.book.patch_state(is_original=False)
        required, _optional = _context_parts(self.book, 1)
        joined = "\n".join(required)
        self.assertIn("Паветта погибла у Бездны", joined)
        self.assertIn("Библия без заметки не канон", joined)

    def test_contract_blocks_place_secret_and_dead(self) -> None:
        wrong = "Марк стоит в комнате. Имя отправителя письма стало ясно."
        issues = contract_issues(wrong, CONTRACT, ["Паветта"])
        self.assertTrue(any("места" in item for item in issues))
        self.assertTrue(any("запрещает" in item for item in issues))
        self.assertFalse(any("мёртв" in item for item in issues))
        fine = "Марк стоит на кухне и молчит над пустым столом."
        self.assertEqual(contract_issues(fine, CONTRACT, []), [])
        dead = fine + " Паветта вошла в кухню."
        self.assertTrue(any("Паветта" in item for item in contract_issues(dead, CONTRACT, ["Паветта"])))
        self.assertIn("контрактом", acceptance_block(wrong, "ночь на кухне", CONTRACT, []))

    def test_lines_about_keep_the_named_paragraph(self) -> None:
        text = "Паветта погибла раньше.\n\nГеральт едет с Цири к горам."
        picked = lines_about(text, ["Геральт"])
        self.assertIn("Геральт", picked)
        self.assertNotIn("Паветта", picked)

    def test_repeated_move_warns_the_next_chapter(self) -> None:
        self.store.record_moves(1, "засада, ночь")
        self.store.record_moves(2, "засада")
        self.store.record_moves(3, "засада, торг")
        warning = self.store.move_warning(4)
        self.assertIn("засада", warning)
        self.assertNotIn("торг", warning)

    def test_spine_parses_finale_and_acts(self) -> None:
        spine = parse_spine("Финал: герой остаётся дома\nАкт 1: ночь на кухне\nАкт 2: поиск\nАкт 3: ответ\n## Глава 1. Кухня")
        self.assertEqual(spine["finale"], "герой остаётся дома")
        self.assertEqual(spine["act2"], "поиск")
        self.store.save_spine(spine)
        self.assertEqual(self.store.spine()["act3"], "ответ")

    def test_editor_flags_a_repeated_word_and_a_misspelled_name(self) -> None:
        repeated = " ".join(["Волынцев сказал коротко."] * 6)
        self.assertTrue(editor_indexes(repeated))
        miss = "Волонцев стоял у рогаток и смотрел на снег."
        contract = "Участники: Волынцев\nИзменение: он слышит набат колокола\n"
        self.assertEqual(editor_indexes(miss, contract), [0])
        garbled = "У розвальней отломилось розвальель и упало в снег у тракта."
        self.assertEqual(editor_indexes(garbled), [0])
        self.assertEqual(editor_indexes("Марк стоит на кухне.\n\nЧай остыл на столе."), [])

    def test_neighboring_duplicate_is_visible_without_a_model(self) -> None:
        paragraph = "Волынцев стоял у рогаток и смотрел, как снег заносит колею тракта."
        hits = neighboring_duplicates(paragraph + "\n\n" + paragraph)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["chapter"], "эта глава")

    def test_chapter_report_sees_a_repeated_word_and_a_missing_name(self) -> None:
        text = " ".join(["Волынцев кивнул."] * 8)
        report = chapter_report(text, ["1772"], "Участники: Шилов\nИзменение: он слышит набат\n")
        self.assertTrue(report["hot"])
        self.assertIn("1772", report["missing_needles"])
        self.assertIn("Шилов", report["absent_names"])

    def test_clean_prose_is_not_rewritten(self) -> None:
        text = "Марк стоит на кухне.\n\nЧай остыл на столе."
        self.assertEqual(marked_indexes(text), [])
        self.assertEqual(de_llm(text), text)

    def test_short_repair_does_not_replace_paragraph(self) -> None:
        source = (
            "Первый абзац достаточно длинный для проверки длины.\n\n"
            "Стоит отметить, что второй абзац тоже длинный и содержит след."
        )
        self.assertEqual(marked_indexes(source), [1])
        kept = splice_paragraphs(source, {1: "коротко"})
        self.assertIn("Стоит отметить", kept)
        replaced = splice_paragraphs(
            source,
            {1: "Второй абзац переписан и остался достаточно длинным для замены следа."},
        )
        self.assertNotIn("Стоит отметить", replaced)

    def test_call_log_records_provider(self) -> None:
        tokens = bind_job(self.book, None)
        try:
            note_call("chapter", LLMResult(text="ок", provider="gigachat", model="max", retries=2), 15)
        finally:
            reset_job(tokens)
        last = self.store.last_call()
        self.assertEqual(last["provider"], "gigachat")
        self.assertEqual(last["retries"], 2)
        self.assertEqual(last["ok"], 1)


if __name__ == "__main__":
    unittest.main()
