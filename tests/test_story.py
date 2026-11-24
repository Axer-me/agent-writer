"""Память сюжета, сброс утверждений и зачин первой главы. Без вызовов модели."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.pipeline.approvals import stage_order, stale_after
from app.pipeline.drama import act_spans, chapter_drama, drama_ready, parse_drama, render_drama, world_act
from app.pipeline.plan import SYSTEM as PLAN_SYSTEM
from app.pipeline.bounds import bound_block_reason, contract_issues, plan_bound_issues
from app.pipeline.chapter import (
    CONTRACT_SYSTEM,
    _context_parts,
    _hold_to_plan,
    _scene_system,
    card_ban_lines,
    read_scene_texts,
    scenes_to_reuse,
    with_card_bans,
    write_scene_text,
)
from app.pipeline.continuity import (
    _parse_arc,
    arc_fits_plan,
    ensure_fresh_arc,
    fill_arc_from_plan,
    resolve_arc,
    sharpen_arc,
    step_is_flat,
)
from app.pipeline.chapter import _prior_note, opening_needles, purpose_stems
from app.pipeline.select import chapter_needles, person_tokens
from app.storage.story import fact_lines, usable_facts
from app.pipeline.plan import book_plan_block_reason, chapter_start_block, rows_from_plan_text
from app.pipeline.prompts import KEEP_LENGTH
from app.pipeline.world import parse_world, world_system
from app.storage.project import ARTIFACTS, BookProject, default_state
from app.storage.story import StoryStore, parse_event_updates


def _chapter(number: int) -> str:
    return (
        f"## Глава {number}. Дело {number}\n"
        "Теги: комната, разговор\n"
        f"Кратко: герой остаётся в комнате {number} и принимает одно решение. "
        "Раскрытие причины ещё рано.\n"
    )


class StoryMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "chapters").mkdir()
        self.book = BookProject(self.root)
        self.book.write_state(default_state("proba", "Проба"))
        for name in ARTIFACTS:
            self.book.write_text(name, "")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_plan_markdown_moves_into_rows(self) -> None:
        text = "\n\n".join(_chapter(number) for number in range(1, 51))
        self.book.write_text("plan.md", text)
        store = StoryStore(self.root)
        store.import_legacy(text, "")
        self.assertEqual(len(store.plan_chapters()), 50)
        self.assertEqual(book_plan_block_reason(self.book), "")
        self.assertIn("комнате 1", store.plan_chapter_text(1))

    def test_event_update_keeps_other_rows(self) -> None:
        store = StoryStore(self.root)
        store.apply_events(
            [{"chapter": 1, "kind": "решение", "body": "остаётся дома", "status": "открыто"}]
        )
        updates = parse_event_updates("- Глава 2 | рана | порез | открыто")
        store.apply_events(updates)
        bodies = [row["body"] for row in store.events()]
        self.assertIn("остаётся дома", bodies)
        self.assertIn("порез", bodies)

    def test_bad_event_text_does_not_wipe(self) -> None:
        store = StoryStore(self.root)
        store.apply_events(
            [{"chapter": 1, "kind": "знание", "body": "не знает хозяина", "status": "открыто"}]
        )
        self.assertEqual(parse_event_updates("просто абзац без строк"), [])
        self.assertEqual(store.events()[0]["body"], "не знает хозяина")

    def test_arc_is_required_context(self) -> None:
        store = StoryStore(self.root)
        store.upsert_plan(rows_from_plan_text(_chapter(1)))
        store.save_arc(
            {
                "promise": "письмо на столе",
                "payoff_chapter": 6,
                "start_chapter": 1,
                "end_chapter": 5,
                "steps": "### Глава 1\nгерой видит письмо",
            }
        )
        required, _optional = _context_parts(self.book, 1)
        joined = "\n".join(required)
        self.assertIn("письмо на столе", joined)
        self.assertIn("Выплата не раньше главы: 6", joined)

    def test_card_edit_marks_later_steps_stale(self) -> None:
        stale = stale_after({}, "card")
        self.assertTrue(stale["plan"])
        self.assertTrue(stale["bible"])
        self.assertTrue(stale["drama"])

    def test_rereading_a_step_does_not_mark_later_steps(self) -> None:
        from app import main
        from app.pipeline.plan import _store

        self.book.write_text("card.md", "Ночь в комнате.\n")
        self.book.patch_state(stale={"drama": False, "bible": False, "plan": False})
        store = _store(self.book)
        store.upsert_plan(
            [{"number": 1, "title": "Дом", "tags": "ночь", "event": "стук в дверь и одно решение"}]
        )
        self.book.patch_state(stale={"drama": False, "bible": True, "plan": True})
        original = main._book
        main._book = lambda _project_id: self.book
        try:
            main.api_save_file("proba", "card.md", main.SaveBody(content="Ночь в комнате.\n"))
            untouched = self.book.read_state()["stale"]
            self.assertTrue(untouched["bible"])
            self.assertTrue(untouched["plan"])
            main.api_save_plan(
                "proba",
                main.PlanSaveBody(
                    chapters=[
                        main.PlanChapterBody(
                            number=1,
                            title="Дом",
                            tags="ночь",
                            event="стук в дверь и одно решение",
                        )
                    ]
                ),
            )
            still = self.book.read_state()["stale"]
            self.assertTrue(still["bible"])
            self.assertTrue(still["plan"])
            main.api_save_file("proba", "card.md", main.SaveBody(content="Ночь в комнате. Стук.\n"))
        finally:
            main._book = original
        changed = self.book.read_state()["stale"]
        self.assertTrue(changed["bible"])
        self.assertTrue(changed["drama"])
        self.assertTrue(changed["plan"])

    def test_chapter_does_not_wait_for_an_approval_flag(self) -> None:
        self.book.patch_state(approved={"card": True, "style": True, "bible": True, "plan": False})
        self.assertIn("План пуст", chapter_start_block(self.book))

    def test_names_use_the_surname_and_skip_plan_labels(self) -> None:
        self.assertEqual(person_tokens("Полковник Алексей Андреевич Волынцев")[0], "Волынцев")
        self.assertNotIn("Полковник", person_tokens("Полковник Алексей Андреевич Волынцев"))
        needles = chapter_needles(
            "Глава 1. Набат\nТеги: дорога\nКратко: Волынцев ведёт колонну к Москве.",
            [{"name": "Полковник Алексей Андреевич Волынцев"}],
        )
        self.assertIn("Волынцев", needles)
        self.assertNotIn("Теги", needles)
        self.assertNotIn("Кратко", needles)
        self.assertNotIn("Полковник", needles)

    def test_opening_needles_come_from_the_sheet(self) -> None:
        needles = opening_needles(
            "Полковник Алексей Волынцев",
            "Российская империя, зима 1772 года. Тракт к Москве.",
            "Главный герой — полковник русской армии.",
            "",
        )
        self.assertIn("Волынцев", needles)
        self.assertIn("1772", needles)
        self.assertIn("Росс", needles)
        self.assertTrue(any(item.startswith("Моск") for item in needles))
        aimed = opening_needles("", "", "Он идёт, чтобы спасти выживших.", "", "удержать анклав для людей")
        self.assertIn("удерж", aimed)
        self.assertTrue(purpose_stems("Он идёт, чтобы спасти выживших.", ""))

    def test_next_beat_lists_what_already_happened(self) -> None:
        blocks = [
            "Изменение: дозор приводит мужика с вестью",
            "Изменение: раненого несут в голову колонны",
        ]
        note = _prior_note(["уже написанный кусок"], blocks[:1])
        self.assertIn("Уже случилось", note)
        self.assertIn("мужика", note)
        self.assertNotIn("раненого", note)
        pieces = []
        notes = []

        def fake_model(prompt: str, block: str) -> str:
            notes.append(prompt)
            return "кусок " + block

        for index, block in enumerate(blocks):
            prompt = _prior_note(pieces, blocks[:index])
            pieces.append(fake_model(prompt, block))
        self.assertIn("Уже случилось", notes[1])
        self.assertEqual(pieces[0][:5], "кусок")

    def test_stale_arc_is_rebuilt_from_the_current_plan(self) -> None:
        store = StoryStore(self.root)
        store.replace_plan(
            [
                {
                    "number": 1,
                    "title": "Набат",
                    "tags": "дорога",
                    "event": "Зима, разорённый почтовый тракт. Волынцев слышит набат и ставит колонну в каре.",
                    "deferral": "",
                }
            ]
        )
        store.save_arc(
            {
                "promise": "Ещё рано говорить о планах действий.",
                "payoff_chapter": 5,
                "start_chapter": 1,
                "end_chapter": 5,
                "steps": "Глава 1: Возвращение домой, поместье и родственники",
            }
        )
        chapters = store.plan_chapters()
        self.assertFalse(arc_fits_plan(store.arc(), chapters, 1))
        ensure_fresh_arc(self.book, force=True)
        fresh = store.arc_text()
        self.assertIn("тракт", fresh.lower())
        self.assertNotIn("поместье", fresh.lower())
        self.assertNotIn("ещё рано", fresh.lower())

    def test_first_chapter_prompt_has_no_forced_road(self) -> None:
        scene = _scene_system(1, 1)
        text = scene + "\n" + CONTRACT_SYSTEM + "\n" + KEEP_LENGTH
        self.assertNotIn("уже в пути", text)
        self.assertNotIn("погода, тело, дорога", text)
        self.assertNotIn("как долго идут", text)
        self.assertIn("не подставляй дорогу", text.lower())
        self.assertNotIn("4–6", CONTRACT_SYSTEM)
        self.assertNotIn("800", scene)
        self.assertNotIn("длину не набирай", scene.lower())
        self.assertIn("следующую главу не начинай", scene.lower())
        self.assertIn("не конспект", scene.lower())
        self.assertIn("ламп", scene.lower())
        self.assertIn("длиннее", scene.lower())
        self.assertIn("обычный день", scene.lower())
        later = _scene_system(1, 3)
        self.assertIn("того же дня", later.lower())
        self.assertIn("четыре разных события", CONTRACT_SYSTEM.lower())
        self.assertNotIn("длиннее", _scene_system(2, 1).lower())

    def test_card_ban_is_copied_verbatim(self) -> None:
        card = "Ночь в мастерской. В первой главе она не выходит из мастерской."
        self.assertEqual(
            card_ban_lines(card),
            ["В первой главе она не выходит из мастерской."],
        )
        contract = with_card_bans(
            "# Контракт\nМесто и время: ночь, мастерская\nУчастники: Лида\n"
            "Нельзя раскрывать: кто оставил часы\n### Сцена 1\nМесто и время: ночь",
            card,
        )
        self.assertIn("В первой главе она не выходит из мастерской.", contract)
        prose = "Лида выходит из мастерской домой."
        self.assertEqual(contract_issues(prose, contract, []), [])

    def test_flat_arc_steps_become_different_turns(self) -> None:
        arc = {
            "promise": "происхождение свёртка ещё рано",
            "payoff_chapter": 6,
            "start_chapter": 2,
            "end_chapter": 6,
            "steps": "\n".join(
                [
                    "Глава 2: Нина продолжает перепись и не трогает свёрток",
                    "Глава 3: Нина работает дальше и не обращает внимания на свёрток",
                    "Глава 4: Нина занимается полкой и игнорирует свёрток",
                    "Глава 5: Нина сосредоточена на работе и оставляет свёрток нетронутым",
                    "Глава 6: Нина завершает этап без изучения свёртка",
                ]
            ),
        }
        sharp = sharpen_arc(arc, [])
        bodies = [line.split(":", 1)[1].strip() for line in sharp["steps"].splitlines()]
        self.assertEqual(len(bodies), 5)
        self.assertEqual(len(set(bodies)), 5)
        self.assertTrue(all(not step_is_flat(body) for body in bodies))
        self.assertEqual(sharp["promise"], arc["promise"])
        kept = sharpen_arc(
            {**arc, "steps": "Глава 2: Утром приходит хозяйка, свёрток мешает записям"},
            [],
        )
        self.assertIn("приходит хозяйка", kept["steps"])
        self.assertGreater(kept["steps"].count("Глава "), 1)

    def test_empty_arc_answer_ignores_removed_deferral(self) -> None:
        chapters = [{"number": 2, "deferral": "Раскрывать письмо ещё рано.", "event": "Герой сидит на кухне и не вскрывает письмо."}]
        filled = resolve_arc(None, chapters, 2, 6)
        self.assertNotIn("Раскрывать письмо", filled["promise"])
        kept = resolve_arc(
            {
                "promise": "письмо не вскрывать",
                "payoff_chapter": 8,
                "start_chapter": 2,
                "end_chapter": 6,
                "steps": "",
            },
            chapters,
            2,
            6,
        )
        self.assertEqual(kept["promise"], "письмо не вскрывать")
        self.assertEqual(kept["payoff_chapter"], 8)

    def test_fact_reports_are_dropped(self) -> None:
        self.assertEqual(
            usable_facts(
                [
                    "Деньги: нет упоминания о финансах",
                    "Лида в мастерской, крышка закрыта.",
                ]
            ),
            ["Лида в мастерской, крышка закрыта."],
        )
        self.assertEqual(usable_facts(["Где кто: Лида одна", "Окончание главы: утро"]), [])
        self.assertEqual(usable_facts(["Рана на ладони затянулась."]), ["Рана на ладони затянулась."])

    def test_accepted_chapter_stays_beside_empty_draft(self) -> None:
        self.book.write_chapter(1, "Лида остаётся в мастерской.", draft=False)
        self.book.patch_state(chapter_number=2)
        payload = self.book.public_dict()
        self.assertIn("Лида остаётся", payload["accepted_text"])
        self.assertEqual(payload["draft"], "")
        self.book.write_chapter(2, "черновик второй", draft=True)
        payload = self.book.public_dict()
        self.assertEqual(payload["draft"], "черновик второй")
        self.assertIn("Лида остаётся", payload["accepted_text"])

    def test_facts_without_dashes_still_land(self) -> None:
        lines = fact_lines("Герой остаётся на кухне.\nПисьмо лежит в конверте на столе.")
        self.assertEqual(
            lines,
            ["Герой остаётся на кухне.", "Письмо лежит в конверте на столе."],
        )

    def test_arc_without_label_uses_plan_deferral(self) -> None:
        labeled = _parse_arc(
            "Обещание — письмо не вскрывать\nВыплата не раньше главы 8\nГлава 2\nСидит на кухне.",
            2,
            6,
        )
        self.assertEqual(labeled["promise"], "письмо не вскрывать")
        self.assertEqual(labeled["payoff_chapter"], 8)
        self.assertIn("Глава 2", labeled["steps"])
        filled = fill_arc_from_plan(
            [{"number": 2, "deferral": "Раскрывать письмо ещё рано.", "event": "Сидит на кухне."}],
            _parse_arc("Глава 2\nСидит на кухне.", 2, 6),
            2,
            6,
        )
        self.assertEqual(filled["promise"], "")
        self.assertEqual(filled["payoff_chapter"], 6)

    def test_month_jump_blocks_accept_and_keeps_text(self) -> None:
        night = " ".join(["кухня"] * 200)
        text = night + "\n\nПрошёл месяц. Дни шли один за другим."
        plan = "Кратко: ночь на кухне. Раскрывать письмо ещё рано."
        self.assertIn("месяц", bound_block_reason(text, plan))
        self.assertEqual(bound_block_reason(night + "\n\nПрошёл месяц.", "через месяц герой выходит"), "")
        self.assertEqual(plan_bound_issues("Погода была тихой.", plan), [])
        kept, held = _hold_to_plan(self.book, 1, text)
        self.assertEqual(kept, text)
        self.assertTrue(any("месяц" in item for item in held))

    def test_promise_about_weeks_is_not_a_time_jump(self) -> None:
        plan = "Ночь в мастерской. Раскрывать ещё рано."
        promise = "Клиент благодарит Лиду и уходит, обещая вернуться через несколько недель."
        self.assertEqual(plan_bound_issues(promise, plan), [])
        self.assertEqual(bound_block_reason(promise, plan), "")
        soon = "Он обещает вернуться через неделю."
        self.assertEqual(plan_bound_issues(soon, plan), [])
        moved = "Через неделю он был уже в другом городе."
        self.assertTrue(plan_bound_issues(moved, plan))
        passed = "Прошла неделя, а потом он ушёл."
        self.assertTrue(any("недел" in item for item in plan_bound_issues(passed, plan)))

    def test_saved_scenes_resume_until_draft_exists(self) -> None:
        write_scene_text(self.book, 1, 1, "первая сцена")
        write_scene_text(self.book, 1, 3, "третья сцена")
        self.assertEqual(read_scene_texts(self.book, 1), ["первая сцена"])
        write_scene_text(self.book, 1, 2, "вторая сцена")
        self.book.write_contract(1, "# Контракт\n### Сцена 1\n")
        contract, scenes = scenes_to_reuse(self.book, 1)
        self.assertEqual(scenes, ["первая сцена", "вторая сцена", "третья сцена"])
        self.assertIn("Контракт", contract)
        self.book.write_chapter(1, "готовый черновик", draft=True)
        self.assertEqual(scenes_to_reuse(self.book, 1), ("", []))
        scene = self.book.chapters_dir / "001-scene-01.md"
        scene.touch()
        os.utime(scene, (scene.stat().st_atime, self.book.chapter_path(1, draft=True).stat().st_mtime + 5))
        again, kept = scenes_to_reuse(self.book, 1)
        self.assertEqual(kept[0], "первая сцена")
        self.assertIn("Контракт", again)

    def test_drama_sheet_changes_with_the_act(self) -> None:
        text = "\n".join(
            [
                "Герой: Нина",
                "Хочет: закончить полку до прихода хозяйки",
                "Верит: чужое не станет её бедой, если его не трогать",
                "Цена: хозяйка решит, что Нина скрывает свёрток",
                "Становится: человеком, который говорит о свёртке",
                "Акт 1: молчит и теряет время",
                "Акт 2: молчание стоит доверия",
                "Акт 3: называет свёрток и платит местом",
            ]
        )
        drama = parse_drama(text)
        self.assertTrue(drama_ready(drama))
        self.assertEqual(parse_drama(render_drama(drama)), drama)
        early = chapter_drama(drama, 1)
        late_act = chapter_drama(drama, 12)
        crack = chapter_drama(drama, 20)
        choice = chapter_drama(drama, 50)
        self.assertIn("закончить полку", early)
        self.assertIn("не отказывается", late_act)
        self.assertIn("считает нормальной", crack)
        self.assertIn("считает нормальной", choice)
        self.assertNotIn("1–15", early)
        self.assertNotEqual(early, late_act)
        self.assertNotEqual(late_act, crack)
        self.assertEqual(crack, choice)
        self.assertNotIn("становится:", crack)
        self.assertEqual(chapter_drama({}, 3), "")

    def test_drama_keeps_a_second_sentence(self) -> None:
        text = "\n".join(
            [
                "Герой: Нина",
                "Хочет: закончить полку до прихода хозяйки.",
                "Если не успеет, хозяйка решит, что ночь пропала.",
                "Верит: чужое не станет её бедой, если его не трогать",
                "Цена: хозяйка решит, что Нина скрывает свёрток",
                "Становится: человеком, который говорит о свёртке",
                "Акт 1: главы 1–4, молчит и теряет время",
                "Акт 2: главы 5–12, молчание стоит доверия",
                "Акт 3: главы 13–20, называет свёрток и платит местом",
            ]
        )
        drama = parse_drama(text)
        self.assertIn("хозяйка решит", drama["want"])
        self.assertEqual(parse_drama(render_drama(drama)), drama)
        self.assertEqual(act_spans(drama), [(1, 4), (5, 12), (13, 20)])

    def test_world_plan_stays_inside_the_drama_sheet(self) -> None:
        text = "\n".join(
            [
                "Герой: Нина",
                "Хочет: закончить полку.",
                "Верит: молчание спасает",
                "Цена: потеряет место",
                "Становится: говорит вслух",
                "Акт 1: главы 1–4, молчит",
                "Акт 2: главы 5–12, трещина",
                "Акт 3: главы 13–20, выбор",
                "Мир акта 1: ночь над аптекой, лампа и закрытый свёрток.",
                "За окном тихая улица.",
                "Мир акта 2: утром лавка уже открыта, а свёрток всё на полке",
                "Мир акта 3: Нина говорит о свёртке при открытой двери",
            ]
        )
        drama = parse_drama(text)
        self.assertIn("тихая улица", drama["world1"])
        self.assertIn("лавка уже открыта", drama["world2"])
        self.assertTrue(drama_ready(drama))
        self.assertEqual(parse_drama(render_drama(drama)), drama)
        self.assertIn("закрытый свёрток", world_act(drama, 1))
        self.assertIn("лавка уже открыта", world_act(drama, 8))
        self.assertEqual(world_act({"hero": "Нина"}, 1), "")
        StoryStore(self.root).save_drama(drama)
        required, _optional = _context_parts(self.book, 2)
        self.assertIn("тихая улица", "\n".join(required))

    def test_character_status_survives_the_new_labels(self) -> None:
        from app.pipeline.bible import parse_characters

        text = """
## Иван
Внешность и манера: хромает на левую ногу.
Хочет от героя: чтобы тот остался в доме.
Давит на веру: ломает молчание, когда зовёт его по имени.
Отношение: не меняет сцену
Статус: мёртв
"""
        found = parse_characters(text)
        self.assertEqual(found, [{"name": "Иван", "status": "dead", "call": "Иван"}])

    def test_drama_spans_follow_the_sheet(self) -> None:
        from app.pipeline.drama import act_spans

        named = parse_drama(
            "Герой: Нина\nХочет: закончить полку\nВерит: молчание спасает\n"
            "Цена: потеряет место\nСтановится: говорит вслух\n"
            "Акт 1: главы 1–4, молчит\nАкт 2: главы 5–12, молчание не спасает\n"
            "Акт 3: главы 13–50, говорит"
        )
        self.assertEqual(act_spans(named), [(1, 4), (5, 12), (13, 50)])
        self.assertIn("главы 1–4", chapter_drama(named, 1))
        self.assertIn("не отказывается", chapter_drama(named, 4))
        self.assertNotIn("главы 5–12", chapter_drama(named, 4))
        self.assertIn("главы 5–12", chapter_drama(named, 5))
        self.assertIn("дало трещину", chapter_drama(named, 12))
        self.assertNotIn("главы 13–50", chapter_drama(named, 12))
        self.assertIn("главы 13–50", chapter_drama(named, 13))
        plain = parse_drama(
            "Герой: А\nХочет: б\nВерит: в\nЦена: г\nСтановится: д\n"
            "Акт 1: молчит\nАкт 2: трещина\nАкт 3: выбор"
        )
        self.assertIsNone(act_spans(plain))

    def test_chapter_context_carries_the_drama_sheet(self) -> None:
        drama = parse_drama(
            "Герой: Нина\nХочет: закончить полку\nВерит: молчание спасает\n"
            "Цена: потеряет место\nСтановится: говорит вслух\n"
            "Акт 1: молчит\nАкт 2: молчание не спасает\nАкт 3: говорит"
        )
        StoryStore(self.root).save_drama(drama)
        required, _optional = _context_parts(self.book, 2)
        blob = "\n".join(required)
        self.assertIn("Драма этой главы", blob)
        self.assertIn("молчание спасает", blob)
        self.assertIn("Драма этой главы", _scene_system(2, 1))
        self.assertIn("поступком", _scene_system(2, 1))
        self.assertIn("Лист драмы", PLAN_SYSTEM)

    def test_original_drama_comes_before_characters(self) -> None:
        original = stage_order(True)
        foreign = stage_order(False)
        self.assertLess(original.index("drama"), original.index("bible"))
        self.assertLess(foreign.index("bible"), foreign.index("drama"))
        stale = stale_after({}, "drama", is_original=True)
        self.assertFalse(stale["drama"])
        self.assertTrue(stale["bible"])
        self.assertTrue(stale["plan"])
        stale = stale_after({}, "bible", is_original=False)
        self.assertFalse(stale["bible"])
        self.assertTrue(stale["drama"])
        self.assertTrue(stale["plan"])
        stale = stale_after({"plan": True}, "plan")
        self.assertFalse(stale["plan"])

    def test_chapter_catalog_keeps_dates_and_status(self) -> None:
        self.book.write_chapter(1, "Одно два три", draft=False)
        self.book.write_chapter(2, "Черновик главы", draft=True)
        rows = {row["number"]: row for row in self.book.chapter_catalog()}
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[1]["status"], "accepted")
        self.assertEqual(rows[1]["words"], 3)
        self.assertTrue(rows[1]["created_at"])
        self.assertTrue(rows[1]["updated_at"])
        self.assertEqual(rows[2]["status"], "draft")
        self.assertEqual(rows[2]["words"], 2)
        self.assertEqual(rows[3]["status"], "empty")
        self.assertEqual(rows[3]["created_at"], "")
        self.assertEqual(rows[3]["words"], 0)


class WorldSheetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "chapters").mkdir()
        self.book = BookProject(self.root)
        self.book.write_state(default_state("proba", "Проба"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_parser_keeps_continuation_and_every_chapter_line(self) -> None:
        sheet, reveals = parse_world(
            "Когда и где: Ночь в порту.\n"
            "Фонари уже погасли.\n"
            "Обычная жизнь: Рыбак считает улов до рассвета. "
            "Глава 1 знакомит с человеком только если это заголовок, а длинная фраза остаётся в поле.\n"
            "Первый час: На рукаве соль и дёготь.\n"
            "Пока скрыто: Кто оставил лодку, читатель ещё не знает.\n"
            "## Глава 1\n"
            "Читатель узнаёт: На рукаве соль, и до рассвета ещё час.\n"
            "Вторая фраза про колокол.\n"
            "## Глава 2. Причал\n"
            "Читатель узнаёт: Колокол бьёт, когда лодка не вернулась.\n"
        )
        self.assertIn("Фонари уже погасли", sheet["when_where"])
        self.assertIn("длинная фраза остаётся", sheet["daily"])
        self.assertIn("соль", reveals[1])
        self.assertIn("колокол", reveals[1].lower())
        self.assertIn("Колокол бьёт", reveals[2])
        self.assertNotIn("Причал", reveals[2])

    def test_foreign_sheet_does_not_invent_and_original_does(self) -> None:
        original = world_system(True)
        foreign = world_system(False)
        self.assertIn("Мира ещё нет", original)
        self.assertIn("не добавляй", foreign)
        self.assertIn("в заметках этого нет", foreign)
        sheet, _rows = parse_world("Когда и где: ** Российская империя, 1772 год")
        self.assertTrue(sheet["when_where"].startswith("Российская"))
        self.assertNotIn("Мира ещё нет", foreign)
        self.assertIn("несколько предложений", original)
        self.assertIn("Что видно вокруг", original)
        self.assertIn("Мир акта", foreign)

    def test_reveal_survives_plan_edit_and_reaches_the_chapter(self) -> None:
        store = StoryStore(self.root)
        store.upsert_plan([{"number": 1, "title": "Дом", "tags": "ночь", "event": "стук"}])
        store.save_reveals({1: "На столе остывший чай.", 9: "такой главы нет"})
        store.upsert_plan([{"number": 1, "title": "Дом", "tags": "ночь", "event": "стук в дверь"}])
        self.assertEqual(store.plan_chapters()[0]["reveal"], "На столе остывший чай.")
        self.assertNotIn("чай", store.plan_chapter_text(1))
        self.assertEqual(len(store.plan_chapters()), 1)
        store.save_world({"when_where": "Ночь в комнате.", "daily": "", "first_hour": "", "hidden": ""})
        required, _optional = _context_parts(self.book, 1)
        joined = "\n".join(required)
        self.assertIn("На столе остывший чай", joined)
        self.assertIn("Ночь в комнате", joined)
        self.assertIn("Что читатель узнаёт", _scene_system(1, 1))
        self.assertIn("Если абзаца нет", _scene_system(2, 1))

    def test_empty_world_does_not_change_the_old_chapter_brief(self) -> None:
        required, _optional = _context_parts(self.book, 1)
        self.assertNotIn("Что читатель узнаёт", "\n".join(required))
        self.assertNotIn("Лист мира", "\n".join(required))


if __name__ == "__main__":
    unittest.main()
